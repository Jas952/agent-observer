"""Portable installation manager, preflight diagnostics, and native launch controls."""
from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import shlex
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

LOCAL_ROOT = Path(__file__).resolve().parents[2]
from .observer_config import project_profile

# Bump when dashboard assets/API requirements change incompatibly.
DASHBOARD_PROTOCOL = 14
DASHBOARD_RESTART_REQUIRED = "The running graph needs an Observer server restart after this dashboard update."


class InstallationError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = int(status)
        self.detail = str(detail)
        super().__init__(self.detail)


def _validate_project_path(path_str: str | None) -> Path:
    """Safely validate and normalize a target project path."""
    if not path_str or not isinstance(path_str, str) or not path_str.strip():
        raise InstallationError(400, "Project directory path must be provided.")
    try:
        project = Path(path_str).expanduser().resolve()
    except Exception as exc:
        raise InstallationError(400, f"Invalid project path: {exc}")

    if not project.is_dir():
        raise InstallationError(400, f"Project directory does not exist: {project}")
    if project == Path("/") or str(project) == "/":
        raise InstallationError(400, "Refusing filesystem root directory as an installation project.")
    if project == Path.home().resolve():
        raise InstallationError(400, "Refusing user home directory directly as an installation project.")
    return project

def _installation_target(project: Path, destination=".agent-observer") -> Path:
    if not isinstance(destination, str) or not destination.strip():
        raise InstallationError(400, "A relative installation directory is required.")
    relative = Path(destination)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise InstallationError(400, "Install in a subdirectory of the selected project.")
    target = project / relative
    for path in (target, *target.parents):
        if path == project:
            break
        if path.is_symlink():
            raise InstallationError(400, f"Refusing symlinked installation directory: {path}")
    return target.resolve()

def _installation_port(value):
    if isinstance(value, bool):
        raise InstallationError(400, "Choose a local port from 1024 through 65535.")
    try:
        port = int(value)
    except (ValueError, TypeError):
        raise InstallationError(400, "Choose a local port from 1024 through 65535.")
    if not 1024 <= port <= 65535:
        raise InstallationError(400, "Choose a local port from 1024 through 65535.")
    return port


def _calculate_revision(project: Path, target: Path) -> str:
    """Deterministic hash of current target configuration state to guard against concurrent edits."""
    hasher = hashlib.sha256()
    critical_files = [
        target / "observer.install.json",
        target / "config/observer.project.json",
        project / ".codex/hooks.json",
        project / ".codex/config.toml",
        project / ".omp/extensions/agent-observer.ts",
    ]
    for p in critical_files:
        if p.is_file():
            try:
                hasher.update(p.name.encode("utf-8"))
                hasher.update(p.read_bytes())
            except OSError:
                pass
        else:
            hasher.update(f"{p.name}:none".encode("utf-8"))
    return hasher.hexdigest()[:16]


def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except OSError:
        return False




def _probe_codex(executable: str | None = None) -> tuple[bool, str | None, str | None]:
    """Inspect Codex CLI availability and version without executing model calls."""
    try:
        from .codex_stream import resolve_codex_executable
        candidate = resolve_codex_executable(executable)
        result = subprocess.run([str(candidate), "--version"], capture_output=True, text=True, timeout=2)
        version = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0 and re.match(r"^codex(?:-cli)?\s+\d+\.\d+\.\d+", version):
            return True, version, str(candidate)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False, None, executable


