#!/usr/bin/env python3
"""Live event graph for the Agent: resources are fixed, routes are observed."""

import os
import sys
import json
import time
import copy
import asyncio
import hashlib
import re
import secrets
import queue
import threading
import subprocess
from urllib.parse import urlparse
from .xray_auth import load_token, event_headers
from .documents import (
    DocumentError,
    document_payload,
    render_markdown,
    resolve_document,
    save_document,
    move_document,
    get_document_context,
)
from .results import ResultStore, ResultError, IMAGES
from .graph_settings import GraphSettings
from .local_services import LocalServiceManager, LocalServiceError
from .project_settings import ProjectSettings
from .project_transport import ProjectTransport
from .host_runtime import revision as host_revision
from .demo_catalog import demo_bootstrap
from .agent_events import AgentEventJournal, sanitize_event_data
from .session_observation import merge_observation, tool_observation
from .installation import InstallationManager, InstallationError
from .connections import ConnectionModules
from .efficiency import ToolEfficiency
from .observer_config import (
    load_settings,
    refresh_settings,
    apply_environment,
    resource_catalog,
    get_config_revision,
    load_current_block_settings,
    save_block_settings,
    DEFAULT_DOCUMENTS,
    VERSION,
)
from .tools_discovery import discover_tools
from .memory_systems import inspect_memory_systems, resolve_memory, public_system
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Dict, List, Any, Optional
from functools import wraps

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware

REPO_ROOT = Path(__file__).resolve().parent
DASHBOARD_HTML_FILE = REPO_ROOT / "web/dashboard.html"
SERVER_REVISION = host_revision()
SETTINGS = globals().get("_INITIAL_SETTINGS") or load_settings()
if not globals().get("_INITIAL_HUB"):
    apply_environment(SETTINGS)
ROUTES_HISTORY_FILE = SETTINGS["history_file"]
AUTH_TOKEN = load_token(create=True, path=SETTINGS["token_file"])
MAX_STEPS = max(200, int(SETTINGS.get("max_steps", 5000)))
MAX_RUNS = max(0, int(SETTINGS.get("max_runs", 0)))
_persist_queue = queue.Queue(maxsize=1)

def _history_writer():
    while True:
        records = _persist_queue.get()
        try:
            if records is None:return
            ROUTES_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = ROUTES_HISTORY_FILE.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(records, stream, ensure_ascii=False)
            temporary.replace(ROUTES_HISTORY_FILE)
        except OSError:
            pass
        finally:
            _persist_queue.task_done()

threading.Thread(target=_history_writer, daemon=True, name="cad-xray-history").start()

def _enqueue_history(records):
    try:
        _persist_queue.put_nowait(records)
    except queue.Full:
        try:
            _persist_queue.get_nowait(); _persist_queue.task_done()
        except queue.Empty:
            pass
        try: _persist_queue.put_nowait(records)
        except queue.Full: pass

PROJECT_ROOT = SETTINGS["project_root"]
RESULTS = ResultStore(PROJECT_ROOT, ROUTES_HISTORY_FILE.parent / "results")
GRAPH_SETTINGS = GraphSettings(SETTINGS)
EFFICIENCY = ToolEfficiency(PROJECT_ROOT)
LOCAL_SERVICES = LocalServiceManager(SETTINGS)
INSTALLATIONS = InstallationManager(SETTINGS)
AGENT_EVENTS = AgentEventJournal(ROUTES_HISTORY_FILE.parent / "agent-events.sqlite3")
CONNECTIONS = ConnectionModules(SETTINGS, INSTALLATIONS)
PROJECT_SETTINGS = ProjectSettings(CONNECTIONS, LOCAL_SERVICES)
PROJECT_TRANSPORT = globals().get("_INITIAL_HUB") or ProjectTransport(PROJECT_SETTINGS)
PROJECT_SETTINGS.transport = PROJECT_TRANSPORT
INSTALLATIONS.transport = PROJECT_TRANSPORT
_codex_receipts = {}
CAD_AGENT_ROOT = PROJECT_ROOT  # Compatibility alias for existing integrations.

@asynccontextmanager
async def application_lifespan(_app):
    loop = asyncio.get_running_loop()
    if not globals().get("_INITIAL_HUB"):
        PROJECT_TRANSPORT.bind(loop, sys.modules[__name__])
    def receive_observation(event):
        module = "omp" if str(event.get("application", "")).casefold() == "omp" else "codex"
        future = asyncio.run_coroutine_threadsafe(ingest_observation(event, expected_module=module), loop)
        response = future.result(timeout=20)
        if response.status_code >= 400:
            raise ValueError("The recorded agent event could not be applied to the selected graph.")
    try:
        await asyncio.to_thread(CONNECTIONS.start, receive_observation)
        if not globals().get("_INITIAL_HUB"):
            from .host_runtime import release_start_lock
            release_start_lock()
            print(f"Observer host ready at http://127.0.0.1:{SETTINGS['port']}/",flush=True)
        yield
    finally:
        if not globals().get("_INITIAL_HUB"):
            await PROJECT_TRANSPORT.close()
        try:
            await asyncio.to_thread(CONNECTIONS.shutdown)
        finally:
            await asyncio.to_thread(LOCAL_SERVICES.shutdown)
            await asyncio.to_thread(PROJECT_SETTINGS.shutdown)
            await asyncio.to_thread(_persist_queue.join)
            _persist_queue.put_nowait(None)


