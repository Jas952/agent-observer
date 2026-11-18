import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from agent_observer.efficiency import ToolEfficiency


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'history.db'
        self.now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.database) as connection:
            connection.execute('CREATE TABLE commands(id INTEGER PRIMARY KEY,timestamp TEXT,rtk_cmd TEXT,input_tokens INTEGER,output_tokens INTEGER,project_path TEXT)')
        self.capture = ToolEfficiency(self.root, self.database)

    def row(self, project=None, command='rtk git status'):
        with sqlite3.connect(self.database) as connection:
            connection.execute('INSERT INTO commands(timestamp,rtk_cmd,input_tokens,output_tokens,project_path) VALUES(?,?,?,?,?)', (self.now, command, 1200, 200, str(project or self.root)))

    def call(self, identifier='one'):
        self.capture.observe({'type': 'TOOL_CALL_START', 'call_id': identifier, 'input': {'command': 'rtk git status'}})
        event = {'type': 'TOOL_CALL_END', 'call_id': identifier, 'timestamp': self.now}
        self.capture.observe(event)
        return event

    def test_only_unique_matching_project_call_is_counted_once(self):
        self.row(project='/other/project'); self.row(command='rtk git diff'); self.row()
        self.assertEqual(self.call()['token_metrics']['without_tokens'], 1200)
        self.assertNotIn('token_metrics', self.call('two'))

    def test_ambiguous_concurrent_commands_do_not_invent_savings(self):
        self.row(); self.row()
        self.assertNotIn('token_metrics', self.call())

    def test_missing_database_does_not_interrupt_observation(self):
        self.database.unlink()
        self.assertNotIn('token_metrics', self.call())

    def test_rtk_history_joined_arguments_match_quoted_shell_input(self):
        self.row(command='rtk proxy printf a b')
        self.capture.observe({'type': 'TOOL_CALL_START', 'call_id': 'quoted', 'input': {'command': "rtk proxy printf 'a b'"}})
        event = {'type': 'TOOL_CALL_END', 'call_id': 'quoted', 'timestamp': self.now}
        self.capture.observe(event)
        self.assertEqual(event['token_metrics']['output_tokens'], 200)

    def test_old_project_statistics_are_not_this_calls_metrics(self):
        self.now = '2000-01-01T00:00:00+00:00'
        self.row()
        self.now = datetime.now(timezone.utc).isoformat()
        self.assertNotIn('token_metrics', self.call())
