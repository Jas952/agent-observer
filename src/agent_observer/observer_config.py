"""Project configuration and explicit resource inventory for Agent Observer."""
import hashlib
import json
import os
from pathlib import Path
import yaml

VERSION = "0.3.0"
DEFAULT_DOCUMENTS = [{"path": "docs", "include": ["**/*.md"]}, {"path": ".", "include": ["*.md"]}]

def load_settings(config_file=None, project_root=None):
    config = Path(config_file or os.environ.get("AGENT_OBSERVER_CONFIG", Path(__file__).resolve().parents[2] / "config/observer.project.json")).expanduser().resolve()
    if (config_file or os.environ.get("AGENT_OBSERVER_CONFIG")) and not config.is_file(): raise ValueError(f"Observer configuration not found: {config}")
    data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    if not isinstance(data, dict) or data.get("schema_version", 1) != 1:
        raise ValueError("Expected an Agent Observer configuration with schema_version 1")
    project = data.get("project", {})
    base = Path(project_root or os.environ.get("AGENT_OBSERVER_PROJECT") or os.environ.get("CAD_AGENT_ROOT") or project.get("root", ".")).expanduser()
    if not base.is_absolute(): base = config.parent / base
    base = base.resolve()
    if not base.is_dir(): raise ValueError(f"Project directory does not exist: {base}")
    profile = data.get("storage_profile", "project")
    if profile not in {"project", "cad-studio"}: raise ValueError("storage_profile must be project or cad-studio")
    identity = hashlib.sha256(str(base).encode()).hexdigest()[:12]
    support = Path.home() / "Library/Application Support" if os.sys.platform == "darwin" else Path.home() / ".local/share"
    storage = support / "CADStudio" if profile == "cad-studio" else support / "AgentObserver" / identity
    port = int(os.environ.get("AGENT_OBSERVER_PORT") or data.get("port", 8765))
    if not 1024 <= port <= 65535: raise ValueError("Use a local port from 1024 through 65535")
    return {**data, "schema_version": 1, "config_file": str(config), "project_root": base,
            "project_name": str(project.get("name") or base.name), "agent_label": str(project.get("agent_label", "Agent")),
            "documents": data.get("documents", DEFAULT_DOCUMENTS), "tools": data.get("tools", []),
            "port": port, "storage": storage, "token_file": Path(os.environ.get("AGENT_OBSERVER_TOKEN_FILE") or os.environ.get("CAD_XRAY_TOKEN_FILE") or storage / "xray-token"),
            "history_file": Path(os.environ.get("AGENT_OBSERVER_HISTORY_FILE") or os.environ.get("CAD_XRAY_HISTORY_FILE") or storage / "xray-runs.json")}

def apply_environment(settings):
    # Legacy emitters use these names; keep the same authenticated wire protocol.
    os.environ["CAD_XRAY_TOKEN_FILE"] = str(settings["token_file"])
    os.environ["CAD_XRAY_HISTORY_FILE"] = str(settings["history_file"])
    os.environ["AGENT_XRAY_ENDPOINT"] = f"http://127.0.0.1:{settings['port']}/api/event"

def resource_catalog(settings):
    root = settings["project_root"]
    nodes, seen = [], set()
    excludes = {".git", ".venv", "node_modules", "__pycache__", ".observer-backups"}
    ids = set()
    for source in settings["documents"]:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str): raise ValueError("Document sources need a path")
        location = (root / source["path"]).resolve()
        if not location.is_relative_to(root): raise ValueError("Document sources must be inside the configured project")
        files = [location] if location.is_file() else (path for pattern in source.get("include", ["**/*.md"]) for path in location.glob(pattern))
        for path in sorted(files):
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not path.is_file(): continue
            relative = path.relative_to(root)
            if any(part in excludes for part in relative.parts) or relative.as_posix() in seen: continue
            seen.add(relative.as_posix())
            try: text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError): continue
            parts = text.splitlines()
            metadata = {}
            if parts and parts[0].strip() == "---":
                try:
                    end = next(i for i in range(1, len(parts)) if parts[i].strip() == "---")
                    metadata = yaml.safe_load("\n".join(parts[1:end])) or {}
                    if not isinstance(metadata, dict): metadata = {}
                except (StopIteration, yaml.YAMLError): pass
            parent = " / ".join(relative.parts[:-1])
            group = str(metadata.get("graph_group") or source.get("group") or parent)
            node_id = str(metadata.get("graph_id") or "resource_" + hashlib.sha1(relative.as_posix().encode()).hexdigest()[:12])
            if node_id in ids: raise ValueError(f"Duplicate resource ID: {node_id}")
            ids.add(node_id)
            nodes.append({"id": node_id, "kind": "document", "label": relative.as_posix(),
                "path": relative.as_posix(), "group": group, "category": str(metadata.get("graph_category") or "Document"),
                "description": str(metadata.get("description") or source.get("description") or "Project document."),
                "estimated_tokens": max(1, round(len(text) / 3.5))})
    tools = list(settings["tools"])
    tools_file = settings.get("tools_markdown")
    if tools_file:
        path = (root / tools_file).resolve()
        if not path.is_relative_to(root): raise ValueError("tools_markdown must be inside the project")
        if path.is_file():
            import re
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
                if match:
                    name, command, purpose = (part.strip().strip("`") for part in match.groups())
                    tools.append({"name": name, "description": f"{purpose} · {command}"})
    for tool in tools:
        name = str(tool.get("name", "")).strip()
        if not name: raise ValueError("Each tool needs a name")
        node_id = str(tool.get("id") or "tool_" + hashlib.sha1(name.encode()).hexdigest()[:12])
        if node_id in ids: raise ValueError(f"Duplicate resource ID: {node_id}")
        ids.add(node_id)
        nodes.append({"id": node_id, "label": name, "kind": "tool", "category": tool.get("category", "Tool"),
            "group": tool.get("group", "Tools & services"), "description": tool.get("description", "Configured tool; usage requires an observed event."), "match": tool.get("match", {})})
    return {"project_title": settings["project_name"], "project_root": str(root), "agent_label": settings["agent_label"],
            "observer_version": VERSION, "nodes": nodes, "agent_spec": [], "observed_only": True}
