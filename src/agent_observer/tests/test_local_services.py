"""Tests for LocalServiceManager and service_runner guardian lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

# Ensure src/ is on sys.path regardless of execution entry point
_SRC_DIR = Path(__file__).resolve().parents[2]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from agent_observer.local_services import LocalServiceManager, LocalServiceError


FIXTURE_SERVER_CODE = """\
import argparse
import http.server
import json
import os
import signal
import sys
import time
import subprocess

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/"):
            body = json.dumps({"status": "ok", "service": "fixture", "pid": os.getpid()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/unhealthy":
            body = json.dumps({"status": "unhealthy"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pid-file", type=str)
    parser.add_argument("--child-pid-file", type=str)
    parser.add_argument("--sigterm-child-pid-file", type=str)
    parser.add_argument("--match-token", type=str, default="")
    parser.add_argument("--stdout-msg", type=str, default="")
    parser.add_argument("--stderr-msg", type=str, default="")
    args = parser.parse_args()

    if args.pid_file:
        with open(args.pid_file, "w") as f:
            f.write(str(os.getpid()))

    child_proc = None
    if args.child_pid_file:
        child_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdin=subprocess.DEVNULL,
        )
        with open(args.child_pid_file, "w") as f:
            f.write(str(child_proc.pid))

    if args.stdout_msg:
        sys.stdout.write(args.stdout_msg + "\\n")
        sys.stdout.flush()

    if args.stderr_msg:
        sys.stderr.write(args.stderr_msg + "\\n")
        sys.stderr.flush()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), HealthHandler)

    def shutdown_handler(signum, frame):
        # If requested, spawn a child process inside the SIGTERM handler to verify
        # that guardian catches and terminates late-born shutdown descendants.
        if args.sigterm_child_pid_file:
            late_child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)"],
                stdin=subprocess.DEVNULL,
            )
            with open(args.sigterm_child_pid_file, "w") as f:
                f.write(str(late_child.pid))
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            server.server_close()
        except OSError:
            pass

if __name__ == "__main__":
    main()
"""

PARENT_RUNNER_CODE = """\
import json
import os
from pathlib import Path
import sys
import time

# Ensure src/ is on sys.path
src_dir = sys.argv[1]
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from agent_observer.local_services import LocalServiceManager

project_root = sys.argv[2]
graph_port = int(sys.argv[3])
port_owned = int(sys.argv[4])
port_ext = int(sys.argv[5])
fixture_script = sys.argv[6]
owned_pid_file = sys.argv[7]
child_pid_file = sys.argv[8]
sigterm_child_pid_file = sys.argv[9]

settings = {
    "port": graph_port,
    "project_root": project_root,
    "local_services": [
        {
            "id": "test-owned-guardian",
            "name": "Owned Guardian Service",
            "endpoints": [f"http://127.0.0.1:{port_owned}"],
            "health_url": f"http://127.0.0.1:{port_owned}/health",
            "command": [
                sys.executable, fixture_script,
                "--port", str(port_owned),
                "--pid-file", owned_pid_file,
                "--child-pid-file", child_pid_file,
                "--sigterm-child-pid-file", sigterm_child_pid_file,
                "--match-token", "owned_guardian_token",
            ],
            "shutdown_timeout": 1,
        },
        {
            "id": "test-ext-guardian",
            "name": "External Guardian Service",
            "endpoints": [f"http://127.0.0.1:{port_ext}"],
            "health_url": f"http://127.0.0.1:{port_ext}/health",
            "process_match": ["ext_hard_token"],
            "shutdown_timeout": 1,
        },
    ],
}

manager = LocalServiceManager(settings)
manager.start("test-owned-guardian")

# Wait for pid files to be created
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    if os.path.isfile(owned_pid_file) and os.path.isfile(child_pid_file):
        break
    time.sleep(0.05)

# Give a brief moment for guardian process_table to discover child
time.sleep(0.4)

sys.stdout.write("PARENT_RUNNER_READY\\n")
sys.stdout.flush()

while True:
    time.sleep(1)
