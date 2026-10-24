#!/usr/bin/env python3
"""Compatibility entry point for the installed Agent Observer package."""
import json
import os
from pathlib import Path
import sys
_here = Path(__file__).resolve().parent
_manifest = json.loads((_here / ".observer-compat.json").read_text(encoding="utf-8"))
_installation = (_here / _manifest["installation"]).resolve()
sys.path.insert(0, str(_installation / "src"))
sys.pycache_prefix = str(_installation / "var/cache/python")
os.environ["AGENT_OBSERVER_CONFIG"] = str(_installation / "config/observer.project.json")
from agent_observer.observer_server import *
if __name__ == "__main__":
    main()
