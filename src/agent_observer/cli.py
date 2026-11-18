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
_SHARED = ["observer.py", "README.md", "AGENTS.md", "config/requirements.txt", "config/observer.project.example.json", "docs/README.md", "docs/assets/graph-demo.png", "examples/instrumented_agent.py", "integrations/cad-agent/xray_server.py", "integrations/cad-agent/xray_auth.py", "integrations/omp/observer.mjs", "integrations/codex/observer.py"]
_SHARED += ["config/omp-reference.json"]
def shared_bundle():
    paths = list(_SHARED)
    paths += [str(path.relative_to(ROOT)) for path in (ROOT / "src/agent_observer").rglob("*") if path.is_file() and (path.suffix == ".py" or path.name == "dashboard.html" or path.parent.name == "web" and path.suffix in {".js", ".css"}) and "__pycache__" not in path.parts]
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
    if target.is_symlink():
        raise ValueError(f"Refusing symlinked installation: {target}")
    for source, installed in (bundle or BUNDLE).items():
        src, dest = ROOT / source, target / installed
        if not src.is_file(): raise ValueError(f"Missing shared source: {src}")
        if not dest.parent.resolve().is_relative_to(target.resolve()):
            raise ValueError(f"Refusing managed file outside installation: {dest}")
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

def preflight_installation(project: Path, target: Path, args):
    """Reject malformed, conflicting, or symlinked files before any filesystem mutations."""
    if not project.is_dir():
        raise ValueError("Project directory must already exist")
    if project in {Path("/"), Path.home().resolve()}:
        raise ValueError("Choose a project directory, not the filesystem root or home directory")
    if target.resolve() in {ROOT, project} or not target.resolve().is_relative_to(project):
        raise ValueError("Install inside the destination project")
    if target.is_symlink():
        raise ValueError(f"Refusing to install into symlink target: {target}")
    for managed in (target / "observer.install.json", target / ".gitignore", target / "config/observer.project.json"):
        for path in (managed, *managed.parents):
            if path == project:
                break
            if path.is_symlink():
                raise ValueError(f"Refusing symlinked installation path: {path}")
    port = getattr(args, "port", 8766)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("Choose a local port from 1024 through 65535")
    documents = getattr(args, "documents", None)
    if documents is not None and (not isinstance(documents, list) or any(not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("include"), list) or any(not isinstance(pattern, str) for pattern in item["include"]) for item in documents)):
        raise ValueError("Document sources require path and include-pattern lists")

    agent_type = getattr(args, "agent", "generic")
    if agent_type == "codex":
        from .codex_hooks import _read_hooks
        _read_hooks(project)

    config_path = target / "config/observer.project.json"
    if config_path.is_symlink():
        raise ValueError(f"Refusing symlinked project config: {config_path}")
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Project config must be a JSON object: {config_path}")
        except Exception as exc:
            raise ValueError(f"Malformed JSON in existing project config {config_path}: {exc}")

@serialized
def install(args):
    project = Path(args.project).expanduser().resolve()
    target = project / args.dest
    preflight_installation(project, target, args)
    target = target.resolve()
    entries = registry(); current = next((x for x in entries["installations"] if x["target"] == str(target)), None)
    previous = read_json(target / "observer.install.json", {}).get("files", {})
    changes = plan(target, previous, args.adopt_existing)
    files = apply(target, changes)
    config = target / "config/observer.project.json"
    agent_type = getattr(args, "agent", "generic")
    if not config.exists():
        documents = getattr(args, "documents", None)
        if documents is None:
            documents = [{"path": "docs", "include": ["**/*.md"]}, {"path": ".", "include": ["*.md"]}]
        if args.profile == "cad-studio" and (project / "ObsidianVault").exists(): documents.append({"path": "ObsidianVault", "include": ["**/*.md"], "group": "ObsidianVault"})
        cfg_data = {"schema_version": 1, "project": {"name": args.name or project.name, "root": os.path.relpath(project, config.parent), "agent_label": args.agent_label}, "storage_profile": args.profile, "documents": documents, "tools_markdown": "TOOLS.md" if (project / "TOOLS.md").exists() else None, "tools": [], "port": args.port, "max_steps": 5000, "max_runs": 0}
        if agent_type == "codex":
            cfg_data["agent_runtime"] = "codex"
        write_json(config, cfg_data)
    elif agent_type == "codex":
        cfg_data = read_json(config, {})
        if isinstance(cfg_data, dict):
            cfg_data["agent_runtime"] = "codex"
            write_json(config, cfg_data)
    if config.exists() and getattr(args, "documents", None) is not None:
        cfg_data = read_json(config, {})
        cfg_data["documents"] = args.documents
        write_json(config, cfg_data)
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
    if agent_type == "omp":
        setup_omp_integration(project, target)
    elif agent_type == "codex":
        setup_codex_integration(project, target, executable=getattr(args, "executable", None))
    print(f"Installed: {target} ({len(changes)} shared files). Project settings preserved.")
