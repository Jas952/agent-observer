"""Native Codex app-server stream observation and relay."""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import websockets

logger = logging.getLogger("agent_observer.codex_stream")

SENSITIVE_EXACT_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "apikey",
    "secret",
    "private_key",
    "access_token",
    "refresh_token",
    "password",
    "client_secret",
    "auth_token",
    "token",
    "bearer_token",
}

NON_SENSITIVE_TOKEN_KEYS = {
    "tokenusage",
    "token_usage",
    "tokens",
    "inputtokens",
    "input_tokens",
    "outputtokens",
    "output_tokens",
    "totaltokens",
    "total_tokens",
    "cachedtokens",
    "cached_tokens",
    "cachedinputtokens",
    "cached_input_tokens",
    "reasoningtokens",
    "reasoning_tokens",
    "reasoningoutputtokens",
    "reasoning_output_tokens",
    "cachewriteinputtokens",
    "cache_write_input_tokens",
    "modelcontextwindow",
    "authmode",
    "auth_mode",
    "plantype",
    "plan_type",
}

REASONING_EVENT_NAMES = {
    "item/reasoning/textdelta",
    "item/reasoning/text_delta",
    "item/reasoning/summarytextdelta",
    "item/reasoning/summary_text_delta",
    "item/reasoning/summarypartadded",
    "reasoningthreaditem",
}

HIGH_VOLUME_DELTA_METHODS = {
    "item/agentmessage/delta",
    "item/plan/delta",
    "command/exec/outputdelta",
    "process/outputdelta",
    "item/commandexecution/outputdelta",
    "item/filechange/outputdelta",
    "item/reasoning/textdelta",
    "item/reasoning/summarytextdelta",
    "item/reasoning/summarypartadded",
    "thread/realtime/outputaudio/delta",
    "thread/realtime/transcript/delta",
    "thread/realtime/item/transcript/delta",
    "fs/changed",
}


def is_sensitive_key(key: str) -> bool:
    """Check if a dictionary key name indicates a sensitive secret or credential."""
    k = key.lower().replace("-", "_")
    if k in SENSITIVE_EXACT_KEYS:
        return True
    if k in NON_SENSITIVE_TOKEN_KEYS:
        return False
    if any(s in k for s in ("password", "secret", "private_key", "api_key", "apikey", "credential")):
        return True
    if k.endswith(("_token", "token")) and not k.endswith(("tokens", "token_usage", "tokenusage")):
        return True
    return False


def bound_text(text: str, limit: int = 65536) -> str:
    """Bound text to a limit, appending an explicit truncation marker if exceeded."""
    if text is None:
        return ""
    s = str(text)
    if len(s) > limit:
        return s[:limit] + "\n[TRUNCATED: remaining output omitted]"
    return s

def sanitize_payload(
    obj: Any,
    max_depth: int = 10,
    max_str_len: int = 16384,
    in_reasoning: bool = False,
) -> Any:
    """Sanitize observational data by redacting auth tokens, API keys, private reasoning, and truncating."""
    if max_depth <= 0:
        return "[MAX_DEPTH_EXCEEDED]"

    if obj is None or isinstance(obj, (bool, int, float)):
        return obj

    if isinstance(obj, str):
        if in_reasoning:
            return "[REDACTED_REASONING]"
        # Redact bearer tokens or API key patterns
        val = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED]", obj, flags=re.IGNORECASE)
        val = re.sub(r"sk-[A-Za-z0-9_\-]{20,}", "sk-[REDACTED]", val)
        if len(val) > max_str_len:
            return val[:max_str_len] + "\n[TRUNCATED: remaining bytes omitted]"
        return val

    if isinstance(obj, dict):
        in_reasoning = in_reasoning or str(obj.get("type", "")).lower() in {"reasoning", "thinking"} or obj.get("channel") == "analysis"
        result = {}
        for k, v in obj.items():
            key_str = str(k)
            k_lower = key_str.lower().replace("-", "_")
            if is_sensitive_key(key_str):
                result[key_str] = "[REDACTED]"
            elif k_lower in {"reasoning", "reasoningtext", "reasoning_text", "thought", "chain_of_thought"} or (
                in_reasoning and k_lower in {"text", "delta", "summary"}
            ):
                result[key_str] = "[REDACTED_REASONING]"
            else:
                metadata = NON_SENSITIVE_TOKEN_KEYS | {"method", "type", "id", "itemid", "item_id", "threadid", "thread_id", "turnid", "turn_id", "status", "timestamp"}
                child_in_reasoning = in_reasoning and k_lower not in metadata
                result[key_str] = sanitize_payload(
                    v,
                    max_depth=max_depth - 1,
                    max_str_len=max_str_len,
                    in_reasoning=child_in_reasoning,
                )
        return result

    if isinstance(obj, list):
        return [
            sanitize_payload(item, max_depth=max_depth - 1, max_str_len=max_str_len, in_reasoning=in_reasoning)
            for item in obj
        ]

    return str(obj)


