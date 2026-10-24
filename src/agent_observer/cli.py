#!/usr/bin/env python3
"""Agent Observer: install, synchronize, run, and emit observed events."""
import argparse
import contextlib
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

LOCAL_ROOT = Path(__file__).resolve().parents[2]
_manifest = LOCAL_ROOT / "observer.install.json"
_source = json.loads(_manifest.read_text()).get("source") if _manifest.is_file() else None
ROOT = Path(_source) if _source and (Path(_source) / "observer.py").is_file() else LOCAL_ROOT
REGISTRY = ROOT / ".observer/installations.json"
_SHARED = ["observer.py", "README.md", "AGENTS.md", "config/requirements.txt", "config/observer.project.example.json", "docs/README.md", "docs/assets/graph-demo.png", "docs/EVENTS.md", "docs/prompts/INTEGRATE.md", "examples/instrumented_agent.py", "integrations/cad-agent/xray_server.py", "integrations/cad-agent/xray_auth.py"]
def shared_bundle():
    paths = list(_SHARED)
    paths += [str(path.relative_to(ROOT)) for path in (ROOT / "src/agent_observer").rglob("*") if path.is_file() and (path.suffix == ".py" or path.name == "dashboard.html") and "__pycache__" not in path.parts]
    return {name: name for name in paths}
BUNDLE = shared_bundle()
COMPAT_BUNDLE = {"integrations/cad-agent/xray_server.py": "xray_server.py", "integrations/cad-agent/xray_auth.py": "xray_auth.py"}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def read_json(path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)

def registry():
    return read_json(REGISTRY, {"schema_version": 1, "installations": []})

def plan(target, previous, adopt=False, bundle=None):
    changes = []
    for source, installed in (bundle or BUNDLE).items():
        src, dest = ROOT / source, target / installed
        if not src.is_file(): raise ValueError(f"Missing shared source: {src}")
        if dest.is_symlink(): raise ValueError(f"Refusing to replace symlink: {dest}")
        actual, desired = digest(dest), digest(src)
        if actual == desired: continue
        if actual and actual != previous.get(installed) and not adopt:
            raise ValueError(f"Locally edited installation: {dest}. Merge that change into {src} before syncing.")
        changes.append((src, dest))
    return changes

def apply(target, changes, bundle=None):
    target.mkdir(parents=True, exist_ok=True)
    backup = target / ".observer-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    written = []
    try:
        for source, dest in changes:
            old = backup / dest.relative_to(target)
            existed = dest.exists()
            if existed:
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, old)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as stream:
                stream.write(source.read_bytes()); temporary = Path(stream.name)
            try: temporary.replace(dest)
            finally: temporary.unlink(missing_ok=True)
            written.append((dest, old, existed))
    except Exception:
        for dest, old, existed in reversed(written):
            if existed: shutil.copy2(old, dest)
            else: dest.unlink(missing_ok=True)
        raise
    return {installed: digest(target / installed) for installed in (bundle or BUNDLE).values()}

