"""Consumer-visible journal isolation, truthful lifecycle and safe resource inventories."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_observer.omp_observer import OmpObserver
from agent_observer.omp_inventory import configured_inventory
from agent_observer.capabilities import validate_inventory_schema, capability_nodes
from agent_observer.observer_config import load_settings, resource_catalog
from agent_observer.connections import ConnectionModules


class OmpObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'omp'
        self.events = []
        self.observer = OmpObserver(self.project, self.root / 'state', self.events.append, omp_home=self.home)

    def session(self, name, cwd=None, directories=None, nested=False):
        path = self.home / 'sessions' / 'cwd' / ('children/' if nested else '') / (name + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        self.append(path, {'type': 'title', 'title': name}, {'type': 'session', 'version': 3, 'id': name,
                          'cwd': str(cwd or self.project), 'additionalDirectories': directories or [], 'timestamp': '2000-01-01T00:00:00Z'})
        return path

    @staticmethod
    def append(path, *records):
        with path.open('a') as stream:
            for record in records:
                stream.write(json.dumps(record) + '\n')

    def test_workspace_headers_scope_sessions_without_importing_old_or_child_messages(self):
        main = self.session('main', cwd='/tmp', directories=[str(self.project)])
        self.session('foreign', cwd=self.root / 'other')
        self.session('child', nested=True)
        self.append(main, {'type': 'message', 'message': {'role': 'user', 'content': 'OLD PRIVATE HISTORY'}})
        self.assertEqual([row['session_id'] for row in self.observer.sessions()], ['omp:main'])
        self.observer.poll_once()
        self.assertEqual([event['type'] for event in self.events], ['SESSION_OBSERVATION'])
        self.events.clear()
        self.append(main, {'type': 'custom', 'customType': 'tool_execution_start', 'data': {'toolName': 'read', 'toolCallId': 'one', 'args': {'path': 'README.md'}}})
        self.observer.poll_once()
        self.assertEqual([event['type'] for event in self.events], ['PROMPT_START', 'TOOL_CALL_START'])
        self.assertTrue(self.events[0]['observed_mid_turn'])
        self.assertEqual(self.events[0]['prompt'], '')
        self.assertNotIn('OLD PRIVATE HISTORY', json.dumps(self.events))

    def test_partial_records_pair_results_deduplicate_and_never_publish_thinking(self):
        main = self.session('main')
        self.observer.poll_once()
        self.events.clear()
        start = {'type': 'custom', 'customType': 'tool_execution_start', 'data': {'toolName': 'bash', 'toolCallId': 'one', 'args': {'command': 'printf test'}}}
        line = json.dumps(start)
        with main.open('a') as stream:
            stream.write(line[:30])
        self.observer.poll_once()
        self.assertEqual(self.events, [])
        with main.open('a') as stream:
            stream.write(line[30:] + '\n')
        self.observer.poll_once()
        self.append(main, {'type': 'message', 'message': {'role': 'toolResult', 'toolName': 'bash', 'toolCallId': 'one', 'content': [{'type': 'text', 'text': 'test'}], 'isError': False}},
                    {'type': 'message', 'message': {'role': 'assistant', 'stopReason': 'toolUse', 'content': [{'type': 'text', 'text': 'Working'}, {'type': 'thinking', 'thinking': 'PRIVATE'}]}},
                    {'type': 'message', 'message': {'role': 'assistant', 'stopReason': 'stop', 'content': [{'type': 'thinking', 'thinking': 'PRIVATE'}, {'type': 'text', 'text': 'Done'}]}})
        self.observer.poll_once()
        self.observer.poll_once()
        self.assertEqual([event['type'] for event in self.events], ['PROMPT_START', 'TOOL_CALL_START', 'TOOL_CALL_END', 'GENERATION_END'])
        self.assertEqual(self.events[2]['command'], 'printf test')
        self.assertEqual(self.events[2]['call_id'], 'one')
        self.assertEqual(self.events[-1]['result'], 'Done')
        self.assertNotIn('PRIVATE', json.dumps(self.events))
        self.assertEqual(len({event['event_id'] for event in self.events}), 4)

    def test_attaching_at_partial_tail_does_not_decode_the_suffix_as_a_new_record(self):
        main = self.session('main')
        with main.open('a') as stream:
            stream.write('{"type":"message","message":')
        self.observer.poll_once()
        self.events.clear()
        with main.open('a') as stream:
            stream.write('{"role":"user","content":"old partial"}}\n')
        self.observer.poll_once()
        self.assertEqual(self.events, [])
        self.append(main, {'type': 'message', 'message': {'role': 'user', 'content': 'New request'}})
        self.observer.poll_once()
        self.assertEqual(self.events[0]['prompt'], 'New request')

    def test_commentary_and_private_only_records_do_not_complete_a_running_turn(self):
        main = self.session('main')
        self.observer.poll_once()
        self.events.clear()
        self.append(main, {'type': 'message', 'message': {'role': 'user', 'content': 'Inspect'}},
                    {'type': 'message', 'message': {'role': 'assistant', 'channel': 'commentary', 'stopReason': 'stop', 'content': [{'type': 'text', 'text': 'Still working'}]}},
                    {'type': 'custom', 'customType': 'private-reasoning', 'data': {'text': 'PRIVATE'}})
        self.observer.poll_once()
        self.assertEqual([event['type'] for event in self.events], ['PROMPT_START'])
        self.assertEqual(self.observer.sessions()[0]['status'], 'running')

    def test_metadata_baseline_and_compaction_never_import_dialogue_or_cumulative_spend(self):
        main = self.session('metadata')
        self.observer._model_metadata['provider/test'] = {'contextWindow': 1000}
        self.append(main, {'type': 'message', 'timestamp': '2026-01-01T00:00:00Z',
                          'message': {'role': 'assistant', 'provider': 'provider', 'model': 'test',
                                      'usage': {'input': 100, 'cacheRead': 700, 'output': 100},
                                      'content': [{'type': 'thinking', 'thinking': 'PRIVATE REASONING'}]}},
                         {'type': 'message', 'message': {'role': 'user', 'content': 'PRIVATE REQUEST'}})
        self.observer.poll_once()
        snapshot = self.events[-1]['observation']
        self.assertEqual(snapshot['context']['tokens'], 900)
        self.assertEqual(snapshot['context']['percent'], 90)
        self.assertNotIn('PRIVATE', json.dumps(self.events))
        self.assertTrue(all(event['type'] == 'SESSION_OBSERVATION' for event in self.events))
        self.append(main, {'type': 'compaction', 'timestamp': '2026-01-01T00:01:00Z',
                          'tokensBefore': 900, 'tokensAfter': 200, 'summary': 'PRIVATE SUMMARY'})
        self.observer.poll_once()
        compacted = self.events[-1]['observation']
        self.assertEqual(compacted['context']['tokens'], 200)
        self.assertEqual(compacted['compaction']['status'], 'completed')
        self.append(main, {'type': 'compaction', 'timestamp': '2026-01-01T00:02:00Z', 'tokensBefore': 950})
        self.observer.poll_once()
        self.assertIsNone(self.events[-1]['observation']['context']['tokens'])
        self.assertNotIn('PRIVATE', json.dumps(self.events))


class OmpInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'omp'
        self.home.mkdir()
        manager = patch.dict(os.environ, {'PI_CODING_AGENT_DIR': str(self.home)})
        manager.start(); self.addCleanup(manager.stop)

    def test_memory_and_command_metadata_are_not_calls_or_credentials(self):
        (self.home / 'config.yml').write_text('memory:\n  backend: hindsight\nhindsight:\n  apiUrl: http://127.0.0.1:18789\n  apiToken: PRIVATE_SECRET\nautolearn:\n  enabled: true\n')
        inventory = validate_inventory_schema({'source': 'omp-config', **configured_inventory(self.project)})
        memories = inventory['memory_systems']
        self.assertEqual([item['id'] for item in memories[:4] if item['enabled']], ['omp-hindsight'])
        self.assertTrue(all(item['availability'] == 'unavailable' for item in memories[1:4]))
        self.assertNotIn('PRIVATE_SECRET', json.dumps(inventory))
        nodes = capability_nodes(inventory)
        self.assertTrue(any(node['kind'] == 'command' and node['label'] == '/memory' for node in nodes))
        self.assertTrue(all(node['status'] == 'idle' for node in nodes))
        self.assertEqual(next(item for item in inventory['native_tools'] if item['name'] == 'memory_edit')['availability'], 'unavailable')

    def test_explicit_omp_choice_loads_omp_resources_without_codex_state(self):
        config = self.project / 'observer.json'
        config.write_text(json.dumps({'project': {'root': str(self.project)}, 'connection': {'module': 'omp', 'repository_mode': 'working_repository', 'enabled': True}}))
        settings = load_settings(config, environment={})
        self.assertEqual(settings['agent_runtime'], 'omp')
        self.assertEqual(settings['agent_label'], 'OMP')
        with patch('pathlib.Path.home', return_value=self.root):
            catalog = resource_catalog(settings)
            modules = ConnectionModules(settings).inspect({'project': str(self.project)})
        self.assertEqual({item['id'] for item in modules['modules']}, {'project', 'codex', 'omp'})
        self.assertTrue(any(node['kind'] == 'memory' and node.get('enabled') is False for node in catalog['nodes']))
        self.assertTrue(any(node['kind'] == 'command' and node['label'] == 'omp ps' for node in catalog['nodes']))
        self.assertEqual(catalog['capability_inventory']['source'], 'omp-config')

    def test_runtime_memory_projection_drops_private_token_file_and_rejects_wrong_types(self):
        item = {'id': 'one', 'name': 'Memory', 'provider': 'custom', 'endpoint': '', 'enabled': False, 'token_file': '/private/token'}
        result = validate_inventory_schema({'memory_systems': [item]})
        self.assertNotIn('token_file', result['memory_systems'][0])
        with self.assertRaises(ValueError):
            validate_inventory_schema({'memory_systems': [{**item, 'enabled': 'true'}]})


if __name__ == '__main__':
    unittest.main()
