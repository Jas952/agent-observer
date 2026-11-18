"""Observer-only session labels; source journals and agent configuration stay unchanged."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3

from .installation import InstallationError
from .observer_config import load_settings


def read_session_names(settings, project, role):
    path = Path(settings['history_file']).parent / 'session-names.sqlite3'
    if not path.is_file():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as database:
        if not database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_names'").fetchone():
            return {}
        return dict(database.execute('SELECT session_id, name FROM session_names WHERE project=? AND role=?',
                                     (str(Path(project).resolve()), role)))


def apply_session_names(rows, settings, project, role):
    from .session_deletion import read_session_tombstones
    tombstones = read_session_tombstones(settings, project, role)
    names = read_session_names(settings, project, role)
    return [{**row, 'display_name': names.get(row['session_id'])} for row in rows if row.get('session_id') not in tombstones]


def rename_session(body, connections, current_sessions):
    if not isinstance(body, dict):
        raise InstallationError(400, 'Expected a session name object.')
    project_value, session_id, name = body.get('project'), body.get('session_id'), body.get('name')
    if not isinstance(project_value, str) or not isinstance(session_id, str) or not session_id:
        raise InstallationError(400, 'Choose a project and session.')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120 or '\n' in name or '\r' in name:
        raise InstallationError(400, 'Enter a single-line name between 1 and 120 characters.')
    project = Path(project_value).expanduser().resolve()
    from .session_projects import discover_session_projects
    workspace = next((row for row in discover_session_projects(connections, current_sessions) if row['path'] == str(project)), None)
    if workspace is None or not any(row['session_id'] == session_id for row in workspace['sessions']):
        raise InstallationError(404, 'Session not found in this project.')
    role = workspace['repository_mode']
    if body.get('repository_mode') != role:
        raise InstallationError(409, 'The project role changed. Refresh its sessions before renaming.')
    _, config_path, _ = connections._configuration(project)
    settings = connections.settings if workspace['current'] else load_settings(config_path, project_root=project, environment={})
    path = Path(settings['history_file']).parent / 'session-names.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(path, timeout=5)) as database, database:
        database.execute('CREATE TABLE IF NOT EXISTS session_names (project TEXT NOT NULL, role TEXT NOT NULL, session_id TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(project,role,session_id))')
        database.execute('INSERT INTO session_names VALUES(?,?,?,?) ON CONFLICT(project,role,session_id) DO UPDATE SET name=excluded.name',
                         (str(project), role, session_id, name.strip()))
    return {'project': str(project), 'session_id': session_id, 'display_name': name.strip()}

