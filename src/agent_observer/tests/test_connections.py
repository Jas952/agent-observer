"""Workspace isolation, module cutover and configuration ownership."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_observer import cli
from agent_observer.capabilities import discover_capabilities
from agent_observer.connections import ConnectionModules
from agent_observer.installation import InstallationError
from agent_observer.observer_config import load_settings


class ConnectionsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.config = self.project / 'private/observer.project.json'
        self.config.parent.mkdir()
        self.data = {'schema_version': 1, 'project': {'root': str(self.project), 'name': 'Project', 'agent_label': 'Project agent'}, 'port': 38123}
        self.config.write_text(json.dumps(self.data))
        registry = patch('agent_observer.cli.REGISTRY', self.root / 'registry.json')
        registry.start();self.addCleanup(registry.stop)
        home = patch('pathlib.Path.home', return_value=self.home)
        home.start();self.addCleanup(home.stop)
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.home / '.codex')})
        env.start();self.addCleanup(env.stop)
        codex_inventory = patch('agent_observer.capabilities._codex_cli_inventory', return_value=None)
        codex_inventory.start();self.addCleanup(codex_inventory.stop)
        self.settings = load_settings(self.config, environment={})
        self.modules = ConnectionModules(self.settings)
        self.addCleanup(self.modules.shutdown)

    def test_cad_profile_does_not_inherit_omp_mcp_or_skills(self):
        (self.project / 'src/CADAgentUI').mkdir(parents=True)
        (self.project / '.codex').mkdir()
        global_mcp = self.home / '.omp/agent/mcp.json'
        global_mcp.parent.mkdir(parents=True)
        global_mcp.write_text(json.dumps({'mcpServers': {'unrelated-omp': {}}}))
        local_omp = self.project / '.omp/mcp.json'
        local_omp.parent.mkdir()
        local_omp.write_text(json.dumps({'mcpServers': {'developer-omp-only': {}}}))
        (self.project / '.mcp.json').write_text(json.dumps({'mcpServers': {'project-only': {}}}))
        skills = self.project / 'docs/skills'
        skills.mkdir(parents=True)
        (skills / 'model.md').write_text('---\nname: CAD modeling\ndescription: Model the selected part\n---\nInstructions\n')
        installed_skill = self.project / '.agents/skills/geometry/SKILL.md'
        installed_skill.parent.mkdir(parents=True)
        installed_skill.write_text('---\nname: Geometry plugin\ndescription: Installed agent skill\n---\nInstructions\n')
        self.data['documents'] = [{'path': 'docs', 'include': ['**/*.md']}]
        self.config.write_text(json.dumps(self.data))
        effective = load_settings(self.config, environment={})
        self.assertEqual(effective['agent_runtime'], 'cad')
        inventory = discover_capabilities(self.project, self.config)
        self.assertEqual([item['name'] for item in inventory['mcp']], ['project-only'])
        self.assertEqual([item['name'] for item in inventory['skills']], ['Geometry plugin'])
        self.assertEqual(inventory['native_tools'], [])
        from agent_observer.capabilities import save_capabilities_snapshot
        from agent_observer.observer_config import resource_catalog
        save_capabilities_snapshot(self.config.parent, {'source': 'codex-config', 'mcp': [{'name': 'stale-codex-server'}]})
        catalog = resource_catalog(effective)
        self.assertEqual([item['name'] for item in catalog['capability_inventory']['mcp']], ['project-only'])
        self.assertEqual([item['name'] for item in catalog['capability_inventory']['skills']], ['Geometry plugin'])
        self.assertIn('docs/skills/model.md', [node.get('path') for node in catalog['nodes'] if node['kind'] == 'document'])

    def test_obsolete_static_cache_cannot_restore_documentation_as_plugins(self):
        from agent_observer.capabilities import load_capabilities_snapshot, save_capabilities_snapshot
        from agent_observer.observer_config import resource_catalog
        (self.project / 'src/CADAgentUI').mkdir(parents=True)
        document = self.project / 'docs/skills/model.md'
        document.parent.mkdir(parents=True)
        document.write_text('---\nname: CAD modeling\n---\nProject reference\n')
        self.data['documents'] = [{'path': 'docs', 'include': ['**/*.md']}]
        self.config.write_text(json.dumps(self.data))
        snapshot = self.config.parent / 'observer.capabilities.json'
        snapshot.write_text(json.dumps({'source': 'project-config', 'skills': [{
            'name': 'CAD modeling', 'path': 'docs/skills/model.md', 'source': 'project-cad'}]}))
        catalog = resource_catalog(load_settings(self.config, environment={}))
        self.assertEqual(catalog['capability_inventory']['skills'], [])
        self.assertFalse(any(node['kind'] == 'skill' for node in catalog['nodes']))
        self.assertIn('docs/skills/model.md', [node.get('path') for node in catalog['nodes'] if node['kind'] == 'document'])
        save_capabilities_snapshot(self.config.parent, {'source': 'project-config', 'mcp': [{'name': 'project-plugin'}]})
        self.assertEqual([item['name'] for item in load_capabilities_snapshot(self.config.parent)['mcp']], ['project-plugin'])

    def test_codex_discovers_its_plugins_without_importing_omp_or_project_documents(self):
        omp = self.home / '.omp/agent/mcp.json'
        omp.parent.mkdir(parents=True)
        omp.write_text(json.dumps({'mcpServers': {'headroom': {}}}))
        codex = self.home / '.codex/config.toml'
        codex.parent.mkdir()
        codex.write_text('[mcp_servers.codegraph]\ncommand = "codegraph"\n[mcp_servers.browser]\nenabled = true\n')
        local_codex = self.project / '.codex/config.toml'
        local_codex.parent.mkdir()
        local_codex.write_text('[mcp_servers.browser]\nenabled = false\n')
        for path, name in [(self.home / '.codex/skills/review/SKILL.md', 'Review plugin'),
                           (self.home / '.omp/agent/skills/omp-only/SKILL.md', 'OMP-only plugin'),
                           (self.project / 'docs/skills/reference.md', 'Project reference')]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'---\nname: {name}\ndescription: Fixture\n---\nInstructions\n')
        inventory = discover_capabilities(self.project, agent='codex')
        self.assertEqual({item['name']: item['connection'] for item in inventory['mcp']},
                         {'browser': 'disabled', 'codegraph': 'configured'})
        self.assertEqual([item['name'] for item in inventory['skills']], ['Review plugin'])
        self.assertFalse(any(item['connection'] == 'connected' for item in inventory['mcp']))
        cad = discover_capabilities(self.project, agent='cad')
        self.assertEqual(cad['mcp'], [])
        self.assertEqual(cad['skills'], [])

    def test_codex_effective_inventory_includes_builtins_and_overrides_file_only_registrations(self):
        config = self.home / '.codex/config.toml'
        config.parent.mkdir()
        config.write_text('[mcp_servers.browser]\nenabled = false\n[mcp_servers.file_only]\ncommand = "stale"\n')
        effective = [
            {'name': 'browser', 'enabled': True, 'transport': {'env': {'TOKEN': 'PRIVATE-MCP-TOKEN'}}},
            {'name': 'cua_repl', 'enabled': True},
            {'name': 'codex_app', 'enabled': False},
        ]
        with patch('agent_observer.capabilities._codex_cli_inventory', return_value=effective):
            inventory = discover_capabilities(self.project, agent='codex')
        self.assertEqual({item['name']: item['connection'] for item in inventory['mcp']},
                         {'browser': 'configured', 'cua_repl': 'configured', 'codex_app': 'disabled'})
        self.assertTrue(all(item['tool_names'] == [] for item in inventory['mcp']))
        self.assertNotIn('PRIVATE-MCP-TOKEN', json.dumps(inventory))
        self.assertFalse(any(item['name'] == 'rtk' for item in inventory['mcp']))

    def test_saved_folders_are_normalized_and_pin_survives_a_new_manager(self):
        first = self.modules.remember({'project': str(self.project), 'pinned': True})
        second = self.modules.remember({'project': str(self.project / '..' / 'project')})
        self.assertEqual(first['id'], second['id'])
        rows = ConnectionModules(self.settings).workspaces()
        self.assertEqual([(row['path'], row['pinned']) for row in rows], [(str(self.project), True)])
        self.assertEqual(cli.registry()['installations'], [])

    def test_stale_custom_config_revision_cannot_overwrite_an_edit(self):
        original = self.modules.inspect({'project': str(self.project)})['workspace']['revision']
        self.data['documents'] = [{'path': 'my-docs', 'include': ['**/*.txt']}]
        changed = json.dumps(self.data)
        self.config.write_text(changed)
        with self.assertRaises(InstallationError) as failure:
            self.modules.connect({'project': str(self.project), 'module': 'project', 'revision': original})
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.config.read_text(), changed)

    def test_module_cutover_preserves_models_auth_memory_and_project_identity(self):
        codex = self.home / '.codex'
        codex.mkdir()
        with sqlite3.connect(codex / 'state_5.sqlite') as connection:
            connection.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,cwd TEXT,source TEXT,updated_at INTEGER)')
        model_config = codex / 'config.toml'
        model_config.write_text('model = "existing-model"\nmodel_provider = "existing-provider"\n')
        self.data['memory_systems'] = []
        self.data['project']['agent_label'] = 'Original agent'
        self.config.write_text(json.dumps(self.data))
        before = model_config.read_bytes()
        inspected = self.modules.inspect({'project': str(self.project)})
        result = self.modules.connect({'project': str(self.project), 'module': 'codex', 'revision': inspected['workspace']['revision']})
        self.assertEqual(result['workspace']['module'], 'codex')
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved['project'], self.data['project'])
        self.assertEqual(saved['memory_systems'], [])
        self.assertEqual(model_config.read_bytes(), before)
        self.assertFalse((self.project / '.codex/hooks.json').exists())
        self.modules.disconnect({'project': str(self.project), 'revision': result['workspace']['revision']})
        self.assertFalse(json.loads(self.config.read_text())['connection']['enabled'])
        self.assertEqual(model_config.read_bytes(), before)

    def test_demo_and_codex_receipts_do_not_claim_project_agent_activity(self):
        self.modules.observe({'application': 'Codex', 'type': 'PROMPT_START'})
        self.modules.observe({'source': 'demo', 'type': 'PROMPT_START'})
        self.assertIsNone(self.modules.last_project_event)
        self.modules.observe({'application': 'Custom agent', 'source': 'project-sdk', 'type': 'TOOL_CALL_END'})
        result = self.modules.status()
        project = next(item for item in result['modules'] if item['id'] == 'project')
        self.assertEqual(project['state'], 'receiving')
        self.assertEqual(project['last_event']['application'], 'Custom agent')

    def test_module_changes_preserve_live_port_auth_and_history_overrides(self):
        actual = {'port': 38234, 'token_file': self.root / 'private-token',
                  'history_file': self.root / 'private-history.json', 'storage': self.root / 'private-storage'}
        self.settings.update(actual)
        revision = self.modules.inspect({'project': str(self.project)})['workspace']['revision']
        result = self.modules.connect({'project': str(self.project), 'module': 'project', 'revision': revision})
        self.assertEqual({key: self.settings[key] for key in actual}, actual)
        self.assertEqual(result['workspace']['port'], 38234)
        self.assertEqual(result['url'], 'http://127.0.0.1:38234')
        self.modules.disconnect({'project': str(self.project), 'revision': result['workspace']['revision']})
        self.assertEqual({key: self.settings[key] for key in actual}, actual)

    def test_settings_read_and_save_preserve_live_listener_and_history(self):
        from agent_observer.observer_config import load_current_block_settings, save_block_settings
        actual = {'port': 38234, 'token_file': self.root / 'private-token',
                  'history_file': self.root / 'private-history.json', 'storage': self.root / 'private-storage'}
        self.settings.update(actual)
        self.data['commands'] = {'hidden': ['read']}
        self.config.write_text(json.dumps(self.data))
        current = load_current_block_settings(self.settings)
        self.assertEqual(current['hidden_commands'], ['read'])
        self.assertEqual({key: self.settings[key] for key in actual}, actual)
        saved = save_block_settings(self.settings, {'revision': current['revision'], 'block_layouts': {'commands': 'grid'}})
        self.assertEqual(saved['block_layouts']['commands'], 'grid')
        self.assertEqual(self.settings['block_layouts']['commands'], 'grid')
        self.assertEqual({key: self.settings[key] for key in actual}, actual)

    def test_uninstalled_folder_does_not_offer_a_connected_module(self):
        folder = self.root / 'not-installed'
        folder.mkdir()
        inspected = self.modules.inspect({'project': str(folder)})
        self.assertFalse(inspected['workspace']['enabled'])
        self.assertFalse(inspected['workspace']['installed'])
        with self.assertRaises(InstallationError) as failure:
            self.modules.disconnect({'project': str(folder), 'revision': inspected['workspace']['revision']})
        self.assertEqual(failure.exception.status, 404)
        self.assertFalse((folder / '.agent-observer').exists())

    def test_usage_does_not_charge_response_and_turn_and_legacy_totals_together(self):
        from agent_observer.codex_observer import CodexObserver
        events = []
        observer = CodexObserver(self.project, self.root / 'capture', events.append, codex_home=self.home / '.codex')
        thread = {'id': 'thread', 'source': 'exec', 'originator': 'codex_exec'}
        records = [
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn'}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 'turn', 'item': {'type': 'UserMessage', 'content': [{'text': 'Read the file'}]}}},
        ]
        for response, usage, total in (
                ('one', {'input_tokens': 10, 'output_tokens': 5}, {'input_tokens': 10, 'output_tokens': 5}),
                ('two', {'input_tokens': 15, 'output_tokens': 7}, {'input_tokens': 25, 'output_tokens': 12})):
            report = {'type': 'token_usage_record', 'payload': {'turn_id': 'turn', 'response_id': response, 'usage': usage, 'turn_token_usage': total}}
            records.extend([report, report, {'type': 'event_msg', 'payload': {'type': 'token_count', 'turn_id': 'turn', 'info': {'total_token_usage': total, 'last_token_usage': usage}}}])
        for index, record in enumerate(records):
            observer._process_record(thread, record, str(index))
        reports = [event for event in events if event['type'] == 'USAGE_UPDATE']
        self.assertEqual(sum(event['input_tokens'] for event in reports), 25)
        self.assertEqual(sum(event['output_tokens'] for event in reports), 12)
        self.assertEqual({event['model_call_id'] for event in reports}, {'one', 'two'})

    def test_private_analysis_and_compaction_do_not_enter_public_activity(self):
        from agent_observer.codex_observer import CodexObserver
        events = []
        observer = CodexObserver(self.project, self.root / 'capture', events.append, codex_home=self.home / '.codex')
        thread = {'id': 'thread', 'source': 'exec', 'originator': 'codex_exec'}
        records = [
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 'turn', 'item': {'type': 'AgentMessage', 'phase': 'analysis', 'content': [{'text': 'PRIVATE-ANALYSIS-MARKER'}]}}},
            {'type': 'compacted', 'payload': {'message': 'PRIVATE-COMPACTION-MARKER'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer', 'content': [{'text': 'PRIVATE-INSTRUCTIONS-MARKER'}]}},
        ]
        for index, record in enumerate(records):
            observer._process_record(thread, record, str(index))
        rendered = json.dumps(events)
        for marker in ('PRIVATE-ANALYSIS-MARKER', 'PRIVATE-COMPACTION-MARKER', 'PRIVATE-INSTRUCTIONS-MARKER'):
            self.assertNotIn(marker, rendered)

    def test_role_persistence_and_workspace_reporting(self):
        folder = self.project
        insp = self.modules.inspect({'project': str(folder)})
        result = self.modules.connect({'project': str(folder), 'repository_mode': 'agent_system',
                                       'revision': insp['workspace']['revision']})
        self.assertEqual(result['workspace']['repository_mode'], 'agent_system')
        self.assertEqual(result['workspace']['module'], 'project')
        cfg = self.config
        data = json.loads(cfg.read_text(encoding='utf-8'))
        self.assertEqual(data['connection']['repository_mode'], 'agent_system')
        self.assertEqual(data['connection']['module'], 'project')

        with patch.object(self.modules, 'codex_availability', return_value={'available': True}):
            rev = self.modules.inspect({'project': str(folder)})['workspace']['revision']
            result2 = self.modules.connect({'project': str(folder), 'repository_mode': 'working_repository',
                                            'revision': rev})
            self.assertEqual(result2['workspace']['repository_mode'], 'working_repository')
            self.assertEqual(result2['workspace']['module'], 'codex')
            data2 = json.loads(cfg.read_text(encoding='utf-8'))
            self.assertEqual(data2['connection']['repository_mode'], 'working_repository')
            self.assertEqual(data2['connection']['module'], 'codex')

    def test_module_mapping_and_legacy_inference(self):
        folder = self.root / 'legacy-inference-test'
        folder.mkdir()
        target = folder / '.agent-observer/config'
        target.mkdir(parents=True)
        cfg = target / 'observer.project.json'
        cfg.write_text(json.dumps({'schema_version': 1, 'project': {'root': str(folder)},
                                   'connection': {'module': 'codex', 'enabled': True}}))
        w = self.modules.inspect({'project': str(folder)})['workspace']
        self.assertEqual(w['repository_mode'], 'working_repository')
        self.assertEqual(w['module'], 'codex')

        cfg.write_text(json.dumps({'schema_version': 1, 'project': {'root': str(folder)},
                                   'connection': {'module': 'project', 'enabled': True}}))
        w2 = self.modules.inspect({'project': str(folder)})['workspace']
        self.assertEqual(w2['repository_mode'], 'agent_system')
        self.assertEqual(w2['module'], 'project')

    def test_role_module_mismatch_and_invalid_validation(self):
        folder = self.root / 'validation-test'
        folder.mkdir()
        insp = self.modules.inspect({'project': str(folder)})
        rev = insp['workspace']['revision']
        with self.assertRaises(InstallationError) as err:
            self.modules.connect({'project': str(folder), 'repository_mode': 'agent_system',
                                  'module': 'codex', 'revision': rev})
        self.assertEqual(err.exception.status, 400)
        self.assertIn('agent_system', err.exception.detail)

        with self.assertRaises(InstallationError) as err:
            self.modules.connect({'project': str(folder), 'repository_mode': 'working_repository',
                                  'module': 'project', 'revision': rev})
        self.assertEqual(err.exception.status, 400)
        self.assertIn('working_repository', err.exception.detail)

        with self.assertRaises(InstallationError) as err:
            self.modules.connect({'project': str(folder), 'repository_mode': 'invalid_role',
                                  'revision': rev})
        self.assertEqual(err.exception.status, 400)

    def test_revisiting_folder_preserves_disabled_and_role_state(self):
        folder = self.project
        insp = self.modules.inspect({'project': str(folder)})
        self.modules.connect({'project': str(folder), 'repository_mode': 'agent_system',
                              'revision': insp['workspace']['revision']})
        rev = self.modules.inspect({'project': str(folder)})['workspace']['revision']
        disc = self.modules.disconnect({'project': str(folder), 'revision': rev})
        self.assertFalse(disc['workspace']['enabled'])
        self.assertEqual(disc['workspace']['repository_mode'], 'agent_system')
        self.assertEqual(disc['workspace']['module'], 'project')

        fresh_modules = ConnectionModules(self.settings)
        self.addCleanup(fresh_modules.shutdown)
        revisited = fresh_modules.inspect({'project': str(folder)})['workspace']
        self.assertFalse(revisited['enabled'])
        self.assertEqual(revisited['repository_mode'], 'agent_system')
        self.assertEqual(revisited['module'], 'project')

    def test_immediate_observer_activation_on_role_change(self):
        from test_connection_analysis import init_codex_db, insert_thread

        codex = self.home / '.codex'
        database = codex / 'state_1.sqlite'
        init_codex_db(database)
        rollout = codex / 'sessions/current.jsonl'
        rollout.parent.mkdir()
        rollout.write_text('')
        insert_thread(database, 'codex-chat', rollout, self.project)
        omp = self.home / '.omp/agent'
        journal = omp / 'sessions/project/current.jsonl'
        journal.parent.mkdir(parents=True)
        journal.write_text(json.dumps({'type': 'session', 'version': 3, 'id': 'omp-chat',
                                       'cwd': str(self.project), 'timestamp': '2026-11-16T00:00:00Z'}) + '\n')
        with patch.dict(os.environ, {'PI_CODING_AGENT_DIR': str(omp)}):
            self.modules.start(lambda event: None)
            rev = self.modules.inspect({'project': str(self.project)})['workspace']['revision']
            self.modules.connect({'project': str(self.project), 'repository_mode': 'working_repository', 'revision': rev})
            observer = self.modules.observer
            self.assertEqual({row['session_id'] for row in observer.sessions()}, {'codex-chat', 'omp:omp-chat'})
            sources = observer.status()['sources']
            self.assertTrue(sources['codex']['watching'])
            self.assertTrue(sources['omp']['watching'])
            rev = self.modules.inspect({'project': str(self.project)})['workspace']['revision']
            self.modules.connect({'project': str(self.project), 'repository_mode': 'agent_system', 'revision': rev})
            self.assertFalse(observer.status()['watching'])
            self.assertIsNone(self.modules.observer)
