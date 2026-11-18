import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from agent_observer.capabilities import discover_capabilities
from agent_observer.observer_config import load_settings


def cad_fixture(directory):
    root = directory / 'project'
    bridge = root / 'src/CADAgentUI/Sources/CADAgentUI/HarnessBridge.swift'
    bridge.parent.mkdir(parents=True)
    bridge.write_text('let bundles = ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"]\n')
    packages = directory / 'home/.npm/_npx/fixture/node_modules'
    vendor = packages / '@deepseek-ai'
    executable = vendor / 'dsh/lib/bin.js'
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\ntouch "' + str(directory / 'executed') + '"\n')
    executable.chmod(0o755)
    (vendor / 'dsh/package.json').write_text(json.dumps({'name': '@deepseek-ai/dsh'}))
    base = vendor / 'dsh-base'
    headless = vendor / 'dsh-headless'
    base.mkdir(); headless.mkdir()
    (base / 'cordis.patch.yml').write_text('''- insert:
    - id: fs
      name: '@deepseek-ai/dsh-tool-fs'
    - id: disabled
      name: '@deepseek-ai/dsh-tool-disabled'
      disabled: true
    - id: dynamic
      name: '@deepseek-ai/dsh-tool-dynamic'
      disabled: !!js arbitrarySideEffect()
''')
    (headless / 'cordis.patch.yml').write_text('[]\n')
    for name, source in {
        'fs': '''const sample = `defineTool({name: "not_a_tool", description: "quoted example"})`;
// defineTool({name: "also_not_a_tool", description: "comment"})
const WRITE_DESCRIPTION = "Replace a UTF-8 file.";
ctx.tools.register(defineTool({name: 'read', description: 'Read a UTF-8 file.', parameters: {}}));
ctx.tools.register(defineTool({name: "write", description: WRITE_DESCRIPTION, parameters: {}}));
''',
        'disabled': 'ctx.tools.register(defineTool({name:"disabled_tool",description:"Unavailable"}));',
        'dynamic': 'ctx.tools.register(defineTool({name:"unresolved_tool",description:"Unknown condition"}));',
    }.items():
        package = vendor / ('dsh-tool-' + name)
        (package / 'lib').mkdir(parents=True)
        (package / 'package.json').write_text(json.dumps({'name': '@deepseek-ai/dsh-tool-' + name, 'main': 'lib/index.js', 'description': 'Fixture tools'}))
        (package / 'lib/index.js').write_text(source)
    config = root / 'private/observer.project.json'
    config.parent.mkdir()
    config.write_text(json.dumps({'schema_version': 1, 'project': {'root': str(root), 'name': 'Embedded CAD', 'agent_label': 'CAD agent'},
                                 'storage_profile': 'project', 'connection': {'module': 'project', 'enabled': True}, 'agent_runtime': 'cad', 'memory_systems': []}))
    return root, directory / 'home', executable, config, vendor


class CADNativeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root, self.home, self.executable, self.config, self.vendor = cad_fixture(self.directory)
        environment = patch.dict(os.environ, {'HOME': str(self.home), 'CAD_DSH': str(self.executable)})
        environment.start(); self.addCleanup(environment.stop)

    def test_project_catalog_uses_enabled_embedded_providers_not_examples_or_other_agent_defaults(self):
        inventory = discover_capabilities(self.root, agent='cad')
        self.assertEqual({tool['name']: tool['description'] for tool in inventory['native_tools']},
                         {'read': 'Read a UTF-8 file.', 'write': 'Replace a UTF-8 file.'})
        self.assertFalse((self.directory / 'executed').exists(), 'Discovery must not launch the project tool provider')
        self.assertEqual(discover_capabilities(self.root, agent='generic')['native_tools'], [])
        self.assertEqual(discover_capabilities(self.directory, agent='cad')['native_tools'], [])

    def test_headless_overrides_and_package_boundaries_control_the_catalog(self):
        (self.vendor / 'dsh-headless/cordis.patch.yml').write_text('- id: fs\n  disabled: true\n- id: disabled\n  disabled: false\n')
        self.assertEqual([tool['name'] for tool in discover_capabilities(self.root, agent='cad')['native_tools']], ['disabled_tool'])
        outside = self.directory / 'private.js'
        outside.write_text('ctx.tools.register(defineTool({name:"private_tool",description:"PRIVATE"}));')
        (self.vendor / 'dsh-tool-disabled/package.json').write_text(json.dumps({'name': '@deepseek-ai/dsh-tool-disabled', 'main': str(outside)}))
        inventory = discover_capabilities(self.root, agent='cad')
        self.assertEqual(inventory['native_tools'], [])
        self.assertNotIn('PRIVATE', json.dumps(inventory))

    def test_invalid_profile_cannot_execute_yaml_objects_or_restore_unrelated_commands(self):
        (self.vendor / 'dsh-headless/cordis.patch.yml').write_text('!!python/object/apply:os.system ["touch ' + str(self.directory / 'executed') + '"]')
        self.assertEqual(discover_capabilities(self.root, agent='cad')['native_tools'], [])
        self.assertFalse((self.directory / 'executed').exists())


