"""Observer-only session deletion and durable per-folder/role tombstones."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3

from .installation import InstallationError
from .observer_config import load_settings



def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id.strip():
        raise InstallationError(400, 'Choose a project and session.')
    if len(session_id) > 512 or any(c in session_id for c in ('\x00', '\n', '\r')):
        raise InstallationError(400, 'Malformed session identifier.')
    parts = session_id.replace('\\', '/').split('/')
    if any(part == '..' for part in parts) or session_id in {'.', '..'}:
        raise InstallationError(400, 'Malformed session identifier.')


def read_session_tombstones(settings, project, role):
    """Read durable tombstones for a specific project folder and repository role."""
    if not isinstance(settings, dict) or 'history_file' not in settings or not project:
        return set()
    path = Path(settings['history_file']).parent / 'session-names.sqlite3'
    if not path.is_file():
        return set()
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as database:
        if not database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_tombstones'").fetchone():
            return set()
        rows = database.execute(
            'SELECT session_id FROM session_tombstones WHERE project=? AND role=?',
            (str(Path(project).resolve()), role)
        ).fetchall()
        return {row[0] for row in rows}


def is_session_tombstoned(settings, project, role, session_id):
    """Predicate indicating whether a session is tombstoned in Observer."""
    if not session_id:
        return False
    return session_id in read_session_tombstones(settings, project, role)


def tombstone_session(settings, project, role, session_id):
    """Durable per-folder/role tombstone persistence in Observer storage."""
    _validate_session_id(session_id)
    project_path = str(Path(project).resolve())
    path = Path(settings['history_file']).parent / 'session-names.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    deleted_at = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(path, timeout=5)) as database, database:
        database.execute(
            'CREATE TABLE IF NOT EXISTS session_tombstones ('
            'project TEXT NOT NULL, '
            'role TEXT NOT NULL, '
            'session_id TEXT NOT NULL, '
            'deleted_at TEXT NOT NULL, '
            'PRIMARY KEY(project, role, session_id))'
        )
        database.execute(
            'INSERT INTO session_tombstones (project, role, session_id, deleted_at) '
            'VALUES(?,?,?,?) ON CONFLICT(project, role, session_id) DO UPDATE SET deleted_at=excluded.deleted_at',
            (project_path, role, session_id, deleted_at)
        )
        # Clear custom display name from Observer-owned metadata if present
        if database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_names'").fetchone():
            database.execute(
                'DELETE FROM session_names WHERE project=? AND role=? AND session_id=?',
                (project_path, role, session_id)
            )


def delete_session(body, connections, current_sessions):
    """Confirmed Observer session deletion for registered exact-folder exact-role membership."""
    if not isinstance(body, dict):
        raise InstallationError(400, 'Expected a session deletion object.')
    if body.get('confirmed') is not True:
        raise InstallationError(400, 'Session deletion must be confirmed.')
    project_value = body.get('project')
    session_id = body.get('session_id')
    repo_mode = body.get('repository_mode')
    if not isinstance(project_value, str) or not project_value.strip():
        raise InstallationError(400, 'Choose a project and session.')
    if not isinstance(session_id, str) or not session_id.strip():
        raise InstallationError(400, 'Choose a project and session.')
    _validate_session_id(session_id)
    if not isinstance(repo_mode, str) or repo_mode not in {'working_repository', 'agent_system'}:
        raise InstallationError(400, 'Invalid repository mode.')

    project = Path(project_value).expanduser().resolve()
    if not project.is_dir():
        raise InstallationError(404, 'Project folder is unavailable.')

    workspace = next((row for row in connections.workspaces() if row['path'] == str(project)), None)
    if workspace is None:
        raise InstallationError(404, 'Project not registered with Observer.')
    role = workspace['repository_mode']
    if repo_mode != role:
        raise InstallationError(409, 'The project role changed. Refresh its sessions before deleting.')

    target, config_path, _ = connections._configuration(project)
    settings = connections.settings if workspace['current'] else load_settings(config_path, project_root=project, environment={})
    if workspace['current']:
        sessions = current_sessions
    else:
        from .connection_analysis import discover_all_codex_sessions, discover_agent_system_sessions
        sessions = (discover_all_codex_sessions(project)['sessions'] if role == 'working_repository'
                    else discover_agent_system_sessions(project, target, settings['history_file']))
    from .session_names import apply_session_names
    if not any(row['session_id'] == session_id for row in apply_session_names(sessions, settings, project, role)):
        raise InstallationError(404, 'Session not found in this project.')

    tombstone_session(settings, project, role, session_id)

    return {
        'deleted': True,
        'project': str(project),
        'repository_mode': role,
        'session_id': session_id,
    }


def purge_in_memory_session(session_id, session_states=None, saved_runs=None, run_states=None, session_inventories=None, *, repository_mode=None):
    """Safely purge in-memory serving structures for a tombstoned session."""
    if session_states is not None and isinstance(session_states, dict):
        session_states.pop(session_id, None)
    if session_inventories is not None and isinstance(session_inventories, dict):
        session_inventories.pop(session_id, None)
    if saved_runs is not None and isinstance(saved_runs, list):
        saved_runs[:] = [
            run for run in saved_runs
            if not ((run.get('session_id') or (run.get('context') or {}).get('session_id')) == session_id
                    and (repository_mode is None or repository_mode == (
                        'working_repository' if str((run.get('context') or {}).get('application') or run.get('source') or '').casefold() == 'codex' or (
                            str((run.get('context') or {}).get('application') or run.get('source') or '').casefold() == 'omp' and session_id.startswith('omp:')) else 'agent_system')))
        ]
    if run_states is not None and isinstance(run_states, dict):
        matching = [
            run_id for run_id, state in run_states.items()
            if getattr(state, 'session_id', None) == session_id
        ]
        for run_id in matching:
            run_states.pop(run_id, None)
