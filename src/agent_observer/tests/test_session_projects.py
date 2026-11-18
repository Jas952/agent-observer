"""Sidebar project grouping must not mix chats or activate foreign runtimes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_observer import cli
from agent_observer.connections import ConnectionModules
from agent_observer.observer_config import load_settings
from agent_observer.session_projects import discover_session_projects
from test_connection_analysis import init_codex_db, insert_thread


class SessionProjectsTests(unittest.TestCase):
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
        for project, role in ((self.current, 'working_repository'), (self.external, 'working_repository'), (self.embedded, 'agent_system')):
            config = project / '.agent-observer/config/observer.project.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'project': {'root': str(project)}, 'port': 43211,
                                         'connection': {'module': 'codex' if role == 'working_repository' else 'project', 'repository_mode': role}}))
        self.config = self.current / '.agent-observer/config/observer.project.json'
        self.home = self.root / 'codex'
        self.database = self.home / 'state_1.sqlite'
        init_codex_db(self.database)
        registry = self.root / 'registry.json'
        home = patch('pathlib.Path.home', return_value=self.root / 'home')
        home.start()
        self.addCleanup(home.stop)
        registry.write_text(json.dumps({'workspaces': [{'path': str(project)} for project in (self.external, self.embedded)]}))
        for manager in (patch.object(cli, 'REGISTRY', registry), patch.dict(os.environ, {'CODEX_HOME': str(self.home)})):
            manager.start()
            self.addCleanup(manager.stop)
        self.modules = ConnectionModules(load_settings(self.config, environment={}))
        self.addCleanup(self.modules.shutdown)

    def test_groups_are_scoped_by_folder_and_role_without_truncating_chats(self):
        for index in range(35):
            insert_thread(self.database, f'external-{index}', self.home / f'{index}.jsonl', self.external)
        insert_thread(self.database, 'unregistered-private', self.home / 'private.jsonl', self.other)
        insert_thread(self.database, 'codex-in-agent-folder', self.home / 'embedded.jsonl', self.embedded)
        settings = load_settings(self.embedded / '.agent-observer/config/observer.project.json', environment={})
        history = settings['history_file']
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(json.dumps([
            {'session_id': 'embedded-chat', 'context': {'application': 'Project agent', 'project_root': str(self.embedded)}},
            {'session_id': 'external-in-history', 'context': {'application': 'Codex', 'project_root': str(self.embedded)}},
            {'session_id': 'wrong-folder', 'context': {'application': 'Project agent', 'project_root': str(self.other)}}
        ]))
        before = self.config.read_bytes()
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}), \
             patch.object(self.modules, 'connect', side_effect=AssertionError('Sidebar must not connect')):
            groups = discover_session_projects(self.modules, [{'session_id': 'live-current', 'status': 'running'}])
        by_path = {row['path']: row for row in groups}
        self.assertEqual(set(by_path), {str(self.current), str(self.external), str(self.embedded)})
        self.assertEqual([session['session_id'] for session in by_path[str(self.current)]['sessions']], ['live-current'])
        self.assertEqual(len(by_path[str(self.external)]['sessions']), 35)
        self.assertEqual([session['session_id'] for session in by_path[str(self.embedded)]['sessions']], ['embedded-chat'])
        self.assertTrue(all(row['graph_url'] is None for row in groups))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIsNone(self.modules.observer)

    def test_a_missing_folder_does_not_hide_other_project_chats(self):
        # Keep a registered but unavailable project alongside a healthy current graph.
        missing = self.root / 'missing'
        registry = cli.registry()
        registry['workspaces'].append({'path': str(missing)})
        cli.write_json(cli.REGISTRY, registry)
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            groups = discover_session_projects(self.modules, [{'session_id': 'live-current'}])
        self.assertEqual(next(row for row in groups if row['current'])['sessions'][0]['session_id'], 'live-current')
        self.assertEqual(next(row for row in groups if row['path'] == str(missing))['error'], 'Project folder is unavailable.')

    def test_foreign_settings_use_only_the_requested_project_without_activation(self):
        from agent_observer.project_settings import ProjectSettings
        for project in (self.current, self.external):
            (project / 'docs').mkdir()
            (project / 'docs' / (project.name + '.md')).write_text('# ' + project.name)
        before = self.config.read_bytes()
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}), \
             patch.object(self.modules, 'connect', side_effect=AssertionError('Settings must not connect')):
            response = ProjectSettings(self.modules).request(str(self.external), '/api/graph-settings')
        paths = {node.get('path') for node in response['config']['nodes'] if node['kind'] == 'document'}
        self.assertIn('docs/external.md', paths)
        self.assertNotIn('docs/current.md', paths)
        self.assertEqual(response['project_root'], str(self.external))
        self.assertEqual(response['config']['project_root'], str(self.external))
        self.assertEqual(self.modules.root, self.current)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIsNone(self.modules.observer)

    def test_settings_reject_unsaved_roots_and_non_settings_operations(self):
        from agent_observer.project_settings import ProjectSettings
        from agent_observer.installation import InstallationError
        gateway = ProjectSettings(self.modules)
        with self.assertRaises(InstallationError) as error:
            gateway.request(str(self.other), '/api/graph-settings')
        self.assertEqual(error.exception.status, 403)
        for endpoint, method in (('/api/config', 'GET'), ('https://example.com/', 'GET'),
                                 ('/api/local-services/%2e%2e/start', 'POST'),
                                 ('/api/graph-settings/reveal', 'GET'), ('/api/sessions', 'POST')):
            with self.subTest(endpoint=endpoint), self.assertRaises(InstallationError) as error:
                gateway.request(str(self.external), endpoint, method)
            self.assertEqual(error.exception.status, 400)

    def test_foreign_codex_activity_requires_a_recent_unfinished_turn(self):
        sessions_dir = self.home / 'sessions'
        sessions_dir.mkdir()
        rollout = sessions_dir / 'active.jsonl'
        started = {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'active-turn'}}
        rollout.write_text(json.dumps(started) + '\n')
        insert_thread(self.database, 'live-foreign', rollout, self.external)
        def status():
            with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
                rows = discover_session_projects(self.modules, [])
            return next(row for row in rows if row['path'] == str(self.external))['sessions'][0]['status']
        self.assertEqual(status(), 'running')
        with rollout.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'active-turn'}}) + '\n')
        self.assertEqual(status(), 'idle')
        rollout.write_text(json.dumps(started) + '\n')
        os.utime(rollout, (1000, 1000))
        self.assertEqual(status(), 'unknown')


    def test_foreign_graph_requires_matching_root_and_dashboard_protocol(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from agent_observer.connections import connection_revision
        from agent_observer.installation import DASHBOARD_PROTOCOL, InstallationError, _calculate_revision

        target = self.external / '.agent-observer'
        config_path = target / 'config/observer.project.json'
        settings = load_settings(config_path, environment={})
        token_file = settings['token_file']
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text('private-fixture-token')
        token_file.chmod(0o600)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/health':
                    payload = {'service': 'agent-observer'}
                    if self.server.protocol is not None:
                        payload['dashboard_protocol'] = self.server.protocol
                elif self.path == '/api/config' and self.headers.get('X-Agent-Observer-Token') == 'private-fixture-token':
                    payload = {'project_root': str(self.server.project_root)}
                else:
                    self.send_error(404)
                    return
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.protocol = None
        server.project_root = self.external
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def close_server():
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()
        self.addCleanup(close_server)
        port = server.server_address[1]
        for project in (self.external, self.embedded):
            path = project / '.agent-observer/config/observer.project.json'
            data = json.loads(path.read_text())
            data['port'] = port
            path.write_text(json.dumps(data))
        before = config_path.read_bytes()

        legacy = self.modules.installations._inspect_connection(self.external, port, target)
        self.assertTrue(legacy['connected'])
        self.assertFalse(legacy['dashboard_compatible'])
        rows = discover_session_projects(self.modules, [])
        external = next(row for row in rows if row['path'] == str(self.external))
        self.assertIsNone(external['graph_url'])
        self.assertNotIn('error',external)
        with patch('agent_observer.capabilities._codex_cli_inventory', return_value=None):
            analysis = self.modules.analyze({'project': str(self.external), 'repository_mode': 'working_repository'})
        self.assertFalse(analysis['ready'])
        self.assertTrue(any(check['id'] == 'dashboard_protocol' and check['status'] == 'error' for check in analysis['checks']))
        with patch('agent_observer.host_runtime.start_host',return_value=8766) as start:
            result=self.modules.installations.start_graph({'project':str(self.external),'revision':_calculate_revision(self.external,target)})
        start.assert_called_once()
        self.assertTrue(result['url'].startswith('http://127.0.0.1:8766/?project='))
        self.assertEqual(config_path.read_bytes(),before)
        with self.assertRaises(InstallationError):
            self.modules.connect({'project':str(self.external),'module':'project',
                                  'revision':connection_revision(self.external,target,config_path)})

        server.protocol = DASHBOARD_PROTOCOL
        self.assertTrue(self.modules.installations._inspect_connection(self.external,port,target)['dashboard_compatible'])
        server.project_root = self.other
        self.assertFalse(self.modules.installations._inspect_connection(self.external,port,target)['connected'])

    def test_renames_persist_by_project_and_role_without_changing_source_titles(self):
        from agent_observer.session_names import rename_session, apply_session_names, read_session_names
        from agent_observer.installation import InstallationError
        current = [{'session_id': 'shared', 'session_name': 'Original current', 'application': 'Codex'}]
        insert_thread(self.database, 'shared', self.home / 'shared.jsonl', self.external, title='Original external')
        before = {project: (project / '.agent-observer/config/observer.project.json').read_bytes() for project in (self.current, self.external)}
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            for project, name in ((self.current, 'Мой проект'), (self.external, 'Вторая сессия')):
                rename_session({'project': str(project), 'repository_mode': 'working_repository', 'session_id': 'shared', 'name': name}, self.modules, current)
            restarted = ConnectionModules(load_settings(self.config, environment={}))
            rows = discover_session_projects(restarted, current)
            self.assertEqual(next(row for row in rows if row['current'])['sessions'][0]['display_name'], 'Мой проект')
            self.assertEqual(next(row for row in rows if row['path'] == str(self.external))['sessions'][0]['display_name'], 'Вторая сессия')
            self.assertEqual(next(row for row in rows if row['path'] == str(self.external))['sessions'][0]['session_name'], 'Original external')
            for project, role, name, status in (
                (self.current, 'agent_system', 'Wrong role', 409),
                (self.other, 'working_repository', 'Wrong folder', 404),
                (self.current, 'working_repository', '   ', 400),
                (self.current, 'working_repository', 'x' * 121, 400),
                (self.current, 'working_repository', 'two\nlines', 400),
            ):
                with self.assertRaises(InstallationError) as error:
                    rename_session({'project': str(project), 'repository_mode': role, 'session_id': 'shared', 'name': name}, self.modules, current)
                self.assertEqual(error.exception.status, status)
        self.assertEqual(current[0]['session_name'], 'Original current')
        self.assertNotIn('display_name', current[0])
        self.assertEqual(apply_session_names(current, self.modules.settings, self.current, 'working_repository')[0]['display_name'], 'Мой проект')
        self.assertEqual(read_session_names(self.modules.settings, self.current, 'agent_system'), {})
        for project, content in before.items():
            self.assertEqual((project / '.agent-observer/config/observer.project.json').read_bytes(), content)

    def test_visibility_is_project_scoped_revisioned_and_required_blocks_cannot_hide(self):
        from agent_observer.project_settings import ProjectSettings
        from agent_observer.documents import DocumentError
        gateway = ProjectSettings(self.modules)
        self.addCleanup(gateway.shutdown)
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            current = gateway.request(str(self.current), '/api/block-settings')
            external = gateway.request(str(self.external), '/api/block-settings')
            saved = gateway.request(str(self.external), '/api/block-settings', 'POST',
                                    {'revision':external['revision'], 'hidden_blocks':['tools','commands']})
            self.assertEqual(saved['hidden_blocks'], ['commands','tools'])
            self.assertEqual(gateway.request(str(self.current), '/api/block-settings')['hidden_blocks'], [])
            with self.assertRaises(DocumentError) as conflict:
                gateway.request(str(self.external), '/api/block-settings', 'POST',
                                {'revision':external['revision'], 'hidden_blocks':[]})
            self.assertEqual(conflict.exception.status, 409)
            for required in ('request', 'agent', 'result'):
                with self.subTest(required=required), self.assertRaises(ValueError):
                    gateway.request(str(self.current), '/api/block-settings', 'POST',
                                    {'revision':current['revision'], 'hidden_blocks':[required]})
            self.assertEqual(gateway.request(str(self.current), '/api/block-settings')['revision'], current['revision'])

    def test_resource_visibility_is_folder_scoped_revisioned_and_protects_core_nodes(self):
        from agent_observer.project_settings import ProjectSettings
        from agent_observer.documents import DocumentError
        gateway = ProjectSettings(self.modules)
        self.addCleanup(gateway.shutdown)
        original = self.config.read_bytes()
        hidden = ['node:mcp_blender', f'document:{self.external}/docs/Reference.md',
                  f'file:{self.external}/src/main.py']
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected': False}):
            external = gateway.request(str(self.external), '/api/block-settings')
            saved = gateway.request(str(self.external), '/api/block-settings', 'POST',
                                    {'revision':external['revision'], 'hidden_resources':hidden,
                                     'hidden_commands':['command_observed_bash']})
            reread = gateway.request(str(self.external), '/api/block-settings')
            self.assertEqual(set(reread['hidden_resources']), set(hidden))
            self.assertEqual(reread['hidden_commands'], ['command_observed_bash'])
            self.assertEqual(self.config.read_bytes(), original)
            with self.assertRaises(DocumentError) as conflict:
                gateway.request(str(self.external), '/api/block-settings', 'POST',
                                {'revision':external['revision'], 'hidden_resources':[]})
            self.assertEqual(conflict.exception.status, 409)
            for required in ('request_prompt', 'agent-hub', 'run_result'):
                with self.subTest(required=required), self.assertRaises(ValueError):
                    gateway.request(str(self.external), '/api/block-settings', 'POST',
                                    {'revision':saved['revision'], 'hidden_resources':[f'node:{required}']})
            self.assertEqual(gateway.request(str(self.external), '/api/block-settings')['revision'], saved['revision'])
            restored = gateway.request(str(self.external), '/api/block-settings', 'POST',
                                       {'revision':saved['revision'], 'hidden_resources':[], 'hidden_commands':[]})
            self.assertEqual(restored['hidden_resources'], [])
            self.assertEqual(restored['hidden_commands'], [])

    def test_foreign_service_runs_and_streams_only_inside_its_saved_folder(self):
        import sys
        import time
        from agent_observer.project_settings import ProjectSettings
        from agent_observer.local_services import LocalServiceError
        from test_local_services import FIXTURE_SERVER_CODE, _find_free_port, wait_port_open
        ports = {}
        for project in (self.current, self.external):
            port = _find_free_port()
            while port in ports.values():
                port = _find_free_port()
            ports[project] = port
            script = project / 'server.py'
            script.write_text(FIXTURE_SERVER_CODE)
            path = project / '.agent-observer/config/observer.project.json'
            data = json.loads(path.read_text())
            data['local_services'] = [{'id':'same-id','endpoints':[f'http://127.0.0.1:{port}'],
                'command':[sys.executable,str(script),'--port',str(port),'--stdout-msg',project.name+'-output'],
                'health_kind':'http','shutdown_timeout':1}]
            path.write_text(json.dumps(data))
        gateway = ProjectSettings(self.modules)
        self.addCleanup(gateway.shutdown)
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected':False}):
            gateway.request(str(self.external), '/api/local-services/same-id/start', 'POST', {})
            self.assertTrue(wait_port_open(ports[self.external]))
            logs = []
            deadline = time.monotonic()+5
            while time.monotonic()<deadline and not any('external-output' in line['text'] for line in logs):
                logs = gateway.request(str(self.external), '/api/local-services/same-id/logs?after=0')['lines']
                time.sleep(.02)
            self.assertTrue(any('external-output' in line['text'] for line in logs))
            self.assertFalse(any('external-output' in line['text'] for line in gateway.request(str(self.current), '/api/local-services/same-id/logs?after=0')['lines']))
            service = gateway.request(str(self.external), '/api/local-services')['services'][0]
            self.assertEqual(service['status'],'running')
            with self.assertRaises(LocalServiceError) as wrong_folder:
                gateway.request(str(self.current), '/api/local-services/same-id/stop', 'POST', {'identity':service['identity']})
            self.assertEqual(wrong_folder.exception.status,409)
            self.assertEqual(gateway.request(str(self.external), '/api/local-services')['services'][0]['status'],'running')
            stopped = gateway.request(str(self.external), '/api/local-services/same-id/stop', 'POST', {'identity':service['identity']})['service']
            self.assertEqual(stopped['status'],'stopped')

    def test_vault_selection_updates_only_the_target_folder_and_discovers_external_notes(self):
        from agent_observer.project_settings import ProjectSettings
        from agent_observer.graph_settings import GraphSettings
        vault = self.root / 'obsidian-vault'
        vault.mkdir()
        (vault / 'Lesson.md').write_text('# Durable lesson\nKeep source evidence.\n')
        gateway = ProjectSettings(self.modules)
        self.addCleanup(gateway.shutdown)
        original = self.config.read_bytes()
        with patch.object(self.modules.installations, '_inspect_connection', return_value={'connected':False}):
            current = gateway.request(str(self.external), '/api/block-settings')
            saved = gateway.request(str(self.external), '/api/block-settings', 'POST',
                                    {'revision':current['revision'],'vault_path':str(vault)})
            self.assertEqual(saved['vault_path'],str(vault))
            self.assertEqual(self.config.read_bytes(),original)
            self.assertEqual(gateway.request(str(self.current), '/api/block-settings')['vault_path'],'')
            note = next(node for node in saved['catalog']['nodes'] if node.get('path')=='@vault/Lesson.md')
            _, _, _, settings = gateway.resolve(str(self.external))
            row = next(row for row in GraphSettings(settings).inventory(saved['catalog'], {})['rows'] if row['node_id']==note['id'])
            self.assertTrue(row['in_graph'])
            self.assertTrue(row['detected'])
            self.assertTrue(row['root_available'])
            removed = gateway.request(str(self.external), '/api/block-settings', 'POST',
                                      {'revision':saved['revision'],'vault_path':''})
            self.assertFalse(any(node.get('path')=='@vault/Lesson.md' for node in removed['catalog']['nodes']))
            self.assertTrue((vault/'Lesson.md').is_file())

    def test_both_agents_share_folder_and_live_results_do_not_hide_native_sessions(self):
        from agent_observer.project_settings import ProjectSettings
        from agent_observer.repository_observer import RepositoryObserver

        omp_home = self.root / 'omp'
        journals = omp_home / 'sessions/project'
        journals.mkdir(parents=True)
        for session, project in [('shared', self.external), ('foreign', self.other)]:
            (journals / (session + '.jsonl')).write_text(json.dumps({
                'type': 'session', 'version': 3, 'id': session,
                'cwd': str(project), 'timestamp': '2026-11-16T00:00:00Z',
            }) + '\n')
            insert_thread(self.database, session, self.home / (session + '.jsonl'), project)
        with patch.dict(os.environ, {'PI_CODING_AGENT_DIR': str(omp_home)}):
            sessions = RepositoryObserver.discover_sessions(self.external)
            self.assertEqual({row['session_id'] for row in sessions}, {'shared', 'omp:shared'})
            self.assertEqual({row['application'] for row in sessions}, {'Codex', 'OMP'})
            from types import SimpleNamespace
            runtime=SimpleNamespace(discover_all_sessions=lambda:([{'session_id':'shared','application':'Codex','status':'running','project_root':str(self.external)}],None))
            transport=SimpleNamespace(primary=SimpleNamespace(SETTINGS={'port':8766}),runtimes={str(self.external):runtime})
            with patch.object(self.modules.installations,'transport',transport,create=True):
                groups = discover_session_projects(self.modules, [])
            external = next(row for row in groups if row['path'] == str(self.external))
            rows = {row['session_id']: row for row in external['sessions']}
            self.assertEqual(set(rows), {'shared', 'omp:shared'})
            self.assertEqual(rows['shared']['status'], 'running')
            self.assertEqual(external['graph_url'],'http://127.0.0.1:8766/')
            self.assertEqual(rows['omp:shared']['application'], 'OMP')

if __name__ == '__main__':
    unittest.main()
