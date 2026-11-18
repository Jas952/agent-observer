"""One HTTP listener, isolated authenticated ASGI runtimes per registered project."""
import asyncio
import importlib.util
import hashlib
import json
import secrets
import sys
import urllib.parse
from pathlib import Path

from .installation import InstallationError
from .xray_auth import load_token


class ProjectTransport:
    def __init__(self, projects):
        self.projects = projects
        self.loop = None
        self.primary = None
        self.runtimes = {}
        self.contexts = {}
        self.loading = {}
        projects.transport = self

    def bind(self, loop, primary):
        self.loop, self.primary = loop, primary
        primary.INSTALLATIONS.transport = self

    @staticmethod
    def endpoint(path):
        if not isinstance(path,str) or any(ord(char)<32 for char in path):
            raise InstallationError(400,'Expected a project API path.')
        parsed = urllib.parse.urlsplit(path); decoded = urllib.parse.unquote(parsed.path)
        blocked = {'project-request','project-file','project-settings','project-hosts','session-open'}
        if (parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith('/api/')
                or '\\' in decoded or any(part in {'.','..'} for part in decoded.split('/'))
                or decoded.split('/')[2] in blocked or decoded != parsed.path and not decoded.startswith('/api/')):
            raise InstallationError(400,'Only direct project API paths are allowed.')
        return path

    async def runtime(self, project):
        root, _, _, settings = self.projects.resolve(project)
        key = str(root)
        if self.primary and key == str(self.primary.PROJECT_ROOT): return self.primary
        if key in self.runtimes: return self.runtimes[key]
        if key in self.loading: return await asyncio.shield(self.loading[key])
        future = asyncio.get_running_loop().create_future(); self.loading[key] = future
        try:
            settings = {**settings,'port':self.primary.SETTINGS['port']}
            name = 'agent_observer._project_'+hashlib.sha256(key.encode()).hexdigest()[:20]
            spec = importlib.util.spec_from_file_location(name,Path(__file__).with_name('observer_server.py'))
            module = importlib.util.module_from_spec(spec)
            module._INITIAL_SETTINGS, module._INITIAL_HUB = settings, self
            sys.modules[name] = module
            # Import executes no project code and does not mutate process environment.
            await asyncio.to_thread(spec.loader.exec_module,module)
            context = module.application_lifespan(module.app)
            await context.__aenter__()
            self.runtimes[key],self.contexts[key] = module,context
            future.set_result(module)
            return module
        except Exception as error:
            future.set_exception(error); future.exception()
            raise
        finally: self.loading.pop(key,None)

    def ensure(self, project):
        if self.loop is None: raise InstallationError(503,'The shared Observer host is starting.')
        return asyncio.run_coroutine_threadsafe(self.runtime(project),self.loop).result(timeout=40)

    def project_for_token(self, token):
        if not token:return None
        for workspace in self.projects.connections.workspaces():
            try:
                root,_,_,settings = self.projects.resolve(workspace['path'])
                if secrets.compare_digest(load_token(path=settings['token_file']),token): return str(root)
            except (OSError,InstallationError,ValueError):continue
        return None

    async def _request(self,project,path,method,body,content_type):
        module = await self.runtime(project)
        parsed = urllib.parse.urlsplit(path)
        payload = body if isinstance(body,bytes) else json.dumps(body).encode() if method=='POST' else b''
        headers = [(b'host',f"127.0.0.1:{self.primary.SETTINGS['port']}".encode()),(b'x-agent-observer-token',module.AUTH_TOKEN.encode()),(b'content-type',content_type.encode())]
        scope = {'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','scheme':'http','method':method,
                 'path':urllib.parse.unquote(parsed.path),'raw_path':parsed.path.encode(),'query_string':parsed.query.encode(),
                 'headers':headers,'server':('127.0.0.1',self.primary.SETTINGS['port']),'client':('127.0.0.1',0),'root_path':''}
        status, response_headers, chunks = 500, [], []
        delivered = False
        done = asyncio.Event()
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered=True;return {'type':'http.request','body':payload,'more_body':False}
            await done.wait();return {'type':'http.disconnect'}
        async def send(message):
            nonlocal status,response_headers
            if message['type']=='http.response.start':status,response_headers=message['status'],message.get('headers',[])
            elif message['type']=='http.response.body':
                chunks.append(message.get('body',b''))
                if not message.get('more_body'):done.set()
        await module.app(scope,receive,send)
        media = next((value.decode() for key,value in response_headers if key.lower()==b'content-type'),'application/octet-stream')
        return status,media,b''.join(chunks)

    def request(self,project,path,method='GET',body=None,content_type='application/json'):
        path = self.endpoint(path)
        if method not in {'GET','POST'}:raise InstallationError(400,'Only GET and POST are allowed.')
        if self.loop is None:raise InstallationError(503,'The shared Observer host is starting.')
        return asyncio.run_coroutine_threadsafe(self._request(project,path,method,body,content_type),self.loop).result(timeout=60)

    async def relay(self, websocket, project):
        module = await self.runtime(project)
        scope = dict(websocket.scope)
        scope['query_string'] = b''
        scope['subprotocols'] = ['cad-xray',module.AUTH_TOKEN]
        scope['headers'] = [(key,value) for key,value in scope['headers'] if key.lower()!=b'sec-websocket-protocol']+[(b'sec-websocket-protocol',('cad-xray, '+module.AUTH_TOKEN).encode())]
        await module.app(scope,websocket._receive,websocket._send)

    async def close(self):
        for context in reversed(list(self.contexts.values())):await context.__aexit__(None,None,None)
        self.contexts.clear();self.runtimes.clear()