"""


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_pid_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def wait_port_open(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def wait_port_closed(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                time.sleep(0.05)
        except OSError:
            return True
    return False


class TestLocalServices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture_script = self.root / "fixture_server.py"
        self.fixture_script.write_text(FIXTURE_SERVER_CODE, encoding="utf-8")
        self.procs: list[subprocess.Popen] = []
        self.addCleanup(self._cleanup_processes)

    def _cleanup_processes(self):
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    except Exception:
                        pass
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()

    def _start_external_server(
        self,
        port: int,
        pid_file: Path | None = None,
        child_pid_file: Path | None = None,
        match_token: str = "",
        stdout_msg: str = "",
        stderr_msg: str = "",
    ) -> subprocess.Popen:
        args = [sys.executable, str(self.fixture_script), "--port", str(port)]
        if pid_file:
            args.extend(["--pid-file", str(pid_file)])
        if child_pid_file:
            args.extend(["--child-pid-file", str(child_pid_file)])
        if match_token:
            args.extend(["--match-token", match_token])
        if stdout_msg:
            args.extend(["--stdout-msg", stdout_msg])
        if stderr_msg:
            args.extend(["--stderr-msg", stderr_msg])

        proc = subprocess.Popen(args)
        self.procs.append(proc)
        self.assertTrue(wait_port_open(port), f"Fixture server failed to open port {port}")
        return proc

    def test_service_shortcut_metadata_redacts_credentials_without_changing_launch_argv(self):
        command = [sys.executable, str(self.fixture_script), "--port", str(_find_free_port()),
                   "--api-key", "inline-sensitive", "--password=inline-password", "--token:inline-token",
                   "--stdout-msg", "environment-sensitive"]
        manager = LocalServiceManager({
            "project_root": self.root,
            "local_services": [{
                "id": "shortcut", "name": "Shortcut service", "description": "Local fixture",
                "endpoints": [f"http://127.0.0.1:{command[3]}"], "command": command,
                "environment": {"API_KEY": "environment-sensitive"},
            }],
        })
        self.addCleanup(manager.shutdown)
        snapshot = manager.snapshot()["services"][0]
        for secret in ["inline-sensitive", "inline-password", "inline-token", "environment-sensitive"]:
            self.assertNotIn(secret, snapshot["launch_command"])
        self.assertIn("--port " + command[3], snapshot["launch_command"])
        self.assertIn("[redacted]", snapshot["launch_command"])
        self.assertEqual(snapshot["cwd"], str(self.root.resolve()))
        self.assertEqual(snapshot["description"], "Local fixture")
        self.assertEqual(manager.definitions["shortcut"]["command"], command)
        self.assertEqual(snapshot["status"], "stopped")
        self.assertNotIn("environment", snapshot)

    def test_invalid_and_colliding_endpoints(self):
        graph_port = _find_free_port()
        # 1. Port matching graph's own port must be rejected
        with self.assertRaises(ValueError) as ctx:
            LocalServiceManager({
                "port": graph_port,
                "project_root": str(self.root),
                "local_services": [{
                    "id": "collide-graph-port",
                    "endpoints": [f"http://127.0.0.1:{graph_port}"],
                    "command": [sys.executable, "-c", "pass"],
                }],
            })

        # 2. Non-loopback endpoint must be rejected
        with self.assertRaises(ValueError) as ctx:
            LocalServiceManager({
                "port": graph_port,
                "project_root": str(self.root),
                "local_services": [{
                    "id": "non-loopback",
                    "endpoints": ["http://192.168.1.100:8080"],
                    "command": [sys.executable, "-c", "pass"],
                }],
            })

        # 3. Duplicate service IDs must be rejected
        svc_port = _find_free_port()
        with self.assertRaises(ValueError) as ctx:
            LocalServiceManager({
                "port": graph_port,
                "project_root": str(self.root),
                "local_services": [
                    {"id": "duplicate-id", "endpoints": [f"http://127.0.0.1:{svc_port}"], "command": [sys.executable, "-c", "pass"]},
                    {"id": "duplicate-id", "endpoints": [f"http://127.0.0.1:{svc_port}"], "command": [sys.executable, "-c", "pass"]},
                ],
            })

        # 4. Working directory outside project must be rejected
        with self.assertRaises(ValueError) as ctx:
            LocalServiceManager({
                "port": graph_port,
                "project_root": str(self.root),
                "local_services": [{
                    "id": "bad-cwd",
                    "cwd": "../outside",
                    "endpoints": [f"http://127.0.0.1:{svc_port}"],
                    "command": [sys.executable, "-c", "pass"],
                }],
            })

    def test_owned_service_lifecycle_stdout_stderr_and_stop(self):
        port = _find_free_port()
        pid_file = self.root / "owned.pid"
        settings = {
            "port": _find_free_port(),
            "project_root": str(self.root),
            "local_services": [{
                "id": "test-owned",
                "name": "Test Owned Service",
                "endpoints": [f"http://127.0.0.1:{port}"],
                "health_url": f"http://127.0.0.1:{port}/health",
                "command": [
                    sys.executable, str(self.fixture_script),
                    "--port", str(port),
                    "--pid-file", str(pid_file),
                    "--stdout-msg", "FIXTURE_STDOUT_LINE_MSG",
                    "--stderr-msg", "FIXTURE_STDERR_LINE_MSG",
                ],
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings)
        try:
            started = manager.start("test-owned")
            self.assertEqual(started["owner"], "graph")
            self.assertIn(started["status"], ("starting", "running"))
            self.assertFalse(started["can_start"])
            self.assertTrue(started["can_stop"])
            self.assertTrue(bool(started["identity"]))

            # Poll until healthy and connected
            deadline = time.monotonic() + 5.0
            connected = False
            while time.monotonic() < deadline:
                snap = manager.snapshot()
                svc = snap["services"][0]
                if svc["status"] == "running" and svc["connected"]:
                    connected = True
                    break
                time.sleep(0.05)
            self.assertTrue(connected, "Owned service did not reach running/connected state")

            # Verify worker PID file exists and process is alive
            self.assertTrue(pid_file.is_file(), "Worker PID file was not written")
            worker_pid = int(pid_file.read_text().strip())
            self.assertTrue(is_pid_alive(worker_pid), "Worker process is not alive")

            # Check captured stdout, stderr, and system logs
            logs = manager.logs("test-owned", after=0)
            texts = [line["text"] for line in logs["lines"]]
            streams = [line["stream"] for line in logs["lines"]]
            self.assertTrue(any("Start requested by graph." in t for t in texts))
            self.assertTrue(any("FIXTURE_STDOUT_LINE_MSG" in t for t in texts))
            self.assertTrue(any("FIXTURE_STDERR_LINE_MSG" in t for t in texts))
            self.assertIn("stdout", streams)
            self.assertIn("stderr", streams)
            self.assertIn("system", streams)

            # Check cursor pagination
            cursor = logs["next_cursor"]
            next_logs = manager.logs("test-owned", after=cursor)
            self.assertEqual(len(next_logs["lines"]), 0)

            # Stop the service with valid expected identity
            stopped = manager.stop("test-owned", expected_identity=started["identity"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(stopped["owner"], "none")
            self.assertTrue(stopped["can_start"])
            self.assertFalse(stopped["can_stop"])

            # Verify worker process actually exited
            self.assertTrue(wait_pid_dead(worker_pid, timeout=4.0), "Worker PID did not terminate after stop")
            self.assertTrue(wait_port_closed(port, timeout=4.0), "Port was not closed after stop")
        finally:
            manager.shutdown()

    def test_stale_identity_rejected(self):
        # 1. Owned service stale identity rejected
        port_owned = _find_free_port()
        pid_file = self.root / "owned_stale.pid"
        settings_owned = {
            "port": _find_free_port(),
            "project_root": str(self.root),
            "local_services": [{
                "id": "owned-stale",
                "endpoints": [f"http://127.0.0.1:{port_owned}"],
                "health_url": f"http://127.0.0.1:{port_owned}/health",
                "command": [
                    sys.executable, str(self.fixture_script),
                    "--port", str(port_owned),
                    "--pid-file", str(pid_file),
                ],
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings_owned)
        try:
            started1 = manager.start("owned-stale")
            id1 = started1["identity"]

            # Wrong identity must raise 409
            with self.assertRaises(LocalServiceError) as ctx:
                manager.stop("owned-stale", expected_identity="bogus-uuid-12345")
            self.assertEqual(ctx.exception.status, 409)

            # Worker is still running
            self.assertTrue(wait_port_open(port_owned), "Owned service did not become ready")
            worker_pid = int(pid_file.read_text().strip())
            self.assertTrue(is_pid_alive(worker_pid))

            # Stop with correct id1
            manager.stop("owned-stale", expected_identity=id1)
            self.assertTrue(wait_pid_dead(worker_pid, timeout=4.0))

            # Start again: receives a new identity
            started2 = manager.start("owned-stale")
            id2 = started2["identity"]
            self.assertNotEqual(id1, id2)

            # Stopping with old id1 must raise 409
            with self.assertRaises(LocalServiceError) as ctx:
                manager.stop("owned-stale", expected_identity=id1)
            self.assertEqual(ctx.exception.status, 409)

            # Stop with id2 succeeds
            manager.stop("owned-stale", expected_identity=id2)
        finally:
            manager.shutdown()

        # 2. External service stale identity rejected
        port_ext = _find_free_port()
        ext_pid_file = self.root / "ext_stale.pid"
        ext_proc = self._start_external_server(
            port_ext,
            pid_file=ext_pid_file,
            match_token="ext_stale_token",
        )
        settings_ext = {
            "port": _find_free_port(),
            "project_root": str(self.root),
            "local_services": [{
                "id": "ext-stale",
                "endpoints": [f"http://127.0.0.1:{port_ext}"],
                "health_url": f"http://127.0.0.1:{port_ext}/health",
                "process_match": ["ext_stale_token"],
                "shutdown_timeout": 1,
            }],
        }
        manager_ext = LocalServiceManager(settings_ext)
        try:
            snap = manager_ext.snapshot()
            ext_svc = snap["services"][0]
            ext_id = ext_svc["identity"]
            self.assertTrue(bool(ext_id))

            # Wrong identity on external service must raise 409
            with self.assertRaises(LocalServiceError) as ctx:
                manager_ext.stop("ext-stale", expected_identity="bogus-fingerprint-999")
            self.assertEqual(ctx.exception.status, 409)

            # External process is still running
            ext_pid = int(ext_pid_file.read_text().strip())
            self.assertTrue(is_pid_alive(ext_pid))

            # Valid stop terminates external process
            stopped = manager_ext.stop("ext-stale", expected_identity=ext_id)
            self.assertEqual(stopped["status"], "stopped")
            ext_proc.wait(timeout=4)
            self.assertTrue(wait_pid_dead(ext_pid, timeout=4.0))
        finally:
            manager_ext.shutdown()

    def test_external_service_lifecycle_and_shutdown_survival(self):
        port = _find_free_port()
        pid_file = self.root / "ext_lifecycle.pid"
        ext_proc = self._start_external_server(
            port,
            pid_file=pid_file,
            match_token="ext_lifecycle_token",
        )
        ext_pid = int(pid_file.read_text().strip())

        settings = {
            "port": _find_free_port(),
            "project_root": str(self.root),
            "local_services": [{
                "id": "test-ext",
                "name": "Test External Service",
                "endpoints": [f"http://127.0.0.1:{port}"],
                "health_url": f"http://127.0.0.1:{port}/health",
                "process_match": ["ext_lifecycle_token"],
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings)
        try:
            snap = manager.snapshot()
            svc = snap["services"][0]
            self.assertEqual(svc["owner"], "external")
            self.assertEqual(svc["status"], "running")
            self.assertTrue(svc["connected"])
            self.assertFalse(svc["can_start"])
            self.assertTrue(svc["can_stop"])
            self.assertTrue(bool(svc["identity"]))

            # Calling start on an already-running external service must keep it external
            started = manager.start("test-ext")
            self.assertEqual(started["owner"], "external")
            self.assertEqual(started["status"], "running")

            # Manager shutdown MUST NOT terminate external process
            manager.shutdown()
            self.assertTrue(is_pid_alive(ext_pid), "External process must survive manager shutdown")
            self.assertTrue(wait_port_open(port), "External service port must remain open")

            # A new manager instance can stop the external service with its identity
            manager2 = LocalServiceManager(settings)
            stopped = manager2.stop("test-ext", expected_identity=svc["identity"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(stopped["owner"], "none")
            ext_proc.wait(timeout=4)
            self.assertTrue(wait_pid_dead(ext_pid, timeout=4.0), "External process did not terminate after stop")
        finally:
            if is_pid_alive(ext_pid):
                try:
                    os.kill(ext_pid, signal.SIGKILL)
                except OSError:
                    pass

    def test_wrong_provider_occupied_port_cannot_be_stopped(self):
        port = _find_free_port()
        rogue_pid_file = self.root / "rogue.pid"
        # Rogue process has command match_token 'wrong_rogue_worker'
        rogue_proc = self._start_external_server(
            port,
            pid_file=rogue_pid_file,
            match_token="wrong_rogue_worker",
        )
        rogue_pid = int(rogue_pid_file.read_text().strip())

        settings = {
            "port": _find_free_port(),
            "project_root": str(self.root),
            "local_services": [{
                "id": "cad-expected",
                "name": "CAD Expected Service",
                "endpoints": [f"http://127.0.0.1:{port}"],
                "health_url": f"http://127.0.0.1:{port}/health",
                # Registered service expects 'cad_trusted_backend'
                "process_match": ["cad_trusted_backend"],
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings)
        try:
            snap = manager.snapshot()
            svc = snap["services"][0]
            self.assertEqual(svc["status"], "occupied")
            self.assertFalse(svc["connected"])
            self.assertFalse(svc["can_stop"])
            self.assertFalse(svc["can_start"])
            self.assertIsNone(svc["identity"])

            # Attempting stop must raise 409 and not stop the process
            with self.assertRaises(LocalServiceError) as ctx:
                manager.stop("cad-expected", expected_identity="fake-identity-token")
            self.assertEqual(ctx.exception.status, 409)

            with self.assertRaises(LocalServiceError) as ctx:
                manager.stop("cad-expected", expected_identity="")
            self.assertEqual(ctx.exception.status, 409)

            # Attempting start must raise 409
            with self.assertRaises(LocalServiceError) as ctx:
                manager.start("cad-expected")
            self.assertEqual(ctx.exception.status, 409)

            # Rogue process must remain completely untouched and running
            self.assertTrue(is_pid_alive(rogue_pid), "Rogue process must not be killed")
        finally:
            manager.shutdown()
            if is_pid_alive(rogue_pid):
                try:
                    os.kill(rogue_pid, signal.SIGKILL)
                except OSError:
                    pass

    def test_ambiguous_multi_pid_port_cannot_be_stopped(self):
        port = _find_free_port()
        pid_file_1 = self.root / "multi_1.pid"
        pid_file_2 = self.root / "multi_2.pid"

        # Spawn two listeners on the same port via SO_REUSEPORT
        code = f"""
