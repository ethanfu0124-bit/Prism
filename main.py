import os
import sys
from pathlib import Path

import webview

from backend import PrismBackend

UI_PATH = Path(__file__).parent / "ui" / "index.html"


def main():
    backend = PrismBackend()
    cfg = backend._config

    window = webview.create_window(
        title="Prism",
        url=str(UI_PATH),
        js_api=backend,
        width=380,
        height=220,
        x=cfg.get("window_x", 100),
        y=cfg.get("window_y", 100),
        frameless=True,
        easy_drag=False,
        on_top=True,
        background_color="#F9F7F4",
        min_size=(320, 180),
    )

    backend.set_window(window)

    def on_closed():
        backend.shutdown()

    window.events.closed += on_closed

    webview.start(debug=False)


if __name__ == "__main__":
    main()
