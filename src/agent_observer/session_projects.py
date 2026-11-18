"""Read-only sidebar inventory for registered project folders and their own chats."""
from pathlib import Path
import sqlite3

from .connection_analysis import discover_all_codex_sessions, discover_agent_system_sessions
from .installation import InstallationError
from .observer_config import load_settings
from .session_names import apply_session_names


def discover_session_projects(connections, current_sessions):
    projects = []
    for workspace in connections.workspaces():
        project = Path(workspace['path']).resolve()
        row = {key: workspace[key] for key in ('path', 'name', 'current', 'repository_mode')}
        row.update(sessions=[], graph_url=None)
        try:
            if not project.is_dir():
                row['error'] = 'Project folder is unavailable.'
            elif workspace['current']:
                row['sessions'] = current_sessions
            else:
                target, config_path, _ = connections._configuration(project)
                if workspace['repository_mode'] == 'working_repository':
                    from .repository_observer import RepositoryObserver
                    row['sessions'] = RepositoryObserver.discover_sessions(project)
                elif config_path.is_file():
                    settings = load_settings(config_path, project_root=project, environment={})
                    row['sessions'] = discover_agent_system_sessions(project, target, settings['history_file'])
                transport = getattr(connections.installations,'transport',None)
                if transport and transport.primary:
                    row['graph_url'] = f"http://127.0.0.1:{transport.primary.SETTINGS['port']}/"
                    runtime = transport.runtimes.get(str(project))
                    if runtime:
                        merged = {session['session_id']:session for session in row['sessions']}
                        merged.update((session['session_id'],session) for session in runtime.discover_all_sessions()[0])
                        row['sessions'] = list(merged.values())
            if workspace['current']:
                row['sessions'] = apply_session_names(row['sessions'], connections.settings, project, workspace['repository_mode'])
            elif project.is_dir() and workspace.get('installed'):
                _, config_path, _ = connections._configuration(project)
                settings = load_settings(config_path, project_root=project, environment={})
                row['sessions'] = apply_session_names(row['sessions'], settings, project, workspace['repository_mode'])
        except (OSError, ValueError, TypeError, sqlite3.Error, InstallationError):
            row['error'] = 'Project session storage or configuration could not be read.'
        projects.append(row)
    return projects
