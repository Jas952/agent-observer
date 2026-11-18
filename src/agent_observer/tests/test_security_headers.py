"""Exercise framing policy through a real isolated HTTP listener."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


class FramingPolicyTests(unittest.TestCase):
    def test_dashboard_and_default_responses_refuse_embedding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "observer.project.json"
            config.write_text(json.dumps({
                "schema_version": 1,
                "project": {"name": "Framing fixture", "root": str(root)},
                "documents": [], "tools": [],
                "connection": {"module": "project", "enabled": False},
            }))
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = {
                **os.environ,
                "HOME": str(root),
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "AGENT_OBSERVER_PROJECT": str(root),
                "AGENT_OBSERVER_CONFIG": str(config),
                "AGENT_OBSERVER_TOKEN_FILE": str(root / "token"),
                "AGENT_OBSERVER_HISTORY_FILE": str(root / "history.json"),
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_observer.observer_server", "--port", str(port)],
                env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        self.fail("Isolated server exited: " + process.stderr.read().decode(errors="replace"))
                    try:
                        with urllib.request.urlopen(base + "/health", timeout=.5):
                            break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            self.fail("Isolated server did not become ready")
                        time.sleep(.02)
                for path in ("/health", "/", "/dashboard", "/?demo=true", "/?project=/foreign&session=unavailable"):
                    with self.subTest(path=path), urllib.request.urlopen(base + path, timeout=5) as response:
                        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
                        self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stderr.close()
