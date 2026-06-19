"""Free a TCP port by terminating processes that listen on it."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time


def _pids_from_lsof(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    return {int(line) for line in result.stdout.split() if line.strip().isdigit()}


def _pids_from_fuser(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["fuser", f"{port}/tcp"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    pids: set[int] = set()
    for part in (result.stdout + " " + result.stderr).split():
        if part.isdigit():
            pids.add(int(part))
    return pids


def _pids_from_ss(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    return {int(m) for m in re.findall(r"pid=(\d+)", result.stdout)}


def find_listening_pids(port: int) -> set[int]:
    """Return PIDs of processes listening on ``port``."""
    pids = _pids_from_lsof(port)
    if not pids:
        pids = _pids_from_fuser(port)
    if not pids:
        pids = _pids_from_ss(port)
    return pids


def free_port(port: int, *, host: str = "127.0.0.1") -> list[int]:
    """Terminate processes listening on ``port``. Returns killed PIDs."""
    del host  # all listeners on port; sufficient for local dev tool
    my_pid = os.getpid()
    targets = find_listening_pids(port) - {my_pid}
    if not targets:
        return []

    killed: list[int] = []
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, PermissionError):
            continue

    if killed:
        time.sleep(0.25)
        remaining = find_listening_pids(port) - {my_pid}
        for pid in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
                if pid not in killed:
                    killed.append(pid)
            except (ProcessLookupError, PermissionError):
                continue

    return killed
