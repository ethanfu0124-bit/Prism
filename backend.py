import json
import threading
import time
from datetime import datetime
from pathlib import Path

import anthropic
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CREDENTIALS_PATH = Path.home() / ".claude" / "credentials.json"
PRISM_CWD_PATH = Path.home() / ".prism_cwd"
PRISM_CONFIG_PATH = Path.home() / ".prism_config.json"
PRISM_HISTORY_PATH = Path.home() / ".prism_history.json"

DEFAULT_CONFIG = {
    "window_x": 100,
    "window_y": 100,
    "opacity": 1.0,
    "optimize_mode": "detailed",
}

SYSTEM_PROMPTS = {
    "concise": (
        "你是一个 prompt 优化专家。用户正在使用 Claude Code 进行编程，以下是他们当前的对话上下文：\n\n"
        "{context}\n\n"
        "请将用户的草稿 prompt 压缩为一条简短、精准的指令，去除冗余表达，保留核心意图。\n"
        "要求：\n- 尽量简短，一两句话以内\n- 语言与用户草稿保持一致（中文/英文）\n"
        "- 只输出优化后的 prompt，不要任何解释"
    ),
    "detailed": (
        "你是一个 prompt 优化专家。用户正在使用 Claude Code 进行编程，以下是他们当前的对话上下文：\n\n"
        "{context}\n\n"
        "请根据上下文，将用户提供的草稿 prompt 优化为一个更清晰、更具体、更易于 AI 理解的 prompt。\n"
        "要求：\n- 保持用户的原始意图\n- 补充必要的上下文和约束条件\n"
        "- 语言与用户草稿保持一致（中文/英文）\n- 只输出优化后的 prompt，不要任何解释"
    ),
}