def serialized(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        import fcntl
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        with (REGISTRY.parent / "sync.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return function(*args, **kwargs)
    return wrapped

@serialized
def install(args):
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir(): raise ValueError("Project directory must already exist")
    target = (project / args.dest).resolve()
    if target == ROOT or not target.is_relative_to(project): raise ValueError("Install inside the destination project")
    entries = registry(); current = next((x for x in entries["installations"] if x["target"] == str(target)), None)
    previous = read_json(target / "observer.install.json", {}).get("files", {})
    changes = plan(target, previous, args.adopt_existing)
    files = apply(target, changes)
    config = target / "config/observer.project.json"
    if not config.exists():
        documents = [{"path": "docs", "include": ["**/*.md"]}, {"path": ".", "include": ["*.md"]}]
        if args.profile == "cad-studio" and (project / "ObsidianVault").exists(): documents.append({"path": "ObsidianVault", "include": ["**/*.md"], "group": "ObsidianVault"})
        write_json(config, {"schema_version": 1, "project": {"name": args.name or project.name, "root": os.path.relpath(project, config.parent), "agent_label": args.agent_label}, "storage_profile": args.profile, "documents": documents, "tools_markdown": "TOOLS.md" if (project / "TOOLS.md").exists() else None, "tools": [], "port": args.port, "max_steps": 5000, "max_runs": 50})
    write_json(target / "observer.install.json", {"source": str(ROOT), "project": str(project), "files": files})
    entry = {"project": str(project), "target": str(target)}
    if args.compat_cad:
        compat = project / "scripts"
        metadata_path = compat / ".observer-compat.json"
        metadata = read_json(metadata_path, {})
        compat_changes = plan(compat, metadata.get("files", {}), args.adopt_existing, COMPAT_BUNDLE)
        metadata = {"source": str(ROOT), "installation": os.path.relpath(target, compat), "files": apply(compat, compat_changes, COMPAT_BUNDLE)}
        write_json(metadata_path, metadata)
        entry["compat"] = {"target": str(compat)}
    elif current and current.get("compat"): entry["compat"] = current["compat"]
    if current: entries["installations"].remove(current)
    entries["installations"].append(entry); write_json(REGISTRY, entries)
    print(f"Installed: {target} ({len(changes)} shared files). Project settings preserved.")

def installation_targets(entry):
    yield Path(entry["target"]), shared_bundle(), "observer.install.json"
    if entry.get("compat"):
        yield Path(entry["compat"]["target"]), COMPAT_BUNDLE, ".observer-compat.json"

@serialized
def sync(quiet=False):
    entries = registry()["installations"]
    planned = []
    for entry in entries:
        if not Path(entry["project"]).is_dir(): raise ValueError(f"Project unavailable: {entry['project']}")
        for target, bundle, filename in installation_targets(entry):
            manifest = read_json(target / filename, {})
            changes = plan(target, manifest.get("files", {}), bundle=bundle)
            planned.append((target, bundle, filename, manifest, changes))
    count = 0
    for target, bundle, filename, manifest, changes in planned:
        if not changes: continue
        manifest["files"] = apply(target, changes, bundle);write_json(target / filename, manifest)
        count += len(changes);print(f"Updated {len(changes)} shared files: {target}")
    if not quiet: print(f"Synchronized {len(entries)} installations; {count} files updated. Server code changes require a server restart.")

def watch(interval=1.0):
    last_error = None
    while True:
        try: sync(quiet=True);last_error = None
        except Exception as error:
            if str(error) != last_error: print(f"Sync stopped for conflicting files: {error}", flush=True);last_error = str(error)
        time.sleep(max(interval, 0.5))

def main():
    parser = argparse.ArgumentParser(description="Agent Observer — reusable local agent route viewer")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install");p.add_argument("project");p.add_argument("--dest", default=".agent-observer");p.add_argument("--name");p.add_argument("--agent-label", default="Agent");p.add_argument("--port", type=int, default=8765);p.add_argument("--profile", choices=["project", "cad-studio"], default="project");p.add_argument("--compat-cad", action="store_true", help="Maintain the legacy CAD scripts launchers");p.add_argument("--adopt-existing", action="store_true", help="Back up and adopt existing observer files on first install")
    sub.add_parser("sync");sub.add_parser("status")
    p = sub.add_parser("watch");p.add_argument("--interval", type=float, default=1)
    p = sub.add_parser("run");p.add_argument("--project");p.add_argument("--config");p.add_argument("--port", type=int)
    p = sub.add_parser("emit");p.add_argument("--config", required=True);p.add_argument("--file", default="-", help="JSON event file, or - for stdin")
    args = parser.parse_args()
    if args.command == "install": install(args)
    elif args.command == "sync": sync()
    elif args.command == "watch":
        print("Watching shared sources. Ctrl+C stops synchronization.", flush=True);watch(args.interval)
    elif args.command == "status":
        for entry in registry()["installations"]:
            for target, bundle, filename in installation_targets(entry):
                manifest = read_json(target / filename, {})
                drift = [name for name in bundle.values() if digest(target / name) != manifest.get("files", {}).get(name)]
                pending = [installed for source, installed in bundle.items() if digest(ROOT / source) != digest(target / installed)]
                print(json.dumps({"project":entry["project"], "target":str(target), "locally_changed":drift, "pending_update":pending}, ensure_ascii=False))
    elif args.command == "emit":
        from .observer_client import ObserverClient
        event = json.loads(sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
        client = ObserverClient(config_file=args.config)
        if not client.emit(event): raise ValueError(f"Telemetry was not delivered: {client.last_error}")
        print("Event delivered")
    elif args.command == "run":
        if args.config: os.environ["AGENT_OBSERVER_CONFIG"] = str(Path(args.config).resolve())
        if args.project: os.environ["AGENT_OBSERVER_PROJECT"] = str(Path(args.project).resolve())
        if args.port:
            os.environ["AGENT_OBSERVER_PORT"] = str(args.port)
            sys.argv = [sys.argv[0], "--port", str(args.port)]
        else: sys.argv = [sys.argv[0]]
        threading.Thread(target=watch, daemon=True, name="observer-sync").start()
        from . import observer_server
        observer_server.main()

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: pass
    except Exception as error: print(str(error), file=sys.stderr);sys.exit(1)
