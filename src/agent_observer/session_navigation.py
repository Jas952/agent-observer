"""Safe session navigation, project graph launch, and historical transcript hydration."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import List, Optional
from urllib.parse import urlencode

from . import cli
from .agent_events import sanitize_event_data
from .connection_analysis import discover_agent_system_sessions
from .codex_observer import (
    CodexObserver, _codex_database, _path, is_safe_rollout_path, make_event_id
)
from .installation import DASHBOARD_RESTART_REQUIRED, InstallationError, _validate_project_path
from .observer_config import load_settings
from .session_deletion import is_session_tombstoned
from .session_names import apply_session_names


# Bounded hydration cache (max 8) & single-flight concurrency controls
_MAX_HYDRATION_CACHE_ENTRIES = 8
_HYDRATION_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_HYDRATION_LOCK = threading.Lock()
_SESSION_HYDRATION_LOCKS = tuple(threading.Lock() for _ in range(16))


def clear_hydration_cache() -> None:
    """Clear cached payloads without disturbing in-flight observation."""
    with _HYDRATION_LOCK:
        _HYDRATION_CACHE.clear()


def is_registered_project(project: Path, connections) -> bool:
    """Verify project is the current root or recorded in the global registry."""
    if project.resolve() == connections.root.resolve():
        return True
    registry = cli.registry()
    for item in registry.get("workspaces", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            try:
                if Path(item["path"]).expanduser().resolve() == project.resolve():
                    return True
            except (OSError, ValueError):
                continue
    for item in registry.get("installations", []):
        if isinstance(item, dict) and isinstance(item.get("project"), str):
            try:
                if Path(item["project"]).expanduser().resolve() == project.resolve():
                    return True
            except (OSError, ValueError):
                continue
    return False


def validate_session_membership(
    project: Path,
    repository_mode: str,
    session_id: str,
    connections,
    current_sessions: Optional[List[dict]] = None
) -> bool:
    """Ensure session_id belongs to the requested project and repository mode, excluding tombstones."""
    target, config_path, _ = connections._configuration(project)
    is_current = project.resolve() == connections.root.resolve()
    settings = connections.settings if is_current else (
        load_settings(config_path, project_root=project, environment={}) if config_path.is_file() else None
    )

    if settings and is_session_tombstoned(settings, project, repository_mode, session_id):
        return False

    if is_current:
        if current_sessions:
            filtered_current = apply_session_names(current_sessions, settings, project, repository_mode) if settings else current_sessions
            if any(s.get("session_id") == session_id for s in filtered_current):
                return True
        if connections.observer:
            obs_sessions = connections.observer.sessions()
            filtered_obs = apply_session_names(obs_sessions, settings, project, repository_mode) if settings else obs_sessions
            if any(s.get("session_id") == session_id for s in filtered_obs):
                return True

    if repository_mode == "working_repository":
        if session_id.startswith("omp:"):
            from .omp_observer import OmpObserver
            if any(row["session_id"] == session_id for row in OmpObserver.discover_sessions(project)):
                return True
        else:
            home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
            if find_codex_thread(project, home, session_id) is not None:
                return True
    elif repository_mode == "agent_system":
        if config_path.is_file() and settings:
            raw_sessions = discover_agent_system_sessions(project, target, settings.get("history_file"))
            sessions = apply_session_names(raw_sessions, settings, project, repository_mode)
            if any(s.get("session_id") == session_id for s in sessions):
                return True

    transport = getattr(connections.installations,"transport",None)
    runtime = transport.runtimes.get(str(project)) if transport else None
    if runtime:
        return any(row.get("session_id")==session_id for row in runtime.discover_all_sessions()[0])

    return False


def open_project_session(
    body: dict,
    connections,
    current_sessions: Optional[List[dict]] = None
) -> dict:
    """
    Safely resolve, validate, and launch/connect a project session.

    Contract:
      Input: { "project": str, "repository_mode": str, "session_id": str }
      Output: { "url": str, "project": str, "session_id": str }
    """
    if not isinstance(body, dict):
        raise InstallationError(400, "Request body must be a JSON object.")

    raw_project = body.get("project")
    if not raw_project or not isinstance(raw_project, str) or not raw_project.strip():
        raise InstallationError(400, "Project directory path must be provided.")

    project = _validate_project_path(raw_project)

    # 1. Registered folder validation (scoped: does not scan all registered projects)
    if not is_registered_project(project, connections):
        raise InstallationError(404, f"Project folder '{project}' is not registered.")

    # 2. Repository role validation
    repository_mode = body.get("repository_mode")
    if not isinstance(repository_mode, str) or repository_mode not in {"agent_system", "working_repository"}:
        raise InstallationError(400, "Repository role must be 'agent_system' or 'working_repository'.")

    workspace = connections._workspace(project)
    expected_mode = workspace.get("repository_mode")
    if repository_mode != expected_mode:
        raise InstallationError(
            400,
            f"Repository role '{repository_mode}' does not match registered project role '{expected_mode}'."
        )

    # 3. Session membership validation (rejects tombstoned sessions)
    session_id = body.get("session_id")
    if not session_id or not isinstance(session_id, str) or not session_id.strip():
        raise InstallationError(400, "session_id must be a non-empty string.")

    if not validate_session_membership(project, repository_mode, session_id, connections, current_sessions):
        raise InstallationError(404, f"Session '{session_id}' not found in project '{project}'.")

    transport = getattr(connections.installations,"transport",None)
    if transport and transport.primary:
        transport.ensure(str(project))
        query = urlencode({"project":str(project),"session":session_id})
        return {"url":f"http://127.0.0.1:{transport.primary.SETTINGS['port']}/?{query}","project":str(project),"session_id":session_id}
    # 4. Warm vs Cold graph path
    query = urlencode({"session": session_id})
    if project.resolve() == connections.root.resolve():
        port = connections.settings.get("port", 8766)
        url = f"http://127.0.0.1:{port}/?{query}"
        return {"url": url, "project": str(project), "session_id": session_id}

    target, config_path, cfg = connections._configuration(project)
    is_installed = config_path.is_file()
    port = cfg.get("port") if is_installed else None

    # Inspect running service if port is configured
    if is_installed and isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        status = connections.installations._inspect_connection(project, port, target)
        if status.get("connected"):
            if not status.get("dashboard_compatible"):
                raise InstallationError(409, DASHBOARD_RESTART_REQUIRED)
            url = f"http://127.0.0.1:{port}/?{query}"
            return {"url": url, "project": str(project), "session_id": session_id}
        elif status.get("status") == "occupied":
            raise InstallationError(409, f"Port {port} is occupied by an unverified or different service.")

    # Cold target: Prepare and start graph through existing analyze / generate flow
    module = "project" if repository_mode == "agent_system" else "omp" if session_id.startswith("omp:") else "codex"
    analysis = connections.analyze({"project": str(project), "repository_mode": repository_mode, "module": module})
    if not analysis.get("ready"):
        error_checks = [c.get("detail") for c in analysis.get("checks", []) if c.get("status") == "error"]
        detail = error_checks[0] if error_checks else "Folder analysis failed readiness."
        raise InstallationError(409, detail)

    gen = connections.generate({
        "project": str(project),
        "repository_mode": repository_mode,
        "module": module,
        "analysis_id": analysis["analysis_id"],
        "revision": analysis["revision"],
    })

    base_url = gen.get("url")
    if not base_url:
        _, fresh_cfg_path, fresh_cfg = connections._configuration(project)
        port = fresh_cfg.get("port", 8766)
        base_url = f"http://127.0.0.1:{port}"

    if base_url.endswith("/"):
        url = f"{base_url}?{query}"
    else:
        url = f"{base_url}/?{query}"

    return {"url": url, "project": str(project), "session_id": session_id}


# =============================================================================
# Historical Codex Hydration
# =============================================================================


def find_codex_thread(project_root: Path, home: Path, session_id: str) -> Optional[dict]:
    """Query thread record by primary key without external processes or side-effects."""
    database = _codex_database(home)
    if database is None or not database.is_file():
        return None
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            projects = set()
            if "project_roots" in tables:
                projects = {
                    str(row["project_id"])
                    for row in conn.execute("SELECT project_id, path FROM project_roots")
                    if _path(row["path"]) == project_root
                }
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(threads)")}
            names = [
                name for name in (
                    "id", "rollout_path", "cwd", "source", "originator",
                    "updated_at", "project_id", "history_mode", "title", "archived"
                ) if name in columns
            ]
            if not {"id", "rollout_path", "cwd"}.issubset(names):
                return None
            cursor = conn.execute(f"SELECT {','.join(names)} FROM threads WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            data = dict(row)
            cwd = _path(data.get("cwd"))
            if str(data.get("project_id")) in projects or (cwd and cwd.is_relative_to(project_root)):
                return data
            return None
    except (sqlite3.Error, OSError, ValueError):
        return None


def hydrate_historical_codex_session(
    session_id, project_root, *, codex_home=None, settings=None, state_factory=None, cache_context=None,
):
    """Replay a source journal into isolated read-only states, without launching an agent."""
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    if state_factory is None:
        raise ValueError("Historical observation requires an isolated state factory.")
    project = Path(project_root).expanduser().resolve()
    home = Path(codex_home or os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    if settings and is_session_tombstoned(settings, project, "working_repository", session_id):
        return None
    config_path = settings.get("config_file") if settings else None
    revision = None
    if config_path and Path(config_path).is_file():
        stat = Path(config_path).stat()
        revision = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    key = (str(project), str(home), session_id)
    with _SESSION_HYDRATION_LOCKS[hash(key) % len(_SESSION_HYDRATION_LOCKS)]:
        thread = find_codex_thread(project, home, session_id)
        if not thread or not isinstance(thread.get("rollout_path"), str):
            return None
        rollout = Path(thread["rollout_path"])
        if not is_safe_rollout_path(rollout, home) or not rollout.is_file() or rollout.suffix in {".zst", ".gz"}:
            return None
        stat = rollout.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with _HYDRATION_LOCK:
            cached = _HYDRATION_CACHE.get(key)
            if cached and cached["signature"] == signature and cached["revision"] == revision and cached["context"] is cache_context:
                _HYDRATION_CACHE.move_to_end(key)
                return cached["data"]

        current = None
        active = {}
        inventory = None
        starts = {"PROMPT_START", "GENERATION_START", "PROMPT_RECEIVED"}

        def observe(event):
            nonlocal current, inventory
            kind = str(event.get("type", event.get("event_type", ""))).upper()
            if kind == "AGENT_EVENT":
                return
            for field in ("input", "args", "result", "output"):
                if field in event:
                    event[field], truncated = sanitize_event_data(event[field])
                    if truncated:
                        event["payload_truncated"] = True
            if kind == "RUNTIME_INVENTORY":
                inventory = event.get("inventory")
                if inventory is not None and current is not None:
                    current.set_runtime_inventory(session_id, inventory)
                return
            run_id = str(event.get("run_id") or "")
            if kind in starts:
                current = state_factory()
                current.session_id = session_id
                if inventory is not None:
                    current.set_runtime_inventory(session_id, inventory)
                active[run_id] = current
                target = current
            else:
                target = active.get(run_id)
                if target is None and current is not None and current.run_id == run_id and kind in {"USAGE_UPDATE", "MODEL_INFO"}:
                    target = current
                if target is None:
                    return
            previous = len(target.history)
            target.process_event(event)
            timestamp = event.get("timestamp")
            if timestamp and len(target.history) > previous:
                target.history[-1]["timestamp"] = timestamp
                if kind in starts:
                    target.run_started_at = timestamp
                    target.history[-1]["run_started_at"] = timestamp
            if not target.current_run_id:
                active.pop(run_id, None)

        with tempfile.TemporaryDirectory(prefix="observer-session-view-") as temporary:
            observer = CodexObserver(project, Path(temporary), observe, codex_home=home)
            with observer.replay_context("view"), rollout.open("rb") as stream:
                for index, line in enumerate(stream):
                    if not line.endswith(b"\n") or len(line) > 8 * 1024 * 1024:
                        continue
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(record, dict):
                        observer._process_record(thread, record, make_event_id(session_id, "hydration", index), replay=True)
        if current is None:
            current = state_factory()
            current.session_id = session_id
            if inventory is not None:
                current.set_runtime_inventory(session_id, inventory)
        snapshot = current.get_snapshot()
        snapshot["context"]["session_id"] = session_id
        history = list(current.history)
        phase = current.orchestrator_phase
        run = {
            "run_id": current.run_id, "session_id": session_id, "context": dict(current.run_context),
            "started_at": current.run_started_at,
            "finished_at": history[-1]["timestamp"] if history and phase in {"complete", "error"} else None,
            "status": {"complete":"completed", "error":"failed"}.get(phase, "interrupted" if history else "idle"),
            "source": current.run_source, "project": current.project, "prompt": current.prompt,
            "capability_inventory": current.capability_inventory, "steps": history,
        }
        result = {"state":snapshot, "history":history, "config":current.config, "run":run}
        with _HYDRATION_LOCK:
            _HYDRATION_CACHE[key] = {"signature":signature, "revision":revision, "context":cache_context, "data":result}
            _HYDRATION_CACHE.move_to_end(key)
            while len(_HYDRATION_CACHE) > _MAX_HYDRATION_CACHE_ENTRIES:
                _HYDRATION_CACHE.popitem(last=False)
        return result



def get_or_hydrate_session_detail(
    session_id, project_root, session_states, saved_runs, *, codex_home=None, settings=None,
    state_factory=None, session_inventories=None, cache_context=None,
):
    """Resolve the selected role's state without modifying live state or saved journals."""
    role = "working_repository" if (settings or {}).get("connection", {}).get("module") in {"codex", "omp"} else "agent_system"
    if settings and is_session_tombstoned(settings, project_root, role, session_id):
        return None
    state = session_states.get(session_id)
    live_observation = copy.deepcopy(getattr(state, "session_observation", {}))
    if state is not None and (role != "working_repository" or state.history or state.current_run_id):
        return {"state":state.get_snapshot(), "history":list(state.history), "config":state.config}
    matching = next((run for run in saved_runs
        if (run.get("session_id") or (run.get("context") or {}).get("session_id")) == session_id
        and ("working_repository" if str((run.get("context") or {}).get("application") or run.get("source") or "").casefold() == "codex" or (
            str((run.get("context") or {}).get("application") or run.get("source") or "").casefold() == "omp" and session_id.startswith("omp:")) else "agent_system") == role), None)
    if matching:
        state = state_factory()
        state.session_id = session_id
        inventory = matching.get("capability_inventory") or (session_inventories or {}).get(session_id)
        if inventory:
            state.set_runtime_inventory(session_id, inventory)
        snapshot = state.get_snapshot()
        snapshot["session_observation"] = copy.deepcopy(matching.get("session_observation") or {})
        snapshot["context"]["session_id"] = session_id
        history = matching.get("steps", [])
        if history:
            snapshot.update(copy.deepcopy(history[-1]))
            snapshot["nodes"] = {**state.nodes, **snapshot.get("nodes", {})}
            snapshot["history_count"] = len(history)
        snapshot["session_observation"] = live_observation or copy.deepcopy(matching.get("session_observation") or snapshot.get("session_observation") or {})
        if matching.get("status") in {"running", "interrupted"}:
            snapshot["orchestrator_phase"] = "interrupted"
        return {"state":snapshot, "history":history, "config":state.config, "run":matching}
    if state is not None and live_observation:
        return {"state": state.get_snapshot(), "history": [], "config": state.config}
    if role == "working_repository" and not session_id.startswith("omp:"):
        return hydrate_historical_codex_session(session_id, project_root, codex_home=codex_home,
            settings=settings, state_factory=state_factory, cache_context=cache_context)
    return None
