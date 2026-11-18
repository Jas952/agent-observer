"""Authenticated UI controls for explicitly configured, loopback-only services."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .service_discovery import discover_service_definitions
from . import project_processes
from .service_discovery import running_service_definitions


class LocalServiceError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def local_url(value):
    if not isinstance(value, str):
        raise ValueError("Service endpoint must be a loopback URL.")
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Service endpoint must be a plain loopback HTTP URL.")
    if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
        raise ValueError("Only loopback services can be controlled.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    # Avoid proxy settings and DNS substitutions of localhost during inspection.
    return parsed, ("127.0.0.1" if host == "localhost" else host), port


def argv(value, cwd):
    if not isinstance(value, list) or not 1 <= len(value) <= 64 or any(not isinstance(x, str) or not x or len(x) > 4096 or "\0" in x for x in value):
        raise ValueError("Service commands must be bounded argument lists.")
    result = value.copy()
    if "/" in result[0] and not Path(result[0]).is_absolute():
        result[0] = str(cwd / result[0])
    return result


def process_info(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "uid=,lstart=,command="], capture_output=True, text=True, timeout=2)
    parts = result.stdout.strip().split(None, 6)
    if result.returncode or len(parts) != 7:
        return None
    return {"pid": pid, "uid": int(parts[0]), "start": " ".join(parts[1:6]), "command": parts[6]}


def listeners(port, host="127.0.0.1"):
    executable = shutil.which("lsof") or "/usr/sbin/lsof"
    result = subprocess.run([executable, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fptn"], capture_output=True, text=True, timeout=2)
    pids, pid, family = set(), None, None
    address = ipaddress.ip_address(host)
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("t"):
            family = line[1:]
        elif line.startswith("n") and pid is not None:
            bound, _, bound_port = line[1:].rpartition(":")
            bound = bound.strip("[]")
            wildcard = bound in {"*", "0.0.0.0", "::"} and family == f"IPv{address.version}"
            if bound_port == str(port) and (bound == str(address) or wildcard):
                pids.add(pid)
    return [info for pid in sorted(pids) if (info := process_info(pid))]


def fingerprint(infos):
    data = [(x["pid"], x["uid"], x["start"], x["command"]) for x in infos]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest() if data else None


@dataclass
class LogBuffer:
    lines: deque = field(default_factory=deque)
    seq: int = 0
    chars: int = 0
    secrets: set = field(default_factory=set)
    offsets: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    discarding: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def sanitize(self, text):
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[redacted]")
        text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[redacted]", text)
        text = re.sub(r"(?i)((?:api[_-]?key|token|password|secret|authorization)[\"']?\s*[=:]\s*)([\"'])(.*?)\2", r"\1\2[redacted]\2", text)
        text = re.sub(r"(?i)((?:api[_-]?key|token|password|secret)[\"']?\s*[=:]\s*)[^\s\"'&,]+", r"\1[redacted]", text)
        text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        text = "".join(c for c in text if c in "\t" or ord(c) >= 32)[:4000]
        return text

    def append(self, text, stream):
        text = self.sanitize(text)
        with self.lock:
            self.seq += 1
            self.lines.append({"seq": self.seq, "text": text, "stream": stream})
            self.chars += len(text)
            while len(self.lines) > 1000 or self.chars > 262144:
                self.chars -= len(self.lines.popleft()["text"])

    def read(self, after):
        with self.lock:
            return {"lines": [x.copy() for x in self.lines if x["seq"] > after], "next_cursor": self.seq,
                    "truncated": bool(self.lines and after < self.lines[0]["seq"] - 1)}


@dataclass
class Owned:
    process: subprocess.Popen
    pipe: int
    identity: str
    definition: dict
    stopping: bool = False


class LocalServiceManager:
    def __init__(self, settings):
        self.lock = threading.RLock()
        self.definitions = {}
        self.owned = {}
        self.buffers = {}
        self.errors = {}
        self.closed = False
        self.root = Path(settings["project_root"]).resolve()
        self.port = int(settings.get("port", 8766))
        self.protected = {os.getpid()}
        pid = os.getppid()
        while pid > 1 and pid not in self.protected:
            self.protected.add(pid)
            result = subprocess.run(["ps", "-p", str(pid), "-o", "ppid="], capture_output=True, text=True, timeout=2)
            if not result.stdout.strip().isdigit():
                break
            pid = int(result.stdout.strip())
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.runtime_definitions = {}
        self.settings = dict(settings)
        self.state_dir = Path(settings.get("history_file", self.root/".agent-observer/var/runs.json")).parent
        self.independent = {}
        self.independent_handles = {}
        self._load_independent()
        self.update_settings(settings)

    def update_settings(self, settings):
        self.settings = dict(settings)
        definitions = {}
        values = discover_service_definitions(settings)
        ids = {item.get("id") for item in values}
        values.extend(item for key,item in self.runtime_definitions.items() if key not in ids)
        if not isinstance(values, list) or len(values) > 32:
            raise ValueError("local_services must be a list of at most 32 trusted definitions.")
        for raw in values:
            if not isinstance(raw, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(raw.get("id", ""))):
                raise ValueError("Each local service needs a unique simple ID.")
            item = dict(raw)
            service_id = item["id"]
            if service_id in definitions:
                raise ValueError("Duplicate local service ID.")
            endpoints = item.get("endpoints")
            if not isinstance(endpoints, list) or not 0 <= len(endpoints) <= 8:
                raise ValueError("Register up to eight loopback endpoints per service.")
            parsed = [local_url(endpoint) for endpoint in endpoints]
            if any(port == self.port for _, _, port in parsed):
                raise ValueError("The graph cannot control its own server port.")
            cwd = (self.root / item.get("cwd", ".")).resolve()
            if not cwd.is_dir() or not cwd.is_relative_to(self.root):
                raise ValueError("Service working directory must be inside this project.")
            item["cwd"] = str(cwd)
            if item.get("command"):
                item["command"] = argv(item["command"], cwd)
            if item.get("stop_command"):
                item["stop_command"] = argv(item["stop_command"], cwd)
            health = item.get("health_url", endpoints[0].rstrip("/") + "/health" if endpoints else "")
            if endpoints:
                _, host, port = local_url(health)
                if (host, port) != parsed[0][1:]:
                    raise ValueError("Health inspection must use the primary registered endpoint.")
            item["health_url"] = health
            item["health_kind"] = item.get("health_kind", "json")
            if item["health_kind"] not in {"json", "http"}:
                raise ValueError("Service health kind must be json or http.")
            item["require_project_cwd"] = item.get("require_project_cwd") is True or item["health_kind"] == "http"
            environment = item.get("environment", {})
            if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v or "=" in k for k, v in environment.items()):
                raise ValueError("Service environment must contain string variables.")
            helper = item.get("credential_command") or item.get("credential_helper")
            if helper:
                if not isinstance(helper, dict) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(helper.get("env", ""))):
                    raise ValueError("Invalid credential helper definition.")
                item["credential_command"] = {"env": helper["env"], "argv": argv(helper.get("argv"), cwd)}
            matches = item.get("process_match", [])
            if not isinstance(matches, list) or any(not isinstance(x, str) or not x for x in matches):
                raise ValueError("process_match must contain literal command fragments.")
            log_files = item.get("log_files", [])
            if not isinstance(log_files, list):
                raise ValueError("log_files must be a list.")
            paths = [(self.root / path).resolve() for path in log_files]
            if any(not path.is_relative_to(self.root) for path in paths):
                raise ValueError("Service log files must remain inside the project.")
            item["log_files"] = [str(path) for path in paths]
            item["shutdown_timeout"] = max(1, min(90, float(item.get("shutdown_timeout", 75))))
            definitions[service_id] = item
        with self.lock:
            for key, owned in self.owned.items():
                if owned.process.poll() is None and definitions.get(key) != owned.definition:
                    raise LocalServiceError(409, "Stop the graph-started service before changing its launch definition.")
            self.definitions = definitions
            for key in definitions:
                self.buffers.setdefault(key, LogBuffer())
                item = definitions[key]
                environment = {**os.environ, **item.get("environment", {})}
                credential_env = (item.get("credential_command") or item.get("credential_helper") or {}).get("env")
                self.buffers[key].secrets.update(value for name, value in environment.items() if value and (name == credential_env or re.search(r"key|token|password|secret", name, re.I)))

    def _definition(self, key):
        if key not in self.definitions:
            raise LocalServiceError(404, "Unknown registered local service.")
        return self.definitions[key]

    def _project_owned(self, infos):
        if not infos:
            return False
        executable = shutil.which("lsof") or "/usr/sbin/lsof"
        for info in infos:
            try:
                result = subprocess.run([executable, "-a", "-p", str(info["pid"]), "-d", "cwd", "-Fn"],
                                        capture_output=True, text=True, timeout=2)
                paths = [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]
                if result.returncode or len(paths) != 1 or not Path(paths[0]).resolve().is_relative_to(self.root):
                    return False
            except (OSError, ValueError, subprocess.SubprocessError):
                return False
        return True

    def _healthy(self, definition):
        parsed, host, port = local_url(definition["health_url"])
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        url = urllib.parse.urlunsplit((parsed.scheme, authority, parsed.path or "/", "", ""))
        try:
            with self.opener.open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=0.7) as response:
                if definition.get("health_kind") == "http":
                    return response.status == 200
                if response.status != 200 or "json" not in response.headers.get("Content-Type", "").lower():
                    return False
                data = response.read(65537)
                if len(data) > 65536:
                    return False
                value = json.loads(data)
                return isinstance(value, dict) and ((isinstance(value.get("status"), str) and value["status"] in {"ok", "healthy", "ready", "running", "up"}) or value.get("ok") is True or value.get("healthy") is True)
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def _launch_command(self, key, item):
        parts, hide_next = [], False
        for argument in item.get("command", []):
            if hide_next:
                parts.append("[redacted]")
                hide_next = False
            elif re.search(r"(?i)(?:^|[-_])(?:api[-_]?key|token|password|secret|authorization)(?:$|[=:])", argument):
                flag, separator, _ = argument.partition("=" if "=" in argument else ":")
                parts.append(flag + separator + "[redacted]" if separator else argument)
                hide_next = not separator
            else:
                parts.append(argument)
        return self.buffers[key].sanitize(shlex.join(parts)) if parts else ""

    def _load_independent(self):
        path = self.state_dir/"independent-services.json"
        try:
            if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:return
            with path.open() as stream: data = json.loads(stream.read(65537))
            if isinstance(data,dict):self.independent = data
        except (OSError,ValueError): pass

    def _save_independent(self):
        self.state_dir.mkdir(parents=True,exist_ok=True)
        path = self.state_dir/"independent-services.json"; temporary = path.with_suffix(".tmp")
        fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,"w") as stream:json.dump(self.independent,stream)
        temporary.replace(path)

    def _independent_alive(self,key):
        record = self.independent.get(key)
        if not isinstance(record,dict):return None
        handle = self.independent_handles.get(key)
        if handle is not None:handle.poll()
        row = project_processes.scan().get(record.get("pid"))
        return record if row and project_processes.inside(row,self.root) and project_processes.identity(row)==record.get("identity") else None

    def stop_process(self,key,expected_identity):
        try:return project_processes.stop(self.root,self.protected,key,expected_identity)
        except ValueError as error:raise LocalServiceError(409,str(error)) from error

    def _start_independent(self,key,item,environment):
        self.state_dir.mkdir(parents=True,exist_ok=True)
        path = self.state_dir/("independent-"+key+".log")
        fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        try:
            with os.fdopen(fd,"ab",buffering=0) as output:
                process = subprocess.Popen(item["command"],cwd=item["cwd"],env=environment,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
            self.independent_handles[key]=process
            row = project_processes.scan(True).get(process.pid)
            if not row or not project_processes.inside(row,self.root):
                process.terminate();process.wait(timeout=3)
                raise LocalServiceError(503,"Independent launch identity could not be established.")
            self.independent[key]={"pid":process.pid,"identity":project_processes.identity(row),"log_file":str(path)}
            self._save_independent()
            self.buffers[key].append("Started independently; Observer shutdown will not stop this process.","system")
            return self._view(key)
        except OSError as error:raise LocalServiceError(503,"Independent service could not be launched.") from error

    def _worker_view(self,key,item,owned,independent):
        rows=project_processes.scan()
        graph_alive=owned is not None and owned.process.poll() is None
        row=rows.get(owned.process.pid) if graph_alive else rows.get(independent["pid"]) if independent else None
        if row is None and item.get("script"):
            pattern=re.compile(r'^(?:\S*/)?npm\s+(?:run\s+)?'+re.escape(item["script"])+r'(?:\s|$)')
            try:expected=shlex.split(item.get("script_command",""))
            except ValueError:expected=[]
            for candidate in rows.values():
                if not project_processes.inside(candidate,self.root):continue
                try:parts=shlex.split(candidate["command"])
                except ValueError:continue
                exact=expected and parts and Path(parts[0]).name==Path(expected[0]).name and parts[1:]==expected[1:]
                if pattern.search(candidate["command"]) or exact:row=candidate;break
        alive=graph_alive or row is not None
        owner="graph" if graph_alive else "independent" if independent else "external" if row else "none"
        current_identity=owned.identity if graph_alive else independent["identity"] if independent else project_processes.identity(row) if row else None
        urls=[]
        if row:
            for pid in project_processes.children(row["pid"],rows):
                if pid in rows and project_processes.inside(rows[pid],self.root):urls.extend(project_processes.http_urls(rows[pid]))
        urls=sorted(set(urls))
        return {"id":key,"name":item.get("name",key),"kind":"worker","endpoint":urls[0] if urls else "","endpoints":urls,
                "memory_system_ids":item.get("memory_system_ids",[]),"node_ids":item.get("node_ids",[]),
                "launch_command":self._launch_command(key,item),"cwd":item["cwd"],"description":item.get("description",""),
                "pid":row["pid"] if row else None,"owner":owner,"identity":current_identity,
                "status":"stopping" if graph_alive and owned.stopping else "running" if alive else "stopped",
                "connected":alive,"can_start":not alive and not self.closed,"can_start_independent":not alive and not self.closed,
                "can_stop":bool(current_identity and row and not project_processes.protected(row,self.protected) and not (owned and owned.stopping)),
                "log_available":bool(self.buffers[key].lines) or owner in {"graph","independent"},
                "note":"Process running; listening addresses are reported separately." if alive else "No matching project process is running.",
                "error":self.errors.get(key)}

    def _view(self, key):
        item = self._definition(key)
        owned = self.owned.get(key)
        independent = self._independent_alive(key)
        alive = owned is not None and owned.process.poll() is None or independent is not None
        if owned is not None and owned.process.poll() is not None and owned.pipe >= 0:
            os.close(owned.pipe)
            owned.pipe = -1
        if not item["endpoints"]:return self._worker_view(key,item,owned,independent)
        _, host, port = local_url(item["endpoints"][0])
        inspection_error = False
        try:
            infos = listeners(port, host)
        except (OSError, ValueError, subprocess.SubprocessError):
            infos, inspection_error = [], True
        try:
            with socket.create_connection((host, port), timeout=0.15):
                listening = True
        except OSError:
            listening = False
        matches = item.get("process_match", [])
        identity_rule = bool(matches) or item["require_project_cwd"]
        expected = len(infos) == 1 and all(info["uid"] == os.getuid() and info["pid"] not in self.protected and all(fragment in info["command"] for fragment in matches) for info in infos)
        if item["require_project_cwd"]:
            expected = expected and self._project_owned(infos)
        healthy = self._healthy(item) if listening else False
        connected = healthy and (not identity_rule or expected)
        owner = "graph" if owned is not None and owned.process.poll() is None else "independent" if independent else ("external" if listening else "none")
        identity = owned.identity if owner=="graph" else independent["identity"] if independent else fingerprint(infos) if expected and identity_rule else None
        status = "running" if connected else "stopped"
        note = "Started outside this graph." if owner == "external" else "Continues running after Observer exits." if independent else ""
        if owned and alive and owned.stopping:
            status = "stopping"
        elif alive and not connected:
            status = "starting" if not self.errors.get(key) else "error"
        elif listening and not connected:
            status = "occupied" if identity_rule and not expected else "unhealthy"
            note = "Port is occupied by an unverified service; stop is disabled." if status == "occupied" else "Service is listening but its health is not confirmed."
        elif not listening and not alive and self.errors.get(key):
            status = "error"
        if owned is not None and not alive and not listening and not owned.stopping:
            status = "error"
            if not self.errors.get(key):
                if owned.process.returncode:
                    self.errors[key] = f"The configured worker exited with code {owned.process.returncode}; open its logs."
                else:
                    self.errors[key] = "The configured worker exited unexpectedly; open its logs."
        if owner == "external" and connected:
            self.errors.pop(key, None)
        if inspection_error:
            note = "Process identity is unavailable; external stop is disabled."
        can_stop = alive and not (owned and owned.stopping) or owner == "external" and bool(identity) and expected and identity_rule
        logs_available = bool(self.buffers[key].lines) or alive or any(Path(path).is_file() for path in item["log_files"])
        return {"id": key, "name": item.get("name", key), "endpoint": item["endpoints"][0], "endpoints": item["endpoints"],
                "memory_system_ids": item.get("memory_system_ids", []), "node_ids": item.get("node_ids", []),
                "launch_command": self._launch_command(key, item), "cwd": item["cwd"],
                "description": item.get("description", ""),
                "status": status, "connected": connected, "owner": owner, "identity": identity,
                "can_start": not alive and not listening and bool(item.get("command")) and not self.closed,
                "can_start_independent": not alive and not listening and bool(item.get("command")) and not self.closed,
                "can_stop": bool(can_stop), "log_available": logs_available, "note": note, "error": self.errors.get(key)}

    def snapshot(self, settings=None):
        with self.lock:
            owners = {value.process.pid:"graph" for value in self.owned.values() if value.process.poll() is None}
            for key in self.independent:
                record = self._independent_alive(key)
                if record:owners[record["pid"]]="independent"
            try:
                processes = project_processes.inventory(self.root,self.protected,owners)
                for item in running_service_definitions(settings or self.settings,processes):self.runtime_definitions[item["id"]]=item
                process_error = None
            except (OSError,subprocess.SubprocessError):
                processes = None;process_error = "Project processes could not be inspected."
            stale = None
            try:self.update_settings(settings or self.settings)
            except LocalServiceError as error:
                if error.status!=409:raise
                stale=error.detail
            services = [self._view(key) for key in self.definitions]
            if stale:
                for service in services:service.update(can_start=False,can_start_independent=False,note=stale)
            return {"project_root":str(self.root),"services":services,"processes":processes,"process_error":process_error,
                    "process_count":len(processes) if processes is not None else None,"active_count":sum(service["connected"] for service in services)}

    def _drain(self, key, pipe, stream):
        try:
            while True:
                line = pipe.readline(8193)
                if not line:
                    break
                if len(line) > 8192 and not line.endswith(b"\n"):
                    while line and not line.endswith(b"\n"):
                        line = pipe.readline(8193)
                    self.buffers[key].append("[Oversized log line omitted]", stream)
                else:
                    self.buffers[key].append(line.decode("utf-8", "replace").rstrip("\r\n"), stream)
        finally:
            pipe.close()

    def start(self, key, independent=False):
        with self.lock:
            item = self._definition(key)
            current = self._view(key)
            if self.closed:
                raise LocalServiceError(409, "The graph is shutting down.")
            if current["owner"] == "graph" or current["connected"]:
                return current
            if not current["can_start"]:
                raise LocalServiceError(409, "This service cannot be started while its port is occupied or no launch command is configured.")
            environment = {**os.environ, **item.get("environment", {}), "PYTHONUNBUFFERED": "1"}
            helper = item.get("credential_command") or item.get("credential_helper")
            if helper and not environment.get(helper["env"]):
                try:
                    result = subprocess.run(helper["argv"], cwd=item["cwd"], capture_output=True, timeout=10)
                    secret = result.stdout.decode().strip() if result.returncode == 0 and len(result.stdout) <= 65536 else ""
                except (OSError, ValueError, subprocess.SubprocessError):
                    secret = ""
                if not secret:
                    raise LocalServiceError(503, "The configured credential helper could not provide the service credential.")
                environment[helper["env"]] = secret
            buffer = self.buffers[key]
            buffer.secrets.update(value for name, value in environment.items() if value and (name == (helper or {}).get("env") or re.search(r"key|token|password|secret", name, re.I)))
            self._read_file_logs(key, label_stale=True)
            if independent:return self._start_independent(key,item,environment)
            read_fd, write_fd = os.pipe()
            try:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("service_runner.py")), str(read_fd)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(read_fd,), start_new_session=True)
                self.owned[key] = Owned(process, write_fd, uuid.uuid4().hex, item)
                self.errors.pop(key, None)
                process.stdin.write((json.dumps({"command": item["command"], "cwd": item["cwd"], "environment": environment, "shutdown_timeout": item["shutdown_timeout"]}) + "\n").encode())
                process.stdin.close()
                for pipe, stream in [(process.stdout, "stdout"), (process.stderr, "stderr")]:
                    threading.Thread(target=self._drain, args=(key, pipe, stream), daemon=True).start()
                buffer.append("Start requested by graph.", "system")
            except (OSError, subprocess.SubprocessError):
                os.close(write_fd)
                failed = self.owned.get(key)
                if failed and failed.pipe == write_fd:
                    failed.pipe = -1
                self.errors[key] = "The configured service could not be launched."
                raise LocalServiceError(503, self.errors[key])
            finally:
                os.close(read_fd)
            return self._view(key)

    def stop(self, key, expected_identity):
        with self.lock:
            item = self._definition(key)
            current = self._view(key)
            if not expected_identity or current["identity"] != expected_identity:
                raise LocalServiceError(409, "Service instance changed; refresh before stopping it.")
            if not current["can_stop"]:
                raise LocalServiceError(409, "This process cannot safely be stopped by the graph.")
            independent = self._independent_alive(key)
            owned = self.owned.get(key) if current["owner"] == "graph" else None
            if independent:
                self.stop_process(f'process-{independent["pid"]}',expected_identity)
            elif owned:
                owned.stopping = True
                os.close(owned.pipe)
                owned.pipe = -1
                owned.process.send_signal(signal.SIGTERM)
            elif not item["endpoints"]:
                self.stop_process(f'process-{current["pid"]}',expected_identity)
            else:
                # Re-read PID/start/command immediately before executing a configured stop.
                _, primary_host, primary_port = local_url(item["endpoints"][0])
                infos = listeners(primary_port, primary_host)
                if fingerprint(infos) != expected_identity or item["require_project_cwd"] and not self._project_owned(infos):
                    raise LocalServiceError(409, "Service process changed; refresh before stopping it.")
                if item.get("stop_command"):
                    try:
                        result = subprocess.run(item["stop_command"], cwd=item["cwd"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=item["shutdown_timeout"] + 5)
                        if result.returncode:
                            raise LocalServiceError(503, "The configured stop command did not complete successfully.")
                    except (OSError, subprocess.SubprocessError):
                        raise LocalServiceError(503, "The configured stop command did not complete successfully.")
                else:
                    for info in infos:
                        if process_info(info["pid"]) != info:
                            raise LocalServiceError(409, "Service process changed; refresh before stopping it.")
                        os.kill(info["pid"], signal.SIGTERM)
            self.buffers[key].append("Stop requested by graph.", "system")
        if owned:
            try:
                owned.process.wait(timeout=item["shutdown_timeout"] + 10)
            except subprocess.TimeoutExpired:
                raise LocalServiceError(503, "Service is still stopping; inspect its status and logs.")
        else:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with self.lock:
                    status = self._view(key)
                if status["identity"] != expected_identity:
                    break
                time.sleep(0.1)
        with self.lock:
            return self._view(key)

    def _read_file_logs(self, key, label_stale=False):
        item = self._definition(key)
        buffer = self.buffers[key]
        paths = list(item["log_files"])
        record = self.independent.get(key)
        if record and record.get("log_file"):paths.append(record["log_file"])
        for name in paths:
            path = Path(name)
            if not path.resolve().is_relative_to(self.root):
                continue
            try:
                with path.open("rb") as source:
                    stat = os.fstat(source.fileno())
                    identity = (stat.st_dev, stat.st_ino)
                    old_identity, offset = buffer.offsets.get(name, (None, max(0, stat.st_size - 65536)))
                    if old_identity not in {None, identity} or stat.st_size < offset:
                        offset = 0
                        buffer.pending.pop(name, None)
                        buffer.discarding.discard(name)
                    if old_identity is None and offset:
                        buffer.discarding.add(name)
                    source.seek(offset)
                    data = source.read(65536)
                    buffer.offsets[name] = (identity, source.tell())
                parts = data.split(b"\n")
                for index, part in enumerate(parts):
                    complete = index < len(parts) - 1
                    if name in buffer.discarding:
                        if complete:
                            buffer.discarding.remove(name)
                        continue
                    record = buffer.pending.pop(name, b"") + part
                    if len(record) > 8192:
                        if not complete:
                            buffer.discarding.add(name)
                        buffer.append(f"[{path.name}] [Oversized log line omitted]", "system")
                    elif complete:
                        tag = f"[{path.name} (prior)]" if label_stale else f"[{path.name}]"
                        buffer.append(f"{tag} {record.decode('utf-8', 'replace').rstrip(chr(13))}", "stdout")
                    else:
                        buffer.pending[name] = record
            except FileNotFoundError:
                continue
            except OSError:
                raise LocalServiceError(503, "A configured service log cannot be read.")

    def logs(self, key, after=0):
        if not isinstance(after, int) or after < 0:
            raise LocalServiceError(400, "Log cursor must be a nonnegative integer.")
        with self.lock:
            self._read_file_logs(key)
            return self.buffers[key].read(after)

    def shutdown(self):
        with self.lock:
            self.closed = True
            owned = [value for value in self.owned.values() if value.process.poll() is None]
            for value in owned:
                value.stopping = True
                if value.pipe >= 0:
                    os.close(value.pipe)
                    value.pipe = -1
                try:
                    value.process.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 100
        for value in owned:
            try:
                value.process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise LocalServiceError(503, "A graph-started service has not finished shutting down.")
