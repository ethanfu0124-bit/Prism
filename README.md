# Prism — Claude Code Prompt 优化工具

轻量级 Windows 桌面悬浮窗，实时读取 Claude Code 对话上下文，一键将你的草稿 prompt 优化为更清晰、更具体的指令。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动

```bash
python main.py
```

---

## Shell Hook 安装（可选，推荐）

安装后，Prism 可以自动感知你当前的工作目录，并切换到对应的 Claude Code 项目上下文。

### Bash（写入 `~/.bashrc`）

```bash
cd() { builtin cd "$@" && pwd > ~/.prism_cwd; }
```

### Zsh（写入 `~/.zshrc`）

```zsh
chpwd() { pwd > ~/.prism_cwd; }
```

安装后重启终端或执行 `source ~/.bashrc` / `source ~/.zshrc`。

> 若不安装，Prism 会自动回退到最近修改的 JSONL 文件，并在界面中显示"回退模式"标签。

---

## 功能说明

| 功能 | 说明 |
|------|------|
| 悬浮置顶 | 窗口始终显示在其他应用上方，可拖拽移动 |
| 透明度调节 | 顶部右侧滑块，范围 60%～100% |
| 精简 / 详细模式 | 分段选择器切换优化风格 |
| ✨ 优化按钮 | 调用 Claude API 优化草稿，结果可替换或复制 |
| ⏱ 历史记录 | 保存最近 50 条优化记录，支持一键恢复 |
| ↻ 刷新 | 手动重新检测项目上下文和登录状态 |
| 位置 & 设置持久化 | 窗口位置、透明度、模式自动保存到 `~/.prism_config.json` |

---

## 鉴权说明

Prism **不需要独立的 API Key**，直接复用 Claude Code 本地登录的 OAuth Token（`~/.claude/credentials.json`）。

请先完成 Claude Code 登录，Prism 启动后状态栏会显示绿点表示已连接。

---

## 配置文件

| 文件 | 说明 |
|------|------|
| `~/.prism_config.json` | 窗口位置、透明度、优化模式 |
| `~/.prism_history.json` | 最近 50 条优化历史 |
| `~/.prism_cwd` | 当前工作目录（由 shell hook 写入） |

---

## 项目结构

```
prism/
├── main.py          # 入口，启动 pywebview 窗口
├── backend.py       # 后端逻辑（文件读取、API 调用、历史记录）
├── ui/
│   └── index.html   # 完整 UI（HTML + CSS + JS）
├── requirements.txt
└── README.md
```

---

## 打包为 exe（可选）

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --add-data "ui;ui" main.py
```

生成文件在 `dist/main.exe`。
 