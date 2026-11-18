"""Session navigation boundaries and passive replay through the real graph projection."""
import asyncio
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from types import SimpleNamespace

from agent_observer.installation import InstallationError
from agent_observer.observer_config import resource_catalog
from agent_observer.results import ResultStore
from agent_observer.project_settings import ProjectSettings
from agent_observer.session_deletion import tombstone_session
from agent_observer.session_navigation import clear_hydration_cache, hydrate_historical_codex_session, open_project_session
from test_connection_analysis import insert_thread
import test_session_projects as project_fixtures
from test_sessions import server


class SessionNavigationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = project_fixtures.SessionProjectsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(clear_hydration_cache)
        clear_hydration_cache()
        self.root = self.fixture.current
        self.home = self.fixture.home
        (self.home / 'sessions').mkdir(parents=True)
        (self.root / 'docs').mkdir()
        (self.root / 'docs/Reference.md').write_text('# Reference\nRecorded source\n')
        self.settings = self.fixture.modules.settings
        self.catalog = resource_catalog(self.settings)
        for manager in (
            patch.dict(server.SETTINGS, self.settings, clear=True),
            patch.object(server, 'PROJECT_ROOT', self.root),
            patch.object(server, 'RESULTS', ResultStore(self.root, self.root / '.captures')),
            patch.object(server, 'saved_runs', []),
            patch.object(self.fixture.modules.installations, '_inspect_connection', return_value={'connected':False}),
            patch.object(server, 'session_inventories', {}),
            patch.object(server, '_enqueue_history', side_effect=AssertionError('Read-only replay wrote a journal')),
            patch.object(server.EFFICIENCY, 'observe', side_effect=AssertionError('Replay recounted live metrics')),
        ):
            manager.start()
            self.addCleanup(manager.stop)

    def factory(self):
        return server.GraphState(persist=False, catalog=self.catalog)

    def rollout(self, session='recorded', answer='Recorded answer'):
        path = self.home / 'sessions' / f'{session}.jsonl'
        records = [
            {'type':'session_meta', 'payload':{'id':session, 'cwd':str(self.root), 'source':'cli'}},
            {'type':'event_msg', 'payload':{'type':'user_message', 'message':'Read the reference'}},
            {'type':'response_item', 'payload':{'type':'function_call', 'name':'read', 'call_id':'read-1', 'arguments':json.dumps({'path':'docs/Reference.md'})}},
            {'type':'response_item', 'payload':{'type':'function_call_output', 'call_id':'read-1', 'output':'Recorded source'}},
            {'type':'event_msg', 'payload':{'type':'task_complete', 'last_agent_message':answer}},
        ]
        for index, record in enumerate(records):
            record['timestamp'] = f'2026-11-01T10:00:{index:02d}Z'
        path.write_text(''.join(json.dumps(record)+'\n' for record in records))
        insert_thread(self.fixture.database, session, path, self.root)
        return path

    def hydrate(self, session='recorded'):
        return hydrate_historical_codex_session(session, self.root, codex_home=self.home,
            settings=self.settings, state_factory=self.factory, cache_context=self.catalog)

    def test_passive_replay_preserves_result_calls_catalog_and_original_files(self):
        answer = '**Recorded answer**\n' + 'Complete source response. ' * 150
        path = self.rollout(answer=answer)
        original = {file:hashlib.sha256(file.read_bytes()).digest() for file in (path, self.fixture.database, self.fixture.config)}
        files = set(self.root.rglob('*'))
        result = self.hydrate()
        self.assertEqual(result['state']['prompt'], 'Read the reference')
        self.assertEqual(result['state']['nodes']['run_result']['result']['text'], answer)
        self.assertEqual(result['config']['project_root'], str(self.root))
        calls = [step['event'] for step in result['history'] if step['event']['type']=='TOOL_CALL_END']
        self.assertEqual([(event['call_id'], event['result']) for event in calls], [('read-1', 'Recorded source')])
        self.assertEqual(result['history'][-1]['timestamp'], '2026-11-01T10:00:04Z')
        self.assertEqual(server.saved_runs, [])
        self.assertEqual(set(self.root.rglob('*')), files)
        for file, digest in original.items():
            self.assertEqual(hashlib.sha256(file.read_bytes()).digest(), digest)

    def test_replay_cache_tracks_changed_source_and_catalog(self):
        path = self.rollout(answer='Before')
        self.assertEqual(self.hydrate()['state']['nodes']['run_result']['result']['text'], 'Before')
        with path.open('a') as stream:
            for record in [
                {'type':'event_msg', 'timestamp':'2026-11-01T10:01:00Z', 'payload':{'type':'user_message','message':'Next request'}},
                {'type':'event_msg', 'timestamp':'2026-11-01T10:01:01Z', 'payload':{'type':'task_complete','last_agent_message':'After'}},
            ]:
                stream.write(json.dumps(record)+'\n')
        self.catalog = {**self.catalog, 'project_title':'Updated project'}
        result = self.hydrate()
        self.assertEqual(result['state']['prompt'], 'Next request')
        self.assertEqual(result['state']['nodes']['run_result']['result']['text'], 'After')
        self.assertEqual(result['config']['project_title'], 'Updated project')

    def test_tombstoned_replay_and_wrong_project_source_are_not_exposed(self):
        self.rollout()
        tombstone_session(self.settings, self.root, 'working_repository', 'recorded')
        self.assertIsNone(self.hydrate())
        self.assertIsNone(hydrate_historical_codex_session('recorded', self.fixture.external,
            codex_home=self.home, settings=self.settings, state_factory=self.factory))

    def test_warm_and_cold_navigation_resolve_only_the_requested_project(self):
        target = self.fixture.external
        path = self.home / 'sessions/external.jsonl'
        path.write_text('')
        insert_thread(self.fixture.database, 'external', path, target)
        body = {'project':str(target), 'repository_mode':'working_repository', 'session_id':'external'}
        manager = self.fixture.modules
        with patch.object(manager.installations, '_inspect_connection', return_value={'connected':True,'dashboard_compatible':True}), patch.object(manager, 'analyze', side_effect=AssertionError('Warm graph was scanned again')):
            result = open_project_session(body, manager)
            self.assertEqual(result['project'], str(target))
            self.assertEqual(parse_qs(urlparse(result['url']).query), {'session':['external']})
        analysis = {'ready':True,'analysis_id':'target-analysis','revision':'target-revision'}
        with patch.object(manager.installations, '_inspect_connection', return_value={'connected':False}), patch.object(manager, 'analyze', return_value=analysis), patch.object(manager, 'generate', return_value={'url':'http://127.0.0.1:43212/'}):
            result = open_project_session(body, manager)
            self.assertEqual(result['url'], 'http://127.0.0.1:43212/?session=external')
            self.assertEqual(result['project'], str(target))

    def test_navigation_rejects_wrong_folder_role_session_and_service_identity(self):
        manager = self.fixture.modules
        path = self.home / 'sessions/external.jsonl'
        path.write_text('')
        insert_thread(self.fixture.database, 'external', path, self.fixture.external)
        body = {'project':str(self.fixture.external), 'repository_mode':'working_repository', 'session_id':'external'}
        for update, status in [({'project':str(self.fixture.other)},404), ({'repository_mode':'agent_system'},400), ({'session_id':'unrelated'},404)]:
            with self.subTest(update=update), self.assertRaises(InstallationError) as error:
                open_project_session({**body,**update}, manager)
            self.assertEqual(error.exception.status,status)
        for state in [{'connected':False,'status':'occupied'}, {'connected':True,'dashboard_compatible':False}]:
            with patch.object(manager.installations, '_inspect_connection', return_value=state), self.assertRaises(InstallationError) as error:
                open_project_session(body, manager)
            self.assertEqual(error.exception.status,409)

    def test_api_deletion_blocks_ingestion_but_preserves_other_role_and_live_session(self):
        first, second = self.factory(), self.factory()
        for state, session in ((first,'alpha'),(second,'beta')):
            state.session_id=session
            state.process_event({'type':'PROMPT_START','session_id':session,'run_id':'run-'+session,'prompt':session,'application':'Codex','source':'codex'})
        gateway=ProjectSettings(self.fixture.modules)
        self.addCleanup(gateway.shutdown)
        managers=[
            patch.object(server,'CONNECTIONS',self.fixture.modules),patch.object(server,'PROJECT_SETTINGS',gateway),
            patch.object(server,'graph_state',first),patch.object(server,'session_states',{'alpha':first,'beta':second}),
            patch.object(server,'run_states',{'run-alpha':first,'run-beta':second}),patch.object(server,'_session_tombstones',{}),
            patch.object(server,'saved_runs',[{'run_id':'remove','session_id':'alpha','source':'codex'},
                {'run_id':'protected-role','session_id':'alpha','source':'agent'},{'run_id':'keep','session_id':'beta','source':'codex'}]),
        ]
        for manager in managers:
            manager.start();self.addCleanup(manager.stop)
        async def body():
            return {'project':str(self.root),'repository_mode':'working_repository','session_id':'alpha','confirmed':True}
        response=asyncio.run(server.delete_project_session_endpoint(SimpleNamespace(json=body)))
        self.assertEqual(response.status_code,200)
        self.assertEqual(set(server.session_states),{'beta'})
        self.assertIs(server.graph_state,second)
        self.assertEqual({run['run_id'] for run in server.saved_runs},{'protected-role','keep'})
        ignored=asyncio.run(server.ingest_observation({'type':'PROMPT_START','session_id':'alpha','run_id':'again','application':'Codex'}))
        self.assertEqual(json.loads(ignored.body)['status'],'ignored')
        self.assertEqual(asyncio.run(server.get_session_detail('alpha')).status_code,404)
        self.assertNotIn('again',server.run_states)

    def test_foreign_api_deletion_does_not_evict_same_id_in_current_graph(self):
        state=self.factory();state.session_id='shared'
        state.process_event({'type':'PROMPT_START','session_id':'shared','run_id':'current-shared','prompt':'Current scope','application':'Codex'})
        foreign=self.fixture.external
        rollout=self.home/'foreign.jsonl';rollout.write_text('')
        insert_thread(self.fixture.database,'shared',rollout,foreign)
        gateway=ProjectSettings(self.fixture.modules)
        self.addCleanup(gateway.shutdown)
        for manager in [
            patch.object(server,'CONNECTIONS',self.fixture.modules),patch.object(server,'PROJECT_SETTINGS',gateway),
            patch.object(server,'graph_state',state),patch.object(server,'session_states',{'shared':state}),
            patch.object(server,'run_states',{'current-shared':state}),patch.object(server,'_session_tombstones',{}),
        ]:
            manager.start();self.addCleanup(manager.stop)
        async def body():
            return {'project':str(foreign),'repository_mode':'working_repository','session_id':'shared','confirmed':True}
        response=asyncio.run(server.delete_project_session_endpoint(SimpleNamespace(json=body)))
        self.assertEqual(response.status_code,200)
        self.assertIs(server.session_states['shared'],state)
        self.assertIs(server.graph_state,state)
        self.assertEqual(asyncio.run(server.get_session_detail('shared')).status_code,200)
