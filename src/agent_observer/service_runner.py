"""Foreground service guardian. The graph's liveness pipe owns its lifetime."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time


def process_table():
    with subprocess.Popen(["ps", "-axo", "pid=,ppid=,pgid=,lstart="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as probe:
        try:
            output, _ = probe.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            probe.kill()
            probe.wait()
            raise
        if probe.returncode:
            raise subprocess.CalledProcessError(probe.returncode, "ps")
        probe_pid = probe.pid
    table = {}
    for line in output.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and int(parts[0]) != probe_pid:
            table[int(parts[0])] = (int(parts[1]), parts[3], int(parts[2]))
    return table


def descendants(root, table):
    found = {root}
    for _ in range(len(table)):
        extra = {pid for pid, info in table.items() if info[0] in found}
        if extra <= found:
            break
        found.update(extra)
    return {pid: table[pid][1] for pid in found if pid in table}


def signal_if_current(pid, identity, signum, table):
    if pid in table and table[pid][1] == identity:
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass


def main():
    alive_fd = int(sys.argv[1])
    config = json.loads(sys.stdin.readline())
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    def watch_parent():
        try:
            while os.read(alive_fd, 1):
                pass
        finally:
            os.close(alive_fd)
            stopping.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    # The guardian remains the session/group leader until all inherited workers die.
    # Its live PGID cannot be reused, even if the foreground worker exits early.
    group = os.getpgrp()
    if group != os.getpid():
        raise ValueError("Guardian requires its own process group.")
    process = subprocess.Popen(config["command"], cwd=config["cwd"], env=config["environment"], stdin=subprocess.DEVNULL)
    known = {}
    next_scan = 0.0
    try:
        while process.poll() is None and not stopping.wait(0.15):
            if time.monotonic() >= next_scan:
                known.update(descendants(process.pid, process_table()))
                next_scan = time.monotonic() + 1
        known.update(descendants(process.pid, process_table()))
        if process.poll() is None:
            process.terminate()
        deadline = time.monotonic() + config["shutdown_timeout"]
        while process.poll() is None and time.monotonic() < deadline:
            known.update(descendants(process.pid, process_table()))
            time.sleep(0.15)
    finally:
        # Also collect children born during graceful shutdown. Detached descendants
        # require exact PID/start identities; inherited children share our live PGID.
        try:
            table = process_table()
            known.update(descendants(process.pid, table))
            for pid, identity in known.items():
                signal_if_current(pid, identity, signal.SIGTERM, table)
            os.killpg(group, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                process.poll()
                table = process_table()
                inherited = any(pid != os.getpid() and info[2] == group for pid, info in table.items())
                detached = any(pid in table and table[pid][1] == identity for pid, identity in known.items())
                if not inherited and not detached:
                    break
                time.sleep(0.1)
            for pid, identity in known.items():
                signal_if_current(pid, identity, signal.SIGKILL, table)
            if any(pid != os.getpid() and info[2] == group for pid, info in table.items()):
                os.killpg(group, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            # Never abandon the owned process group because inspection failed.
            os.killpg(group, signal.SIGKILL)
    process.wait(timeout=3)
    return process.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Service guardian could not start or finish the configured worker.", file=sys.stderr, flush=True)
        sys.exit(1)
