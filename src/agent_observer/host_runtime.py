"""Stable loopback host. Replace only verified same-user Observer Python servers."""
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.request

DEFAULT_PORT = 8766
from .cli import ROOT
_lease = None


@functools.lru_cache(maxsize=1)
def revision():
    digest = hashlib.sha256()
    for path in sorted((ROOT/'src/agent_observer').rglob('*')):
        if path.is_file() and path.suffix in {'.py','.js','.css','.html'} and '__pycache__' not in path.parts and 'tests' not in path.parts:
            digest.update(str(path.relative_to(ROOT)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()


def _registry():
    try:return json.loads((ROOT/'.observer/installations.json').read_text()).get('installations',[])
    except (OSError,ValueError):return []


def _scripts():
    paths = {str((ROOT/'observer.py').resolve())}
    for entry in _registry():
        for target in [entry.get('target'),(entry.get('compat') or {}).get('target')]:
            if target:
                paths.update(str((Path(target)/name).resolve()) for name in ('observer.py','xray_server.py'))
    return paths


def _verified(row, scripts):
    try:
        args=shlex.split(row['command'])
        if not args or not Path(args[0]).name.lower().startswith('python'):return False
        index=1
        while index<len(args) and args[index] in {'-u','-B','-E','-I','-s','-S','-O','-OO'}:index+=1
        if index>=len(args) or args[index].startswith('-'):return False
        script=Path(args[index])
        if not script.is_absolute():
            if not row.get('cwd'):return False
            script=Path(row['cwd'])/script
        if str(script.resolve()) not in scripts:return False
        return Path(args[index]).name=='xray_server.py' or index+1<len(args) and args[index+1]=='run'
    except (ValueError,OSError):return False


def release_start_lock():
    global _lease
    if _lease is not None:
        fcntl.flock(_lease,fcntl.LOCK_UN);os.close(_lease);_lease=None


def _migrate_ports(port):
    if port != DEFAULT_PORT:return
    paths={Path(entry['target'])/'config/observer.project.json' for entry in _registry() if entry.get('target')}
    if os.environ.get('AGENT_OBSERVER_CONFIG'):paths.add(Path(os.environ['AGENT_OBSERVER_CONFIG']))
    for path in paths:
        try:
            if path.is_symlink():continue
            data=json.loads(path.read_text())
            if data.get('port')==port:continue
            data['port']=port;temporary=path.with_suffix('.host-tmp')
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n')
            temporary.replace(path)
        except FileNotFoundError:continue
        except (OSError,ValueError,AttributeError) as error:
            print(f'Observer kept an unreadable project configuration unchanged: {path} ({type(error).__name__}).',file=sys.stderr)


def prepare(port=DEFAULT_PORT):
    """Return a reserved socket, or None when the identical host is already ready."""
    global _lease
    from .project_processes import scan, identity
    from .installation import DASHBOARD_PROTOCOL
    if not 1<=port<=65535:raise ValueError('Observer port must be between 1 and 65535.')
    directory=ROOT/'.observer';directory.mkdir(exist_ok=True)
    _lease=os.open(directory/f'host-{port}.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    fcntl.flock(_lease,fcntl.LOCK_EX)
    rows=scan(True);scripts=_scripts();excluded={os.getpid()};pid=os.getppid()
    while pid in rows and pid not in excluded:excluded.add(pid);pid=rows[pid]['ppid']
    candidates={pid:row for pid,row in rows.items() if pid not in excluded and _verified(row,scripts)}
    listeners={pid for pid,row in rows.items() if any(item['port']==port for item in row['ports'])}
    if listeners-set(candidates):
        release_start_lock();raise ValueError(f'Port {port} belongs to an unrelated or unverifiable process; it was not stopped.')
    reusable=False
    if listeners:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{port}/health',timeout=2) as response:data=json.load(response)
            reusable=data.get('service')=='agent-observer' and data.get('dashboard_protocol')==DASHBOARD_PROTOCOL and data.get('server_revision')==revision()
        except (OSError,ValueError):pass
    stop={pid:row for pid,row in candidates.items() if (port==DEFAULT_PORT or pid in listeners) and not (reusable and pid in listeners)}
    fresh=scan(True)
    for pid,row in stop.items():
        if pid in fresh and identity(fresh[pid])==identity(row):
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
    deadline=time.monotonic()+15
    while stop and time.monotonic()<deadline:
        current=scan(True);stop={pid:row for pid,row in stop.items() if pid in current and identity(current[pid])==identity(row)}
        if stop:time.sleep(.1)
    if stop:
        release_start_lock();raise ValueError('A verified old Observer is still shutting down; no unrelated process was killed.')
    _migrate_ports(port)
    if reusable:release_start_lock();return None
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);sock.bind(('127.0.0.1',port));sock.listen(128);sock.setblocking(False)
    except OSError:
        sock.close();release_start_lock();raise
    return sock


def start_host(project, config):
    directory=ROOT/'var/host';directory.mkdir(parents=True,exist_ok=True)
    fd=os.open(directory/'startup.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'ab',buffering=0) as output:
        subprocess.Popen([sys.executable,str(ROOT/'observer.py'),'run','--project',str(project),'--config',str(config)],cwd=ROOT,stdout=output,stderr=output,stdin=subprocess.DEVNULL,start_new_session=True)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{DEFAULT_PORT}/health',timeout=.5) as response:data=json.load(response)
            if data.get('service')=='agent-observer' and data.get('server_revision')==revision():return DEFAULT_PORT
        except (OSError,ValueError):pass
        time.sleep(.1)
    raise ValueError('Observer host did not start; inspect var/host/startup.log.')
