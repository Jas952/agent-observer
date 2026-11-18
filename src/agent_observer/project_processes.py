"""Read-only device inventory and identity-checked project process termination."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import shlex
import subprocess
import threading
import time

_lock = threading.RLock()
_cached = (0.0, {})


def redact(command):
    command = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@',r'\1[redacted]@',command)
    command = re.sub(r'(?i)(\b(?:bearer)\s+)[^\s]+', r'\1[redacted]', command)
    return re.sub(r'(?i)((?:--)?[\w-]*(?:token|password|secret|api[_-]?key|authorization)[\w-]*[=:\s]+)(?:"[^"\n]*"|\x27[^\x27\n]*\x27|[^\s]+)', r'\1[redacted]', command)[:4096]


def scan(force=False):
    global _cached
    with _lock:
        if not force and time.monotonic()-_cached[0] < 1:
            return _cached[1]
        ps = subprocess.run(['ps','-axo','pid=,ppid=,pgid=,uid=,tty=,lstart=,args='], capture_output=True, text=True, timeout=4, check=True,env={**os.environ,'LC_ALL':'C'})
        rows = {}
        for line in ps.stdout.splitlines():
            parts = line.split(None, 10)
            if len(parts) != 11 or not all(x.isdigit() for x in parts[:4]):
                continue
            pid, ppid, pgid, uid = map(int, parts[:4])
            if uid == os.getuid():
                rows[pid] = dict(pid=pid, ppid=ppid, pgid=pgid, uid=uid, tty=parts[4], started_at=' '.join(parts[5:10]), command=parts[10], ports=[])
        lsof = shutil.which('lsof') or '/usr/sbin/lsof'
        cwd = subprocess.run([lsof,'-a','-u',str(os.getuid()),'-d','cwd','-Fpn'], capture_output=True, text=True, timeout=5)
        if cwd.returncode not in (0,1):
            raise OSError('Project process working directories could not be inspected.')
        pid = None
        for line in cwd.stdout.splitlines():
            if line.startswith('p') and line[1:].isdigit(): pid = int(line[1:])
            elif line.startswith('n') and pid in rows: rows[pid]['cwd'] = line[1:]
        ports = subprocess.run([lsof,'-nP','-a','-u',str(os.getuid()),'-iTCP','-sTCP:LISTEN','-Fpn'], capture_output=True, text=True, timeout=5)
        if ports.returncode not in (0,1): raise OSError('Project listeners could not be inspected.')
        pid = None
        for line in ports.stdout.splitlines():
            if line.startswith('p') and line[1:].isdigit(): pid = int(line[1:])
            elif line.startswith('n') and pid in rows:
                address, _, port = line[1:].rpartition(':')
                endpoint={'host':address.strip('[]'),'port':int(port)} if port.isdigit() else None
                if endpoint and endpoint not in rows[pid]['ports']:rows[pid]['ports'].append(endpoint)
        _cached = (time.monotonic(), rows)
        return rows


def identity(row):
    return hashlib.sha256(json.dumps([row['pid'],row['uid'],row['started_at'],row['command'],row.get('cwd')]).encode()).hexdigest()


def inside(row, root):
    try:
        if Path(row['cwd']).resolve().is_relative_to(root):return True
    except (KeyError,OSError,ValueError):pass
    try:
        args=shlex.split(row['command'])
        if not args or not re.fullmatch(r'(?:python[0-9.]*|Python|node|ruby|php)',Path(args[0]).name):return False
        index=1
        while index<len(args) and args[index] in {'-u','-B','-I'}:index+=1
        script=Path(args[index])
        return script.is_absolute() and script.is_file() and script.resolve().is_relative_to(root)
    except (IndexError,OSError,ValueError):return False


def children(pid, rows):
    found = {pid}
    while True:
        extra = {key for key,row in rows.items() if row['ppid'] in found} - found
        if not extra:return found
        found.update(extra)

def protected(row, ancestors):
    command = row['command']
    return (row['pid'] in ancestors or bool(re.search(r'(?:observer\.py\s+run\b|observer_server\.py|xray_server\.py)',command))
            or bool(re.fullmatch(r'(?:[^\s]*/)?-?(?:zsh|bash|fish|sh)(?:\s+-[il]+)?',command)))


def inventory(root, ancestors, owners=None, force=False):
    root = Path(root).resolve(); rows = scan(force); result = []
    owners = owners or {}
    for pid,row in rows.items():
        if not inside(row,root): continue
        owner, origin = 'external', 'external_terminal' if row['tty'] not in ('??','?','-') else 'external_process'
        current = pid; visited = set()
        while current in rows and current not in visited:
            visited.add(current)
            if current in owners:
                owner = owners[current]; origin = 'graph_independent' if owner == 'independent' else 'graph'; break
            current = rows[current]['ppid']
        blocked = protected(row,ancestors)
        result.append({**{key:row[key] for key in ('pid','ppid','pgid','tty','started_at','ports')},
                       'id':f'process-{pid}', 'name':Path(row['command'].split()[0]).name,
                       'command':redact(row['command']), 'cwd':row.get('cwd','Not reported'), 'owner':owner,'origin':origin,
                       'status':'running','identity':identity(row),'can_stop':not blocked,
                       'note':'Observer and interactive shell controls are protected.' if blocked else '', 'urls':[]})
        if row['ports']:result[-1]['urls']=http_urls(row)
    return sorted(result,key=lambda row:row['pid'])


def http_urls(row):
    if not re.search(r'node|vite|next|http\.server|uvicorn|gunicorn|wrangler',row['command'],re.I): return []
    result = []
    for port in {entry['port'] for entry in row['ports']}:
        connection = http.client.HTTPConnection('127.0.0.1',port,timeout=.3)
        try:
            connection.request('HEAD','/'); connection.getresponse()
            result.append(f'http://127.0.0.1:{port}')
        except (OSError,http.client.HTTPException): pass
        finally: connection.close()
    return result


def stop(root, ancestors, process_id, expected_identity):
    global _cached
    match = re.fullmatch(r'process-(\d+)',process_id)
    if not match: raise ValueError('Unknown project process.')
    pid = int(match[1]); root = Path(root).resolve(); rows = scan(True); row = rows.get(pid)
    if not row or identity(row) != expected_identity or not inside(row,root) or protected(row,ancestors):
        raise ValueError('Process identity or project ownership changed; refresh before stopping.')
    targets = children(pid,rows)
    if any(not inside(rows[key],root) or protected(rows[key],ancestors) for key in targets):
        raise ValueError('The process has unrelated or protected children; stop the specific project worker instead.')
    fresh = scan(True)
    if any(key not in fresh or identity(fresh[key]) != identity(rows[key]) for key in targets):
        raise ValueError('Process tree changed; refresh before stopping.')
    for key in sorted(targets,key=lambda key:key == pid):
        try: os.kill(key,signal.SIGTERM)
        except ProcessLookupError: pass
    with _lock:_cached=(0.0,{})
    return {'id':process_id,'pid':pid,'status':'stopping'}
