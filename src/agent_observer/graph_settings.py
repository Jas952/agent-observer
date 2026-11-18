"""Graph feature inventory. Discovery is declarative; only an explicit reveal opens Finder."""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

from .capabilities import discover_capabilities, mcp_node_id, skill_node_id, integration_node_id
from .tools_discovery import discover_tools
from .results import ResultError

FEATURES = [('request', 'Project prompt & refinements', {'prompt', 'spec', 'comment'}), ('agent', 'Agent', {'agent'}),
            ('documents', 'Documents & references', {'document'}), ('tools', 'Tools & services', {'tool'}),
            ('commands', 'Commands', {'command'}), ('files', 'Working files', {'file'}),
            ('capabilities', 'MCP & skills', {'mcp', 'skill', 'integration'}), ('memory', 'Memory systems', {'memory'}),
            ('result', 'Result', {'result'})]


class GraphSettings:
    def __init__(self, settings):
        self.settings = settings
        self.root = Path(settings['project_root']).resolve()
        self.roots = {}
        self.discovered = None

    def scan(self):
        self.discovered = {'capabilities': discover_capabilities(self.root, self.settings.get('config_file')), 'tools': discover_tools(self.settings)}
        self.scanned_at = datetime.now(timezone.utc).isoformat()
        return self.discovered

    def inventory(self, config, snapshot):
        discovered = self.discovered if self.discovered is not None else self.scan()
        self.roots = {'installation': self.root}
        catalog_nodes = {node['id']: node for node in config.get('nodes', [])}
        nodes = dict(catalog_nodes)
        nodes.update(snapshot.get('nodes', {}))
        graph_ids = set(nodes)
        rows = []
        def add(key, label, kind, *, detected=False, connected=None, path=None, description='', node_id=None, in_graph=None):
            if path is not None:
                path = Path(path).expanduser()
                if path.exists():
                    self.roots[key] = path if path.is_dir() else path.parent
            rows.append({'id': key, 'name': label, 'kind': kind, 'detected': bool(detected),
                         'connected': connected, 'root_available': key in self.roots,
                         'description': description, 'node_id': node_id,
                         'resource': {key:value for key,value in nodes.get(node_id, {}).items()
                                      if key in {'id','kind','name','label','path','absolute_path','category'}},
                         'in_graph': node_id in graph_ids if in_graph is None else in_graph})
        for key, label, kinds in FEATURES:
            count = sum(node.get('kind') in kinds for node in nodes.values())
            add('block:' + key, (config.get('agent_label') or label) if key == 'agent' else label, 'block', detected=True,
                path=self.root, description=f'Installed graph renderer · {count} server-registered resources', node_id='group:' + key)
        cap = discovered['capabilities']
        configured_paths = {'project-omp': [self.root / '.omp/mcp.json', self.root / '.omp/agent/mcp.json'],
                            'project-mcp': [self.root / '.mcp.json'],
                            'global-omp': [Path.home() / '.omp/agent/mcp.json', Path.home() / '.omp/mcp.json'],
                            'claude-mcp': [Path.home() / '.claude/.mcp.json', Path.home() / '.claude/mcp.json']}
        disk = {}
        for item in cap.get('mcp', []):
            source = next((path for path in configured_paths.get(item.get('source'), []) if path.is_file()), None)
            disk[mcp_node_id(item['name'])] = source
        skill_paths = {}
        for item in cap.get('skills', []):
            path = Path(item.get('path', '')).expanduser()
            skill_paths[skill_node_id(item['name'])] = path if path.is_absolute() else self.root / path
        for item in cap.get('mcp', []):
            identifier = mcp_node_id(item['name'])
            nodes.setdefault(identifier, {**item, 'id': identifier, 'kind': 'mcp', 'label': item['name']})
        for item in cap.get('skills', []):
            identifier = skill_node_id(item['name'])
            nodes.setdefault(identifier, {**item, 'id': identifier, 'kind': 'skill', 'label': item['name']})
        for item in cap.get('integrations', []):
            identifier = integration_node_id(item['name'])
            nodes.setdefault(identifier, {**item, 'id': identifier, 'kind': 'integration', 'label': item['name']})
        for node in nodes.values():
            kind, identifier = node.get('kind'), node['id']
            if kind not in {'mcp', 'skill', 'integration', 'memory', 'tool', 'command', 'document', 'file'}:
                continue
            path, found, connected = None, False, None
            if kind == 'mcp':
                path = disk.get(identifier)
                found = path is not None
                connected = node.get('connection') in {'connected', 'provided'}
            elif kind == 'skill':
                path = skill_paths.get(identifier)
                found = bool(path and path.is_file())
            elif kind == 'integration':
                binary = shutil.which(str(node.get('name', node.get('label', ''))))
                path, found = binary, bool(binary)
            elif kind == 'memory':
                # Registered provider is not a verified live API connection.
                found = bool(node.get('provider') or node.get('memory_system_id'))
                path = Path(self.settings['config_file'])
                connected = node.get('connection') == 'connected'
            elif kind == 'command':
                found = True
                path = self.root
            elif kind in {'document', 'file'}:
                registered = catalog_nodes.get(identifier) if kind == 'document' else None
                candidate = Path(registered['absolute_path']).resolve() if registered and registered.get('absolute_path') else (self.root / str(node.get('path') or '')).resolve()
                found = (bool(registered) or candidate.is_relative_to(self.root)) and candidate.is_file()
                path = candidate if found else None
            else:
                names = [str(node.get('name') or node.get('label') or '')] + list(node.get('match', {}).get('command_prefixes', []))
                binary = next((shutil.which(name.split()[0]) for name in names if name and shutil.which(name.split()[0])), None)
                path, found = binary, bool(binary)
            add(identifier, node.get('label', node.get('name', identifier)), kind, detected=found,
                connected=connected, path=path, description=node.get('description', ''), node_id=identifier)
        command_names = {row['name'] for row in rows if row['kind'] == 'command'}
        for command in config.get('native_tools', []):
            name = command.get('name') if isinstance(command, dict) else str(command)
            if name and name not in command_names:
                add('command:' + name, name, 'command', detected=True, path=self.root,
                    description=command.get('description', '') if isinstance(command, dict) else '', in_graph=True)
                command_names.add(name)
        for identifier, kind, label in [('request_prompt','prompt','Project prompt'), ('agent-hub','agent',config.get('agent_label') or 'Agent'), ('run_result','result','Agent result')]:
            add(identifier, label, kind, detected=True, node_id=identifier, in_graph=True)
        known_names = {row['name'].casefold() for row in rows}
        for item in discovered['tools'].get('tools', []):
            if item['name'].casefold() in known_names:
                continue
            binary = shutil.which(item['name'])
            add('local:' + hashlib.sha256(item['name'].encode()).hexdigest()[:16], item['name'], 'tool',
                detected=bool(binary) or item.get('evidence') == 'available', path=binary or self.root,
                description=item.get('description', ''))
        registered_providers = {node.get('provider') for node in nodes.values() if node.get('kind') == 'memory'}
        for name, candidates in [('Hindsight', [self.root / '.hindsight']),
                                 ('Mem0', [self.root / '.mem0']), ('Letta', [self.root / '.letta'])]:
            if name.lower() in registered_providers:
                continue
            path = next((path for path in candidates if path.is_dir()), None)
            if name.casefold() not in known_names:
                add('local-memory:' + name.lower(), name, 'memory', detected=path is not None, path=path,
                    description='Known local provider location. Register it in Memory settings to observe its operations.')
        groups = {'prompt':'request', 'spec':'request', 'comment':'request', 'agent':'agent', 'result':'result', 'command': 'commands', 'document': 'documents', 'file': 'files', 'tool': 'tools', 'memory': 'memory', 'mcp': 'capabilities', 'skill': 'capabilities', 'integration': 'capabilities'}
        for row in rows:
            row['group'] = row['id'].split(':', 1)[1] if row['kind'] == 'block' else groups.get(row['kind'], 'tools')
        from .observer_config import get_config_revision
        return {'project_root': str(self.root), 'rows': rows, 'scanned_at': self.scanned_at,
                'hidden_blocks': list(self.settings.get('hidden_blocks', [])),
                'hidden_resources': list(self.settings.get('hidden_resources', [])),
                'hidden_commands': list(self.settings.get('hidden_commands', [])),
                'revision': get_config_revision(Path(self.settings['config_file']))}

    def reveal(self, identifier):
        path = self.roots.get(identifier)
        if path is None or not path.is_dir():
            raise ResultError(404, 'No discovered folder is available for this entry. Search again first.')
        if sys.platform != 'darwin':
            raise ResultError(501, 'Native folder reveal is currently supported on macOS.')
        subprocess.run(['/usr/bin/open', str(path)], check=True, timeout=5, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'opened': True}
