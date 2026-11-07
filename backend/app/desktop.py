"""Desktop launcher for the packaged Windows app.

Starts the FastAPI server (which also serves the statically exported frontend)
in a background thread and opens a native WebView2 window on it. Double-clicking
the packaged exe runs this module through ``packaging/entry.py``.

Run from source with:  python -m app.desktop

Design notes:
  * A windowed (no-console) build has ``sys.stdout``/``sys.stderr`` set to
    ``None``. Libraries that log to them - uvicorn's default logging config
    installs ``StreamHandler(sys.stderr)`` - would then raise on the first log
    line and kill startup. We give those streams a sink and tell uvicorn not to
    reconfigure logging at all (``log_config=None``), so our own file handler
    stays authoritative and nothing can raise.
  * Logging is configured before any application import and every unexpected
    exception is written to ``app.log`` plus a dialog, because a windowed build
    has no console to show a traceback in.
  * Application imports (``.config``, ``.main``, ``webview``) are deliberately
    lazy so a broken install reports *why* instead of vanishing.
  * The server binds 127.0.0.1 only - the app is single-user and never exposes
    itself on the network.
  * If the port is already in use by a healthy instance of this app we do not
    start a second engine; we just open another window on the existing one.
  * If the native window cannot be created (pywebview or the WebView2 runtime
    missing) we fall back to the default browser and keep serving, instead of
    exiting.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

WINDOW_TITLE = "AI Video FPS Enhancer"


def _parse_port(raw: str | None, default: int = 8123) -> int:
    """A malformed AVE_PORT must not crash startup with a ValueError."""
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 65535 else default


DEFAULT_PORT = _parse_port(os.environ.get("AVE_PORT"))

# The WebView2 Runtime's install key (Evergreen bootstrapper GUID); combined with
# the Software / Software\WOW6432Node prefixes below.
_WEBVIEW2_KEY = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

logger = logging.getLogger("app.desktop")


# ------------------------------------------------------------- logging / state
def state_dir() -> Path:
    """Writable per-user folder (same rule as app.config, duplicated so logging
    works even if importing the application itself fails)."""
    override = os.environ.get("AVE_LOCAL_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "AI Video Enhancer"


def log_path() -> Path:
    return state_dir() / "app.log"


def _ensure_std_streams() -> None:
    """Guarantee sys.stdout/sys.stderr are writable objects.

    PyInstaller's windowed bootloader sets both to ``None``; the standard library
    and third-party packages (uvicorn, warnings) write to them unconditionally,
    which turns a stray log line into a startup crash.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - lives for the process
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def _excepthook(exc_type, exc, tb) -> None:  # pragma: no cover - diagnostics path
    logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))


