"""Analysis, session inventory and readiness verification for connection setup."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from . import cli
from .capabilities import discover_capabilities
from .codex_observer import (_codex_database, _matching_threads, _path,
                            inspect_rollout_turn_state, is_safe_rollout_path,
                            normalize_iso_timestamp)
from .installation import (_calculate_revision, _installation_target,
                           _validate_project_path, InstallationError)
from .observer_config import project_profile
from .tools_discovery import discover_tools


def compute_analysis_revision(project: Path, repository_mode: str, target: Path,
                              config_path: Path, codex_home: Optional[Path] = None, *, module=None) -> str:
    """Deterministic hash binding configuration, repository role and source readiness."""
    hasher = hashlib.sha256()
    proj = Path(project).expanduser().resolve()
    hasher.update(str(proj).encode("utf-8"))
    hasher.update(str(repository_mode).encode("utf-8"))

    # 1. Base installation and configuration revision
    hasher.update(_calculate_revision(proj, Path(target)).encode("utf-8"))
    cfg = Path(config_path)
    if cfg.is_file():
        try:
            hasher.update(cfg.read_bytes())
        except OSError:
            pass

    # 2. Source readiness & component identity
    if module == "omp":
        from .omp_observer import omp_home
        home = omp_home()
        hasher.update(b"module:omp")
        hasher.update(f"sessions:{(home / 'sessions').is_dir()}".encode())
    elif repository_mode == "working_repository":
        home = Path(codex_home or os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
        db = _codex_database(home)
        if db and db.is_file():
            hasher.update(f"codex_db:{db.name}:{db.stat().st_size > 0}".encode("utf-8"))
        else:
            hasher.update(b"codex_db:none")
    else:
        profile = project_profile(proj)
        hasher.update(f"profile:{profile}".encode("utf-8"))
        for marker in (proj / "src/CADAgentUI", proj / "scripts/xray_server.py", proj / ".omp/extensions/agent-observer.ts"):
            hasher.update(f"{marker.name}:{marker.exists()}".encode("utf-8"))

    # Bind discovered sources/integration evidence, but not growing session-log bytes.
    resources = scan_project_resources(proj)
    hasher.update(json.dumps({"documents": resources["doc_files"], "directories": resources["doc_dirs"],
                             "telemetry": resources["telemetry_markers"], "limited": resources["traversal_limited"]},
                            sort_keys=True).encode())
    evidence_paths = [proj / path for path in resources["telemetry_markers"]]
    evidence_paths += [proj / ".omp/extensions/agent-observer.ts", proj / "scripts/xray_server.py"]
    if module == "omp":
        evidence_paths += [home / "config.yml", home / "config.yaml", home / "mcp.json", proj / ".omp/config.yml"]
    elif repository_mode == "working_repository":
        evidence_paths += [home / "config.toml", proj / ".codex/config.toml", proj / ".mcp.json"]
    for path in evidence_paths:
        if path.is_file() and not path.is_symlink():
            info = path.stat()
            hasher.update(f"{path}:{info.st_size}:{info.st_mtime_ns}".encode())
    return hasher.hexdigest()[:20]


def discover_all_codex_sessions(project_root: Path, codex_home: Optional[Path] = None) -> Dict[str, Any]:
    """Discover all project-matching Codex sessions from SQLite index without a 20-session cap."""
    home = Path(codex_home or os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    resolved_root = Path(project_root).expanduser().resolve()
    database = _codex_database(home)
    if database is None:
        return {"available": False, "database": None, "sessions": [], "unreadable": [],
                "error": "No state_*.sqlite database found in Codex home."}

    try:
        threads = _matching_threads(resolved_root, home)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {"available": False, "database": str(database), "sessions": [], "unreadable": [],
                "error": f"Failed to read Codex session index: {exc}"}

    sessions: List[Dict[str, Any]] = []
    unreadable_reports: List[Dict[str, Any]] = []

    for thread in threads:
        thread_id = str(thread["id"])
        title = thread.get("title")
        name = str(title).strip() if (title and str(title).strip()) else f"Codex session {thread_id[:8]}"
        cwd = str(_path(thread.get("cwd")) or resolved_root)
        updated = thread.get("updated_at")
        updated_iso = normalize_iso_timestamp(updated) if updated else None

        raw_rollout = thread.get("rollout_path")
        status = "unknown"
        run_id = None
        unreadable = False
        compressed = False
        reason: Optional[str] = None

        if not isinstance(raw_rollout, str) or not raw_rollout.strip():
            unreadable = True
            status = "unreadable"
            reason = "No rollout path recorded in thread index"
        else:
            rollout_path = Path(raw_rollout)
            if not rollout_path.is_absolute():
                candidate = (home / rollout_path).resolve()
                if candidate.is_file():
                    rollout_path = candidate
                else:
                    rollout_path = rollout_path.resolve()
            else:
                rollout_path = rollout_path.resolve()

            if not is_safe_rollout_path(rollout_path, home):
                unreadable = True
                status = "unreadable"
                reason = "Rollout path is outside safe Codex sessions directory"
            elif rollout_path.suffix in {".zst", ".gz"}:
                compressed = True
                unreadable = True
                status = "compressed"
                reason = f"Compressed archive ({rollout_path.suffix}); live JSONL observation unavailable"
            elif not rollout_path.is_file():
                unreadable = True
                status = "unreadable"
                reason = f"Rollout file not found on disk: {rollout_path.name}"
            else:
                try:
                    stat = rollout_path.stat()
                    with rollout_path.open("rb") as stream:
                        header = stream.readline(8 * 1024 * 1024 + 1)
                    record = json.loads(header)
                    if len(header) > 8 * 1024 * 1024 or not isinstance(record, dict) or not record.get("type"):
                        raise ValueError("Journal does not begin with a structured JSONL record")
                    inspection = inspect_rollout_turn_state(rollout_path)
                    has_evidence = inspection["has_active_turn"] and 0 <= time.time() - stat.st_mtime <= 1800
                    if inspection["has_active_turn"]:
                        status = "running" if has_evidence else "unknown"
                        turn_id = inspection["active_turn_id"]
                    else:
                        turn_id = inspection["last_completed_turn_id"]
                        status = "idle" if turn_id else "unknown"
                    run_id = f"codex:{thread_id}:{turn_id}" if turn_id else None
                except Exception as exc:
                    unreadable = True
                    status = "unreadable"
                    reason = f"Error reading rollout file: {exc}"

        if unreadable:
            unreadable_reports.append({
                "session_id": thread_id,
                "path": raw_rollout,
                "reason": reason,
                "compressed": compressed
            })

        sessions.append({
            "session_id": thread_id,
            "session_name": name,
            "application": "Codex",
            "status": status,
            "run_id": run_id,
            "cwd": cwd,
            "project_path": str(resolved_root),
            "project_root": str(resolved_root),
            "updated_at": updated_iso,
            "unreadable": unreadable,
            "compressed": compressed,
            "reason": reason,
            "archived": bool(thread.get("archived", False)),
        })

    def _sort_key(s: Dict[str, Any]) -> Tuple[int, float]:
        st = s.get("status")
        rank = 0 if st == "running" else (1 if st == "idle" else 2)
        ts = 0.0
        up = s.get("updated_at")
        if up:
            try:
                ts = datetime.fromisoformat(up).timestamp()
            except (ValueError, OSError):
                ts = 0.0
        return (rank, -ts)

    sessions.sort(key=_sort_key)

    total_indexed = len(threads)
    compressed_count = sum(1 for u in unreadable_reports if u.get("compressed"))
    unreadable_count = len(unreadable_reports)
    readable_count = max(0, total_indexed - unreadable_count)

    return {
        "available": True,
        "database": str(database),
        "total_indexed": total_indexed,
        "readable_count": readable_count,
        "compressed_count": compressed_count,
        "unreadable_count": unreadable_count,
        "sessions": sessions,
        "unreadable": unreadable_reports,
    }


def discover_agent_system_sessions(project_root: Path, target: Path,
                                   history_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Discover recorded sessions from project agent history storage if available."""
    storage_file = history_file or (target / "storage/xray-runs.json")
    if not storage_file.is_file():
        return []
    try:
        data = cli.read_json(storage_file, [])
        if not isinstance(data, list):
            return []
        rows = []
        for run in data:
            if not isinstance(run, dict):
                continue
            context = run.get("context") or {}
            if not isinstance(context, dict):
                continue
            recorded_root = context.get("project_root")
            if recorded_root and Path(recorded_root).expanduser().resolve() != project_root.resolve():
                continue
            session_id = run.get("session_id") or context.get("session_id")
            if not session_id:
                continue
            application = str(context.get("application") or run.get("source") or "Agent")
            if application.casefold() == "codex" or any(row["session_id"] == str(session_id) for row in rows):
                continue
            st = run.get("status", "idle")
            status = "unknown" if st in {"running", "interrupted"} else st
            rows.append({
                "session_id": str(session_id),
                "session_name": str(context.get("session_name") or session_id),
                "application": application,
                "status": status,
                "run_id": run.get("run_id"),
                "cwd": str(project_root),
                "project_path": str(project_root),
                "project_root": str(project_root),
                "updated_at": run.get("finished_at") or run.get("started_at"),
                "unreadable": False,
                "compressed": False,
                "reason": None,
                "archived": False,
            })
        return rows
    except Exception:
        return []


