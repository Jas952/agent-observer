import tempfile
import base64
import unittest
import os
import subprocess
import sys
from pathlib import Path
from agent_observer.results import ResultStore, ResultError


class ResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ResultStore(self.root, self.root / '.captures')
        self.capture = self.store.begin('run-one')

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Named pipes require POSIX")
    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        os.mkfifo(self.root / "capture.pipe")
        probe = """
import sys
from agent_observer.results import ResultStore, ResultError
try:
    ResultStore(sys.argv[1], sys.argv[2]).read_project("capture.pipe")
except ResultError as error:
    assert error.status == 403
else:
    raise AssertionError("A named pipe must not become a result file")
"""
        subprocess.run(
            [sys.executable, "-c", probe, str(self.root), str(self.store.storage)],
            check=True, capture_output=True, timeout=5,
        )

    def event(self, kind, path='model.scad', call='one', **fields):
        self.capture.observe({'type': kind, 'tool': 'edit', 'call_id': call, 'input': {'path': path}, **fields})

    def test_net_changes_keep_original_baseline_and_immutable_after(self):
        file = self.root / 'model.scad'
        file.write_text('size=10;\ncube(size);\n')
        self.event('TOOL_CALL_START')
        file.write_text('size=12;\ncube(size);\n')
        self.event('TOOL_CALL_END')
        self.event('TOOL_CALL_START', call='two')
        file.write_text('size=14;\ncube(size);\n// finished\n')
        self.event('TOOL_CALL_END', call='two')
        summary = self.capture.finish('Published response', 'completed')
        record = self.store.load('run-one')['files'][0]
        self.assertEqual((record['added'], record['removed']), (2, 1))
        self.assertEqual(record['before'], 'size=10;\ncube(size);\n')
        self.assertNotIn('before', summary['files'][0])
        file.write_text('changed in another request')
        self.assertEqual(self.store.artifact('run-one', record['id'])[1], b'size=14;\ncube(size);\n// finished\n')
        self.assertEqual(self.store.artifact('run-one', record['id'], True)[1], b'changed in another request')
        with self.assertRaises(ResultError):
            self.store.begin('run-one')

    def test_reverted_file_is_not_a_final_change(self):
        file = self.root / 'model.scad'
        file.write_text('original')
        self.event('TOOL_CALL_START'); file.write_text('temporary'); self.event('TOOL_CALL_END')
        self.event('TOOL_CALL_START', call='two'); file.write_text('original'); self.event('TOOL_CALL_END', call='two')
        self.assertEqual(self.capture.finish('', 'completed')['files'], [])

    def test_adapter_baseline_wins_over_late_server_read_and_deleted_file_opens_before(self):
        file = self.root / 'model.scad'
        file.write_text('after')
        self.event('TOOL_CALL_START', file_snapshots=[{'path': 'model.scad', 'stage': 'before', 'exists': True, 'content': 'before\n'}])
        file.unlink()
        self.event('TOOL_CALL_END')
        record = self.capture.finish('', 'completed')['files'][0]
        self.assertEqual((record['added'], record['removed'], record['change']), (0, 1, 'deleted'))
        self.assertIsNone(record['original_url'])
        self.assertEqual(self.store.artifact('run-one', record['id'])[1], b'before\n')

    def test_no_call_id_pairs_fifo_without_crossing_tools(self):
        self.event('TOOL_CALL_START', path='a.py', call='')
        self.event('TOOL_CALL_START', path='b.py', call='')
        (self.root / 'a.py').write_text('a=1\n')
        self.event('TOOL_CALL_END', path='a.py', call='')
        (self.root / 'b.py').write_text('b=2\n')
        self.event('TOOL_CALL_END', path='b.py', call='')
        self.assertEqual({item['path']: item['added'] for item in self.capture.finish('', 'completed')['files']}, {'a.py': 1, 'b.py': 1})

    def test_external_symlink_and_unobserved_artifact_cannot_be_opened(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / 'secret.txt'; secret.write_text('private')
            (self.root / 'link').symlink_to(outside, target_is_directory=True)
            self.event('TOOL_CALL_START', path='link/secret.txt')
            self.event('TOOL_CALL_END', path='link/secret.txt')
            self.capture.finish('', 'completed', {'artifacts': ['link/secret.txt', str(secret)]})
            with self.assertRaises(ResultError):
                self.store.artifact('run-one', 'a' * 24, True)
            self.assertEqual(self.store.load('run-one')['files'], [])

    def test_explicit_binary_model_output_is_preserved_not_fabricated(self):
        data = bytes(range(256))
        (self.root / 'part.stl').write_bytes(data)
        record = self.capture.finish('', 'completed', {'artifacts': ['part.stl']})['files'][0]
        self.assertIsNone(record['added'])
        self.assertEqual(record['preview']['format'], 'stl')
        self.assertEqual(self.store.artifact('run-one', record['id'])[1], data)

    def test_late_tool_events_cannot_rewrite_a_finished_result(self):
        file = self.root / 'model.scad'
        self.event('TOOL_CALL_START'); file.write_text('first'); self.event('TOOL_CALL_END')
        summary = self.capture.finish('Original response', 'completed')
        self.event('TOOL_CALL_START', call='late'); file.write_text('late'); self.event('TOOL_CALL_END', call='late')
        self.assertEqual(self.capture.finish('Later response', 'completed')['text'], 'Original response')
        self.assertEqual(self.store.artifact('run-one', summary['files'][0]['id'])[1], b'first')

    def test_explicit_snapshots_support_mcp_mutations_without_guessing_the_tool_name(self):
        file = self.root / 'model.scad'
        file.write_text('after\n')
        self.capture.observe({'type': 'TOOL_CALL_START', 'tool': 'mcp__files__change', 'call_id': 'mcp',
                              'file_snapshots': [{'path': 'model.scad', 'stage': 'before', 'content': 'before\n', 'exists': True}]})
        self.capture.observe({'type': 'TOOL_CALL_END', 'tool': 'mcp__files__change', 'call_id': 'mcp', 'success': True})
        record = self.capture.finish('', 'completed')['files'][0]
        self.assertEqual((record['path'], record['added'], record['removed']), ('model.scad', 1, 1))

    def test_attach_valid_model_and_code_with_baseline(self):
        self.capture.finish('Original generation complete', 'completed')
        stl_bytes = b'solid test\nfacet normal 0 0 0\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid'
        scad_text = 'size=20;\ncube(size);\n'
        before_text = 'size=10;\ncube(size);\n'
        files = [
            {'name': 'model.stl', 'content_base64': base64.b64encode(stl_bytes).decode()},
            {'name': 'model.scad', 'content': scad_text, 'before': {'exists': True, 'content': before_text}}
        ]
        summary = self.store.attach('run-one', files)
        self.assertEqual(len(summary['files']), 2)
        stl_rec = next(f for f in summary['files'] if f['name'] == 'model.stl')
        scad_rec = next(f for f in summary['files'] if f['name'] == 'model.scad')
        self.assertEqual(stl_rec['kind'], 'model')
        self.assertEqual(stl_rec['preview']['format'], 'stl')
        self.assertIsNone(stl_rec['original_url'])
        self.assertEqual(self.store.artifact('run-one', stl_rec['id'])[1], stl_bytes)

        # ?original=1 must reject uploaded attachments even if matching file exists in project
        (self.root / 'model.stl').write_bytes(b'unrelated project content')
        with self.assertRaises(ResultError) as ctx:
            self.store.artifact('run-one', stl_rec['id'], original=True)
        self.assertEqual(ctx.exception.status, 404)

        # Check diff and line counts
        manifest = self.store.load('run-one')
        scad_manifest = next(f for f in manifest['files'] if f['name'] == 'model.scad')
        self.assertEqual(scad_manifest['before'], before_text)
        self.assertEqual(scad_manifest['after'], scad_text)
        self.assertEqual(scad_manifest['added'], 1)
        self.assertEqual(scad_manifest['removed'], 1)
        self.assertIn('+size=20;', scad_manifest['diff'])
        self.assertIsNone(scad_manifest['original_url'])

    def test_attach_unknown_baseline_and_known_new_file(self):
        self.capture.finish('', 'completed')
        files = [
            {'name': 'unknown.py', 'content': 'print("hello")\nprint("world")\n'},
            {'name': 'new.py', 'content': 'a=1\nb=2\n', 'before': {'exists': False}}
        ]
        summary = self.store.attach('run-one', files)
        unknown_rec = next(f for f in summary['files'] if f['name'] == 'unknown.py')
        new_rec = next(f for f in summary['files'] if f['name'] == 'new.py')
        self.assertIsNone(unknown_rec['added'])
        self.assertIsNone(unknown_rec['removed'])
        self.assertEqual(unknown_rec['change'], 'output')
        self.assertFalse(unknown_rec['before_available'])

        self.assertEqual(new_rec['change'], 'added')
        self.assertTrue(new_rec['before_available'])
        self.assertEqual(new_rec['added'], 2)
        self.assertEqual(new_rec['removed'], 0)

    def test_attach_companion_preview_for_models(self):
        (self.root / 'gear.step').write_text('ISO-10303-21;\n')
        self.capture.finish('', 'completed', {'artifacts': ['gear.step']})
        step_rec = self.store.load('run-one')['files'][0]
        self.assertIsNone(step_rec.get('preview'))

        stl_bytes = b'solid gear\nfacet normal 0 0 0\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid'
        summary = self.store.attach('run-one', [{'name': 'gear.stl', 'content_base64': base64.b64encode(stl_bytes).decode()}])
        updated_step = next(f for f in summary['files'] if f['name'] == 'gear.step')
        stl_rec = next(f for f in summary['files'] if f['name'] == 'gear.stl')
        self.assertIsNotNone(updated_step.get('preview'))
        self.assertEqual(updated_step['preview']['url'], stl_rec['preview']['url'])

    def test_attach_rejects_unsafe_paths_and_directory_traversal(self):
        self.capture.finish('', 'completed')
        for bad in ['../escaped.scad', '/tmp/root.scad', 'sub/model.scad', '', '   ', 'nul\x00l.scad']:
            with self.assertRaises(ResultError) as ctx:
                self.store.attach('run-one', [{'name': bad, 'content': 'cube();'}])
            self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.store.load('run-one')['files'], [])

    def test_attach_rejects_duplicates_and_overwrites(self):
        self.capture.finish('', 'completed')
        self.store.attach('run-one', [{'name': 'model.stl', 'content_base64': base64.b64encode(b'original').decode()}])
        # Overwriting existing file in result must be rejected
        with self.assertRaises(ResultError) as ctx:
            self.store.attach('run-one', [{'name': 'model.stl', 'content_base64': base64.b64encode(b'overwrite').decode()}])
        self.assertEqual(ctx.exception.status, 409)
        # Duplicates within same batch must be rejected
        with self.assertRaises(ResultError) as ctx:
            self.store.attach('run-one', [
                {'name': 'dup.scad', 'content': 'a=1;'},
                {'name': 'dup.scad', 'content': 'a=2;'}
            ])
        self.assertEqual(ctx.exception.status, 400)
        manifest = self.store.load('run-one')
        self.assertEqual(len(manifest['files']), 1)
        self.assertEqual(self.store.artifact('run-one', manifest['files'][0]['id'])[1], b'original')

    def test_attach_rejects_malformed_base64(self):
        self.capture.finish('', 'completed')
        with self.assertRaises(ResultError) as ctx:
            self.store.attach('run-one', [{'name': 'bad.stl', 'content_base64': '!!!not base64!!!'}])
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.store.load('run-one')['files'], [])

    def test_attach_rejects_size_overruns(self):
        from agent_observer.results import MAX_TEXT
        self.capture.finish('', 'completed')
        with self.assertRaises(ResultError) as ctx:
            self.store.attach('run-one', [{'name': 'huge.scad', 'content': 'x' * (MAX_TEXT + 1)}])
        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(self.store.load('run-one')['files'], [])

    def test_attach_requires_existing_completed_result(self):
        with self.assertRaises(ResultError) as ctx:
            self.store.attach('non-existent-run', [{'name': 'part.stl', 'content_base64': base64.b64encode(b'data').decode()}])
        self.assertEqual(ctx.exception.status, 404)
