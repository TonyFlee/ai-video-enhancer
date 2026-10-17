"""Verify the packaged desktop app never pops a terminal (console) window.

The desktop app is a *windowed* process with no console of its own. On Windows,
every console-mode child it starts (ffmpeg, ffprobe, nvidia-smi) gets a brand new
console window allocated for it unless CREATE_NO_WINDOW is passed. This script
starts the packaged app on a free port, hammers the endpoints that spawn those
children, and counts any console windows that appear while it does so.

    python scripts/verify_no_console_windows.py
    python scripts/verify_no_console_windows.py --exe "..\\dist_fixed\\AI Video Enhancer\\AI Video Enhancer.exe"

Exits non-zero if any console window is seen (i.e. the app is still spamming).
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# ConsoleWindowClass = a classic conhost console; PseudoConsoleWindow = the
# pseudo console Windows Terminal creates for one.
CONSOLE_CLASSES = {"consolewindowclass", "pseudoconsolewindow"}


def _process_name(pid: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)
    return ""


def console_windows() -> dict[int, dict]:
    """Every currently visible top-level console window."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: dict[int, dict] = {}

    def cb(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value.lower() not in CONSOLE_CLASSES:
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found[int(hwnd)] = {
            "hwnd": int(hwnd),
            "class": cls.value,
            "title": title.value,
            "pid": pid.value,
            "proc": _process_name(pid.value),
            "visible": bool(user32.IsWindowVisible(hwnd)),
        }
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_healthy(base: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=5) as response:
                if json.loads(response.read().decode())["status"] == "ok":
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def hammer(base: str, rounds: int) -> list[str]:
    errors: list[str] = []
    for _ in range(rounds):
        for path in ("/api/health", "/api/system"):
            try:
                urllib.request.urlopen(base + path, timeout=30).read()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve()
    default_exe = here.parent.parent / "dist" / "AI Video Enhancer" / "AI Video Enhancer.exe"
    parser.add_argument("--exe", default=str(default_exe), help="path to the packaged app")
    parser.add_argument("--requests", type=int, default=20, help="health+system rounds to fire")
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds to wait for startup")
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="hide the GPU from the app (avoids competing with a job on a real GPU)",
    )
    args = parser.parse_args()

    exe = Path(args.exe)
    if not exe.exists():
        print(f"exe not found: {exe}", file=sys.stderr)
        return 2

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "AVE_PORT": str(port)}
    if args.no_gpu:
        env["CUDA_VISIBLE_DEVICES"] = ""

    # Snapshot BEFORE launching: the app also spawns ffmpeg/nvidia-smi while it
    # starts up, which is exactly when the user sees the terminals appear.
    baseline = console_windows()

    print(f"launching {exe}")
    print(f"  port={port}  gpu_hidden={args.no_gpu}")
    process = subprocess.Popen([str(exe)], env=env)
    seen: dict[int, dict] = {}
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            for hwnd, win in console_windows().items():
                if hwnd not in baseline and hwnd not in seen:
                    seen[hwnd] = win
            time.sleep(0.01)

    errors: list[str] = []
    try:
        if not wait_healthy(base, args.timeout):
            print("app never became healthy", file=sys.stderr)
            return 2
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        errors = hammer(base, args.requests)
        stop.set()
        watcher.join(timeout=2)
    finally:
        process.kill()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass

    consoles = [win for win in seen.values() if win["visible"] or win["title"]]
    payload = {
        "exe": str(exe),
        "requests": args.requests * 2,
        "errors": errors,
        "consoleWindows": consoles,
        "pass": not consoles,
    }
    print(json.dumps(payload, indent=2))
    if consoles:
        print("\nFAIL: the app opened terminal windows while probing.", file=sys.stderr)
        return 1
    print(f"\nPASS: {args.requests * 2} requests, zero terminal windows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