class InstallationManager:
    """Persistent installation manager shared by CLI and Observer HTTP server."""

    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}
        self.lock = threading.RLock()

    def choose_folder(self) -> dict:
        """Native folder chooser dialog on macOS; constant script, safe cancellation."""
        if sys.platform == "darwin":
            script = 'POSIX path of (choose folder with prompt "Select Project Folder")'
            try:
                res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
                if res.returncode == 0:
                    raw = res.stdout.strip()
                    if raw and Path(raw).is_dir():
                        return {"path": str(Path(raw).resolve())}
            except Exception:
                pass
        return {"path": None}

    def list(self) -> dict:
        """List registered installations from registry without exposing private auth tokens."""
        from . import cli
        reg = cli.registry()
        entries = []
        for item in reg.get("installations", []):
            proj_str = item.get("project")
            target_str = item.get("target")
            if not proj_str or not target_str:
                continue
            proj_path = Path(proj_str)
            target_path = Path(target_str)
            installed = (target_path / "observer.install.json").is_file()
            cfg_path = target_path / "config/observer.project.json"
            cfg = cli.read_json(cfg_path, {}) if cfg_path.is_file() else {}
            port = cfg.get("port", 8766)
            listening = _is_port_listening(port)
            entries.append({
                "project": str(proj_path),
                "target": str(target_path),
                "name": cfg.get("project", {}).get("name") or proj_path.name,
                "agent": cfg.get("connection", {}).get("module") if cfg.get("connection", {}).get("module") in {"codex", "omp"} else project_profile(proj_path, cfg),
                "installed": installed,
                "port": port,
                "listening": listening,
            })
        return {"installations": entries}

    def _inspect_connection(self, project: Path, port: int, target: Path) -> dict:
        """Authenticate /api/config to verify if server on port actually belongs to this project."""
        transport = getattr(self,"transport",None)
        if transport and transport.primary and (target/"config/observer.project.json").is_file():
            return {"connected":True,"owner":"host","status":"running","dashboard_compatible":True}
        if not _is_port_listening(port):
            return {"connected": False, "owner": "none", "status": "stopped"}

        health_url = f"http://127.0.0.1:{port}/health"
        is_observer = False
        dashboard_compatible = False
        try:
            req = urllib.request.Request(health_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=0.6) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict) and data.get("service") == "agent-observer":
                        is_observer = True
                        dashboard_compatible = data.get("dashboard_protocol") == DASHBOARD_PROTOCOL
        except Exception:
            pass

        if not is_observer:
            return {"connected": False, "owner": "none", "status": "occupied"}

        from .observer_config import load_settings
        from .xray_auth import load_token
        cfg_file = target / "config/observer.project.json"
        if not cfg_file.is_file():
            return {"connected": False, "owner": "none", "status": "occupied"}

        token = None
        try:
            settings = load_settings(config_file=str(cfg_file), project_root=str(project), environment={})
            token = load_token(path=settings.get("token_file"))
        except Exception:
            token = None

        if token:
            try:
                cfg_url = f"http://127.0.0.1:{port}/api/config"
                req = urllib.request.Request(cfg_url, headers={"X-Agent-Observer-Token": token, "X-CAD-Token": token})
                with urllib.request.urlopen(req, timeout=0.6) as resp:
                    if resp.status == 200:
                        conf_data = json.loads(resp.read().decode("utf-8"))
                        conf_root = conf_data.get("project_root")
                        if conf_root and Path(conf_root).resolve() == project.resolve():
                            return {"connected": True, "owner": "external",
                                    "status": "running", "dashboard_compatible": dashboard_compatible}
            except Exception:
                pass

        return {"connected": False, "owner": "none", "status": "occupied"}

    def analyze(self, body: dict) -> dict:
        """Compute an installation plan, file changes, diagnostics, and capabilities."""
        from . import cli
        project = _validate_project_path(body.get("project"))
        target = _installation_target(project, body.get("dest", ".agent-observer"))
        installed = (target / "observer.install.json").is_file() and (target / "config/observer.project.json").is_file()

        # Agent detection
        detected_agents = [
            {"id": "codex", "name": "Codex", "detected": (project / ".codex").is_dir()},
            {"id": "omp", "name": "OMP", "detected": (project / ".omp").is_dir()},
            {"id": "cad", "name": "CAD Studio", "detected": (project / "scripts/xray_server.py").is_file() or (project / "ObsidianVault").is_dir()},
            {"id": "generic", "name": "Generic Agent", "detected": True},
        ]
        agent = body.get("agent")
        if not agent:
            cfg = cli.read_json(target / "config/observer.project.json", {})
            profile = project_profile(project, cfg)
            selected = cfg.get("connection", {}).get("module")
            if selected in {"codex", "omp"}:
                agent = selected
            elif selected == "project" or profile != "generic":
                agent = profile
            else:
                agent = "codex" if cfg.get("agent_runtime") == "codex" or (project / ".codex").is_dir() else "generic"
        if agent not in {"codex", "omp", "cad", "generic"}:
            raise InstallationError(400, "Choose Codex, OMP, CAD, or a generic agent.")

        configured_port = body.get("port")
        if configured_port is not None:
            port = _installation_port(configured_port)
        else:
            port = 8766

        url = f"http://127.0.0.1:{port}"
        revision = _calculate_revision(project, target)

        # File changes calculation
        previous = cli.read_json(target / "observer.install.json", {}).get("files", {})
        planning_error = None
        try:
            raw_changes = cli.plan(target, previous, adopt=False)
            changes = []
            for src, dest in raw_changes:
                action = "update" if dest.exists() else "create"
                changes.append({
                    "path": str(dest.relative_to(project)),
                    "action": action,
                    "description": f"{action.capitalize()} Observer shared file",
                })
        except ValueError as exc:
            changes = []
            planning_error = str(exc)

        cfg_file = target / "config/observer.project.json"
        existing_config = cli.read_json(cfg_file, {}) if cfg_file.is_file() else {}
        if not isinstance(existing_config, dict):
            raise InstallationError(400, "Project configuration must contain a JSON object.")
        if not cfg_file.exists():
            changes.append({
                "path": str(cfg_file.relative_to(project)),
                "action": "create",
                "description": "Create project configuration",
            })
        elif ((body.get("port") is not None and port != existing_config.get("port"))
              or (body.get("documents") is not None and body["documents"] != existing_config.get("documents"))
              or (agent == "codex" and existing_config.get("agent_runtime") != "codex")
              or (body.get("executable") and body["executable"] != existing_config.get("codex", {}).get("executable"))):
            changes.append({"path": str(cfg_file.relative_to(project)), "action": "update", "description": "Update selected project integration settings"})

        # Codex hook changes
        from .codex_hooks import inspect_codex_hooks
        hooks_info = inspect_codex_hooks(project)
        if agent == "codex":
            hooks_file = project / ".codex/hooks.json"
            if not hooks_info.get("all_14"):
                action = "update" if hooks_file.is_file() else "create"
                changes.append({
                    "path": ".codex/hooks.json",
                    "action": action,
                    "description": "Register 14 Codex lifecycle hooks",
                })

        # Documents
        documents = body.get("documents")
        if documents is None:
            existing_config = cli.read_json(cfg_file, {}) if cfg_file.is_file() else {}
            documents = existing_config.get("documents")
        if documents is None:
            documents = [{"path": "docs", "include": ["**/*.md"]}, {"path": ".", "include": ["*.md"]}]
            if (project / "ObsidianVault").is_dir():
                documents.append({"path": "ObsidianVault", "include": ["**/*.md"]})
        if not isinstance(documents, list) or any(not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("include"), list) or any(not isinstance(pattern, str) for pattern in item["include"]) for item in documents):
            raise InstallationError(400, "Document sources require path and include-pattern lists.")

        # Inspect Codex CLI
        explicit_exec = body.get("executable")
        # Repository settings are data, not permission to execute an arbitrary file during analysis.
        codex_avail, codex_ver, codex_path = _probe_codex(explicit_exec) if agent == "codex" else (False, None, None)
        configured_exec = existing_config.get("codex", {}).get("executable")
        verification_required = bool(agent == "codex" and configured_exec and not explicit_exec and (not codex_path or Path(configured_exec).expanduser().resolve() != Path(codex_path).resolve()))
        if verification_required:
            codex_avail, codex_ver, codex_path = False, None, configured_exec

        # Connection status
        conn = self._inspect_connection(project, port, target)

        # Last event
        identity = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:12]
        support = Path.home() / "Library/Application Support" if sys.platform == "darwin" else Path.home() / ".local/share"
        var_diag = support / "AgentObserver" / identity / "hooks/diagnostics.json"
        last_event_at = None
        if var_diag.is_file():
            try:
                d = json.loads(var_diag.read_text(encoding="utf-8"))
                last_event_at = d.get("last_event_at") if d.get("last_delivered_count", 0) > 0 else None
            except Exception:
                pass
        if cfg_file.is_file():
            from .observer_config import load_settings
            from .agent_events import AgentEventJournal
            settings = load_settings(cfg_file, project_root=project, environment={})
            receipt = AgentEventJournal(settings["history_file"].parent / "agent-events.sqlite3").latest()
            if receipt:
                last_event_at = receipt["timestamp"]

        # Warnings
        warnings = []
        full_stream = False
        if agent == "codex" and codex_avail:
            try:
                help_result = subprocess.run([codex_path, "--help"], capture_output=True, text=True, timeout=3)
                full_stream = help_result.returncode == 0 and "--remote-auth-token-env" in help_result.stdout
            except (OSError, subprocess.TimeoutExpired):
                pass
        if agent == "codex":
            warnings.append(
                "Codex hooks observe local function tools, file edits, and lifecycle events. Hosted tools (such as WebSearch) do not trigger local tool hooks. For complete capture including hosted tools, launch Codex via 'observer.py codex <project>'."
            )
            if verification_required:
                warnings.append("The project's configured Codex executable is preserved but was not executed during analysis. Enter it explicitly in Advanced Options to verify it.")
            elif not codex_avail:
                warnings.append("Codex CLI was not found in standard locations. Specify its absolute executable path.")
            elif not full_stream:
                warnings.append("This Codex build lacks the authenticated remote client. Use a compatible CLI for full stream capture.")
        if conn["status"] == "occupied":
            warnings.append(f"Port {port} is occupied by an unverified or different service.")
        if planning_error:
            warnings.append(planning_error)

        # Diagnostics
        diagnostics = [
            {
                "id": "codex_cli",
                "label": "Codex Executable",
                "status": "ok" if codex_avail else "warning",
                "detail": f"Codex {codex_ver} ({codex_path})" if codex_avail else (f"Configured executable requires explicit verification: {codex_path}" if verification_required else f"Executable unavailable: {codex_path or 'not found'}"),
            },
            {
                "id": "hooks_config",
                "label": "Codex Hooks Configuration",
                "status": "ok" if hooks_info.get("all_14") else ("warning" if hooks_info.get("installed") else "unknown"),
                "detail": "All 14 hooks registered in .codex/hooks.json" if hooks_info.get("all_14") else (f"{len(hooks_info.get('events', []))}/14 hooks registered" if hooks_info.get("installed") else "No Observer hooks registered"),
            },
            {
                "id": "hook_trust",
                "label": "Codex Hook Trust",
                "status": "warning" if hooks_info.get("installed") else "unknown",
                "detail": "Codex requires interactive hook trust review (/hooks) before running non-managed command hooks." if hooks_info.get("installed") else "No hooks configured",
            },
            {
                "id": "server_port",
                "label": f"Server Port ({port})",
                "status": "ok" if conn["connected"] or conn["status"] == "stopped" else "error",
                "detail": "Observer server running and verified" if conn["connected"] else ("Port is free" if conn["status"] == "stopped" else f"Port {port} is occupied by another process"),
            },
            {
                "id": "telemetry",
                "label": "Recent Events",
                "status": "ok" if last_event_at else "unknown",
                "detail": f"Last event recorded at {last_event_at}" if last_event_at else "No events recorded yet",
            },
        ]
        if planning_error:
            diagnostics.append({"id": "shared_files", "label": "Local Installation Edits", "status": "error", "detail": planning_error})

        # Capabilities
        capabilities = [
            {"name": "Tool calls & MCP", "status": "available" if full_stream else "partial", "detail": "Native and MCP operations exposed by Codex; unknown source events remain visible in the event journal."},
            {"name": "File changes", "status": "available", "detail": "Explicit mutation paths and observed diffs. Before snapshots require trusted hooks or a verifiable source diff; script-internal I/O is not inferred."},
            {"name": "Model token usage", "status": "available", "detail": "Provider-reported usage when supplied by the runtime; no fabricated estimates."},
            {"name": "Agent lifecycle", "status": "available", "detail": "Protocol events plus 14 supplemental lifecycle hooks after Codex trust review."},
            {
                "name": "Hosted tools (WebSearch)",
                "status": "available" if full_stream else "partial",
                "detail": "Capture requires the Observer Codex launcher. A running graph alone does not prove that capture is attached.",
            },
        ]

        # Manual commands & project-specific prompt
        entry = str(LOCAL_ROOT / "observer.py")
        quote = shlex.quote
        executable_option = ["--executable", codex_path] if codex_path else []
        def command(*parts):
            return " ".join(quote(str(part)) for part in parts)
        manual_commands = [
            command(sys.executable, entry, "install", project, "--agent", agent, "--dest", body.get("dest", ".agent-observer"), "--port", port, *executable_option),
            command(sys.executable, entry, "run", "--config", cfg_file, "--project", project),
        ]
        if agent == "codex":
            manual_commands.append(command(sys.executable, entry, "codex", project, "--config", cfg_file, *executable_option))
        manual_commands.append(command(sys.executable, entry, "diagnose", project, *executable_option))
        sources = ", ".join(f"{item['path']} ({', '.join(item['include'])})" for item in documents) or "none selected"
        prompt = (
            f"Integrate Agent Observer into {project} for {agent}. Preserve existing agent, model/provider, approvals, hooks and project files.\n\n"
            + "\n".join(manual_commands)
            + f"\n\nDocument catalog sources: {sources}. Catalog presence is not evidence that a document was read. "
            "Record actual session, tool start/end, explicit file paths, errors, final results and runtime-provided usage; never invent events, reasoning, file reads or token counts. "
            "For Codex use the Observer Codex launcher for the complete exposed protocol stream; ordinary Codex sessions have supplemental hooks only, after /hooks trust review. "
            "For custom agents integrate examples/instrumented_agent.py with the real execution lifecycle. "
            f"Send telemetry only to http://127.0.0.1:{port}/api/event. Never log credentials or private reasoning. Do not add CAD-specific memory or services to unrelated agents."
        )

        can_install = planning_error is None and not hooks_info.get("error")
        can_start = installed and conn["status"] == "stopped"
        can_disconnect = installed and bool(hooks_info.get("installed"))

        return {
            "project": str(project),
            "agent": agent,
            "revision": revision,
            "installed": installed,
            "port": port,
            "url": url,
            "agents": detected_agents,
            "documents": documents,
            "changes": changes,
            "warnings": warnings,
            "manual_commands": manual_commands,
            "prompt": prompt,
            "coverage": {
                "mode": "stream" if full_stream else ("hooks" if agent == "codex" else "custom"),
                "full_stream": full_stream,
                "capabilities": capabilities,
                "detail": "Available through the Observer Codex launcher, not an assertion that an ordinary Codex session is being captured.",
            },
            "diagnostics": diagnostics,
            "codex": {
                "available": codex_avail,
                "version": codex_ver,
                "executable": codex_path,
                "verification_required": verification_required,
                "hook_trust": "review_required" if hooks_info.get("installed") else "unknown",
                "last_event_at": last_event_at,
            },
            "connection": conn,
            "can_install": can_install,
            "can_start": can_start,
            "can_disconnect": can_disconnect,
        }

    def install(self, body: dict) -> dict:
        """Safely execute installation plan, refusing stale revisions."""
        project = _validate_project_path(body.get("project"))
        target = _installation_target(project, body.get("dest", ".agent-observer"))

        revision = body.get("revision")
        if not revision or not isinstance(revision, str) or not revision.strip():
            raise InstallationError(400, "Missing required fresh revision; please analyze before installing.")
        current_rev = _calculate_revision(project, target)
        if revision != current_rev:
            raise InstallationError(409, "Target project configuration changed; please re-analyze before installing.")
        preview = self.analyze(body)
        if not preview["can_install"]:
            raise InstallationError(409, "Resolve installation conflicts before installing: " + "; ".join(preview["warnings"]))
        agent = preview["agent"]
        # Preflight check on hooks config before shared copy
        if agent == "codex":
            hooks_file = project / ".codex/hooks.json"
            if hooks_file.is_file():
                try:
                    json.loads(hooks_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise InstallationError(400, f"Malformed JSON in existing Codex hooks file {hooks_file}: {exc}")

        configured_port = body.get("port")
        if configured_port is not None:
            port = _installation_port(configured_port)
        else:
            port = 8766

        from . import cli
        args = argparse.Namespace(
            project=str(project),
            dest=body.get("dest", ".agent-observer"),
            name=body.get("name") or project.name,
            agent_label=body.get("agent_label") or ("Codex" if agent == "codex" else ("OMP" if agent == "omp" else "Agent")),
            agent=agent,
            port=port,
            profile="project",
            compat_cad=(agent == "cad"),
            adopt_existing=False,
            executable=body.get("executable"),
            documents=preview["documents"],
        )
        cli.install(args)
        if body.get("port") is not None:
            configuration = target / "config/observer.project.json"
            data = cli.read_json(configuration, {})
            data["port"] = port
            cli.write_json(configuration, data)

        # Return fresh analysis after install
        return self.analyze(body)

    def diagnose(self, project_or_body: str | dict) -> dict:
        """Perform read-only diagnostic inspection of a project."""
        if isinstance(project_or_body, str):
            body = {"project": project_or_body}
        elif isinstance(project_or_body, dict):
            body = project_or_body
        else:
            raise InstallationError(400, "Project path must be provided.")
        return self.analyze(body)

    def disconnect(self, body: dict) -> dict:
        """Safely remove ONLY Observer-owned hooks and integration shims from project."""
        project = _validate_project_path(body.get("project"))
        target = _installation_target(project, body.get("dest", ".agent-observer"))

        revision = body.get("revision")
        if not revision or not isinstance(revision, str) or not revision.strip():
            raise InstallationError(400, "Missing required fresh revision; please analyze before disconnecting.")
        current_rev = _calculate_revision(project, target)
        if revision != current_rev:
            raise InstallationError(409, "Target project configuration changed; please re-analyze before disconnecting.")
        agent = body.get("agent", "codex")
        if agent == "codex":
            from .codex_hooks import remove_codex_hooks
            remove_codex_hooks(project)
        elif agent == "omp":
            shim = project / ".omp/extensions/agent-observer.ts"
            if shim.is_file() and not shim.is_symlink() and shim.read_text(encoding="utf-8").startswith("// Agent Observer OMP extension shim\n// Managed by Agent Observer."):
                shim.unlink()
        else:
            raise InstallationError(400, "Only Observer-owned Codex hooks or the OMP extension can be disconnected.")

        return self.analyze(body)

    def start_graph(self, body: dict) -> dict:
        """Open an isolated project runtime on the existing shared Observer host."""
        project = _validate_project_path(body.get("project"))
        target = _installation_target(project, body.get("dest", ".agent-observer"))
        cfg_path = target / "config/observer.project.json"
        if not cfg_path.is_file():
            raise InstallationError(400, "Project is not installed. Please install Agent Observer first.")
        revision = body.get("revision")
        if not revision or not isinstance(revision, str) or not revision.strip():
            raise InstallationError(400, "Missing required fresh revision; please analyze before starting graph.")
        current_rev = _calculate_revision(project, target)
        if revision != current_rev:
            raise InstallationError(409, "Target project configuration changed; please re-analyze before starting graph.")

        transport = getattr(self,"transport",None)
        if transport and transport.primary:
            transport.ensure(str(project))
            port = transport.primary.SETTINGS["port"]
        else:
            from .host_runtime import start_host
            port = start_host(project,cfg_path)
        return {"url":f"http://127.0.0.1:{port}/?project={quote(str(project),safe='')}",
                "service":{"id":"observer-host","name":"Agent Observer","endpoint":f"http://127.0.0.1:{port}",
                           "status":"running","connected":True,"owner":"host"}}