def resolve_codex_executable(executable: str | Path | None = None) -> Path:
    """Resolve a valid, executable Codex CLI path without silent provider or model substitutions."""
    if executable:
        p = Path(executable).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise FileNotFoundError(f"Specified Codex executable not found or not executable: {executable}")

    env_exe = os.environ.get("CODEX_EXECUTABLE")
    if env_exe:
        p = Path(env_exe).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    which_path = shutil.which("codex")
    if which_path:
        return Path(which_path).resolve()

    # Standard system installation candidates (test runtimes require explicit argument or CODEX_EXECUTABLE)
    candidates = [
        Path.home() / ".local/bin/codex",
        Path.home() / ".npm-global/bin/codex",
        Path("/usr/local/bin/codex"),
        Path("/opt/homebrew/bin/codex"),
    ]
    extension_root = Path.home() / ".vscode/extensions"
    if extension_root.is_dir():
        extensions = sorted(extension_root.glob("openai.chatgpt-*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for extension in extensions[:4]:
            candidates.extend(sorted(extension.glob("bin/*/codex")))
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c.resolve()
    raise FileNotFoundError(
        "Codex executable not found on PATH or known locations. "
        "Please provide --executable <PATH> or set CODEX_EXECUTABLE."
    )


def emit_observer_event(
    event: dict,
    config_file: str | Path | None = None,
    project_root: str | Path | None = None,
    timeout: float = 0.25,
) -> bool:
    """Send an event to the local Agent Observer endpoint fail-open without cross-project env pollution."""
    try:
        from .observer_config import load_settings
        if not config_file:
            if not project_root:
                return False
            config_file = Path(project_root) / ".agent-observer/config/observer.project.json"
            if not config_file.is_file():
                return False
        settings = load_settings(config_file=config_file, project_root=project_root, environment={})
        port = int(settings.get("port") or 8766)
        token_file = Path(settings.get("token_file") or "")
        token = ""
        if token_file.is_file():
            try:
                token = token_file.read_text(encoding="utf-8").strip()
            except OSError:
                token = ""
        endpoint = f"http://127.0.0.1:{port}/api/event"
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-CAD-Token": token,
            "X-Agent-Observer-Token": token,
        }
        req = urllib.request.Request(endpoint, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.debug(f"Observer event delivery skipped fail-open: {exc}")
        return False

def _get_hook_before_snapshot(project_root: Path | None, tool_id: str) -> list[dict] | None:
    """Retrieve pre-mutation baseline snapshot from codex_hooks if recorded."""
    if not tool_id:
        return None
    try:
        from .codex_hooks import get_pre_tool_snapshot
        res = get_pre_tool_snapshot(str(project_root) if project_root else None, tool_id, consume=True)
        if isinstance(res, list):
            return [{**record, "path": str((project_root / record["path"]).resolve())} if project_root and isinstance(record.get("path"), str) else record for record in res if isinstance(record, dict)]
    except Exception:
        pass
    return None

def _command_read_paths(actions: list, cwd: Path) -> list[str]:
    """Keep reported reads and literal operands of simple read commands, not script-internal I/O."""
    paths = []
    def add(value, reported=False):
        if not isinstance(value, str) or not value or value == "-" or "\0" in value:
            return
        if not reported and (value.startswith("~") or any(char in value for char in "$`*?[];&|<>\n")):
            return
        try:
            path = str((cwd / value).resolve())
        except (OSError, ValueError):
            return
        if path not in paths:
            paths.append(path)
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("type") == "read":
            add(action.get("path"), reported=True)
        try:
            tokens = shlex.split(action.get("command") or action.get("cmd") or "")
        except ValueError:
            continue
        if not tokens or any(any(char in token for char in ";&|<>`") for token in tokens):
            continue
        if Path(tokens[0]).name == "rtk":
            tokens = tokens[1:]
            if tokens and tokens[0] == "proxy":
                tokens = tokens[1:]
        if not tokens or Path(tokens[0]).name not in {"cat", "head", "tail", "sed"}:
            continue
        program, index, script, positional = Path(tokens[0]).name, 1, False, False
        if any(token in {"--help", "--version"} or program == "sed" and token.startswith(("-i", "--in-place")) for token in tokens[1:]):
            continue
        while index < len(tokens):
            token = tokens[index]
            if token == "--" and not positional:
                positional = True
            elif token.startswith("-") and token != "-" and not positional:
                if program in {"head", "tail"} and token in {"-n", "-c", "--lines", "--bytes", "--pid", "-s", "--sleep-interval"}:
                    index += 1
                elif program == "sed" and token in {"-e", "-f", "--expression", "--file"}:
                    script = True
                    index += 1
                    if token in {"-f", "--file"} and index < len(tokens):
                        add(tokens[index])
                elif program == "sed" and token.startswith(("-e", "-f", "--expression=", "--file=")):
                    script = True
                    if token.startswith("-f"):
                        add(token[2:])
                    elif token.startswith("--file="):
                        add(token.split("=", 1)[1])
            elif program == "sed" and not script:
                script = True
            else:
                add(token)
            index += 1
    return paths


def _before_from_change(change: dict, after: dict) -> dict | None:
    """Reverse only a source diff that matches the entire observed after-state."""
    kind = change.get("kind")
    kind = kind.get("type") if isinstance(kind, dict) else kind
    diff, content = change.get("diff"), after.get("content")
    if not isinstance(diff, str) or not isinstance(content, str) or not after.get("exists"):
        return None
    if kind == "add" and diff == content:
        return {"path": after["path"], "stage": "before", "content": "", "exists": False, "source": "codex-file-change"}
    if kind != "update" or len(diff) > 65536:
        return None
    lines, patch = content.splitlines(keepends=True), diff.splitlines(keepends=True)
    before, cursor, index, hunks = [], 0, 0, 0
    while index < len(patch):
        header = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", patch[index])
        if not header:
            return None
        old_start, old_count = int(header[1]), int(header[2] or 1)
        new_start, new_count = int(header[3]), int(header[4] or 1)
        start = max(new_start - 1, 0)
        if start < cursor or start > len(lines):
            return None
        before.extend(lines[cursor:start])
        cursor = start
        if len(before) != max(old_start - 1, 0):
            return None
        old_seen = new_seen = 0
        index += 1
        while index < len(patch) and not patch[index].startswith("@@ "):
            line = patch[index]
            if not line or line[0] not in " +-":
                return None
            prefix, text = line[0], line[1:]
            if prefix in " +":
                if cursor >= len(lines) or lines[cursor] != text:
                    return None
                cursor += 1
                new_seen += 1
            if prefix in " -":
                before.append(text)
                old_seen += 1
            index += 1
        if (old_seen, new_seen) != (old_count, new_count):
            return None
        hunks += 1
    if not hunks:
        return None
    before.extend(lines[cursor:])
    return {"path": after["path"], "stage": "before", "content": "".join(before), "exists": True, "source": "codex-unified-diff"}



class CodexNormalizer:
    """Normalizes native Codex app-server JSON-RPC stream into graph events with turn/session/subagent isolation."""

    def __init__(
        self,
        emitter: Callable[[dict], bool] | None = None,
        project_root: str | Path | None = None,
        config_file: str | Path | None = None,
    ):
        self.emitter = emitter
        self.project_root = Path(project_root).resolve() if project_root else None
        self.config_file = str(Path(config_file).resolve()) if config_file else None

        self.instance_id = secrets.token_hex(4)
        self.seq_num = 0
        self.session_id: str = ""
        self.turn_id: str | None = None
        self.thread_models: dict[str, dict[str, str]] = {}
        self.session_inventories: dict[str, dict] = {}
        self.delivery_failures = 0
        self.final_answers: set[str] = set()

        # Thread/Session hierarchy: subagent_thread_id -> {parent_session_id, parent_run_id, agent_id, agent_path, model}
        self.subagents: Dict[str, Dict[str, Any]] = {}

        # In-flight tools: item_id -> dict
        self.in_flight_tools: Dict[str, Dict[str, Any]] = {}

        # Turns with already emitted PROMPT_START
        self.prompt_emitted_for_turn: Set[str] = set()

        # Token usage attempt deduplication: (thread_id, turn_id, totalTokens, outputTokens) -> bool
        self.emitted_usage_attempts: Set[Tuple[str, str, int, int]] = set()

        # Last assistant message per turn: turn_id -> text
        self.last_agent_message: Dict[str, str] = {}

        # Local recorded events list for test verification and diagnostics (capped)
        self.recorded_events: List[dict] = []

    def _next_event_id(self) -> str:
        self.seq_num += 1
        return f"codex-stream-{self.instance_id}-{self.seq_num}"

    def _now_iso(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _extract_timestamp(self, msg: dict) -> str:
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        for key in ("emittedAtMs", "startedAtMs", "completedAtMs"):
            val = params.get(key) or msg.get(key)
            if isinstance(val, (int, float)) and val > 0:
                try:
                    return datetime.datetime.fromtimestamp(val / 1000.0, tz=datetime.timezone.utc).isoformat()
                except Exception:
                    pass
        return self._now_iso()

    @property
    def current_run_id(self) -> str | None:
        if self.session_id and self.turn_id:
            return f"codex:{self.session_id}:{self.turn_id}"
        return None

    def _inventory_for(self, session: str) -> dict:
        if session not in self.session_inventories:
            from .capabilities import discover_capabilities
            self.session_inventories[session] = discover_capabilities(self.project_root, self.config_file, agent="codex")
        return self.session_inventories[session]

    def _publish_inventory(self, session: str):
        self._emit({"type": "RUNTIME_INVENTORY", "application": "Codex", "source": "codex", "session_id": session, "inventory": self._inventory_for(session), "timestamp": self._now_iso()})

    def _emit(self, event: dict) -> bool:
        from .agent_events import sanitize_event_data
        if event.get("type") == "AGENT_EVENT" and isinstance(event.get("agent_event"), dict):
            payload = event["agent_event"]
            payload["data"], payload["truncated"] = sanitize_event_data(payload.get("data"), name=str(payload.get("name", "")))
        event, _ = sanitize_event_data(event)
        if event.get("native_tool") and event.get("type") == "TOOL_CALL_START":
            inventory = self.session_inventories.get(event.get("session_id"))
            if inventory is not None and not any(tool["name"] == event["tool"] for tool in inventory.get("native_tools", [])):
                inventory.setdefault("native_tools", []).append({"name": event["tool"], "source": "codex-protocol"})
        self.recorded_events.append(event)
        if len(self.recorded_events) > 5000:
            self.recorded_events.pop(0)

        try:
            delivered = bool(self.emitter(event)) if self.emitter is not None else emit_observer_event(event, self.config_file, self.project_root)
        except Exception:
            delivered = False
        if not delivered:
            self.delivery_failures += 1
            if self.delivery_failures == 1:
                logger.warning("Observer capture is unavailable; Codex continues, but this session's graph is incomplete.")
        return delivered

    def _file_changes(self, changes) -> list[dict]:
        normalized = []
        for change in changes if isinstance(changes, list) else []:
            if not isinstance(change, dict) or not isinstance(change.get("path"), str):
                continue
            path = Path(change["path"])
            try:
                path = (self.project_root / path if self.project_root else path).resolve()
            except (OSError, ValueError):
                pass
            normalized.append({**change, "path": str(path)})
        return normalized

    def _capture_file_snapshots(self, changes: list[dict], stage: str, item_id: str = "") -> list[dict]:
        if stage == "before":
            if item_id:
                hook_snap = _get_hook_before_snapshot(self.project_root, item_id)
                if hook_snap:
                    return hook_snap
            # Never infer disk read at item/started as pre-mutation baseline.
            # App-server notifications arrive asynchronously after mutations may already be in-flight.
            return []

        snapshots = []
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            path_str = ch.get("path")
            if not path_str:
                continue
            full_path = (self.project_root / path_str).resolve() if self.project_root else Path(path_str)
            if self.project_root and not full_path.is_relative_to(self.project_root):
                continue
            try:
                if not full_path.exists():
                    snapshots.append({"path": str(full_path), "stage": "after", "content": "", "exists": False})
                    continue
                with full_path.open("rb") as stream:
                    data = stream.read(65537)
                if len(data) > 65536 or b"\0" in data:
                    continue
                snapshots.append({"path": str(full_path), "stage": "after", "content": data.decode("utf-8"), "exists": True})
            except (OSError, UnicodeError):
                continue
        return snapshots
    def _source_context(self, message: dict) -> tuple[str, str | None]:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        result = message.get("result") if isinstance(message.get("result"), dict) else {}
        thread = params.get("thread") or result.get("thread") or {}
        thread_id = params.get("threadId") or (thread.get("id") if isinstance(thread, dict) else None)
        session = str(thread_id or self.session_id or f"connection:{self.instance_id}")
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        turn_id = params.get("turnId") or turn.get("id")
        return session, f"codex:{session}:{turn_id}" if turn_id else None


    def observe_client_message(self, msg: dict):
        """Observe client -> server JSON-RPC message (approval, request, response)."""
        if not isinstance(msg, dict):
            return

        method = msg.get("method")
        if not method:
            method = "response" if ("result" in msg or "error" in msg) else "client_message"

        event_id = self._next_event_id()
        sanitized = sanitize_payload(msg)
        session, run_id = self._source_context(msg)

        agent_ev = {
            "type": "AGENT_EVENT",
            "application": "Codex",
            "source": "codex",
            "session_id": session,
            "event_id": event_id,
            "agent_event": {
                "name": str(method),
                "direction": "client",
                "data": sanitized,
            },
            "timestamp": self._now_iso(),
        }
        if run_id:
            agent_ev["run_id"] = run_id

        self._emit(agent_ev)

    def observe_server_message(self, msg: dict):
        """Observe server -> client JSON-RPC message (notification, response, request)."""
        if not isinstance(msg, dict):
            return

        method = msg.get("method")
        if not method:
            method = "response" if ("result" in msg or "error" in msg) else "server_message"

        method_str = str(method)
        method_lower = method_str.lower()
        ts = self._extract_timestamp(msg)
        event_id = self._next_event_id()

        # Sanitize data: if reasoning notification, redact reasoning body
        is_reasoning = method_lower in REASONING_EVENT_NAMES
        sanitized = sanitize_payload(msg, in_reasoning=is_reasoning)
        session, run_id = self._source_context(msg)

        # 1. ALWAYS emit AGENT_EVENT for every protocol notification/message
        agent_ev = {
            "type": "AGENT_EVENT",
            "application": "Codex",
            "source": "codex",
            "session_id": session,
            "event_id": event_id,
            "agent_event": {
                "name": method_str,
                "direction": "server",
                "data": sanitized,
            },
            "timestamp": ts,
        }
        if run_id:
            agent_ev["run_id"] = run_id

        self._emit(agent_ev)

        # 2. High-volume deltas remain journal-only (do not create graph steps)
        if method_lower in HIGH_VOLUME_DELTA_METHODS:
            return

        # 3. Normalize semantic lifecycle events
        self._normalize_semantic(method_str, msg, ts)

    def _normalize_semantic(self, method: str, msg: dict, ts: str):
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        result = msg.get("result") if isinstance(msg.get("result"), dict) else {}
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        if thread.get("id") and isinstance(result.get("model"), str):
            self.thread_models[str(thread["id"])] = {"model": result["model"], "backend": str(result.get("modelProvider") or "")}

        if method == "thread/started":
            thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
            tid = thread.get("id")
            if tid:
                self.session_id = str(tid)
                self._publish_inventory(self.session_id)

        elif method == "turn/started":
            thread_id = str(params.get("threadId") or self.session_id)
            if thread_id:
                self.session_id = thread_id
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or "")
            if turn_id:
                self.turn_id = turn_id

            run_id = f"codex:{self.session_id}:{self.turn_id}" if (self.session_id and self.turn_id) else None
            sub = self.subagents.get(self.session_id, {})
            parent_run_id = sub.get("parent_run_id")
            agent_id = sub.get("agent_id")

            # Check if turn contains initial user message items
            items = turn.get("items") or []
            prompt_text = ""
            for it in items:
                if isinstance(it, dict) and it.get("type") == "userMessage":
                    content = it.get("content") or []
                    if isinstance(content, list):
                        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")]
                        prompt_text = "\n".join(parts)
                    elif isinstance(content, str):
                        prompt_text = content
                    break

            if prompt_text and turn_id and turn_id not in self.prompt_emitted_for_turn:
                ev = {
                    "type": "PROMPT_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": self.session_id,
                    "run_id": run_id,
                    "prompt": prompt_text,
                    "timestamp": ts,
                    **self.thread_models.get(self.session_id, {}),
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)
                self.prompt_emitted_for_turn.add(turn_id)

        elif method == "item/started":
            thread_id = str(params.get("threadId") or self.session_id)
            turn_id = str(params.get("turnId") or self.turn_id or "")
            if thread_id:
                self.session_id = thread_id
            if turn_id:
                self.turn_id = turn_id

            run_id = f"codex:{thread_id}:{turn_id}" if (thread_id and turn_id) else self.current_run_id
            sub = self.subagents.get(thread_id, {})
            parent_run_id = sub.get("parent_run_id")
            agent_id = sub.get("agent_id")

            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_type = str(item.get("type") or "")
            item_id = str(item.get("id") or "")

            if item_type == "userMessage":
                if turn_id and turn_id not in self.prompt_emitted_for_turn:
                    content = item.get("content") or []
                    if isinstance(content, list):
                        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")]
                        p_text = "\n".join(parts)
                    else:
                        p_text = str(content)
                    ev = {
                        "type": "PROMPT_START",
                        "application": "Codex",
                        "source": "codex",
                        "session_id": thread_id,
                        "run_id": run_id,
                        "prompt": p_text,
                        "timestamp": ts,
                        **self.thread_models.get(thread_id, {}),
                    }
                    if parent_run_id:
                        ev["parent_run_id"] = parent_run_id
                    if agent_id:
                        ev["agent_id"] = agent_id
                    self._emit(ev)
                    self.prompt_emitted_for_turn.add(turn_id)

            elif item_type == "commandExecution":
                cmd = str(item.get("command") or "")
                actions = item.get("commandActions") if isinstance(item.get("commandActions"), list) else []
                reported_command = actions[0].get("command") if len(actions) == 1 and isinstance(actions[0], dict) else None
                reported_command = reported_command if isinstance(reported_command, str) else cmd
                read_paths = _command_read_paths(actions, Path(item.get("cwd") or self.project_root or os.getcwd()))
                self.in_flight_tools[item_id] = {
                    "tool": "bash",
                    "raw_tool": "commandExecution",
                    "command": reported_command,
                    "raw_command": cmd,
                    "cwd": item.get("cwd"),
                    "paths": read_paths,
                    "command_actions": actions,
                    "session_id": thread_id,
                    "turn_id": turn_id,
                }
                ev = {
                    "type": "TOOL_CALL_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": "bash",
                    "raw_tool": "commandExecution",
                    "native_tool": True,
                    "input": json.dumps({"command": cmd, "cwd": item.get("cwd")}, ensure_ascii=False),
                    "command": reported_command,
                    "raw_command": cmd,
                    "paths": read_paths,
                    "file_actions": [{"path": path, "action": "read"} for path in read_paths],
                    "command_actions": actions,
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "fileChange":
                changes = self._file_changes(item.get("changes"))
                before_snapshots = self._capture_file_snapshots(changes, stage="before", item_id=item_id)
                self.in_flight_tools[item_id] = {
                    "tool": "apply_patch",
                    "raw_tool": "fileChange",
                    "changes": changes,
                    "before_snapshots": before_snapshots,
                    "session_id": thread_id,
                    "turn_id": turn_id,
                }
                ev = {
                    "type": "TOOL_CALL_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": "apply_patch",
                    "raw_tool": "fileChange",
                    "native_tool": True,
                    "paths": [change["path"] for change in changes if isinstance(change, dict) and isinstance(change.get("path"), str)],
                    "input": json.dumps(
                        {"changes": [{"path": c.get("path"), "kind": c.get("kind")} for c in changes if isinstance(c, dict)]},
                        ensure_ascii=False,
                    ),
                    "file_snapshots": before_snapshots,
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "webSearch":
                action = item.get("action") if isinstance(item.get("action"), dict) else {}
                q = str(item.get("query") or action.get("query") or "")
                self.in_flight_tools[item_id] = {
                    "tool": "WebSearch",
                    "raw_tool": "webSearch",
                    "query": q,
                    "session_id": thread_id,
                    "turn_id": turn_id,
                }
                ev = {
                    "type": "TOOL_CALL_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": "WebSearch",
                    "raw_tool": "webSearch",
                    "native_tool": True,
                    "query": q,
                    "input": json.dumps({"query": q, "action": item.get("action")}, ensure_ascii=False),
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "mcpToolCall":
                server = str(item.get("server") or "mcp")
                tool_subname = str(item.get("tool") or "tool")
                canonical_tool = f"mcp__{server}__{tool_subname}"
                args = item.get("arguments")
                self.in_flight_tools[item_id] = {
                    "tool": canonical_tool,
                    "raw_tool": "mcpToolCall",
                    "server": server,
                    "session_id": thread_id,
                    "turn_id": turn_id,
                }
                ev = {
                    "type": "TOOL_CALL_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": canonical_tool,
                    "raw_tool": "mcpToolCall",
                    "mcp_server": server,
                    "input": json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args or "{}"),
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "subAgentActivity":
                agent_thread_id = item.get("agentThreadId")
                if agent_thread_id:
                    self.subagents[str(agent_thread_id)] = {
                        "parent_session_id": thread_id,
                        "parent_run_id": run_id,
                        "agent_id": item_id,
                        "agent_path": item.get("agentPath"),
                        "model": item.get("model"),
                    }
                if item.get("model"):
                    m_ev = {
                        "type": "MODEL_INFO",
                        "application": "Codex",
                        "source": "codex",
                        "session_id": thread_id,
                        "run_id": run_id,
                        "model": str(item.get("model")),
                        "agent_id": item_id,
                        "timestamp": ts,
                    }
                    if parent_run_id:
                        m_ev["parent_run_id"] = parent_run_id
                    self._emit(m_ev)

            elif item_type not in {"reasoning", "agentMessage", "plan"}:
                # Retain non-whitelisted/custom tool names
                custom_name = str(item.get("name") or item.get("tool") or item_type)
                args = item.get("arguments") or item.get("input") or {}
                self.in_flight_tools[item_id] = {
                    "tool": custom_name,
                    "raw_tool": item_type,
                    "session_id": thread_id,
                    "turn_id": turn_id,
                }
                ev = {
                    "type": "TOOL_CALL_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": custom_name,
                    "raw_tool": item_type,
                    "native_tool": True,
                    "input": json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args),
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

        elif method == "item/completed":
            thread_id = str(params.get("threadId") or self.session_id)
            turn_id = str(params.get("turnId") or self.turn_id or "")
            if thread_id:
                self.session_id = thread_id
            if turn_id:
                self.turn_id = turn_id

            run_id = f"codex:{thread_id}:{turn_id}" if (thread_id and turn_id) else self.current_run_id
            sub = self.subagents.get(thread_id, {})
            parent_run_id = sub.get("parent_run_id")
            agent_id = sub.get("agent_id")

            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_type = str(item.get("type") or "")
            item_id = str(item.get("id") or "")
            stored = self.in_flight_tools.pop(item_id, {})

            if item_type == "commandExecution":
                exit_code = item.get("exitCode")
                success = exit_code == 0
                out = bound_text(item.get("aggregatedOutput") or "")
                ev = {
                    "type": "TOOL_CALL_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": stored.get("tool", "bash"),
                    "raw_tool": "commandExecution",
                    "native_tool": True,
                    "command": stored.get("command", item.get("command", "")),
                    "raw_command": stored.get("raw_command", item.get("command", "")),
                    "paths": stored.get("paths", []),
                    "file_actions": [{"path": path, "action": "read"} for path in stored.get("paths", [])],
                    "command_actions": item.get("commandActions") or stored.get("command_actions", []),
                    "status": "completed" if success else "failed",
                    "result": out,
                    "exit_code": exit_code,
                    "success": success,
                    "duration_ms": item.get("durationMs"),
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "fileChange":
                changes = self._file_changes(item.get("changes") if isinstance(item.get("changes"), list) else stored.get("changes", []))
                before_snapshots = stored.get("before_snapshots") or _get_hook_before_snapshot(self.project_root, item_id) or []
                after_snapshots = self._capture_file_snapshots(changes, stage="after", item_id=item_id)
                supplied = {snapshot["path"] for snapshot in before_snapshots}
                changes_by_path = {str((self.project_root / change["path"]).resolve()) if self.project_root else str(Path(change["path"]).resolve()): change for change in changes if isinstance(change, dict) and isinstance(change.get("path"), str)}
                for after in after_snapshots:
                    if after["path"] not in supplied and after["path"] in changes_by_path:
                        before = _before_from_change(changes_by_path[after["path"]], after)
                        if before:
                            before_snapshots.append(before)
                combined_snapshots = list(before_snapshots) + [s for s in after_snapshots if s not in before_snapshots]
                raw_st = str(item.get("status") or "completed")
                success = raw_st.lower() in {"completed", "applied", "success", "ok"}
                ev = {
                    "type": "TOOL_CALL_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": "apply_patch",
                    "raw_tool": "fileChange",
                    "native_tool": True,
                    "success": success,
                    "status": raw_st,
                    "file_snapshots": combined_snapshots,
                    "paths": [change["path"] for change in changes if isinstance(change, dict) and isinstance(change.get("path"), str)],
                    "file_changes": changes,
                    "result": f"File change {raw_st} ({len(changes)} files)",
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "webSearch":
                action = item.get("action") if isinstance(item.get("action"), dict) else {}
                query = str(item.get("query") or action.get("query") or stored.get("query") or "")
                result = {key: item[key] for key in ("action", "results", "error") if item.get(key) is not None}
                success = not item.get("error") and item.get("status") not in {"failed", "error"}
                ev = {
                    "type": "TOOL_CALL_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": "WebSearch",
                    "raw_tool": "webSearch",
                    "native_tool": True,
                    "success": success,
                    "status": "completed" if success else "failed",
                    "query": query,
                    "input": json.dumps({"query": query, "action": action}, ensure_ascii=False),
                    "result": bound_text(json.dumps(result, ensure_ascii=False)),
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "mcpToolCall":
                server = str(item.get("server") or stored.get("server", "mcp"))
                tool_subname = str(item.get("tool") or "tool")
                canonical_tool = stored.get("tool") or f"mcp__{server}__{tool_subname}"
                err = item.get("error")
                success = (str(item.get("status", "")).lower() in {"completed", "success", "ok"}) and not err
                res = item.get("result")
                res_str = bound_text(
                    str(err)
                    if err
                    else (json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res or "Completed"))
                )
                ev = {
                    "type": "TOOL_CALL_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": canonical_tool,
                    "raw_tool": "mcpToolCall",
                    "mcp_server": server,
                    "success": success,
                    "status": "completed" if success else "failed",
                    "result": res_str,
                    "duration_ms": item.get("durationMs"),
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

            elif item_type == "agentMessage":
                text = item.get("text")
                if isinstance(text, str) and text and turn_id and item.get("phase") == "final_answer":
                    self.last_agent_message[turn_id] = text
                    self.final_answers.add(turn_id)
                elif isinstance(text, str) and text and turn_id and item.get("phase") is None and turn_id not in self.final_answers:
                    self.last_agent_message[turn_id] = text
                elif isinstance(text, str) and text and item.get("phase") == "commentary":
                    self._emit({"type": "THINKING", "application": "Codex", "source": "codex", "session_id": thread_id, "run_id": run_id, "phase": bound_text(text, 1200), "timestamp": ts})

            elif stored:
                tool_name = stored.get("tool", item_type)
                err = item.get("error")
                success = (str(item.get("status", "")).lower() not in {"error", "failed"}) and not err
                res = item.get("result") or item.get("output") or ("Completed" if success else str(err or "Failed"))
                ev = {
                    "type": "TOOL_CALL_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "call_id": item_id,
                    "tool": tool_name,
                    "raw_tool": item_type,
                    "native_tool": True,
                    "success": success,
                    "status": "completed" if success else "failed",
                    "result": bound_text(str(res)),
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

        elif method == "thread/tokenUsage/updated":
            thread_id = str(params.get("threadId") or self.session_id)
            turn_id = str(params.get("turnId") or self.turn_id or "")
            run_id = f"codex:{thread_id}:{turn_id}" if (thread_id and turn_id) else self.current_run_id
            sub = self.subagents.get(thread_id, {})
            parent_run_id = sub.get("parent_run_id")
            agent_id = sub.get("agent_id")

            token_usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
            last = token_usage.get("last") if isinstance(token_usage.get("last"), dict) else {}

            total_tokens = int(last.get("totalTokens") or 0)
            output_tokens = int(last.get("outputTokens") or 0)
            cumulative = token_usage.get("total") if isinstance(token_usage.get("total"), dict) else {}
            cumulative_tokens = int(cumulative.get("totalTokens") or total_tokens)
            attempt_key = (thread_id, turn_id, cumulative_tokens, output_tokens)

            if total_tokens > 0 and attempt_key not in self.emitted_usage_attempts:
                self.emitted_usage_attempts.add(attempt_key)
                metrics = {
                    "input_tokens": int(last.get("inputTokens") or 0),
                    "output_tokens": output_tokens,
                    "cached_tokens": int(last.get("cachedInputTokens") or 0),
                    "reasoning_tokens": int(last.get("reasoningOutputTokens") or 0),
                    "total_tokens": total_tokens,
                }
                ev = {
                    "type": "THINKING",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": thread_id,
                    "run_id": run_id,
                    "phase": f"Model tokens: {total_tokens:,} ({metrics['input_tokens']:,} in, {metrics['output_tokens']:,} out)",
                    "token_metrics": metrics,
                    **metrics,
                    "model_call_id": f"codex:{thread_id}:{turn_id}:usage:{cumulative_tokens}",
                    "usage_kind": "token_usage",
                    "timestamp": ts,
                }
                if parent_run_id:
                    ev["parent_run_id"] = parent_run_id
                if agent_id:
                    ev["agent_id"] = agent_id
                self._emit(ev)

        elif method == "turn/completed":
            thread_id = str(params.get("threadId") or self.session_id)
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or self.turn_id or "")
            run_id = f"codex:{thread_id}:{turn_id}" if (thread_id and turn_id) else self.current_run_id
            sub = self.subagents.get(thread_id, {})
            parent_run_id = sub.get("parent_run_id")
            agent_id = sub.get("agent_id")

            err = turn.get("error")
            turn_status = str(turn.get("status") or ("failed" if err else "completed"))
            success = err is None and turn_status == "completed"
            result_str = str(err) if err else (self.last_agent_message.get(turn_id) or f"Turn {turn_status}")

            ev = {
                "type": "GENERATION_END",
                "application": "Codex",
                "source": "codex",
                "session_id": thread_id,
                "run_id": run_id,
                "success": success,
                "status": turn_status,
                "result": bound_text(result_str),
                "duration_ms": turn.get("durationMs"),
                "timestamp": ts,
            }
            if parent_run_id:
                ev["parent_run_id"] = parent_run_id
            if agent_id:
                ev["agent_id"] = agent_id
            self._emit(ev)

        elif method == "mcpServer/startupStatus/updated":
            session = str(params.get("threadId") or self.session_id)
            name = params.get("name")
            if session and isinstance(name, str) and name:
                inventory = self._inventory_for(session)
                server = next((item for item in inventory["mcp"] if item["name"] == name), None)
                if server is None:
                    server = {"name": name, "source": "codex-protocol", "description_source": "codex-protocol", "tool_names": []}
                    inventory["mcp"].append(server)
                connection = {"starting": "connecting", "ready": "connected", "failed": "disconnected", "cancelled": "disconnected"}.get(params.get("status"), "unknown")
                server.update(connection=connection, availability=connection)
                self._publish_inventory(session)
        elif method == "skills/changed":
            from .capabilities import discover_skills
            session = str(params.get("threadId") or self.session_id)
            if session:
                self._inventory_for(session)["skills"] = discover_skills(self.project_root, agent="codex")
                self._publish_inventory(session)


class CodexAppServer:
    """Manages the real `codex app-server --listen stdio://` subprocess."""

    def __init__(
        self,
        executable: str | Path,
        cwd: str | Path,
        env: dict[str, str] | None = None,
        on_server_message: Callable[[str, dict], Any] | None = None,
        options: list[str] | None = None,
    ):
        self.executable = Path(executable).resolve()
        self.cwd = Path(cwd).resolve()
        self.env = env or {}
        self.on_server_message = on_server_message
        self.options = options or []

        self.process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stopped = False

    async def start(self):
        """Start the app-server stdio child process."""
        child_env = os.environ.copy()
        child_env.update(self.env)

        self.process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "app-server",
            *self.options,
            "--listen",
            "stdio://",
            cwd=str(self.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
            limit=32 * 1024 * 1024,
        )
        self._read_task = asyncio.create_task(self._stdout_reader())
        self._stderr_task = asyncio.create_task(self._stderr_reader())

    async def _stdout_reader(self):
        while not self._stopped and self.process and self.process.stdout:
            try:
                line = await self.process.stdout.readline()
            except Exception as e:
                logger.debug(f"app-server stdout readline exception: {e}")
                break
            if not line:
                break
            raw_text = line.decode("utf-8", errors="replace").strip()
            if not raw_text:
                continue
            try:
                data = json.loads(raw_text)
            except Exception:
                continue

            if self.on_server_message:
                try:
                    res = self.on_server_message(raw_text, data)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.warning(f"Error in on_server_message handler: {e}")

    async def _stderr_reader(self):
        while not self._stopped and self.process and self.process.stderr:
            try:
                line = await self.process.stderr.readline()
            except Exception:
                break
            if not line:
                break

    async def send_raw(self, raw_line: str):
        """Write a raw JSON line to the app-server's stdin."""
        if self.process and self.process.stdin and not self._stopped:
            data = raw_line.strip().encode("utf-8") + b"\n"
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def stop(self):
        """Gracefully terminate the app-server subprocess."""
        self._stopped = True
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()

        if self.process:
            try:
                if self.process.returncode is None:
                    self.process.terminate()
                    try:
                        await asyncio.wait_for(self.process.wait(), timeout=1.5)
                    except asyncio.TimeoutError:
                        self.process.kill()
                        await self.process.wait()
            except ProcessLookupError:
                pass


def _app_server_options(arguments: list[str]) -> list[str]:
    """Apply explicitly selected CLI settings to the server as well as its client."""
    options, index = [], 0
    aliases = {"-m": "model", "--model": "model", "-s": "sandbox_mode", "--sandbox": "sandbox_mode", "-a": "approval_policy", "--ask-for-approval": "approval_policy"}
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-c", "--config", "--enable", "--disable"} or argument in aliases:
            if index + 1 >= len(arguments):
                raise ValueError(f"Missing value for Codex option {argument}.")
            value = arguments[index + 1]
            options.extend(["-c", f"{aliases[argument]}={json.dumps(value)}"] if argument in aliases else [argument, value])
            index += 2
            continue
        if argument.startswith(("--config=", "--enable=", "--disable=")):
            options.append(argument)
        elif argument == "--search":
            options.extend(["-c", 'web_search="live"'])
        index += 1
    return options

class CodexRelay:
    """Authenticated loopback WebSocket relay bridging native Codex CLI/clients and app-server stdio."""

    def __init__(
        self,
        project: str | Path,
        config_file: str | Path | None = None,
        executable: str | Path | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        auth_token: str | None = None,
        normalizer: CodexNormalizer | None = None,
        codex_args: list[str] | None = None,
    ):
        self.project = Path(project).resolve()
        self.config_file = str(Path(config_file).resolve()) if config_file else None
        self.executable = resolve_codex_executable(executable)
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Codex observation relay must bind to a loopback address.")
        self.host = host
        self.requested_port = port
        self.auth_token = auth_token or secrets.token_hex(24)
        self.token_env_var = "CODEX_REMOTE_TOKEN"
        self.normalizer = normalizer or CodexNormalizer(
            project_root=self.project, config_file=self.config_file
        )
        self.codex_args = codex_args or []

        self.server: Any = None
        self.port: int | None = None
        self.url: str | None = None
        self.app_server: CodexAppServer | None = None
        self.active_websockets: Set[Any] = set()
        self._stopped = False
        self._observations: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._observer_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None

    async def _monitor_app_server(self):
        code = await self.app_server.process.wait()
        if not self._stopped:
            logger.error("Codex app-server exited (%s); inspect the native Codex configuration.", code)
            if self.server:
                self.server.close()

    async def _observe_messages(self):
        while True:
            direction, message = await self._observations.get()
            try:
                observer = self.normalizer.observe_client_message if direction == "client" else self.normalizer.observe_server_message
                await asyncio.to_thread(observer, message)
            except Exception:
                logger.exception("Observer could not normalize a Codex event; the native transport remains unchanged.")
            finally:
                self._observations.task_done()

    async def start(self):
        """Start the app-server subprocess and the authenticated loopback WebSocket listener."""
        self._observer_task = asyncio.create_task(self._observe_messages())
        # 1. Start real app-server
        self.app_server = CodexAppServer(
            executable=self.executable,
            cwd=self.project,
            env={"AGENT_OBSERVER_CODEX_CAPTURE": "stream"},
            on_server_message=self._on_server_message,
            options=_app_server_options(self.codex_args),
        )
        await self.app_server.start()

        # 2. Start authenticated loopback WebSocket server
        async def process_request(connection, request):
            auth = request.headers.get("authorization")
            expected = f"Bearer {self.auth_token}"
            if auth != expected:
                return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
            return None

        self.server = await websockets.serve(
            self._handle_client,
            self.host,
            self.requested_port,
            process_request=process_request,
            max_size=32 * 1024 * 1024,
            close_timeout=1,
        )
        self.port = self.server.sockets[0].getsockname()[1]
        address = f"[{self.host}]" if ":" in self.host else self.host
        self.url = f"ws://{address}:{self.port}"
        logger.debug(f"CodexRelay started at {self.url}")
        self._monitor_task = asyncio.create_task(self._monitor_app_server())

    async def _handle_client(self, websocket: Any):
        self.active_websockets.add(websocket)
        try:
            async for raw_msg in websocket:
                try:
                    data = json.loads(raw_msg)
                except Exception:
                    data = {}

                # Normalizer observes client message (approvals, client requests)
                await self._observations.put(("client", data))

                # Forward unchanged to app-server stdio
                if self.app_server:
                    await self.app_server.send_raw(raw_msg)
        finally:
            self.active_websockets.discard(websocket)

    async def _on_server_message(self, raw_line: str, data: dict):
        # 1. Normalizer translates and emits AGENT_EVENT + normalized graph events
        await self._observations.put(("server", data))

        # 2. Forward server output unchanged to connected WebSocket clients
        if self.active_websockets:
            await asyncio.gather(
                *[ws.send(raw_line) for ws in list(self.active_websockets)],
                return_exceptions=True,
            )

    async def send_client_message(self, msg: dict):
        """Helper for non-interactive parent smoke or test harness without a WebSocket connection."""
        await self._observations.put(("client", msg))
        raw = json.dumps(msg, ensure_ascii=False)
        if self.app_server:
            await self.app_server.send_raw(raw)

    async def stop(self):
        """Clean teardown of WebSocket server and app-server subprocess."""
        if self._stopped:
            return
        self._stopped = True

        for ws in list(self.active_websockets):
            try:
                await ws.close()
            except Exception:
                pass
        self.active_websockets.clear()

        if self.server:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass

        if self.app_server:
            await self.app_server.stop()
        if self._monitor_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
        try:
            await asyncio.wait_for(self._observations.join(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("Observer capture did not fully flush before shutdown; this session's graph is incomplete.")
        finally:
            if self._observer_task:
                self._observer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._observer_task


@contextlib.asynccontextmanager
async def codex_relay_session(
    project: str | Path,
    config_file: str | Path | None = None,
    executable: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    normalizer: CodexNormalizer | None = None,
    codex_args: list[str] | None = None,
):
    """Async context manager helper for lower-level tests and parent JSON-RPC smoke testing."""
    relay = CodexRelay(
        project=project,
        config_file=config_file,
        executable=executable,
        host=host,
        port=port,
        normalizer=normalizer,
        codex_args=codex_args,
    )
    try:
        await relay.start()
        yield relay
    finally:
        await relay.stop()


def run_codex(
    project: str | Path,
    config_file: str | Path | None = None,
    executable: str | Path | None = None,
    codex_args: list[str] | None = None,
) -> int:
    """Synchronous entrypoint: starts relay and app-server, launches native Codex CLI with TTY, returns exit code."""
    project_path = Path(project).resolve()
    if not project_path.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project}")

    from .observer_config import load_settings
    selected_config = Path(config_file).resolve() if config_file else project_path / ".agent-observer/config/observer.project.json"
    if not selected_config.is_file():
        raise FileNotFoundError("Install Agent Observer for this project before launching Codex.")
    settings = load_settings(selected_config, environment={})
    if settings["project_root"] != project_path:
        raise ValueError("The selected Observer configuration belongs to another project.")
    config_file = selected_config
    configured = settings.get("codex", {}).get("executable")
    codex_exe = resolve_codex_executable(executable or configured)
    help_result = subprocess.run([str(codex_exe), "--help"], capture_output=True, text=True, timeout=5)
    if help_result.returncode or "--remote-auth-token-env" not in help_result.stdout:
        raise RuntimeError("Full capture requires a Codex build with the remote App Server client (tested with 0.166.0).")
    if any(argument in {"--remote", "--remote-auth-token-env", "app-server", "exec", "e", "review"} or argument.startswith(("--remote=", "--remote-auth-token-env=")) for argument in codex_args or []):
        raise ValueError("Full capture launches the interactive Codex client; transport overrides and headless subcommands are not supported.")
    from .installation import InstallationManager
    connection = InstallationManager()._inspect_connection(project_path, settings["port"], selected_config.parent.parent)
    if not connection["connected"]:
        raise RuntimeError("This project's Observer server is not running. Start it with the generated run command first.")

    loop = asyncio.new_event_loop()
    relay = CodexRelay(
        project=project_path,
        config_file=config_file,
        executable=codex_exe,
        codex_args=codex_args,
    )

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True, name="CodexRelayThread")
    thread.start()

    try:
        start_future = asyncio.run_coroutine_threadsafe(relay.start(), loop)
        start_future.result(timeout=15.0)

        # Print concise launch information (NEVER print auth token or private reasoning)
        print(f"[Observer] Codex stream relay listening on {relay.url} (capture: full)")
        print(f"[Observer] Launching native Codex CLI ({codex_exe.name})...")

        env = os.environ.copy()
        env["AGENT_OBSERVER_CODEX_CAPTURE"] = "stream"
        env[relay.token_env_var] = relay.auth_token

        cli_cmd = [
            str(codex_exe),
            "--remote",
            relay.url,
            "--remote-auth-token-env",
            relay.token_env_var,
        ]
        if codex_args:
            cli_cmd.extend(codex_args)

        proc = subprocess.run(cli_cmd, env=env, cwd=str(project_path))
        return proc.returncode
    finally:
        try:
            stop_future = asyncio.run_coroutine_threadsafe(relay.stop(), loop)
            stop_future.result(timeout=10.0)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3.0)


__all__ = [
    "CodexAppServer",
    "CodexNormalizer",
    "CodexRelay",
    "bound_text",
    "codex_relay_session",
    "emit_observer_event",
    "is_sensitive_key",
    "resolve_codex_executable",
    "run_codex",
    "sanitize_payload",
]
