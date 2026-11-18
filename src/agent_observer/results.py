"""Observed result files and immutable previews; never scans or executes the project."""
import base64
import binascii
import difflib
import errno
import hashlib
import json
import os
import re
import shlex
import stat
import tempfile
from pathlib import Path
from urllib.parse import quote

MAX_TEXT = 2 * 1024 * 1024
MAX_ARTIFACT = 32 * 1024 * 1024
MAX_CAPTURE = 64 * 1024 * 1024
CODE = {'.py', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.swift', '.scad', '.rs', '.go', '.c', '.h', '.cpp', '.css', '.html', '.sh', '.sql'}
IMAGES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MODELS = {'.stl', '.obj', '.step', '.stp', '.glb', '.gltf'}


class ResultError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


def arguments(event):
    value = event.get('input', event.get('args', {}))
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {'patch': value}
    return value if isinstance(value, dict) else {}


def observed_paths(event):
    """Only explicit mutation paths/output flags, not paths mentioned in prose."""
    args = arguments(event)
    name = str(event.get('tool', event.get('tool_name', ''))).lower().split('.')[-1]
    found = []
    for snapshot in event.get('file_snapshots', []) if isinstance(event.get('file_snapshots'), list) else []:
        if isinstance(snapshot, dict) and snapshot.get('stage') == 'before' and isinstance(snapshot.get('path'), str):
            found.append((snapshot['path'], False))
    if name in {'write', 'edit', 'apply_patch', 'create_file', 'write_file', 'edit_file'}:
        path = args.get('path', args.get('file_path', event.get('path')))
        if isinstance(path, str):
            found.append((path, False))
        changes = args.get('changes', event.get('file_changes', []))
        if event.get('raw_tool') == 'fileChange' and isinstance(changes, list):
            found.extend((change['path'], False) for change in changes if isinstance(change, dict) and isinstance(change.get('path'), str))
            found.extend((change['kind']['move_path'], False) for change in changes if isinstance(change, dict) and isinstance(change.get('kind'), dict) and isinstance(change['kind'].get('move_path'), str))
        patch = args.get('patch', args.get('content', args.get('input', '')))
        if isinstance(patch, str):
            for line in patch.splitlines():
                match = re.match(r'^\[(.+)#[0-9A-Fa-f]{4}\]$', line) or re.match(r'^\*\*\* (?:Update|Add|Delete) File: (.+)$', line)
                if match:
                    found.append((match.group(1), False))
    if name in {'bash', 'shell', 'shell_command', 'exec_command', 'terminal'}:
        command = args.get('command', event.get('command', ''))
        if isinstance(command, str):
            try:
                lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|')
                lexer.whitespace_split = True
                tokens = list(lexer)
                for index, token in enumerate(tokens):
                    if token in {'-o', '--output'} and index + 1 < len(tokens):
                        found.append((tokens[index + 1], True))
                    elif token.startswith('--output='):
                        found.append((token.split('=', 1)[1], True))
            except ValueError:
                pass
    return list(dict.fromkeys(found))


def file_kind(path):
    suffix = Path(path).suffix.lower()
    return 'model' if suffix in MODELS else 'image' if suffix in IMAGES else 'code' if suffix in CODE else 'text'


def is_safe_basename(name):
    if not isinstance(name, str) or not name or not name.strip():
        return False
    if any(ord(c) < 32 for c in name):
        return False
    if '/' in name or '\\' in name or '..' in name or name in {'.', '..'}:
        return False
    return Path(name).name == name


def line_changes(before, after):
    if before is None or after is None:
        return None, None, None
    old, new = before.splitlines(), after.splitlines()
    if len(old) + len(new) > 50000:
        return None, None, None
    patch = list(difflib.unified_diff(old, new, fromfile='Before', tofile='After', lineterm=''))
    return sum(line.startswith('+') for line in patch[2:]), sum(line.startswith('-') for line in patch[2:]), '\n'.join(patch)


class ResultStore:
    def __init__(self, project_root, storage):
        self.root = Path(project_root).resolve()
        self.storage = Path(storage).resolve()

    def relative(self, value):
        if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
            raise ResultError(400, 'Invalid result file path.')
        raw = Path(value).expanduser()
        if '..' in raw.parts or '://' in value:
            raise ResultError(403, 'Result file escapes the project.')
        absolute = raw if raw.is_absolute() else self.root / raw
        try:
            relative = absolute.relative_to(self.root)
        except ValueError:
            raise ResultError(403, 'Result file is outside this project.')
        if not relative.parts or relative.parts[0] in {'.git', '.agent-observer'} or absolute == self.storage or self.storage in absolute.parents:
            raise ResultError(403, 'This private path is not a result file.')
        return relative.as_posix()

    def read_project(self, relative):
        """Walk directory descriptors: symlink swaps cannot escape the root."""
        relative = self.relative(relative)
        descriptors = []
        try:
            descriptors.append(os.open(self.root, os.O_RDONLY | os.O_DIRECTORY))
            parts = Path(relative).parts
            for part in parts[:-1]:
                descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptors[-1]))
            descriptors.append(os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptors[-1]))
            info = os.fstat(descriptors[-1])
            if not stat.S_ISREG(info.st_mode):
                raise ResultError(403, 'Only regular result files can be opened.')
            if info.st_size > MAX_ARTIFACT:
                return {'available': False, 'exists': True, 'reason': 'File exceeds the preview limit.'}
            with os.fdopen(os.dup(descriptors[-1]), 'rb') as stream:
                data = stream.read(MAX_ARTIFACT + 1)
            if len(data) > MAX_ARTIFACT:
                return {'available': False, 'exists': True, 'reason': 'File exceeds the preview limit.'}
            return {'available': True, 'exists': True, 'data': data, 'mtime': info.st_mtime_ns}
        except FileNotFoundError:
            return {'available': True, 'exists': False, 'data': None}
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ResultError(403, 'Symlinks are not result files.')
            return {'available': False, 'exists': None, 'reason': 'File cannot be read safely.'}
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def directory(self, run_id):
        if not isinstance(run_id, str) or not run_id or len(run_id) > 512:
            raise ResultError(400, 'Invalid result request.')
        return self.storage / hashlib.sha256(run_id.encode()).hexdigest()

    def load(self, run_id):
        path = self.directory(run_id) / 'manifest.json'
        try:
            if not path.resolve().is_relative_to(self.storage) or path.is_symlink():
                raise ResultError(403, 'Invalid result storage.')
            with path.open(encoding='utf-8') as stream:
                result = json.load(stream)
        except (OSError, ValueError):
            raise ResultError(404, 'No captured result is available for this request.')
        if result.get('run_id') != run_id:
            raise ResultError(404, 'Result request does not match.')
        return result

    def artifact(self, run_id, file_id, original=False):
        if not re.fullmatch(r'[0-9a-f]{24}', file_id):
            raise ResultError(404, 'Unknown result file.')
        manifest = self.load(run_id)
        record = next((item for item in manifest.get('files', []) if item.get('id') == file_id), None)
        if not record:
            raise ResultError(404, 'This file was not recorded in the result.')
        if original:
            if record.get('attachment') or not record.get('original_url'):
                raise ResultError(404, 'Original project file is not available for attachments.')
            snapshot = self.read_project(record['path'])
            if not snapshot.get('available') or not snapshot.get('exists'):
                raise ResultError(404, snapshot.get('reason', 'The original file no longer exists.'))
            data = snapshot['data']
        else:
            target = self.directory(run_id) / file_id
            try:
                if not target.resolve().is_relative_to(self.storage) or target.is_symlink():
                    raise ResultError(403, 'Invalid result artifact.')
                with target.open('rb') as stream:
                    data = stream.read(MAX_ARTIFACT + 1)
            except OSError:
                raise ResultError(404, 'Captured artifact is unavailable.')
            if len(data) > MAX_ARTIFACT:
                raise ResultError(413, 'Artifact exceeds the preview limit.')
        return record, data
    def attach(self, run_id, files_payload):
        manifest = self.load(run_id)
        if not isinstance(files_payload, list) or not files_payload:
            raise ResultError(400, 'Request must contain a non-empty files list.')
        if len(files_payload) > 128:
            raise ResultError(400, 'Too many attachment files in one request.')

        directory = self.directory(run_id)
        existing_files = manifest.get('files', [])
        existing_names = {item.get('name') for item in existing_files}
        existing_ids = {item.get('id') for item in existing_files}
        incoming_names = set()

        current_bytes = 0
        if directory.is_dir():
            for entry in directory.iterdir():
                if entry.is_file() and entry.name != 'manifest.json' and not entry.name.startswith('.'):
                    try:
                        current_bytes += entry.stat().st_size
                    except OSError:
                        pass

        prepared = []
        batch_bytes = 0
        for item in files_payload:
            if not isinstance(item, dict):
                raise ResultError(400, 'Each attachment item must be an object.')
            name = item.get('name')
            if not is_safe_basename(name):
                raise ResultError(400, f'Invalid or unsafe attachment filename: {name!r}')
            if name in existing_names:
                raise ResultError(409, f'File {name!r} is already recorded in this result.')
            if name in incoming_names:
                raise ResultError(400, f'Duplicate file {name!r} in request.')
            incoming_names.add(name)

            file_id = hashlib.sha256(name.encode()).hexdigest()[:24]
            if file_id in existing_ids:
                raise ResultError(409, f'File ID for {name!r} already exists in this result.')
            if (directory / file_id).exists():
                raise ResultError(409, f'Captured file for {name!r} already exists.')

            has_b64 = 'content_base64' in item
            has_text = 'content' in item
            if not has_b64 and not has_text:
                raise ResultError(400, f'Attachment {name!r} must have content or content_base64.')
            if has_b64 and has_text:
                raise ResultError(400, f'Attachment {name!r} cannot provide both content and content_base64.')

            if has_b64:
                raw_b64 = item['content_base64']
                if not isinstance(raw_b64, str):
                    raise ResultError(400, f'Attachment {name!r} content_base64 must be a string.')
                try:
                    data = base64.b64decode(raw_b64, validate=True)
                except (binascii.Error, ValueError):
                    raise ResultError(400, f'Invalid base64 payload for {name!r}.')
            else:
                raw_text = item['content']
                if not isinstance(raw_text, str):
                    raise ResultError(400, f'Attachment {name!r} content must be a string.')
                data = raw_text.encode('utf-8')

            kind = file_kind(name)
            if kind in {'code', 'text'} and len(data) > MAX_TEXT:
                raise ResultError(413, f'Code/text attachment {name!r} exceeds the {MAX_TEXT} byte limit.')
            if len(data) > MAX_ARTIFACT:
                raise ResultError(413, f'Artifact {name!r} exceeds the {MAX_ARTIFACT} byte limit.')

            batch_bytes += len(data)
            if current_bytes + batch_bytes > MAX_CAPTURE:
                raise ResultError(413, 'Total captured artifacts exceed the 64 MiB limit.')

            before_field = item.get('before')
            before_state = None
            if before_field is not None:
                if not isinstance(before_field, dict):
                    raise ResultError(400, f'Invalid before specification for {name!r}.')
                exists = before_field.get('exists')
                if exists is False:
                    before_state = {'exists': False, 'content': ''}
                elif exists is True:
                    b_content = before_field.get('content')
                    if not isinstance(b_content, str):
                        raise ResultError(400, f'Invalid before content for {name!r}.')
                    if len(b_content.encode('utf-8')) > MAX_TEXT:
                        raise ResultError(413, f'Before content for {name!r} exceeds the text limit.')
                    before_state = {'exists': True, 'content': b_content}
                else:
                    raise ResultError(400, f'Invalid before.exists for {name!r}.')

            prepared.append({
                'name': name,
                'file_id': file_id,
                'data': data,
                'kind': kind,
                'before_state': before_state,
            })

        written_files = []
        try:
            new_records = []
            for prep in prepared:
                file_id = prep['file_id']
                name = prep['name']
                data = prep['data']
                kind = prep['kind']
                before_state = prep['before_state']

                target = directory / file_id
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(data)
                written_files.append(target)

                url = f'/api/results/{quote(run_id, safe="")}/files/{file_id}'
                record = {
                    'id': file_id,
                    'path': name,
                    'name': name,
                    'kind': kind,
                    'change': 'output',
                    'added': None,
                    'removed': None,
                    'before': None,
                    'after': None,
                    'before_available': False,
                    'after_available': True,
                    'original_url': None,
                    'artifact_url': url,
                    'artifact_version': 'after',
                    'attachment': True,
                    'success': True,
                }

                if kind not in {'model', 'image'} and len(data) <= MAX_TEXT and b'\0' not in data:
                    try:
                        record['after'] = data.decode('utf-8')
                    except UnicodeDecodeError:
                        record['kind'] = 'file'

                if record.get('after') is not None and before_state is not None:
                    record['before_available'] = True
                    if before_state['exists'] is False:
                        record['before'] = ''
                        record['change'] = 'added'
                        record['added'], record['removed'], record['diff'] = line_changes('', record['after'])
                    else:
                        record['before'] = before_state['content']
                        record['change'] = 'modified'
                        record['added'], record['removed'], record['diff'] = line_changes(record['before'], record['after'])

                record['preview_text'] = (record.get('after') or record.get('before') or '')[:1600]

                suffix = Path(name).suffix.lower()
                if suffix in {'.stl', '.obj'} or suffix in IMAGES:
                    record['preview'] = {'format': suffix[1:], 'url': url}

                new_records.append(record)

            all_files = list(manifest.get('files', [])) + new_records
            for item in all_files:
                if item.get('kind') == 'model' and not item.get('preview'):
                    companion = next((other for other in all_files if Path(other['path']).with_suffix('') == Path(item['path']).with_suffix('') and other.get('preview')), None)
                    if companion:
                        item['preview'] = companion['preview']

            manifest['files'] = all_files
            descriptor, temporary = tempfile.mkstemp(dir=directory, prefix='.manifest-')
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                    json.dump(manifest, stream, ensure_ascii=False)
                os.replace(temporary, directory / 'manifest.json')
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

            summary = {**manifest, 'text': manifest['text'][:2400], 'files': [{key: value for key, value in rec.items() if key not in {'before', 'after', 'diff'}} for rec in manifest['files']]}
            summary['revision'] = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:20]
            return summary
        except Exception:
            for wf in written_files:
                try:
                    wf.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def begin(self, run_id):
        if (self.directory(run_id) / 'manifest.json').exists():
            raise ResultError(409, 'This request already has an immutable result. Use a new request ID.')
        return CapturedResult(self, run_id)