app = FastAPI(title="Agent Observer", version=VERSION, lifespan=application_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://127.0.0.1:{SETTINGS['port']}", f"http://localhost:{SETTINGS['port']}"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _allowed_origin(origin, host):
    if not origin: return True  # Native clients authenticate separately.
    parsed = urlparse(origin)
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.netloc == host

@app.middleware("http")
async def local_client_guard(request: Request, call_next):
    host = request.headers.get("host", "")
    if urlparse("http://" + host).hostname not in {"localhost", "127.0.0.1", "::1"}:
        return JSONResponse({"detail": "Invalid host"}, status_code=403)
    if not _allowed_origin(request.headers.get("origin"), host):
        return JSONResponse({"detail": "Invalid origin"}, status_code=403)
    if request.url.path.startswith("/api/"):
        token = request.headers.get("x-agent-observer-token") or request.headers.get("x-cad-token", "")
        if not secrets.compare_digest(token, AUTH_TOKEN):
            project = await asyncio.to_thread(PROJECT_TRANSPORT.project_for_token,token) if not globals().get("_INITIAL_HUB") else None
            if project:
                try:
                    raw = await request.body()
                    status,media,data = await asyncio.to_thread(PROJECT_TRANSPORT.request,project,request.url.path+("?" + request.url.query if request.url.query else ""),request.method,raw,request.headers.get("content-type","application/json"))
                    return Response(data,status_code=status,headers={"Content-Type":media,"Cache-Control":"no-store"})
                except InstallationError as error:return JSONResponse({"detail":error.detail},status_code=error.status)
            return JSONResponse({"detail": "Local client authentication required"}, status_code=401)
    if request.method == "POST":
        chunks, length = [], 0
        path = request.url.path
        if path.startswith("/api/results/") and path.endswith("/attachments"):
            limit = 96 * 1024 * 1024
        elif path in {"/api/document", "/api/document/preview", "/api/event"}:
            limit = 8 * 1024 * 1024
        else:
            limit = 262144
        async for chunk in request.stream():
            length += len(chunk)
            if length > limit:
                return JSONResponse({"detail": "Event too large"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
    return response


def load_resource_catalog(*, application=None) -> dict:
    config_file = SETTINGS.get("config_file")
    if config_file and Path(config_file).is_file():
        try:
            refresh_settings(SETTINGS, config_file)
        except Exception:
            pass
    source = str(application or "").casefold()
    if SETTINGS.get("connection", {}).get("module") in {"codex", "omp"} and source in {"codex", "omp"}:
        return resource_catalog({**SETTINGS, "agent_runtime": source, "agent_label": "OMP" if source == "omp" else "Codex"})
    return resource_catalog(SETTINGS)

class GraphState:
    """Static inventory plus only the route confirmed by incoming events."""

    def __init__(self, *, persist=True, catalog=None):
        self.persist = persist
        self.catalog = catalog if catalog is not None else load_resource_catalog()
        self.capability_inventory = copy.deepcopy(self.catalog.get("capability_inventory"))
        self.session_id: Optional[str] = None
        self.native_tool_names: Set[str] = {t["name"].casefold() for t in self.catalog.get("native_tools", []) if isinstance(t, dict) and "name" in t}
        if not self.native_tool_names and self.catalog.get("agent_runtime") != "codex":
            self.native_tool_names = {"read", "write", "edit", "eval", "bash", "glob", "grep", "task", "wait", "web_search"}
        self.reset()

    def set_runtime_inventory(self, session_id: str, inventory: dict):
        from .capabilities import capability_nodes, validate_inventory_schema
        validated = validate_inventory_schema(inventory)
        self.session_id = session_id
        self.capability_inventory = validated
        if "native_tools" in validated and isinstance(validated["native_tools"], list):
            self.native_tool_names = {t["name"].casefold() for t in validated["native_tools"] if isinstance(t, dict) and "name" in t}

        new_nodes = {n["id"]: n for n in capability_nodes(validated)}
        existing_cap_ids = {k for k, v in self.nodes.items() if v.get("kind") in {"mcp", "skill", "integration"} or v.get("group") == "capabilities" or v.get("inventory_resource")}
        used_ids = {edge["from"] for edge in self.edges} | {edge["to"] for edge in self.edges}

        for n_id, n_data in new_nodes.items():
            if n_id in self.nodes:
                prior = self.nodes[n_id]
                self.nodes[n_id] = {
                    **n_data,
                    "status": prior.get("status", "idle"),
                    "reason": prior.get("reason", ""),
                    "payload": prior.get("payload", ""),
                    "duration_ms": prior.get("duration_ms", 0.0),
                    "resource_decision": prior.get("resource_decision"),
                }
            else:
                self.nodes[n_id] = n_data

        config_node_ids = {n["id"] for n in self.config.get("nodes", [])}
        for old_id in existing_cap_ids - set(new_nodes.keys()):
            if old_id in config_node_ids and not self.nodes[old_id].get("inventory_resource"):
                continue
            old_node = self.nodes[old_id]
            if old_id in used_ids or old_node.get("status") != "idle" or old_node.get("resource_decision"):
                old_node["availability"] = "unavailable"
                if old_node.get("kind") == "mcp":
                    old_node["connection"] = "unknown"
            else:
                self.nodes.pop(old_id, None)

    def update_catalog(self, new_catalog: dict):
        self.catalog = new_catalog
        self.config = new_catalog
        if self.capability_inventory and "native_tools" in self.capability_inventory:
            native_list = self.capability_inventory.get("native_tools", [])
            self.native_tool_names = {t["name"].casefold() for t in native_list if isinstance(t, dict) and "name" in t}
        else:
            native_list = new_catalog.get("native_tools", [])
            self.native_tool_names = {t["name"].casefold() for t in native_list if isinstance(t, dict) and "name" in t}

        new_nodes = {n["id"]: n for n in new_catalog.get("nodes", [])}
        used_ids = {edge["from"] for edge in self.edges} | {edge["to"] for edge in self.edges}

        for n_id, n_data in new_nodes.items():
            if n_id in self.nodes:
                prior = self.nodes[n_id]
                merged = {
                    **n_data,
                    "status": prior.get("status", "idle"),
                    "reason": prior.get("reason", ""),
                    "payload": prior.get("payload", ""),
                    "duration_ms": prior.get("duration_ms", 0.0),
                    "resource_decision": prior.get("resource_decision"),
                }
                if prior.get("connection") in {"connected", "connecting"}:
                    merged["connection"] = prior["connection"]
                if prior.get("availability") in {"provided", "connected", "available"}:
                    merged["availability"] = prior["availability"]
                self.nodes[n_id] = merged
            else:
                self.nodes[n_id] = {
                    **n_data,
                    "status": "idle",
                    "reason": "",
                    "payload": "",
                    "duration_ms": 0.0,
                }

        catalog_node_ids = set(new_nodes.keys())
        if self.capability_inventory:
            from .capabilities import capability_nodes
            for r_node in capability_nodes(self.capability_inventory):
                catalog_node_ids.add(r_node["id"])

        to_check = set(self.nodes.keys()) - catalog_node_ids
        for old_id in to_check:
            if old_id == "agent-hub" or any(block.get("id") == old_id for block in self.prompt_blocks):
                continue
            old_node = self.nodes[old_id]
            if old_id in used_ids or old_node.get("status") != "idle" or old_node.get("resource_decision"):
                old_node["availability"] = "unavailable"
                if old_node.get("kind") == "mcp":
                    old_node["connection"] = "unknown"
            else:
                self.nodes.pop(old_id, None)
    def reset(self, same_session: bool = False):
        self.config = self.catalog
        self.prompt = ""
        self.project = SETTINGS["project_name"]
        self.mode = ""
        self.project_spec = ""
        self.attachments: List[str] = []
        self.prompt_blocks: List[dict] = []
        self.nodes = {n["id"]: {**n, "status": "idle", "reason": "", "payload": "", "duration_ms": 0.0}
                      for n in self.config.get("nodes", [])}
        if same_session and self.capability_inventory:
            from .capabilities import capability_nodes
            to_del = [k for k, v in self.nodes.items() if v.get("kind") in {"mcp", "skill", "integration"} or v.get("group") == "capabilities" or v.get("inventory_resource")]
            for k in to_del:
                self.nodes.pop(k, None)
            for node in capability_nodes(self.capability_inventory):
                self.nodes[node["id"]] = node
        elif not same_session:
            self.capability_inventory = None
            self.session_id = None
        self.edges: List[dict] = []
        self.history: List[dict] = []
        self.step_counter = 0
        self.current_run_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.run_started_at: Optional[str] = None
        self.run_source = "agent"
        self.run_context: dict = {}
        if not same_session:
            self.session_observation = {}
        self.orchestrator_phase = "idle"
        self.orchestrator_action = "Waiting for observed Agent events"
        self.latest_resource_id: Optional[str] = None
        self.results = None
    def _capture_context(self, event: dict):
        for key in ("session_id", "session_name", "application", "project_id", "requested_model", "model", "router_model", "backend", "router_label", "database_name", "build_time", "build_version"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                self.run_context[key] = value.strip()[:240]
        for key in ("capture_mode", "replay", "observed_mid_turn"):
            if isinstance(event.get(key), (str, bool)):
                self.run_context[key] = event[key]

    def _persist_run(self, status: str):
        if not self.persist or not self.current_run_id:
            return
        record = {"run_id": self.current_run_id, "session_id": self.session_id, "started_at": self.run_started_at,
                  "finished_at": datetime.now(timezone.utc).isoformat() if status != "running" else None,
                  "status": status, "source": self.run_source, "project": self.project, "context": copy.deepcopy(self.run_context),
                  "mode": self.mode, "project_spec": self.project_spec, "attachments": list(self.attachments),
                  "prompt": self.prompt, "prompt_blocks": copy.deepcopy(self.prompt_blocks),
                  "capability_inventory": copy.deepcopy(self.capability_inventory),
                  "session_observation": copy.deepcopy(self.session_observation),
                  "steps": list(self.history[-MAX_STEPS:])}
        existing = next((i for i, item in enumerate(saved_runs) if item.get("run_id") == self.current_run_id), None)
        if existing is None:
            saved_runs.insert(0, record)
        else:
            saved_runs[existing] = record
        if MAX_RUNS:
            del saved_runs[MAX_RUNS:]
        _enqueue_history(list(saved_runs))

    def _record_step(self, event: dict, description: str):
        event_copy = copy.deepcopy({key: value for key, value in event.items() if key != "file_snapshots"})
        for key, value in tuple(event_copy.items()):
            if key not in {"input", "args", "result"} and isinstance(value, str) and len(value) > 4000:
                event_copy[key] = value[:4000] + "…"
        self.history.append({
            "step_index": self.step_counter, "run_id": self.run_id, "session_id": self.session_id,
            "run_started_at": self.run_started_at, "timestamp": datetime.now(timezone.utc).isoformat(),
            "description": description, "event": event_copy, "prompt": self.prompt,
            "prompt_blocks": copy.deepcopy(self.prompt_blocks), "project": self.project,
            "mode": self.mode, "project_spec": self.project_spec, "attachments": list(self.attachments),
            "orchestrator_phase": self.orchestrator_phase, "orchestrator_action": self.orchestrator_action,
            "nodes": copy.deepcopy({key: value for key, value in self.nodes.items() if value.get("status") != "idle" or value.get("resource_decision") is not None}), "edges": copy.deepcopy(self.edges),
            "run_source": self.run_source, "context": copy.deepcopy(self.run_context),
            "session_observation": copy.deepcopy(self.session_observation),
            "capability_inventory": copy.deepcopy(self.capability_inventory),
        })

        self.step_counter += 1
        del self.history[:-MAX_STEPS]

    def _find_document(self, target: str) -> Optional[dict]:
        clean = str(target or "").removeprefix("file://").replace("\\", "/").strip()
        clean = re.sub(r":\d+(?:[-+]\d*)?(?::raw)?$", "", clean)
        if not clean:
            return None
        docs = [node for node in self.nodes.values() if node.get("kind") == "document"]
        exact = [node for node in docs if clean in {node.get("id"), node.get("label"), node.get("path"), node.get("absolute_path")}]
        if exact:
            return exact[0] if len(exact) == 1 else None
        candidate = Path(clean).expanduser()
        resolved = (candidate if candidate.is_absolute() else PROJECT_ROOT / candidate).resolve()
        matches = []
        for node in docs:
            absolute = node.get("absolute_path")
            path = node.get("path") or node.get("label")
            if absolute and resolved == Path(absolute).expanduser().resolve():
                matches.append(node)
            elif not absolute and path and not str(path).startswith("@vault/") and resolved == (PROJECT_ROOT / path).resolve():
                matches.append(node)
        if matches:
            return matches[0] if len(matches) == 1 else None
        if not candidate.is_absolute() and "/" not in clean:
            matches = [node for node in docs if Path(node.get("path") or node["label"]).name == clean]
        return matches[0] if len(matches) == 1 else None

    def _resolve_resource_target(self, target: str) -> dict:
        clean = str(target or "").strip()
        if not clean:
            raise ValueError("target_id is required and must be a non-empty string")

        # Tier 1: Exact matches against node ID, label, or document path
        exact_matches = {}
        for n_id, node in self.nodes.items():
            if node.get("kind") not in {"document", "tool", "command", "prompt_source", "memory"}:
                continue
            if n_id == clean or node.get("label") == clean:
                exact_matches[n_id] = node
            elif node.get("kind") in {"document", "prompt_source"} and (node.get("path") == clean or node.get("absolute_path") == clean):
                exact_matches[n_id] = node

        if len(exact_matches) > 1:
            matched_keys = ", ".join(sorted(exact_matches.keys()))
            raise ValueError(f"Ambiguous target '{clean}': matches multiple resources ({matched_keys})")
        if len(exact_matches) == 1:
            return next(iter(exact_matches.values()))

        # Tier 2: Document registered path resolution
        clean_path = clean.replace("file://", "").replace("\\", "/").strip()
        path_matches = {}
        for n_id, node in self.nodes.items():
            if node.get("kind") in {"document", "prompt_source"}:
                doc_label = str(node.get("label") or "").replace("\\", "/")
                doc_path = str(node.get("path") or "").replace("\\", "/")
                doc_abs = str(node.get("absolute_path") or "").replace("\\", "/")

                matched = False
                if clean_path in {doc_label, doc_path, doc_abs}:
                    matched = True
                elif clean_path.lstrip("/") in {doc_label, doc_path}:
                    matched = True
                elif clean_path.endswith("/" + doc_label) or clean_path.endswith("/" + doc_path):
                    matched = True
                elif doc_label.endswith("/" + clean_path) or doc_path.endswith("/" + clean_path):
                    matched = True
                elif "/" not in clean_path:
                    if Path(doc_label).name == clean_path or Path(doc_path).name == clean_path:
                        matched = True

                if not matched:
                    try:
                        resolved_path = Path(clean_path).expanduser().resolve()
                        if doc_abs and str(resolved_path) == doc_abs:
                            matched = True
                        elif resolved_path.is_relative_to(PROJECT_ROOT):
                            rel = resolved_path.relative_to(PROJECT_ROOT).as_posix()
                            if rel in {doc_label, doc_path}:
                                matched = True
                    except Exception:
                        pass

                if matched:
                    path_matches[n_id] = node

        if len(path_matches) > 1:
            matched_keys = ", ".join(sorted(path_matches.keys()))
            raise ValueError(f"Ambiguous target '{clean}': matches multiple document paths ({matched_keys})")
        if len(path_matches) == 1:
            return next(iter(path_matches.values()))

        raise ValueError(f"Unknown resource target: '{clean}'")

    def _ensure_tool(self, name: str, node_id: Optional[str] = None) -> dict:
        raw_name = str(name or "tool").strip()
        raw_lower = raw_name.casefold()

        for item in self.nodes.values():
            if item.get("kind") != "tool":
                continue
            if item.get("label", "").casefold() == raw_lower or item.get("name", "").casefold() == raw_lower:
                return item
            match_obj = item.get("match", {})
            if isinstance(match_obj, dict):
                aliases = [str(x).casefold() for x in match_obj.get("tool_names", []) if isinstance(x, str)]
                if raw_lower in aliases:
                    return item
                prefixes = [str(x).casefold() for x in match_obj.get("command_prefixes", []) if isinstance(x, str)]
                if raw_lower in prefixes:
                    return item

        if node_id in self.nodes and self.nodes[node_id].get("kind") == "document": node_id = None
        stable_id = node_id or ("observed_tool_" + hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:12])
        if stable_id not in self.nodes:
            self.nodes[stable_id] = {"id": stable_id, "label": raw_name, "category": "tool",
                                     "group": "Tools called by the agent", "kind": "tool",
                                     "description": "Tool observed in an agent event.",
                                     "status": "idle", "reason": "", "payload": "", "duration_ms": 0.0}
        return self.nodes[stable_id]

    @staticmethod
    def _explicit_paths(event: dict) -> list[str]:
        candidates = []
        def visit(value):
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    if value.strip() and not any(char in value for char in "\r\n\0"):
                        candidates.append(value.strip())
                else:
                    if isinstance(parsed, (dict, list)):
                        visit(parsed)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, dict):
                for key in ("path", "file_path", "filepath", "filename", "file", "target_id", "target", "target_path", "source_path", "dest_path", "destination", "uri", "url"):
                    if isinstance(value.get(key), str):
                        candidates.append(value[key].strip())
                for key in ("paths", "file_paths", "files", "changes", "file_changes", "edits", "patch", "edit"):
                    if isinstance(value.get(key), (dict, list)):
                        visit(value[key])
        visit(event)
        for key in ("input", "args", "arguments", "parameters"):
            if event.get(key) is not None:
                visit(event[key])
        return list(dict.fromkeys(candidate for candidate in candidates if candidate))

    def _connect_agent_to(self, target_id: str):
        edge = {"from": "agent-hub", "to": target_id, "label": "observed interaction"}
        if edge not in self.edges:
            self.edges.append(edge)

    def process_event(self, event: dict) -> dict:
        if not isinstance(event, dict): raise ValueError("Event must be an object")
        event = dict(event or {})
        event_type = str(event.get("type", event.get("event_type", ""))).upper()
        aliases = {"GENERATION_START": "PROMPT_START", "PROMPT_RECEIVED": "PROMPT_START",
                   "KNOWLEDGE_ACCESS": "KNOWLEDGE_ACCESS", "TOOL_ACCESS": "TOOL_CALL_START",
                   "TOOL_RESULT": "TOOL_CALL_END", "GENERATION_ERROR": "GENERATION_END"}
        raw_event_type = event_type
        event_type = aliases.get(event_type, event_type)
        event["type"] = event_type
        if raw_event_type == "GENERATION_ERROR":
            event["success"] = False
            event["status"] = "failed"
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        incoming_id = str(event.get("run_id") or "")
        if event_type not in {"RESET", "RUNTIME_INVENTORY", "SESSION_OBSERVATION"}:
            if not incoming_id:
                raise ValueError("run_id is required")
            if self.current_run_id and incoming_id != self.current_run_id:
                raise ValueError("Event belongs to another run")
        if event_type == "RESET":
            if self.current_run_id:
                self._persist_run("interrupted")
            self.reset()
            return self.get_snapshot()

        if event_type == "SESSION_OBSERVATION":
            incoming_session = str(event.get("session_id") or "")
            if not incoming_session or self.session_id and self.session_id != incoming_session:
                raise ValueError("Observation requires its own session_id")
            self.session_id = incoming_session
            self.session_observation = merge_observation(self.session_observation, event.get("observation"))
            self.session_observation.setdefault("session", {})["id"] = incoming_session
            self._capture_context(event)
            if self.current_run_id:
                self._persist_run("running")
            elif self.persist and self.run_id:
                saved = next((run for run in saved_runs if run.get("run_id") == self.run_id), None)
                if saved is not None:
                    saved["session_observation"] = copy.deepcopy(self.session_observation)
                    _enqueue_history(list(saved_runs))
            return self.get_snapshot()

        if event_type == "RUNTIME_INVENTORY":
            session_id = str(event.get("session_id") or event.get("run_id") or "")
            inventory = event.get("inventory")
            if not isinstance(inventory, dict):
                raise ValueError("inventory must be an object")
            self.set_runtime_inventory(session_id, inventory)
            self._capture_context(event)
            mcp_cnt = len(inventory.get("mcp", []))
            skill_cnt = len(inventory.get("skills", []))
            self.orchestrator_action = f"Capabilities registered ({mcp_cnt} MCP, {skill_cnt} skills)"
            self._record_step(event, "Session capability inventory registered.")
            return self.get_snapshot()

        if event_type == "PROMPT_START":
            if self.current_run_id:
                self._persist_run("interrupted")
            incoming_session = str(event.get("session_id") or "")
            is_same = bool(incoming_session and incoming_session == self.session_id)
            saved_inv = session_inventories.get(incoming_session) if self.persist and incoming_session else None
            if self.persist:
                self.catalog = load_resource_catalog(application=event.get("application"))
                self.native_tool_names = {tool["name"].casefold() for tool in self.catalog.get("native_tools", [])
                                          if isinstance(tool, dict) and "name" in tool}
            self.reset(same_session=is_same)
            if saved_inv:
                self.set_runtime_inventory(incoming_session, saved_inv)
            self.current_run_id = str(event.get("run_id") or f"run_{int(time.time() * 1000)}")
            self.run_id = self.current_run_id
            self.run_started_at = datetime.now(timezone.utc).isoformat()
            self.results = RESULTS.begin(self.run_id) if self.persist else None
            self.run_source = str(event.get("source", "agent"))
            self.run_context = {
                "application": self.config.get("application", SETTINGS["agent_label"]),
                "project_root": str(PROJECT_ROOT),
                "knowledge_sources": copy.deepcopy(self.config.get("document_sources", [])),
                "document_count": sum(n.get("kind") == "document" for n in self.nodes.values()),
                "project_structure": sorted(p.name + ("/" if p.is_dir() else "") for p in PROJECT_ROOT.iterdir() if not p.name.startswith("."))[:100] if self.persist else [],
            }
            self.prompt = str(event.get("prompt", ""))
            self.project = str(event.get("project", self.project))
            self.mode = str(event.get("mode", ""))
            self.project_spec = str(event.get("project_spec", ""))
            self.attachments = [str(item) for item in event.get("attachments", []) if str(item).strip()]
            self.prompt_blocks = [{"id": "request_prompt", "kind": "prompt", "label": "Project prompt",
                                   "text": self.prompt}]
            project_spec = str(event.get("project_spec", "")).strip()
            if project_spec:
                self.prompt_blocks.append({"id": "project_spec", "kind": "spec", "label": "Project specification",
                                           "text": project_spec})
            comments = event.get("comments", [])
            if isinstance(comments, list):
                for index, comment in enumerate(comments, 1):
                    if isinstance(comment, str):
                        comment = {"text": comment}
                    text = str(comment.get("text", "")).strip()
                    if text:
                        self.prompt_blocks.append({"id": str(comment.get("id") or f"comment_{index}"),
                                                   "kind": "comment", "label": str(comment.get("label") or f"Refinement comment {index}"),
                                                   "text": text, "region": str(comment.get("region", ""))})
            self.orchestrator_phase = "running"
            self.orchestrator_action = "Agent started the request"
            sources = [block["id"] for block in self.prompt_blocks]
            for source_id in sources:
                self.edges.append({"from": source_id, "to": "agent-hub", "label": "request input"})
            self._capture_context(event)
            self._record_step(event, "Request received by the agent; route started.")
            self._persist_run("running")
            return self.get_snapshot()

        if not self.current_run_id:
            # Ignore detached events instead of inventing a route or a prompt.
            return self.get_snapshot()

        if event_type != "RESOURCE_DECISION":
            self.orchestrator_phase = "running"
        target = str(event.get("target_id") or event.get("path") or event.get("file_path") or "")
        tool_name = str(event.get("tool") or event.get("tool_name") or event.get("operation") or event.get("name") or "").strip()
        if not tool_name and isinstance(event.get("input"), dict):
            tool_name = str(event["input"].get("tool") or event["input"].get("tool_name") or event["input"].get("operation") or "").strip()
        description = ""

        if event_type == "RESOURCE_DECISION":
            raw_decision = event.get("decision")
            if not isinstance(raw_decision, str) or not raw_decision.strip():
                raise ValueError("decision is required and must be 'not_required' or 'bypassed'")
            decision = raw_decision.strip().lower()
            if decision not in {"not_required", "bypassed"}:
                raise ValueError(f"Invalid decision '{raw_decision}'. Must be 'not_required' or 'bypassed'")

            reason = event.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("reason is required and must be a non-empty string")
            reason = reason.strip()

            raw_target = event.get("target_id")
            if raw_target is None:
                raw_target = event.get("target")
            if not isinstance(raw_target, str) or not raw_target.strip():
                raise ValueError("target_id is required and must be a non-empty string")
            clean_target = raw_target.strip()

            node = self._resolve_resource_target(clean_target)

            raw_req = event.get("requirement")
            requirement = str(raw_req).strip() if isinstance(raw_req, str) and raw_req.strip() else None

            self.orchestrator_phase = "running"
            step = self.step_counter + 1
            node["resource_decision"] = {
                "decision": decision,
                "reason": reason,
                "requirement": requirement,
                "step": step,
            }
            req_suffix = f" · req: {requirement}" if requirement else ""
            description = f"Resource decision · {node.get('label', node['id'])} ({decision}): {reason}{req_suffix}"
            self.orchestrator_action = f"Resource decision · {node.get('label', node['id'])}: {decision}"
        elif event_type == "THINKING":
            thought = str(event.get("thought", "") or "").strip()
            text = str(event.get("text", "") or "").strip()
            phase = str(event.get("phase", "") or "").strip()
            self.orchestrator_action = phase or (thought[:120] if thought else "Agent is processing the request")
            if "step completed" in self.orchestrator_action.lower():
                for node in self.nodes.values():
                    if node.get("status") == "active": node["status"] = "observed"
            description = f"💭 {thought}" if thought else (phase or text or self.orchestrator_action)
        elif event_type == "MODEL_INFO":
            description = "Model reported by the agent transport"
            self.session_observation = merge_observation(self.session_observation, {"model": {"id": event.get("model"), "effort": event.get("reasoning_effort")}})
        elif event_type == "PROMPT_UPDATE":
            self.prompt = str(event.get("prompt") or "")
            for block in self.prompt_blocks:
                if block.get("id") == "request_prompt":
                    block["text"] = self.prompt
            description = "Recorded request received"
        elif event_type == "USAGE_UPDATE":
            description = "Provider token usage reported"
        elif event_type == "REQUEST_MANIFEST":
            description = "Request context assembled: " + str(event.get("backend", "unknown"))
            self.orchestrator_action = description
        elif event_type == "KNOWLEDGE_ACCESS":
            node = self._find_document(target)
            if node:
                node["status"] = "hit" if str(event.get("success", True)).lower() not in {"false", "0", "no"} else "error"
                node["reason"] = str(event.get("reason", "Read into the request context"))
                node["payload"] = str(event.get("output", event.get("payload_preview", "")))[:800]
                if event.get("estimated_tokens"):
                    try:
                        node["estimated_tokens"] = int(event["estimated_tokens"])
                    except (ValueError, TypeError):
                        pass
                self.latest_resource_id = node["id"]
                self._connect_agent_to(node["id"])
                description = f"Read document {node['label']} into context"
            else:
                description = f"Document access: {target}"
            self.orchestrator_action = description
        elif event_type in {"TOOL_CALL_START", "TOOL_CALL_END", "SANDBOX_CHECK"}:
            explicit_paths = self._explicit_paths(event)
            explicit_path = explicit_paths[0] if explicit_paths else ""
            candidate = explicit_path or target or ""
            is_start = event_type == "TOOL_CALL_START" and str(event.get("status", "")).lower() not in {"ok", "success", "pass", "error", "failed"}
            success_raw = event.get("success", str(event.get("status", "ok")).lower() not in {"error", "failed", "failure"})
            success = success_raw if isinstance(success_raw, bool) else str(success_raw).lower() in {"true", "1", "yes", "ok", "success", "pass"}
            op_status = "active" if is_start else ("hit" if success else "error")

            result_details = {}
            if isinstance(event.get("result"), dict) and isinstance(event["result"].get("details"), dict):
                result_details = event["result"]["details"]
            elif isinstance(event.get("details"), dict):
                result_details = event["details"]

            explicit_mcp_server = event.get("mcp_server") or result_details.get("serverName") or result_details.get("mcpServerName")
            explicit_mcp_tool = event.get("mcp_tool") or result_details.get("mcpToolName") or result_details.get("toolName")

            native_hint = event.get("native_tool") is True and not explicit_mcp_server and not re.match(r"^mcp(?:__|[.:])", tool_name, re.I)
            is_native = bool(tool_name and (tool_name.casefold() in self.native_tool_names or native_hint))
            if native_hint and tool_name.casefold() not in self.native_tool_names:
                self.native_tool_names.add(tool_name.casefold())
                if self.capability_inventory is None:
                    self.capability_inventory = copy.deepcopy(self.catalog.get("capability_inventory") or {})
                self.capability_inventory.setdefault("native_tools", []).append({"name": tool_name, "source": "codex-runtime", "description": "Native operation reported by the agent runtime."})

            node = resolve_memory(self.nodes.values(), event)
            if node:
                node["status"] = op_status
                node["reason"] = f"Recorded {event.get('memory_operation', 'memory')} request"
                node["payload"] = str(event.get("result", event.get("input", "")))[:1000]
                node["duration_ms"] = float(event.get("duration_ms", 0) or 0)
                self.latest_resource_id = node["id"]
                self._connect_agent_to(node["id"])
                description = ("Memory · " if is_start else "Memory result · ") + node["label"]
                self.orchestrator_action = description
            elif is_native:
                cmd_id = f"cmd_{hashlib.sha1(tool_name.encode()).hexdigest()[:12]}"
                if cmd_id not in self.nodes:
                    self.nodes[cmd_id] = {
                        "id": cmd_id, "label": tool_name, "category": "command",
                        "group": "commands", "kind": "command",
                        "description": f"Native command '{tool_name}'",
                        "status": "idle", "reason": "", "payload": "", "duration_ms": 0.0
                    }
                node = self.nodes[cmd_id]
                node["status"] = op_status
                node["reason"] = str(event.get("reason", event.get("query", "")))
                node["payload"] = str(event.get("output", event.get("result", event.get("input", event.get("payload_preview", "")))))[:1000]
                node["duration_ms"] = float(event.get("duration_ms", 0) or 0)
                self.latest_resource_id = node["id"]
                self._connect_agent_to(node["id"])
                description = ("Command · " if is_start else "Command result · ") + tool_name
                self.orchestrator_action = description
            else:
                matching_mcp = None
                if explicit_mcp_server:
                    matching_mcp = next((n for n in self.nodes.values() if n.get("kind") == "mcp" and n.get("name") == explicit_mcp_server), None)
                if not matching_mcp and tool_name:
                    matching_mcp = next((n for n in self.nodes.values() if n.get("kind") == "mcp" and tool_name in n.get("tool_names", [])), None)

                if not matching_mcp and explicit_mcp_server:
                    # First-seen direct MCP call with explicit owner without prior inventory
                    srv_id = f"mcp_{hashlib.sha1(explicit_mcp_server.encode()).hexdigest()[:12]}"
                    conn = "unknown"
                    avail = "unknown"
                    if srv_id not in self.nodes:
                        self.nodes[srv_id] = {
                            "id": srv_id, "kind": "mcp", "name": explicit_mcp_server, "label": explicit_mcp_server,
                            "group": "capabilities", "category": "MCP",
                            "description": f"Runtime MCP server '{explicit_mcp_server}'",
                            "connection": conn, "availability": avail, "tool_names": [tool_name] if tool_name else [],
                            "status": "idle", "reason": "", "payload": "", "duration_ms": 0.0
                        }
                    matching_mcp = self.nodes[srv_id]

                if matching_mcp:
                    node = matching_mcp
                    node["status"] = op_status
                    node["reason"] = str(event.get("reason", f"Invoked {tool_name}"))
                    node["payload"] = str(event.get("output", event.get("result", event.get("input", event.get("payload_preview", "")))))[:1000]
                    node["duration_ms"] = float(event.get("duration_ms", 0) or 0)
                    self.latest_resource_id = node["id"]
                    self._connect_agent_to(node["id"])
                    description = ("MCP call · " if is_start else "MCP result · ") + f"{node['name']}:{tool_name}"
                    self.orchestrator_action = description
                else:
                    node = self._ensure_tool(tool_name or candidate or "Tool", event.get("node_id"))
                    node["status"] = op_status
                    node["reason"] = str(event.get("reason", event.get("query", "")))
                    node["payload"] = str(event.get("output", event.get("result", event.get("input", event.get("payload_preview", "")))))[:1000]
                    node["duration_ms"] = float(event.get("duration_ms", 0) or 0)
                    self.latest_resource_id = node["id"]
                    self._connect_agent_to(node["id"])
                    description = ("Tool call · " if is_start else "Tool result · ") + (tool_name or node["label"])
                    self.orchestrator_action = description

            documents = {document["id"]: document for path in explicit_paths if (document := self._find_document(path))}
            for document in documents.values():
                document["status"] = op_status
                document["reason"] = f"Access through tool {tool_name or node['label']}"
                document["payload"] = node["payload"]
                doc_edge = {"from": node["id"], "to": document["id"], "label": "call file"}
                if doc_edge not in self.edges:
                    self.edges.append(doc_edge)
                self.latest_resource_id = document["id"]

            # Explicit mounted path xd://<actual MCP tool name> (native write only, never read schema / edit / glob)
            if tool_name == "write" and explicit_path.startswith("xd://"):
                xd_target = explicit_path[len("xd://"):].split("?")[0].strip("/")
                target_mcp_node = None
                if explicit_mcp_server:
                    target_mcp_node = next((n for n in self.nodes.values() if n.get("kind") == "mcp" and n.get("name") == explicit_mcp_server), None)
                if not target_mcp_node and xd_target:
                    target_mcp_node = next((n for n in self.nodes.values() if n.get("kind") == "mcp" and xd_target in n.get("tool_names", [])), None)

                if not target_mcp_node and explicit_mcp_server:
                    srv_id = f"mcp_{hashlib.sha1(explicit_mcp_server.encode()).hexdigest()[:12]}"
                    conn = "unknown"
                    avail = "unknown"
                    if srv_id not in self.nodes:
                        self.nodes[srv_id] = {
                            "id": srv_id, "kind": "mcp", "name": explicit_mcp_server, "label": explicit_mcp_server,
                            "group": "capabilities", "category": "MCP",
                            "description": f"Runtime MCP server '{explicit_mcp_server}'",
                            "connection": conn, "availability": avail, "tool_names": [xd_target] if xd_target else [],
                            "status": "idle", "reason": "", "payload": "", "duration_ms": 0.0
                        }
                    target_mcp_node = self.nodes[srv_id]

                if target_mcp_node:
                    target_mcp_node["status"] = op_status
                    target_mcp_node["reason"] = f"Invoked via write xd://{xd_target}"
                    xd_edge = {"from": node["id"], "to": target_mcp_node["id"], "label": "mounted MCP device"}
                    if xd_edge not in self.edges: self.edges.append(xd_edge)

            # A shell path counts as skill access only when the runtime reports an actual read.
            skill_paths = explicit_paths if tool_name in {"read", "read_file"} else [
                action["path"] for action in event.get("file_actions", [])
                if isinstance(action, dict) and action.get("action") == "read" and isinstance(action.get("path"), str)
            ] if isinstance(event.get("file_actions"), list) else []
            skills = [resource for resource in self.nodes.values() if resource.get("kind") == "skill"]
            matched_skills = {}
            for skill in skills:
                name = skill.get("name", "")
                if event.get("skill_name") == name and name:
                    matched_skills[skill["id"]] = skill
                for path in skill_paths:
                    uri = "skill://" + name
                    if name and (path == uri or path.startswith(uri + "/")):
                        matched_skills[skill["id"]] = skill
                    elif skill.get("path") and not path.startswith("skill://"):
                        candidate_path = Path(path)
                        candidate_path = (PROJECT_ROOT / candidate_path).resolve()
                        if candidate_path == Path(skill["path"]).expanduser().resolve():
                            matched_skills[skill["id"]] = skill
            for skill in matched_skills.values():
                skill["status"] = op_status
                skill["reason"] = f"Skill accessed via {tool_name}"
                edge = {"from": node["id"], "to": skill["id"], "label": "skill access"}
                if edge not in self.edges:
                    self.edges.append(edge)

            # Recognize native shell names without relabeling the recorded command.
            is_shell_tool = tool_name.casefold() in {"bash", "shell", "sh", "exec", "zsh", "exec_command", "shell_command", "run_command", "terminal"}
            if is_shell_tool:
                cmd_text = None
                if isinstance(event.get("input"), dict):
                    cmd_text = event["input"].get("command", event["input"].get("cmd"))
                elif isinstance(event.get("command"), str):
                    cmd_text = event["command"]
                elif isinstance(event.get("args"), dict) and "command" in event["args"]:
                    cmd_text = event["args"]["command"]
                elif isinstance(candidate, str) and candidate.startswith("rtk "):
                    cmd_text = candidate

                if cmd_text and isinstance(cmd_text, str):
                    import shlex
                    tokens = []
                    try:
                        tokens = shlex.split(cmd_text)
                    except ValueError:
                        tokens = cmd_text.strip().split()
                    idx = 0
                    while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("="):
                        idx += 1
                    if idx < len(tokens) and Path(tokens[idx]).name == "rtk":
                        idx += 1
                        if idx < len(tokens) and tokens[idx] == "proxy":
                            idx += 1
                        while idx < len(tokens) and tokens[idx].startswith("-"):
                            idx += 1
                        inner_cmd = Path(tokens[idx]).name.lower() if idx < len(tokens) else "rtk"

                        rtk_node = self.nodes.get("integration_rtk")
                        if rtk_node:
                            rtk_node["status"] = op_status
                            rtk_node["reason"] = f"Wrapped execution: {inner_cmd}"
                            rtk_edge = {"from": node["id"], "to": "integration_rtk", "label": "wrapped by RTK"}
                            if rtk_edge not in self.edges: self.edges.append(rtk_edge)

                        for t_node in [n for n in self.nodes.values() if n.get("kind") == "tool"]:
                            if t_node.get("label", "").lower() == inner_cmd or inner_cmd in [p.lower() for p in t_node.get("match", {}).get("command_prefixes", [])]:
                                t_node["status"] = op_status
                                t_edge = {"from": "integration_rtk", "to": t_node["id"], "label": "invoked tool"}
                                if t_edge not in self.edges: self.edges.append(t_edge)
        elif event_type == "CODEGEN_START":
            self.orchestrator_action = f"Generating code ({event.get('language', 'CAD')})"
            description = self.orchestrator_action
        elif event_type == "GENERATION_END":
            status = str(event.get("status", "completed")).lower()
            waiting = status in {"waiting", "waiting_for_input"}
            ok_raw = event.get("success", status not in {"failed", "error", "cancelled"})
            success = ok_raw if isinstance(ok_raw, bool) else str(ok_raw).lower() not in {"false", "0", "no", "failed", "error", "cancelled"}
            for node in self.nodes.values():
                if node.get("status") == "active": node["status"] = "observed"
            result_id = "run_result"
            self.nodes[result_id] = {"id": result_id, "label": "Agent result", "category": "result",
                                     "group": "Run result", "kind": "result",
                                     "description": "Final response delivered by the agent.",
                                     "status": "waiting" if waiting else ("hit" if success else "error"), "reason": "",
                                     "payload": str(event.get("result", event.get("payload_preview", "")))[:2400], "duration_ms": 0.0}
            text = event.get("result", event.get("payload_preview", ""))
            if isinstance(text, dict): text = text.get("text", "")
            if self.results:
                self.nodes[result_id]["result"] = self.results.finish(str(text or ""), "waiting" if waiting else "completed" if success else "failed", event)
            elif not self.persist:
                self.nodes[result_id]["result"] = {"text":str(text or ""), "status":"waiting" if waiting else "completed" if success else "failed", "files":[]}
            if self.latest_resource_id:
                self.edges.append({"from": self.latest_resource_id, "to": result_id, "label": "result"})
            else:
                self.edges.append({"from": "request_prompt", "to": result_id, "label": "result"})
            self.orchestrator_phase = "waiting" if waiting else ("complete" if success else "error")
            self.orchestrator_action = "Waiting for user input" if waiting else ("Result delivered" if success else "Generation failed")
            description = str(event.get("result", event.get("payload_preview", self.orchestrator_action)))[:1200]
        else:
            raise ValueError(f"Unknown event type: {event_type or 'empty'}")

        if event_type in {"TOOL_CALL_START", "TOOL_CALL_END"}:
            observed = {**event, "type": event_type}
            if self.persist:
                EFFICIENCY.observe(observed)
            if "token_metrics" in observed:
                event["token_metrics"] = observed["token_metrics"]
            if self.results:
                self.results.observe(observed)
        patch = tool_observation(event)
        if patch:
            self.session_observation = merge_observation(self.session_observation, patch)
        self._capture_context(event)
        self._record_step(event, description)
        if event_type == "GENERATION_END":
            self._persist_run("waiting" if waiting else ("completed" if self.orchestrator_phase == "complete" else "failed"))
            self.current_run_id = None
        else:
            self._persist_run("running")
        return self.get_snapshot()

    def get_snapshot(self) -> dict:
        counts = {status: sum(1 for node in self.nodes.values() if node.get("status") == status)
                  for status in ("active", "hit", "error", "idle")}
        return {"orchestrator_action": self.orchestrator_action, "orchestrator_phase": self.orchestrator_phase,
                "run_source": self.run_source, "context": copy.deepcopy(self.run_context), "run_id": self.run_id, "run_started_at": self.run_started_at, "project": self.project,
                "session_observation": copy.deepcopy(self.session_observation),
                "mode": self.mode, "project_spec": self.project_spec, "attachments": list(self.attachments),
                "prompt": self.prompt, "prompt_blocks": copy.deepcopy(self.prompt_blocks),
                "nodes": self.nodes, "edges": copy.deepcopy(self.edges), "sandbox_nodes": {},
                "capability_inventory": self.capability_inventory or self.config.get("capability_inventory"),
                "native_tools": copy.deepcopy((self.capability_inventory if self.capability_inventory is not None else self.config.get("capability_inventory", {})).get("native_tools", self.config.get("native_tools", []))),
                "block_layouts": self.config.get("block_layouts", {}),
                "last_alert": None, "metrics": {**counts, "total_nodes": len(self.nodes)},
                "history_count": len(self.history), "latest_step": self.history[-1] if self.history else None,
                "recent_events": [
                    {"timestamp": item["timestamp"], "description": item["description"], "event": item["event"]}
                    for item in self.history[-60:]
                ]}


_session_tombstones = {}

def deleted_session_ids(role):
    from .session_deletion import read_session_tombstones
    key = (str(Path(SETTINGS["history_file"]).parent), role)
    if key not in _session_tombstones:
        _session_tombstones[key] = read_session_tombstones(SETTINGS, PROJECT_ROOT, role)
    return _session_tombstones[key]

def active_repository_role():
    return "working_repository" if SETTINGS.get("connection", {}).get("module") in {"codex", "omp"} else "agent_system"


def _recorded_module(application, session_id):
    application = str(application).casefold()
    if application == "codex":
        return "codex"
    return "omp" if application == "omp" and str(session_id).startswith("omp:") else "project"

# Saved routes stay local to this project folder and can be replayed after restart.
try:
    saved_runs = json.loads(ROUTES_HISTORY_FILE.read_text(encoding="utf-8")) if ROUTES_HISTORY_FILE.exists() else []
    if not isinstance(saved_runs, list):
        saved_runs = []
    for saved_run in saved_runs:
        prompt = str(saved_run.get("prompt", ""))
        if not saved_run.get("source"):
            saved_run["source"] = "legacy"
        if saved_run.get("status") == "running":
            saved_run["status"] = "interrupted"
except (OSError, json.JSONDecodeError):
    saved_runs = []
for role in ("working_repository", "agent_system"):
    deleted = deleted_session_ids(role)
    saved_runs[:] = [run for run in saved_runs if not (
        ("working_repository" if _recorded_module((run.get("context") or {}).get("application") or run.get("source"), run.get("session_id") or (run.get("context") or {}).get("session_id")) != "project" else "agent_system") == role
        and (run.get("session_id") or (run.get("context") or {}).get("session_id")) in deleted)]

# Global singleton graph state
graph_state = GraphState()

session_inventories: Dict[str, dict] = {}
session_states: Dict[str, GraphState] = {}
run_states: Dict[str, GraphState] = {}


def route_event(event: dict) -> tuple:
    """Independent state machines let cancelled, new and parallel runs interleave safely."""
    global graph_state
    if not isinstance(event, dict): raise ValueError("Event must be an object")
    kind = str(event.get("type", event.get("event_type", ""))).upper()
    if kind == "RESET":
        for state in list(run_states.values()):
            if state.current_run_id: state._persist_run("interrupted")
        run_states.clear()
        session_states.clear()
        session_inventories.clear()
        graph_state = GraphState()
        return graph_state.get_snapshot(), True

    if kind == "SESSION_OBSERVATION":
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
            raise ValueError("Observation requires an explicit session_id")
        from .session_observation import clean_observation
        event = {**event, "observation": clean_observation(event.get("observation"))}
        state = session_states.get(session_id)
        if state is None:
            state = GraphState(catalog=load_resource_catalog(application=event.get("application")))
            state.session_id = session_id
            session_states[session_id] = state
        snapshot = state.process_event(event)
        return snapshot, state is graph_state

    if kind == "RUNTIME_INVENTORY":
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("Runtime inventory requires an explicit session_id")
        from .capabilities import validate_inventory_schema
        inventory = validate_inventory_schema(event.get("inventory"))
        event = {key: value for key, value in event.items() if key in {"type", "event_type", "session_id", "run_id", "cwd", "source", "timestamp", "application", "agent_id"}}
        event["inventory"] = inventory
        session_inventories[session_id] = inventory
        state = session_states.get(session_id)
        if state is None:
            state = GraphState(catalog=load_resource_catalog(application=event.get("application")))
            state.session_id = session_id
            session_states[session_id] = state
        matching_states = [s for s in run_states.values() if s.session_id == session_id]
        for matching in matching_states:
            matching.set_runtime_inventory(session_id, inventory)
        snapshot = state.process_event(event)
        displayed = graph_state is state or not graph_state.current_run_id
        if displayed:
            graph_state = state
        return snapshot, displayed

    run_id = str(event.get("run_id") or "")
    if not run_id: raise ValueError("run_id is required")
    session_id = str(event.get("session_id") or "")

    starts = {"PROMPT_START", "GENERATION_START", "PROMPT_RECEIVED"}
    if kind in starts:
        if run_id in run_states: raise ValueError("Run already started")
        if len(run_states) >= 8: raise ValueError("Too many active runs; reset interrupted routes first")
        state = GraphState()
        state.session_id = session_id
        prior = session_states.get(session_id)
        if prior:
            state.session_observation = copy.deepcopy(prior.session_observation)
        if session_id and session_id in session_inventories:
            state.set_runtime_inventory(session_id, session_inventories[session_id])
        run_states[run_id] = state
        try: snapshot = state.process_event(event)
        except Exception:
            run_states.pop(run_id, None); raise
        graph_state = state
        if session_id:
            session_states[session_id] = state
    else:
        state = run_states.get(run_id)
        if state is None:
            raise ValueError("Run has not started or is already finished")
        if session_id and session_id != state.session_id:
            raise ValueError("Event belongs to another session")
        snapshot = state.process_event(event)
    displayed = (state is graph_state)
    if not state.current_run_id: run_states.pop(run_id, None)
    return snapshot, displayed

def discover_all_sessions() -> tuple[list[dict], Optional[str]]:
    """Both external sources share a folder; discovery never establishes usage."""
    selected = SETTINGS.get("connection", {}).get("module", "project")
    sessions_by_id = {}
    if selected in {"codex", "omp"}:
        from .repository_observer import RepositoryObserver
        discovered = CONNECTIONS.observer.sessions() if CONNECTIONS.observer else RepositoryObserver.discover_sessions(PROJECT_ROOT)
        sessions_by_id.update((row["session_id"], dict(row)) for row in discovered)

    def allowed(application, session_id):
        module = _recorded_module(application, session_id)
        return module in {"codex", "omp"} if selected in {"codex", "omp"} else module == "project"

    for session_id, state in list(session_states.items()):
        application = state.run_context.get("application") or SETTINGS["agent_label"]
        if not allowed(application, session_id):
            continue
        phase = state.orchestrator_phase
        sessions_by_id[session_id] = {
            **sessions_by_id.get(session_id, {}),
            "session_id": session_id, "session_name": state.run_context.get("session_name") or sessions_by_id.get(session_id, {}).get("session_name") or session_id,
            "application": application,
            "status": "running" if state.current_run_id else {"complete": "completed", "error": "failed", "waiting": "waiting"}.get(phase, "idle"),
            "run_id": state.run_id, "project_path": str(PROJECT_ROOT), "project_root": str(PROJECT_ROOT),
            "updated_at": state.history[-1]["timestamp"] if state.history else state.run_started_at,
        }
    for run in list(saved_runs):
        context = run.get("context") or {}
        session_id = run.get("session_id") or context.get("session_id")
        application = context.get("application") or run.get("source") or SETTINGS["agent_label"]
        if not session_id or session_id in sessions_by_id or not allowed(application, session_id):
            continue
        sessions_by_id[session_id] = {
            "session_id": session_id, "session_name": context.get("session_name") or session_id,
            "application": application, "status": "unknown" if run.get("status") in {"running", "interrupted"} else run.get("status", "idle"),
            "run_id": run.get("run_id"), "project_path": str(PROJECT_ROOT), "project_root": str(PROJECT_ROOT),
            "updated_at": run.get("finished_at") or run.get("started_at"),
        }
    rows = list(sessions_by_id.values())
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    rows.sort(key=lambda row: 0 if row["status"] == "running" else 2 if row["status"] == "unknown" else 1)
    from .session_names import apply_session_names
    role = "working_repository" if selected in {"codex", "omp"} else "agent_system"
    rows = apply_session_names(rows, SETTINGS, PROJECT_ROOT, role)
    return rows, rows[0]["session_id"] if rows else None



class ConnectionManager:
    """WebSockets broadcast manager."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept(subprotocol="cad-xray")
        self.active_connections.append(websocket)
        sessions, default_id = await asyncio.to_thread(discover_all_sessions)
        # Send initial full state
        await websocket.send_json({
            "type": "INIT",
            "state": graph_state.get_snapshot(),
            "config": graph_state.config,
            "history": graph_state.history,
            "runs": saved_runs,
            "sessions": sessions,
            "default_session_id": default_id
        })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        async def send(connection):
            try:
                await asyncio.wait_for(connection.send_json(message), timeout=0.5)
            except Exception:
                self.disconnect(connection)
                try: await connection.close(code=1013)
                except Exception: pass
        await asyncio.gather(*(send(connection) for connection in list(self.active_connections)))


ws_manager = ConnectionManager()


@app.get("/health")
async def get_health():
    from .installation import DASHBOARD_PROTOCOL
    return JSONResponse({"status": "ok", "service": "agent-observer", "version": VERSION,
                         "dashboard_protocol": DASHBOARD_PROTOCOL,
                         "server_revision": SERVER_REVISION})

@app.get("/api/connections")
async def get_connections():
    return JSONResponse(await asyncio.to_thread(CONNECTIONS.summary))


@app.get("/api/connections/status")
async def get_connection_status():
    return JSONResponse(await asyncio.to_thread(CONNECTIONS.status))


@app.post("/api/connections/{action}")
async def connection_action(action: str, request: Request):
    methods = {"inspect": CONNECTIONS.inspect, "workspaces": CONNECTIONS.remember,
               "connect": CONNECTIONS.connect, "disconnect": CONNECTIONS.disconnect,
               "reveal": CONNECTIONS.reveal, "replay": CONNECTIONS.replay,
               "analyze": CONNECTIONS.analyze, "generate": CONNECTIONS.generate}
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Choose a project folder.")
        if action == "choose-folder":
            if body:
                raise ValueError("Folder selection does not accept commands.")
            return JSONResponse(await asyncio.to_thread(INSTALLATIONS.choose_folder))
        if action not in methods:
            return JSONResponse({"detail": "Connection action not found"}, status_code=404)
        previous = dict(SETTINGS.get("connection", {}))
        result = await asyncio.to_thread(methods[action], body)
        if action in {"connect", "generate", "disconnect"} and result.get("workspace", {}).get("current"):
            current = SETTINGS.get("connection", {})
            changed = previous != current and not (
                previous.get("module") in {"codex", "omp"} and current.get("module") in {"codex", "omp"}
                and previous.get("enabled", True) == current.get("enabled", True))
            if changed:
                session_inventories.clear()
                _codex_receipts.clear()
                route_event({"type": "RESET"})
            catalog = await refresh_resource_catalog()
            snapshot = graph_state.get_snapshot()
            history = list(graph_state.history)
            await ws_manager.broadcast({"type": "RESET" if changed else "STATE_UPDATE", "module_changed": changed,
                                        "config": catalog, "state": snapshot, "history": history})
            result.update(config=catalog, state=snapshot, history=history)
        return JSONResponse(result)
    except InstallationError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError) as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    except (OSError, subprocess.SubprocessError):
        return JSONResponse({"detail": "Connection could not finish. Check folder access and refresh."}, status_code=503)



@app.get("/api/installations")
async def get_installations():
    return JSONResponse(await asyncio.to_thread(INSTALLATIONS.list))


async def installation_action(action, request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Expected an installation settings object.")
        method = getattr(INSTALLATIONS, action)
        if action == "choose_folder":
            if body:
                raise ValueError("Folder selection does not accept commands or paths.")
            result = await asyncio.to_thread(method)
        elif action == "diagnose":
            if set(body) - {"project", "agent", "port", "executable", "dest"} or not isinstance(body.get("project"), str):
                raise ValueError("Choose the project to diagnose.")
            result = await asyncio.to_thread(method, body)
        else:
            result = await asyncio.to_thread(method, body)
        return JSONResponse(result)
    except InstallationError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Invalid installation settings. Choose a project folder and analyze it again."}, status_code=400)
    except (OSError, subprocess.SubprocessError):
        return JSONResponse({"detail": "The installation action could not finish. Review project permissions and try again."}, status_code=503)


@app.post("/api/installations/analyze")
async def analyze_installation(request: Request):
    return await installation_action("analyze", request)


@app.post("/api/installations/install")
async def install_project(request: Request):
    return await installation_action("install", request)


@app.post("/api/installations/diagnose")
async def diagnose_installation(request: Request):
    return await installation_action("diagnose", request)


@app.post("/api/installations/choose-folder")
async def choose_installation_folder(request: Request):
    return await installation_action("choose_folder", request)


@app.post("/api/installations/start")
async def start_installed_graph(request: Request):
    return await installation_action("start_graph", request)


@app.post("/api/installations/disconnect")
async def disconnect_installation(request: Request):
    return await installation_action("disconnect", request)


@app.get("/api/agent-events")
async def get_agent_events(application: str = "Codex", session_id: str = "", after: int = 0, limit: int = 200):
    try:
        return JSONResponse(await asyncio.to_thread(AGENT_EVENTS.read, application=application, session_id=session_id, after=after, limit=limit))
    except ValueError as error:
        return JSONResponse({"detail": str(error)}, status_code=400)


# ── REST Endpoints ────────────────────────────────────────────────────────────

async def refresh_resource_catalog():
    catalog = await asyncio.to_thread(load_resource_catalog, application=graph_state.run_context.get("application") or graph_state.catalog.get("agent_label"))
    graph_state.update_catalog(catalog)
    await asyncio.to_thread(LOCAL_SERVICES.update_settings, SETTINGS)
    return catalog


@app.get("/assets/{name}")
async def get_dashboard_asset(name: str):
    media = {
        "documents-graph.js": "text/javascript",
        "documents-graph.css": "text/css",
        "block-settings.js": "text/javascript",
        "vault-settings.js": "text/javascript",
        "block-settings.css": "text/css",
        "resource-layouts.js": "text/javascript",
        "resource-layouts.css": "text/css",
        "operation-evidence.js": "text/javascript",
        "activity-view.js": "text/javascript",
        "activity-view.css": "text/css",
        "documents-files.js": "text/javascript",
        "documents-files.css": "text/css",
        "memory-systems.js": "text/javascript",
        "memory-systems.css": "text/css",
        "route-playback.js": "text/javascript",
        "result-view.js": "text/javascript",
        "result-view.css": "text/css",
        "tool-insights.js": "text/javascript",
        "tool-insights.css": "text/css",
        "graph-settings.js": "text/javascript",
        "graph-settings.css": "text/css",
        "service-terminal.js": "text/javascript",
        "service-terminal.css": "text/css",
        "installation.js": "text/javascript",
        "installation.css": "text/css",
        "session-navigation.css": "text/css",
        "i18n.js": "text/javascript",
        "i18n.ru.js": "text/javascript",
        "i18n.css": "text/css",
        "markdown-it.js": "text/javascript",
        "message-markdown.js": "text/javascript",
        "message-markdown.css": "text/css",
        "result-report.js": "text/javascript",
        "block-packing.js": "text/javascript",
        "agent-observation.js": "text/javascript",
        "agent-observation.css": "text/css",
    }
    if name not in media:
        return JSONResponse({"detail": "Asset not found"}, status_code=404)
    return FileResponse(REPO_ROOT / "web" / name, media_type=media[name])

@app.get("/api/results/{run_id}")
async def get_result_manifest(run_id: str):
    try:
        return JSONResponse(await asyncio.to_thread(RESULTS.load, run_id), headers={"Cache-Control": "no-store"})
    except ResultError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)

@app.get("/api/results/{run_id}/files/{file_id}")
async def get_result_file(run_id: str, file_id: str, original: bool = False):
    try:
        record, data = await asyncio.to_thread(RESULTS.artifact, run_id, file_id, original)
        suffix = Path(record["name"]).suffix.lower()
        media = IMAGES.get(suffix, "application/octet-stream" if record["kind"] in {"model", "file"} else "text/plain; charset=utf-8")
        from urllib.parse import quote
        disposition = "inline" if media.startswith(("image/", "text/")) else "attachment"
        return Response(data, media_type=media, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(record['name'])}"})
    except ResultError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)

@app.post("/api/results/{run_id}/attachments")
async def post_result_attachments(run_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict) or "files" not in body or not isinstance(body["files"], list):
        return JSONResponse({"detail": "Request body must contain 'files' list"}, status_code=400)
    try:
        summary = await asyncio.to_thread(RESULTS.attach, run_id, body["files"])
    except ResultError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    attachment_records = [f for f in summary.get("files", []) if f.get("attachment")]
    observation_files = [
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "path": f.get("path"),
            "kind": f.get("kind"),
            "change": f.get("change"),
            "added": f.get("added"),
            "removed": f.get("removed"),
            "artifact_url": f.get("artifact_url"),
            "preview": f.get("preview"),
        }
        for f in attachment_records
    ]
    observation_event = {
        "type": "RESULT_ATTACHMENTS",
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "files": observation_files,
    }

    description = f"Result attachments uploaded ({len(observation_files)} file{'s' if len(observation_files) != 1 else ''})"

    saved_run = next((r for r in saved_runs if r.get("run_id") == run_id), None)
    if saved_run:
        observation_event["session_id"] = saved_run.get("session_id", "")
        for step in saved_run.get("steps", []):
            nodes = step.get("nodes", {})
            if "run_result" in nodes:
                nodes["run_result"]["result"] = summary
        steps = saved_run.get("steps", [])
        if steps:
            last_step = steps[-1]
            obs_step = copy.deepcopy(last_step)
            obs_step["step_index"] = len(steps)
            obs_step["timestamp"] = datetime.now(timezone.utc).isoformat()
            obs_step["description"] = description
            obs_step["event"] = observation_event
            if "nodes" in obs_step and "run_result" in obs_step["nodes"]:
                obs_step["nodes"]["run_result"]["result"] = summary
            steps.append(obs_step)
            del steps[:-MAX_STEPS]
        _enqueue_history(list(saved_runs))

    if graph_state.run_id == run_id or not graph_state.run_id:
        if "run_result" in graph_state.nodes:
            graph_state.nodes["run_result"]["result"] = summary
            graph_state.nodes["run_result"]["status"] = "hit"
        for step in graph_state.history:
            if "run_result" in step.get("nodes", {}):
                step["nodes"]["run_result"]["result"] = summary
                step["nodes"]["run_result"]["status"] = "hit"
        if graph_state.history:
            last_step = graph_state.history[-1]
            obs_step = copy.deepcopy(last_step)
            obs_step["step_index"] = len(graph_state.history)
            obs_step["timestamp"] = datetime.now(timezone.utc).isoformat()
            obs_step["description"] = description
            obs_step["event"] = observation_event
            if "nodes" in obs_step and "run_result" in obs_step["nodes"]:
                obs_step["nodes"]["run_result"]["result"] = summary
                obs_step["nodes"]["run_result"]["status"] = "hit"
            graph_state.history.append(obs_step)
            del graph_state.history[:-MAX_STEPS]
        if saved_run:
            saved_run["steps"] = list(graph_state.history[-MAX_STEPS:])
            _enqueue_history(list(saved_runs))

    if run_id in run_states:
        st = run_states[run_id]
        if "run_result" in st.nodes:
            st.nodes["run_result"]["result"] = summary
            st.nodes["run_result"]["status"] = "hit"
        for step in st.history:
            if "run_result" in step.get("nodes", {}):
                step["nodes"]["run_result"]["result"] = summary
                step["nodes"]["run_result"]["status"] = "hit"
        if st.history:
            last_step = st.history[-1]
            obs_step = copy.deepcopy(last_step)
            obs_step["step_index"] = len(st.history)
            obs_step["timestamp"] = datetime.now(timezone.utc).isoformat()
            obs_step["description"] = description
            obs_step["event"] = observation_event
            if "nodes" in obs_step and "run_result" in obs_step["nodes"]:
                obs_step["nodes"]["run_result"]["result"] = summary
                obs_step["nodes"]["run_result"]["status"] = "hit"
            st.history.append(obs_step)
            del st.history[:-MAX_STEPS]
    await ws_manager.broadcast({
        "type": "STATE_UPDATE",
        "event": observation_event,
        "state": graph_state.get_snapshot(),
        "history_step": graph_state.history[-1] if (graph_state.run_id == run_id and graph_state.history) else None,
        "runs": saved_runs,
    })

    return JSONResponse({"status": "ok", "result": summary})

@app.get("/")
@app.get("/dashboard")
async def get_dashboard(demo: bool = False, session: Optional[str] = None, project: Optional[str] = None):
    """Serves the interactive X-Ray Graph visualizer HTML page."""
    if DASHBOARD_HTML_FILE.exists():
        if not demo:
            await refresh_resource_catalog()
        content = DASHBOARD_HTML_FILE.read_text(encoding="utf-8")
        bootstrap = {
            "config": graph_state.config,
            "runs": saved_runs[:1],
            "state": graph_state.get_snapshot(),
            "token": AUTH_TOKEN,
            "history": list(graph_state.history)
        }
        if demo:
            bootstrap = demo_bootstrap()
        elif session and (not project or project == str(PROJECT_ROOT)):
            detail = await session_detail_payload(session)
            if detail is None:
                return HTMLResponse("<h2>Session not found for this project.</h2>", status_code=404)
            bootstrap.update(config=detail["config"], state=detail["state"], history=detail["history"])
            if detail.get("run"):
                bootstrap["runs"] = [detail["run"]]
        nonce = secrets.token_urlsafe(24)
        content = re.sub(r"<script(?=[\s>])", lambda match: f'<script nonce="{nonce}"', content)
        serialized = json.dumps(bootstrap, ensure_ascii=False).replace("<", "\\u003c")
        inject = f'<script nonce="{nonce}">window.__BOOTSTRAP__ = {serialized};</script>'
        content = content.replace("</head>", f"{inject}\n</head>")
        return HTMLResponse(content=content, headers={"Content-Security-Policy": f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"})
    return HTMLResponse(
        content="<h2>Dashboard asset not found. Expected src/agent_observer/web/dashboard.html.</h2>",
        status_code=404
    )


@app.get("/api/config")
async def get_config():
    """Returns the current Agent resource catalog."""
    return JSONResponse(content=await refresh_resource_catalog())


@app.get("/api/state")
async def get_state():
    """Returns current graph snapshot."""
    return JSONResponse(content=graph_state.get_snapshot())

@app.api_route("/api/project-settings", methods=["GET", "POST"])
async def project_settings(request: Request):
    try:
        body = await request.json() if request.method == "POST" else None
        if body is not None and not isinstance(body, dict):
            raise InstallationError(400, "Project settings require an object.")
        result = await asyncio.to_thread(PROJECT_SETTINGS.request,
            request.query_params.get("project"), request.query_params.get("endpoint"), request.method, body)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except (InstallationError, ResultError, LocalServiceError, DocumentError) as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError, OSError):
        return JSONResponse({"detail": "The selected project’s settings could not be read."}, status_code=400)



@app.get("/api/project-hosts")
async def get_project_hosts():
    return JSONResponse(await asyncio.to_thread(PROJECT_SETTINGS.hosts), headers={"Cache-Control": "no-store"})


@app.get("/api/graph-settings")
async def get_graph_settings():
    result = await asyncio.to_thread(GRAPH_SETTINGS.inventory, graph_state.config, graph_state.get_snapshot())
    result["local_services"] = (await asyncio.to_thread(LOCAL_SERVICES.snapshot))["services"]
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@app.post("/api/graph-settings")
async def discover_graph_settings():
    await asyncio.to_thread(GRAPH_SETTINGS.scan)
    await post_capabilities_discover()
    result = await asyncio.to_thread(GRAPH_SETTINGS.inventory, graph_state.config, graph_state.get_snapshot())
    result["config"] = graph_state.config
    result["local_services"] = (await asyncio.to_thread(LOCAL_SERVICES.snapshot))["services"]
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@app.post("/api/graph-settings/reveal")
async def reveal_graph_settings(request: Request):
    try:
        body = await request.json()
        return JSONResponse(await asyncio.to_thread(GRAPH_SETTINGS.reveal, body.get("id")))
    except ResultError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"detail": "Choose a discovered entry."}, status_code=400)
    except (OSError, subprocess.SubprocessError):
        return JSONResponse({"detail": "The file manager could not open this folder."}, status_code=503)


@app.get("/api/local-services")
async def get_local_services():
    fresh = await asyncio.to_thread(load_settings, SETTINGS["config_file"], project_root=PROJECT_ROOT)
    return JSONResponse(await asyncio.to_thread(LOCAL_SERVICES.snapshot, fresh), headers={"Cache-Control": "no-store"})


async def refresh_local_service_settings():
    fresh = await asyncio.to_thread(load_settings, SETTINGS["config_file"], project_root=PROJECT_ROOT)
    await asyncio.to_thread(LOCAL_SERVICES.update_settings, fresh)


@app.post("/api/local-services/{service_id}/start")
async def start_local_service(service_id: str, request: Request):
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict) or body not in ({}, {"mode": "independent"}):
            return JSONResponse({"detail": "Start accepts a registered service ID and optional independent mode, not command arguments."}, status_code=400)
        await refresh_local_service_settings()
        service = await asyncio.to_thread(LOCAL_SERVICES.start, service_id, independent=body.get("mode") == "independent")
        return JSONResponse({"service": service})
    except LocalServiceError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Invalid local service configuration or request."}, status_code=400)


@app.post("/api/local-services/{service_id}/stop")
async def stop_local_service(service_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"identity"} or not isinstance(body["identity"], str):
            return JSONResponse({"detail": "Refresh the service and supply its current identity."}, status_code=400)
        service = await asyncio.to_thread(LOCAL_SERVICES.stop, service_id, body["identity"])
        return JSONResponse({"service": service})
    except LocalServiceError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Invalid service stop request."}, status_code=400)


@app.post("/api/local-processes/{process_id}/stop")
async def stop_local_process(process_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"identity"} or not isinstance(body["identity"], str):
            return JSONResponse({"detail": "Refresh the process and supply its current identity."}, status_code=400)
        result = await asyncio.to_thread(LOCAL_SERVICES.stop_process, process_id, body["identity"])
        return JSONResponse({"process": result})
    except LocalServiceError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Invalid project process stop request."}, status_code=400)


@app.get("/api/local-services/{service_id}/logs")
async def get_local_service_logs(service_id: str, after: int = 0):
    try:
        return JSONResponse(await asyncio.to_thread(LOCAL_SERVICES.logs, service_id, after), headers={"Cache-Control": "no-store"})
    except LocalServiceError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)


@app.get("/api/history")
async def get_history():
    """Returns full event timeline history for replay scrubber."""
    return JSONResponse(content={"history": graph_state.history})


@app.get("/api/runs")
async def get_runs():
    """Returns completed or interrupted local runs for replay."""
    return JSONResponse(content={"runs": saved_runs})

@app.get("/api/sessions")
async def get_sessions():
    """Returns discovered and observed sessions metadata and default active session."""
    sessions, default_id = await asyncio.to_thread(discover_all_sessions)
    return JSONResponse({"sessions": sessions, "default_session_id": default_id})

@app.get("/api/session-projects")
async def get_session_projects():
    """Project-scoped sidebar metadata; never starts or connects another graph."""
    from .session_projects import discover_session_projects
    def inventory():
        sessions, _ = discover_all_sessions()
        return discover_session_projects(CONNECTIONS, sessions)
    return JSONResponse({"projects": await asyncio.to_thread(inventory)})

@app.post("/api/session-name")
async def rename_project_session(request: Request):
    from .session_names import rename_session
    from .installation import InstallationError
    try:
        body = await request.json()
        def rename():
            sessions, _ = discover_all_sessions()
            return rename_session(body, CONNECTIONS, sessions)
        return JSONResponse(await asyncio.to_thread(rename))
    except InstallationError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Expected a valid session name."}, status_code=400)

@app.post("/api/session-open")
async def open_project_session_endpoint(request: Request):
    from .session_navigation import open_project_session
    try:
        body = await request.json()
        def open_session():
            current = discover_all_sessions()[0] if isinstance(body, dict) and body.get("project") == str(PROJECT_ROOT) else []
            return open_project_session(body, CONNECTIONS, current)
        return JSONResponse(await asyncio.to_thread(open_session))
    except InstallationError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Expected a valid project session."}, status_code=400)

@app.post("/api/session-delete")
async def delete_project_session_endpoint(request: Request):
    global graph_state
    from .session_deletion import delete_session, purge_in_memory_session
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise InstallationError(400, "Expected a session deletion object.")
        def remove():
            root, target, workspace, settings = PROJECT_SETTINGS.resolve(body.get("project"))
            if not workspace["current"]:
                port = workspace.get("port")
                if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
                    status = CONNECTIONS.installations._inspect_connection(root, port, target)
                    if status.get("connected"):
                        if not status.get("dashboard_compatible"):
                            from .installation import DASHBOARD_RESTART_REQUIRED
                            raise InstallationError(409, DASHBOARD_RESTART_REQUIRED)
                        return PROJECT_SETTINGS.forward(port, settings, "/api/session-delete", "POST", body)
            current = discover_all_sessions()[0] if workspace["current"] else []
            return delete_session(body, CONNECTIONS, current)
        result = await asyncio.to_thread(remove)
        if result["project"] == str(PROJECT_ROOT) and result["repository_mode"] == active_repository_role():
            deleted_session_ids(result["repository_mode"]).add(result["session_id"])
            purge_in_memory_session(result["session_id"], session_states, saved_runs, run_states, session_inventories, repository_mode=result["repository_mode"])
            if graph_state.session_id == result["session_id"]:
                graph_state = next(iter(session_states.values()), None)
                if graph_state is None:
                    graph_state = GraphState()
        return JSONResponse(result)
    except InstallationError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Expected a valid session deletion."}, status_code=400)


async def session_detail_payload(session_id):
    from .session_navigation import get_or_hydrate_session_detail
    if session_id in deleted_session_ids(active_repository_role()):
        return None
    existing = session_states.get(session_id)
    source = existing.run_context.get("application") if existing else None
    if existing is None:
        sessions, _ = await asyncio.to_thread(discover_all_sessions)
        metadata = next((row for row in sessions if row["session_id"] == session_id), None)
        if metadata is None:
            return None
        source = metadata.get("application")
    catalog = existing.catalog if existing else await asyncio.to_thread(load_resource_catalog, application=source)
    factory = lambda: GraphState(persist=False, catalog=catalog)
    detail = await asyncio.to_thread(get_or_hydrate_session_detail, session_id, PROJECT_ROOT,
        session_states, saved_runs, settings=SETTINGS, state_factory=factory,
        session_inventories=session_inventories, cache_context=catalog)
    if detail is not None:
        return detail
    state = factory()
    state.session_id = session_id
    snapshot = state.get_snapshot()
    snapshot["context"]["session_id"] = session_id
    return {"state":snapshot, "history":[], "config":state.config}


@app.get("/api/bootstrap")
async def get_project_bootstrap(session: Optional[str] = None):
    """Selected project state for in-page navigation; never returns credentials."""
    detail = await session_detail_payload(session) if session else None
    if session and detail is None:
        return JSONResponse({"detail": "Session not found for this project."}, status_code=404)
    sessions, default_id = await asyncio.to_thread(discover_all_sessions)
    return JSONResponse({
        "config": detail["config"] if detail else graph_state.config,
        "state": detail["state"] if detail else graph_state.get_snapshot(),
        "history": detail["history"] if detail else list(graph_state.history),
        "runs": [detail["run"]] if detail and detail.get("run") else saved_runs[:1],
        "sessions": sessions, "default_session_id": default_id,
    })


@app.post("/api/project-request")
async def request_project_graph(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise InstallationError(400, "Expected a project request.")
        status, media, data = await asyncio.to_thread(PROJECT_TRANSPORT.request,
            body.get("project"), body.get("path"), body.get("method", "GET"), body.get("body"))
        return Response(data, status_code=status, headers={"Content-Type": media})
    except InstallationError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Expected a valid project request."}, status_code=400)


@app.get("/api/project-file")
async def get_project_graph_file(project: str, path: str):
    try:
        PROJECT_TRANSPORT.endpoint(path)
        if not path.startswith("/api/results/"):
            raise InstallationError(400, "Only result artifacts can be downloaded through this endpoint.")
        status, media, data = await asyncio.to_thread(PROJECT_TRANSPORT.request, project, path)
        return Response(data, status_code=status, headers={"Content-Type": media})
    except InstallationError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)


@app.get("/api/sessions/{session_id:path}")
async def get_session_detail(session_id: str):
    """Return the selected session, observing historical sources without affecting live runs."""
    detail = await session_detail_payload(session_id)
    if detail is None:
        return JSONResponse({"detail": "Session not found for this repository and agent."}, status_code=404)
    return JSONResponse({key:detail[key] for key in ("state", "history", "config")})


@app.get("/api/document")
async def get_document(path: str):
    try:
        return JSONResponse(await asyncio.to_thread(document_payload, SETTINGS, graph_state.catalog, path))
    except DocumentError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)


@app.post("/api/document/preview")
async def preview_document(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict): raise DocumentError(400, "Expected a document object.")
        path = body.get("path", "")
        content = body.get("content", "")
        if not isinstance(content, str): raise DocumentError(400, "Content must be a string.")
        target, root, doc_format, _, _ = get_document_context(SETTINGS, graph_state.catalog, path)
        if doc_format == "markdown":
            doc_rel = path[len("@vault/"):] if path.startswith("@vault/") else path
            rendered = await asyncio.to_thread(render_markdown, content, doc_rel, root)
        else:
            import html
            rendered = '<pre class="observer-text-preview">' + html.escape(content) + '</pre>'
        return JSONResponse({"html": rendered})
    except DocumentError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Expected valid document JSON."}, status_code=400)


@app.post("/api/document")
async def write_document(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict): raise DocumentError(400, "Expected a document object.")
        result = await asyncio.to_thread(save_document, SETTINGS, graph_state.catalog, body.get("path", ""), body.get("content"), body.get("revision"))
        return JSONResponse(result)
    except DocumentError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Expected valid document JSON."}, status_code=400)


@app.post("/api/document/move")
async def move_document_endpoint(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict): raise DocumentError(400, "Expected a document move object.")
        path = body.get("path", "")
        destination = body.get("destination", "")
        revision = body.get("revision", "")
        result = await asyncio.to_thread(move_document, SETTINGS, graph_state.catalog, path, destination, revision)
        await refresh_resource_catalog()
        return JSONResponse(result)
    except DocumentError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except ValueError as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    except (TypeError, json.JSONDecodeError):
        return JSONResponse({"detail": "Expected valid document move JSON."}, status_code=400)


@app.get("/api/block-settings")
async def get_block_settings():
    result = await asyncio.to_thread(load_current_block_settings, SETTINGS)
    public_result = dict(result)
    public_result["memory_auth_configured"] = {system["id"]: bool(system.get("token_file")) for system in result.get("memory_systems", [])}
    if "memory_systems" in public_result:
        public_result["memory_systems"] = [public_system(s) for s in public_result["memory_systems"]]
    return JSONResponse(public_result)


@app.get("/api/memory-systems")
async def get_memory_systems(runtime: str = ""):
    if runtime not in {"", "omp", "codex"}:
        return JSONResponse({"detail": "Unknown memory runtime."}, status_code=400)
    current = await asyncio.to_thread(load_current_block_settings, SETTINGS)
    systems = list(current["memory_systems"])
    if runtime == "omp":
        from .omp_inventory import configured_inventory
        ids = {system["id"] for system in systems}
        systems.extend(system for system in configured_inventory(PROJECT_ROOT)["memory_systems"] if system["id"] not in ids)
    return JSONResponse(await asyncio.to_thread(inspect_memory_systems, {**SETTINGS, "memory_systems": systems}))


@app.post("/api/block-settings")
async def post_block_settings(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"detail": "Expected a JSON object."}, status_code=400)
        saved = await asyncio.to_thread(save_block_settings, SETTINGS, body)
        public_saved = dict(saved)
        public_saved["memory_auth_configured"] = {system["id"]: bool(system.get("token_file")) for system in saved.get("memory_systems", [])}
        if "memory_systems" in public_saved:
            public_saved["memory_systems"] = [public_system(s) for s in public_saved["memory_systems"]]
        visibility_keys = set(body) - {"revision"}
        if visibility_keys and visibility_keys <= {"hidden_blocks", "hidden_resources", "hidden_commands"}:
            preferences = {key:saved[key] for key in visibility_keys}
            for state in {graph_state, *run_states.values()}:
                state.config = {**state.config, **preferences}
                state.catalog = {**state.catalog, **preferences}
            return JSONResponse({**public_saved, "catalog": graph_state.config})
        if set(body) == {"revision", "capability_links"}:
            # Link edits update project metadata without rediscovering or resetting capability state.
            for state in {graph_state, *run_states.values()}:
                state.catalog = {**state.catalog, "capability_links": saved["capability_links"]}
                state.config = {**state.config, "capability_links": saved["capability_links"]}
            return JSONResponse({**public_saved, "catalog": graph_state.config})
        catalog = await refresh_resource_catalog()
        return JSONResponse({**public_saved, "catalog": catalog})
    except DocumentError as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
    except ValueError as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    except (TypeError, json.JSONDecodeError):
        return JSONResponse({"detail": "Expected valid JSON."}, status_code=400)


@app.get("/api/tools/discover")
async def get_tools_discover():
    result = await asyncio.to_thread(discover_tools, SETTINGS)
    return JSONResponse(result)

@app.get("/api/capabilities")
async def get_capabilities():
    config_file = SETTINGS.get("config_file")
    if config_file and Path(config_file).is_file():
        try:
            await asyncio.to_thread(refresh_settings, SETTINGS, config_file)
        except Exception:
            pass
    config_dir = Path(SETTINGS["config_file"]).parent
    from .capabilities import load_capabilities_snapshot, discover_capabilities, merge_capabilities, normalize_manual_capabilities, _discovery_source
    discovered = load_capabilities_snapshot(config_dir)
    if not discovered or discovered.get("source") != _discovery_source(SETTINGS.get("agent_runtime")):
        discovered = await asyncio.to_thread(discover_capabilities, PROJECT_ROOT, Path(SETTINGS["config_file"]), agent=SETTINGS.get("agent_runtime"))
    manual_cap = normalize_manual_capabilities(SETTINGS.get("capabilities", {}))
    combined = merge_capabilities(discovered, manual_cap)
    runtime_inv = graph_state.capability_inventory or {}
    return JSONResponse({
        "inventory": combined,
        "manual": manual_cap,
        "runtime_inventory": runtime_inv,
        "discovery": discovered,
    })


@app.post("/api/capabilities/discover")
async def post_capabilities_discover():
    config_file_path = Path(SETTINGS["config_file"])
    if config_file_path.is_file():
        try:
            await asyncio.to_thread(refresh_settings, SETTINGS, config_file_path)
        except Exception:
            pass
    from .capabilities import discover_capabilities, save_capabilities_snapshot, merge_capabilities, normalize_manual_capabilities
    config_dir = config_file_path.parent
    discovered = await asyncio.to_thread(discover_capabilities, PROJECT_ROOT, config_file_path)
    await asyncio.to_thread(save_capabilities_snapshot, config_dir, discovered)
    manual_cap = normalize_manual_capabilities(SETTINGS.get("capabilities", {}))
    combined = merge_capabilities(discovered, manual_cap)
    catalog = await refresh_resource_catalog()
    snapshot = graph_state.get_snapshot()
    await ws_manager.broadcast({
        "type": "STATE_UPDATE",
        "config": catalog,
        "state": snapshot,
    })
    return JSONResponse({
        "inventory": combined,
        "config": catalog,
        "discovery": discovered,
    })


@app.post("/api/event")
async def post_event(request: Request):
    """Ingests a telemetry event from agents, Python scripts or Swift app."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await ingest_observation(body)


async def ingest_observation(body, *, expected_module=None):
    """One event-loop ingestion path for authenticated clients and passive readers."""
    connection = SETTINGS.get("connection", {})
    selected = connection.get("module", "codex" if SETTINGS.get("agent_runtime") == "codex" else "project")
    application = str(body.get("application", "")).casefold() if isinstance(body, dict) else ""
    external = selected in {"codex", "omp"}
    foreign = (application not in {"codex", "omp"} and external and bool(application)
               or not external and application in {"codex", "omp"}
               and not (application == "omp" and SETTINGS.get("agent_runtime") == "omp")
               or external and not application and isinstance(body, dict) and body.get("source") == "agent")
    disabled = not connection.get("enabled", True) and isinstance(body, dict) and body.get("type") != "RESET"
    if foreign or disabled or expected_module and not external:
        return JSONResponse({"status": "ignored"})
    if application == "omp" and external and isinstance(body, dict):
        raw_session = str(body.get("session_id") or "")
        if raw_session and not raw_session.startswith("omp:"):
            body["session_id"] = "omp:" + raw_session
        if body.get("capture_mode") == "extension" and body.get("type") not in {"RUNTIME_INVENTORY", "SESSION_OBSERVATION"} and CONNECTIONS.observer and hasattr(CONNECTIONS.observer, "has_session") and CONNECTIONS.observer.has_session(body.get("session_id", "")):
            return JSONResponse({"status": "ignored", "transport": "read-only-journal"})
    if isinstance(body, dict):
        session_id = str(body.get("session_id") or "")
        if not session_id:
            owner = run_states.get(str(body.get("run_id") or ""))
            session_id = owner.session_id if owner else ""
        if session_id and session_id in deleted_session_ids(active_repository_role()):
            return JSONResponse({"status": "ignored"})

    previous_catalog = graph_state.config
    if isinstance(body, dict) and str(body.get("type", "")).upper() == "AGENT_EVENT":
        try:
            recorded = await asyncio.to_thread(AGENT_EVENTS.append, body)
        except ValueError as error:
            return JSONResponse({"status": "error", "detail": str(error)}, status_code=422)
        return JSONResponse({"status": "ok", **recorded})
    receipt_id = body.get("event_id") if isinstance(body, dict) and application in {"codex", "omp"} else None
    if receipt_id and receipt_id in _codex_receipts:
        return JSONResponse({"status": "ok", "duplicate": True})
    if isinstance(body, dict) and application in {"codex", "omp"}:
        for key in ("input", "args", "result", "output"):
            if key in body:
                body[key], truncated = sanitize_event_data(body[key])
                if truncated:
                    body["payload_truncated"] = True
    target_state = run_states.get(str(body.get("run_id") or "")) if isinstance(body, dict) else None
    try:
        snapshot, displayed = route_event(body)
    except ResultError as error:
        return JSONResponse(content={"status": "error", "detail": error.detail}, status_code=error.status)
    except (TypeError, ValueError, AttributeError) as exc:
        return JSONResponse(content={"status": "error", "detail": str(exc)}, status_code=422)
    CONNECTIONS.observe(body)
    if receipt_id:
        _codex_receipts[receipt_id] = True
        if len(_codex_receipts) > 10000:
            _codex_receipts.pop(next(iter(_codex_receipts)))
    session_id = str(body.get("session_id") or snapshot.get("context", {}).get("session_id") or "")
    run_id = str(body.get("run_id") or "")
    session_state = target_state or run_states.get(run_id) or session_states.get(session_id)
    if session_state is None and snapshot.get("run_id"):
        session_state = next((s for s in session_states.values() if s.run_id == snapshot["run_id"]), None)
    session_state = session_state or graph_state
    step = session_state.history[-1] if session_state.history and body.get("type") != "SESSION_OBSERVATION" else None

    await ws_manager.broadcast({
        "type": "STATE_UPDATE",
        "session_id": session_id,
        "run_id": run_id,
        "event": step["event"] if (str(body.get("type", body.get("event_type", ""))).upper() == "RUNTIME_INVENTORY" and step) else body,
        "config": session_state.config,
        "state": snapshot,
        "history_step": step,
    })
    return JSONResponse(content={"status": "ok", "state": snapshot})


@app.post("/api/reset")
async def post_reset():
    """Resets the state machine back to idle."""
    route_event({"type": "RESET"})
    snapshot = graph_state.get_snapshot()
    await ws_manager.broadcast({
        "type": "RESET",
        "state": snapshot,
        "history": graph_state.history,
        "runs": saved_runs
    })
    return JSONResponse(content={"status": "reset", "state": snapshot})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time bi-directional WebSocket connection."""
    origin = websocket.headers.get("origin")
    protocols = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    if not _allowed_origin(origin, websocket.headers.get("host", "")) or len(protocols) != 2 or protocols[0] != "cad-xray" or not secrets.compare_digest(protocols[1], AUTH_TOKEN):
        await websocket.close(code=1008); return
    project = websocket.query_params.get("project")
    if project and project != str(PROJECT_ROOT):
        try:
            await PROJECT_TRANSPORT.relay(websocket, project)
        except (InstallationError, OSError, WebSocketDisconnect):
            pass
        finally:
            try:
                await websocket.close(code=1011)
            except RuntimeError:
                pass
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action", "").upper()
                if action == "RESET":
                    route_event({"type": "RESET"})
                    await ws_manager.broadcast({
                        "type": "RESET",
                        "state": graph_state.get_snapshot(),
                        "history": graph_state.history,
                        "runs": saved_runs
                    })
                elif action == "EVENT":
                    await ingest_observation(msg.get("event", {}))
            except Exception as e:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# ── Python Trace Decorator & Helper ───────────────────────────────────────────

def trace_knowledge_node(node_id: str):
    """Decorator to report actual calls from Python tools to the live route graph.

        Usage:
        @trace_knowledge_node("vault_measurements")
        def query_inventory(query: str):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_t = time.perf_counter()
            query_str = str(args) + " " + str(kwargs)

            # Emit TOOL_CALL_START
            emit_xray_event({
                "type": "TOOL_CALL_START",
                "node_id": node_id,
                "tool": func.__name__,
                "reason": "Python function called",
                "query": query_str[:250]
            })

            success = True
            output = None
            rows = None
            try:
                res = func(*args, **kwargs)
                output = res
                if isinstance(res, dict):
                    rows = res.get("count", 1 if res else 0)
                elif isinstance(res, list):
                    rows = len(res)
                return res
            except Exception as ex:
                success = False
                output = str(ex)
                raise ex
            finally:
                duration_ms = (time.perf_counter() - start_t) * 1000.0
                emit_xray_event({
                    "type": "TOOL_CALL_END",
                    "node_id": node_id,
                    "tool": func.__name__,
                    "success": success,
                    "output": str(output)[:400] if output else "",
                    "rows": rows,
                    "duration_ms": duration_ms,
                    "reason": "Python function completed"
                })

        return wrapper
    return decorator


def emit_xray_event(event: dict):
    """Synchronous / fire-and-forget helper to emit an X-Ray event.
    Works whether running standalone or sending to http://127.0.0.1:8766/api/event.
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            os.environ.get("AGENT_XRAY_ENDPOINT", "http://127.0.0.1:8766/api/event"),
            data=json.dumps({**event, "run_id": os.environ.get("CAD_AGENT_RUN_ID", event.get("run_id", ""))}).encode("utf-8"),
            headers=event_headers()
        )
        with urllib.request.urlopen(req, timeout=0.2) as resp:
            pass
    except Exception:
        # Non-blocking if server not yet started
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent Live Schema Visualizer Server")
    parser.add_argument("--port", type=int, default=SETTINGS["port"], help="Local dashboard port")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Local host address (default: 127.0.0.1)")
    args = parser.parse_args()

    print(f"\n╔════════════════════════════════════════════════════════════════════╗")
    print(f"║   Agent Observer — project route graph       ║")
    print(f"║   Dashboard URL:  http://127.0.0.1:{args.port:<5}                            ║")
    print(f"║   WebSocket WS:   ws://127.0.0.1:{args.port:<5}/ws                           ║")
    print(f"╚════════════════════════════════════════════════════════════════════╝\n")

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("X-Ray accepts loopback hosts only")
    fd = os.environ.get("AGENT_OBSERVER_SOCKET_FD")
    uvicorn.run(app, host=args.host, port=args.port, fd=int(fd) if fd else None, log_level="warning")


if __name__ == "__main__":
    main()