def setup_codex_integration(project: Path, target: Path, executable: str | None = None):
    """Installs project .codex/hooks.json lifecycle hooks, records optional executable, and safe capability snapshot."""
    from .codex_hooks import install_codex_hooks
    from .capabilities import discover_capabilities, save_capabilities_snapshot
    config_dir = target / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = config_dir / "observer.project.json"
    cfg_data = read_json(cfg_file, {}) if cfg_file.is_file() else {}
    if isinstance(cfg_data, dict):
        cfg_data["agent_runtime"] = "codex"
        if executable:
            cfg_data.setdefault("codex", {})["executable"] = executable
        write_json(cfg_file, cfg_data)

    # Install hooks after config is updated
    install_codex_hooks(project, target)

    # Ensure gitignore protects target var
    target_ignore = target / ".gitignore"
    ignore_entries = {"var/", "*.tmp", "hooks.lock", ".observer-backups/"}
    existing = target_ignore.read_text(encoding="utf-8") if target_ignore.is_file() else ""
    missing = sorted(ignore_entries - set(existing.splitlines()))
    if missing:
        target_ignore.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + "\n".join(missing) + "\n", encoding="utf-8")

    # Discover capabilities with cfg_file so agent_runtime='codex' takes effect
    try:
        inventory = discover_capabilities(project_root=project, config_file=cfg_file)
        snapshot_file = save_capabilities_snapshot(config_dir, inventory)
        mcp_count = len(inventory.get("mcp", []))
        skill_count = len(inventory.get("skills", []))
        rtk_status = next((i["availability"] for i in inventory.get("integrations", []) if i["name"] == "rtk"), "unknown")
        print(f"Generated safe capability snapshot: {snapshot_file} ({mcp_count} MCP, {skill_count} skills, RTK: {rtk_status}).")
    except Exception as exc:
        print(f"Capability snapshot skipped: {exc}", file=sys.stderr)
    print(f"Registered Codex lifecycle hooks (14 events) in {project / '.codex/hooks.json'}")
