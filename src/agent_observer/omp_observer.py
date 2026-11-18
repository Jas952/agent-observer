"""Read-only OMP v3 journal observation; never replay old conversation on attach."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import shutil
import subprocess

import time
from .agent_events import sanitize_event_data
from .session_observation import merge_observation, omp_record_observation

MAX_RECORD = 8 * 1024 * 1024


def omp_home():
    return Path(os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.omp/agent').expanduser().resolve()


def session_header(path):
    """Only read bounded metadata, never scan messages to find project ownership."""
    title = ''
    with Path(path).open('rb') as stream:
        for _ in range(3):
            line = stream.readline(65537)
            if len(line) > 65536 or not line.endswith(b'\n'):
                return None
            item = json.loads(line)
            if item.get('type') == 'title':
                title = str(item.get('title') or '')[:240]
            elif item.get('type') == 'session' and item.get('id') and item.get('version') == 3:
                return {key: item.get(key) for key in ('id', 'cwd', 'additionalDirectories', 'timestamp')} | {'title': title}
    return None


def _text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(part['text'] for part in content if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str)) if isinstance(content, list) else ''


class OmpObserver:
    def __init__(self, project_root, state_dir, emit, *, omp_home=None, poll_interval=.5):
        self.project_root = Path(project_root).resolve()
        self.home = Path(omp_home).expanduser().resolve() if omp_home else globals()['omp_home']()
        self.state_dir = Path(state_dir)
        self._emit = emit
        self.poll_interval = max(.1, poll_interval)
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._cursors = {}
        self._runs = {}
        self._calls = {}
        self._last_event = None
        self._error = None
        self._attached_at = time.time()
        self._partial_baselines = set()
        self._model_metadata = {}
        self._metadata_pending = set()

    def _sources(self):
        root = self.home / 'sessions'
        for path in sorted(root.glob('*/*.jsonl')):
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    continue
                header = session_header(path)
                if not header:
                    continue
                directories = [header.get('cwd'), *(header.get('additionalDirectories') or [])]
                if not any(isinstance(value, str) and Path(value).expanduser().resolve() == self.project_root for value in directories):
                    continue
                yield path, header
            except (OSError, ValueError, TypeError):
                continue

    def sessions(self):
        with self._lock:
            rows = []
            for path, header in self._sources():
                sid = header['id']
                rows.append({'session_id': 'omp:' + sid, 'session_name': header['title'] or f'OMP {sid[:8]}',
                             'application': 'OMP', 'status': 'running' if sid in self._runs else 'unknown',
                             'run_id': self._runs.get(sid), 'cwd': header.get('cwd'),
                             'project_path': str(self.project_root), 'project_root': str(self.project_root),
                             'updated_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                             'capture_mode': 'read-only-journal'})
            return sorted(rows, key=lambda row: row['updated_at'], reverse=True)

    @classmethod
    def discover_sessions(cls, project_root, *, omp_home=None):
        return cls(project_root, Path('.'), lambda event: None, omp_home=omp_home).sessions()

    def has_session(self, session_id):
        raw = str(session_id).removeprefix('omp:')
        return any(header['id'] == raw for _, header in self._sources())

    def _baseline(self, path, stat, offset=None):
        offset = stat.st_size if offset is None else offset
        self._cursors[str(path)] = (stat.st_ino, offset)
        self._partial_baselines.discard(str(path))
        if offset == stat.st_size and offset:
            with path.open('rb') as source:
                source.seek(-1, 2)
                if source.read(1) != b'\n':
                    self._partial_baselines.add(str(path))

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._attached_at = time.time()
            # Fresh attachment always starts at EOF, including after a server restart.
            # No disk cursor can authorize importing the intervening conversation.
            for path, header in self._sources():
                stat = path.stat()
                self._baseline(path, stat)
                self._metadata_pending.add(str(path))
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='omp-observer', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._thread = None

    def status(self):
        watching = bool(self._thread and self._thread.is_alive() and not self._stop.is_set())
        return {'state': 'error' if self._error else 'watching' if watching else 'stopped',
                'watching': watching, 'error': self._error, 'project_root': str(self.project_root),
                'matching_thread_count': len(self.sessions()), 'last_event_at': self._last_event,
                'transport': 'Read-only OMP journals; new records only'}

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
                self._error = None
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                self._error = f'OMP journal observation paused: {type(error).__name__}'
            self._stop.wait(self.poll_interval)

    def poll_once(self):
        # Do not hold the lock while delivering to the server's event loop.
        for path, header in self._sources():
            stat = path.stat()
            previous = self._cursors.get(str(path))
            if str(path) in self._metadata_pending and previous:
                self._metadata_baseline(path, header, previous[1])
                self._metadata_pending.discard(str(path))
            if previous is None or previous[0] != stat.st_ino or stat.st_size < previous[1]:
                try:
                    created = datetime.fromisoformat(str(header.get('timestamp')).replace('Z', '+00:00')).timestamp()
                except (ValueError, TypeError):
                    created = 0
                offset = 0 if previous is None and created >= self._attached_at else stat.st_size
                self._baseline(path, stat, offset)
                self._runs.pop(header['id'], None)
                self._calls.pop(header['id'], None)
                self._metadata_baseline(path, header)
                continue
            with path.open('rb') as stream:
                stream.seek(previous[1])
                for _ in range(256):
                    if self._stop.is_set():
                        return
                    offset = stream.tell()
                    line = stream.readline(MAX_RECORD + 1)
                    if len(line) > MAX_RECORD:
                        raise ValueError('OMP record exceeds the supported event limit')
                    if not line or not line.endswith(b'\n'):
                        break
                    if str(path) in self._partial_baselines:
                        self._partial_baselines.discard(str(path))
                        self._cursors[str(path)] = (stat.st_ino, stream.tell())
                        continue
                    record = json.loads(line)
                    identity = f'omp-journal:{header["id"]}:{stat.st_ino}:{offset}'
                    self._process(header, record, identity)
                    self._cursors[str(path)] = (stat.st_ino, stream.tell())

    def _send(self, header, identity, event):
        payload = {'application': 'OMP', 'source': 'omp', 'session_id': 'omp:' + header['id'],
                   'session_name': header['title'], 'cwd': str(self.project_root),
                   'source_cwd': header.get('cwd'), 'project_root': str(self.project_root),
                   'capture_mode': 'read-only-journal', 'event_id': identity, **event}
        payload, _ = sanitize_event_data(payload)
        self._emit(payload)
        self._last_event = datetime.now(timezone.utc).isoformat()

    def _metadata_patch(self, header, record):
        patch = omp_record_observation(record)
        model = patch.get('model', {})
        if model.get('id'):
            raw = model['id']
            provider = model.get('provider')
            selector = raw if '/' in raw else f'{provider}/{raw}' if provider else raw
            model['id'] = selector
            if provider or '/' in selector:
                model['provider'] = provider or selector.split('/', 1)[0]
            if selector not in self._model_metadata:
                metadata = {}
                executable = shutil.which('omp')
                if executable:
                    try:
                        result = subprocess.run([executable, 'models', 'find', raw.split('/')[-1], '--json', '--no-extensions'],
                            cwd=header.get('cwd') if Path(header.get('cwd') or '').is_dir() else self.project_root,
                            env={**os.environ, 'PI_CODING_AGENT_DIR': str(self.home)},
                            capture_output=True, text=True, timeout=10)
                        data = json.loads(result.stdout) if result.returncode == 0 else {}
                        matches = [item for item in data.get('models', []) if item.get('selector') == selector]
                        if len(matches) == 1:
                            metadata = {key: matches[0].get(key) for key in ('contextWindow', 'provider', 'name')}
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        pass
                if len(self._model_metadata) < 64:
                    self._model_metadata[selector] = metadata
            metadata = self._model_metadata.get(selector, {})
            if metadata.get('contextWindow'):
                model['context_window'] = metadata['contextWindow']
                patch.setdefault('context', {})['window'] = metadata['contextWindow']
        return patch

    def _metadata_baseline(self, path, header, limit=None):
        observation = {'session': {'id': 'omp:' + header['id'], 'name': header.get('title'),
                         'cwd': header.get('cwd'), 'application': 'OMP',
                         'additional_directories': header.get('additionalDirectories') or []}}
        # Inspect bounded metadata only. Conversation text is never emitted or replayed.
        with path.open('rb') as source:
            size = min(source.seek(0, 2), limit) if limit is not None else source.seek(0, 2)
            source.seek(max(0, size - 2 * 1024 * 1024))
            if source.tell():
                source.readline()
            for line in source:
                if source.tell() > size:
                    break
                if not line.endswith(b'\n') or len(line) > MAX_RECORD:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(record, dict):
                    observation = merge_observation(observation, self._metadata_patch(header, record))
        self._send(header, f'omp-metadata:{header["id"]}:{path.stat().st_ino}:{size}',
                   {'type': 'SESSION_OBSERVATION', 'observation': observation})

    def _ensure_run(self, header, identity, prompt=None):
        sid = header['id']
        if sid not in self._runs or prompt is not None:
            run = 'omp:' + sid + ':' + hashlib.sha256(identity.encode()).hexdigest()[:16]
            self._runs[sid] = run
            self._calls[sid] = {}
            self._send(header, identity + ':start', {'type': 'PROMPT_START', 'run_id': run,
                       'prompt': prompt or '', 'observed_mid_turn': prompt is None})
        return self._runs[sid]

    def _process(self, header, record, identity):
        sid = header['id']
        patch = self._metadata_patch(header, record)
        if patch:
            self._send(header, identity + ':metadata', {'type': 'SESSION_OBSERVATION', 'observation': patch})
        if record.get('type') == 'custom' and record.get('customType') == 'tool_execution_start':
            data = record.get('data') or {}
            if not isinstance(data, dict) or not isinstance(data.get('toolName'), str) or not data.get('toolCallId'):
                return
            run = self._ensure_run(header, identity)
            args = data.get('args')
            self._calls[sid][data['toolCallId']] = args
            event = {'type': 'TOOL_CALL_START', 'run_id': run, 'call_id': data['toolCallId'],
                     'tool_name': data['toolName'], 'input': args, 'args': args}
            if isinstance(args, dict):
                for key in ('command', 'path'):
                    if isinstance(args.get(key), str):
                        event[key] = args[key]
            self._send(header, identity, event)
            return
        if record.get('type') != 'message':
            return
        message = record.get('message') or {}
        if not isinstance(message, dict):
            return
        role = message.get('role')
        if role == 'user':
            # Compaction/system notifications are not user requests.
            if message.get('attribution') == 'agent':
                return
            self._ensure_run(header, identity, _text(message.get('content')))
        elif role == 'toolResult':
            run = self._ensure_run(header, identity)
            args = self._calls[sid].get(message.get('toolCallId'))
            output = _text(message.get('content'))
            event = {'type': 'TOOL_CALL_END', 'run_id': run, 'call_id': message.get('toolCallId'),
                     'tool_name': message.get('toolName', ''), 'input': args, 'args': args,
                     'output': output, 'result': output, 'success': not message.get('isError', False)}
            details = message.get('details') or {}
            if isinstance(details, dict):
                for target, keys in [('mcp_server', ('serverName', 'mcpServerName')), ('mcp_tool', ('mcpToolName', 'toolName'))]:
                    value = next((details[key] for key in keys if isinstance(details.get(key), str)), None)
                    if value:
                        event[target] = value
            if isinstance(args, dict):
                for key in ('command', 'path'):
                    if isinstance(args.get(key), str):
                        event[key] = args[key]
            self._send(header, identity, event)
            self._calls[sid].pop(message.get('toolCallId'), None)
        elif role == 'assistant' and sid in self._runs:
            # A committed terminal message is the only journal completion evidence.
            reason = message.get('stopReason')
            if reason not in {'stop', 'error', 'aborted'} or any(isinstance(part, dict) and part.get('type') == 'toolCall' for part in message.get('content', []) if isinstance(message.get('content'), list)):
                return
            if message.get('phase') == 'commentary' or message.get('channel') in {'analysis', 'commentary'}:
                return
            event = {'type': 'GENERATION_END', 'run_id': self._runs[sid], 'result': _text(message.get('content')),
                     'success': reason == 'stop', 'status': 'completed' if reason == 'stop' else 'failed'}
            self._send(header, identity, event)
            self._runs.pop(sid, None)
            self._calls.pop(sid, None)

    def replay_latest(self):
        return {'status': 'unsupported', 'events_replayed': 0,
                'detail': 'OMP journal attachment captures only new records. Replay captured activity from Observer history.'}
