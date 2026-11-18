"""Observe both external agents for one repository without launching either agent."""
from .codex_observer import CodexObserver
from .omp_observer import OmpObserver


class RepositoryObserver:
    def __init__(self, project_root, state_dir, emit):
        self.codex = CodexObserver(project_root, state_dir, emit)
        self.omp = OmpObserver(project_root, state_dir, emit)

    def start(self):
        try:
            self.codex.start()
            self.omp.start()
        except Exception:
            self.stop()
            raise

    def stop(self):
        try:
            self.codex.stop()
        finally:
            self.omp.stop()

    def sessions(self):
        return self.codex.sessions() + self.omp.sessions()

    @staticmethod
    def discover_sessions(project_root):
        return CodexObserver.discover_sessions(project_root) + OmpObserver.discover_sessions(project_root)

    def has_session(self, session_id):
        if str(session_id).startswith('omp:'):
            return self.omp.has_session(session_id)
        return any(row['session_id'] == session_id for row in self.codex.sessions())

    def status(self):
        sources = {'codex': self.codex.status(), 'omp': self.omp.status()}
        watching = any(status.get('watching') or status.get('state') == 'watching' for status in sources.values())
        errors = [status['error'] for status in sources.values() if status.get('error')]
        return {'state': 'watching' if watching else 'error' if errors else 'stopped',
                'watching': watching, 'sources': sources,
                'matching_thread_count': sum(status.get('matching_thread_count', 0) for status in sources.values()),
                'last_event_at': max((status['last_event_at'] for status in sources.values() if status.get('last_event_at')), default=None),
                'error': '; '.join(errors) or None,
                'transport': 'Read-only Codex and OMP journals'}

    def replay_latest(self, *, source='codex'):
        return (self.omp if source == 'omp' else self.codex).replay_latest()
