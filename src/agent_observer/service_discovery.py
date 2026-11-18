"""Read-only launch discovery from trusted, saved project manifests."""
import json
import re
import shutil
from pathlib import Path


def discover_service_definitions(settings):
    configured = settings.get('local_services', [])
    if not isinstance(configured, list) or len(configured) > 32 or any(not isinstance(item, dict) for item in configured):
        raise ValueError('local_services must contain at most 32 trusted definitions.')
    definitions = list(configured)
    root = Path(settings['project_root']).resolve()
    manifest = root / 'package.json'
    if not manifest.is_file() or not manifest.resolve().is_relative_to(root) or not shutil.which('npm'):
        return definitions
    try:
        with manifest.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return definitions
        package = json.loads(raw)
        scripts = package.get('scripts', {})
        if not isinstance(scripts, dict):
            return definitions
    except (OSError, ValueError, AttributeError):
        return definitions
    occupied = {str(url).rstrip('/') for item in definitions for url in item.get('endpoints', [])}
    ids = {item.get('id') for item in definitions}
    defaults = {'vite': 5173, 'next': 3000, 'astro': 4321, 'nuxt': 3000, 'wrangler': 8787, 'react-scripts': 3000}
    for name in ('dev', 'serve', 'start', 'preview', 'site', 'rss', 'refresh', 'monitor'):
        script = scripts.get(name)
        if not isinstance(script, str) or len(script) > 4096:
            continue
        framework = next((tool for tool in defaults if re.search(r'(?:^|\s)' + re.escape(tool) + r'(?:\s|$)', script)), None)
        if not framework or re.search(r'\b(?:deploy|publish)\b', script):
            continue
        port_match = re.search(r'(?:--port(?:=|\s+)|-p\s+|\bPORT=)(\d{2,5})\b', script)
        port = int(port_match[1]) if port_match else 4173 if name == 'preview' and framework == 'vite' else defaults[framework]
        endpoint = f'http://127.0.0.1:{port}'
        key = 'project-' + name
        if not 1024 <= port <= 65535 or port == settings.get('port') or endpoint in occupied or key in ids or len(definitions) >= 32:
            continue
        args = ['--hostname', '127.0.0.1'] if framework == 'next' else ['--ip', '127.0.0.1'] if framework == 'wrangler' else ['--host', '127.0.0.1']
        if framework == 'react-scripts':
            args = []
        definitions.append({'id': key, 'name': 'npm:' + name, 'cwd': '.',
                            'command': [shutil.which('npm'), 'run', name] + (['--'] + args if args else []),
                            'environment': {'HOST': '127.0.0.1'}, 'endpoints': [endpoint],
                            'health_url': endpoint + '/', 'health_kind': 'http', 'require_project_cwd': True,
                            'process_match': [], 'source': 'package.json'})
        occupied.add(endpoint)
        ids.add(key)
    for name in ('dev','serve','start','preview','site','rss','refresh','monitor'):
        script=scripts.get(name);key='project-'+name
        if key in ids or len(definitions)>=32 or not isinstance(script,str) or len(script)>4096 or re.search(r'\b(?:deploy|publish)\b',script) or any(re.search(r'(?:^|\s)'+re.escape(tool)+r'(?:\s|$)',script) for tool in defaults):continue
        definitions.append({'id':key,'name':'npm:'+name,'cwd':'.','command':[shutil.which('npm'),'run',name],
                            'endpoints':[],'health_kind':'http','require_project_cwd':True,'source':'package.json',
                            'script':name,'script_command':script,
                            'description':'Trusted project script; listener addresses are detected from its running processes.'})
        ids.add(key)
    return definitions


def running_service_definitions(settings, processes):
    """Only offer restart recipes from actual npm ancestors and trusted scripts."""
    from .project_processes import scan, http_urls
    root = Path(settings['project_root']).resolve()
    try:
        manifest=root/'package.json'
        if not manifest.resolve().is_relative_to(root):return []
        with manifest.open('rb') as stream:raw=stream.read(65537)
        if len(raw)>65536:return []
        scripts = json.loads(raw)['scripts']
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not isinstance(scripts, dict) or not shutil.which('npm'):
        return []
    rows = scan(); result = []
    for process in processes:
        if not process['ports']: continue
        pid = process['pid']; seen = set(); name = None
        while pid in rows and pid not in seen:
            seen.add(pid); match = re.search(r'(?:^|\s)npm\s+(?:run\s+)?([A-Za-z0-9:_-]+)(?:\s|$)', rows[pid]['command'])
            if match and match[1] in scripts and isinstance(scripts[match[1]], str):
                name = match[1]; break
            pid = rows[pid]['ppid']
        if not name: continue
        urls = http_urls(process)
        process['urls'] = urls
        if not urls: continue
        result.append({'id':'project-'+name.replace(':','-'), 'name':'npm:'+name, 'cwd':'.',
                       'command':[shutil.which('npm'),'run',name], 'endpoints':urls[:8],
                       'health_url':urls[0]+'/', 'health_kind':'http','require_project_cwd':True,
                       'process_match':[], 'source':'running-project-process',
                       'description':'Trusted package.json script matched to a live project process.'})
    return result
