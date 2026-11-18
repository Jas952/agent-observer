"""Public session metadata only; no commands, credentials or model reasoning."""
from __future__ import annotations

import copy
import math
from .agent_events import sanitize_event_data

_FIELDS = {
    'session': {'id', 'name', 'cwd', 'application', 'additional_directories'},
    'model': {'id', 'provider', 'effort', 'context_window', 'service_tier'},
    'context': {'tokens', 'window', 'percent', 'source', 'estimated', 'observed_at'},
    'queue': {'available', 'pending', 'steering', 'follow_up'},
    'todos': {'phases'}, 'jobs': {'available', 'items'},
    'mode': {'plan', 'goal', 'vibe', 'loop'},
    'approval': {'status', 'kind', 'title'},
    'compaction': {'status', 'method', 'tokens_before', 'tokens_after', 'at'},
    'retry': {'status', 'attempt', 'delay_ms', 'reason'},
    'memory': {'operation', 'status'},
}


def _bounded(value, depth=0):
    if depth > 5:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, list):
        return [_bounded(item, depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        return {str(key)[:80]: _bounded(item, depth + 1) for key, item in list(value.items())[:40]}
    return None


def clean_observation(patch):
    if not isinstance(patch, dict):
        raise ValueError('observation must be an object')
    result = {}
    for section, fields in _FIELDS.items():
        value = patch.get(section)
        if isinstance(value, dict):
            result[section] = {key: _bounded(item) for key, item in value.items() if key in fields}
    return sanitize_event_data(result)[0]


def merge_observation(previous, patch):
    result = copy.deepcopy(previous or {})
    clean = clean_observation(patch)
    old_model = result.get('model', {}).get('id')
    next_model = clean.get('model', {}).get('id')
    if next_model and old_model and next_model != old_model:
        result.pop('context', None)
    for section, value in clean.items():
        result[section] = {**result.get(section, {}), **value}
    compact = clean.get('compaction', {})
    if compact.get('status') == 'completed' and 'context' not in clean:
        result['context'] = {**result.get('context', {}), 'tokens': None, 'percent': None,
                             'source': 'awaiting_post_compaction_usage'}
    context = result.get('context', {})
    tokens, window = context.get('tokens'), context.get('window')
    if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and tokens >= 0 and isinstance(window, (int, float)) and not isinstance(window, bool) and window > 0:
        context['percent'] = min(100, tokens / window * 100)
    elif 'context' in clean and ('tokens' in clean['context'] or 'window' in clean['context']):
        context['percent'] = None
    return result


def omp_record_observation(record):
    """Project metadata from one committed record, never its dialogue content."""
    kind, at = record.get('type'), record.get('timestamp')
    if kind == 'model_change' and record.get('role', 'default') == 'default':
        return {'model': {'id': record.get('model')}}
    if kind == 'thinking_level_change':
        return {'model': {'effort': record.get('configured') or record.get('thinkingLevel')}}
    if kind == 'service_tier_change':
        return {'model': {'service_tier': record.get('serviceTier')}}
    if kind == 'mode_change':
        mode = str(record.get('mode', 'none'))
        return {'mode': {name: 'active' if name == mode else 'inactive' for name in ('plan', 'goal', 'vibe', 'loop')}}
    if kind == 'compaction':
        after = record.get('tokensAfter')
        context = {'tokens': after if isinstance(after, (int, float)) else None,
                   'percent': None, 'observed_at': at, 'estimated': True,
                   'source': 'compaction' if after is not None else 'awaiting_post_compaction_usage'}
        return {'compaction': {'status': 'completed', 'method': record.get('method'),
                              'tokens_before': record.get('tokensBefore'), 'tokens_after': after, 'at': at}, 'context': context}
    if kind == 'custom' and record.get('customType') == 'user_todo_edit':
        data = record.get('data') or {}
        return {'todos': {'phases': data.get('phases', [])}} if isinstance(data, dict) else {}
    message = record.get('message')
    if kind == 'message' and isinstance(message, dict) and message.get('role') == 'toolResult' and message.get('toolName') == 'todo' and not message.get('isError'):
        phases = (message.get('details') or {}).get('phases')
        if isinstance(phases, list):
            return {'todos': {'phases': phases}}
    if kind == 'message' and isinstance(message, dict) and message.get('role') == 'assistant':
        usage = message.get('usage')
        result = {}
        if message.get('model'):
            result['model'] = {'id': message['model'], 'provider': message.get('provider')}
        if isinstance(usage, dict):
            # OMP input excludes cached tokens. These are one call's context, not session spend.
            fields = [usage.get(key, 0) for key in ('input', 'output', 'cacheRead', 'cacheWrite')]
            if all(isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0 for n in fields) and any(fields):
                result['context'] = {'tokens': sum(fields), 'source': 'last_provider_usage',
                                     'estimated': True, 'observed_at': at}
        return result
    return {}

def tool_observation(event):
    kind = str(event.get('type', '')).upper()
    if kind not in {'TOOL_CALL_START', 'TOOL_CALL_END'}:
        return {}
    args = event.get('args') or event.get('input') or {}
    if not isinstance(args, dict):
        args = {}
    name = str(event.get('tool_name') or event.get('tool') or '').split('.')[-1]
    if name == 'write' and str(args.get('path', '')).startswith('xd://'):
        name = str(args['path'])[5:].split('/')[0]
    status = 'running' if kind == 'TOOL_CALL_START' else 'failed' if event.get('success') is False else 'completed'
    if name in {'recall', 'retain', 'reflect', 'learn', 'memory_edit'}:
        return {'memory': {'operation': name, 'status': status}}
    if name in {'ask', 'request_user_input'}:
        return {'approval': {'status': 'pending' if kind == 'TOOL_CALL_START' else 'resolved', 'kind': 'question', 'title': 'Agent is waiting for user input' if kind == 'TOOL_CALL_START' else 'User input ended'}}
    if kind != 'TOOL_CALL_END' or event.get('success') is False:
        return {}
    if name == 'update_plan' and isinstance(args.get('plan'), list):
        return {'todos': {'phases': [{'name': 'Plan', 'tasks': [{'content': task.get('step'), 'status': task.get('status')} for task in args['plan'] if isinstance(task, dict)]}]}}
    result = event.get('result')
    details = result.get('details') if isinstance(result, dict) else event.get('details')
    if name == 'todo' and isinstance(details, dict) and isinstance(details.get('phases'), list):
        return {'todos': {'phases': details['phases']}}
    return {}


def codex_record_observation(record):
    payload = record.get('payload') or {}
    if not isinstance(payload, dict):
        return {}
    kind = record.get('type')
    name = str(payload.get('type', kind))
    if kind == 'turn_context':
        return {'model': {'id': payload.get('model'), 'effort': payload.get('effort')}}
    info = payload.get('info') if isinstance(payload.get('info'), dict) else {}
    if name == 'token_count':
        usage = info.get('last_token_usage')
        result = {}
        if isinstance(info.get('model_context_window'), (int, float)) and info['model_context_window'] > 0:
            result['context'] = {'window': info['model_context_window']}
        if isinstance(usage, dict):
            tokens = usage.get('total_tokens')
            if tokens is None and all(isinstance(usage.get(key), (int, float)) for key in ('input_tokens', 'output_tokens')):
                tokens = usage['input_tokens'] + usage['output_tokens']
            if isinstance(tokens, (int, float)) and tokens >= 0:
                result.setdefault('context', {}).update(tokens=tokens, source='last_provider_usage', estimated=True, observed_at=record.get('timestamp'))
        return result
    if name in {'plan_update', 'plan_updated'} and isinstance(payload.get('plan'), list):
        return {'todos': {'phases': [{'name': 'Plan', 'tasks': [{'content': task.get('step'), 'status': task.get('status')} for task in payload['plan'] if isinstance(task, dict)]}]}}
    if 'compact' in name.casefold():
        return {'compaction': {'status': 'running' if name.endswith('_start') else 'completed', 'at': record.get('timestamp')}}
    return {}
