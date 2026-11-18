"""Read-only, project-scoped observation of the existing Codex Desktop and CLI."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import threading
import time
from urllib.parse import unquote, urlsplit

from .agent_events import sanitize_event_data
from .session_observation import codex_record_observation

DESKTOP_APP_PATH = Path('/Applications/ChatGPT.app')
DESKTOP_BUNDLE_ID = 'com.openai.codex'
_NESTED_TOOL_DECLARATION = re.compile(
    r"(?ms)^### `(?P<name>[A-Za-z_]\w*)`\r?\n(?P<description>.*?)"
    r"^exec tool declaration:\s*\r?\n```ts\r?\n"
    r"declare const tools:\s*\{\s*(?P=name)\("
)


def normalize_iso_timestamp(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    if isinstance(value, str) and value.strip():
        return value
    return datetime.now(timezone.utc).isoformat()


def classify_source(source, originator):
    source, originator = str(source or '').lower(), str(originator or '').lower()
    if originator in {'codex_work_desktop', 'codex_desktop'}:
        return 'codex-desktop'
    if source in {'cli', 'terminal', 'exec'} or originator in {'codex_cli', 'codex_exec', 'cli'}:
        return 'codex-cli'
    return source or 'unknown'


def find_codex_desktop_info():
    for candidate in (DESKTOP_APP_PATH, Path('/Applications/Codex.app'),
                      Path.home() / 'Applications/ChatGPT.app', Path.home() / 'Applications/Codex.app'):
        try:
            with (candidate / 'Contents/Info.plist').open('rb') as stream:
                identifier = plistlib.load(stream).get('CFBundleIdentifier')
            if identifier == DESKTOP_BUNDLE_ID:
                return True, candidate, identifier
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    return False, None, None


def find_codex_cli_path():
    _, app, _ = find_codex_desktop_info()
    candidates = [app / 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'] if app else []
    system = shutil.which('codex')
    if system:
        candidates.append(Path(system))
    return next((path for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def _path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value)
    if parsed.scheme == 'file':
        if parsed.netloc not in {'', 'localhost'}:
            return None
        value = unquote(parsed.path)
    elif parsed.scheme:
        return None
    return Path(value).expanduser().resolve()


def is_safe_rollout_path(path, home):
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to((Path(home) / name).resolve()) for name in ('sessions', 'archived_sessions'))

def _codex_database(home):
    files = sorted(Path(home).glob('state_*.sqlite'),
                   key=lambda path: int(path.stem.split('_')[-1]) if path.stem.split('_')[-1].isdigit() else -1,
                   reverse=True)
    return files[0] if files else None


def _matching_threads(project_root, home):
    database = _codex_database(home)
    if database is None:
        return []
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        projects = set()
        if 'project_roots' in tables:
            projects = {str(row['project_id']) for row in connection.execute('SELECT project_id,path FROM project_roots')
                        if _path(row['path']) == project_root}
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(threads)')}
        names = [name for name in ('id', 'rollout_path', 'cwd', 'source', 'originator', 'updated_at', 'project_id', 'history_mode', 'title', 'archived') if name in columns]
        if not {'id', 'rollout_path', 'cwd'}.issubset(names):
            raise ValueError('Unsupported Codex thread index schema')
        ordering = ' ORDER BY updated_at DESC' if 'updated_at' in columns else ''
        matching = []
        for row in connection.execute('SELECT ' + ','.join(names) + ' FROM threads' + ordering):
            data = dict(row)
            cwd = _path(data.get('cwd'))
            if str(data.get('project_id')) in projects or (cwd and cwd.is_relative_to(project_root)):
                matching.append(data)
        return matching


def _empty_inventory():
    return {'source': 'codex-runtime', 'mcp': [], 'skills': [], 'integrations': [], 'native_tools': []}


def _declared_inventory(namespaces):
    inventory = _empty_inventory()
    native = {}
    for namespace in namespaces if isinstance(namespaces, list) else []:
        if not isinstance(namespace, dict) or not isinstance(namespace.get('tools'), list):
            continue
        namespace_name = str(namespace.get('name') or '')
        tools = []
        for tool in namespace['tools']:
            if not isinstance(tool, dict) or not isinstance(tool.get('name'), str) or not tool['name'].strip():
                continue
            name = tool['name'].strip()
            description = tool.get('description') if isinstance(tool.get('description'), str) else ''
            tools.append({'name': name, 'description': description.strip()[:512], 'source': 'codex-schema'})
            if namespace_name.startswith('mcp__'):
                continue
            qualified = name if namespace_name in {'', 'functions', 'collaboration'} or name.startswith(namespace_name + '.') else namespace_name + '.' + name
            native[qualified.casefold()] = {'name': qualified, 'description': description.strip()[:512], 'source': 'codex-schema'}
            for declaration in _NESTED_TOOL_DECLARATION.finditer(description):
                nested = declaration.group('name')
                if nested.startswith('mcp__'):
                    continue
                native[nested.casefold()] = {'name': nested, 'description': declaration.group('description').strip()[:512],
                                            'source': 'codex-nested-schema'}
        if namespace_name.startswith('mcp__'):
            inventory['mcp'].append({'name': namespace_name[5:], 'connection': 'unknown',
                'availability': 'provided', 'source': 'codex-runtime',
                'description': str(namespace.get('description') or '')[:512],
                'tools': tools, 'tool_names': [tool['name'] for tool in tools]})
    inventory['native_tools'] = list(native.values())
    return inventory


def _merge_inventory(target, added):
    for key in ('native_tools', 'mcp'):
        entries = {item['name'].casefold(): item for item in target[key]}
        for item in added[key]:
            name = item['name'].casefold()
            if key == 'mcp' and name in entries:
                tools = {tool['name']: tool for tool in entries[name]['tools']}
                tools.update({tool['name']: tool for tool in item['tools']})
                item = {**item, 'tools': list(tools.values()), 'tool_names': list(tools)}
            entries[name] = item
        target[key] = list(entries.values())


def _read_declared_inventory(path):
    inventory = _empty_inventory()
    with path.open('rb') as stream:
        while line := stream.readline(8 * 1024 * 1024 + 1):
            if len(line) > 8 * 1024 * 1024:
                raise ValueError('Codex source record exceeds the supported event limit')
            if not line.endswith(b'\n'):
                break
            if b'additional_tools' not in line:
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            payload = record.get('payload')
            if record.get('type') == 'response_item' and isinstance(payload, dict) and payload.get('type') == 'additional_tools':
                _merge_inventory(inventory, _declared_inventory(payload.get('tools')))
    return inventory


def read_codex_native_tools(project_root, *, codex_home=None):
    """Read declarations for this project's latest session, never its calls or conversation."""
    home = Path(codex_home or os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()
    try:
        threads = _matching_threads(Path(project_root).resolve(), home)
        if not threads:
            return []
        raw = threads[0].get('rollout_path')
        if not isinstance(raw, str):
            return []
        path = Path(raw)
        if path.suffix in {'.zst', '.gz'} or not is_safe_rollout_path(path, home) or not path.is_file():
            return []
        inventory = _read_declared_inventory(path)
        for tool in inventory['native_tools']:
            tool['source'] = 'codex-session-schema'
        return inventory['native_tools']
    except (OSError, ValueError, sqlite3.Error):
        return []



def extract_explicit_paths(item):
    paths = []
    if item.get('type') == 'CommandExecution':
        cwd = _path(item.get('cwd'))
        if cwd is not None:
            from .codex_stream import _command_read_paths
            return _command_read_paths(item.get('parsed_cmd', []) or [], cwd)
        for action in item.get('parsed_cmd', []) or []:
            if isinstance(action, dict) and action.get('type') == 'read' and isinstance(action.get('path'), str):
                paths.append(action['path'])
    elif item.get('type') == 'FileChange':
        changes = item.get('changes', {})
        paths.extend(changes if isinstance(changes, dict) else [x['path'] for x in changes if isinstance(x, dict) and isinstance(x.get('path'), str)])
    return list(dict.fromkeys(paths))


def make_event_id(thread, generation, offset):
    return f'rollout:{thread}:{generation}:{offset}'


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part['text'] for part in content if isinstance(part, dict) and isinstance(part.get('text'), str))
    return ''