def scan_project_resources(project_root: Path, max_files: int = 20000, max_depth: Optional[int] = None) -> Dict[str, Any]:
    """Safely traverse project folder within bounds, discovering documents and telemetry evidence."""
    root = Path(project_root).expanduser().resolve()
    skip_dirs = {
        ".git", ".agent-observer", ".observer-backups", "node_modules",
        ".venv", "venv", "env", ".env", "__pycache__", "dist", "build",
        ".pytest_cache", ".mypy_cache", ".obsidian", ".observer", "var"
    }

    doc_extensions = {".md", ".markdown"}
    doc_dirs = set()
    doc_files: List[str] = []
    telemetry_markers: List[str] = []

    file_count = 0
    traversal_limited = False

    def on_error(_error):
        nonlocal traversal_limited
        traversal_limited = True
    for candidate in ("docs", "ObsidianVault", "documentation", "architecture"):
        cand_path = root / candidate
        if cand_path.is_dir() and not cand_path.is_symlink():
            doc_dirs.add(candidate)

    for current_dir, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
        current_path = Path(current_dir)
        try:
            rel = current_path.relative_to(root)
            depth = len(rel.parts)
        except ValueError:
            continue

        if max_depth is not None and depth > max_depth:
            traversal_limited = True
            dirnames.clear()
            continue

        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs and not (current_path / d).is_symlink())

        for fname in sorted(filenames):
            if file_count >= max_files:
                traversal_limited = True
                break
            file_count += 1
            fpath = current_path / fname
            if fpath.is_symlink():
                try:
                    if not fpath.resolve().is_relative_to(root):
                        continue
                except (OSError, ValueError):
                    continue

            ext = fpath.suffix.lower()
            if ext in doc_extensions:
                try:
                    rel_file = str(fpath.relative_to(root))
                    doc_files.append(rel_file)
                    parts = Path(rel_file).parts
                    if len(parts) > 1 and parts[0] not in skip_dirs:
                        doc_dirs.add(parts[0])
                except ValueError:
                    pass
            elif ext in {".py", ".ts", ".js", ".mjs"} and not set(fpath.relative_to(root).parts) & {"tests", "examples", "integrations", "agent_observer"}:
                try:
                    with fpath.open(encoding="utf-8", errors="ignore") as source:
                        content_sample = source.read(8192)
                    if ("ObserverClient(" in content_sample and ("agent_observer" in content_sample or "observer_client" in content_sample)
                            and ("PROMPT_START" in content_sample or "GENERATION_START" in content_sample)):
                        telemetry_markers.append(str(fpath.relative_to(root)))
                except (OSError, UnicodeDecodeError):
                    pass

        if traversal_limited:
            break

    return {
        "doc_dirs": sorted(doc_dirs),
        "doc_files": sorted(doc_files),
        "doc_file_count": len(doc_files),
        "telemetry_markers": telemetry_markers,
        "files_scanned": file_count,
        "traversal_limited": traversal_limited,
    }


