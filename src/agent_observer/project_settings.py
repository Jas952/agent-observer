"""Project-bound settings access; never swap the serving graph's configuration."""
import json
import re
import urllib.error
import urllib.request
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .connection_analysis import discover_all_codex_sessions, discover_agent_system_sessions
from .graph_settings import GraphSettings
from .installation import InstallationError
from .observer_config import load_settings, resource_catalog, load_current_block_settings, save_block_settings
from .local_services import LocalServiceManager, LocalServiceError
from .session_names import apply_session_names
from .xray_auth import load_token


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProjectSettings:
    def __init__(self, connections, local_services=None):
        self.connections = connections
        self.local_services = local_services
        self.managers = {}
        self.lock = threading.RLock()

    @staticmethod
    def validate_endpoint(endpoint, method):
        allowed = {
            '/api/block-settings': {'GET', 'POST'},
            '/api/graph-settings': {'GET', 'POST'},
            '/api/connections/status': {'GET'},
            '/api/sessions': {'GET'},
            '/api/graph-settings/reveal': {'POST'},
            '/api/local-services': {'GET'},
        }
        parsed = urllib.parse.urlsplit(endpoint or '')
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise InstallationError(400, 'This operation is not available in project settings.')
        methods = allowed.get(parsed.path)
        service = re.fullmatch(r'/api/local-services/([A-Za-z0-9_:-][A-Za-z0-9_.:-]*)/(start|stop|logs)', parsed.path)
        process = re.fullmatch(r'/api/local-processes/process-\d+/stop', parsed.path)
        if parsed.query and not (service and service[2] == 'logs' and re.fullmatch(r'after=\d{1,12}', parsed.query)):
            raise InstallationError(400, 'This operation is not available in project settings.')
        if service:
            methods = {'GET'} if service[2] == 'logs' else {'POST'}
        if process:
            methods = {'POST'}
        if methods is None or method not in methods:
            raise InstallationError(400, 'This operation is not available in project settings.')

    def resolve(self, project):
        if not isinstance(project, str) or not project:
            raise InstallationError(400, 'Choose a saved project folder.')
        root = Path(project).expanduser().resolve()
        workspace = next((row for row in self.connections.workspaces() if row['path'] == str(root)), None)
        if not workspace:
            raise InstallationError(403, 'Project settings are restricted to saved project folders.')
        if not root.is_dir():
            raise InstallationError(404, 'Project folder is unavailable.')
        target, config_path, _ = self.connections._configuration(root)
        settings = load_settings(config_path, project_root=root, environment={})
        return root, target, workspace, settings

    def request(self, project, endpoint, method='GET', body=None):
        self.validate_endpoint(endpoint, method)
        root, target, workspace, settings = self.resolve(project)
        service_endpoint = urllib.parse.urlsplit(endpoint)
        transport = getattr(self,"transport",None)
        if transport and transport.loop is not None and str(root)!=str(self.connections.settings['project_root']):
            status,_,raw = transport.request(str(root),endpoint,method,body)
            data = json.loads(raw)
            if status>=400:raise InstallationError(status,data.get('detail','Project request failed.'))
            if endpoint=='/api/graph-settings':
                _,_,raw_config=transport.request(str(root),'/api/config')
                data['config']=json.loads(raw_config)
            return data
        is_service = service_endpoint.path.startswith(('/api/local-services', '/api/local-processes'))
        if is_service and str(root) in self.managers:
            return self.service_request(root, settings, endpoint, method, body)
        port = workspace.get('port')
        if not workspace['current'] and isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
            state = self.connections.installations._inspect_connection(root, port, target)
            if state.get('connected') and state.get('dashboard_compatible'):
                result = self.forward(port, settings, endpoint, method, body)
                if endpoint == '/api/graph-settings':
                    result['config'] = self.forward(port, settings, '/api/config', 'GET', None)
                    if result['config'].get('project_root') != str(root):
                        raise InstallationError(409, 'Settings belong to a different project.')
                return result
        if is_service:
            return self.service_request(root, settings, endpoint, method, body)
        if endpoint == '/api/block-settings':
            saved = load_current_block_settings(settings) if method == 'GET' else save_block_settings(settings, body)
            from .memory_systems import public_system
            saved['memory_systems'] = [public_system(system) for system in saved.get('memory_systems', [])]
            if method == 'POST':
                saved['catalog'] = resource_catalog(settings)
            return saved
        if endpoint == '/api/connections/status':
            return self.connections.inspect({'project': str(root)}, include_resources=False)
        if endpoint == '/api/sessions':
            if workspace['repository_mode'] == 'working_repository':
                sessions = discover_all_codex_sessions(root)['sessions']
            else:
                sessions = discover_agent_system_sessions(root, target, settings['history_file'])
            return {'sessions': apply_session_names(sessions, settings, root, workspace['repository_mode'])}
        if endpoint in {'/api/graph-settings', '/api/graph-settings/reveal'}:
            inventory = GraphSettings(settings)
            config = resource_catalog(settings)
            result = inventory.inventory(config, {})
            if endpoint.endswith('/reveal'):
                return inventory.reveal((body or {}).get('id'))
            result.update(config=config, local_services=self.service_request(root, settings, '/api/local-services', 'GET', None)['services'], runtime_available=False)
            return result
        raise InstallationError(409, 'Open this project’s graph to control its services or read its logs.')

    def service_request(self, root, settings, endpoint, method, body):
        with self.lock:
            if str(root) == str(self.connections.settings['project_root']) and self.local_services is not None:
                manager = self.local_services
            else:
                manager = self.managers.get(str(root))
                if manager is None:
                    manager = self.managers[str(root)] = LocalServiceManager(settings)
        path = urllib.parse.urlsplit(endpoint)
        if path.path == '/api/local-services':
            return manager.snapshot(settings)
        process = re.fullmatch(r'/api/local-processes/(process-\d+)/stop', path.path)
        if process:
            if not isinstance(body, dict) or set(body) != {'identity'} or not isinstance(body['identity'], str):
                raise InstallationError(400, 'Refresh the process and supply its current identity.')
            return {'process': manager.stop_process(process[1], body['identity'])}
        match = re.fullmatch(r'/api/local-services/([^/]+)/(start|stop|logs)', path.path)
        key, action = match.groups()
        if action == 'start':
            if not isinstance(body, dict) or body not in ({}, {'mode': 'independent'}):
                raise InstallationError(400, 'Start accepts a registered service ID and optional independent mode, not command arguments.')
            manager.update_settings(settings)
            return {'service': manager.start(key, independent=body.get('mode') == 'independent')}
        if action == 'stop':
            if not isinstance(body, dict) or set(body) != {'identity'} or not isinstance(body['identity'], str):
                raise InstallationError(400, 'Refresh the service and supply its current identity.')
            return {'service': manager.stop(key, body['identity'])}
        after = int(urllib.parse.parse_qs(path.query).get('after', ['0'])[0])
        return manager.logs(key, after)

    def hosts(self):
        def inspect(workspace):
            try:
                result = self.request(workspace['path'], '/api/local-services')
                return {'project_root':workspace['path'], 'services':result['services'], 'processes':result.get('processes'), 'process_count':result.get('process_count'), 'available':True}
            except (InstallationError, LocalServiceError, ValueError, OSError):
                return {'project_root':workspace['path'], 'services':[], 'available':False}
        with ThreadPoolExecutor(max_workers=4) as pool:
            return {'projects': list(pool.map(inspect, self.connections.workspaces()))}

    def shutdown(self):
        for manager in list(self.managers.values()):
            manager.shutdown()

    @staticmethod
    def forward(port, settings, endpoint, method, body):
        token = load_token(path=settings.get('token_file'))
        if not token:
            raise InstallationError(503, 'The selected project’s graph authentication is unavailable.')
        payload = json.dumps(body).encode('utf-8') if method == 'POST' else None
        request = urllib.request.Request(f'http://127.0.0.1:{port}{endpoint}', data=payload, method=method,
                                         headers={'X-Agent-Observer-Token': token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                detail = json.load(error).get('detail')
            except (ValueError, AttributeError):
                detail = None
            raise InstallationError(error.code, detail or 'The selected project’s graph rejected the request.') from error
        except (OSError, ValueError) as error:
            raise InstallationError(503, 'The selected project’s graph could not be reached.') from error
