"""Project folders and observation modules; never configure an agent's model."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import cli
from .connection_analysis import compute_analysis_revision, run_analysis
from .installation import (DASHBOARD_RESTART_REQUIRED, InstallationError, InstallationManager,
                           _calculate_revision, _installation_target, _validate_project_path)
from .observer_config import _config_lock, load_settings, project_profile, refresh_settings



def workspace_id(project):
    return hashlib.sha256(str(project).encode()).hexdigest()[:16]


def connection_revision(project, target, config_path):
    revision = hashlib.sha256(_calculate_revision(project, target).encode())
    path = Path(config_path)
    if path.is_file():
        revision.update(path.read_bytes())
    return revision.hexdigest()[:20]


class ConnectionModules:
    def __init__(self, settings, installations=None):
        self.settings = settings
        self.installations = installations or InstallationManager(settings)
        self.root = Path(settings["project_root"]).resolve()
        self.lock = threading.RLock()
        self.observer = None
        self.emit = None
        self._observer_module = None
        self.last_project_event = None
        self._analyses = {}
    def _configuration(self, project):
        target = _installation_target(project)
        if project == self.root:
            path = Path(self.settings["config_file"])
        else:
            path = target / "config/observer.project.json"
        data = cli.read_json(path, {})
        if not isinstance(data, dict):
            raise InstallationError(400, "Observer project settings must contain an object.")
        return target, path, data

    def _workspace(self, project, saved=None):
        target, path, config = self._configuration(project)
        profile = project_profile(project, config)
        selected = config.get("connection", {})
        module = selected.get("module")
        if not module or module not in {"project", "codex", "omp"}:
            module = "project" if profile != "generic" else (
                "codex" if config.get("agent_runtime") == "codex" else "project")
        repo_mode = selected.get("repository_mode")
        if not repo_mode or repo_mode not in {"agent_system", "working_repository"}:
            repo_mode = "working_repository" if module in {"codex", "omp"} else "agent_system"
        return {"id": workspace_id(project), "path": str(project),
                "name": str(config.get("project", {}).get("name") or project.name),
                "pinned": bool((saved or {}).get("pinned", False)),
                "current": project == self.root, "exists": project.is_dir(),
                "installed": path.is_file(), "module": module,
                "repository_mode": repo_mode,
                "profile": profile, "enabled": path.is_file() and selected.get("enabled", True),
                "revision": connection_revision(project, target, path),
                "port": self.settings["port"] if project == self.root else config.get("port")}

    def workspaces(self):
        registry = cli.registry()
        entries = {str(self.root): {"path": str(self.root)}}
        for item in registry.get("installations", []):
            if isinstance(item, dict) and isinstance(item.get("project"), str):
                entries[item["project"]] = {"path": item["project"]}
        for item in registry.get("workspaces", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                entries[item["path"]] = item
        rows = []
        for value, saved in entries.items():
            try:
                project = Path(value).expanduser().resolve()
                rows.append(self._workspace(project, saved))
            except (OSError, ValueError, InstallationError):
                continue
        return sorted(rows, key=lambda row: (not row["pinned"], not row["current"], row["name"].casefold()))

    def remember(self, body):
        project = _validate_project_path(body.get("project"))
        if "pinned" in body and not isinstance(body["pinned"], bool):
            raise InstallationError(400, "Pinned must be a boolean.")
        @cli.serialized
        def save():
            registry = cli.registry()
            rows = registry.setdefault("workspaces", [])
            item = next((row for row in rows if row.get("path") == str(project)), None)
            if item is None:
                item = {"path": str(project), "pinned": False}
                rows.append(item)
            if "pinned" in body:
                item["pinned"] = body["pinned"]
            cli.write_json(cli.REGISTRY, registry)
            return self._workspace(project, item)
        return save()

    @staticmethod
    def codex_availability():
        home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
        desktops = [Path("/Applications/ChatGPT.app"), Path("/Applications/Codex.app"),
                    Path.home() / "Applications/Codex.app"]
        desktop = next((path for path in desktops if
                        (path / "Contents/Resources/codex-cli").is_dir()), None)
        executable = None
        if desktop:
            candidate = desktop / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                executable = str(candidate)
        if not executable:
            from .codex_stream import resolve_codex_executable
            try:
                executable = str(resolve_codex_executable())
            except (OSError, ValueError):
                pass
        available = any(home.glob("state_*.sqlite"))
        return {"available": available, "home": str(home), "desktop": str(desktop) if desktop else None,
                "cli": executable, "transport": "Read-only Codex session journals"}

    def inspect(self, body, *, include_resources=True):
        project = _validate_project_path(body.get("project", str(self.root)))
        workspace = self._workspace(project)
        codex = self.codex_availability()
        active = workspace["current"] and workspace["enabled"]
        observing = self.observer.status() if self.observer and active else None
        profile = workspace["profile"]
        adapter = {"cad": "CAD Studio telemetry", "omp": "OMP extension",
                   "generic": "Agent event integration"}[profile]
        project_state = "ready" if profile == "cad" else "needs_integration"
        if profile == "omp" and (project / ".omp/extensions/agent-observer.ts").is_file():
            project_state = "ready"
        if active and workspace["module"] == "project" and self.last_project_event:
            project_state = "receiving"
        modules = [
            {"id": "project", "name": "Project agent", "profile": profile,
             "adapter": adapter, "state": project_state,
             "role": "agent_system", "role_label": "Agent system",
             "description": ("Embedded CAD Studio agent — Direct API or Harness through the app's configured router."
                             if profile == "cad" else "Agent built into this project, with its own API or model router."),
             "last_event": self.last_project_event if workspace["current"] else None},
            {"id": "codex", "name": "Codex Desktop / CLI", "state": (observing or {}).get("state", "available" if codex["available"] else "unavailable") if workspace["module"] == "codex" else "available" if codex["available"] else "unavailable",
             "role": "working_repository", "role_label": "Working repository",
             "description": "External Codex agent developing the selected project.",
             **codex, "observation": observing if workspace["module"] == "codex" else None},
        ]
        from .omp_observer import omp_home
        omp_available = (omp_home() / "sessions").is_dir()
        modules.append({"id": "omp", "name": "OMP", "role": "working_repository",
                        "role_label": "Working repository",
                        "state": (observing or {}).get("state", "available" if omp_available else "unavailable") if workspace["module"] == "omp" else "available" if omp_available else "unavailable",
                        "description": "Read-only OMP session journals, including explicitly added workspace directories. New records only; no model or memory changes.",
                        "transport": "Read-only OMP journals", "available": omp_available,
                        "observation": observing if workspace["module"] == "omp" else None})
        for module in modules:
            source_status = (observing or {}).get("sources", {}).get(module["id"])
            if source_status:
                module.update(state=source_status["state"], observation=source_status)
        from .capabilities import discover_capabilities
        inventory = discover_capabilities(project, agent=workspace["module"] if workspace["module"] in {"codex", "omp"} else profile) if include_resources else {}
        document_roots = [name for name in ("docs", "ObsidianVault") if (project / name).is_dir()]
        return {"workspace": workspace, "modules": modules,
                "resources": {"documents": document_roots, "mcp": len(inventory.get("mcp", [])),
                              "skills": len(inventory.get("skills", [])),
                              "note": "Discovered resources are not evidence of use. Calls appear when events arrive."},
                "cli_command": shlex.join([sys.executable, str(cli.ROOT / "observer.py"), "connect", str(project), "--role", workspace["repository_mode"]]),
                "integration_prompt": (
                    f"Connect the existing agent in {project} to Agent Observer. "
                    f"Use {self.settings['config_file'] if project == self.root else project / '.agent-observer/config/observer.project.json'}. "
                    "Keep its current model, provider, router, credentials and approval policy unchanged. "
                    "For Codex, use the passive Desktop/CLI module without installing hooks or launching a replacement agent. "
                    "For a project agent, reuse its installed telemetry; otherwise instrument its real request/tool/result lifecycle "
                    "with the Observer SDK and examples/instrumented_agent.py. Include stable run/call IDs, explicit file paths, "
                    "published output, reported token usage and available runtime inventory. Do not infer usage from catalog files "
                    "or expose private reasoning/secrets. Exercise one real read operation and report any unobservable events."),
                "manual": "Emit the real request, tool and result lifecycle with the installed Observer client; never infer actions from catalog files."}

    def summary(self):
        return {"current_project": str(self.root), "workspaces": self.workspaces(),
                **self.inspect({"project": str(self.root)})}

    def status(self):
        return self.inspect({"project": str(self.root)}, include_resources=False)

    def start(self, emit):
        self.emit = emit
        self._activate()

    def _activate(self):
        with self.lock:
            selected = self._workspace(self.root)
            if (self.observer and selected["module"] in {"codex", "omp"} and self._observer_module == "repository" and selected["enabled"]
                    and self.observer.status().get("state") == "watching"):
                return
            if self.observer:
                self.observer.stop()
                self.observer = None
            config = self._configuration(self.root)[2]
            connection = config.get("connection", {})
            if selected["module"] in {"codex", "omp"} and connection.get("enabled", True) and self.emit:
                from .repository_observer import RepositoryObserver
                self.observer = RepositoryObserver(self.root, self.settings["history_file"].parent, self.emit)
                self.observer.start()
                self._observer_module = "repository"

    def connect(self, body):
        project = _validate_project_path(body.get("project"))
        module = body.get("module")
        repository_mode = body.get("repository_mode")
        if repository_mode is not None and repository_mode not in {"agent_system", "working_repository"}:
            raise InstallationError(400, "Repository role must be 'agent_system' or 'working_repository'.")
        if module is not None and module not in {"project", "codex", "omp"}:
            raise InstallationError(400, "Choose Project agent, Codex Desktop / CLI or OMP.")
        if repository_mode and module:
            if repository_mode == "agent_system" and module != "project":
                raise InstallationError(400, "Repository role 'agent_system' requires module 'project'.")
            if repository_mode == "working_repository" and module not in {"codex", "omp"}:
                raise InstallationError(400, "Repository role 'working_repository' requires Codex or OMP.")
        elif repository_mode:
            module = "project" if repository_mode == "agent_system" else "codex"
        elif module:
            repository_mode = "working_repository" if module in {"codex", "omp"} else "agent_system"
        else:
            raise InstallationError(400, "Choose a repository role or connection module.")
        target, config_path, data = self._configuration(project)
        if body.get("revision") != connection_revision(project, target, config_path):
            raise InstallationError(409, "Project settings changed. Refresh the connection before connecting.")
        if module == "codex" and not self.codex_availability()["available"]:
            raise InstallationError(409, "Codex has no readable session storage yet. Open Codex normally and create a project session first.")
        profile = project_profile(project, data)
        existed = config_path.is_file()
        if existed and project != self.root:
            status = self.installations._inspect_connection(project, data.get("port", 8766), target)
            if status.get("connected") and not status.get("dashboard_compatible"):
                raise InstallationError(409, DASHBOARD_RESTART_REQUIRED)
        hooks = project / ".codex/hooks.json"
        if hooks.is_file():
            try:
                if not isinstance(json.loads(hooks.read_text(encoding="utf-8")), dict):
                    raise ValueError("Expected hooks object")
            except (OSError, ValueError):
                raise InstallationError(409, "Codex hooks are malformed. Repair them before replacing the old integration.")
        if not config_path.is_file():
            args = argparse.Namespace(project=str(project), dest=".agent-observer", name=project.name,
                agent_label="CAD Agent" if profile == "cad" else "Agent", agent="generic",
                port=8766, profile="cad-studio" if profile == "cad" else "project",
                compat_cad=profile == "cad", adopt_existing=False, executable=None, documents=body.get("documents"))
            cli.install(args)
        # Installation safety/conflict checks precede any module setting changes.
        with _config_lock:
            if existed and body["revision"] != connection_revision(project, target, config_path):
                raise InstallationError(409, "Project settings changed during connection. Refresh and retry.")
            data = cli.read_json(config_path, {})
            data["connection"] = {"module": module, "repository_mode": repository_mode, "enabled": True}
            transport=getattr(self.installations,"transport",None)
            data["port"]=transport.primary.SETTINGS["port"] if transport and transport.primary else 8766
            data["agent_runtime"] = module if module in {"codex", "omp"} else profile
            if body.get("documents"):
                documents = data.setdefault("documents", [])
                for proposed in body["documents"]:
                    existing = next((source for source in documents if source.get("path") == proposed["path"]), None)
                    if existing is None:
                        documents.append(proposed)
                    else:
                        existing["include"] = list(dict.fromkeys([*existing.get("include", ["**/*.md"]), *proposed.get("include", [])]))
            cli.write_json(config_path, data)
        if module == "omp" or module == "project" and profile == "omp":
            cli.setup_omp_integration(project, target)
        # Remove only our obsolete hooks: passive Desktop/CLI capture needs no trust changes.
        from .codex_hooks import remove_codex_hooks
        if (project / ".codex/hooks.json").is_file():
            remove_codex_hooks(project)
        from .capabilities import discover_capabilities, save_capabilities_snapshot
        inventory = discover_capabilities(project, config_path)
        save_capabilities_snapshot(config_path.parent, inventory)
        self.remember({"project": str(project)})
        if project == self.root:
            refresh_settings(self.settings, config_path)
            self._activate()
            if module == "codex" and self.observer and self.observer.status()["state"] != "watching":
                raise InstallationError(503, self.observer.status().get("error") or "Codex observation could not attach.")
            return {"current": True, "url": f"http://127.0.0.1:{self.settings['port']}", **self.inspect({"project": str(project)})}
        start = self.installations.start_graph({"project": str(project), "revision": _calculate_revision(project, target)})
        self.forward_connect(project, module, repository_mode)
        return {"current": False, **start, **self.inspect({"project": str(project)})}


    def analyze(self, body):
        project = _validate_project_path(body.get("project"))
        repository_mode = body.get("repository_mode")
        if repository_mode is not None and repository_mode not in {"agent_system", "working_repository"}:
            raise InstallationError(400, "Repository role must be 'agent_system' or 'working_repository'.")
        if not repository_mode:
            workspace = self._workspace(project)
            repository_mode = workspace.get("repository_mode", "working_repository")

        module = body.get("module") or ("project" if repository_mode == "agent_system" else "codex")
        if module not in {"project", "codex", "omp"} or (module == "project") != (repository_mode == "agent_system"):
            raise InstallationError(400, "Choose the matching Project, Codex or OMP observation source.")
        target, config_path, _ = self._configuration(project)

        analysis_data = run_analysis(
            project=project,
            repository_mode=repository_mode,
            target=target,
            config_path=config_path,
            settings=self.settings,
            last_project_event=self.last_project_event if project == self.root else None,
            module=module,
        )

        analysis_id = f"ana_{hashlib.sha256(f'{project}:{repository_mode}:{time.time()}'.encode()).hexdigest()[:16]}"

        workspace = self._workspace(project)
        workspace["repository_mode"] = repository_mode
        workspace["module"] = module
        if project != self.root and workspace["installed"]:
            status = self.installations._inspect_connection(project, workspace.get("port") or 8766, target)
            if status.get("connected") and not status.get("dashboard_compatible"):
                analysis_data["ready"] = False
                analysis_data["checks"].append({"id": "dashboard_protocol", "name": "Graph server", "status": "error",
                                                "detail": DASHBOARD_RESTART_REQUIRED})
                analysis_data["logs"].append({"level": "error", "message": DASHBOARD_RESTART_REQUIRED})

        response = {
            "workspace": workspace,
            "repository_mode": repository_mode,
            "module": module,
            "revision": analysis_data["revision"],
            "analysis_id": analysis_id,
            "ready": analysis_data["ready"],
            "checks": analysis_data["checks"],
            "logs": analysis_data["logs"],
            "sessions": analysis_data["sessions"],
            "resources": analysis_data["resources"],
        }

        with self.lock:
            self._analyses[analysis_id] = response
            while len(self._analyses) > 32:
                self._analyses.pop(next(iter(self._analyses)))

        return response

    def analysis_revision(self, project, repository_mode, module=None):
        project = _validate_project_path(str(project))
        target, config_path, _ = self._configuration(project)
        return compute_analysis_revision(project, repository_mode, target, config_path, module=module)

    def generate(self, body):
        project = _validate_project_path(body.get("project"))
        repository_mode = body.get("repository_mode")
        if repository_mode not in {"agent_system", "working_repository"}:
            raise InstallationError(400, "Repository role must be 'agent_system' or 'working_repository'.")
        analysis_id = body.get("analysis_id")
        if not analysis_id or not isinstance(analysis_id, str):
            raise InstallationError(409, "Analysis ID is required to generate a connection.")

        with self.lock:
            analysis = self._analyses.get(analysis_id)
            if not analysis:
                raise InstallationError(409, "Analysis expired or not found. Run folder analysis before generating.")

            if Path(analysis["workspace"]["path"]).resolve() != project:
                raise InstallationError(409, "Analysis does not match the requested project folder.")

            if analysis.get("repository_mode") != repository_mode:
                raise InstallationError(409, "Analysis role does not match the requested repository role.")

            if not analysis.get("ready"):
                error_checks = [c["detail"] for c in analysis.get("checks", []) if c.get("status") == "error"]
                detail = error_checks[0] if error_checks else "Analysis has unresolved blocking issues."
                raise InstallationError(409, f"Folder analysis failed readiness: {detail}")

            target, config_path, _ = self._configuration(project)
            module = body.get("module") or ("project" if repository_mode == "agent_system" else "codex")
            if module != analysis.get("module"):
                raise InstallationError(409, "Observation source changed. Analyze the selected source again.")
            fresh_rev = self.analysis_revision(project, repository_mode, module=module)
            if analysis.get("revision") != fresh_rev:
                raise InstallationError(409, "Project files or configuration changed since analysis. Refresh analysis before generating.")
            if body.get("revision") != fresh_rev:
                raise InstallationError(409, "Project revision is stale. Refresh analysis before generating.")

            if module == "codex" and not self.codex_availability()["available"]:
                raise InstallationError(409, "Codex has no readable session storage yet. Open Codex normally and create a project session first.")
            if module == "codex":
                from .connection_analysis import discover_all_codex_sessions
                discovery = discover_all_codex_sessions(project)
                if not discovery["available"]:
                    raise InstallationError(409, "Codex session index is no longer readable. Analyze the folder again.")
            if repository_mode == "agent_system":
                profile = project_profile(project)
                if profile == "omp" and not (project / ".omp/extensions/agent-observer.ts").is_file():
                    raise InstallationError(409, "OMP agent-observer extension is missing. Integration required before generating.")

            proposed_docs = analysis.get("resources", {}).get("proposed_documents")

            module = analysis["module"]
            conn_rev = connection_revision(project, target, config_path)
            connect_payload = {
                "project": str(project),
                "module": module,
                "repository_mode": repository_mode,
                "revision": conn_rev,
            }
            if proposed_docs:
                connect_payload["documents"] = proposed_docs

            result = self.connect(connect_payload)
            result["analysis_id"] = analysis_id
            result["generated"] = True
            return result
    def disconnect(self, body):
        project = _validate_project_path(body.get("project"))
        target, path, data = self._configuration(project)
        if not path.is_file():
            raise InstallationError(404, "This folder has no connected Observer configuration.")
        if body.get("revision") != connection_revision(project, target, path):
            raise InstallationError(409, "Project settings changed. Refresh before disconnecting.")
        with _config_lock:
            data = cli.read_json(path, {})
            current_conn = data.get("connection", {})
            current_mod = current_conn.get("module", self._workspace(project)["module"])
            current_role = current_conn.get("repository_mode")
            if not current_role or current_role not in {"agent_system", "working_repository"}:
                current_role = "working_repository" if current_mod in {"codex", "omp"} else "agent_system"
            data["connection"] = {"module": current_mod, "repository_mode": current_role, "enabled": False}
            cli.write_json(path, data)
        if project == self.root:
            refresh_settings(self.settings, path)
            self._activate()
        return self.inspect({"project": str(project)})

    def forward_connect(self, project, module=None, repository_mode=None):
        """Activate a module in an already verified graph, not just its config file."""
        if repository_mode and not module:
            module = "project" if repository_mode == "agent_system" else "codex"
        elif module and not repository_mode:
            repository_mode = "working_repository" if module in {"codex", "omp"} else "agent_system"
        target, config_path, _ = self._configuration(project)
        transport = getattr(self.installations,"transport",None)
        if transport and transport.primary:
            body={"project":str(project),"module":module,"repository_mode":repository_mode,
                  "revision":connection_revision(project,target,config_path)}
            status,_,raw=transport.request(str(project),"/api/connections/connect","POST",body)
            response=json.loads(raw)
            if status>=400:raise InstallationError(status,response.get("detail","The selected project rejected the connection."))
            return response
        settings = load_settings(config_path, project_root=project, environment={})
        token = settings["token_file"].read_text(encoding="utf-8").strip()
        payload = json.dumps({"project": str(project), "module": module, "repository_mode": repository_mode,
                              "revision": connection_revision(project, target, config_path)}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{settings['port']}/api/connections/connect",
            data=payload, headers={"Content-Type": "application/json", "X-Agent-Observer-Token": token})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise InstallationError(409, "This project graph is running an older server. Restart its xray command to load connection modules.")
            raise InstallationError(error.code, "The project graph could not activate this connection. Refresh its settings.")

    def reveal(self, body):
        project = _validate_project_path(body.get("project"))
        if str(project) not in {row["path"] for row in self.workspaces()}:
            raise InstallationError(404, "Save this project folder before revealing it.")
        if sys.platform != "darwin":
            raise InstallationError(501, "Native folder reveal is available on macOS.")
        subprocess.run(["/usr/bin/open", str(project)], check=True, timeout=5,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"opened": True}

    def replay(self, body):
        if Path(body.get("project", "")).expanduser().resolve() != self.root or not self.observer:
            raise InstallationError(409, "Connect Codex for the current project before loading recorded activity.")
        source = "omp" if str(body.get("session_id", "")).startswith("omp:") else self.settings.get("connection", {}).get("module", "codex")
        return self.observer.replay_latest(source=source)

    def observe(self, event):
        if str(event.get("application", "")).casefold() != "codex" and event.get("source") not in {"demo", "legacy"}:
            self.last_project_event = {"received_at": datetime.now(timezone.utc).isoformat(),
                                       "application": str(event.get("application") or self.settings["agent_label"]),
                                       "type": str(event.get("type", ""))}

    def shutdown(self):
        with self.lock:
            if self.observer:
                self.observer.stop()
                self.observer = None