def _thread_excepthook(args) -> None:  # pragma: no cover - diagnostics path
    logger.critical(
        "Unhandled exception in thread %s",
        getattr(args.thread, "name", "?"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def bootstrap_logging() -> Path:
    """Set up file + console logging and global exception hooks. Never raises."""
    path = log_path()
    _ensure_std_streams()
    handlers: list[logging.Handler] = []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, mode="w", encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=handlers or None,
        )
        # A broken log line must never take the app down.
        logging.raiseExceptions = False
        logging.getLogger().setLevel(logging.INFO)
    except Exception:  # noqa: BLE001 - logging must never break startup
        pass
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    return path


def _messagebox(text: str, *, error: bool = False) -> None:
    """Native message box; used only when there is no window to talk to yet."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0, text, WINDOW_TITLE, 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        )
    except Exception:  # noqa: BLE001 - never let a dialog failure mask the real problem
        logger.error(text)


# ------------------------------------------------------------------------- window
def webview2_installed() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for prefix in (r"Software", r"Software\WOW6432Node"):
            try:
                with winreg.OpenKey(hive, f"{prefix}\\{_WEBVIEW2_KEY}") as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
                if value and value != "0.0.0.0":
                    return True
            except OSError:
                continue
    return False


def _splash_html() -> str:
    """A tiny branded loading page so the window is never a blank white box."""
    import base64

    html = """<!doctype html><html><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;background:#09090b;color:#e4e4e7;
    font-family:system-ui,-apple-system,Segoe UI,sans-serif;display:grid;place-items:center}
  .box{display:flex;flex-direction:column;align-items:center;gap:22px}
  .ring{width:38px;height:38px;border-radius:50%;border:3px solid #27272a;border-top-color:#0ea5e9;
    animation:spin .9s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  h1{font-size:15px;font-weight:600;letter-spacing:.2px;margin:0}
  p{font-size:12px;color:#71717a;margin:0}
</style></head><body><div class="box">
  <div class="ring"></div>
  <h1>AI Video FPS Enhancer</h1>
  <p>Loading the AI engine&hellip;</p>
</div></body></html>"""
    return "data:text/html;base64," + base64.b64encode(html.encode("utf-8")).decode("ascii")


def _wait_for_interrupt() -> None:
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        pass


def _browser_fallback(url: str, reason: str) -> None:
    logger.warning("%s; opening the default browser instead (%s)", reason, url)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        logger.exception("Could not open a browser")
    _wait_for_interrupt()


def _open_window(url: str) -> None:
    """Open a native window on the app URL. Never raises: worst case it falls back
    to the default browser and keeps the server running."""
    try:
        import webview
    except Exception as exc:  # noqa: BLE001 - missing/failed pywebview
        _messagebox(
            "The desktop window could not be created, so the app opened in your browser instead:\n\n"
            f"{url}\n\n(app.log has the details)"
        )
        _browser_fallback(url, f"pywebview unavailable: {exc!r}")
        return

    if not webview2_installed():
        _messagebox(
            "Microsoft Edge WebView2 Runtime is required for the app window.\n\n"
            "The app will open in your default browser instead. Install the runtime to get "
            "the native window:\nhttps://developer.microsoft.com/microsoft-edge/webview2/",
        )

    try:
        window = webview.create_window(
            WINDOW_TITLE,
            _splash_html(),
            width=1320,
            height=900,
            min_size=(980, 660),
            resizable=True,
            background_color="#09090b",
        )

        def _navigate_when_ready() -> None:
            try:
                deadline = time.time() + 90
                while time.time() < deadline and not _healthy(f"{url}/api/health"):
                    time.sleep(0.25)
                window.load_url(url)
            except Exception:  # noqa: BLE001
                logger.exception("Could not navigate the window to the app")

        threading.Thread(target=_navigate_when_ready, name="navigate", daemon=True).start()
        logger.info("Opening desktop window on %s", url)
        webview.start()
        logger.info("Desktop window closed")
    except Exception as exc:  # noqa: BLE001 - GUI stack failed at runtime
        _messagebox(
            "The desktop window failed to open, so the app opened in your browser instead:\n\n"
            f"{url}\n\n(app.log has the details)"
        )
        _browser_fallback(url, f"desktop window failed: {exc!r}")


# ------------------------------------------------------------------------ server
def _healthy(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _port_is_ours(port: int) -> bool:
    return _healthy(f"http://127.0.0.1:{port}/api/health")


def _pick_port(preferred: int) -> int:
    """Return `preferred` if free, an ephemeral port otherwise."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])


def _start_server(port: int):
    """Run uvicorn in a background thread; returns (server, thread)."""
    import uvicorn

    from .main import app as fastapi_app

    config = uvicorn.Config(
        app=fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        # Keep our own logging setup: uvicorn's default config installs a
        # StreamHandler(sys.stderr), which is None in a windowed build.
        log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return server, thread


# -------------------------------------------------------------------------- main
def main() -> int:
    log_file = bootstrap_logging()

    try:
        # The engine must serve the exported UI on its own origin.
        os.environ.setdefault("AVE_SERVE_UI", "1")

        from .config import IS_FROZEN, UI_DIR, ensure_dirs

        ensure_dirs()
        logger.info("Starting %s (frozen=%s, python=%s)", WINDOW_TITLE, IS_FROZEN, sys.version.split()[0])

        if not UI_DIR.is_dir():
            logger.warning("No UI bundle at %s - the API runs without serving the interface.", UI_DIR)

        url = f"http://127.0.0.1:{DEFAULT_PORT}"
        if _port_is_ours(DEFAULT_PORT):
            # Another copy of the app is already running: just show it.
            if os.environ.get("AVE_NO_WINDOW") == "1":
                logger.info("Existing instance on port %d and AVE_NO_WINDOW=1; nothing to do", DEFAULT_PORT)
                return 0
            logger.info("Existing instance found on port %d; opening a second window", DEFAULT_PORT)
            _open_window(url)
            return 0

        port = _pick_port(DEFAULT_PORT)
        logger.info("Starting API server on 127.0.0.1:%d", port)
        server, thread = _start_server(port)
        url = f"http://127.0.0.1:{port}"

        deadline = time.time() + 120
        while time.time() < deadline:
            if _healthy(f"{url}/api/health"):
                break
            if not thread.is_alive():
                raise RuntimeError("The API server thread stopped during startup.")
            time.sleep(0.25)
        else:
            raise TimeoutError("The API server did not become healthy in time.")

        logger.info("Engine ready at %s", url)
        try:
            if os.environ.get("AVE_NO_WINDOW") == "1":
                # Headless mode: keep the engine running with no desktop window.
                # Closing the window normally quits the app (and cancels any job
                # still in flight), so automated runs use this instead.
                logger.info("AVE_NO_WINDOW=1 set - running headless, no desktop window")
                _wait_for_interrupt()
            else:
                _open_window(url)
        finally:
            server.should_exit = True
            thread.join(timeout=15)
        logger.info("Shutdown complete")
        return 0
    except Exception:  # noqa: BLE001 - last line of defence for a windowed build
        logger.exception("Fatal error during startup")
        _messagebox(
            "AI Video FPS Enhancer could not start.\n\n"
            f"Details were written to:\n{log_file}\n\n"
            "The most common cause is a missing Microsoft Edge WebView2 Runtime.",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