def _committed_end(path):
    with path.open('rb') as stream:
        stream.seek(0, 2)
        end = stream.tell()
        position = end
        while position:
            size = min(position, 65536)
            position -= size
            stream.seek(position)
            block = stream.read(size)
            newline = block.rfind(b'\n')
            if newline >= 0:
                return position + newline + 1
        return 0


def inspect_rollout_turn_state(path):
    if not path or not Path(path).is_file():
        return {
            "has_active_turn": False,
            "has_evidence": False,
            "active_turn_id": None,
            "active_turn_offset": None,
            "last_completed_turn_id": None,
            "last_completed_offset": 0,
            "last_event_timestamp": None,
        }
    path = Path(path)
    active_turn_id = None
    active_turn_offset = None
    turn_completed = True
    last_completed_turn_id = None
    last_completed_offset = 0
    last_event_timestamp = None

    try:
        with path.open('rb') as stream:
            while True:
                line_offset = stream.tell()
                line = stream.readline(8 * 1024 * 1024 + 1)
                if not line:
                    break
                if not line.endswith(b'\n'):
                    break
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                kind = str(record.get('type', ''))
                payload = record.get('payload') if isinstance(record.get('payload'), dict) else {}
                name = str(payload.get('type', kind)) if kind in {'event_msg', 'response_item'} else kind
                turn_id = payload.get('turn_id') or payload.get('root_turn_id')
                timestamp = record.get('timestamp')

                if kind == 'event_msg' and name == 'task_started':
                    turn_completed = False
                    active_turn_id = str(turn_id or 'observed')
                    active_turn_offset = line_offset
                    last_event_timestamp = timestamp
                elif kind == 'event_msg' and name in {'task_complete', 'turn_aborted'}:
                    turn_completed = True
                    last_completed_turn_id = active_turn_id or str(turn_id or 'observed')
                    last_completed_offset = stream.tell()
                    active_turn_id = None
                    active_turn_offset = None
                    last_event_timestamp = timestamp
                elif not turn_completed:
                    last_event_timestamp = timestamp
                else:
                    if turn_id and (kind == 'turn_context' or (kind == 'event_msg' and name in {'item_completed', 'user_message'})):
                        turn_completed = False
                        active_turn_id = str(turn_id)
                        active_turn_offset = line_offset
                        last_event_timestamp = timestamp
    except OSError:
        pass

    has_active_turn = not turn_completed and active_turn_offset is not None
    has_evidence = False
    if has_active_turn:
        try:
            stat = path.stat()
            now = time.time()
            has_evidence = abs(now - stat.st_mtime) <= 1800
        except OSError:
            has_evidence = False

    return {
        "has_active_turn": has_active_turn,
        "has_evidence": has_evidence,
        "active_turn_id": active_turn_id,
        "active_turn_offset": active_turn_offset,
        "last_completed_turn_id": last_completed_turn_id,
        "last_completed_offset": last_completed_offset,
        "last_event_timestamp": last_event_timestamp,
    }