class CADModuleCutoverTests(unittest.TestCase):
    def test_real_http_cutover_resets_live_commands_and_rejects_the_other_agent_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root, home, executable, config, vendor = cad_fixture(directory)
            codex_home = directory / 'codex'
            rollout = codex_home / 'sessions/2026/01/01/session.jsonl'
            rollout.parent.mkdir(parents=True)
            rollout.write_text(json.dumps({'type': 'response_item', 'payload': {'type': 'additional_tools', 'tools': [
                {'type': 'namespace', 'name': 'functions', 'tools': [
                    {'name': 'exec', 'description': 'Run a native Codex operation'},
                    {'name': 'apply_patch', 'description': 'Apply a native Codex patch'}]}]}}) + '\n')
            with sqlite3.connect(codex_home / 'state_5.sqlite') as database:
                database.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,cwd TEXT,source TEXT,updated_at INTEGER)')
                database.execute('INSERT INTO threads VALUES(?,?,?,?,?)', ('fixture-thread', str(rollout), str(root), 'vscode', 1))
            with socket.socket() as address:
                address.bind(('127.0.0.1', 0)); port = address.getsockname()[1]
            data = json.loads(config.read_text()); data['port'] = port; config.write_text(json.dumps(data))
            private = {'AGENT_OBSERVER_TOKEN_FILE': str(directory / 'auth-token'),
                       'AGENT_OBSERVER_HISTORY_FILE': str(directory / 'history/runs.json')}
            settings = load_settings(config, environment=private)
            settings['token_file'].parent.mkdir(parents=True, exist_ok=True)
            settings['token_file'].write_text('fixture-token')
            settings['token_file'].chmod(0o600)
            environment = {key: value for key, value in os.environ.items() if not key.startswith(('CAD_', 'AGENT_OBSERVER_', 'XRAY_'))}
            environment.update(HOME=str(home), CODEX_HOME=str(codex_home), CAD_DSH=str(executable), PYTHONPATH=str(Path(__file__).resolve().parents[2]))
            environment.update(private)
            launcher = ('import sys; from pathlib import Path; from agent_observer import cli; '
                        'cli.REGISTRY = Path(sys.argv.pop(1)); cli.main()')
            process = subprocess.Popen([sys.executable, '-c', launcher, str(directory / 'installations.json'),
                                       'run', '--config', str(config), '--port', str(port)], env=environment,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                def request(path, body=None):
                    encoded = json.dumps(body).encode() if body is not None else None
                    message = urllib.request.Request(f'http://127.0.0.1:{port}' + path, data=encoded,
                        headers={'X-Agent-Observer-Token': 'fixture-token', 'Content-Type': 'application/json'})
                    with urllib.request.urlopen(message, timeout=15) as response:
                        return json.load(response)
                deadline = time.monotonic() + 15
                while True:
                    try:
                        request('/health'); break
                    except (OSError, urllib.error.URLError):
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail('The isolated graph did not start')
                        time.sleep(.03)
                def connect(module):
                    workspace = request('/api/connections')['workspace']
                    return request('/api/connections/connect', {'project': str(root), 'module': module, 'revision': workspace['revision']})
                initial = request('/api/config')
                self.assertEqual({tool['name'] for tool in initial['native_tools']}, {'read', 'write'})
                attached = connect('codex')
                self.assertEqual({tool['name'] for tool in attached['config']['native_tools']}, {'exec', 'apply_patch'})
                foreign = request('/api/event', {'type': 'PROMPT_START', 'run_id': 'foreign-cad', 'application': 'CAD Studio', 'source': 'agent', 'prompt': 'Not the selected agent'})
                self.assertEqual(foreign['status'], 'ignored')
                base = {'run_id': 'codex-recorded', 'application': 'Codex', 'source': 'codex', 'session_id': 'fixture-thread'}
                request('/api/event', {**base, 'type': 'PROMPT_START', 'prompt': 'Inspect the selected project'})
                request('/api/event', {**base, 'type': 'TOOL_CALL_START', 'tool': 'exec', 'native_tool': True, 'call_id': 'before-switch', 'input': '{"command":"printf result"}'})
                self.assertTrue(any(node.get('label') == 'exec' and node.get('status') == 'active' for node in request('/api/state')['nodes'].values()))
                returned = connect('project')
                self.assertEqual({tool['name'] for tool in returned['config']['native_tools']}, {'read', 'write'})
                self.assertEqual(returned['history'], [])
                self.assertFalse(any(node.get('kind') == 'command' for node in returned['state']['nodes'].values()))
                before = request('/api/state')
                late = request('/api/event', {**base, 'type': 'TOOL_CALL_END', 'tool': 'exec', 'native_tool': True, 'call_id': 'before-switch', 'success': True, 'result': 'late'})
                self.assertEqual(late['status'], 'ignored')
                after = request('/api/state')
                self.assertEqual(after['nodes'], before['nodes'])
                self.assertEqual(after['edges'], before['edges'])
                self.assertEqual(after['run_id'], before['run_id'])
                self.assertFalse((directory / 'executed').exists())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