import os, socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', {port}))
s.listen(5)
with open(sys.argv[1], 'w') as f:
    f.write(str(os.getpid()))
while True:
    time.sleep(1)
"""
        proc1 = subprocess.Popen([sys.executable, "-c", code, str(pid_file_1)])
        proc2 = subprocess.Popen([sys.executable, "-c", code, str(pid_file_2)])
        self.procs.extend([proc1, proc2])

        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if pid_file_1.is_file() and pid_file_2.is_file():
                break
            time.sleep(0.05)
        self.assertTrue(wait_port_open(port))

        pid1 = int(pid_file_1.read_text().strip())
        pid2 = int(pid_file_2.read_text().strip())

        settings = {
            "port": _find_free_port(),
            "project_root": str(self.root),
            "local_services": [{
                "id": "multi-pid-svc",
                "endpoints": [f"http://127.0.0.1:{port}"],
                "process_match": ["python"],
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings)
        try:
            snap = manager.snapshot()
            svc = snap["services"][0]
            # Ambiguous multi-PID: len(infos) > 1 -> expected is False
            self.assertEqual(svc["status"], "occupied")
            self.assertFalse(svc["can_stop"])
            self.assertIsNone(svc["identity"])

            with self.assertRaises(LocalServiceError) as ctx:
                manager.stop("multi-pid-svc", expected_identity="any_id")
            self.assertEqual(ctx.exception.status, 409)

            # Neither process should have been killed
            self.assertTrue(is_pid_alive(pid1))
            self.assertTrue(is_pid_alive(pid2))
        finally:
            manager.shutdown()

    def test_hard_parent_death_cleans_owned_and_child_leaves_external(self):
        """Simulate SIGKILL on parent manager to ensure guardian terminates child process group
        (including child born inside SIGTERM handler) but leaves external service running."""
        port_owned = _find_free_port()
        port_ext = _find_free_port()
        graph_port = _find_free_port()

        ext_pid_file = self.root / "ext_hard.pid"
        owned_pid_file = self.root / "owned_hard.pid"
        child_pid_file = self.root / "child_hard.pid"
        sigterm_child_pid_file = self.root / "sigterm_child_hard.pid"
        parent_runner_script = self.root / "parent_runner.py"
        parent_runner_script.write_text(PARENT_RUNNER_CODE, encoding="utf-8")

        # 1. Start external process
        ext_proc = self._start_external_server(
            port_ext,
            pid_file=ext_pid_file,
            match_token="ext_hard_token",
        )
        ext_pid = int(ext_pid_file.read_text().strip())

        # 2. Start parent runner process
        parent_proc = subprocess.Popen(
            [
                sys.executable,
                str(parent_runner_script),
                str(_SRC_DIR),
                str(self.root),
                str(graph_port),
                str(port_owned),
                str(port_ext),
                str(self.fixture_script),
                str(owned_pid_file),
                str(child_pid_file),
                str(sigterm_child_pid_file),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.procs.append(parent_proc)

        # 3. Wait for parent readiness
        ready_line = parent_proc.stdout.readline().strip()
        self.assertEqual(ready_line, "PARENT_RUNNER_READY")

        # Verify initial processes are alive
        owned_pid = int(owned_pid_file.read_text().strip())
        child_pid = int(child_pid_file.read_text().strip())
        self.assertTrue(is_pid_alive(owned_pid), "Owned worker process must be alive")
        self.assertTrue(is_pid_alive(child_pid), "Child descendant process must be alive")
        self.assertTrue(is_pid_alive(ext_pid), "External process must be alive")
        self.assertTrue(wait_port_open(port_owned))
        self.assertTrue(wait_port_open(port_ext))

        # 4. Simulate hard crash of graph parent (kill -9)
        os.kill(parent_proc.pid, signal.SIGKILL)
        parent_proc.wait()

        # 5. Wait for SIGTERM-born child PID file to be created
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if sigterm_child_pid_file.is_file():
                break
            time.sleep(0.05)
        self.assertTrue(sigterm_child_pid_file.is_file(), "Child born in SIGTERM handler was not recorded")
        sigterm_child_pid = int(sigterm_child_pid_file.read_text().strip())
        self.addCleanup(lambda: is_pid_alive(sigterm_child_pid) and os.kill(sigterm_child_pid, signal.SIGKILL))

        # 6. Guardian must detect EOF on liveness pipe and clean owned worker, descendant child,
        # AND late child born inside SIGTERM handler
        self.assertTrue(wait_pid_dead(owned_pid, timeout=5.0), "Owned worker was not terminated by guardian")
        self.assertTrue(wait_pid_dead(child_pid, timeout=5.0), "Child descendant was not terminated by guardian")
        self.assertTrue(wait_pid_dead(sigterm_child_pid, timeout=5.0), "Child born in SIGTERM handler was not cleaned by guardian")
        self.assertTrue(wait_port_closed(port_owned, timeout=5.0), "Owned port was not closed after guardian cleanup")

        # 7. External process MUST survive unaffected
        self.assertTrue(is_pid_alive(ext_pid), "External process must survive parent hard death")
        self.assertTrue(wait_port_open(port_ext), "External service port must remain open")

    def test_log_redaction_cursor_bounds_and_files(self):
        port = _find_free_port()
        log_file = self.root / "custom_service.log"
        # Test quoted JSON and secret in external log file
        log_file.write_text(
            'Initial log: Bearer secret_file_token_abc\n'
            'Config snippet: {"api_key": "file_json_key", "authorization": "file_json_auth", "password": "file_json_pass"}\n'
            'Known secret leak: external_env_secret_val\n',
            encoding="utf-8",
        )

        settings = {
            "port": _find_free_port(),
            "project_root": str(self.root),
            "local_services": [{
                "id": "log-test",
                "name": "Log Test Service",
                "endpoints": [f"http://127.0.0.1:{port}"],
                "health_url": f"http://127.0.0.1:{port}/health",
                "command": [
                    sys.executable, str(self.fixture_script),
                    "--port", str(port),
                    "--stdout-msg", '\x1b[32mOK:\x1b[0m {"api_key": "top_secret_key_123", "token": "bearer_tok_456", "password": "pass_secret_789"}',
                    "--stderr-msg", "ERR: top_secret_key_123 found in stderr and external_env_secret_val",
                ],
                "environment": {
                    "CAD_SECRET_API_KEY": "top_secret_key_123",
                    "EXTERNAL_SERVICE_PASSWORD": "external_env_secret_val",
                },
                "log_files": ["custom_service.log"],
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings)
        try:
            started = manager.start("log-test")
            # Wait until healthy
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                snap = manager.snapshot()
                if snap["services"][0]["connected"]:
                    break
                time.sleep(0.05)

            # Check logs
            logs = manager.logs("log-test", after=0)
            all_text = " ".join(line["text"] for line in logs["lines"])

            # Verify redaction of environment secrets
            self.assertNotIn("top_secret_key_123", all_text)
            self.assertNotIn("external_env_secret_val", all_text)
            self.assertNotIn("secret_file_token_abc", all_text)

            # Verify redaction of quoted JSON fields
            self.assertNotIn("bearer_tok_456", all_text)
            self.assertNotIn("pass_secret_789", all_text)
            self.assertNotIn("file_json_key", all_text)
            self.assertNotIn("file_json_auth", all_text)
            self.assertNotIn("file_json_pass", all_text)

            # Verify ANSI sequences stripped
            self.assertNotIn("\x1b[32m", all_text)
            self.assertNotIn("\x1b[0m", all_text)

            # Verify literal strings
            self.assertIn("OK:", all_text)
            self.assertIn("Initial log:", all_text)

            # Verify cursor validation
            with self.assertRaises(LocalServiceError) as ctx:
                manager.logs("log-test", after=-1)
            self.assertEqual(ctx.exception.status, 400)

            with self.assertRaises(LocalServiceError) as ctx:
                manager.logs("log-test", after="invalid_cursor")  # type: ignore
            self.assertEqual(ctx.exception.status, 400)

            # Verify cursor advancement
            next_cursor = logs["next_cursor"]
            subsequent = manager.logs("log-test", after=next_cursor)
            self.assertEqual(len(subsequent["lines"]), 0)
            self.assertEqual(subsequent["next_cursor"], next_cursor)

            manager.stop("log-test", expected_identity=started["identity"])
        finally:
            manager.shutdown()

    def test_external_file_logs_hold_partial_secret_records_and_discard_oversized_records(self):
        secret = "sensitive-fragmented-credential"
        log = self.root / "external.log"
        log.write_text("Header remains\nSplit credential: " + secret[:10], encoding="utf-8")
        manager = LocalServiceManager({
            "project_root": str(self.root),
            "port": _find_free_port(),
            "local_services": [{
                "id": "external-log",
                "endpoints": [f"http://127.0.0.1:{_find_free_port()}"],
                "environment": {"EXTERNAL_API_KEY": secret},
                "log_files": ["external.log"],
            }],
        })
        try:
            first = manager.logs("external-log")
            self.assertTrue(any("Header remains" in line["text"] for line in first["lines"]))
            self.assertFalse(any(secret[:10] in line["text"] for line in first["lines"]))
            with log.open("a", encoding="utf-8") as stream:
                stream.write(secret[10:] + "\nComplete record remains\n")
            second = manager.logs("external-log", first["next_cursor"])
            text = "\n".join(line["text"] for line in second["lines"])
            self.assertNotIn(secret, text)
            self.assertNotIn(secret[10:], text)
            self.assertIn("Complete record remains", text)
            with log.open("a", encoding="utf-8") as stream:
                stream.write("x" * 9000 + secret[:10])
            partial = manager.logs("external-log", second["next_cursor"])
            with log.open("a", encoding="utf-8") as stream:
                stream.write(secret[10:] + "\nFollowing record remains\n")
            final = manager.logs("external-log", partial["next_cursor"])
            text = "\n".join(line["text"] for line in partial["lines"] + final["lines"])
            self.assertNotIn(secret[:10], text)
            self.assertNotIn(secret[10:], text)
            self.assertIn("Following record remains", text)
        finally:
            manager.shutdown()

    def test_credential_helper_alias_and_unexpected_worker_exit_error(self):
        port = _find_free_port()
        helper_val = "helper-secret-val-999"
        settings = {
            "project_root": str(self.root),
            "port": _find_free_port(),
            "local_services": [{
                "id": "helper-svc",
                "endpoints": [f"http://127.0.0.1:{port}"],
                "command": [sys.executable, "-c", "import sys, time; time.sleep(0.1); sys.exit(0)"],
                "credential_helper": {
                    "env": "TEST_HELPER_KEY",
                    "argv": [sys.executable, "-c", f"print('{helper_val}')"],
                },
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings)
        try:
            started = manager.start("helper-svc")
            self.assertEqual(started["owner"], "graph")
            # Wait for short-lived process to exit unexpectedly without stop request
            deadline = time.monotonic() + 3.0
            view = None
            while time.monotonic() < deadline:
                snap = manager.snapshot()
                view = snap["services"][0]
                if view["status"] == "error":
                    break
                time.sleep(0.05)
            self.assertIsNotNone(view)
            self.assertEqual(view["status"], "error")
            self.assertIn("exited", view["error"].lower())
        finally:
            manager.shutdown()

    def test_preexisting_file_logs_precede_start_marker(self):
        port = _find_free_port()
        log_path = self.root / "preexisting.log"
        log_path.write_text("Old log record from yesterday\n", encoding="utf-8")
        settings = {
            "project_root": str(self.root),
            "port": _find_free_port(),
            "local_services": [{
                "id": "preexisting-log-svc",
                "endpoints": [f"http://127.0.0.1:{port}"],
                "command": [sys.executable, "-c", "import time; time.sleep(1)"],
                "log_files": ["preexisting.log"],
                "shutdown_timeout": 1,
            }],
        }
        manager = LocalServiceManager(settings)
        try:
            manager.start("preexisting-log-svc")
            logs = manager.logs("preexisting-log-svc", after=0)
            texts = [line["text"] for line in logs["lines"]]
            self.assertTrue(any("Old log record from yesterday" in t for t in texts))
            old_idx = next(i for i, t in enumerate(texts) if "Old log record from yesterday" in t)
            start_idx = next(i for i, t in enumerate(texts) if "Start requested by graph" in t)
            # Pre-existing logs must precede the start marker
            self.assertLess(old_idx, start_idx)
            # And pre-existing log record is tagged as prior
            self.assertIn("(prior)", texts[old_idx])
        finally:
            manager.shutdown()

    def test_http_listener_identity_requires_the_selected_project_directory(self):
        project_a, project_b = self.root / "a", self.root / "b"
        project_a.mkdir()
        project_b.mkdir()
        port = _find_free_port()
        proc = subprocess.Popen([sys.executable, str(self.fixture_script), "--port", str(port)],
                                cwd=project_b, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(proc)
        self.assertTrue(wait_port_open(port))
        definition = {"id":"site", "endpoints":[f"http://127.0.0.1:{port}"], "health_kind":"http"}
        manager_a = LocalServiceManager({"project_root":project_a, "local_services":[definition]})
        manager_b = LocalServiceManager({"project_root":project_b, "local_services":[definition]})
        self.addCleanup(manager_a.shutdown)
        self.addCleanup(manager_b.shutdown)
        wrong = manager_a.snapshot()["services"][0]
        self.assertEqual(wrong["status"], "occupied")
        self.assertFalse(wrong["can_stop"])
        self.assertIsNone(wrong["identity"])
        manager_a.shutdown()
        self.assertIsNone(proc.poll())
        correct = manager_b.snapshot()["services"][0]
        self.assertEqual(correct["status"], "running")
        self.assertTrue(correct["can_stop"])
        manager_b.stop("site", correct["identity"])
        proc.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