def setup_omp_integration(project: Path, target: Path):
    """Installs project .omp/extensions/agent-observer.ts shim and initial capability snapshot."""
    from .capabilities import discover_capabilities, save_capabilities_snapshot
    ext_dir = project / ".omp/extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    shim_path = ext_dir / "agent-observer.ts"
    adapter_path = target / "integrations/omp/observer.mjs"
    rel_adapter = os.path.relpath(adapter_path, ext_dir).replace("\\", "/")
    exact_header = "// Agent Observer OMP extension shim\n// Managed by Agent Observer. Loads on next session/reload."
    shim_content = f"{exact_header}\nimport observerExtension from \"{rel_adapter}\";\nexport default observerExtension;\n"
    if shim_path.is_file():
        try:
            content = shim_path.read_text(encoding="utf-8")
            if not content.startswith(exact_header + "\n"):
                print(f"Preserved unowned extension file: {shim_path} (not overwritten). To link Agent Observer, import '{rel_adapter}' manually.", file=sys.stderr)
            else:
                shim_path.write_text(shim_content, encoding="utf-8")
                print(f"Updated OMP extension shim: {shim_path}")
        except OSError as e:
            print(f"Failed to inspect or update extension file {shim_path}: {e}", file=sys.stderr)
    else:
        shim_path.write_text(shim_content, encoding="utf-8")
        print(f"Installed OMP extension shim: {shim_path}")

    inventory = discover_capabilities(project, agent="omp")
    config_dir = target / "config"
    snapshot_file = save_capabilities_snapshot(config_dir, inventory)
    mcp_count = len(inventory.get("mcp", []))
    skill_count = len(inventory.get("skills", []))
    rtk_status = next((i["availability"] for i in inventory.get("integrations", []) if i["name"] == "rtk"), "unknown")
    print(f"Generated safe capability snapshot: {snapshot_file} ({mcp_count} MCP, {skill_count} skills, RTK: {rtk_status}).")
    print("Note: a new OMP launch loads the extension; an already-running session is observed through its journal.")


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
        if not changes:
            # Adopt identical newly managed files without copying or claiming user edits.
            files = dict(manifest.get("files", {}))
            for source, installed in bundle.items():
                current = digest(target / installed)
                if current is not None and current != files.get(installed) and current == digest(ROOT / source):
                    files[installed] = current
            if files != manifest.get("files", {}):
                manifest["files"] = files
                write_json(target / filename, manifest)
            continue
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
    p = sub.add_parser("connect", help="Connect a project agent or observe Codex/OMP without changing their models")
    p.add_argument("project");p.add_argument("--module", choices=["project", "codex", "omp"], default=None)
    p.add_argument("--role", "--repository-mode", dest="repository_mode", choices=["agent_system", "working_repository"], default=None, help="Repository role: Project agent or an external Codex/OMP development workspace")
    p.add_argument("--no-run", action="store_true", help="Configure the module without launching the graph")
    p = sub.add_parser("install");p.add_argument("project");p.add_argument("--dest", default=".agent-observer");p.add_argument("--name");p.add_argument("--agent-label", default="Agent");p.add_argument("--agent", choices=["generic", "omp", "cad", "codex"], default="generic", help="Target agent environment");p.add_argument("--port", type=int, default=8766);p.add_argument("--profile", choices=["project", "cad-studio"], default="project");p.add_argument("--compat-cad", action="store_true", help="Maintain the legacy CAD scripts launchers");p.add_argument("--adopt-existing", action="store_true", help="Back up and adopt existing observer files on first install");p.add_argument("--executable", default=None, help="Explicit path to Codex executable")
    p = sub.add_parser("connect-omp", help="Connect OMP agent to Observer with extension shim and capability snapshot");p.add_argument("project");p.add_argument("--dest", default=".agent-observer");p.add_argument("--name");p.add_argument("--agent-label", default="OMP");p.add_argument("--port", type=int, default=8766);p.add_argument("--profile", choices=["project", "cad-studio"], default="project");p.add_argument("--adopt-existing", action="store_true", help="Back up and adopt existing observer files on first install")
    p = sub.add_parser("connect-codex", help="Connect Codex agent to Observer with lifecycle hooks and capability snapshot");p.add_argument("project");p.add_argument("--dest", default=".agent-observer");p.add_argument("--name");p.add_argument("--agent-label", default="Codex");p.add_argument("--port", type=int, default=8766);p.add_argument("--profile", choices=["project", "cad-studio"], default="project");p.add_argument("--executable", default=None);p.add_argument("--adopt-existing", action="store_true", help="Back up and adopt existing observer files on first install")
    p = sub.add_parser("installation-plan", help="Generate installation plan and diagnostics for a project");p.add_argument("project");p.add_argument("--dest", default=".agent-observer");p.add_argument("--agent", choices=["generic", "omp", "cad", "codex"], default="codex");p.add_argument("--port", type=int, default=None);p.add_argument("--executable", default=None);p.add_argument("--json", action="store_true")
    p = sub.add_parser("diagnose", help="Diagnose project installation and connection status");p.add_argument("project");p.add_argument("--executable", default=None);p.add_argument("--json", action="store_true")
    p = sub.add_parser("disconnect-codex", help="Remove Observer hooks from Codex project configuration");p.add_argument("project");p.add_argument("--dest", default=".agent-observer");p.add_argument("--json", action="store_true")
    p = sub.add_parser("codex", help="Launch native Codex CLI under full Agent Observer stream capture");p.add_argument("project");p.add_argument("--executable", default=None);p.add_argument("--config", default=None);p.epilog = "Pass native Codex options after --, for example: codex PROJECT -- --model MODEL"
    sub.add_parser("sync");sub.add_parser("status")
    p = sub.add_parser("watch");p.add_argument("--interval", type=float, default=1)
    p = sub.add_parser("run");p.add_argument("--project");p.add_argument("--config");p.add_argument("--port", type=int)
    p = sub.add_parser("emit");p.add_argument("--config", required=True);p.add_argument("--file", default="-", help="JSON event file, or - for stdin")
    argv = sys.argv[1:]
    native_args = []
    if argv and argv[0] == "codex" and "--" in argv:
        separator = argv.index("--")
        argv, native_args = argv[:separator], argv[separator + 1:]
    args = parser.parse_args(argv)
    if args.command == "codex":
        args.codex_args = native_args
    if args.command == "connect":
        from .connections import ConnectionModules
        from .installation import InstallationManager, _calculate_revision, _validate_project_path
        from .observer_config import load_settings, project_profile
        project = _validate_project_path(args.project)
        target = project / ".agent-observer"
        config_file = target / "config/observer.project.json"
        if not config_file.is_file():
            profile = project_profile(project)
            install(argparse.Namespace(project=str(project), dest=".agent-observer", name=project.name,
                agent_label="CAD Agent" if profile == "cad" else "Agent", agent="generic",
                port=8766, profile="cad-studio" if profile == "cad" else "project",
                compat_cad=profile == "cad", adopt_existing=False, executable=None, documents=None))
        settings = load_settings(config_file, project_root=project, environment={})
        installations = InstallationManager(settings)
        modules = ConnectionModules(settings, installations)
        role = getattr(args, "repository_mode", None)
        module = getattr(args, "module", None)
        if role and not module:
            module = "project" if role == "agent_system" else "codex"
        elif module and not role:
            role = "working_repository" if module in {"codex", "omp"} else "agent_system"
        elif not role and not module:
            module = "project"
            role = "agent_system"
        connection = installations._inspect_connection(project, settings["port"], target)
        if connection["connected"]:
            result = modules.forward_connect(project, module, role)
            print(f"Connected {module} ({role}): {result['url']}")
            return
        result = modules.connect({"project": str(project), "module": module, "repository_mode": role,
                                  "revision": modules.inspect({"project": str(project)})["workspace"]["revision"]})
        print(f"Configured {module} ({role}): {result['url']}", flush=True)
        if args.no_run:
            return
        # The graph runs in this terminal; the user's existing Codex remains untouched.
        args.command = "run";args.project = str(project);args.config = str(config_file);args.port = 8766
    if args.command == "install": install(args)
    elif args.command == "connect-omp":
        args.agent = "omp"
        args.compat_cad = False
        args.executable = None
        install(args)
    elif args.command == "connect-codex":
        args.agent = "codex"
        args.compat_cad = False
        install(args)
    elif args.command == "installation-plan":
        from .installation import InstallationManager
        manager = InstallationManager()
        body = {"project": args.project, "agent": args.agent, "port": args.port, "dest": args.dest, "executable": args.executable}
        plan_data = manager.analyze(body)
        if args.json:
            print(json.dumps(plan_data, indent=2, ensure_ascii=False))
        else:
            print(f"Plan for {plan_data['project']} ({plan_data['agent']}):")
            print(f"  Installed: {plan_data['installed']} | Revision: {plan_data['revision']} | Port: {plan_data['port']}")
            print(f"  Connection: {plan_data['connection']['status']} (connected: {plan_data['connection']['connected']}, owner: {plan_data['connection']['owner']})")
            print(f"  Codex: {'available' if plan_data['codex']['available'] else 'not found'} ({plan_data['codex'].get('version') or 'no version'})")
            if plan_data['changes']:
                print(f"  File changes ({len(plan_data['changes'])}):")
                for ch in plan_data['changes'][:5]:
                    print(f"    - {ch['action']}: {ch['path']}")
                if len(plan_data['changes']) > 5:
                    print(f"    ... and {len(plan_data['changes']) - 5} more")
            if plan_data['warnings']:
                print("  Warnings:")
                for w in plan_data['warnings']:
                    print(f"    ! {w}")
    elif args.command == "diagnose":
        from .installation import InstallationManager
        manager = InstallationManager()
        diag_data = manager.diagnose({"project": args.project, "executable": args.executable})
        if args.json:
            print(json.dumps(diag_data, indent=2, ensure_ascii=False))
        else:
            print(f"Diagnosis for {diag_data['project']}:")
            print(f"  Installed: {diag_data['installed']} | Connection: {diag_data['connection']['status']} (connected: {diag_data['connection']['connected']})")
            print(f"  Port: {diag_data['port']} | URL: {diag_data['url']}")
            for d in diag_data.get("diagnostics", []):
                print(f"  [{d['status'].upper()}] {d['label']}: {d['detail']}")
    elif args.command == "disconnect-codex":
        from .installation import InstallationManager
        manager = InstallationManager()
        preview = manager.analyze({"project": args.project, "dest": args.dest, "agent": "codex"})
        res = manager.disconnect({"project": args.project, "dest": args.dest, "revision": preview["revision"]})
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"Disconnected Codex hooks for {res['project']}. Revision: {res['revision']}")
    elif args.command == "codex":
        from .codex_stream import run_codex
        project = Path(args.project).expanduser().resolve()
        config_file = Path(args.config).expanduser().resolve() if args.config else project / ".agent-observer/config/observer.project.json"
        codex_args = list(args.codex_args or [])
        code = run_codex(str(project), str(config_file), executable=args.executable, codex_args=codex_args)
        sys.exit(code)
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
        if LOCAL_ROOT != ROOT:
            command = [sys.executable,str(ROOT/"observer.py"),"run","--config",str(Path(args.config).resolve() if args.config else LOCAL_ROOT/"config/observer.project.json")]
            if args.project:command.extend(["--project",str(Path(args.project).resolve())])
            if args.port is not None:command.extend(["--port",str(args.port)])
            os.execv(sys.executable,command)
        if args.config: os.environ["AGENT_OBSERVER_CONFIG"] = str(Path(args.config).resolve())
        if args.project: os.environ["AGENT_OBSERVER_PROJECT"] = str(Path(args.project).resolve())
        from .host_runtime import DEFAULT_PORT, prepare
        port = args.port if args.port is not None else DEFAULT_PORT
        os.environ["AGENT_OBSERVER_PORT"] = str(port)
        from .observer_config import load_settings as load_run_settings
        launch_settings=load_run_settings(config_file=args.config,project_root=args.project)
        server_socket = prepare(port)
        if server_socket is None:
            from urllib.parse import urlencode
            query=urlencode({"project":str(launch_settings["project_root"])})
            print(f"Observer is already running at http://127.0.0.1:{port}/?{query}")
            return
        os.environ["AGENT_OBSERVER_SOCKET_FD"] = str(server_socket.fileno())
        sys.argv = [sys.argv[0], "--port", str(port)]
        threading.Thread(target=watch, daemon=True, name="observer-sync").start()
        from . import observer_server
        observer_server.main()

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: pass
    except Exception as error: print(str(error), file=sys.stderr);sys.exit(1)
