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
from urllib.parse import urlparse
from .xray_auth import load_token, event_headers
from .observer_config import load_settings, apply_environment, resource_catalog, VERSION
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional
from functools import wraps

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

REPO_ROOT = Path(__file__).resolve().parent
DASHBOARD_HTML_FILE = REPO_ROOT / "web/dashboard.html"
SETTINGS = load_settings()
apply_environment(SETTINGS)
ROUTES_HISTORY_FILE = SETTINGS["history_file"]
AUTH_TOKEN = load_token(create=True)
MAX_STEPS = max(200, int(SETTINGS.get("max_steps", 5000)))
MAX_RUNS = max(1, int(SETTINGS.get("max_runs", 50)))
_persist_queue = queue.Queue(maxsize=1)

def _history_writer():
    while True:
        records = _persist_queue.get()
        try:
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
CAD_AGENT_ROOT = PROJECT_ROOT  # Compatibility alias for existing integrations.

app = FastAPI(title="Agent Observer", version=VERSION)

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
        if not secrets.compare_digest(request.headers.get("x-agent-observer-token") or request.headers.get("x-cad-token", ""), AUTH_TOKEN):
            return JSONResponse({"detail": "Local client authentication required"}, status_code=401)
    if request.method == "POST":
        chunks, length = [], 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > 262144:
                return JSONResponse({"detail": "Event too large"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
    return response


def load_resource_catalog() -> dict:
    return resource_catalog(SETTINGS)


class GraphState:
    """Static inventory plus only the route confirmed by incoming events."""

    def __init__(self):
        self.catalog = load_resource_catalog()
        self.reset()

    def reset(self):
        self.config = self.catalog
        self.prompt = ""
        self.project = SETTINGS["project_name"]
        self.mode = ""
        self.project_spec = ""
        self.attachments: List[str] = []
        self.prompt_blocks: List[dict] = []
        self.nodes = {n["id"]: {**n, "status": "idle", "reason": "", "payload": "", "duration_ms": 0.0}
                      for n in self.config.get("nodes", [])}
        self.edges: List[dict] = []
        self.history: List[dict] = []
        self.step_counter = 0
        self.current_run_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.run_started_at: Optional[str] = None
        self.run_source = "agent"
        self.orchestrator_phase = "idle"
        self.orchestrator_action = "Waiting for observed Agent events"
        self.latest_resource_id: Optional[str] = None

    def _persist_run(self, status: str):
        if not self.current_run_id:
            return
        record = {"run_id": self.current_run_id, "started_at": self.run_started_at,
                  "finished_at": datetime.now(timezone.utc).isoformat() if status != "running" else None,
                  "status": status, "source": self.run_source, "project": self.project,
                  "mode": self.mode, "project_spec": self.project_spec, "attachments": list(self.attachments),
                  "prompt": self.prompt, "prompt_blocks": copy.deepcopy(self.prompt_blocks),
                  "steps": list(self.history[-MAX_STEPS:])}
        existing = next((i for i, item in enumerate(saved_runs) if item.get("run_id") == self.current_run_id), None)
        if existing is None:
            saved_runs.insert(0, record)
        else:
            saved_runs[existing] = record
        del saved_runs[MAX_RUNS:]
        _enqueue_history(list(saved_runs))

    def _record_step(self, event: dict, description: str):
        event_copy = copy.deepcopy(event)
        for key, value in tuple(event_copy.items()):
            if isinstance(value, str) and len(value) > 4000:
                event_copy[key] = value[:4000] + "…"
        self.history.append({
            "step_index": self.step_counter, "run_id": self.run_id, "run_started_at": self.run_started_at, "timestamp": datetime.now(timezone.utc).isoformat(),
            "description": description, "event": event_copy, "prompt": self.prompt,
            "prompt_blocks": copy.deepcopy(self.prompt_blocks), "project": self.project,
            "mode": self.mode, "project_spec": self.project_spec, "attachments": list(self.attachments),
            "orchestrator_phase": self.orchestrator_phase, "orchestrator_action": self.orchestrator_action,
            "nodes": copy.deepcopy({key: value for key, value in self.nodes.items() if value.get("status") != "idle"}), "edges": copy.deepcopy(self.edges),
            "run_source": self.run_source,
        })

        self.step_counter += 1
        del self.history[:-MAX_STEPS]

    def _find_document(self, target: str) -> Optional[dict]:
        clean = str(target or "").replace("file://", "").replace("\\", "/").strip()
        if not clean: return None
        docs = [n for n in self.nodes.values() if n.get("kind") == "document"]
        exact = next((n for n in docs if clean == n["id"] or clean == n["label"] or clean.endswith("/" + n["label"])), None)
        if exact: return exact
        matches = [n for n in docs if Path(n["label"]).name == clean] if "/" not in clean else []
        return matches[0] if len(matches) == 1 else None

    def _ensure_tool(self, name: str, node_id: Optional[str] = None) -> dict:
        raw_name = str(name or "tool")
        known = next((item for item in self.nodes.values()
                      if item.get("kind") == "tool" and item.get("label", "").casefold() == raw_name.casefold()), None)
        if known:
            return known
        if node_id in self.nodes and self.nodes[node_id].get("kind") == "document": node_id = None
        stable_id = node_id or ("observed_tool_" + hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:12])
        if stable_id not in self.nodes:
            self.nodes[stable_id] = {"id": stable_id, "label": raw_name, "category": "tool",
                                     "group": "Инструменты, вызванные агентом", "kind": "tool",
                                     "description": "Tool observed in an agent event.",
                                     "status": "idle", "reason": "", "payload": "", "duration_ms": 0.0}
        return self.nodes[stable_id]

    def _document_from_input(self, value: Any) -> Optional[dict]:
        candidates = [str(value or "")]
        try:
            parsed = json.loads(candidates[0])
            def collect(item):
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    for child in item.values(): collect(child)
                elif isinstance(item, list):
                    for child in item: collect(child)
            collect(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        for candidate in candidates:
            normalized = candidate.replace("\\", "/")
            for node in self.nodes.values():
                label = str(node.get("label", ""))
                if node.get("kind") == "document" and (normalized == label or normalized.endswith("/" + label)):
                    return node
        return None

    def _connect_agent_to(self, target_id: str):
        edge = {"from": "agent-hub", "to": target_id, "label": "фактическое обращение"}
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
        if event_type != "RESET":
            if not incoming_id:
                raise ValueError("run_id is required")
            if self.current_run_id and incoming_id != self.current_run_id:
                raise ValueError("Event belongs to another run")
        if event_type == "RESET":
            if self.current_run_id:
                self._persist_run("interrupted")
            self.reset()
            return self.get_snapshot()

        if event_type == "PROMPT_START":
            if self.current_run_id:
                self._persist_run("interrupted")
            self.catalog = load_resource_catalog()
            self.reset()
            self.current_run_id = str(event.get("run_id") or f"run_{int(time.time() * 1000)}")
            self.run_id = self.current_run_id
            self.run_started_at = datetime.now(timezone.utc).isoformat()
            self.run_source = str(event.get("source", "agent"))
            self.prompt = str(event.get("prompt", ""))
            self.project = str(event.get("project", self.project))
            self.mode = str(event.get("mode", ""))
            self.project_spec = str(event.get("project_spec", ""))
            self.attachments = [str(item) for item in event.get("attachments", []) if str(item).strip()]
            self.prompt_blocks = [{"id": "request_prompt", "kind": "prompt", "label": "Промпт проекта",
                                   "text": self.prompt}]
            project_spec = str(event.get("project_spec", "")).strip()
            if project_spec:
                self.prompt_blocks.append({"id": "project_spec", "kind": "spec", "label": "Закреплённое ТЗ проекта",
                                           "text": project_spec})
            comments = event.get("comments", [])
            if isinstance(comments, list):
                for index, comment in enumerate(comments, 1):
                    if isinstance(comment, str):
                        comment = {"text": comment}
                    text = str(comment.get("text", "")).strip()
                    if text:
                        self.prompt_blocks.append({"id": str(comment.get("id") or f"comment_{index}"),
                                                   "kind": "comment", "label": str(comment.get("label") or f"Комментарий {index}"),
                                                   "text": text, "region": str(comment.get("region", ""))})
            self.orchestrator_phase = "running"
            self.orchestrator_action = "Agent started the request"
            sources = [block["id"] for block in self.prompt_blocks]
            for source_id in sources:
                self.edges.append({"from": source_id, "to": "agent-hub", "label": "поступило агенту"})
            self._record_step(event, "Запрос получен Agent; маршрут начат.")
            self._persist_run("running")
            return self.get_snapshot()

        if not self.current_run_id:
            # Ignore detached events instead of inventing a route or a prompt.
            return self.get_snapshot()

        self.orchestrator_phase = "running"
        target = str(event.get("target_id") or event.get("path") or event.get("file_path") or "")
        tool_name = str(event.get("tool") or event.get("tool_name") or "")
        description = ""

        if event_type == "THINKING":
            text = str(event.get("text", ""))[:1200]
            self.orchestrator_action = str(event.get("phase") or "Agent is processing the request")
            if "step completed" in self.orchestrator_action.lower():
                for node in self.nodes.values():
                    if node.get("status") == "active": node["status"] = "observed"
            description = text or self.orchestrator_action
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
            candidate = target or str(event.get("path") or event.get("file_path") or event.get("input") or event.get("query", ""))
            node = self._ensure_tool(tool_name or candidate or "Инструмент", event.get("node_id"))
            document = self._find_document(candidate) or self._document_from_input(event.get("input", ""))
            is_start = event_type == "TOOL_CALL_START" and str(event.get("status", "")).lower() not in {"ok", "success", "pass", "error", "failed"}
            success_raw = event.get("success", str(event.get("status", "ok")).lower() not in {"error", "failed", "failure"})
            success = success_raw if isinstance(success_raw, bool) else str(success_raw).lower() in {"true", "1", "yes", "ok", "success", "pass"}
            node["status"] = "active" if is_start else ("hit" if success else "error")
            node["reason"] = str(event.get("reason", event.get("query", "")))
            node["payload"] = str(event.get("output", event.get("result", event.get("input", event.get("payload_preview", "")))))[:1000]
            node["duration_ms"] = float(event.get("duration_ms", 0) or 0)
            self.latest_resource_id = node["id"]
            self._connect_agent_to(node["id"])
            if document:
                document["status"] = "active" if is_start else ("hit" if success else "error")
                document["reason"] = f"Обращение через инструмент {tool_name or node['label']}"
                document["payload"] = node["payload"]
                doc_edge = {"from": node["id"], "to": document["id"], "label": "файл вызова"}
                if doc_edge not in self.edges: self.edges.append(doc_edge)
                self.latest_resource_id = document["id"]
            description = ("Tool call · " if is_start else "Tool result · ") + (tool_name or node["label"])
            self.orchestrator_action = description
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
                                     "group": "Результат запуска", "kind": "result",
                                     "description": "Final response delivered by the agent.",
                                     "status": "waiting" if waiting else ("hit" if success else "error"), "reason": "",
                                     "payload": str(event.get("result", event.get("payload_preview", "")))[:1200], "duration_ms": 0.0}
            if self.latest_resource_id:
                self.edges.append({"from": self.latest_resource_id, "to": result_id, "label": "результат"})
            else:
                self.edges.append({"from": "request_prompt", "to": result_id, "label": "результат"})
            self.orchestrator_phase = "waiting" if waiting else ("complete" if success else "error")
            self.orchestrator_action = "Waiting for user input" if waiting else ("Result delivered" if success else "Generation failed")
            description = str(event.get("result", event.get("payload_preview", self.orchestrator_action)))[:1200]
        else:
            raise ValueError(f"Неизвестный тип события: {event_type or 'пусто'}")

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
                "run_source": self.run_source, "run_id": self.run_id, "run_started_at": self.run_started_at, "project": self.project,
                "mode": self.mode, "project_spec": self.project_spec, "attachments": list(self.attachments),
                "prompt": self.prompt, "prompt_blocks": copy.deepcopy(self.prompt_blocks),
                "nodes": self.nodes, "edges": copy.deepcopy(self.edges), "sandbox_nodes": {},
                "last_alert": None, "metrics": {**counts, "total_nodes": len(self.nodes)},
                "history_count": len(self.history), "latest_step": self.history[-1] if self.history else None,
                "recent_events": [
                    {"timestamp": item["timestamp"], "description": item["description"], "event": item["event"]}
                    for item in self.history[-60:]
                ]}


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

