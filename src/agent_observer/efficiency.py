"""Attribute RTK's own reported counters to unique, recorded shell calls only."""
import math
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .results import arguments


def normalized_command(value):
    try:
        return tuple(shlex.split(str(value)))
    except ValueError:
        return ()


class ToolEfficiency:
    def __init__(self, root, database=None):
        self.root = str(Path(root).resolve())
        self.database = Path(database) if database else Path.home() / ('Library/Application Support/rtk/history.db' if sys.platform == 'darwin' else '.local/share/rtk/history.db')
        self.pending, self.used = {}, set()

    def observe(self, event):
        kind = event.get('type')
        call_id = str(event.get('call_id', event.get('tool_call_id', '')))
        if not call_id:
            return
        if kind == 'TOOL_CALL_START':
            command = arguments(event).get('command', event.get('command', ''))
            tokens = normalized_command(command)
            if tokens and Path(tokens[0]).name == 'rtk':
                self.pending[call_id] = ('rtk', *tokens[1:])
        elif kind == 'TOOL_CALL_END':
            tokens = self.pending.pop(call_id, None)
            if not tokens or not self.database.is_file():
                return
            stamp = event.get('timestamp')
            try:
                end = datetime.fromtimestamp(stamp / 1000 if stamp > 1e11 else stamp, timezone.utc) if isinstance(stamp, (int, float)) else datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else datetime.now(timezone.utc)
                with sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=.15) as connection:
                    # Index-sized bounded tail; do not return commands or unrelated statistics to the UI.
                    rows = connection.execute('SELECT id,timestamp,rtk_cmd,input_tokens,output_tokens,project_path FROM commands ORDER BY id DESC LIMIT 512').fetchall()
                candidates = []
                for row_id, timestamp, command, before, after, project in rows:
                    if row_id in self.used or not project or str(Path(project).resolve()) != self.root or command.strip() != ' '.join(tokens):
                        continue
                    recorded = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    if recorded.tzinfo is None:
                        recorded = recorded.replace(tzinfo=timezone.utc)
                    if abs((end - recorded).total_seconds()) <= 2 and all(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in (before, after)):
                        candidates.append((row_id, int(before), int(after)))
                # Concurrent identical commands are ambiguous: unknown is better than invented savings.
                if len(candidates) == 1:
                    row_id, before, after = candidates[0]
                    self.used.add(row_id)
                    event['token_metrics'] = {'output_tokens': after, 'without_tokens': before,
                                              'source': 'rtk-history', 'estimated': True, 'baseline': 'reported'}
            except (OSError, ValueError, TypeError, sqlite3.Error):
                return
