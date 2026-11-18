"""Source-event completeness, isolation, and private payload protection."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_observer.agent_events import AgentEventJournal


class AgentEventJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'events.sqlite3'
        self.journal = AgentEventJournal(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def event(self, number, *, session='thread-a', application='Codex', name='future/nativeTool', data=None):
        return {'type': 'AGENT_EVENT', 'application': application, 'session_id': session,
                'event_id': f'event-{number}', 'run_id': f'{session}:turn',
                'agent_event': {'name': name, 'direction': 'server',
                                'data': data if data is not None else {'ordinal': number}}}

    def test_all_pages_survive_reopen_without_graph_history_limit(self):
        for number in range(75):
            self.journal.append(self.event(number))
        reopened = AgentEventJournal(self.path)
        cursor, observed = 0, []
        while True:
            page = reopened.read(session_id='thread-a', after=cursor, limit=11)
            observed.extend((event['event_id'], event['agent_event']['data']['ordinal']) for event in page['events'])
            cursor = page['next_cursor']
            if not page['has_more']:
                break
        self.assertEqual(observed, [(f'event-{number}', number) for number in range(75)])
        self.assertEqual(reopened.read(session_id='thread-a', after=cursor)['events'], [])

    def test_duplicate_delivery_does_not_merge_distinct_sessions_or_agents(self):
        original = self.journal.append(self.event(1))
        duplicate = self.journal.append(self.event(1, data={'ordinal': 'duplicate'}))
        self.journal.append(self.event(1, session='thread-b'))
        self.journal.append(self.event(1, application='Custom'))
        self.assertEqual((original['inserted'], duplicate['inserted'], original['seq'] == duplicate['seq']), (True, False, True))
        self.assertEqual([event['agent_event']['data'] for event in self.journal.read(session_id='thread-a')['events']], [{'ordinal': 1}])
        self.assertEqual([event['session_id'] for event in self.journal.read()['events']], ['thread-a', 'thread-b'])
        self.assertEqual([event['application'] for event in self.journal.read(application='Custom')['events']], ['Custom'])

    def test_credentials_and_private_reasoning_never_reach_persisted_payload(self):
        self.journal.append(self.event(1, data={
            'authorization': 'Bearer private-auth-value',
            'nested': {'chain_of_thought': 'private-chain-value', 'api_key': 'private-api-value'},
            'items': [{'id': 'reasoning-1', 'type': 'reasoning', 'summary': ['private-summary-value']}],
            'usage': {'inputTokens': 100, 'outputTokens': 25},
            'message': 'public result',
        }))
        self.journal.append(self.event(2, name='item/reasoning/textDelta', data={'itemId': 'reasoning-1', 'delta': 'private-delta-value'}))
        payload = self.journal.read()['events']
        serialized = json.dumps(payload)
        for secret in ['private-auth-value', 'private-chain-value', 'private-api-value', 'private-summary-value', 'private-delta-value']:
            self.assertNotIn(secret, serialized)
        self.assertEqual(payload[0]['agent_event']['data']['usage'], {'inputTokens': 100, 'outputTokens': 25})
        self.assertEqual(payload[0]['agent_event']['data']['message'], 'public result')
        self.assertEqual(payload[1]['agent_event']['data']['itemId'], 'reasoning-1')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_payload_limits_are_visible_and_do_not_hide_later_events(self):
        self.journal.append(self.event(1, data={'output': 'x' * 70000}))
        self.journal.append(self.event(2, data={'result': 'next result'}))
        page = self.journal.read()
        first, second = page['events']
        self.assertTrue(first['truncated'])
        self.assertFalse(second['truncated'])
        self.assertEqual(second['agent_event']['data'], {'result': 'next result'})


if __name__ == '__main__':
    unittest.main()
