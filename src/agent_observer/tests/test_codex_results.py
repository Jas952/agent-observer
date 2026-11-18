"""Consumer-visible results from asynchronous native Codex notifications."""
from pathlib import Path
import tempfile
import unittest

from agent_observer.codex_stream import CodexNormalizer
from agent_observer.results import ResultStore


class CodexResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = []
        self.capture = ResultStore(self.root, self.root / 'private').begin('codex-result')
        def emit(event):
            self.events.append(event)
            if event['type'] in {'TOOL_CALL_START', 'TOOL_CALL_END'}:
                self.capture.observe(event)
            return True
        self.normalizer = CodexNormalizer(project_root=self.root, emitter=emit)

    def tearDown(self):
        self.temp.cleanup()

    def message(self, method, item):
        self.normalizer.observe_server_message({'method': method, 'params': {'threadId': 'thread', 'turnId': 'turn', 'item': item}})

    def test_late_file_notifications_preserve_dirty_before_and_all_changed_files(self):
        # Notifications may arrive after the files have already been changed. Git is not the baseline.
        first, second = self.root / 'answer.py', self.root / 'result.md'
        first.write_text('answer = 42\n')
        second.write_text('# Result\n42\n')
        changes = [
            {'path': str(first), 'kind': {'type': 'update'}, 'diff': '@@ -1 +1 @@\n-answer = 41\n+answer = 42\n'},
            {'path': str(second), 'kind': {'type': 'add'}, 'diff': '# Result\n42\n'},
        ]
        item = {'id': 'patch', 'type': 'fileChange', 'changes': changes}
        self.message('item/started', item)
        self.message('item/completed', {**item, 'status': 'completed'})
        files = self.capture.files
        self.assertEqual(set(files), {'answer.py', 'result.md'})
        self.assertEqual((files['answer.py']['before'], files['answer.py']['after'], files['answer.py']['added'], files['answer.py']['removed']), ('answer = 41\n', 'answer = 42\n', 1, 1))
        self.assertEqual((files['result.md']['change'], files['result.md']['added'], files['result.md']['removed']), ('added', 2, 0))

    def test_nonmatching_after_state_never_fabricates_a_before_snapshot(self):
        target = self.root / 'answer.py'
        target.write_text('answer = 99\n')
        item = {'id': 'conflicting-patch', 'type': 'fileChange', 'changes': [{'path': str(target), 'kind': {'type': 'update'}, 'diff': '@@ -1 +1 @@\n-answer = 41\n+answer = 42\n'}]}
        self.message('item/started', item)
        self.message('item/completed', {**item, 'status': 'completed'})
        file = self.capture.files['answer.py']
        self.assertEqual(file['after'], 'answer = 99\n')
        self.assertEqual((file['before_available'], file['before'], file['added'], file['removed']), (False, None, None, None))

    def test_identical_usage_attempts_are_counted_but_duplicate_notifications_are_not(self):
        last = {'inputTokens': 100, 'outputTokens': 25, 'totalTokens': 125}
        for total in (125, 125, 250):
            self.normalizer.observe_server_message({'method': 'thread/tokenUsage/updated', 'params': {'threadId': 'thread', 'turnId': 'turn', 'tokenUsage': {'last': last, 'total': {'totalTokens': total}}}})
        usage = [event for event in self.events if event.get('usage_kind') == 'token_usage']
        self.assertEqual((sum(event['input_tokens'] for event in usage), sum(event['output_tokens'] for event in usage)), (200, 50))
        self.assertEqual(len({event['model_call_id'] for event in usage}), 2)

    def test_final_answer_wins_over_late_commentary_and_cancel_is_not_success(self):
        self.message('item/completed', {'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'The changed files are ready.'})
        self.message('item/completed', {'id': 'comment', 'type': 'agentMessage', 'phase': 'commentary', 'text': 'Late progress update.'})
        self.normalizer.observe_server_message({'method': 'turn/completed', 'params': {'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'completed'}}})
        result = [event for event in self.events if event['type'] == 'GENERATION_END'][-1]
        self.assertEqual((result['result'], result['success']), ('The changed files are ready.', True))
        self.normalizer.observe_server_message({'method': 'turn/completed', 'params': {'threadId': 'thread', 'turn': {'id': 'cancelled', 'status': 'interrupted'}}})
        result = [event for event in self.events if event['type'] == 'GENERATION_END'][-1]
        self.assertEqual((result['status'], result['success']), ('interrupted', False))


if __name__ == '__main__':
    unittest.main()
