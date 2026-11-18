"""Behavioral regression tests for staged connection analysis, session inventory and generate."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_observer import cli
from agent_observer.connections import ConnectionModules, connection_revision
from agent_observer.installation import InstallationError
from agent_observer.observer_config import load_settings


def init_codex_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("""
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                path TEXT NOT NULL,
                PRIMARY KEY (project_id, position)
            )
        """)
        conn.execute("""
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                project_id TEXT,
                originator TEXT
            )
        """)
        conn.commit()


def insert_thread(db_path: Path, thread_id: str, rollout_path: Path | str, cwd: Path | str,
                  title: str = "Test thread", project_id: str | None = None,
                  updated_at: int = 1000, source: str = "vscode", originator: str = "codex_work_desktop"):
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("""
            INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider, cwd, title, sandbox_policy, approval_mode, project_id, originator)
            VALUES (?, ?, ?, ?, ?, 'openai', ?, ?, '{}', 'never', ?, ?)
        """, (thread_id, str(rollout_path), 1000, updated_at, source, str(cwd), title, project_id, originator))
        conn.commit()


def insert_project_root(db_path: Path, project_id: str, path: Path | str):
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("""
            INSERT INTO project_roots (project_id, position, path)
            VALUES (?, 0, ?)
        """, (project_id, str(path)))
        conn.commit()


class ConnectionAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.codex_home = self.home / '.codex'
        self.codex_home.mkdir()
        self.db_path = self.codex_home / 'state_1.sqlite'

        self.config = self.project / 'private/observer.project.json'
        self.config.parent.mkdir()
        self.data = {
            'schema_version': 1,
            'project': {'root': str(self.project), 'name': 'Project', 'agent_label': 'Project agent'},
            'port': 38123
        }
        self.config.write_text(json.dumps(self.data))

        registry = patch('agent_observer.cli.REGISTRY', self.root / 'registry.json')
        registry.start()
        self.addCleanup(registry.stop)

        home = patch('pathlib.Path.home', return_value=self.home)
        home.start()
        self.addCleanup(home.stop)

        env = patch.dict(os.environ, {'CODEX_HOME': str(self.codex_home)})
        env.start()
        self.addCleanup(env.stop)

        codex_inventory = patch('agent_observer.capabilities._codex_cli_inventory', return_value=None)
        codex_inventory.start()
        self.addCleanup(codex_inventory.stop)

        self.settings = load_settings(self.config, environment={})
        self.modules = ConnectionModules(self.settings)
        self.addCleanup(self.modules.shutdown)

    def test_read_only_analysis_does_not_mutate_or_connect(self):
        """Analyze must be completely read-only: no install, no observer launch, no registry write."""
        uninstalled = self.root / 'uninstalled-target'
        uninstalled.mkdir()

        res = self.modules.analyze({'project': str(uninstalled), 'repository_mode': 'working_repository'})

        # Response contract verification
        self.assertEqual(res['repository_mode'], 'working_repository')
        self.assertEqual(res['module'], 'codex')
        self.assertIsInstance(res['revision'], str)
        self.assertTrue(res['analysis_id'].startswith('ana_'))
        self.assertIsInstance(res['ready'], bool)
        self.assertIsInstance(res['checks'], list)
        self.assertIsInstance(res['logs'], list)
        self.assertIsInstance(res['sessions'], list)
        self.assertIsInstance(res['resources'], dict)

        # Filesystem isolation: no .agent-observer created in target
        self.assertFalse((uninstalled / '.agent-observer').exists())

        # Process isolation: no observer started
        self.assertIsNone(self.modules.observer)

        # Registry isolation: uninstalled folder not registered as installation
        self.assertNotIn(str(uninstalled), [i['project'] for i in cli.registry().get('installations', [])])

        # Logs must contain literal text without secrets or env dumps
        for log in res['logs']:
            self.assertIn(log['level'], {'info', 'warning', 'error'})
            self.assertIsInstance(log['message'], str)
            self.assertNotIn('TOKEN', log['message'])
            self.assertNotIn('SECRET', log['message'])

    def test_wrong_folder_codex_session_isolation(self):
        """Sessions from other folders must not leak into analyzed project."""
        init_codex_db(self.db_path)
        proj_a = self.root / 'project-alpha'
        proj_b = self.root / 'project-beta'
        proj_a.mkdir()
        proj_b.mkdir()

        sessions_dir = self.codex_home / 'sessions/2026/01/01'
        sessions_dir.mkdir(parents=True)

        # Project A sessions
        rollout_a1 = sessions_dir / 'a1.jsonl'
        rollout_a1.write_text('{"type": "task_started"}\n')
        insert_thread(self.db_path, 'thread-a1', rollout_a1, proj_a, title='Alpha Session 1')

        rollout_a2 = sessions_dir / 'a2.jsonl'
        rollout_a2.write_text('{"type": "task_started"}\n')
        insert_thread(self.db_path, 'thread-a2', rollout_a2, proj_a / 'subfolder', title='Alpha Subfolder')

        # Project B sessions
        rollout_b1 = sessions_dir / 'b1.jsonl'
        rollout_b1.write_text('{"type": "task_started"}\n')
        insert_thread(self.db_path, 'thread-b1', rollout_b1, proj_b, title='Beta Session 1')

        rollout_b2 = sessions_dir / 'b2.jsonl'
        rollout_b2.write_text('{"type": "task_started"}\n')
        insert_thread(self.db_path, 'thread-b2', rollout_b2, proj_b, title='Beta Session 2')

        # Project ID association for Project A
        rollout_a3 = sessions_dir / 'a3.jsonl'
        rollout_a3.write_text('{"type": "task_started"}\n')
        insert_project_root(self.db_path, 'proj-id-a', proj_a)
        insert_thread(self.db_path, 'thread-a3', rollout_a3, self.root / 'other-cwd', title='Alpha by ID', project_id='proj-id-a')

        # Analyze Project A
        res_a = self.modules.analyze({'project': str(proj_a), 'repository_mode': 'working_repository'})
        a_ids = {s['session_id'] for s in res_a['sessions']}
        self.assertEqual(a_ids, {'thread-a1', 'thread-a2', 'thread-a3'})
        for s in res_a['sessions']:
            self.assertEqual(s['project_path'], str(proj_a))

        # Analyze Project B
        res_b = self.modules.analyze({'project': str(proj_b), 'repository_mode': 'working_repository'})
        b_ids = {s['session_id'] for s in res_b['sessions']}
        self.assertEqual(b_ids, {'thread-b1', 'thread-b2'})
        for s in res_b['sessions']:
            self.assertEqual(s['project_path'], str(proj_b))

        # Complete cross-project isolation verified
        self.assertTrue(a_ids.isdisjoint(b_ids))

    def test_all_matching_session_inventory_beyond_20_and_honest_reporting(self):
        """All matching sessions beyond 20-cap must be returned with honest unreadable/compressed reporting."""
        init_codex_db(self.db_path)
        sessions_dir = self.codex_home / 'sessions/2026/01/01'
        sessions_dir.mkdir(parents=True)

        # Create 28 sessions matching self.project
        # 25 regular readable sessions
        for i in range(25):
            rpath = sessions_dir / f'session-{i}.jsonl'
            rpath.write_text('{"type": "task_started"}\n')
            insert_thread(self.db_path, f'thread-{i:02d}', rpath, self.project, title=f'Session {i}', updated_at=1000 + i)

        # 2 compressed archives (.zst and .gz)
        zst_path = sessions_dir / 'archived-session.zst'
        zst_path.touch()
        insert_thread(self.db_path, 'thread-zst', zst_path, self.project, title='Compressed ZST', updated_at=2000)

        gz_path = sessions_dir / 'archived-session.gz'
        gz_path.touch()
        insert_thread(self.db_path, 'thread-gz', gz_path, self.project, title='Compressed GZ', updated_at=2001)

        # 1 missing rollout file on disk
        missing_path = sessions_dir / 'missing-on-disk.jsonl'
        insert_thread(self.db_path, 'thread-missing', missing_path, self.project, title='Missing Rollout', updated_at=2002)

        res = self.modules.analyze({'project': str(self.project), 'repository_mode': 'working_repository'})

        # Must NOT cap at 20: exactly 28 sessions returned
        self.assertEqual(len(res['sessions']), 28)

        # Check compressed sessions reporting
        zst_session = next(s for s in res['sessions'] if s['session_id'] == 'thread-zst')
        self.assertEqual(zst_session['status'], 'compressed')
        self.assertTrue(zst_session['compressed'])
        self.assertTrue(zst_session['unreadable'])
        self.assertIn('.zst', zst_session['reason'])

        gz_session = next(s for s in res['sessions'] if s['session_id'] == 'thread-gz')
        self.assertEqual(gz_session['status'], 'compressed')
        self.assertTrue(gz_session['compressed'])
        self.assertTrue(gz_session['unreadable'])

        # Check missing rollout reporting
        missing_session = next(s for s in res['sessions'] if s['session_id'] == 'thread-missing')
        self.assertEqual(missing_session['status'], 'unreadable')
        self.assertTrue(missing_session['unreadable'])

        # Check rollout_health check warns honestly but does not block readiness
        rollout_check = next(c for c in res['checks'] if c['id'] == 'rollout_health')
        self.assertEqual(rollout_check['status'], 'warning')
        self.assertEqual(rollout_check['count'], 3)
        # Distinguish total indexed from readable journals
        self.assertEqual(res['resources']['sessions_total'], 28)
        self.assertEqual(res['resources']['sessions_readable'], 25)
        self.assertEqual(res['resources']['sessions_compressed'], 2)
        self.assertEqual(res['resources']['sessions_unreadable'], 3)

        sess_check = next(c for c in res['checks'] if c['id'] == 'sessions_discovery')
        self.assertEqual(sess_check['count'], 28)
        self.assertIn('28 indexed session(s) discovered (25 readable journal(s)', sess_check['detail'])

        # Readiness is True: warnings on compressed/unreadable rollouts do not block connection
        self.assertTrue(res['ready'])

    def test_invalidation_for_path_role_revision_and_missing_prerequisite(self):
        """Generate must reject stale revisions, mismatched paths/roles, or failed prerequisites with 409."""
        init_codex_db(self.db_path)
        sessions_dir = self.codex_home / 'sessions/2026/01/01'
        sessions_dir.mkdir(parents=True)
        rpath = sessions_dir / 'session-valid.jsonl'
        rpath.write_text('{"type": "task_started"}\n')
        insert_thread(self.db_path, 'thread-valid', rpath, self.project, title='Valid Session')

        ana = self.modules.analyze({'project': str(self.project), 'repository_mode': 'working_repository'})
        self.assertTrue(ana['ready'])
        analysis_id = ana['analysis_id']
        rev = ana['revision']

        # 1. Missing / invalid analysis_id
        with self.assertRaises(InstallationError) as cm:
            self.modules.generate({'project': str(self.project), 'repository_mode': 'working_repository',
                                   'revision': rev, 'analysis_id': 'bogus-id-12345'})
        self.assertEqual(cm.exception.status, 409)

        # 2. Mismatched project path
        other_proj = self.root / 'different-project'
        other_proj.mkdir()
        with self.assertRaises(InstallationError) as cm:
            self.modules.generate({'project': str(other_proj), 'repository_mode': 'working_repository',
                                   'revision': rev, 'analysis_id': analysis_id})
        self.assertEqual(cm.exception.status, 409)

        # 3. Mismatched repository role
        with self.assertRaises(InstallationError) as cm:
            self.modules.generate({'project': str(self.project), 'repository_mode': 'agent_system',
                                   'revision': rev, 'analysis_id': analysis_id})
        self.assertEqual(cm.exception.status, 409)

        # 4. Stale revision in request payload
        with self.assertRaises(InstallationError) as cm:
            self.modules.generate({'project': str(self.project), 'repository_mode': 'working_repository',
                                   'revision': 'stale-client-rev-999', 'analysis_id': analysis_id})
        self.assertEqual(cm.exception.status, 409)

        # 5. Changed configuration on disk invalidates scanned proof
        self.config.write_text(json.dumps({'schema_version': 1, 'project': {'root': str(self.project), 'name': 'Renamed'}, 'port': 38123}))
        with self.assertRaises(InstallationError) as cm:
            self.modules.generate({'project': str(self.project), 'repository_mode': 'working_repository',
                                   'revision': rev, 'analysis_id': analysis_id})
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("changed since analysis", cm.exception.detail)

        # 6. Failed analysis cannot be generated (missing integration for generic agent)
        uninstrumented = self.root / 'generic-agent-repo'
        uninstrumented.mkdir()
        uninst_ana = self.modules.analyze({'project': str(uninstrumented), 'repository_mode': 'agent_system'})
        self.assertFalse(uninst_ana['ready'])
        with self.assertRaises(InstallationError) as cm:
            self.modules.generate({'project': str(uninstrumented), 'repository_mode': 'agent_system',
                                   'revision': uninst_ana['revision'], 'analysis_id': uninst_ana['analysis_id']})
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("failed readiness", cm.exception.detail)

    def test_agent_system_readiness_and_zero_sessions_warn_not_fail(self):
        """Agent system with CAD telemetry succeeds with 0 sessions (waiting status, not error)."""
        (self.project / 'src/CADAgentUI').mkdir(parents=True)
        (self.project / 'scripts').mkdir()
        (self.project / 'scripts/xray_server.py').write_text('from agent_observer.observer_server import main\n')

        ana = self.modules.analyze({'project': str(self.project), 'repository_mode': 'agent_system'})

        self.assertEqual(ana['repository_mode'], 'agent_system')
        self.assertEqual(ana['module'], 'project')
        self.assertTrue(ana['ready'])

        # Telemetry is pass
        telem_check = next(c for c in ana['checks'] if c['id'] == 'telemetry_readiness')
        self.assertEqual(telem_check['status'], 'pass')

        # Sessions check is waiting, not error
        sess_check = next(c for c in ana['checks'] if c['id'] == 'sessions')
        self.assertEqual(sess_check['status'], 'waiting')
        self.assertEqual(sess_check['count'], 0)

        # Generate succeeds
        gen = self.modules.generate({
            'project': str(self.project),
            'repository_mode': 'agent_system',
            'revision': ana['revision'],
            'analysis_id': ana['analysis_id'],
        })
        self.assertTrue(gen['generated'])
        self.assertEqual(gen['workspace']['module'], 'project')

    def test_deep_documents_are_found_and_incomplete_scans_block_generation(self):
        from agent_observer.connection_analysis import scan_project_resources
        init_codex_db(self.db_path)
        deep = self.project / 'docs/a/b/c/d/e/f/g'
        deep.mkdir(parents=True)
        (deep / 'reference.markdown').write_text('# Deep reference')
        for index in range(25):
            (self.project / f'note-{index:02}.md').write_text('# Reference')
        scan = scan_project_resources(self.project)
        self.assertFalse(scan['traversal_limited'])
        self.assertIn('docs/a/b/c/d/e/f/g/reference.markdown', scan['doc_files'])
        self.assertEqual(scan['doc_file_count'], 26)
        with patch('agent_observer.connection_analysis.scan_project_resources',
                   side_effect=lambda root: scan_project_resources(root, max_files=1)):
            report = self.modules.analyze({'project': str(self.project), 'repository_mode': 'working_repository'})
        self.assertFalse(report['ready'])
        self.assertTrue(report['resources']['traversal_limited'])

    def test_embedded_sessions_use_target_history_not_the_open_graph_history(self):
        other = self.root / 'other-agent'
        other.mkdir()
        other_config = other / '.agent-observer/config/observer.project.json'
        other_config.parent.mkdir(parents=True)
        other_config.write_text(json.dumps({'schema_version': 1, 'project': {'root': str(other)}}))
        source_history = self.settings['history_file']
        source_history.parent.mkdir(parents=True, exist_ok=True)
        source_history.write_text(json.dumps([{'session_id': 'private-current', 'context': {'project_root': str(self.project), 'application': 'Embedded A'}}]))
        target_history = load_settings(other_config, environment={})['history_file']
        target_history.parent.mkdir(parents=True, exist_ok=True)
        target_history.write_text(json.dumps([
            {'session_id': 'other-session', 'run_id': 'latest', 'context': {'project_root': str(other), 'application': 'Embedded B'}},
            {'session_id': 'other-session', 'run_id': 'older', 'context': {'project_root': str(other), 'application': 'Embedded B'}},
            {'session_id': 'foreign', 'context': {'project_root': str(self.project), 'application': 'Embedded A'}},
            {'session_id': 'external-codex', 'context': {'project_root': str(other), 'application': 'Codex'}}
        ]))
        report = self.modules.analyze({'project': str(other), 'repository_mode': 'agent_system'})
        self.assertEqual([session['session_id'] for session in report['sessions']], ['other-session'])
        self.assertEqual(report['sessions'][0]['run_id'], 'latest')

    def test_generate_rechecks_index_readability_before_mutating_configuration(self):
        init_codex_db(self.db_path)
        report = self.modules.analyze({'project': str(self.project), 'repository_mode': 'working_repository'})
        self.assertTrue(report['ready'])
        original = self.config.read_bytes()
        self.db_path.write_bytes(b'not a SQLite database')
        with self.assertRaises(InstallationError) as error:
            self.modules.generate({'project': str(self.project), 'repository_mode': 'working_repository',
                                   'analysis_id': report['analysis_id'], 'revision': report['revision']})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertIsNone(self.modules.observer)

    def test_discovered_documents_join_existing_sources_only_after_generate(self):
        from agent_observer.observer_config import resource_catalog
        init_codex_db(self.db_path)
        (self.project / 'docs/nested').mkdir(parents=True)
        (self.project / 'docs/nested/deep.md').write_text('# Deep')
        (self.project / 'handbook').mkdir()
        (self.project / 'handbook/reference.markdown').write_text('# Reference')
        self.data['documents'] = [{'path': 'docs', 'include': ['*.md']}]
        self.config.write_text(json.dumps(self.data))
        before = self.config.read_bytes()
        report = self.modules.analyze({'project': str(self.project), 'repository_mode': 'working_repository'})
        self.assertEqual(self.config.read_bytes(), before)
        self.modules.generate({'project': str(self.project), 'repository_mode': 'working_repository',
                               'analysis_id': report['analysis_id'], 'revision': report['revision']})
        catalog = resource_catalog(load_settings(self.config, environment={}))
        paths = {node.get('path') for node in catalog['nodes'] if node['kind'] == 'document'}
        self.assertIn('docs/nested/deep.md', paths)
        self.assertEqual(json.loads(self.config.read_text())['port'], 8766)
        self.assertIn('handbook/reference.markdown', paths)

    def test_example_and_library_mentions_do_not_prove_an_embedded_agent(self):
        example = self.project / 'examples'
        example.mkdir()
        source = 'from agent_observer.observer_client import ObserverClient\nclient=ObserverClient()\nclient.emit({\"type\":\"PROMPT_START\"})\n'
        (example / 'demo.py').write_text(source)
        (self.project / 'library.py').write_text('# agent_observer integration example')
        report = self.modules.analyze({'project': str(self.project), 'repository_mode': 'agent_system'})
        self.assertFalse(report['ready'])
        runtime = self.project / 'runtime.py'
        runtime.write_text(source)
        report = self.modules.analyze({'project': str(self.project), 'repository_mode': 'agent_system'})
        self.assertTrue(report['ready'])
        runtime.unlink()
        with self.assertRaises(InstallationError) as error:
            self.modules.generate({'project': str(self.project), 'repository_mode': 'agent_system',
                                   'analysis_id': report['analysis_id'], 'revision': report['revision']})
        self.assertEqual(error.exception.status, 409)


if __name__ == '__main__':
    unittest.main()
