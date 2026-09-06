"""The console in a native window instead of a browser tab.

Nothing about the front end changes here, and that is the whole point. What a
browser contributes to `pulsar dashboard` is a rendering engine and a window;
`pywebview` supplies the same engine — WebView2 on Windows, WebKit on macOS,
GTK/Qt on Linux — without the tab strip, the address bar and the twenty other
tabs the console was competing with for attention. The Python process is already
local and already serves the pages, so "desktop" is a change of frame, not of
architecture.

Two details are not optional.

`private_mode=False` with a real `storage_path`: pywebview defaults to a private
session, which throws `localStorage` away when the window closes. The console
keeps the chosen theme and the network screen's Display drawer there, so the
default would silently reset both on every launch.

The View menu: every screen's state lives in the URL, which is what makes a view
linkable and reloadable — and a window has no address bar to copy it out of.
Handing the *current* URL to the real browser gives that back, so narrowing the
graph down to something worth showing someone is still one action from a link.
"""

from __future__ import annotations

import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

#: Matches `--bg` in the light theme, so the window does not flash white before
#: the first paint. The page repaints itself in dark mode a frame later.
BACKGROUND = "#f6f6f3"


class DesktopUnavailable(RuntimeError):
    """Raised when the optional windowing dependency is not installed."""


def run_window(
    httpd: ThreadingHTTPServer,
    url: str,
    *,
    storage_path: Path,
    width: int = 1600,
    height: int = 1000,
    debug: bool = False,
) -> None:
    """Serve in a background thread and block on the window.

    The GUI toolkit owns the main thread — a hard requirement on macOS and the
    convention everywhere else — so the roles are the reverse of the browser
    path: `serve_forever` moves to a daemon thread and `webview.start` blocks
    until the window is closed. Closing it shuts the server down on the way out,
    which is what makes the window feel like the application rather than like a
    viewer attached to one.
    """
    try:
        import webview
        from webview.menu import Menu, MenuAction
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
        raise DesktopUnavailable(
            "The native window needs pywebview, which is an optional extra.\n"
            "Install it with:  pip install \"pulsar[desktop]\"\n"
            "Or keep using the browser:  pulsar dashboard"
        ) from exc

    server_thread = threading.Thread(
        target=httpd.serve_forever, name="pulsar-console", daemon=True
    )
    server_thread.start()

    window = webview.create_window(
        "PULSAR",
        url,
        width=width,
        height=height,
        min_size=(1100, 700),
        background_color=BACKGROUND,
        text_select=True,     # the drafts and briefs on screen are meant to be copied
        zoomable=True,
    )

    def open_in_browser() -> None:
        webbrowser.open(window.get_current_url() or url)

    menu = [Menu("View", [MenuAction("Open this view in a browser", open_in_browser)])]

    storage_path.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(
            menu=menu,
            private_mode=False,
            storage_path=str(storage_path),
            debug=debug,
        )
    finally:
        httpd.shutdown()
        server_thread.join(timeout=5)
