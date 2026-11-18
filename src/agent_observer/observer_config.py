"""Project configuration and explicit resource inventory for Agent Observer."""
import hashlib
import json
import os
import posixpath
from urllib.parse import unquote, urlsplit
from markdown_it import MarkdownIt
from pathlib import Path
import yaml
import threading
from .memory_systems import normalize_memory_systems, memory_node, public_system

_config_lock = threading.Lock()
VERSION = "0.3.0"
DEFAULT_DOCUMENTS = [{"path": "docs", "include": ["**/*.md"]}, {"path": ".", "include": ["*.md"]}]
DEFAULT_BLOCK_LAYOUTS = {"commands": "list", "tools": "list", "capabilities": "list", "files": "list", "memory": "list"}

def _normalize_capability_links(raw):
    if not isinstance(raw, dict):
        raise ValueError("'capability_links' must be an object.")
    result = {}
    for key, links in raw.items():
        kind, _, digest = key.partition("_") if isinstance(key, str) else ("", "", "")
        if kind not in {"mcp", "skill"} or len(digest) != 12 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("GitHub links require an MCP or skill resource ID.")
        if not isinstance(links, list) or any(not isinstance(link, str) for link in links):
            raise ValueError("Each capability's GitHub links must be a list of URL strings.")
        clean = []
        for link in links:
            url = link.strip()
            try:
                parsed = urlsplit(url)
                valid = (parsed.scheme == "https" and parsed.hostname in {"github.com", "www.github.com", "gist.github.com"}
                         and parsed.port in {None, 443} and parsed.username is None and parsed.password is None
                         and not any(char.isspace() for char in url))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Use HTTPS GitHub or GitHub Gist URLs without embedded credentials.")
            if url not in clean:
                clean.append(url)
        result[key] = clean
    return result

def project_profile(project, config=None):
    """Identify the outer agent, independently of its model provider."""
    project, config = Path(project), config or {}
    if (config.get("storage_profile") == "cad-studio"
            or (project / "src/CADAgentUI").is_dir()
            or (project / "scripts/xray_server.py").is_file()):
        return "cad"
    return "omp" if (project / ".omp").is_dir() else "generic"