# Global singleton graph state
graph_state = GraphState()

run_states: Dict[str, GraphState] = {}

def route_event(event: dict) -> tuple:
    """Independent state machines let cancelled, new and parallel runs interleave safely."""
    global graph_state
    if not isinstance(event, dict): raise ValueError("Event must be an object")
    kind = str(event.get("type", event.get("event_type", ""))).upper()
    if kind == "RESET":
        for state in run_states.values():
            if state.current_run_id: state._persist_run("interrupted")
        run_states.clear(); graph_state = GraphState()
        return graph_state.get_snapshot(), True
    run_id = str(event.get("run_id") or "")
    if not run_id: raise ValueError("run_id is required")
    starts = {"PROMPT_START", "GENERATION_START", "PROMPT_RECEIVED"}
    if kind in starts:
        if run_id in run_states: raise ValueError("Run already started")
        if len(run_states) >= 8: raise ValueError("Too many active runs; reset interrupted routes first")
        state = GraphState(); run_states[run_id] = state
        try: snapshot = state.process_event(event)
        except Exception:
            run_states.pop(run_id, None); raise
        graph_state = state
    else:
        state = run_states.get(run_id)
        if state is None: raise ValueError("Run has not started or is already finished")
        snapshot = state.process_event(event)
    displayed = state is graph_state
    if not state.current_run_id: run_states.pop(run_id, None)
    return snapshot, displayed