class PrismBackend:
    def __init__(self):
        self._window = None
        self._config = self._load_config()
        self._oauth_token = None
        self._current_project_dir = None
        self._current_jsonl_path = None
        self._fallback_mode = False
        self._context_messages = []
        self._observer = Observer()
        self._cwd_handler = None
        self._jsonl_handler = None
        self._api_cancel_event = threading.Event()
        self._jsonl_watch = None

        self._load_credentials()
        self._init_project_context()
        self._start_watchers()

    # ------------------------------------------------------------------ config

    def _load_config(self):
        try:
            if PRISM_CONFIG_PATH.exists():
                with open(PRISM_CONFIG_PATH, encoding="utf-8") as f:
                    cfg = json.load(f)
                    return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
        return dict(DEFAULT_CONFIG)

    def _save_config(self):
        try:
            with open(PRISM_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------ credentials

    def _load_credentials(self):
        try:
            with open(CREDENTIALS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            token = data.get("claudeAiOauthToken", "")
            if token:
                self._oauth_token = token
        except Exception:
            self._oauth_token = None

    # ------------------------------------------------------------------ project context

    def _resolve_project_context(self):
        cwd = self._read_cwd_file()
        if cwd:
            matched = self._match_project_dir(cwd)
            if matched:
                self._current_project_dir = matched
                self._fallback_mode = False
                self._current_jsonl_path = self._latest_jsonl(matched)
                return
        self._fallback_mode = True
        self._current_jsonl_path = self._global_latest_jsonl()

    def _init_project_context(self):
        self._resolve_project_context()
        self._reload_context()

    def _read_cwd_file(self):
        try:
            if PRISM_CWD_PATH.exists():
                return PRISM_CWD_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return None

    def _match_project_dir(self, cwd: str):
        if not CLAUDE_PROJECTS_DIR.exists():
            return None
        normalized = cwd.replace("\\", "-").replace("/", "-").replace(":", "-").lstrip("-")
        for d in CLAUDE_PROJECTS_DIR.iterdir():
            if d.is_dir() and d.name == normalized:
                return d
        # case-insensitive fallback
        for d in CLAUDE_PROJECTS_DIR.iterdir():
            if d.is_dir() and d.name.lower() == normalized.lower():
                return d
        return None

    def _latest_jsonl(self, project_dir: Path):
        files = list(project_dir.glob("*.jsonl"))
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)

    def _global_latest_jsonl(self):
        if not CLAUDE_PROJECTS_DIR.exists():
            return None
        all_files = list(CLAUDE_PROJECTS_DIR.rglob("*.jsonl"))
        if not all_files:
            return None
        return max(all_files, key=lambda p: p.stat().st_mtime)

    def _reload_context(self):
        if not self._current_jsonl_path or not self._current_jsonl_path.exists():
            self._context_messages = []
            return
        messages = []
        try:
            with open(self._current_jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        role = obj.get("role") or obj.get("type", "")
                        content = obj.get("content", "")
                        if isinstance(content, list):
                            parts = []
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    parts.append(block.get("text", ""))
                            content = "\n".join(parts)
                        if role and content:
                            messages.append({"role": role, "content": str(content)})
                    except Exception:
                        continue
        except Exception:
            pass
        self._context_messages = messages[-10:]

    def _build_context_string(self):
        if not self._context_messages:
            return "（暂无对话上下文）"
        lines = []
        for m in self._context_messages:
            role_label = "用户" if m["role"] == "human" else "Claude"
            snippet = m["content"][:400]
            lines.append(f"[{role_label}]: {snippet}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ watchers

    def _start_watchers(self):
        cwd_dir = str(PRISM_CWD_PATH.parent)
        self._cwd_handler = _CwdHandler(self)
        self._observer.schedule(self._cwd_handler, cwd_dir, recursive=False)

        if self._current_jsonl_path:
            self._watch_jsonl(self._current_jsonl_path)

        self._observer.start()

    def _watch_jsonl(self, path: Path):
        if self._jsonl_watch:
            try:
                self._observer.unschedule(self._jsonl_watch)
            except Exception:
                pass
        handler = _JsonlHandler(self)
        self._jsonl_handler = handler
        self._jsonl_watch = self._observer.schedule(handler, str(path.parent), recursive=False)

    def on_cwd_changed(self):
        old_jsonl = self._current_jsonl_path
        self._resolve_project_context()

        if self._current_jsonl_path != old_jsonl and self._current_jsonl_path:
            try:
                self._watch_jsonl(self._current_jsonl_path)
            except Exception:
                pass

        self._reload_context()
        self._notify_ui_context_update()

    def on_jsonl_changed(self):
        self._reload_context()
        self._notify_ui_context_update()

    def _notify_ui_context_update(self):
        if self._window:
            try:
                project_label = self._get_project_label()
                count = len(self._context_messages)
                fallback = self._fallback_mode
                self._window.evaluate_js(
                    f"window.updateContextStatus({json.dumps(project_label)}, {count}, {json.dumps(fallback)})"
                )
            except Exception:
                pass

    def _get_project_label(self):
        if self._current_jsonl_path:
            return self._current_jsonl_path.parent.name[-40:]
        return "未检测到项目"

    # ------------------------------------------------------------------ pywebview API

    def get_initial_state(self):
        return {
            "config": self._config,
            "connected": self._oauth_token is not None,
            "project_label": self._get_project_label(),
            "context_count": len(self._context_messages),
            "fallback_mode": self._fallback_mode,
        }

    def save_window_position(self, x, y):
        self._config["window_x"] = x
        self._config["window_y"] = y
        self._save_config()

    def save_opacity(self, opacity):
        self._config["opacity"] = opacity
        self._save_config()

    def save_optimize_mode(self, mode):
        self._config["optimize_mode"] = mode
        self._save_config()

    def move_window(self, dx, dy):
        if self._window:
            try:
                x = self._window.x + int(dx)
                y = self._window.y + int(dy)
                self._window.move(x, y)
            except Exception:
                pass

    def get_window_position(self):
        if self._window:
            try:
                return {"x": self._window.x, "y": self._window.y}
            except Exception:
                pass
        return None

    def minimize_window(self):
        if self._window:
            try:
                self._window.minimize()
            except Exception:
                pass

    def refresh_context(self):
        self._load_credentials()
        self._init_project_context()
        return {
            "connected": self._oauth_token is not None,
            "project_label": self._get_project_label(),
            "context_count": len(self._context_messages),
            "fallback_mode": self._fallback_mode,
        }

    def optimize_prompt(self, draft, mode):
        if not self._oauth_token:
            return {"error": "no_token", "message": "未检测到 Claude 登录信息，请先启动 Claude Code 并完成登录"}

        context_str = self._build_context_string()
        system_template = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["detailed"])
        system = system_template.replace("{context}", context_str)

        self._api_cancel_event.clear()
        result = {}

        def call():
            try:
                client = anthropic.Anthropic(api_key=self._oauth_token, base_url="https://api.anthropic.com")
                response = client.messages.create(
                    model="claude-sonnet-4-5",
                    max_tokens=1024,
                    system=system,
                    messages=[{"role": "user", "content": draft}],
                    timeout=15,
                )
                if self._api_cancel_event.is_set():
                    result["cancelled"] = True
                    return
                result["text"] = response.content[0].text
            except anthropic.AuthenticationError:
                result["error"] = "auth"
                result["message"] = "登录已过期，请重启 Claude Code 重新登录"
            except Exception as e:
                msg = str(e)
                if "timeout" in msg.lower() or "timed out" in msg.lower():
                    result["error"] = "timeout"
                    result["message"] = "请求超时，请重试"
                else:
                    result["error"] = "api"
                    result["message"] = f"优化失败：{msg}"

        t = threading.Thread(target=call, daemon=True)
        t.start()
        t.join(timeout=16)

        if t.is_alive():
            self._api_cancel_event.set()
            return {"error": "timeout", "message": "请求超时，请重试"}

        if result.get("cancelled"):
            return {"error": "timeout", "message": "请求超时，请重试"}

        if "text" in result:
            self._save_history(draft, result["text"], mode)

        if result.get("error") == "auth":
            self._oauth_token = None

        return result

    # ------------------------------------------------------------------ history

    def _save_history(self, draft, optimized, mode):
        history = []
        try:
            if PRISM_HISTORY_PATH.exists():
                with open(PRISM_HISTORY_PATH, encoding="utf-8") as f:
                    history = json.load(f)
        except Exception:
            history = []

        entry = {
            "timestamp": datetime.now().isoformat(),
            "draft": draft,
            "optimized": optimized,
            "mode": mode,
        }
        history.append(entry)
        if len(history) > 50:
            history = history[-50:]

        try:
            with open(PRISM_HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_history(self):
        try:
            if PRISM_HISTORY_PATH.exists():
                with open(PRISM_HISTORY_PATH, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def copy_to_clipboard(self, text):
        try:
            import subprocess
            proc = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
            proc.communicate(input=text.encode("utf-16"))
            return True
        except Exception:
            return False

    def set_window(self, window):
        self._window = window

    def shutdown(self):
        try:
            self._observer.stop()
            self._observer.join(timeout=2)
        except Exception:
            pass


class _CwdHandler(FileSystemEventHandler):
    def __init__(self, backend: PrismBackend):
        self._backend = backend
        self._last = 0

    def on_modified(self, event):
        if Path(event.src_path).name == PRISM_CWD_PATH.name:
            now = time.time()
            if now - self._last > 0.5:
                self._last = now
                self._backend.on_cwd_changed()

    def on_created(self, event):
        self.on_modified(event)


class _JsonlHandler(FileSystemEventHandler):
    def __init__(self, backend: PrismBackend):
        self._backend = backend
        self._last = 0

    def on_modified(self, event):
        if event.src_path.endswith(".jsonl"):
            now = time.time()
            if now - self._last > 0.5:
                self._last = now
                self._backend.on_jsonl_changed()
