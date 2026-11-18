"""Isolated deterministic unit tests for confirmed Observer session deletion and tombstones."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_observer import cli
from agent_observer.connections import ConnectionModules
from agent_observer.installation import InstallationError
from agent_observer.observer_config import load_settings
from agent_observer.session_deletion import (
    delete_session,
    read_session_tombstones,
    is_session_tombstoned,
    purge_in_memory_session,
    tombstone_session,
)
from agent_observer.session_names import apply_session_names, read_session_names, rename_session
from agent_observer.session_projects import discover_session_projects
from test_connection_analysis import init_codex_db, insert_thread


class SessionDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.current = self.root / 'current'
        self.external = self.root / 'external'
        self.embedded = self.root / 'embedded'
        self.other = self.root / 'not-registered'
        for project in (self.current, self.external, self.embedded, self.other):
            project.mkdir()

        for project, role in (
            (self.current, 'working_repository'),
            (self.external, 'working_repository'),
            (self.embedded, 'agent_system'),
        ):
            config = project / '.agent-observer/config/observer.project.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({
                'project': {'root': str(project)},
                'port': 43211,
                'connection': {
                    'module': 'codex' if role == 'working_repository' else 'project',
                    'repository_mode': role,
                },
            }))

        self.config = self.current / '.agent-observer/config/observer.project.json'
        self.home = self.root / 'codex'
        self.database = self.home / 'state_1.sqlite'
        init_codex_db(self.database)

        registry = self.root / 'registry.json'
        home_patch = patch('pathlib.Path.home', return_value=self.root / 'home')
        home_patch.start()
        self.addCleanup(home_patch.stop)

        registry.write_text(json.dumps({
            'workspaces': [{'path': str(project)} for project in (self.external, self.embedded)],
        }))

        for manager in (
            patch.object(cli, 'REGISTRY', registry),
            patch.dict(os.environ, {'CODEX_HOME': str(self.home)}),
        ):
            manager.start()
            self.addCleanup(manager.stop)

        self.modules = ConnectionModules(load_settings(self.config, environment={}))
        self.addCleanup(self.modules.shutdown)

    def test_unconfirmed_deletion_is_rejected(self):
        current = [{'session_id': 'cur-1', 'session_name': 'Chat 1', 'application': 'Codex'}]
        for invalid_confirmation in (False, None, 'true', 1, 'yes', []):
            with self.subTest(confirmed=invalid_confirmation):
                with self.assertRaises(InstallationError) as error:
                    delete_session({
                        'project': str(self.current),
                        'repository_mode': 'working_repository',
                        'session_id': 'cur-1',
                        'confirmed': invalid_confirmation,
                    }, self.modules, current)
                self.assertEqual(error.exception.status, 400)

    def test_invalid_and_malformed_session_identifiers_are_rejected(self):
        current = [{'session_id': 'cur-1', 'session_name': 'Chat 1', 'application': 'Codex'}]
        # Non-dict body
        for bad_body in (None, 'string', [1, 2, 3]):
            with self.assertRaises(InstallationError) as error:
                delete_session(bad_body, self.modules, current)
            self.assertEqual(error.exception.status, 400)

        # Empty / whitespace project or session
        for bad_proj, bad_sess in (('', 'cur-1'), ('  ', 'cur-1'), (str(self.current), ''), (str(self.current), '   ')):
            with self.assertRaises(InstallationError) as error:
                delete_session({
                    'project': bad_proj,
                    'repository_mode': 'working_repository',
                    'session_id': bad_sess,
                    'confirmed': True,
                }, self.modules, current)
            self.assertEqual(error.exception.status, 400)

        # Invalid repository mode
        for bad_mode in ('invalid_mode', 'other', '', 123):
            with self.assertRaises(InstallationError) as error:
                delete_session({
                    'project': str(self.current),
                    'repository_mode': bad_mode,
                    'session_id': 'cur-1',
                    'confirmed': True,
                }, self.modules, current)
            self.assertEqual(error.exception.status, 400)

        # Malformed session IDs
        for malformed in (
            'id\x00withnull',
            'id\nwithnewline',
            'id\rwithreturn',
            '..',
            '.',
            '../secret',
            'foo/../../bar',
            'x' * 513,
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(InstallationError) as error:
                    delete_session({
                        'project': str(self.current),
                        'repository_mode': 'working_repository',
                        'session_id': malformed,
                        'confirmed': True,
                    }, self.modules, current)
                self.assertEqual(error.exception.status, 400)

    def test_unregistered_and_nonexistent_projects_are_rejected(self):
        current = [{'session_id': 'cur-1', 'session_name': 'Chat 1', 'application': 'Codex'}]
        # Unregistered project folder
        with self.assertRaises(InstallationError) as error:
            delete_session({
                'project': str(self.other),
                'repository_mode': 'working_repository',
                'session_id': 'cur-1',
                'confirmed': True,
            }, self.modules, current)
        self.assertEqual(error.exception.status, 404)

        # Nonexistent folder
        nonexistent = self.root / 'does-not-exist'
        with self.assertRaises(InstallationError) as error:
            delete_session({
                'project': str(nonexistent),
                'repository_mode': 'working_repository',
                'session_id': 'cur-1',
                'confirmed': True,
            }, self.modules, current)
        self.assertEqual(error.exception.status, 404)

    def test_wrong_role_is_rejected(self):
        current = [{'session_id': 'cur-1', 'session_name': 'Chat 1', 'application': 'Codex'}]
        # Current is working_repository, request says agent_system
        with self.assertRaises(InstallationError) as error:
            delete_session({
                'project': str(self.current),
                'repository_mode': 'agent_system',
                'session_id': 'cur-1',
                'confirmed': True,
            }, self.modules, current)
        self.assertEqual(error.exception.status, 409)

        # Embedded is agent_system, request says working_repository
        with self.assertRaises(InstallationError) as error:
            delete_session({
                'project': str(self.embedded),
                'repository_mode': 'working_repository',
                'session_id': 'emb-1',
                'confirmed': True,
            }, self.modules, current)
        self.assertEqual(error.exception.status, 409)

    def test_nonexistent_session_in_valid_project_is_rejected(self):
        current = [{'session_id': 'cur-1', 'session_name': 'Chat 1', 'application': 'Codex'}]
        with self.assertRaises(InstallationError) as error:
            delete_session({
                'project': str(self.current),
                'repository_mode': 'working_repository',
                'session_id': 'unknown-session-id',
                'confirmed': True,
            }, self.modules, current)
        self.assertEqual(error.exception.status, 404)

    def test_confirmed_deletion_returns_contract_records_tombstone_and_purges_custom_name(self):
        insert_thread(self.database, 'thread-alpha', self.home / 'alpha.jsonl', self.external, title='Alpha Original')
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            # Set a custom display name first
            rename_session({
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'thread-alpha',
                'name': 'Custom Display Name',
            }, self.modules, [])

            settings = load_settings(self.external / '.agent-observer/config/observer.project.json', environment={})
            names_before = read_session_names(settings, self.external, 'working_repository')
            self.assertEqual(names_before.get('thread-alpha'), 'Custom Display Name')
            self.assertFalse(is_session_tombstoned(settings, self.external, 'working_repository', 'thread-alpha'))

            # Execute confirmed deletion
            result = delete_session({
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'thread-alpha',
                'confirmed': True,
            }, self.modules, [])

            # Check exact contract return
            self.assertEqual(result, {
                'deleted': True,
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'thread-alpha',
            })

            # Check durable tombstone presence
            self.assertTrue(is_session_tombstoned(settings, self.external, 'working_repository', 'thread-alpha'))
            self.assertIn('thread-alpha', read_session_tombstones(settings, self.external, 'working_repository'))

            # Check custom display name purged
            names_after = read_session_names(settings, self.external, 'working_repository')
            self.assertNotIn('thread-alpha', names_after)

    def test_tombstoned_session_disappears_from_apply_session_names_and_projects(self):
        current = [
            {'session_id': 'cur-alpha', 'session_name': 'Alpha Current', 'application': 'Codex'},
            {'session_id': 'cur-beta', 'session_name': 'Beta Current', 'application': 'Codex'},
        ]
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            # Rename cur-beta
            rename_session({
                'project': str(self.current),
                'repository_mode': 'working_repository',
                'session_id': 'cur-beta',
                'name': 'Renamed Beta',
            }, self.modules, current)

            # Delete cur-alpha
            delete_session({
                'project': str(self.current),
                'repository_mode': 'working_repository',
                'session_id': 'cur-alpha',
                'confirmed': True,
            }, self.modules, current)

            # apply_session_names must filter out cur-alpha and keep cur-beta with its display_name
            filtered = apply_session_names(current, self.modules.settings, self.current, 'working_repository')
            session_ids = [row['session_id'] for row in filtered]
            self.assertNotIn('cur-alpha', session_ids)
            self.assertIn('cur-beta', session_ids)
            self.assertEqual(filtered[0]['display_name'], 'Renamed Beta')

            # discover_session_projects must not show cur-alpha
            rows = discover_session_projects(self.modules, current)
            cur_workspace = next(row for row in rows if row['current'])
            cur_session_ids = [s['session_id'] for s in cur_workspace['sessions']]
            self.assertNotIn('cur-alpha', cur_session_ids)
            self.assertIn('cur-beta', cur_session_ids)

    def test_tombstone_persists_across_restart_and_polling(self):
        insert_thread(self.database, 'persist-thread', self.home / 'persist.jsonl', self.external, title='Persistent Chat')
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            delete_session({
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'persist-thread',
                'confirmed': True,
            }, self.modules, [])

            # Simulate full server restart with fresh ConnectionModules
            restarted = ConnectionModules(load_settings(self.config, environment={}))
            self.addCleanup(restarted.shutdown)

            # Initial post-restart check
            rows = discover_session_projects(restarted, [])
            ext_workspace = next(row for row in rows if row['path'] == str(self.external))
            ext_session_ids = [s['session_id'] for s in ext_workspace['sessions']]
            self.assertNotIn('persist-thread', ext_session_ids)

            # Subsequent polling check (simulating repeated periodic discovery)
            polled_rows = discover_session_projects(restarted, [])
            polled_ext = next(row for row in polled_rows if row['path'] == str(self.external))
            self.assertNotIn('persist-thread', [s['session_id'] for s in polled_ext['sessions']])

    def test_folder_and_role_boundaries_are_strictly_isolated(self):
        # Insert shared session id in external working_repository
        insert_thread(self.database, 'shared-id', self.home / 'shared.jsonl', self.external, title='External Thread')

        # Insert shared session id in embedded agent_system history
        embedded_settings = load_settings(self.embedded / '.agent-observer/config/observer.project.json', environment={})
        history = embedded_settings['history_file']
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(json.dumps([
            {'session_id': 'shared-id', 'context': {'application': 'Project Agent', 'project_root': str(self.embedded)}},
        ]))

        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            # Delete shared-id in external working_repository
            delete_session({
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'shared-id',
                'confirmed': True,
            }, self.modules, [])

            # Check that external tombstone exists
            ext_settings = load_settings(self.external / '.agent-observer/config/observer.project.json', environment={})
            self.assertTrue(is_session_tombstoned(ext_settings, self.external, 'working_repository', 'shared-id'))

            # Check that embedded agent_system is NOT tombstoned
            self.assertFalse(is_session_tombstoned(embedded_settings, self.embedded, 'agent_system', 'shared-id'))
            self.assertEqual(read_session_tombstones(embedded_settings, self.embedded, 'agent_system'), set())

            # Check that discovery for embedded still contains shared-id
            rows = discover_session_projects(self.modules, [])
            embedded_workspace = next(row for row in rows if row['path'] == str(self.embedded))
            self.assertIn('shared-id', [s['session_id'] for s in embedded_workspace['sessions']])

            # Check that external discovery does NOT contain shared-id
            external_workspace = next(row for row in rows if row['path'] == str(self.external))
            self.assertNotIn('shared-id', [s['session_id'] for s in external_workspace['sessions']])

    def test_original_agent_data_and_source_histories_are_untouched(self):
        rollout_file = self.home / 'untouched.jsonl'
        rollout_content = '{"event": "start", "text": "hello"}\n{"event": "message", "text": "preserved"}\n'
        rollout_file.write_text(rollout_content)

        insert_thread(self.database, 'preserve-me', rollout_file, self.external, title='Original Unmodified Title')

        # Hash database and rollout file before deletion
        db_hash_before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        rollout_hash_before = hashlib.sha256(rollout_file.read_bytes()).hexdigest()
        config_file = self.external / '.agent-observer/config/observer.project.json'
        config_hash_before = hashlib.sha256(config_file.read_bytes()).hexdigest()

        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            delete_session({
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'preserve-me',
                'confirmed': True,
            }, self.modules, [])

        # Verify Codex SQLite database, rollout JSONL, and config are byte-for-byte identical
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), db_hash_before)
        self.assertEqual(hashlib.sha256(rollout_file.read_bytes()).hexdigest(), rollout_hash_before)
        self.assertEqual(rollout_file.read_text(), rollout_content)
        self.assertEqual(hashlib.sha256(config_file.read_bytes()).hexdigest(), config_hash_before)

    def test_already_deleted_session_cannot_be_deleted_again(self):
        insert_thread(self.database, 'del-once', self.home / 'once.jsonl', self.external, title='Delete Once')
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            res = delete_session({
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'del-once',
                'confirmed': True,
            }, self.modules, [])
            self.assertTrue(res['deleted'])

            # Second deletion must fail with 404 because session is no longer active in project
            with self.assertRaises(InstallationError) as error:
                delete_session({
                    'project': str(self.external),
                    'repository_mode': 'working_repository',
                    'session_id': 'del-once',
                    'confirmed': True,
                }, self.modules, [])
            self.assertEqual(error.exception.status, 404)

    def test_tombstoned_session_cannot_be_renamed(self):
        insert_thread(self.database, 'del-then-rename', self.home / 'del_ren.jsonl', self.external, title='Title')
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            delete_session({
                'project': str(self.external),
                'repository_mode': 'working_repository',
                'session_id': 'del-then-rename',
                'confirmed': True,
            }, self.modules, [])

            with self.assertRaises(InstallationError) as error:
                rename_session({
                    'project': str(self.external),
                    'repository_mode': 'working_repository',
                    'session_id': 'del-then-rename',
                    'name': 'New Name Attempt',
                }, self.modules, [])
            self.assertEqual(error.exception.status, 404)

    def test_purge_in_memory_session_helper(self):
        class DummyState:
            def __init__(self, session_id):
                self.session_id = session_id

        session_states = {'s-1': DummyState('s-1'), 's-2': DummyState('s-2')}
        session_inventories = {'s-1': {'tools': []}, 's-2': {'tools': []}}
        saved_runs = [
            {'session_id': 's-1', 'run_id': 'r-1'},
            {'context': {'session_id': 's-1'}, 'run_id': 'r-1b'},
            {'session_id': 's-2', 'run_id': 'r-2'},
        ]
        run_states = {'r-1': DummyState('s-1'), 'r-2': DummyState('s-2')}

        purge_in_memory_session('s-1', session_states, saved_runs, run_states, session_inventories)

        self.assertNotIn('s-1', session_states)
        self.assertIn('s-2', session_states)
        self.assertNotIn('s-1', session_inventories)
        self.assertIn('s-2', session_inventories)
        self.assertEqual(len(saved_runs), 1)
        self.assertEqual(saved_runs[0]['session_id'], 's-2')
        self.assertNotIn('r-1', run_states)
        self.assertIn('r-2', run_states)

