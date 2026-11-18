"""Behavior tests for public memory settings and authoritative experience counts."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_observer.documents import DocumentError
from agent_observer.memory_systems import inspect_memory_systems, normalize_memory_systems, public_system
from agent_observer.observer_config import load_settings, load_current_block_settings, save_block_settings


class MemorySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'observer.project.json'
        self.private_token = self.root / 'private-token'
        self.private_token.write_text('fixture-private-token', encoding='utf-8')
        self.systems = [
            {'id': 'hindsight', 'name': 'Experience', 'provider': 'hindsight',
             'endpoint': 'http://127.0.0.1:8888', 'bank_ids': ['cad'], 'token_file': str(self.private_token)},
            {'id': 'other', 'name': 'Independent memory', 'provider': 'custom', 'endpoint': '',
             'categories': [{'id': 'reference', 'label': 'Reference'}]},
        ]
        self.config.write_text(json.dumps({'schema_version': 1, 'project': {'root': str(self.root)},
                                           'memory_systems': self.systems}), encoding='utf-8')
        self.settings = load_settings(str(self.config))

    def public_patch(self):
        current = load_current_block_settings(self.settings)
        return {'revision': current['revision'],
                'memory_systems': [public_system(system) for system in current['memory_systems']]}

    def test_repository_and_model_metadata_save_preserves_private_auth_and_other_system(self):
        patch = self.public_patch()
        patch['memory_systems'][0].update(github_url='https://github.com/vectorize-io/hindsight',
                                         model={'name': 'configured-router-alias', 'provider': 'openai',
                                                'base_url': 'http://127.0.0.1:20128/v1'})
        token_before = self.private_token.read_bytes()
        save_block_settings(self.settings, patch)
        reloaded = load_settings(str(self.config))['memory_systems']
        self.assertEqual(reloaded[0]['github_url'], patch['memory_systems'][0]['github_url'])
        self.assertEqual(reloaded[0]['model']['name'], 'configured-router-alias')
        self.assertEqual(reloaded[0]['token_file'], str(self.private_token))
        self.assertEqual(self.private_token.read_bytes(), token_before)
        self.assertEqual(reloaded[1], normalize_memory_systems([self.systems[1]])[0])
        self.assertNotIn('token_file', public_system(reloaded[0]))

    def test_explicit_system_removal_is_not_undone_by_secret_preservation(self):
        patch = self.public_patch()
        patch['memory_systems'] = [patch['memory_systems'][0]]
        save_block_settings(self.settings, patch)
        self.assertEqual([s['id'] for s in load_settings(str(self.config))['memory_systems']], ['hindsight'])
        patch = self.public_patch()
        patch['memory_systems'] = []
        save_block_settings(self.settings, patch)
        self.assertEqual(load_settings(str(self.config))['memory_systems'], [])

    def test_memory_stays_a_list_when_loading_or_saving_older_grid_settings(self):
        data = json.loads(self.config.read_text(encoding='utf-8'))
        data['block_layouts'] = {'memory': 'grid', 'commands': 'free'}
        self.config.write_text(json.dumps(data), encoding='utf-8')
        self.settings = load_settings(str(self.config))
        self.assertEqual(self.settings['block_layouts']['memory'], 'list')
        self.assertEqual(self.settings['block_layouts']['commands'], 'free')
        current = load_current_block_settings(self.settings)
        save_block_settings(self.settings, {'revision': current['revision'],
                                           'block_layouts': {'memory': 'grid', 'tools': 'grid'}})
        saved = json.loads(self.config.read_text(encoding='utf-8'))['block_layouts']
        self.assertEqual(saved['memory'], 'list')
        self.assertEqual(saved['commands'], 'free')
        self.assertEqual(saved['tools'], 'grid')

    def test_stale_form_cannot_overwrite_a_concurrent_configuration_change(self):
        stale = self.public_patch()
        fresh = self.public_patch()
        fresh['memory_systems'][1]['name'] = 'Changed in another window'
        save_block_settings(self.settings, fresh)
        before = self.config.read_bytes()
        stale['memory_systems'][0]['github_url'] = 'https://github.com/vectorize-io/hindsight'
        with self.assertRaises(DocumentError) as raised:
            save_block_settings(self.settings, stale)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.config.read_bytes(), before)

    def test_invalid_repository_credentials_and_inference_descriptors_never_reach_disk(self):
        bad_urls = ['http://github.com/vectorize-io/hindsight', 'https://example.com/repo',
                    'https://github.com.evil.invalid/repo', 'https://user:secret@github.com/repo']
        for url in bad_urls:
            with self.subTest(url=url):
                patch = self.public_patch()
                patch['memory_systems'][0]['github_url'] = url
                before = self.config.read_bytes()
                with self.assertRaises(ValueError):
                    save_block_settings(self.settings, patch)
                self.assertEqual(self.config.read_bytes(), before)
        patch = self.public_patch()
        patch['memory_systems'][0]['model'] = {'name': 'alias', 'base_url': 'http://user:secret@localhost/v1'}
        with self.assertRaises(ValueError):
            save_block_settings(self.settings, patch)


class TaxonomyCountTests(unittest.TestCase):
    def setUp(self):
        self.categories = [{'slug_path': 'geometry', 'name': 'Geometry', 'lessons_count': 8},
                           {'slug_path': 'validation', 'name': 'Validation', 'lessons_count': 7}]
        self.health = {'status': 'ok', 'lessons_count': 10, 'indexed_count': 8,
                       'unindexed_count': 2, 'categories_count': 2}
        self.health_status = 200
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status = 200
                if self.path.startswith('/v1/default/banks?'):
                    body = {'banks': [], 'total': 0}
                elif self.path == '/categories':
                    body = {'categories': owner.categories}
                elif self.path == '/health':
                    status, body = owner.health_status, owner.health
                else:
                    status, body = 404, {}
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def inspect(self):
        endpoint = f'http://127.0.0.1:{self.server.server_port}'
        return inspect_memory_systems({'project_root': '.', 'memory_systems': [
            {'id': 'fixture', 'name': 'Fixture', 'provider': 'hindsight',
             'endpoint': endpoint, 'experience_endpoint': endpoint}
        ]})['systems'][0]

    def test_unique_experience_is_not_double_counted_across_categories(self):
        result = self.inspect()
        self.assertEqual(result['taxonomy_unique_lessons'], 10)
        self.assertEqual(result['taxonomy_assignments_total'], 15)
        self.assertEqual(result['taxonomy_categories_count'], 2)

    def test_unavailable_unique_counter_stays_unknown_even_with_category_counts(self):
        self.health_status = 503
        result = self.inspect()
        self.assertIsNone(result['taxonomy_unique_lessons'])
        self.assertEqual(result['taxonomy_assignments_total'], 15)

    def test_partial_category_counts_are_not_presented_as_a_complete_total(self):
        del self.categories[1]['lessons_count']
        result = self.inspect()
        self.assertIsNone(result['taxonomy_assignments_total'])
        self.assertEqual(result['taxonomy_unique_lessons'], 10)

    def test_reported_empty_experience_is_zero_not_unknown(self):
        self.categories = []
        self.health.update(lessons_count=0, categories_count=0)
        result = self.inspect()
        self.assertEqual(result['taxonomy_unique_lessons'], 0)
        self.assertEqual(result['taxonomy_assignments_total'], 0)

    def test_nested_descendants_and_zero_counters_are_preserved_without_omission(self):
        self.categories = [
            {'slug_path': 'cad_domain', 'name': 'CAD Domain', 'parent_slug_path': None, 'lessons_count': 0,
             'children': [
                 {'slug_path': 'cad_domain/geometry', 'name': 'Geometry & Solids',
                  'parent_slug_path': 'cad_domain', 'lessons_count': 0, 'children': []},
                 {'slug_path': 'cad_domain/interpretation', 'name': 'Drawing & Spec Interpretation',
                  'parent_slug_path': 'cad_domain', 'lessons_count': 0, 'children': []},
             ]}
        ]
        self.health.update(lessons_count=0, categories_count=3)
        result = self.inspect()
        self.assertEqual(result['taxonomy_unique_lessons'], 0)
        self.assertEqual(result['taxonomy_assignments_total'], 0)
        self.assertEqual(result['taxonomy_categories_count'], 3)
        self.assertEqual(len(result['categories']), 3)
        by_id = {c['id']: c for c in result['categories']}
        self.assertIn('cad_domain', by_id)
        self.assertIn('cad_domain/geometry', by_id)
        self.assertIn('cad_domain/interpretation', by_id)
        self.assertIsNone(by_id['cad_domain']['parent_id'])
        self.assertEqual(by_id['cad_domain/geometry']['parent_id'], 'cad_domain')
        self.assertEqual(by_id['cad_domain/interpretation']['parent_id'], 'cad_domain')
        self.assertEqual(by_id['cad_domain']['lessons_count'], 0)
        self.assertEqual(by_id['cad_domain/geometry']['lessons_count'], 0)
        self.assertEqual(by_id['cad_domain/interpretation']['lessons_count'], 0)

    def test_configured_fallback_leaves_lesson_counters_unknown_when_api_unavailable(self):
        endpoint = f'http://127.0.0.1:{self.server.server_port}'
        system = {
            'id': 'fallback-sys', 'name': 'Fallback Sys', 'provider': 'hindsight',
            'endpoint': endpoint, 'experience_endpoint': 'http://127.0.0.1:9',  # Unreachable port
            'categories': [{'id': 'cad_domain', 'label': 'CAD Domain'},
                           {'id': 'cad_domain/geometry', 'label': 'Geometry'}]
        }
        inspected = inspect_memory_systems({'project_root': '.', 'memory_systems': [system]})['systems'][0]
        self.assertEqual(inspected['categories_source'], 'configuration')
        self.assertIn('categories_error', inspected)
        self.assertIsNone(inspected['taxonomy_assignments_total'])
        self.assertEqual(len(inspected['categories']), 2)
        for cat in inspected['categories']:
            self.assertIsNone(cat.get('lessons_count'))

    def test_authoritative_api_categories_do_not_resurrect_removed_configured_categories(self):
        endpoint = f'http://127.0.0.1:{self.server.server_port}'
        system = {
            'id': 'auth-sys', 'name': 'Authoritative Sys', 'provider': 'hindsight',
            'endpoint': endpoint, 'experience_endpoint': endpoint,
            'categories': [{'id': 'removed_category', 'label': 'Removed Category'}]
        }
        inspected = inspect_memory_systems({'project_root': '.', 'memory_systems': [system]})['systems'][0]
        self.assertEqual(inspected['categories_source'], 'CAD experience API')
        ids = [c['id'] for c in inspected['categories']]
        self.assertNotIn('removed_category', ids)
        self.assertIn('geometry', ids)
        self.assertIn('validation', ids)


if __name__ == '__main__':
    unittest.main()
