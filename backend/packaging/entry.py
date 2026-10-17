"""PyInstaller entry point for the packaged app.

PyInstaller runs its entry script as a *top-level* module, so
``app/desktop.py`` cannot be the entry point directly: its relative imports
(``from .config import ...``) would fail with "attempted relative import with no
known parent package". Importing the package properly here keeps ``app`` a real
package inside the frozen archive.

It also keeps a crash-proof startup trace at ``%LOCALAPPDATA%\\AI Video Enhancer\\
startup.log`` (flushed line by line) plus faulthandler, so a windowed build that
dies before logging is up still leaves a readable record - including native
crash stacks.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _trace_path() -> Path:
    base = os.environ.get("AVE_LOCAL_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"),
        "AI Video Enhancer",
    )
    return Path(base) / "startup.log"


def _open_trace():
    try:
        path = _trace_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "w", encoding="utf-8", buffering=1)
    except OSError:
        return None


def _note(trace, message: str) -> None:
    if trace is None:
        return
    try:
        print(message, file=trace)
    except Exception:  # noqa: BLE001 - the trace must never break startup
        pass


def run() -> int:
    trace = _open_trace()
    if trace is not None:
        try:
            import faulthandler

            faulthandler.enable(trace)
        except Exception:  # noqa: BLE001
            pass
    _note(trace, f"entry: frozen={getattr(sys, 'frozen', False)} python={sys.version.split()[0]}")
    _note(trace, f"entry: stdout={sys.stdout!r} stderr={sys.stderr!r}")
    _note(trace, f"entry: _MEIPASS={getattr(sys, '_MEIPASS', None)}")
    try:
        _note(trace, "entry: importing app.desktop")
        from app.desktop import main

        _note(trace, "entry: calling main()")
        code = main()
        _note(trace, f"entry: main returned {code}")
        return code
    except BaseException:  # noqa: BLE001 - record and re-raise
        if trace is not None:
            try:
                traceback.print_exc(file=trace)
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        _note(trace, "entry: exit")


if __name__ == "__main__":
    raise SystemExit(run())
