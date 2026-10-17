"""Run child processes without ever flashing a console window.

The packaged desktop app is built windowed (``console=False``), so it has no
console of its own. On Windows, whenever such a process starts a console-mode
child (``ffmpeg``, ``ffprobe``, ``nvidia-smi``) the system allocates a *brand
new* console window for that child unless ``CREATE_NO_WINDOW`` is passed. That is
what made the app spray terminal windows every time the UI polled ``/api/health``
or ``/api/system``.

Route every spawn through :func:`run` / :func:`popen` so the flag is never
forgotten. On non-Windows platforms the flag is ``0`` and is ignored.
"""
from __future__ import annotations

import subprocess
from typing import Any

# 0 on non-Windows platforms, where creationflags is not used at all.
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd: Any, **kwargs: Any):
    """``subprocess.run`` that never opens a console window."""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def popen(cmd: Any, **kwargs: Any) -> subprocess.Popen:
    """``subprocess.Popen`` that never opens a console window."""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.Popen(cmd, **kwargs)