def load_settings(config_file=None, project_root=None, *, environment=None):
    env = os.environ if environment is None else environment
    config = Path(config_file or env.get("AGENT_OBSERVER_CONFIG", Path(__file__).resolve().parents[2] / "config/observer.project.json")).expanduser().resolve()
    if (config_file or env.get("AGENT_OBSERVER_CONFIG")) and not config.is_file(): raise ValueError(f"Observer configuration not found: {config}")
    data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    if not isinstance(data, dict) or data.get("schema_version", 1) != 1:
        raise ValueError("Expected an Agent Observer configuration with schema_version 1")
    project = data.get("project", {})
    base = Path(project_root or env.get("AGENT_OBSERVER_PROJECT") or env.get("CAD_AGENT_ROOT") or project.get("root", ".")).expanduser()
    if not base.is_absolute(): base = config.parent / base
    base = base.resolve()
    if not base.is_dir(): raise ValueError(f"Project directory does not exist: {base}")
    profile = data.get("storage_profile", "project")
    if profile not in {"project", "cad-studio"}: raise ValueError("storage_profile must be project or cad-studio")
    identity = hashlib.sha256(str(base).encode()).hexdigest()[:12]
    support = Path.home() / "Library/Application Support" if os.sys.platform == "darwin" else Path.home() / ".local/share"
    storage = support / "CADStudio" if profile == "cad-studio" else support / "AgentObserver" / identity
    port = int(env.get("AGENT_OBSERVER_PORT") or data.get("port", 8766))
    if not 1024 <= port <= 65535: raise ValueError("Use a local port from 1024 through 65535")
    raw_bl = data.get("block_layouts")
    block_layouts = {k: (raw_bl.get(k) if isinstance(raw_bl, dict) and raw_bl.get(k) in {"list", "grid", "free"} else "list") for k in DEFAULT_BLOCK_LAYOUTS}
    block_layouts["memory"] = "list"
    from .capabilities import normalize_manual_capabilities
    manual_capabilities = normalize_manual_capabilities(data.get("capabilities"))
    connection = data.get("connection")
    if connection is None:
        module = "project" if project_profile(base, data) != "generic" else (
            "codex" if data.get("agent_runtime") == "codex" else "project")
        repo_mode = "working_repository" if module == "codex" else "agent_system"
        connection = {"module": module, "repository_mode": repo_mode, "enabled": True}
    if (not isinstance(connection, dict) or connection.get("module") not in {"project", "codex", "omp"}
            or not isinstance(connection.get("enabled", True), bool)):
        raise ValueError("connection requires a Project, Codex or OMP module and a boolean enabled state")
    repo_mode = connection.get("repository_mode")
    if not repo_mode:
        repo_mode = "working_repository" if connection.get("module") in {"codex", "omp"} else "agent_system"
    if repo_mode not in {"agent_system", "working_repository"}:
        raise ValueError("repository_mode must be agent_system or working_repository")
    if repo_mode == "agent_system" and connection.get("module") != "project":
        raise ValueError("repository_mode agent_system requires module project")
    if repo_mode == "working_repository" and connection.get("module") not in {"codex", "omp"}:
        raise ValueError("repository_mode working_repository requires module codex or omp")
    connection = {**connection, "repository_mode": repo_mode}
    runtime = connection["module"] if connection["module"] in {"codex", "omp"} else project_profile(base, data)
    return {**data, "schema_version": 1, "config_file": str(config), "project_root": base,
            "connection": dict(connection), "agent_runtime": runtime,
            "project_name": str(project.get("name") or base.name),
            "agent_label": "OMP" if connection.get("module") == "omp" else "Codex" if runtime == "codex" else str(project.get("agent_label", "Agent")),
            "documents": data.get("documents", DEFAULT_DOCUMENTS), "tools": data.get("tools", []),
            "tools_markdown": str(data.get("tools_markdown") or ""),
            "capabilities": manual_capabilities,
            "capability_links": _normalize_capability_links(data.get("capability_links", {})),
            "memory_systems": normalize_memory_systems(data.get("memory_systems", [])),
            "vault_path": str(data.get("vault_path") or ""),
            "commands": data.get("commands", {"hidden": []}) if isinstance(data.get("commands"), dict) else {"hidden": []},
            "hidden_commands": list((data.get("commands") or {}).get("hidden", [])) if isinstance(data.get("commands"), dict) else [],
            "hidden_blocks": [key for key in data.get("hidden_blocks", []) if key in {"documents", "memory", "commands", "tools", "files", "capabilities"}],
            "hidden_resources": list(data.get("hidden_resources", [])),
            "block_layouts": block_layouts,
            "port": port, "storage": storage, "token_file": Path(env.get("AGENT_OBSERVER_TOKEN_FILE") or env.get("CAD_XRAY_TOKEN_FILE") or storage / "xray-token"),
            "history_file": Path(env.get("AGENT_OBSERVER_HISTORY_FILE") or env.get("CAD_XRAY_HISTORY_FILE") or storage / "xray-runs.json")}

def refresh_settings(settings, config_file=None):
    """Reload project configuration without relocating the running listener or its data."""
    runtime = {key: settings[key] for key in ("port", "token_file", "history_file", "storage") if key in settings}
    fresh = load_settings(config_file or settings["config_file"], project_root=settings["project_root"], environment={})
    settings.update({**fresh, **runtime})
    return settings

def get_config_revision(config_file) -> str:
    cfg = Path(config_file)
    if cfg.is_file():
        try: return hashlib.sha256(cfg.read_bytes()).hexdigest()
        except OSError: pass
    return hashlib.sha256(b"{}").hexdigest()

def load_current_block_settings(settings: dict) -> dict:
    with _config_lock:
        config_path = Path(settings["config_file"])
        if config_path.is_file():
            try:
                refresh_settings(settings, config_path)
            except Exception:
                pass
        rev = get_config_revision(config_path)
        from .capabilities import normalize_manual_capabilities
        return {
            "revision": rev,
            "project_root": str(settings["project_root"]),
            "capabilities": normalize_manual_capabilities(settings.get("capabilities")),
            "capability_links": _normalize_capability_links(settings.get("capability_links", {})),
            "memory_systems": normalize_memory_systems(settings.get("memory_systems", [])),
            "documents": settings.get("documents", DEFAULT_DOCUMENTS),
            "vault_path": str(settings.get("vault_path") or ""),
            "tools": settings.get("tools", []),
            "tools_markdown": str(settings.get("tools_markdown") or ""),
            "hidden_commands": list((settings.get("commands") or {}).get("hidden", [])),
            "hidden_blocks": list(settings.get("hidden_blocks", [])),
            "hidden_resources": list(settings.get("hidden_resources", [])),
            "block_layouts": dict(settings.get("block_layouts", DEFAULT_BLOCK_LAYOUTS)),
        }
