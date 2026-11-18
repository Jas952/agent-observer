"""Safe OMP configuration/reference metadata; never execute providers or memory writes."""
import json
from pathlib import Path
import os
import yaml

from .omp_observer import omp_home

# Names and setting gates from the installed OMP public tool reference.
TOOLS = {
    'read': ('read', True), 'write': ('write', True), 'edit': ('edit', True),
    'bash': ('bash', True), 'eval': ('eval', True), 'glob': ('glob', True),
    'grep': ('grep', True), 'task': ('task', True), 'wait': ('async', True),
    'todo': ('todo', True), 'ask': ('ask', True), 'web_search': ('web_search', True),
    'lsp': ('lsp', True), 'ast_edit': ('astEdit', True), 'debug': ('debug', True),
    'ida': ('ida', True), 'find': ('find', 'auto'), 'ast_grep': ('astGrep', False),
    'github': ('github', False), 'checkpoint': ('checkpoint', False),
    'rewind': ('checkpoint', False), 'context_notes': ('compaction.experimentalContextManagement', False),
    'new_context': ('compaction.experimentalContextManagement', False),
    'generate_image': ('generate_image', False), 'security_scan': ('security', False),
    'tts': ('speechgen', False),
}


def _merge(target, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def read_omp_settings(project_root=None):
    data = {}
    roots = [omp_home(), Path(project_root) / '.omp'] if project_root else [omp_home()]
    for root in roots:
        for filename in ('config.yml', 'config.yaml', 'settings.json'):
            path = root / filename
            try:
                if not path.is_file() or path.stat().st_size > 1024 * 1024:
                    continue
                raw = json.loads(path.read_text()) if path.suffix == '.json' else yaml.safe_load(path.read_text())
                if isinstance(raw, dict):
                    _merge(data, raw)
                break
            except (OSError, ValueError, yaml.YAMLError):
                continue
    return data


def _get(config, key, default=None):
    value = config
    for part in key.split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    return default if value is None else value


def reference_commands():
    path = Path(__file__).resolve().parents[2] / 'config/omp-reference.json'
    try:
        return json.loads(path.read_text())['commands']
    except (OSError, ValueError, KeyError):
        return []


def configured_inventory(project_root=None):
    config = read_omp_settings(project_root)
    backend = _get(config, 'memory.backend', 'off')
    if backend not in {'off', 'local', 'hindsight', 'mnemopi', 'sharpshooter'}:
        backend = 'off'
    native = []
    for name, (gate, default) in TOOLS.items():
        value = _get(config, gate if '.' in gate else gate + '.enabled', default)
        enabled = value not in (False, 'off', 'none')
        native.append({'name': name, 'description': f'OMP {name}; configuration gate {gate}. Runtime availability may depend on mode and installed dependencies.',
                       'source': 'omp-config', 'availability': 'configured' if enabled else 'unavailable'})
    for name in ('retain', 'recall', 'reflect', 'memory_edit', 'learn', 'manage_skill'):
        enabled = backend in {'hindsight', 'mnemopi'} if name in {'retain', 'recall', 'reflect'} else backend == 'mnemopi' if name == 'memory_edit' else bool(_get(config, 'autolearn.enabled', False))
        native.append({'name': name, 'description': f'OMP {name}; availability follows the selected memory backend and learning policy.',
                       'source': 'omp-config', 'availability': 'configured' if enabled else 'unavailable'})
    systems = []
    endpoint = os.environ.get('HINDSIGHT_API_URL') or _get(config, 'hindsight.apiUrl', 'http://localhost:8888')
    for key, name, tools in [('hindsight', 'OMP · Hindsight', ['retain', 'recall', 'reflect', 'learn']),
                             ('local', 'OMP · Local summaries', ['learn']),
                             ('mnemopi', 'OMP · Mnemopi SQLite', ['retain', 'recall', 'reflect', 'memory_edit', 'learn']),
                             ('sharpshooter', 'OMP · Sharpshooter decisions', [])]:
        enabled = backend == key
        systems.append({'id': 'omp-' + key, 'name': name,
                        'provider': 'hindsight' if key == 'hindsight' and enabled else 'custom',
                        'endpoint': endpoint if key == 'hindsight' and enabled else '',
                        'enabled': enabled, 'availability': 'configured' if enabled else 'unavailable',
                        'source': 'omp-config', 'description': f'{"Selected" if enabled else "Not selected"} OMP memory backend. Configuration is not proof of server health or retention.',
                        'match': {'tool_names': tools if enabled else []}})
    for key, name, enabled, tools in [('managed-skills', 'OMP · Managed skills / Auto-learn', bool(_get(config, 'autolearn.enabled', False)), ['manage_skill']),
                                      ('archive', 'OMP · Read-only session archive', bool(_get(config, 'archive.enabled', True)), [])]:
        systems.append({'id': 'omp-' + key, 'name': name, 'provider': 'custom', 'endpoint': '',
                        'enabled': enabled, 'availability': 'configured' if enabled else 'unavailable',
                        'source': 'omp-config', 'description': 'Independent OMP capability; not an additional selected memory backend.',
                        'match': {'tool_names': tools if enabled else []}})
    commands = reference_commands()
    # Only metadata for explicit native command files; never expand their templates.
    for root in [omp_home() / 'commands', *([Path(project_root) / '.omp/commands'] if project_root else [])]:
        for path in sorted(root.glob('*.md')):
            commands.append({'name': '/' + path.stem, 'source': 'omp-command-file',
                             'description': 'Discovered OMP command template; not executed.', 'availability': 'configured'})
    return {'native_tools': native, 'commands': commands, 'memory_systems': systems}
