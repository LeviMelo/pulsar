"""The native-window wrapper.

Opening a window needs a display and a webview runtime, so what is checked here
is the part that runs on a machine that has neither: the optional dependency is
genuinely optional, and its absence produces an instruction rather than a
traceback. Everything past that point is pywebview's, not ours.
"""
from __future__ import annotations

import builtins

import pytest

from pulsar_research.webapp.desktop import BACKGROUND, DesktopUnavailable, run_window


def test_a_missing_webview_explains_itself_instead_of_crashing(monkeypatch):
    """The console runs in a browser without this extra, and must say so."""
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "webview" or name.startswith("webview."):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    with pytest.raises(DesktopUnavailable) as caught:
        run_window(object(), "http://localhost:8787/", storage_path=object())  # type: ignore[arg-type]

    message = str(caught.value)
    assert "pip install" in message
    assert "pulsar dashboard" in message, "must name the way that still works"


def test_the_window_does_not_flash_a_colour_the_app_never_uses():
    """The pre-paint background has to match the light theme's own `--bg`."""
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent
           / "src" / "pulsar_research" / "webapp" / "frontend"
           / "src" / "styles" / "app.css").read_text(encoding="utf-8")
    assert f"--bg: {BACKGROUND};" in css
