"""Codex lifecycle hook adapter and normalizer for Agent Observer.

Observes all 14 documented Codex lifecycle hooks, records them as AGENT_EVENT,
and synthesizes normalized graph events in hooks-only mode.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time
from datetime import datetime, timezone
import uuid

HOOK_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "PreModelRequest",
    "PostModelRequest",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "Interrupt",
)

def sanitize_payload(obj):
    """Apply the shared credential/privacy policy without hiding token-usage metrics."""
    from .agent_events import sanitize_event_data
    return sanitize_event_data(obj)[0]


def get_hook_storage(cwd: str | Path | None, *, create=True) -> Path:
    """Return platform private Observer storage directory for hook state and snapshots."""
    cwd_path = Path(cwd or os.getcwd()).resolve()
    identity = hashlib.sha256(str(cwd_path).encode("utf-8")).hexdigest()[:12]
    support = Path.home() / "Library/Application Support" if sys.platform == "darwin" else Path.home() / ".local/share"
    target_dir = support / "AgentObserver" / identity / "hooks"
    if create:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir.chmod(0o700)
    return target_dir


@contextlib.contextmanager
def hook_state_lock(storage_dir: Path):
    """File lock around persistent hook state to synchronize concurrent hook processes."""
    storage_dir.mkdir(parents=True, exist_ok=True)
    lock_file = storage_dir / "hooks.lock"
    with lock_file.open("a") as stream:
        lock_file.chmod(0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_hook_state(storage_dir: Path) -> dict:
    state_file = storage_dir / "state.json"
    if state_file.is_file():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def write_hook_state(storage_dir: Path, state: dict) -> None:
    state_file = storage_dir / "state.json"
    tmp = state_file.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(state_file)
    except OSError:
        pass


def update_hook_diagnostics(storage_dir: Path, **fields) -> None:
    diag_file = storage_dir / "diagnostics.json"
    current = {}
    if diag_file.is_file():
        try:
            current = json.loads(diag_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
    current.update(fields)
    tmp = diag_file.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(diag_file)
    except OSError:
        pass


def _extract_candidate_file_paths(tool_name: str, tool_input, cwd: Path, project_root: Path | None = None) -> list[Path]:
    if not re.search(r"write|edit|patch|create|delete|move|rename|update", tool_name, re.I):
        return []
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            tool_input = {"patch": tool_input}
    if not isinstance(tool_input, dict):
        return []
    candidates = [tool_input.get(key) for key in ("file_path", "path", "file")]
    for key in ("changes", "edits", "files"):
        for item in tool_input.get(key, []) if isinstance(tool_input.get(key), list) else []:
            candidates.append(item.get("path") if isinstance(item, dict) else item)
    patch = tool_input.get("patch") or tool_input.get("diff") or tool_input.get("input") or tool_input.get("command")
    if isinstance(patch, str):
        for line in patch.splitlines():
            match = re.match(r"^\*\*\* (?:Update File|Add File|Delete File|Move to): (.+)$", line) or re.match(r"^(?:---|\+\+\+) (?:a/|b/)?(.+?)(?:\t.*)?$", line)
            if match:
                candidates.append(match.group(1))
    paths = []
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip() and candidate != "/dev/null":
            path = (cwd / candidate.strip()).resolve()
            if path.is_relative_to(project_root or cwd) and path not in paths:
                paths.append(path)
    return paths


def _capture_file_snapshot(file_path: Path, cwd: Path, stage: str = "before") -> dict | None:
    """Capture a bounded text snapshot of up to 32 KB."""
    try:
        if not file_path.exists():
            return {"path": str(file_path.relative_to(cwd)), "stage": stage, "content": "", "exists": False}
        if file_path.is_file() and file_path.stat().st_size <= 32768:
            content = file_path.read_text(encoding="utf-8")
            return {
                "path": str(file_path.relative_to(cwd)),
                "stage": stage,
                "content": content,
                "exists": True,
            }
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def get_pre_tool_snapshot(cwd: str | Path | None, tool_use_id: str, *, consume=False) -> list[dict] | None:
    """Safely retrieve true pre-tool file snapshot list captured by PreToolUse hook."""
    if not tool_use_id:
        return None
    storage_dir = get_hook_storage(cwd or os.getcwd(), create=False)
    if not (storage_dir / "state.json").is_file():
        return None
    with hook_state_lock(storage_dir):
        state = read_hook_state(storage_dir)
        tool_data = state.get("tools", {}).get(tool_use_id)
        if tool_data:
            stored = tool_data.get("before_snapshot")
            if consume:
                state["tools"].pop(tool_use_id, None)
                write_hook_state(storage_dir, state)
            if isinstance(stored, list):
                return stored
    return None

def emit_events(config_file: str | Path | None, events: list[dict], project_root: str | Path | None = None) -> int:
    """Deliver a batch of events to the Observer server for the given configuration."""
    if not events:
        return 0
    try:
        from .observer_client import ObserverClient
        client = ObserverClient(config_file=str(config_file) if config_file else None, project_root=None if config_file else project_root, timeout=0.5, environment={})
        delivered = 0
        for ev in events:
            if client.emit(ev):
                delivered += 1
        return delivered
    except Exception:
        return 0


def handle_hook(payload: dict, hook_name: str | None = None, config_file: str | Path | None = None) -> dict:
    """Process a single hook payload, persist paired state, and emit AGENT_EVENT and normalized graph events."""
    try:
        session_id = payload.get("session_id") or "unknown_session"
        cwd_str = payload.get("cwd") or os.getcwd()
        cwd = Path(cwd_str).resolve()
        from .observer_config import load_settings
        observer_root = load_settings(config_file, environment={})["project_root"] if config_file else cwd
        event_name = hook_name or payload.get("hook_event_name") or "UnknownHook"
        model = payload.get("model")
        turn_id = payload.get("turn_id")
        run_id = f"codex:{session_id}:{turn_id}" if turn_id else f"codex:{session_id}"
        timestamp = datetime.now(timezone.utc).isoformat()
        event_id = str(uuid.uuid4())

        storage_dir = get_hook_storage(observer_root)
        sanitized = sanitize_payload(payload)

        # PreToolUse always records the true before-snapshot in local locked state
        # regardless of stream mode, so app-server stream or PostToolUse has the baseline.
        before_snap = None
        if event_name == "PreToolUse":
            tool_name = payload.get("tool_name", "unknown_tool")
            tool_use_id = payload.get("tool_use_id") or str(uuid.uuid4())
            tool_input = payload.get("tool_input", {})
            before_snap = [snapshot for path in _extract_candidate_file_paths(tool_name, tool_input, cwd, observer_root) if (snapshot := _capture_file_snapshot(path, observer_root))]

            with hook_state_lock(storage_dir):
                state = read_hook_state(storage_dir)
                tools_state = state.setdefault("tools", {})
                tools_state[tool_use_id] = {
                    "tool_name": tool_name,
                    "start_time": time.time(),
                    "before_snapshot": before_snap,
                }
                if len(tools_state) > 500:
                    oldest = sorted(tools_state.keys(), key=lambda k: tools_state[k].get("start_time", 0))[:200]
                    for k in oldest:
                        tools_state.pop(k, None)
                write_hook_state(storage_dir, state)
            if before_snap:
                sanitized["before_snapshot"] = before_snap

        # 1. Base raw AGENT_EVENT for every hook
        agent_event = {
            "type": "AGENT_EVENT",
            "application": "Codex",
            "source": "codex",
            "session_id": session_id,
            "run_id": run_id if turn_id else None,
            "event_id": event_id,
            "agent_event": {
                "name": event_name,
                "direction": "hook",
                "data": sanitized,
            },
            "timestamp": timestamp,
        }

        events_to_emit = [agent_event]
        stream_active = (os.environ.get("AGENT_OBSERVER_CODEX_CAPTURE") == "stream")

        # 2. Normalized semantic events only when NOT in transparent stream mode
        if not stream_active:
            if event_name == "SessionStart":
                if model:
                    events_to_emit.append({
                        "type": "MODEL_INFO",
                        "application": "Codex",
                        "source": "codex",
                        "session_id": session_id,
                        "run_id": run_id,
                        "model": str(model)[:240],
                        "timestamp": timestamp,
                    })
                from .capabilities import discover_capabilities
                events_to_emit.append({
                    "type": "RUNTIME_INVENTORY", "application": "Codex", "source": "codex",
                    "session_id": session_id, "run_id": run_id, "timestamp": timestamp,
                    "inventory": discover_capabilities(cwd, config_file, agent="codex"),
                })

            elif event_name == "UserPromptSubmit":
                prompt_text = payload.get("prompt") or ""
                events_to_emit.append({
                    "type": "PROMPT_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": session_id,
                    "run_id": run_id,
                    "prompt": prompt_text,
                    "project": cwd.name,
                    "timestamp": timestamp,
                })
                if model:
                    events_to_emit.append({
                        "type": "MODEL_INFO",
                        "application": "Codex",
                        "source": "codex",
                        "session_id": session_id,
                        "run_id": run_id,
                        "model": str(model)[:240],
                        "timestamp": timestamp,
                    })

            elif event_name == "PreToolUse":
                tool_name = payload.get("tool_name", "unknown_tool")
                tool_use_id = payload.get("tool_use_id") or str(uuid.uuid4())
                tool_input = payload.get("tool_input", {})
                is_native = not tool_name.startswith("mcp__")

                try:
                    input_text = json.dumps(tool_input, ensure_ascii=False) if isinstance(tool_input, (dict, list)) else str(tool_input)
                except Exception:
                    input_text = "[Arguments omitted: not serializable]"

                tool_start_ev = {
                    "type": "TOOL_CALL_START",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": session_id,
                    "run_id": run_id,
                    "call_id": tool_use_id,
                    "tool": tool_name,
                    "native_tool": is_native,
                    "input": input_text,
                    "timestamp": timestamp,
                }
                if before_snap:
                    tool_start_ev["file_snapshots"] = before_snap
                events_to_emit.append(tool_start_ev)
            elif event_name == "PostToolUse":
                tool_name = payload.get("tool_name", "unknown_tool")
                tool_use_id = payload.get("tool_use_id") or "unknown_call"
                tool_response = payload.get("tool_response", "")

                before_snap = None
                duration_ms = 0.0
                with hook_state_lock(storage_dir):
                    state = read_hook_state(storage_dir)
                    stored = state.get("tools", {}).pop(tool_use_id, None)
                    if stored:
                        before_snap = stored.get("before_snapshot")
                        start_time = stored.get("start_time")
                        if start_time:
                            duration_ms = max(0.0, (time.time() - start_time) * 1000.0)
                        write_hook_state(storage_dir, state)

                snapshots = before_snap if isinstance(before_snap, list) else []
                after_snap = [snapshot for before in snapshots if (snapshot := _capture_file_snapshot((observer_root / before["path"]).resolve(), observer_root, stage="after"))]

                try:
                    result_text = json.dumps(tool_response, ensure_ascii=False) if isinstance(tool_response, (dict, list)) else str(tool_response)
                except Exception:
                    result_text = "[Result omitted: not serializable]"

                is_native = not tool_name.startswith("mcp__")
                tool_end_ev = {
                    "type": "TOOL_CALL_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": session_id,
                    "run_id": run_id,
                    "call_id": tool_use_id,
                    "tool": tool_name,
                    "native_tool": is_native,
                    "success": payload.get("success", str(payload.get("status", "")).lower() not in {"failed", "error", "failure"} and not payload.get("error")),
                    "result": result_text[:65536] + ("\n[truncated]" if len(result_text) > 65536 else ""),
                    "duration_ms": duration_ms,
                    "timestamp": timestamp,
                }
                snaps = snapshots + after_snap
                if snaps:
                    tool_end_ev["file_snapshots"] = snaps
                events_to_emit.append(tool_end_ev)

            elif event_name == "PostModelRequest":
                if model:
                    events_to_emit.append({
                        "type": "MODEL_INFO",
                        "application": "Codex",
                        "source": "codex",
                        "session_id": session_id,
                        "run_id": run_id,
                        "model": str(model)[:240],
                        "timestamp": timestamp,
                    })
                usage = payload.get("token_usage")
                if isinstance(usage, dict):
                    in_tok = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                    out_tok = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
                    events_to_emit.append({
                        "type": "THINKING",
                        "application": "Codex",
                        "source": "codex",
                        "session_id": session_id,
                        "run_id": run_id,
                        "usage_kind": "token_usage",
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "timestamp": timestamp,
                    })

            elif event_name == "Stop":
                events_to_emit.append({
                    "type": "GENERATION_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": session_id,
                    "run_id": run_id,
                    "status": "completed",
                    "success": True,
                    "result": str(payload.get("last_assistant_message") or "Turn completed")[:4000],
                    "timestamp": timestamp,
                })

            elif event_name == "SessionEnd":
                events_to_emit.append({
                    "type": "GENERATION_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": session_id,
                    "run_id": run_id,
                    "status": "completed",
                    "success": True,
                    "result": "Session ended",
                    "timestamp": timestamp,
                })

            elif event_name == "Interrupt":
                events_to_emit.append({
                    "type": "GENERATION_END",
                    "application": "Codex",
                    "source": "codex",
                    "session_id": session_id,
                    "run_id": run_id,
                    "status": "interrupted",
                    "success": False,
                    "result": "Turn interrupted by user",
                    "timestamp": timestamp,
                })

        # 3. Delivery
        cfg = config_file
        if not cfg:
            cand = cwd / ".agent-observer/config/observer.project.json"
            if cand.is_file():
                cfg = cand
        delivered = emit_events(cfg, events_to_emit, project_root=observer_root)
        update_hook_diagnostics(
            storage_dir,
            last_event_at=timestamp,
            last_event_name=event_name,
            last_delivered_count=delivered,
            total_events_in_batch=len(events_to_emit),
        )
    except Exception as exc:
        try:
            update_hook_diagnostics(get_hook_storage(os.getcwd()), last_error=str(exc))
        except Exception:
            pass

    # Never output instructions or arbitrary text; Stop and SubagentStop require empty JSON object
    return {}


def is_codex_hooks_installed(project: Path) -> bool:
    """Check if project .codex/hooks.json has Observer lifecycle handlers."""
    hooks_file = project / ".codex/hooks.json"
    if not hooks_file.is_file():
        return False
    try:
        data = _read_hooks(project)
        hooks_dict = data.get("hooks", {})
        for event_name, groups in hooks_dict.items():
            if isinstance(groups, list):
                for grp in groups:
                    for h in grp.get("hooks", []):
                        cmd = h.get("command", "")
                        if _owned_hook(h, project):
                            return True
    except (OSError, ValueError):
        pass
    return False


def inspect_codex_hooks(project: Path) -> dict:
    """Inspect configured Codex hooks in project."""
    hooks_file = project / ".codex/hooks.json"
    if not hooks_file.is_file():
        return {"installed": False, "all_14": False, "events": [], "unowned_events": [], "path": str(hooks_file)}
    try:
        data = _read_hooks(project)
        hooks_dict = data.get("hooks", {})
        owned_events = []
        unowned_events = []
        for ev, groups in hooks_dict.items():
            if isinstance(groups, list):
                has_owned = False
                has_unowned = False
                for grp in groups:
                    for h in grp.get("hooks", []):
                        cmd = h.get("command", "")
                        if _owned_hook(h, project):
                            has_owned = True
                        else:
                            has_unowned = True
                if has_owned:
                    owned_events.append(ev)
                if has_unowned:
                    unowned_events.append(ev)
        return {
            "installed": len(owned_events) > 0,
            "all_14": all(ev in owned_events for ev in HOOK_EVENTS),
            "events": sorted(owned_events),
            "unowned_events": sorted(unowned_events),
            "path": str(hooks_file),
        }
    except (OSError, ValueError):
        return {"installed": False, "all_14": False, "events": [], "unowned_events": [], "path": str(hooks_file), "error": "Malformed JSON"}


def _read_hooks(project: Path) -> dict:
    codex_dir = project / ".codex"
    hooks_file = codex_dir / "hooks.json"
    if codex_dir.is_symlink() or hooks_file.is_symlink():
        raise ValueError("Refusing symlinked Codex hook configuration.")
    if codex_dir.exists() and not codex_dir.is_dir():
        raise ValueError("The .codex path must be a directory.")
    if hooks_file.is_file() and hooks_file.stat().st_size > 1024 * 1024:
        raise ValueError("Codex hooks configuration exceeds the one-megabyte limit.")
    data = json.loads(hooks_file.read_text(encoding="utf-8")) if hooks_file.exists() else {"hooks": {}}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("Codex hooks must contain a hooks object.")
    for groups in data.get("hooks", {}).values():
        if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get("hooks"), list) or any(not isinstance(handler, dict) for handler in group["hooks"]) for group in groups):
            raise ValueError("Codex hook groups must contain handler lists.")
    data.setdefault("hooks", {})
    return data

def _owned_hook(handler: dict, project: Path) -> bool:
    if handler.get("statusMessage") != "Agent Observer":
        return False
    try:
        command = shlex.split(handler.get("command", ""))
        shim = Path(command[1]).resolve()
        return len(command) >= 4 and shim.is_relative_to(project.resolve()) and shim.parts[-3:] == ("integrations", "codex", "observer.py") and command[2] == "--event"
    except (ValueError, TypeError, IndexError):
        return False

def _write_hooks(project: Path, data: dict):
    directory = project / ".codex"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    try:
        temporary.replace(directory / "hooks.json")
    finally:
        temporary.unlink(missing_ok=True)

def install_codex_hooks(project: Path, target: Path, shim_path: Path | None = None) -> None:
    """Register all lifecycle hooks while preserving every unowned handler."""
    data = _read_hooks(project)
    shim = shim_path or (target / "integrations/codex/observer.py")
    shim_cmd = shlex.join([sys.executable, str(shim)])
    config_option = " --config " + shlex.quote(str(target / "config/observer.project.json"))

    for event in HOOK_EVENTS:
        groups = data["hooks"].setdefault(event, [])
        # Check if an Observer-owned handler already exists
        found_handler = None
        for grp in groups:
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                if _owned_hook(h, project):
                    found_handler = h
                    break
            if found_handler:
                break

        if found_handler is not None:
            found_handler["type"] = "command"
            found_handler["command"] = f"{shim_cmd} --event {event}{config_option}"
            found_handler["statusMessage"] = "Agent Observer"
        else:
            groups.append({
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{shim_cmd} --event {event}{config_option}",
                        "statusMessage": "Agent Observer",
                    }
                ]
            })

    _write_hooks(project, data)


def remove_codex_hooks(project: Path) -> None:
    """Safely remove ONLY Observer-owned hook handlers from project .codex/hooks.json."""
    hooks_file = project / ".codex/hooks.json"
    if not hooks_file.is_file():
        return
    data = _read_hooks(project)

    hooks_dict = data["hooks"]
    events_to_clean = list(hooks_dict.keys())

    for event in events_to_clean:
        groups = hooks_dict.get(event, [])
        if not isinstance(groups, list):
            continue
        cleaned_groups = []
        for grp in groups:
            handlers = grp.get("hooks", [])
            remaining = [
                h for h in handlers
                if not _owned_hook(h, project)
            ]
            if remaining or not any(_owned_hook(handler, project) for handler in handlers):
                grp_copy = dict(grp)
                grp_copy["hooks"] = remaining
                cleaned_groups.append(grp_copy)
        if cleaned_groups:
            hooks_dict[event] = cleaned_groups
        else:
            del hooks_dict[event]

    _write_hooks(project, data)


def main():
    """Command entry point executed by Codex hooks."""
    import argparse
    parser = argparse.ArgumentParser(description="Agent Observer Codex lifecycle hook adapter")
    parser.add_argument("--event", default=None, help="Codex lifecycle hook name")
    parser.add_argument("--config", default=None, help="Explicit observer config file")
    args, _ = parser.parse_known_args()

    raw_input = ""
    try:
        raw_input = sys.stdin.read()
    except Exception:
        pass

    payload = {}
    if raw_input.strip():
        try:
            payload = json.loads(raw_input)
        except Exception:
            pass

    out = handle_hook(payload, hook_name=args.event, config_file=args.config)
    # Output valid empty JSON
    sys.stdout.write(json.dumps(out or {}) + "\n")
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
