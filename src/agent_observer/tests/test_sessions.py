"""Independent sessions, concurrent turns and reconnect-safe result selection."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent_observer.results import ResultStore
from agent_observer.capabilities import mcp_node_id

_sandbox = tempfile.TemporaryDirectory(prefix='observer-sessions-')
_root = Path(_sandbox.name)
with patch.dict(os.environ, {
    'AGENT_OBSERVER_PROJECT': str(_root),
    'AGENT_OBSERVER_TOKEN_FILE': str(_root / 'token'),
    'AGENT_OBSERVER_HISTORY_FILE': str(_root / 'runs.json'),
}):
    from agent_observer import observer_server as server


def tearDownModule():
    server._persist_queue.join()
    _sandbox.cleanup()


class SessionBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in {
            'saved_runs': [], '_codex_receipts': {},
            'RESULTS': ResultStore(self.temp.name, Path(self.temp.name) / '.captures'),
            '_enqueue_history': lambda records: None,
        }.items():
            manager = patch.object(server, name, value)
            manager.start()
            self.addCleanup(manager.stop)
        self.connection = patch.dict(server.SETTINGS, {'connection': {'module': 'project', 'enabled': True}})
        self.connection.start()
        self.addCleanup(self.connection.stop)
        server.route_event({'type': 'RESET'})
        self.addCleanup(lambda: server.route_event({'type': 'RESET'}))

    def send(self, session, run, kind='PROMPT_START', **fields):
        return server.route_event({'type': kind, 'session_id': session, 'run_id': run, **fields})[0]

    def detail(self, session):
        response = asyncio.run(server.get_session_detail(session))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.body)

    def test_interleaved_sessions_preserve_independent_prompts_calls_and_results(self):
        self.send('alpha', 'a', prompt='Read alpha')
        self.send('beta', 'b', prompt='Inspect beta')
        self.send('alpha', 'a', 'TOOL_CALL_START', tool='read', call_id='read-a', input={'path': 'alpha.txt'})
        self.send('beta', 'b', 'TOOL_CALL_START', tool='bash', call_id='bash-b', input={'command': 'printf beta'})
        self.send('alpha', 'a', 'TOOL_CALL_END', tool='read', call_id='read-a', success=True, result='alpha content')
        self.send('alpha', 'a', 'GENERATION_END', result='Alpha finished')
        alpha, beta = self.detail('alpha'), self.detail('beta')
        self.assertEqual(alpha['state']['prompt'], 'Read alpha')
        self.assertEqual(alpha['state']['nodes']['run_result']['payload'], 'Alpha finished')
        self.assertEqual(beta['state']['prompt'], 'Inspect beta')
        self.assertEqual(beta['state']['orchestrator_phase'], 'running')
        self.assertNotIn('run_result', beta['state']['nodes'])
        self.assertEqual({step['event'].get('call_id') for step in alpha['history']} - {None}, {'read-a'})
        self.assertEqual({step['event'].get('call_id') for step in beta['history']} - {None}, {'bash-b'})

    def test_overlapping_turns_in_one_session_do_not_overwrite_each_other(self):
        self.send('shared', 'older', prompt='Older turn')
        self.send('shared', 'newer', prompt='Newer turn')
        older = self.send('shared', 'older', 'GENERATION_END', result='Older answer')
        self.assertEqual(older['prompt'], 'Older turn')
        self.assertEqual(self.detail('shared')['state']['prompt'], 'Newer turn')
        self.assertEqual(server.run_states['newer'].current_run_id, 'newer')
        with self.assertRaisesRegex(ValueError, 'another session'):
            self.send('foreign', 'newer', 'GENERATION_END', result='Wrong session')

    def test_idle_discovered_session_never_borrows_active_request(self):
        self.send('active', 'active-run', prompt='Private active request')
        with patch.object(server, 'discover_all_sessions', return_value=([{'session_id': 'idle'}], 'idle')):
            idle = self.detail('idle')
        self.assertEqual(idle['history'], [])
        self.assertEqual(idle['state']['prompt'], '')
        self.assertEqual(idle['state']['orchestrator_phase'], 'idle')
        self.assertEqual(idle['state']['context']['session_id'], 'idle')
        self.assertEqual(self.detail('active')['state']['prompt'], 'Private active request')
        self.assertEqual(asyncio.run(server.get_session_detail('not-discovered')).status_code, 404)

    def test_module_selection_excludes_foreign_sessions_and_legacy_ids_are_not_invented(self):
        self.send('project-session', 'project-run', prompt='Project request')
        self.send('codex-session', 'codex-run', prompt='Codex request', application='Codex')
        server.route_event({'type': 'PROMPT_START', 'run_id': 'legacy', 'prompt': 'Legacy request'})
        rows, default = server.discover_all_sessions()
        self.assertEqual([row['session_id'] for row in rows], ['project-session'])
        self.assertEqual(default, 'project-session')
        self.assertNotIn('state', rows[0])
        self.assertNotIn('history', rows[0])

    def test_saved_session_restores_latest_result_after_restart_without_starting_a_run(self):
        self.send('saved', 'first', prompt='First request')
        self.send('saved', 'first', 'GENERATION_END', result='First answer')
        self.send('saved', 'latest', prompt='Latest request')
        self.send('saved', 'latest', 'GENERATION_END', result='Latest answer')
        server.session_states.clear()
        restored = self.detail('saved')
        self.assertEqual(restored['state']['prompt'], 'Latest request')
        self.assertEqual(restored['state']['nodes']['run_result']['payload'], 'Latest answer')
        self.assertEqual(restored['history'][-1]['event']['result'], 'Latest answer')
        self.assertEqual(server.run_states, {})

    def test_background_completion_broadcast_carries_its_own_state_and_history(self):
        async def exercise():
            with patch.object(server.ws_manager, 'broadcast', new_callable=AsyncMock) as broadcast:
                for session, run, kind in [('alpha', 'a', 'PROMPT_START'), ('beta', 'b', 'PROMPT_START'), ('alpha', 'a', 'GENERATION_END')]:
                    response = await server.ingest_observation({'type': kind, 'session_id': session, 'run_id': run, 'prompt': session, 'result': session + ' result'})
                    self.assertEqual(response.status_code, 200)
                final = broadcast.call_args_list[-1].args[0]
                self.assertEqual(final['session_id'], 'alpha')
                self.assertEqual(final['state']['nodes']['run_result']['payload'], 'alpha result')
                self.assertEqual(final['history_step']['event']['run_id'], 'a')
        asyncio.run(exercise())

    def test_runtime_inventories_do_not_cross_session_boundaries(self):
        inventory = {'source': 'test', 'mcp': [{'name': 'alpha-server', 'tool_names': ['inspect']}], 'skills': [], 'native_tools': [], 'integrations': []}
        server.route_event({'type': 'RUNTIME_INVENTORY', 'session_id': 'alpha', 'inventory': inventory})
        self.send('alpha', 'a', prompt='Alpha request')
        self.send('beta', 'b', prompt='Beta request')
        self.assertIn(mcp_node_id('alpha-server'), self.detail('alpha')['state']['nodes'])
        self.assertNotIn(mcp_node_id('alpha-server'), self.detail('beta')['state']['nodes'])

    def test_websocket_events_advance_each_session_and_deliver_background_result(self):
        messages = []
        events = iter([
            {'type': 'PROMPT_START', 'session_id': 'alpha', 'run_id': 'a', 'prompt': 'Alpha'},
            {'type': 'PROMPT_START', 'session_id': 'beta', 'run_id': 'b', 'prompt': 'Beta'},
            {'type': 'GENERATION_END', 'session_id': 'alpha', 'run_id': 'a', 'result': 'Alpha answer'},
        ])

        class Socket:
            headers = {'host': '127.0.0.1:8766', 'sec-websocket-protocol': 'cad-xray, ' + server.AUTH_TOKEN}
            query_params = {}

            async def accept(self, **kwargs):
                pass

            async def send_json(self, message):
                messages.append(message)

            async def receive_text(self):
                event = next(events, None)
                if event is None:
                    raise server.WebSocketDisconnect()
                return json.dumps({'action': 'EVENT', 'event': event})

        asyncio.run(server.websocket_endpoint(Socket()))
        updates = [message for message in messages if message['type'] == 'STATE_UPDATE']
        self.assertEqual([message['state']['prompt'] for message in updates], ['Alpha', 'Beta', 'Alpha'])
        self.assertEqual(updates[-1]['state']['nodes']['run_result']['payload'], 'Alpha answer')
        self.assertEqual(self.detail('beta')['state']['orchestrator_phase'], 'running')

    def test_embedded_omp_and_external_omp_keep_separate_session_scope(self):
        from agent_observer.observer_config import load_settings

        root = Path(self.temp.name)
        (root / '.omp').mkdir()
        config = root / 'observer.project.json'
        data = {'schema_version': 1, 'project': {'root': str(root), 'agent_label': 'Embedded OMP'},
                'connection': {'module': 'project', 'enabled': True}, 'documents': [], 'tools': []}

        def select(module):
            data['connection']['module'] = module
            config.write_text(json.dumps(data), encoding='utf-8')
            server.SETTINGS.update(load_settings(config, root, environment={}))

        def begin(session, application='OMP'):
            return asyncio.run(server.ingest_observation({
                'type': 'PROMPT_START', 'session_id': session, 'run_id': session + '-run',
                'application': application, 'source': application.casefold(), 'capture_mode': 'extension',
                'prompt': 'Observe ' + application,
            }))

        with patch.dict(server.SETTINGS), patch.object(server, 'PROJECT_ROOT', root), patch('agent_observer.repository_observer.RepositoryObserver.discover_sessions', return_value=[]):
            select('project')
            self.assertEqual(begin('embedded-omp').status_code, 200)
            self.assertIn('embedded-omp', {row['session_id'] for row in server.discover_all_sessions()[0]})
            self.assertEqual(self.detail('embedded-omp')['config']['agent_label'], 'Embedded OMP')
            select('omp')
            self.assertEqual(begin('external-omp').status_code, 200)
            sessions = {row['session_id'] for row in server.discover_all_sessions()[0]}
            self.assertIn('omp:external-omp', sessions)
            self.assertNotIn('embedded-omp', sessions)
            self.assertEqual(begin('codex-external', 'Codex').status_code, 200)
            self.assertEqual(self.detail('codex-external')['config']['agent_runtime'], 'codex')
            self.assertEqual(self.detail('omp:external-omp')['config']['agent_runtime'], 'omp')
            select('codex')
            rows = {row['session_id'] for row in server.discover_all_sessions()[0]}
            self.assertEqual(rows, {'codex-external', 'omp:external-omp'})
            for session, result in [('codex-external', 'Codex result'), ('omp:external-omp', 'OMP result')]:
                application = 'OMP' if session.startswith('omp:') else 'Codex'
                run_id = ('external-omp' if application == 'OMP' else session) + '-run'
                response = asyncio.run(server.ingest_observation({
                    'type': 'GENERATION_END', 'application': application, 'session_id': session,
                    'run_id': run_id, 'result': result,
                }))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.detail(session)['state']['nodes']['run_result']['payload'], result)
            server.session_states.clear()
            self.assertEqual(self.detail('omp:external-omp')['state']['nodes']['run_result']['payload'], 'OMP result')
            self.assertEqual(self.detail('codex-external')['state']['nodes']['run_result']['payload'], 'Codex result')
            select('project')
            restored = {row['session_id'] for row in server.discover_all_sessions()[0]}
            self.assertIn('embedded-omp', restored)
            self.assertNotIn('omp:external-omp', restored)

    def test_session_observation_without_run_stays_idle_and_follows_only_its_own_session(self):
        event = {'type': 'SESSION_OBSERVATION', 'session_id': 'alpha',
                 'observation': {'session': {'id': 'wrong'}, 'context': {'tokens': 900, 'window': 1000}}}
        snapshot, _ = server.route_event(event)
        self.assertEqual(snapshot['session_observation']['session']['id'], 'alpha')
        self.assertEqual(snapshot['orchestrator_phase'], 'idle')
        self.assertEqual(snapshot['history_count'], 0)
        self.assertIsNone(snapshot['run_id'])
        self.assertEqual(self.send('alpha', 'a', prompt='Alpha')['session_observation']['context']['percent'], 90)
        beta = self.send('beta', 'b', prompt='Beta')
        self.assertEqual(beta['session_observation'], {})
        self.send('alpha', 'a', 'GENERATION_END', result='Done')
        updated, _ = server.route_event({'type': 'SESSION_OBSERVATION', 'session_id': 'alpha',
            'observation': {'compaction': {'status': 'completed'}}})
        self.assertEqual(updated['orchestrator_phase'], 'complete')
        self.assertIsNone(updated['session_observation']['context']['tokens'])
        self.assertIsNone(updated['session_observation']['context']['percent'])
        self.assertIsNone(updated['latest_step']['event'].get('observation'))
        self.assertEqual(next(run for run in server.saved_runs if run['session_id'] == 'alpha')['session_observation']['context']['percent'], None)

    def test_compaction_metadata_restores_without_regressing_to_the_last_run_snapshot(self):
        self.send('alpha', 'a', prompt='Alpha')
        server.route_event({'type': 'SESSION_OBSERVATION', 'session_id': 'alpha',
            'observation': {'context': {'tokens': 950, 'window': 1000}}})
        self.send('alpha', 'a', 'GENERATION_END', result='Done')
        server.route_event({'type': 'SESSION_OBSERVATION', 'session_id': 'alpha',
            'observation': {'compaction': {'status': 'completed'}, 'context': {'tokens': 100}}})
        server.session_states.clear()
        restored = self.detail('alpha')['state']
        self.assertEqual(restored['session_observation']['context']['percent'], 10)
        self.assertEqual(restored['orchestrator_phase'], 'complete')
