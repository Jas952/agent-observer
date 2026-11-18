import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from agent_observer.installation import InstallationError
from agent_observer.observer_config import load_settings
from agent_observer.project_transport import ProjectTransport


class ProjectTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_api_paths_cannot_escape_or_reenter_the_proxy(self):
        for path in ['http://example.test/api/config','//example.test/api/config','/api/../private',
                     '/api/%2e%2e/private','/api/project-request','/api/%70roject-file',
                     '/api/project-settings?project=other','/private','/api/\\private']:
            with self.subTest(path=path),self.assertRaises(InstallationError):ProjectTransport.endpoint(path)
        self.assertEqual(ProjectTransport.endpoint('/api/sessions/a%20b'),'/api/sessions/a%20b')

    async def test_encoded_omp_session_id_reaches_project_route_decoded(self):
        from fastapi import FastAPI
        app = FastAPI()
        @app.get('/api/sessions/{session_id:path}')
        async def session(session_id: str):
            return {'session_id': session_id}
        hub = ProjectTransport(SimpleNamespace())
        hub.primary = SimpleNamespace(SETTINGS={'port': 8766})
        async def runtime(_project):
            return SimpleNamespace(app=app, AUTH_TOKEN='test')
        hub.runtime = runtime
        status, _, body = await hub._request('project', '/api/sessions/omp%3Aabc', 'GET', None, 'application/json')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['session_id'], 'omp:abc')

    async def test_two_registered_projects_have_isolated_documents_and_auth_without_project_servers(self):
        with TemporaryDirectory() as directory:
            settings={}
            for name in ['first','second']:
                root=(Path(directory)/name).resolve();(root/'docs').mkdir(parents=True)
                (root/'docs/owner.md').write_text('Document belongs to '+name)
                config=root/'observer.project.json'
                config.write_text(json.dumps({'schema_version':1,'storage_profile':'project','project':{'root':str(root),'name':name},
                                               'connection':{'module':'codex','repository_mode':'working_repository','enabled':False},'agent_runtime':'codex',
                                               'documents':[{'path':'docs','include':['**/*.md']}]}))
                settings[str(root)]=load_settings(config,project_root=root,environment={})
            class Projects:
                connections=SimpleNamespace(settings={'project_root':Path(directory)},workspaces=lambda:[{'path':key} for key in settings])
                def resolve(self,project):
                    if project not in settings:raise InstallationError(403,'Unregistered project')
                    root=Path(project)
                    return root,root/'.agent-observer',{'path':project},settings[project]
            hub=ProjectTransport(Projects())
            primary=SimpleNamespace(PROJECT_ROOT=Path(directory),SETTINGS={'port':8766},INSTALLATIONS=SimpleNamespace())
            hub.bind(asyncio.get_running_loop(),primary)
            before=dict(os.environ)
            try:
                roots=list(settings)
                for name,root in zip(['first','second'],roots):
                    status,_,raw=await asyncio.to_thread(hub.request,root,'/api/config')
                    self.assertEqual(status,200);catalog=json.loads(raw)
                    self.assertEqual(catalog['project_root'],root)
                    node=next(node for node in catalog['nodes'] if node.get('path')=='docs/owner.md')
                    status,_,document=await asyncio.to_thread(hub.request,root,'/api/document?path='+node['path'])
                    self.assertEqual(status,200)
                    self.assertIn(('Document belongs to '+name).encode(),document)
                    self.assertNotIn(('Document belongs to '+('second' if name=='first' else 'first')).encode(),document)
                self.assertNotEqual(hub.runtimes[roots[0]].AUTH_TOKEN,hub.runtimes[roots[1]].AUTH_TOKEN)
                self.assertEqual(os.environ,before)
                with self.assertRaises(InstallationError):await asyncio.to_thread(hub.request,'/not-registered','/api/config')
                from unittest.mock import patch
                runtime=hub.runtimes[roots[0]]
                original_health=(await runtime.get_health()).body
                with patch.object(runtime,'host_revision',return_value='new-source-not-loaded'):
                    response=await runtime.get_health()
                self.assertEqual(response.status_code,200)
                self.assertEqual(json.loads(response.body)['server_revision'],json.loads(original_health)['server_revision'])
                self.assertNotEqual(json.loads(response.body)['server_revision'],'new-source-not-loaded')
            finally:await hub.close()


if __name__=='__main__':unittest.main()