def normalize_project_relative(path_str: str, project_root: Path) -> str:
    p = Path(path_str).expanduser()
    resolved_root = project_root.resolve()
    candidate = (resolved_root / p).resolve() if not p.is_absolute() else p.resolve()
    if candidate.is_relative_to(resolved_root):
        rel = candidate.relative_to(resolved_root).as_posix()
        return rel if rel != "" else "."
    return posixpath.normpath(path_str.replace("\\", "/"))

def save_block_settings(settings: dict, updates: dict) -> dict:
    if not isinstance(updates, dict):
        raise ValueError("Expected a JSON object with configuration updates.")
    revision = updates.get("revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("The 'revision' string is required.")
    config_path = Path(settings["config_file"])
    with _config_lock:
        current_rev = get_config_revision(config_path)
        if revision != current_rev:
            from .documents import DocumentError
            raise DocumentError(409, "This configuration changed on disk. Reload settings before saving; draft kept.")
        raw_data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        if not isinstance(raw_data, dict): raw_data = {}
        project_root = settings["project_root"]
        candidate_data = dict(raw_data)
        if "hidden_blocks" in updates:
            hidden = updates["hidden_blocks"]
            if not isinstance(hidden, list) or any(key not in {"documents", "memory", "commands", "tools", "files", "capabilities"} for key in hidden):
                raise ValueError("Only optional graph blocks can be hidden.")
            candidate_data["hidden_blocks"] = sorted(set(hidden))
        if "hidden_resources" in updates:
            hidden = updates["hidden_resources"]
            required = {"node:request_prompt", "node:agent-hub", "node:run_result"}
            if (not isinstance(hidden, list) or len(hidden) > 10000
                    or any(not isinstance(key, str) or not key.startswith(("node:", "document:", "file:"))
                           or len(key) > 8192 or key in required for key in hidden)):
                raise ValueError("Only optional resource visibility keys can be hidden.")
            candidate_data["hidden_resources"] = sorted(set(hidden))

        # 1. Reject obsolete prompt_sources
        if "prompt_sources" in updates:
            raise ValueError("Manual 'prompt_sources' configuration is no longer supported.")

        # 2. Validate capabilities
        if "capabilities" in updates:
            raw_caps = updates["capabilities"]
            if not isinstance(raw_caps, dict):
                raise ValueError("'capabilities' must be an object.")
            from .capabilities import normalize_manual_capabilities
            clean_caps = normalize_manual_capabilities(raw_caps)
            if "mcp" in raw_caps:
                if not isinstance(raw_caps["mcp"], list):
                    raise ValueError("'capabilities.mcp' must be a list.")
                seen_m = set()
                for item in raw_caps["mcp"]:
                    if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                        raise ValueError("Each manual MCP server must have a non-empty 'name'.")
                    m_name = str(item.get("name", "")).strip().casefold()
                    if m_name in seen_m:
                        raise ValueError(f"Duplicate manual MCP server name: {item.get('name')}")
                    seen_m.add(m_name)
                    if "tool_names" in item:
                        if not isinstance(item["tool_names"], list) or any(not isinstance(t, str) or not t.strip() for t in item["tool_names"]):
                            raise ValueError("MCP tool_names must be a list of non-empty strings.")
                    if "description" in item and not isinstance(item["description"], str):
                        raise ValueError("MCP description must be a string.")
            if "skills" in raw_caps:
                if not isinstance(raw_caps["skills"], list):
                    raise ValueError("'capabilities.skills' must be a list.")
                seen_s = set()
                for item in raw_caps["skills"]:
                    if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                        raise ValueError("Each manual skill must have a non-empty 'name'.")
                    s_name = str(item.get("name", "")).strip().casefold()
                    if s_name in seen_s:
                        raise ValueError(f"Duplicate manual skill name: {item.get('name')}")
                    seen_s.add(s_name)
                    if "path" in item and not isinstance(item["path"], str):
                        raise ValueError("Skill path must be a string.")
                    if "description" in item and not isinstance(item["description"], str):
                        raise ValueError("Skill description must be a string.")
            candidate_data["capabilities"] = clean_caps
        if "memory_systems" in updates:
            raw_systems = updates["memory_systems"]
            if not isinstance(raw_systems, list):
                raise ValueError("'memory_systems' must be a list.")
            existing_systems = {
                s.get("id"): s for s in raw_data.get("memory_systems", [])
                if isinstance(s, dict) and isinstance(s.get("id"), str)
            }
            merged_systems = []
            for item in raw_systems:
                if not isinstance(item, dict):
                    raise ValueError("Each memory system must be an object.")
                clean_item = dict(item)
                item_id = clean_item.get("id")
                if item_id in existing_systems:
                    existing = existing_systems[item_id]
                    if "token_file" not in clean_item:
                        clean_item["token_file"] = existing.get("token_file", "")
                merged_systems.append(clean_item)
            candidate_data["memory_systems"] = normalize_memory_systems(merged_systems)
        if "capability_links" in updates:
            links = _normalize_capability_links(candidate_data.get("capability_links", {}))
            for key, urls in _normalize_capability_links(updates["capability_links"]).items():
                if urls:
                    links[key] = urls
                else:
                    links.pop(key, None)
            candidate_data["capability_links"] = links
        # 2. Validate documents
        if "documents" in updates:
            raw_docs = updates["documents"]
            if not isinstance(raw_docs, list): raise ValueError("'documents' must be a list.")
            norm_docs = []
            for item in raw_docs:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ValueError("Each document source must be an object with a 'path' string.")
                d_str = item["path"].strip()
                if not d_str: raise ValueError("Document source path cannot be empty.")
                norm_path = normalize_project_relative(d_str, project_root)
                d_loc = (project_root / norm_path).resolve()
                if not d_loc.is_relative_to(project_root):
                    raise ValueError(f"Document source must be inside project root: {item['path']}")
                if "include" in item:
                    if not isinstance(item["include"], list) or not all(isinstance(x, str) and x.strip() for x in item["include"]):
                        raise ValueError(f"Document include patterns must be a list of non-empty strings: {item['path']}")
                norm_docs.append({**item, "path": norm_path})
            candidate_data["documents"] = norm_docs

        # 3. Validate vault_path
        if "vault_path" in updates:
            v_raw = updates["vault_path"]
            if not isinstance(v_raw, str): raise ValueError("'vault_path' must be a string.")
            v_str = v_raw.strip()
            if not v_str:
                candidate_data["vault_path"] = ""
            else:
                v_dir = Path(v_str).expanduser().resolve()
                if not v_dir.is_dir(): raise ValueError(f"Vault directory does not exist: {v_str}")
                candidate_data["vault_path"] = str(v_dir)

        # 4. Validate tools
        if "tools" in updates:
            raw_tools = updates["tools"]
            if not isinstance(raw_tools, list): raise ValueError("'tools' must be a list.")
            seen_tool_names = set()
            for tool in raw_tools:
                if not isinstance(tool, dict) or not str(tool.get("name", "")).strip():
                    raise ValueError("Each tool must be an object with a non-empty 'name'.")
                t_name = str(tool.get("name", "")).strip().casefold()
                if t_name in seen_tool_names:
                    raise ValueError(f"Duplicate tool name: {tool.get('name')}")
                seen_tool_names.add(t_name)
                if "match" in tool:
                    m_obj = tool["match"]
                    if not isinstance(m_obj, dict):
                        raise ValueError(f"Tool {tool.get('name')!r} match must be an object.")
                    if "tool_names" in m_obj:
                        if not isinstance(m_obj["tool_names"], list) or not all(isinstance(x, str) and x.strip() for x in m_obj["tool_names"]):
                            raise ValueError(f"Tool {tool.get('name')!r} match.tool_names must be a list of non-empty strings.")
                    if "command_prefixes" in m_obj:
                        if not isinstance(m_obj["command_prefixes"], list) or not all(isinstance(x, str) and x.strip() for x in m_obj["command_prefixes"]):
                            raise ValueError(f"Tool {tool.get('name')!r} match.command_prefixes must be a list of non-empty strings.")
            candidate_data["tools"] = raw_tools
        # 5. Validate tools_markdown
        if "tools_markdown" in updates:
            tm_raw = updates["tools_markdown"]
            if not isinstance(tm_raw, str): raise ValueError("'tools_markdown' must be a string.")
            tm_str = tm_raw.strip()
            if tm_str:
                tm_path = (project_root / tm_str).resolve()
                if not tm_path.is_relative_to(project_root):
                    raise ValueError("tools_markdown must be inside project root.")
                if not tm_path.is_file():
                    raise ValueError(f"tools_markdown file does not exist: {tm_str}")
            candidate_data["tools_markdown"] = tm_str

        # 6. Validate hidden_commands
        if "hidden_commands" in updates:
            raw_hidden = updates["hidden_commands"]
            if not isinstance(raw_hidden, list) or not all(isinstance(cmd, str) and cmd.strip() for cmd in raw_hidden):
                raise ValueError("'hidden_commands' must be a list of non-empty strings.")
            commands_dict = candidate_data.get("commands")
            if not isinstance(commands_dict, dict): commands_dict = {}
            commands_dict["hidden"] = [cmd.strip() for cmd in raw_hidden]
            candidate_data["commands"] = commands_dict
        # 7. Validate block_layouts
        if "block_layouts" in updates:
            raw_layouts = updates["block_layouts"]
            if not isinstance(raw_layouts, dict):
                raise ValueError("'block_layouts' must be an object.")
            valid_groups = set(DEFAULT_BLOCK_LAYOUTS)
            valid_modes = {"list", "grid", "free"}
            norm_layouts = dict(candidate_data.get("block_layouts") or settings.get("block_layouts", DEFAULT_BLOCK_LAYOUTS))
            for k, v in raw_layouts.items():
                if k not in valid_groups:
                    raise ValueError(f"Invalid block_layouts group: {k!r}. Allowed groups: {sorted(valid_groups)}")
                if v not in valid_modes:
                    raise ValueError(f"Invalid block_layouts mode for {k!r}: {v!r}. Allowed modes: {sorted(valid_modes)}")
                norm_layouts[k] = "list" if k == "memory" else v
            candidate_data["block_layouts"] = norm_layouts

        # Pre-validate prospective catalog BEFORE writing!
        candidate_settings = {
            **settings,
            **candidate_data,
            "project_root": project_root,
            "config_file": settings["config_file"],
        }
        # This will raise ValueError on duplicates or invalid paths, preserving on-disk config
        resource_catalog(candidate_settings)

        # If validation succeeds, backup existing configuration
        storage = settings["storage"]
        backup_dir = storage / "config-backups"
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup_file = backup_dir / f"{current_rev}.json"
        if config_path.is_file() and not backup_file.exists():
            fd = os.open(backup_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(config_path.read_bytes())

        candidate_data["schema_version"] = 1
        encoded = (json.dumps(candidate_data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        import tempfile, stat
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        mode = stat.S_IMODE(config_path.stat().st_mode) if config_path.exists() else 0o644
        with tempfile.NamedTemporaryFile(dir=config_path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            os.fchmod(stream.fileno(), mode)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.replace(config_path)
        new_rev = hashlib.sha256(encoded).hexdigest()
        refresh_settings(settings, config_path)
        from .capabilities import normalize_manual_capabilities
        return {
            "revision": new_rev,
            "project_root": str(settings["project_root"]),
            "capabilities": normalize_manual_capabilities(settings.get("capabilities")),
            "capability_links": _normalize_capability_links(settings.get("capability_links", {})),
            "memory_systems": normalize_memory_systems(settings.get("memory_systems", [])),
            "documents": settings.get("documents", DEFAULT_DOCUMENTS),
            "vault_path": str(settings.get("vault_path") or ""),
            "tools": settings.get("tools", []),
            "tools_markdown": str(settings.get("tools_markdown") or ""),
            "hidden_commands": list((settings.get("commands") or {}).get("hidden", [])),
            "hidden_blocks": list(settings.get("hidden_blocks", [])),
            "hidden_resources": list(settings.get("hidden_resources", [])),
            "block_layouts": dict(settings.get("block_layouts", DEFAULT_BLOCK_LAYOUTS)),
        }

def apply_environment(settings):
    # Legacy emitters use these names; keep the same authenticated wire protocol.
    os.environ["CAD_XRAY_TOKEN_FILE"] = str(settings["token_file"])
    os.environ["CAD_XRAY_HISTORY_FILE"] = str(settings["history_file"])
    os.environ["AGENT_XRAY_ENDPOINT"] = f"http://127.0.0.1:{settings['port']}/api/event"

def _document_links(nodes, texts):
    paths = {node["path"]: node["id"] for node in nodes if node["kind"] == "document"}
    names = {}
    for path in paths:
        for name in {posixpath.basename(path), posixpath.splitext(posixpath.basename(path))[0]}:
            names.setdefault(name, set()).add(path)
    parser = MarkdownIt("commonmark")

    def wiki_reference(state, silent):
        if not state.src.startswith("[[", state.pos):
            return False
        end = state.src.find("]]", state.pos + 2)
        if end < 0 or "\n" in state.src[state.pos:end]:
            return False
        if not silent:
            token = state.push("observer_wikilink", "", 0)
            token.content = state.src[state.pos + 2:end]
        state.pos = end + 2
        return True

    parser.inline.ruler.before("link", "observer_wikilink", wiki_reference)
    pairs = set()

    def resolve(source, raw, wiki=False):
        target = unquote(raw.split("|", 1)[0] if wiki else raw).strip()
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return None
        target = parsed.path.replace("\\", "/")
        is_vault = source.startswith("@vault/")
        if is_vault:
            relative = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
            rooted = "@vault/" + posixpath.normpath(target.lstrip("/"))
        else:
            relative = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
            rooted = posixpath.normpath(target.lstrip("/"))
        candidates = [rooted, relative] if wiki or target.startswith("/") else [relative, rooted]
        for candidate in candidates:
            if candidate == ".." or candidate.startswith("../"):
                continue
            variants = [candidate] if candidate in paths else [candidate, candidate + ".md", candidate + ".markdown"]
            matches = {path for path in variants if path in paths}
            if len(matches) == 1:
                return paths[next(iter(matches))]
            if len(matches) > 1:
                return None
        if wiki and "/" not in target:
            matches = names.get(target, set())
            if len(matches) == 1:
                return paths[next(iter(matches))]
        return None

    def visit(tokens, source, source_id):
        for token in tokens:
            if token.type == "image":
                continue
            references = []
            if token.type == "link_open":
                references.append((token.attrGet("href") or "", False))
            elif token.type == "observer_wikilink":
                references.append((token.content, True))
            for raw, wiki in references:
                try:
                    target = resolve(source, raw, wiki)
                except ValueError:
                    continue
                if target and target != source_id:
                    pairs.add((source_id, target))
            if token.children:
                visit(token.children, source, source_id)

    for path, node_id in paths.items():
        visit(parser.parse(texts.get(node_id, "")), path, node_id)
    return [{"from": source, "to": target} for source, target in sorted(pairs)]

def resource_catalog(settings):
    root = settings["project_root"]
    nodes, texts = [], {}
    seen_docs = set()
    # 1. Configured document sources (inside project)
    project_doc_physical_paths = set()
    excludes = {".git", ".venv", "node_modules", "__pycache__", ".observer-backups"}
    ids = set()

    for source in settings.get("documents", []):
        if not isinstance(source, dict) or not isinstance(source.get("path"), str): raise ValueError("Document sources need a path")
        location = (root / source["path"]).resolve()
        if not location.is_relative_to(root): raise ValueError("Document sources must be inside the configured project")
        files = [location] if location.is_file() else (path for pattern in source.get("include", ["**/*.md"]) for path in location.glob(pattern))
        for path in sorted(files):
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not path.is_file(): continue
            relative = path.relative_to(root)
            if any(part in excludes for part in relative.parts) or relative.as_posix() in seen_docs: continue
            seen_docs.add(relative.as_posix())
            project_doc_physical_paths.add(resolved)
            try: text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError): continue
            parts = text.splitlines()
            metadata = {}
            body = text
            if parts and parts[0].strip() == "---":
                try:
                    end = next(i for i in range(1, len(parts)) if parts[i].strip() == "---")
                    body = "\n".join(parts[end + 1:])
                    metadata = yaml.safe_load("\n".join(parts[1:end])) or {}
                    if not isinstance(metadata, dict): metadata = {}
                except (StopIteration, yaml.YAMLError): pass
            parent = " / ".join(relative.parts[:-1])
            group = str(metadata.get("graph_group") or source.get("group") or parent)
            node_id = str(metadata.get("graph_id") or "resource_" + hashlib.sha1(relative.as_posix().encode()).hexdigest()[:12])
            if node_id in ids: raise ValueError(f"Duplicate resource ID: {node_id}")
            ids.add(node_id)
            nodes.append({
                "id": node_id, "kind": "document", "label": relative.as_posix(),
                "path": relative.as_posix(), "group": group, "category": str(metadata.get("graph_category") or "Document"),
                "description": str(metadata.get("description") or source.get("description") or "Project document."),
                "estimated_tokens": max(1, round(len(text) / 3.5)),
                "source_root": "project", "source_relative": relative.as_posix(),
                "absolute_path": str(resolved), "format": "markdown",
            })
            texts[node_id] = body

    # 3. Explicit Obsidian vault (internal or external)
    vault_str = str(settings.get("vault_path") or "").strip()
    vault_root = (root / Path(vault_str).expanduser()).resolve() if vault_str else None
    if vault_root and vault_root.is_dir():
        vault_excludes = excludes | {".obsidian", ".trash", ".trashcan"}
        for path in sorted(vault_root.rglob("*.md")):
            resolved = path.resolve()
            if not resolved.is_relative_to(vault_root) or not path.is_file():
                continue
            # Physical path dedup: if already a registered project doc, do not duplicate as vault node
            if resolved in project_doc_physical_paths:
                continue
            relative = path.relative_to(vault_root)
            if any(part in vault_excludes for part in relative.parts):
                continue
            token = f"@vault/{relative.as_posix()}"
            if token in seen_docs:
                continue
            seen_docs.add(token)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            parts = text.splitlines()
            metadata = {}
            body = text
            if parts and parts[0].strip() == "---":
                try:
                    end = next(i for i in range(1, len(parts)) if parts[i].strip() == "---")
                    body = "\n".join(parts[end + 1:])
                    metadata = yaml.safe_load("\n".join(parts[1:end])) or {}
                    if not isinstance(metadata, dict): metadata = {}
                except (StopIteration, yaml.YAMLError): pass
            parent = " / ".join(relative.parts[:-1])
            group = str(metadata.get("graph_group") or ("Obsidian Vault" + (f" / {parent}" if parent else "")))
            category = str(metadata.get("graph_category") or "Obsidian")
            node_id = str(metadata.get("graph_id") or "vault_" + hashlib.sha1(relative.as_posix().encode()).hexdigest()[:12])
            if node_id in ids:
                node_id = "vault_" + hashlib.sha1((token + "_node").encode()).hexdigest()[:12]
            ids.add(node_id)
            nodes.append({
                "id": node_id, "kind": "document", "label": token,
                "path": token, "group": group, "category": category,
                "description": str(metadata.get("description") or "Obsidian vault document."),
                "estimated_tokens": max(1, round(len(text) / 3.5)),
                "source_root": "vault", "source_relative": relative.as_posix(),
                "absolute_path": str(resolved), "format": "markdown",
            })
            texts[node_id] = body

    # 4. Configured and derived tools
    raw_tools = list(settings.get("tools", []))
    tools_file = settings.get("tools_markdown")
    derived_tools = []
    if tools_file:
        path = (root / tools_file).resolve()
        if not path.is_relative_to(root): raise ValueError("tools_markdown must be inside the project")
        if path.is_file():
            import re
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
                if match:
                    name, command, purpose = (part.strip().strip("`") for part in match.groups())
                    derived_tools.append({
                        "name": name,
                        "description": f"{purpose} · {command}",
                        "source": str(tools_file),
                        "origin": "tools_markdown",
                        "immutable": True,
                    })
    all_tools = [{**t, "source": "settings", "origin": "settings", "immutable": False} for t in raw_tools] + derived_tools
    for tool in all_tools:
        name = str(tool.get("name", "")).strip()
        if not name: raise ValueError("Each tool needs a name")
        m_obj = tool.get("match", {})
        if not isinstance(m_obj, dict):
            raise ValueError(f"Tool {name!r} match must be an object.")
        if "tool_names" in m_obj and (not isinstance(m_obj["tool_names"], list) or not all(isinstance(x, str) for x in m_obj["tool_names"])):
            raise ValueError(f"Tool {name!r} match.tool_names must be a list of strings.")
        if "command_prefixes" in m_obj and (not isinstance(m_obj["command_prefixes"], list) or not all(isinstance(x, str) for x in m_obj["command_prefixes"])):
            raise ValueError(f"Tool {name!r} match.command_prefixes must be a list of strings.")
        node_id = str(tool.get("id") or "tool_" + hashlib.sha1(name.encode()).hexdigest()[:12])
        if node_id in ids: raise ValueError(f"Duplicate resource ID: {node_id}")
        ids.add(node_id)
        nodes.append({
            "id": node_id, "label": name, "kind": "tool", "category": tool.get("category", "Tool"),
            "group": tool.get("group", "Tools & services"),
            "description": tool.get("description", "Configured tool; usage requires an observed event."),
            "match": m_obj,
            "source": tool.get("source", "settings"),
            "origin": tool.get("origin", "settings"),
            "immutable": tool.get("immutable", False),
        })
    # 4. Capabilities inventory (MCP, Skills, Integrations, Native Tools)
    from .capabilities import discover_capabilities, capability_nodes, load_capabilities_snapshot, merge_capabilities, normalize_manual_capabilities, DEFAULT_NATIVE_TOOLS, _discovery_source
    config_file_path = Path(settings.get("config_file", root / "config/observer.project.json"))
    cap_inventory = load_capabilities_snapshot(config_file_path.parent)
    expected_source = _discovery_source(settings.get("agent_runtime"))
    if not cap_inventory or cap_inventory.get("source") != expected_source:
        cap_inventory = discover_capabilities(root, config_file_path, agent=settings.get("agent_runtime"))

    manual_cap = normalize_manual_capabilities(settings.get("capabilities"))
    combined_inventory = merge_capabilities(cap_inventory, manual_cap)

    for c_node in capability_nodes(combined_inventory):
        if c_node["id"] not in ids:
            ids.add(c_node["id"])
            nodes.append(c_node)
    memory_systems = normalize_memory_systems(settings.get("memory_systems", []))
    memory_ids = {system["id"] for system in memory_systems}
    memory_systems += [system for system in combined_inventory.get("memory_systems", []) if system["id"] not in memory_ids]
    for system in memory_systems:
        node = memory_node(system)
        if node["id"] in ids:
            continue
        ids.add(node["id"])
        nodes.append(node)
    return {
        "project_title": settings["project_name"],
        "project_root": str(root),
        "agent_label": settings["agent_label"],
        "agent_runtime": str(settings.get("agent_runtime", "")),
        "agent_description": str(settings.get("agent_description") or ""),
        "document_sources": [{"path": item["path"], "group": item.get("group", item["path"])} for item in settings.get("documents", [])],
        "vault_path": str(vault_root) if vault_root else "",
        "vault_configured": bool(vault_root and vault_root.is_dir()),
        "tools_markdown": str(settings.get("tools_markdown") or ""),
        "derived_tools": derived_tools,
        "hidden_commands": list((settings.get("commands") or {}).get("hidden", [])),
        "observer_version": VERSION,
        "capability_inventory": combined_inventory,
        "manual_capabilities": manual_cap,
        "capability_links": _normalize_capability_links(settings.get("capability_links", {})),
        "memory_systems": [public_system(system) for system in memory_systems],
        "native_tools": combined_inventory.get("native_tools", DEFAULT_NATIVE_TOOLS),
        "block_layouts": dict(settings.get("block_layouts", DEFAULT_BLOCK_LAYOUTS)),
        "hidden_blocks": list(settings.get("hidden_blocks", [])),
        "hidden_resources": list(settings.get("hidden_resources", [])),
        "nodes": nodes,
        "document_links": _document_links(nodes, texts),
        "agent_spec": [],
        "observed_only": True
    }