class CapturedResult:
    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id
        self.pending, self.files, self.baselines = {}, {}, {}
        self.bytes, self.baseline_bytes, self.artifact_sizes = 0, 0, {}
        self.summary = None

    def supplied(self, event, path, stage):
        for record in event.get('file_snapshots', []) if isinstance(event.get('file_snapshots'), list) else []:
            if not isinstance(record, dict) or not isinstance(record.get('path'), str) or record.get('stage') != stage:
                continue
            try:
                matches = self.store.relative(record['path']) == self.store.relative(path)
            except ResultError:
                continue
            if matches:
                content = record.get('content')
                if record.get('exists') is False:
                    return {'available': True, 'exists': False, 'data': None}
                if isinstance(content, str) and len(content.encode()) <= MAX_TEXT:
                    return {'available': True, 'exists': True, 'data': content.encode()}
        return None

    def observe(self, event):
        if self.summary is not None:
            return
        kind = event.get('type')
        call_id = str(event.get('call_id', event.get('tool_call_id', ''))) or 'tool:' + str(event.get('tool', event.get('tool_name', '')))
        if kind == 'TOOL_CALL_START':
            records = []
            for raw, output in observed_paths(event):
                try:
                    path = self.store.relative(raw)
                    supplied = self.supplied(event, raw, 'before')
                    before = supplied or ({'available': False, 'reason': 'Before-state was not exposed by the Codex stream.'} if event.get('raw_tool') == 'fileChange' else self.store.read_project(path))
                except ResultError:
                    continue
                if path not in self.baselines:
                    size = len(before.get('data') or b'')
                    if self.baseline_bytes + size > MAX_CAPTURE:
                        before = {'available': False, 'exists': before.get('exists'), 'reason': 'Before-capture limit reached.'}
                    else:
                        self.baseline_bytes += size
                    self.baselines[path] = before
                records.append((raw, path, output, {'mtime': before.get('mtime')}))
            if records:
                self.pending.setdefault(call_id, []).append(records)
        elif kind == 'TOOL_CALL_END':
            success = event.get('success', True) not in (False, 'false', 'error', 'failed')
            queue = self.pending.get(call_id, [])
            records = queue.pop(0) if queue else []
            if not queue:
                self.pending.pop(call_id, None)
            for raw, path, output, before_call in records:
                try:
                    after = self.supplied(event, raw, 'after') or self.store.read_project(path)
                except ResultError:
                    self.files.pop(path, None)
                    continue
                if not self.baselines[path].get('available'):
                    supplied = self.supplied(event, raw, 'before')
                    if supplied:
                        self.baselines[path] = supplied
                before = self.baselines[path]
                changed = before.get('available') and after.get('available') and before.get('data') != after.get('data')
                produced = output and success and after.get('exists') and (changed or before_call.get('mtime') != after.get('mtime'))
                if changed or produced or success and not output and not before.get('available'):
                    self.capture(path, before, after, output, success)
                elif path in self.files and before.get('available') and after.get('available') and before.get('data') == after.get('data') and not output:
                    removed = self.files.pop(path, None)
                    if removed and removed.get('artifact_url'):
                        (self.store.directory(self.run_id) / removed['id']).unlink(missing_ok=True)
                        self.bytes -= self.artifact_sizes.pop(path, 0)

    def capture(self, path, before, after, output=False, success=True):
        file_id = hashlib.sha256(path.encode()).hexdigest()[:24]
        data = before.get('data') if after.get('exists') is False else after.get('data')
        kind = file_kind(path)
        record = {'id': file_id, 'path': path, 'name': Path(path).name, 'kind': kind,
                  'change': 'deleted' if after.get('exists') is False else 'added' if before.get('available') and before.get('exists') is False else 'output' if output else 'modified',
                  'added': None, 'removed': None, 'before': None, 'after': None,
                  'before_available': bool(before.get('available')), 'after_available': bool(after.get('available')),
                  'original_url': f'/api/results/{quote(self.run_id, safe="")}/files/{file_id}?original=1' if after.get('exists') else None}
        for stage, snapshot in [('before', before), ('after', after)]:
            content = snapshot.get('data')
            if snapshot.get('available') and snapshot.get('exists') is False:
                record[stage] = ''
            elif isinstance(content, bytes) and len(content) <= MAX_TEXT and b'\0' not in content and kind not in {'model', 'image'}:
                try:
                    record[stage] = content.decode('utf-8')
                except UnicodeDecodeError:
                    record['kind'] = 'file'
        if record['before'] is not None and record['after'] is not None:
            record['added'], record['removed'], record['diff'] = line_changes(record['before'], record['after'])
        record['preview_text'] = (record.get('after') or record.get('before') or '')[:1600]
        previous_size = self.artifact_sizes.get(path, 0)
        if self.bytes - previous_size + len(data or b'') <= MAX_CAPTURE and data is not None:
            directory = self.store.directory(self.run_id)
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            target = directory / file_id
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
            self.bytes += len(data) - previous_size
            self.artifact_sizes[path] = len(data)
            url = f'/api/results/{quote(self.run_id, safe="")}/files/{file_id}'
            record['artifact_url'] = url
            record['artifact_version'] = 'before' if after.get('exists') is False else 'after'
            suffix = Path(path).suffix.lower()
            if suffix in {'.stl', '.obj'} or suffix in IMAGES:
                record['preview'] = {'format': suffix[1:], 'url': url}
        else:
            if previous_size:
                (self.store.directory(self.run_id) / file_id).unlink(missing_ok=True)
                self.bytes -= self.artifact_sizes.pop(path, 0)
            record['notice'] = after.get('reason') or before.get('reason') or 'Preview capture limit reached.'
        record['success'] = success
        self.files[path] = record

    def finish(self, text, status, event=None):
        if self.summary is not None:
            return self.summary
        event = event or {}
        artifacts = event.get('artifacts', event.get('output_files', []))
        if isinstance(artifacts, list):
            for item in artifacts[:128]:
                raw = item.get('path') if isinstance(item, dict) else item
                try:
                    path = self.store.relative(raw)
                    if path not in self.files:
                        after = self.store.read_project(path)
                        if after.get('available') and after.get('exists'):
                            self.capture(path, {'available': False, 'exists': None}, after, True)
                except ResultError:
                    continue
        for item in self.files.values():
            if item['kind'] == 'model' and not item.get('preview'):
                companion = next((other for other in self.files.values() if Path(other['path']).with_suffix('') == Path(item['path']).with_suffix('') and other.get('preview')), None)
                if companion:
                    item['preview'] = companion['preview']
        manifest = {'run_id': self.run_id, 'status': status, 'text': str(text or ''), 'files': list(self.files.values())}
        directory = self.store.directory(self.run_id)
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix='.manifest-')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(manifest, stream, ensure_ascii=False)
            os.replace(temporary, directory / 'manifest.json')
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        summary = {**manifest, 'text': manifest['text'][:2400], 'files': [{key: value for key, value in record.items() if key not in {'before', 'after', 'diff'}} for record in manifest['files']]}
        summary['revision'] = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:20]
        self.summary = summary
        self.pending.clear()
        self.baselines.clear()
        self.files.clear()
        return summary