class CodexObserver:
    def __init__(self, project_root, state_dir, emit, *, codex_home=None, poll_interval=0.5):
        self.project_root = Path(project_root).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.codex_home = Path(codex_home or os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()
        self._emit = emit
        self.poll_interval = max(.1, poll_interval)
        self._cursor_file = self.state_dir / 'codex_observer_cursors.json'
        self._inspections = {}
        self._attached_at = None
        self._cursors = {}
        self._turns = {}
        self._current = {}
        self._pending = {}
        self._modern = set()
        self._inventories = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_receipt_time = None
        self._last_receipt_source = None
        self._error = None
        self._unsupported_rollouts = []
        self._replay_id = None

    def _database(self):
        return _codex_database(self.codex_home)

    def _get_matching_threads(self):
        return _matching_threads(self.project_root, self.codex_home)

    def _record_unsupported(self, path, reason):
        item = {'path': str(path), 'reason': reason}
        if item not in self._unsupported_rollouts:
            self._unsupported_rollouts.append(item)

    def _rollout(self, thread):
        raw = thread.get('rollout_path')
        if not isinstance(raw, str):
            return None
        path = Path(raw)
        if not is_safe_rollout_path(path, self.codex_home):
            self._record_unsupported(path, 'Rollout is outside the selected Codex home')
            return None
        if path.suffix in {'.zst', '.gz'}:
            self._record_unsupported(path, 'Compressed archive; live JSONL observation is unavailable for this file')
            return None
        return path if path.is_file() else None

    def _inspect_rollout(self, path):
        stat = Path(path).stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with self._lock:
            cached = self._inspections.get(str(path))
            if cached is None or cached[0] != signature:
                cached = (signature, inspect_rollout_turn_state(path))
                self._inspections[str(path)] = cached
            result = dict(cached[1])
        result["has_evidence"] = result["has_active_turn"] and 0 <= time.time() - stat.st_mtime <= 1800
        return result

    def sessions(self) -> list[dict]:
        """Discover project-matching Codex sessions read-only, reporting activity accurately."""
        try:
            threads = self._get_matching_threads()
        except (OSError, sqlite3.Error, ValueError):
            return []

        unarchived = [t for t in threads if not t.get('archived')]
        archived = [t for t in threads if t.get('archived')]
        candidates = unarchived + archived

        rows = []
        for thread in candidates:
            thread_id = thread['id']
            path = self._rollout(thread)
            source = classify_source(thread.get('source'), thread.get('originator'))
            title = thread.get('title')
            name = str(title).strip() if (title and str(title).strip()) else f"Codex session {thread_id[:8]}"
            cwd = str(_path(thread.get('cwd')) or self.project_root)
            updated = thread.get('updated_at')
            if isinstance(updated, (int, float)) and not isinstance(updated, bool):
                try:
                    updated_iso = datetime.fromtimestamp(updated / 1000 if updated > 1e11 else updated, timezone.utc).isoformat()
                except (OverflowError, ValueError):
                    updated_iso = str(updated)
            else:
                updated_iso = str(updated) if updated else None

            if path is None:
                status, run_id = "unknown", None
            else:
                inspection = self._inspect_rollout(path)
                if inspection["has_active_turn"]:
                    status = "running" if inspection["has_evidence"] else "unknown"
                    turn_id = inspection["active_turn_id"]
                else:
                    status, turn_id = "idle", inspection["last_completed_turn_id"]
                run_id = f"codex:{thread_id}:{turn_id}" if turn_id else None

            rows.append({
                "session_id": thread_id,
                "session_name": name,
                "application": "Codex",
                "status": status,
                "run_id": run_id,
                "cwd": cwd,
                "project_path": str(self.project_root),
                "project_root": str(self.project_root),
                "updated_at": updated_iso,
            })

        rows.sort(key=lambda s: (0 if s["status"] == "running" else 1 if s["status"] != "unknown" else 2))
        return rows

    @classmethod
    def discover_sessions(cls, project_root, *, codex_home=None, state_dir=None) -> list[dict]:
        home = Path(codex_home or os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()
        import tempfile
        temp = cls(project_root, state_dir or Path(tempfile.gettempdir()), lambda e: None, codex_home=home)
        return temp.sessions()
    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._error = None
            self._attached_at = int(time.time())
            try:
                if self._cursor_file.is_file():
                    saved = json.loads(self._cursor_file.read_text())
                    if isinstance(saved, dict):
                        self._cursors = saved
            except (OSError, ValueError):
                self._cursors = {}
            # Offline changes remain recorded history, not live work.
            try:
                if self._database() is None:
                    raise ValueError("Codex session index is unavailable")
                for thread in self._get_matching_threads():
                    path = self._rollout(thread)
                    if path:
                        stat = path.stat()
                        prior = self._cursors.get(thread['id'], {})
                        inspection = self._inspect_rollout(path)
                        if inspection["has_active_turn"] and inspection["has_evidence"]:
                            offset = inspection["active_turn_offset"]
                        else:
                            offset = _committed_end(path)
                        self._cursors[thread['id']] = {'path': str(path), 'inode': stat.st_ino,
                            'file_generation': prior.get('file_generation', 0) + 1,
                            'offset': offset, 'partial_line': ''}
                    if thread.get('history_mode') == 'paginated':
                        self._modern.add(thread['id'])
                self._save_cursors()
            except (OSError, ValueError, sqlite3.Error):
                self._error = 'Codex session storage could not be attached. Open Codex normally, then reconnect.'
                return
            self._thread = threading.Thread(target=self._run_loop, name='codex-project-observer', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        if not thread or not thread.is_alive():
            self._thread = None
            self._save_cursors()

    def _save_cursors(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._cursor_file.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(self._cursors, stream)
        temporary.replace(self._cursor_file)

    def status(self):
        try:
            matching = self._get_matching_threads()
            error = self._error
        except (OSError, sqlite3.Error, ValueError):
            matching, error = [], 'Codex session index could not be read'
        watching = bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())
        state = 'error' if error else ('unavailable' if self._database() is None else 'watching' if watching else 'stopped')
        available, app, identifier = find_codex_desktop_info()
        cli = find_codex_cli_path()
        return {'state': state, 'connection_state': state, 'status': state, 'watching': watching,
                'project_root': str(self.project_root), 'codex_home': str(self.codex_home),
                'matching_thread_count': len(matching), 'last_event_at': self._last_receipt_time,
                'last_received_at': self._last_receipt_time, 'last_receipt_source': self._last_receipt_source,
                'error': error, 'latest_matching_sessions': [{key: row.get(key) for key in ('id', 'updated_at', 'source', 'originator')} for row in matching[:5]],
                'desktop_bundle_available': available, 'desktop_bundle_path': str(app) if app else None,
                'desktop_bundle_id': identifier, 'cli_path': str(cli) if cli else None,
                'unsupported_rollouts': list(self._unsupported_rollouts)}

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    self._poll_once()
                self._error = None
            except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
                self._error = f'Codex observation paused: {type(error).__name__}. Records will retry; refresh the connection if this persists.'
            self._stop_event.wait(self.poll_interval)

    def _poll_once(self):
        for thread in self._get_matching_threads():
            path = self._rollout(thread)
            if path is None:
                continue
            identifier = thread['id']
            if thread.get('history_mode') == 'paginated':
                self._modern.add(identifier)
            stat = path.stat()
            cursor = self._cursors.get(identifier)
            if cursor is None:
                inspection = self._inspect_rollout(path)
                recent = thread.get("updated_at")
                if isinstance(recent, (int, float)) and recent > 1e11:
                    recent /= 1000
                newly_updated = self._attached_at is None or isinstance(recent, (int, float)) and recent >= self._attached_at
                offset = inspection["active_turn_offset"] if (inspection["has_active_turn"] and inspection["has_evidence"]) else 0 if newly_updated else _committed_end(path)
                cursor = self._cursors[identifier] = {'path': str(path), 'inode': stat.st_ino, 'file_generation': 1, 'offset': offset, 'partial_line': ''}
            elif cursor['inode'] != stat.st_ino or stat.st_size < cursor['offset']:
                inspection = self._inspect_rollout(path)
                offset = inspection["active_turn_offset"] if (inspection["has_active_turn"] and inspection["has_evidence"]) else _committed_end(path)
                cursor.update(path=str(path), inode=stat.st_ino, file_generation=cursor['file_generation'] + 1, offset=offset, partial_line='')
            if stat.st_size == cursor['offset']:
                continue
            with path.open('rb') as stream:
                stream.seek(cursor['offset'])
                while not self._stop_event.is_set():
                    offset = stream.tell()
                    line = stream.readline(8 * 1024 * 1024 + 1)
                    if len(line) > 8 * 1024 * 1024:
                        raise ValueError('Codex source record exceeds the supported event limit')
                    if not line or not line.endswith(b'\n'):
                        break  # Re-read incomplete UTF-8 bytes next time; never commit a partial record.
                    if line.strip():
                        record = json.loads(line)
                        self._process_record(thread, record, make_event_id(identifier, cursor['file_generation'], offset))
                    cursor['offset'] = stream.tell()
            self._save_cursors()

    def _emit_event(self, event):
        if event.get('replay') and event.get('run_id'):
            event['run_id'] += ':replay:' + self._replay_id
        if event.get('type') != 'AGENT_EVENT':
            encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, default=str).encode()
            event.setdefault('event_id', 'codex-semantic:' + hashlib.sha256(encoded).hexdigest())
        event['observed_at'] = datetime.now(timezone.utc).isoformat()
        self._emit(event)  # Delivery failure must leave the source cursor uncommitted.
        self._last_receipt_time = datetime.now(timezone.utc).isoformat()
        self._last_receipt_source = event.get('source')

    def _process_record(self, thread, record, event_id, *, replay=False):
        if not isinstance(record, dict):
            raise ValueError('Codex rollout record is not an object')
        payload = record.get('payload') if isinstance(record.get('payload'), dict) else {}
        kind = str(record.get('type', ''))
        name = str(payload.get('type', kind)) if kind in {'event_msg', 'response_item'} else kind
        thread_id = thread['id']
        turn = str(payload.get('turn_id') or payload.get('root_turn_id') or self._current.get(thread_id) or 'observed')
        if kind in {'turn_context', 'event_msg'} and payload.get('turn_id'):
            self._current[thread_id] = turn
        source = classify_source(thread.get('source'), thread.get('originator'))
        meta = {'application': 'Codex', 'source': source, 'session_id': thread_id,
                'run_id': f'codex:{thread_id}:{turn}', 'turn_id': turn, 'cwd': str(self.project_root),
                'timestamp': normalize_iso_timestamp(record.get('timestamp')), 'capture_mode': 'recorded', 'replay': replay}
        private = name.casefold() in {'reasoning', 'thinking', 'analysis', 'agent_reasoning', 'agent_reasoning_raw_content'}
        item = payload.get('item', {}) if isinstance(payload.get('item'), dict) else {}
        private = private or str(item.get('type', '')).casefold() in {'reasoning', 'thinking', 'analysis'}
        private = private or str(payload.get('channel', payload.get('phase', ''))).casefold() == 'analysis'
        private = private or str(item.get('channel', item.get('phase', ''))).casefold() == 'analysis'
        if private:
            return
        if kind == 'session_meta':
            public = {key: payload[key] for key in ('id', 'session_id', 'cwd', 'source', 'originator', 'cli_version', 'history_mode', 'runtime_workspace_roots') if key in payload}
            if payload.get('history_mode') == 'paginated':
                self._modern.add(thread_id)
        elif kind == 'turn_context':
            public = {key: payload[key] for key in ('turn_id', 'model', 'effort', 'cwd') if key in payload}
        elif 'compact' in name.casefold():
            public = {'type': name, 'fields': sorted(payload), 'content_omitted': True}
        elif kind == 'response_item' and name == 'message' and (
                payload.get('role') not in {'user', 'assistant'} or
                payload.get('role') == 'assistant' and payload.get('phase') not in {'commentary', 'final_answer'}):
            public = {'role': payload.get('role'), 'type': name, 'content_omitted': True}
        else:
            public = payload
        public, truncated = sanitize_event_data(public, name=name)
        self._emit_event({**meta, 'type': 'AGENT_EVENT', 'event_id': event_id,
                          'agent_event': {'name': name or 'unknown', 'direction': 'server', 'data': public, 'truncated': truncated}})
        observation = codex_record_observation(record)
        if kind == 'session_meta':
            observation['session'] = {'id': thread_id, 'application': 'Codex', 'cwd': payload.get('cwd')}
        if observation:
            self._emit_event({**meta, 'type': 'SESSION_OBSERVATION', 'event_id': event_id + ':metadata',
                              'observation': observation})
        key = (thread_id, turn)
        info = self._turns.setdefault(key, {'started': False, 'prompt': '', 'final': '', 'calls': set(), 'responses': set(), 'usage': {}})

        def send(event_type, **data):
            clean, _ = sanitize_event_data(data)
            self._emit_event({**meta, 'type': event_type, **clean})

        def begin():
            if not info['started']:
                send('PROMPT_START', prompt=info['prompt'], observed_mid_turn=name != 'task_started')
                info['started'] = True

        def completed(call_id, tool_name, arguments, result, *, success=True, paths=None, server=None, duration_ms=0, **extra):
            call_id = str(call_id or event_id)
            if call_id in info['calls']:
                return
            begin()
            send('TOOL_CALL_END', call_id=call_id, tool_name=tool_name, native_tool=not bool(server),
                 mcp_server=server, input=arguments, result=result, paths=paths or [], success=success,
                 status='completed' if success else 'failed', duration_ms=duration_ms, **extra)
            info['calls'].add(call_id)

        if kind == 'response_item' and name == 'additional_tools':
            inventory = self._inventories.get(thread_id)
            if inventory is None:
                path = self._rollout(thread)
                inventory = _read_declared_inventory(path) if path else _empty_inventory()
                self._inventories[thread_id] = inventory
            _merge_inventory(inventory, _declared_inventory(payload.get('tools')))
            self._emit_event({**meta, 'type': 'RUNTIME_INVENTORY', 'inventory': {**inventory}})
        elif kind == 'event_msg' and name == 'task_started':
            begin()
        elif kind == 'event_msg' and name == 'item_completed':
            self._modern.add(thread_id)
            item_type = item.get('type', '')
            if item_type == 'UserMessage':
                info['prompt'] = _text(item.get('content'))
                begin()
                send('PROMPT_UPDATE', prompt=info['prompt'])
            elif item_type == 'AgentMessage':
                if item.get('phase') == 'final_answer':
                    info['final'] = _text(item.get('content'))
            else:
                started, ended = payload.get('started_at_ms'), payload.get('completed_at_ms')
                duration = max(0, ended - started) if isinstance(started, (float, int)) and isinstance(ended, (float, int)) else 0
                if not duration and isinstance(item.get('duration'), (float, int)):
                    duration = max(0, item['duration'] * 1000)
                success = str(item.get('status', 'completed')).casefold() not in {'failed', 'error', 'declined', 'cancelled'}
                if item_type == 'CommandExecution':
                    command = item.get('command', '')
                    if isinstance(command, list):
                        import shlex
                        command = shlex.join(command)
                    code = item.get('exit_code')
                    paths = extract_explicit_paths(item)
                    completed(item.get('id'), 'exec', {'command': command, 'cwd': item.get('cwd')},
                        {'exit_code': code, 'stdout': item.get('aggregated_output', ''), 'status': item.get('status')},
                        success=success and code in {None, 0}, paths=paths, duration_ms=duration,
                        file_actions=[{'path': path, 'action': 'read'} for path in paths])
                elif item_type == 'McpToolCall':
                    completed(item.get('id'), str(item.get('tool') or 'mcp'), item.get('arguments', {}), item.get('result'),
                        server=item.get('server'), success=success, duration_ms=duration)
                elif item_type == 'FileChange':
                    paths = extract_explicit_paths(item)
                    changes = item.get('changes', {})
                    mutations = [{'path': path, **({key: value for key, value in change.items() if key in {'diff', 'type', 'kind'}} if isinstance(change, dict) else {})} for path, change in changes.items()] if isinstance(changes, dict) else changes
                    completed(item.get('id'), 'apply_patch', {'paths': paths}, {'stdout': item.get('stdout', ''), 'stderr': item.get('stderr', '')},
                        success=success, paths=paths, duration_ms=duration, file_changes=mutations, before_unavailable=True)
                elif item_type == 'Extension':
                    completed(item.get('id'), str(item.get('kind') or 'web_search'), {'query': item.get('query'), 'action': item.get('action')},
                        item.get('results'), success=success, duration_ms=duration)
        elif kind == 'event_msg' and name in {'task_complete', 'turn_aborted'}:
            begin()
            failed = name == 'turn_aborted' or bool(payload.get('error'))
            result = info['final'] or _text(payload.get('last_agent_message'))
            send('GENERATION_END', result=result, success=not failed,
                 status='cancelled' if name == 'turn_aborted' else 'failed' if failed else 'completed', usage=info['usage'])
            self._turns.pop(key, None)
        elif kind == 'turn_context':
            begin()
            send('MODEL_INFO', model=str(payload.get('model') or ''), reasoning_effort=payload.get('effort'))
        elif kind == 'token_usage_record':
            response = payload.get('response_id')
            if response and response in info['responses']:
                return
            cumulative = not response and isinstance(payload.get('turn_token_usage'), dict)
            usage = payload.get('turn_token_usage') if cumulative else payload.get('usage', {})
            if not isinstance(usage, dict):
                return
            next_usage = dict(info['usage'])
            for field, value in usage.items():
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    next_usage[field] = value if cumulative else next_usage.get(field, 0) + value
            begin()
            send('USAGE_UPDATE', response_id=response, model_call_id=response or f'{thread_id}:{turn}:usage-total',
                 usage_kind='token_usage', usage=usage, **usage)
            info['usage'] = next_usage
            if response:
                info['responses'].add(response)
        elif kind == 'response_item' and thread_id not in self._modern:
            call_id = str(payload.get('call_id') or payload.get('id') or event_id)
            if name in {'function_call', 'custom_tool_call'}:
                raw = payload.get('arguments', payload.get('input', {}))
                try:
                    arguments = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    arguments = {'patch': raw} if payload.get('name') == 'apply_patch' else {'command': raw}
                self._pending[(thread_id, call_id)] = (str(payload.get('name') or 'unknown'), arguments)
            elif name in {'function_call_output', 'custom_tool_call_output'}:
                pending = self._pending.get((thread_id, call_id))
                if pending:
                    tool, arguments = pending
                    paths = [arguments[field] for field in ('path', 'file_path') if isinstance(arguments, dict) and isinstance(arguments.get(field), str)]
                    server = None
                    if tool.startswith('mcp__'):
                        pieces = tool.split('__', 2)
                        if len(pieces) == 3:
                            server, tool = pieces[1:]
                    output = payload.get('output')
                    completed(call_id, tool, arguments, output, success=not payload.get('is_error', False), paths=paths, server=server)
                    self._pending.pop((thread_id, call_id), None)
            elif name == 'message':
                if payload.get('role') == 'user':
                    info['prompt'] = _text(payload.get('content'))
                    begin()
                    send('PROMPT_UPDATE', prompt=info['prompt'])
                elif payload.get('role') == 'assistant' and payload.get('phase') == 'final_answer':
                    info['final'] = _text(payload.get('content'))
            elif name == 'web_search_call' and payload.get('status') == 'completed':
                completed(payload.get('id'), 'web_search', payload.get('action', {}), payload.get('results', []))
        elif kind == 'event_msg' and name == 'user_message':
            info['prompt'] = str(payload.get('message') or '')
            begin()
            send('PROMPT_UPDATE', prompt=info['prompt'])
        elif kind == 'event_msg' and name == 'token_count':
            if thread_id in self._modern or info['responses']:
                return  # New Codex reports this same usage again through legacy token_count.
            report = payload.get('info') or {}
            usage = report.get('total_token_usage')
            if isinstance(usage, dict):
                begin()
                baseline = info.setdefault('legacy_usage_baseline', {
                    key: max(0, value - (report.get('last_token_usage') or {}).get(key, value))
                    for key, value in usage.items() if isinstance(value, int) and not isinstance(value, bool)})
                totals = {key: max(0, value - baseline.get(key, 0)) for key, value in usage.items()
                          if isinstance(value, int) and not isinstance(value, bool)}
                send('USAGE_UPDATE', model_call_id=f'{thread_id}:{turn}:legacy-usage-total',
                     usage_kind='token_usage', usage=totals, **totals)

    @contextmanager
    def replay_context(self, identifier=None):
        """Isolate replay identities from live source events without starting a reader."""
        with self._lock:
            self._replay_id = identifier or str(time.time_ns())
            try:
                yield
            finally:
                self._replay_id = None

    def replay_latest(self):
        with self.replay_context():
            for thread in self._get_matching_threads():
                path = self._rollout(thread)
                if path is None:
                    continue
                records, latest, turn = [], None, None
                with path.open('rb') as stream:
                    for line in stream:
                        if not line.endswith(b'\n'):
                            continue
                        record = json.loads(line)
                        data = record.get('payload', {})
                        if record.get('type') == 'event_msg' and data.get('type') == 'task_started':
                            records.clear()
                            turn = data.get('turn_id')
                        records.append(record)
                        if record.get('type') == 'event_msg' and data.get('type') == 'task_complete':
                            latest = (turn or data.get('turn_id'), list(records))
                if latest:
                    turn, records = latest
                    replay_thread = dict(thread)
                    replay_thread['id'] = thread['id']
                    prior_current = dict(self._current)
                    prior_turns = self._turns
                    prior_pending = dict(self._pending)
                    self._turns = {}
                    try:
                        for index, record in enumerate(records):
                            self._process_record(replay_thread, record, make_event_id(thread['id'], 'replay', f'{turn}:{index}'), replay=True)
                    finally:
                        self._turns, self._current = prior_turns, prior_current
                        self._pending = prior_pending
                    return {'status': 'ok', 'thread_id': thread['id'], 'turn_id': turn, 'events_replayed': len(records),
                            'source': classify_source(thread.get('source'), thread.get('originator')),
                            'detail': f'Loaded {len(records)} recorded events. This is history, not live execution.'}
            if self._unsupported_rollouts:
                return {'status': 'unsupported_history', 'events_replayed': 0,
                        'detail': 'Matching history contains unsupported archives. New readable Codex sessions can still be observed.'}
            return {'status': 'no_completed_turns', 'events_replayed': 0, 'detail': 'No completed Codex turn recorded for this project.'}