def build_proposed_documents(project_root: Path, doc_dirs: List[str], doc_files: List[str]) -> List[Dict[str, Any]]:
    """Propose prospective document configuration sources without saving to disk."""
    has_root_md = any(Path(f).parent == Path(".") for f in doc_files)
    proposed: List[Dict[str, Any]] = []

    if "docs" in doc_dirs:
        proposed.append({"path": "docs", "include": ["**/*.md", "**/*.markdown"]})
    if "ObsidianVault" in doc_dirs:
        proposed.append({"path": "ObsidianVault", "include": ["**/*.md", "**/*.markdown"], "group": "ObsidianVault"})

    for d in doc_dirs:
        if d not in {"docs", "ObsidianVault"}:
            proposed.append({"path": d, "include": ["**/*.md", "**/*.markdown"]})

    if has_root_md:
        proposed.append({"path": ".", "include": ["*.md", "*.markdown"]})


    return proposed


def run_analysis(project: Path, repository_mode: str, target: Path, config_path: Path,
                 settings: Optional[Dict[str, Any]] = None,
                 last_project_event: Optional[Dict[str, Any]] = None,
                 codex_home: Optional[Path] = None, *, module=None) -> Dict[str, Any]:
    """Execute read-only staged analysis of folder and component readiness."""
    proj = Path(project).expanduser().resolve()
    logs: List[Dict[str, str]] = []
    checks: List[Dict[str, Any]] = []

    module = module or ("project" if repository_mode == "agent_system" else "codex")
    logs.append({"level": "info", "message": f"Analyzing project directory: {proj}"})
    logs.append({"level": "info", "message": f"Repository role: {repository_mode} (module: {module})"})

    # Check 1: Role validation
    if repository_mode in {"agent_system", "working_repository"}:
        checks.append({
            "id": "role_validation",
            "name": "Repository Role",
            "status": "pass",
            "detail": f"Repository role '{repository_mode}' selected (maps to module '{module}').",
        })
    else:
        checks.append({
            "id": "role_validation",
            "name": "Repository Role",
            "status": "error",
            "detail": f"Invalid repository role '{repository_mode}'. Must be 'agent_system' or 'working_repository'.",
        })

    # Check 2: Project folder accessibility
    if proj.is_dir():
        checks.append({
            "id": "project_folder",
            "name": "Project Folder",
            "status": "pass",
            "detail": f"Project directory '{proj}' exists and is accessible.",
        })
    else:
        checks.append({
            "id": "project_folder",
            "name": "Project Folder",
            "status": "error",
            "detail": f"Project directory '{proj}' does not exist or is not a directory.",
        })

    # Scan resources (bounded)
    scan_data = scan_project_resources(proj)
    logs.append({"level": "info", "message": f"Scanned {scan_data['files_scanned']} project files (traversal limited: {scan_data['traversal_limited']})"})

    # Check 3: Traversal bounds
    if scan_data["traversal_limited"]:
        checks.append({
            "id": "traversal_bounds",
            "name": "Project Traversal",
            "status": "error",
            "detail": f"Folder scan is incomplete ({scan_data['files_scanned']} files checked). Narrow inaccessible or oversized sources before generating.",
        })
    else:
        checks.append({
            "id": "traversal_bounds",
            "name": "Project Traversal",
            "status": "pass",
            "detail": f"Project scan completed within bounds ({scan_data['files_scanned']} files checked, dependencies skipped).",
        })

    # Mode-specific analysis
    sessions: List[Dict[str, Any]] = []
    configuration = cli.read_json(config_path, {}) if config_path.is_file() else {}
    profile = project_profile(proj, configuration)
    logs.append({"level": "info", "message": f"Detected project profile: {profile}"})

    codex_data = {}
    if module == "omp":
        import shutil
        from .omp_observer import OmpObserver, omp_home
        sessions = OmpObserver.discover_sessions(proj)
        available = bool(shutil.which("omp") or (omp_home() / "sessions").is_dir())
        checks.append({"id": "omp_runtime", "name": "OMP", "status": "pass" if available else "error",
                       "detail": "OMP executable or read-only session storage found." if available else "No OMP executable or session storage found."})
        checks.append({"id": "sessions", "name": "OMP Sessions", "status": "pass" if sessions else "waiting",
                       "count": len(sessions), "detail": f"{len(sessions)} matching session(s). Match uses cwd or an explicitly added workspace; only new records are captured."})
        checks.append({"id": "telemetry_readiness", "name": "OMP observation", "status": "pass",
                       "detail": "Read-only journal capture requires no OMP restart. The extension adds richer live metadata on a subsequent OMP launch."})
    elif repository_mode == "working_repository":
        codex_data = discover_all_codex_sessions(proj, codex_home=codex_home)
        sessions = codex_data["sessions"]

        # Codex index check
        if codex_data["available"]:
            logs.append({"level": "info", "message": f"Located Codex session index: {codex_data['database']}"})
            checks.append({
                "id": "codex_index",
                "name": "Codex Session Index",
                "status": "pass",
                "detail": f"Codex SQLite session database found: {codex_data['database']}.",
            })
        else:
            err_msg = codex_data.get("error") or "Codex has no readable session storage yet. Open Codex normally and create a project session first."
            logs.append({"level": "error", "message": err_msg})
            checks.append({
                "id": "codex_index",
                "name": "Codex Session Index",
                "status": "error",
                "detail": err_msg,
            })

        # Codex sessions discovery check
        total_indexed = codex_data.get("total_indexed", len(sessions))
        readable_count = codex_data.get("readable_count", len(sessions))
        compressed_count = codex_data.get("compressed_count", 0)
        unreadable_count = codex_data.get("unreadable_count", 0)
        logs.append({"level": "info", "message": f"Found {total_indexed} total matching indexed Codex session(s) ({readable_count} readable journal(s))"})
        if total_indexed > 0:
            running_count = sum(1 for s in sessions if s.get("status") == "running")
            unknown_count = sum(1 for session in sessions if session.get("status") == "unknown")
            status_desc = f"{running_count} active, {unknown_count} with unknown activity"
            detail = (f"{total_indexed} indexed session(s) discovered ({readable_count} readable journal(s), "
                      f"{compressed_count} compressed, {unreadable_count - compressed_count} unreadable; {status_desc}).")
            checks.append({
                "id": "sessions_discovery",
                "name": "Codex Sessions",
                "status": "pass",
                "count": total_indexed,
                "detail": detail,
            })
        else:
            checks.append({
                "id": "sessions_discovery",
                "name": "Codex Sessions",
                "status": "warning",
                "count": 0,
                "detail": "No matching Codex sessions found for this project directory yet. Sessions will appear once started in Codex.",
            })
        # Rollout health check
        unreadable_count = len(codex_data.get("unreadable", []))
        compressed_count = sum(1 for u in codex_data.get("unreadable", []) if u.get("compressed"))
        if unreadable_count > 0:
            logs.append({"level": "warning", "message": f"Encountered {unreadable_count} compressed or unreadable session rollout(s) ({compressed_count} compressed)"})
            checks.append({
                "id": "rollout_health",
                "name": "Session Rollout Storage",
                "status": "warning",
                "count": unreadable_count,
                "detail": f"{unreadable_count} session(s) have unreadable or compressed rollouts ({compressed_count} compressed). Live observation will skip these.",
            })
        else:
            checks.append({
                "id": "rollout_health",
                "name": "Session Rollout Storage",
                "status": "pass" if total_indexed else "waiting",
                "detail": "Indexed journal headers are readable; completed records are decoded during observation." if total_indexed else "No matching journals to inspect yet.",
            })

    elif repository_mode == "agent_system":
        hist_file = None
        if config_path.is_file():
            from .observer_config import load_settings
            hist_file = load_settings(config_path, project_root=proj, environment={})["history_file"]
        elif settings and Path(settings["project_root"]).resolve() == proj:
            hist_file = settings.get("history_file")
        sessions = discover_agent_system_sessions(proj, target, history_file=hist_file)

        # Telemetry readiness check
        if profile == "cad" and ((proj / "scripts/xray_server.py").is_file() or last_project_event):
            logs.append({"level": "info", "message": "CAD Studio telemetry adapter is present"})
            checks.append({
                "id": "telemetry_readiness",
                "name": "Project Telemetry",
                "status": "pass",
                "detail": "CAD Studio telemetry adapter is present; runtime activity requires recorded events.",
            })
        elif profile == "omp":
            omp_ext = proj / ".omp/extensions/agent-observer.ts"
            if omp_ext.is_file():
                logs.append({"level": "info", "message": "OMP observer extension verified at .omp/extensions/agent-observer.ts"})
                checks.append({
                    "id": "telemetry_readiness",
                    "name": "Project Telemetry",
                    "status": "pass",
                    "detail": "OMP observer extension found in .omp/extensions/agent-observer.ts.",
                })
            else:
                logs.append({"level": "error", "message": "OMP observer extension is missing in .omp/extensions/agent-observer.ts"})
                checks.append({
                    "id": "telemetry_readiness",
                    "name": "Project Telemetry",
                    "status": "error",
                    "detail": "OMP project detected but agent-observer extension is missing in .omp/extensions/agent-observer.ts.",
                })
        else:  # generic
            if last_project_event:
                app_name = last_project_event.get("application", "agent")
                logs.append({"level": "info", "message": f"Recorded telemetry received from {app_name}"})
                checks.append({
                    "id": "telemetry_readiness",
                    "name": "Project Telemetry",
                    "status": "pass",
                    "detail": f"Recorded telemetry event received from {app_name}; this is not a live heartbeat.",
                })
            elif scan_data["telemetry_markers"]:
                marker = scan_data["telemetry_markers"][0]
                logs.append({"level": "info", "message": f"Observer integration found in project code: {marker}"})
                checks.append({
                    "id": "telemetry_readiness",
                    "name": "Project Telemetry",
                    "status": "pass",
                    "detail": f"Observer integration detected in project code: {marker}.",
                })
            else:
                logs.append({"level": "error", "message": "Missing telemetry integration: Observer client SDK required"})
                checks.append({
                    "id": "telemetry_readiness",
                    "name": "Project Telemetry",
                    "status": "error",
                    "detail": "Project agent has no detected telemetry integration. Instrument your agent with the Observer client SDK (examples/instrumented_agent.py) to stream events.",
                })

        # Sessions check for agent_system (optional: zero sessions is NOT a failure)
        s_count = len(sessions)
        if s_count > 0:
            checks.append({
                "id": "sessions",
                "name": "Agent Sessions",
                "status": "pass",
                "count": s_count,
                "detail": f"{s_count} recorded agent session(s) found in storage.",
            })
        else:
            checks.append({
                "id": "sessions",
                "name": "Agent Sessions",
                "status": "waiting",
                "count": 0,
                "detail": "Waiting for agent runs; event stream will appear when agent starts.",
            })

    # Discovered documents check (optional: zero documents is NOT a failure)
    doc_count = scan_data["doc_file_count"]
    if doc_count > 0:
        checks.append({
            "id": "document_sources",
            "name": "Document Sources",
            "status": "pass",
            "count": doc_count,
            "paths": scan_data["doc_files"][:10],
            "detail": f"{doc_count} document file(s) found across {len(scan_data['doc_dirs'])} folder(s) (optional).",
        })
    else:
        checks.append({
            "id": "document_sources",
            "name": "Document Sources",
            "status": "pass",
            "count": 0,
            "paths": [],
            "detail": "No document sources found in project (optional).",
        })

    # Discovered capabilities check (read-only, optional: zero is NOT a failure)
    inventory = discover_capabilities(proj, config_path if config_path.is_file() else None,
                                      agent=module if module in {"codex", "omp"} else profile)
    mcp_list = inventory.get("mcp", [])
    skills_list = inventory.get("skills", [])
    native_tools = inventory.get("native_tools", [])
    total_caps = len(mcp_list) + len(skills_list) + len(native_tools)

    if total_caps > 0:
        checks.append({
            "id": "capabilities",
            "name": "Agent Capabilities",
            "status": "pass",
            "count": total_caps,
            "detail": f"{len(mcp_list)} MCP server(s), {len(skills_list)} skill(s), {len(native_tools)} native tool(s) discovered (optional).",
        })
    else:
        checks.append({
            "id": "capabilities",
            "name": "Agent Capabilities",
            "status": "pass",
            "count": 0,
            "detail": "No MCP servers or custom skills discovered (optional).",
        })

    proposed_docs = build_proposed_documents(proj, scan_data["doc_dirs"], scan_data["doc_files"])
    tools = discover_tools({"project_root": proj, "tools": configuration.get("tools", []),
                            "tools_markdown": configuration.get("tools_markdown")})["tools"]
    for component_id, name, count, detail in [
        ("commands", "Commands", len(native_tools) + len(inventory.get("commands", [])), "Declared native operations and command references; this is not recorded use."),
        ("tools", "Tools & services", len(tools), "Local/configured tool metadata; tools were not invoked and services were not started."),
        ("memory", "Memory systems", len(configuration.get("memory_systems") or []) + len(inventory.get("memory_systems", [])), "Configured memory references, including disabled alternatives; connectivity was not probed.")
    ]:
        checks.append({"id": component_id, "name": name, "status": "pass" if count else "waiting",
                       "count": count, "detail": f"{count} found. {detail}"})
    for component_id, name, detail in [
        ("request", "Project prompt", "Waiting for the selected agent's recorded request."),
        ("working_files", "Working files", "Waiting for explicit file operations in this folder."),
        ("result", "Result", "Waiting for a captured final response or artifact.")
    ]:
        checks.append({"id": component_id, "name": name, "status": "waiting", "detail": detail})

    # Readiness determination: ready only if zero blocking errors
    logs.extend({"level": "error" if check["status"] == "error" else "warning" if check["status"] == "warning" else "info",
                 "message": f"{check['name']}: {check['status']} — {check['detail']}"} for check in checks)
    ready = not any(c.get("status") == "error" for c in checks)
    logs.append({
        "level": "info" if ready else "error",
        "message": f"Analysis complete: {'READY' if ready else 'BLOCKED'}"
    })

    revision = compute_analysis_revision(proj, repository_mode, target, config_path, codex_home=codex_home, module=module)

    return {
        "ready": ready,
        "revision": revision,
        "checks": checks,
        "logs": logs,
        "sessions": sessions,
        "resources": {
            "documents": scan_data["doc_dirs"],
            "document_count": doc_count,
            "mcp": len(mcp_list),
            "skills": len(skills_list),
            "native_tools": len(native_tools),
            "commands": len(inventory.get("commands", [])),
            "memory_systems": len(inventory.get("memory_systems", [])),
            "proposed_documents": proposed_docs,
            "sessions_total": codex_data.get("total_indexed", len(sessions)) if repository_mode == "working_repository" else len(sessions),
            "sessions_readable": codex_data.get("readable_count", len(sessions)) if repository_mode == "working_repository" else len(sessions),
            "sessions_compressed": codex_data.get("compressed_count", 0) if repository_mode == "working_repository" else 0,
            "sessions_unreadable": codex_data.get("unreadable_count", 0) if repository_mode == "working_repository" else 0,
            "traversal_limited": scan_data["traversal_limited"],
            "files_scanned": scan_data["files_scanned"],
            "note": "Discovered resources are not evidence of use. Calls appear when events arrive.",
        }
    }
