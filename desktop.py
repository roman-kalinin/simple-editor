"""Standalone desktop window for Simple Video Editor.

Starts the existing HTTP backend (server.py) on a local port in a background
thread, then opens a native window pointed at it using pywebview. On Windows 11
this uses the built-in Edge WebView2 runtime, so there's no browser tab, URL
bar, or second browser process — it looks and behaves like a native app.

The whole video-editing UI (index.html / app.js / style.css) and all ffmpeg
work are reused unchanged from the browser build; only the host window differs.

Run directly for a dev window:   python desktop.py
Packaged form is built via SimpleEditor.spec (target "SimpleEditorApp").
"""
import sys
import threading

import webview

import server


def _run_server(srv):
    try:
        srv.serve_forever()
    except Exception:  # noqa: BLE001
        pass


def main():
    # Bind the backend first so we know the real URL (port may shift if 8765
    # is taken by a stale instance).
    srv, url = server.start_server()

    t = threading.Thread(target=_run_server, args=(srv,), daemon=True)
    t.start()

    window = webview.create_window(
        "Simple Video Editor",
        url,
        width=1280,
        height=820,
        min_size=(900, 600),
        # Let dropped files hit the page's own drag/drop handlers.
        text_select=False,
    )

    # Clean shutdown of the HTTP server when the window closes.
    def _on_closed():
        try:
            srv.shutdown()
        except Exception:  # noqa: BLE001
            pass

    window.events.closed += _on_closed

    # gui=None lets pywebview pick the best backend (EdgeChromium/WebView2 on
    # Windows). Explicit fallbacks keep it from silently failing on odd setups.
    try:
        webview.start(gui="edgechromium")
    except Exception:  # noqa: BLE001
        try:
            webview.start()
        except Exception as e:  # noqa: BLE001
            # Last resort: fall back to the browser so the user isn't stuck.
            import webbrowser
            print(f"WebView failed ({e}); opening in browser instead.", file=sys.stderr)
            webbrowser.open(url)
            _run_server(srv)


if __name__ == "__main__":
    main()
