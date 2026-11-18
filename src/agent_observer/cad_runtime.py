"""Read the embedded CAD application's installed Harness tool declarations."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import sys

import yaml

from .tools_discovery import _read_bounded

_STRING = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
_COMMENTS = re.compile(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*.*?\*/', re.S)
_LITERAL = _STRING + r'|`(?:\\.|[^`\\])*`'
_DECLARATION = re.compile(r'\bdefineTool\(\s*\{\s*name:\s*(' + _STRING + r')\s*,\s*description:\s*(' + _LITERAL + r'|[A-Za-z_$][\w$]*)', re.S)
_CONSTANT = re.compile(r'\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(' + _LITERAL + r')')


def _declarations(text, config, purpose):
    spans = [match.span() for match in _COMMENTS.finditer(text) if match.group(1)]
    code = _COMMENTS.sub(lambda match: match.group(1) or ' ' * len(match.group()), text)
    outside = lambda match: not any(start <= match.start() < end for start, end in spans)

    def literal(value):
        if value.startswith(('"', "'")):
            return ast.literal_eval(value)
        text = value[1:-1]
        return text.partition('${')[0].split('. ')[0] + '.' if '${' in text else text

    constants = {match.group(1): literal(match.group(2)) for match in _CONSTANT.finditer(code) if outside(match)}
    result = []
    for match in _DECLARATION.finditer(code):
        if outside(match):
            name, description = match.groups()
            result.append((literal(name), literal(description) if description.startswith(('"', "'", '`')) else constants.get(description)))
    tool_name = config.get('toolName') if isinstance(config, dict) else None
    if tool_name is None and re.search(r'\bname:\s*(?:config\.)?toolName\b', code):
        default = re.search(r'\btoolName:\s*\w+\.string\(\)\.default\((' + _STRING + r')\)', code)
        if default and outside(default):
            tool_name = literal(default.group(1))
    if isinstance(tool_name, str):
        result.append((tool_name, purpose))
    return result


class _ProfileLoader(yaml.SafeLoader):
    pass


_ProfileLoader.add_constructor('tag:yaml.org,2002:js', lambda loader, node: {'expression': loader.construct_scalar(node)})


def _enabled(row):
    disabled = row.get('disabled', False)
    if isinstance(disabled, dict):
        expression = disabled.get('expression')
        if expression == "process.platform === 'win32'":
            return sys.platform != 'win32'
        if expression == "process.platform !== 'win32'":
            return sys.platform == 'win32'
        return False
    return disabled is False or disabled is None


def _packages():
    candidates = [os.environ.get('CAD_DSH'), shutil.which('dsh')]
    cache = Path.home() / '.npm/_npx'
    if cache.is_dir():
        candidates.extend(sorted(cache.glob('*/node_modules/.bin/dsh')))
    for candidate in candidates:
        if not candidate:
            continue
        executable = Path(candidate).expanduser().resolve()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            continue
        for parent in executable.parents:
            if parent.name != 'node_modules':
                continue
            manifest = _read_bounded(parent / '@deepseek-ai/dsh/package.json')
            try:
                if manifest and json.loads(manifest).get('name') == '@deepseek-ai/dsh':
                    return parent
            except (ValueError, AttributeError):
                pass
    return None


def discover_cad_native_tools(project_root):
    """Declared Harness capabilities; never a claim of calls or Direct-API tool use."""
    root = Path(project_root).resolve()
    bridge = root / 'src/CADAgentUI/Sources/CADAgentUI/HarnessBridge.swift'
    source = _read_bounded(bridge)
    if not source or not all(name in source for name in ('@deepseek-ai/dsh-base', '@deepseek-ai/dsh-headless')):
        return []
    packages = _packages()
    if packages is None:
        return []
    rows = {}
    try:
        for bundle in ('dsh-base', 'dsh-headless'):
            text = _read_bounded(packages / '@deepseek-ai' / bundle / 'cordis.patch.yml')
            if text is None:
                return []
            patches = yaml.load(text, Loader=_ProfileLoader)
            if not isinstance(patches, list):
                return []
            for patch in patches:
                if not isinstance(patch, dict):
                    continue
                for row in patch.get('insert', [patch]):
                    if isinstance(row, dict) and isinstance(row.get('id'), str):
                        previous = rows.get(row['id'], {})
                        rows[row['id']] = {**previous, **row}
    except (yaml.YAMLError, TypeError):
        return []
    found = {}
    for row in rows.values():
        name = row.get('name', '')
        if not isinstance(name, str) or not name.startswith('@deepseek-ai/dsh-tool-') or not _enabled(row):
            continue
        parts = name.split('/')
        package = packages / parts[0] / parts[1]
        try:
            metadata = json.loads(_read_bounded(package / 'package.json') or '{}')
            if metadata.get('name') != '/'.join(parts[:2]):
                continue
            export = metadata.get('exports', {}).get('./' + '/'.join(parts[2:])) if len(parts) > 2 else metadata.get('main', 'lib/index.js')
            if isinstance(export, dict):
                export = export.get('default') or export.get('import')
            if not isinstance(export, str):
                continue
            module = (package / export).resolve()
            if not module.is_relative_to(package.resolve()):
                continue
            text = _read_bounded(module, max_bytes=256 * 1024)
            if text is None:
                continue
            declarations = _declarations(text, row.get('config', {}), metadata.get('description'))
            for tool, description in declarations:
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', tool):
                    continue
                found[tool] = {'name': tool, 'description': str(description or metadata.get('description') or tool)[:512],
                               'source': 'cad-harness-declaration'}
        except (ValueError, AttributeError, TypeError, SyntaxError):
            continue
    return list(found.values())