class ConnectionManager:
    """WebSockets broadcast manager."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept(subprotocol="cad-xray")
        self.active_connections.append(websocket)
        # Send initial full state
        await websocket.send_json({
            "type": "INIT",
            "state": graph_state.get_snapshot(),
            "config": graph_state.config,
            "history": graph_state.history,
            "runs": saved_runs
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


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
@app.get("/dashboard")
async def get_dashboard():
    """Serves the interactive X-Ray Graph visualizer HTML page."""
    if DASHBOARD_HTML_FILE.exists():
        content = DASHBOARD_HTML_FILE.read_text(encoding="utf-8")
        bootstrap = {
            "config": graph_state.config,
            "runs": saved_runs[:1],
            "state": graph_state.get_snapshot(),
            "token": AUTH_TOKEN
        }
        nonce = secrets.token_urlsafe(24)
        content = content.replace("<script>", f'<script nonce="{nonce}">')
        serialized = json.dumps(bootstrap, ensure_ascii=False).replace("<", "\\u003c")
        inject = f'<script nonce="{nonce}">window.__BOOTSTRAP__ = {serialized};</script>'
        content = content.replace("</head>", f"{inject}\n</head>")
        return HTMLResponse(content=content, headers={"Content-Security-Policy": f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:"})
    return HTMLResponse(
        content="<h2>Dashboard asset not found. Expected src/agent_observer/web/dashboard.html.</h2>",
        status_code=404
    )


@app.get("/api/config")
async def get_config():
    """Returns the current Agent resource catalog."""
    return JSONResponse(content=graph_state.config)


@app.get("/api/state")
async def get_state():
    """Returns current graph snapshot."""
    return JSONResponse(content=graph_state.get_snapshot())


@app.get("/api/history")
async def get_history():
    """Returns full event timeline history for replay scrubber."""
    return JSONResponse(content={"history": graph_state.history})


@app.get("/api/runs")
async def get_runs():
    """Returns completed or interrupted local runs for replay."""
    return JSONResponse(content={"runs": saved_runs})


@app.post("/api/event")
async def post_event(request: Request):
    """Ingests a telemetry event from agents, Python scripts or Swift app."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    previous_catalog = graph_state.config
    try:
        snapshot, displayed = route_event(body)
    except (TypeError, ValueError, AttributeError) as exc:
        return JSONResponse(content={"status": "error", "detail": str(exc)}, status_code=422)
    if displayed:
        await ws_manager.broadcast({
            "type": "STATE_UPDATE",
            "event": body,
            **({"config": graph_state.config} if graph_state.config is not previous_catalog else {}),
            "state": snapshot,
            "history_step": graph_state.history[-1] if graph_state.history else None,
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
                    ev = msg.get("event", {})
                    snapshot, displayed = route_event(ev)
                    if not displayed: continue
                    await ws_manager.broadcast({
                        "type": "STATE_UPDATE",
                        "event": ev,
                        "state": snapshot,
                        "history_step": graph_state.history[-1] if graph_state.history else None
                    })
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
                "reason": "Вызвана Python-функция",
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
                    "reason": "Python-функция завершилась"
                })

        return wrapper
    return decorator


def emit_xray_event(event: dict):
    """Synchronous / fire-and-forget helper to emit an X-Ray event.
    Works whether running standalone or sending to http://127.0.0.1:8765/api/event.
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            os.environ.get("AGENT_XRAY_ENDPOINT", "http://127.0.0.1:8765/api/event"),
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
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
