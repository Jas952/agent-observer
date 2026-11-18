"""Static and runtime capability inventory and discovery for Agent Observer.

Covers MCP servers, Agent skills, CLI integrations (RTK), and native tools.
Does not execute tools or leak credentials/arguments/environment/tokens.
"""
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

DISCOVERY_VERSION = 5

DEFAULT_NATIVE_TOOLS = [
    {"name": "read", "description": "Read local path, internal URI, or URL with line selectors", "source": "builtin"},
    {"name": "write", "description": "Create or overwrite file atomically", "source": "builtin"},
    {"name": "edit", "description": "Hashline patch existing file with snapshot validation", "source": "builtin"},
    {"name": "eval", "description": "Evaluate Python or JavaScript code directly in the runtime kernel", "source": "builtin"},
    {"name": "bash", "description": "Command execution shell", "source": "builtin"},
    {"name": "glob", "description": "Recursive file and directory pattern discovery", "source": "builtin"},
    {"name": "grep", "description": "Regex content pattern search", "source": "builtin"},
    {"name": "task", "description": "Subagent task delegation", "source": "builtin"},
    {"name": "wait", "description": "Wait on background tasks and subagent deliveries", "source": "builtin"},
    {"name": "web_search", "description": "Search web and documentation with citations", "source": "builtin"},
]

NATIVE_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "read": "Read local path, internal URI, or URL with line selectors",
    "write": "Create or overwrite file atomically",
    "edit": "Hashline patch existing file with snapshot validation",
    "eval": "Evaluate Python or JavaScript code directly in the runtime kernel",
    "bash": "Command execution shell",
    "glob": "Recursive file and directory pattern discovery",
    "grep": "Regex content pattern search",
    "task": "Subagent task delegation",
    "wait": "Wait on background tasks and subagent deliveries",
    "web_search": "Search web and documentation with citations",
    "apply_patch": "Apply structured patch to files",
    "read_image": "Inspect image dimensions, format, and visual contents",
    "view_image": "Inspect image dimensions, format, and visual contents",
    "browser": "Drive headless Chromium browser for navigation and interaction",
    "screenshot": "Capture screen or application window",
    "transcribe": "Extract speech to text from audio recordings",
}

GENERIC_MCP_PATTERN = re.compile(
    r"^(?:(?:Configured|Manual|Runtime)\s+)?MCP\s+server\s+['\"][^'\"]+['\"]\.?$",
    re.IGNORECASE,
)

KNOWN_MCP_SERVERS: Dict[str, Dict[str, Any]] = {
    "codebase-memory-mcp": {
        "description": "Persistent codebase knowledge graph maintaining AST symbol indices, call hierarchies, and architectural dependencies across sessions.",
        "github_url": "https://github.com/DeusData/codebase-memory-mcp",
        "source_url": "https://github.com/DeusData/codebase-memory-mcp",
        "description_source": "github",
        "tools": [
            {"name": "index_repository", "description": "Index a repository into the knowledge graph (AST structures, dependencies, call hierarchies)"},
            {"name": "search_graph", "description": "Search code knowledge graph for functions, classes, routes, and variables"},
            {"name": "query_graph", "description": "Execute Cypher queries against the codebase knowledge graph"},
            {"name": "trace_path", "description": "Trace call paths or data flow through the codebase graph"},
            {"name": "get_code_snippet", "description": "Retrieve verbatim source code snippet for a qualified symbol"},
            {"name": "get_graph_schema", "description": "Retrieve knowledge graph node labels and edge relationship schema"},
            {"name": "get_architecture", "description": "High-level architecture overview including packages, services, and Leiden communities"},
            {"name": "search_code", "description": "Graph-augmented text search ranked by structural importance"},
            {"name": "list_projects", "description": "List all indexed projects and repositories in the graph store"},
            {"name": "delete_project", "description": "Delete an indexed project from the knowledge graph"},
            {"name": "index_status", "description": "Inspect indexing progress and node/edge statistics for a project"},
            {"name": "detect_changes", "description": "Detect code modifications and analyze their structural blast radius"},
            {"name": "manage_adr", "description": "Create or update Architecture Decision Records in the knowledge graph"},
            {"name": "ingest_traces", "description": "Ingest runtime execution traces to enrich graph relationships"},
        ],
    },
    "codegraph": {
        "description": "Local SQLite-backed code intelligence for symbol search, call hierarchy traversal, and change-impact analysis.",
        "github_url": "https://github.com/colbymchenry/codegraph",
        "source_url": "https://github.com/colbymchenry/codegraph",
        "description_source": "package",
        "tools": [
            {"name": "codegraph_explore", "description": "Primary exploration tool returning verbatim source of relevant symbols grouped by file"},
            {"name": "codegraph_search", "description": "Fast symbol search by name returning kind, location, and signature"},
            {"name": "codegraph_node", "description": "Retrieve full symbol definition, signature, caller/callee trail, and source body"},
            {"name": "codegraph_callers", "description": "List functions and methods that call a specified symbol"},
            {"name": "codegraph_callees", "description": "List functions and methods called by a specified symbol"},
            {"name": "codegraph_impact", "description": "Compute downstream blast radius and affected symbols for a refactor"},
            {"name": "codegraph_files", "description": "List indexed project file hierarchy with language and symbol counts"},
            {"name": "codegraph_status", "description": "Check knowledge graph index health, node counts, and sync status"},
        ],
    },
    "jcodemunch": {
        "description": "Token-efficient source code exploration server using tree-sitter AST parsing for symbol navigation, outline indexing, and catalog dispatch.",
        "github_url": "https://github.com/jgravelle/jcodemunch-mcp",
        "source_url": "https://github.com/jgravelle/jcodemunch-mcp",
        "description_source": "package",
        "tools": [
            {"name": "route", "description": "Map a natural-language task to recommended catalog actions and argument templates"},
            {"name": "menu", "description": "Discover and browse the jcodemunch action catalog without keeping full schemas resident"},
            {"name": "order", "description": "Dispatch any catalog action by name with validated arguments"},
            {"name": "announce_model", "description": "Self-report active model identifier to configure appropriate tool tiers"},
            {"name": "set_tool_tier", "description": "Override exposed tool tier (core, standard, full) for the active session"},
            {"name": "jcodemunch_guide", "description": "Return version-current CLAUDE.md / AGENTS.md policy snippet"},
        ],
    },
    "xcodebuildmcp": {
        "description": "Builds and tests Apple projects, manages simulators, and exposes app inspection and UI automation tools.",
        "github_url": "https://github.com/getsentry/XcodeBuildMCP",
        "source_url": "https://github.com/getsentry/XcodeBuildMCP",
        "description_source": "package",
        "tools": [
            {"name": "build_run_sim", "description": "Build, install, and launch application on iOS Simulator with log streaming"},
            {"name": "build_sim", "description": "Compile Xcode project for iOS simulator without launching"},
            {"name": "test_sim", "description": "Run test suites on iOS simulator and report results"},
            {"name": "boot_sim", "description": "Boot iOS simulator device for testing and manual workflows"},
            {"name": "open_sim", "description": "Open Simulator frontend application on macOS"},
            {"name": "clean", "description": "Clean Xcode build products and intermediate files"},
            {"name": "discover_projs", "description": "Scan directory for Xcode projects (.xcodeproj) and workspaces (.xcworkspace)"},
            {"name": "get_app_bundle_id", "description": "Extract bundle identifier from built .app package"},
            {"name": "session_show_defaults", "description": "Inspect active session defaults profile for project, scheme, and simulator"},
            {"name": "session_set_defaults", "description": "Configure default project, scheme, and simulator for the session profile"},
            {"name": "session_clear_defaults", "description": "Clear session defaults profile settings"},
            {"name": "session_use_defaults_profile", "description": "Switch active session defaults profile"},
            {"name": "show_build_settings", "description": "Display Xcode build settings for project targets"},
            {"name": "snapshot_ui", "description": "Capture semantic runtime UI accessibility tree with elementRef targets"},
            {"name": "stop_app_sim", "description": "Terminate running application on simulator"},
            {"name": "install_app_sim", "description": "Install built application package onto simulator"},
            {"name": "launch_app_sim", "description": "Launch installed application on simulator and capture runtime logs"},
            {"name": "list_schemes", "description": "List available Xcode build schemes in workspace"},
            {"name": "list_sims", "description": "List available iOS simulator devices and runtime versions"},
            {"name": "record_sim_video", "description": "Record video capture of iOS simulator screen"},
            {"name": "screenshot", "description": "Capture screenshot of iOS simulator display"},
            {"name": "get_coverage_report", "description": "Extract per-target code coverage metrics from xcresult bundle"},
            {"name": "get_file_coverage", "description": "Extract line-by-line coverage for a specific source file"},
            {"name": "get_sim_app_path", "description": "Locate built app bundle path on simulator disk"},
        ],
    },
    "blender": {
        "description": "Live Blender 3D viewport and scene bridge supporting procedural geometry generation, mesh inspection, and bpy Python script execution.",
        "github_url": "https://github.com/ahujasid/blender-mcp",
        "source_url": "https://github.com/ahujasid/blender-mcp",
        "description_source": "builtin",
        "tools": [
            {"name": "execute_blender_code", "description": "Execute Python scripts in live Blender instance with full bpy API access"},
            {"name": "get_scene_info", "description": "Inspect Blender scene hierarchy, object dimensions, meshes, and rig health"},
            {"name": "look", "description": "Render and return single camera screenshot of current Blender 3D scene"},
            {"name": "generate_3d", "description": "Generate procedural or AI textured 3D model and import into scene"},
            {"name": "import_asset", "description": "Import 3D model asset into the live Blender scene"},
            {"name": "search_assets", "description": "Search external 3D asset libraries (Poly Haven, Sketchfab, Poly Pizza)"},
            {"name": "get_addon_status", "description": "Inspect connected Blender version and loaded addon generator extensions"},
            {"name": "disable_telemetry", "description": "Disable telemetry prompt and screenshot collection"},
            {"name": "record_trajectory_feedback", "description": "Record evaluation feedback for trajectory steps"},
        ],
    },
    "playwright": {
        "description": "Headless browser automation framework providing multi-browser navigation, DOM interaction, network tracing, and visual screenshot capture.",
        "github_url": "https://github.com/microsoft/playwright-mcp",
        "source_url": "https://github.com/microsoft/playwright-mcp",
        "description_source": "package",
        "tools": [
            {"name": "browser_navigate", "description": "Navigate browser to a specified URL"},
            {"name": "browser_click", "description": "Click element identified by selector or coordinates"},
            {"name": "browser_type", "description": "Type text into targeted input field"},
            {"name": "browser_screenshot", "description": "Capture full page or element screenshot"},
            {"name": "browser_evaluate", "description": "Execute JavaScript in page context and return result"},
            {"name": "browser_wait_for", "description": "Wait for selector, navigation, or timeout condition"},
        ],
    },
    "headroom": {
        "description": "Compresses agent context, retrieves original content by reference, and reports compression statistics.",
        "github_url": "https://github.com/chopratejas/headroom",
        "source_url": "https://github.com/chopratejas/headroom",
        "description_source": "package",
        "tools": [
            {"name": "headroom_compress", "description": "Compress search results, tool outputs, or file content to save context tokens"},
            {"name": "headroom_retrieve", "description": "Retrieve original uncompressed content by hash"},
            {"name": "headroom_stats", "description": "Inspect session compression statistics, saved tokens, and cost reduction"},
        ],
    },
    "apple-docs": {
        "description": "Apple Developer Documentation and WWDC archive search providing API reference, sample code, and platform compatibility guides.",
        "github_url": "https://github.com/kimsungwhee/apple-docs-mcp",
        "source_url": "https://github.com/kimsungwhee/apple-docs-mcp",
        "description_source": "package",
        "tools": [
            {"name": "search_apple_docs", "description": "Search Apple Developer Documentation for APIs, frameworks, guides, and samples"},
            {"name": "get_apple_doc_content", "description": "Fetch detailed content of an Apple Developer Documentation page"},
            {"name": "search_framework_symbols", "description": "Browse and search symbols within a specific Apple framework"},
            {"name": "get_technology_overviews", "description": "Access Apple technology guides, tutorials, and architectural overviews"},
            {"name": "get_sample_code", "description": "Browse complete Apple sample code projects and best-practice examples"},
            {"name": "get_platform_compatibility", "description": "Check API availability and deployment target compatibility across Apple OS versions"},
            {"name": "get_related_apis", "description": "Analyze API relationships, protocol conformances, and recommended alternatives"},
            {"name": "find_similar_apis", "description": "Discover alternative or modern replacement APIs for deprecated Apple interfaces"},
            {"name": "resolve_references_batch", "description": "Resolve referenced types and methods for a documentation page"},
            {"name": "list_technologies", "description": "Browse Apple technologies and frameworks categorized by domain"},
            {"name": "get_documentation_updates", "description": "Track latest Apple platform updates, new APIs, and release notes"},
            {"name": "browse_wwdc_topics", "description": "List WWDC session topic categories and identifier tags"},
            {"name": "list_wwdc_years", "description": "List available WWDC conference years and video catalog counts"},
            {"name": "list_wwdc_videos", "description": "Browse WWDC session videos filtered by year or topic"},
            {"name": "get_wwdc_video", "description": "Access complete WWDC session video transcript and resources"},
            {"name": "search_wwdc_content", "description": "Full-text search across WWDC video transcripts and code examples"},
            {"name": "get_wwdc_code_examples", "description": "Browse code examples extracted from WWDC sessions"},
            {"name": "find_related_wwdc_videos", "description": "Discover prerequisite and follow-up WWDC sessions related to a talk"},
        ],
    },
    "node_repl": {
        "description": "Node.js REPL interactive JavaScript/TypeScript evaluation environment.",
        "github_url": "",
        "source_url": "",
        "description_source": "builtin",
        "tools": [
            {"name": "eval", "description": "Evaluate JavaScript/TypeScript expression in Node.js REPL"},
        ],
    },
    "computer-use": {
        "description": "Desktop interaction service providing OS mouse, keyboard, and screen automation.",
        "github_url": "",
        "source_url": "",
        "description_source": "builtin",
        "tools": [
            {"name": "mouse_click", "description": "Simulate mouse click at screen coordinates"},
            {"name": "keyboard_type", "description": "Simulate keyboard keystrokes or text entry"},
            {"name": "take_screenshot", "description": "Capture desktop screen pixel buffer"},
        ],
    },
}


def is_generic_placeholder(desc: Optional[str], name: Optional[str] = None) -> bool:
    """Returns True if the description is empty or a generic placeholder string."""
    if not desc:
        return True
    s = desc.strip()
    if not s:
        return True
    if GENERIC_MCP_PATTERN.match(s):
        return True
    if name:
        norm_name = name.strip().lower()
        if s.lower() in {
            f"mcp server '{norm_name}'",
            f"mcp server \"{norm_name}\"",
            f"configured mcp server '{norm_name}'",
            f"configured mcp server \"{norm_name}\"",
            f"manual mcp server '{norm_name}'",
            f"manual mcp server \"{norm_name}\"",
            f"runtime mcp server '{norm_name}'",
            f"runtime mcp server \"{norm_name}\"",
            "mcp server",
            norm_name,
        }:
            return True
    return False


def get_mcp_metadata(
    name: str,
    fallback_description: str = "",
    tools: Optional[List[Dict[str, str]]] = None,
    tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Describe a server and enrich only operations declared by its inventory."""
    norm = name.strip()
    known = KNOWN_MCP_SERVERS.get(norm.lower(), {})
    documented = {tool["name"]: tool.get("description", "") for tool in known.get("tools", [])}
    reported = {tool["name"]: tool.get("description", "") for tool in tools or []}
    names = tool_names or list(reported)
    definitions = [
        {"name": tool_name, "description": reported.get(tool_name) or documented.get(tool_name, "")}
        for tool_name in dict.fromkeys(names)
    ]

    if fallback_description and not is_generic_placeholder(fallback_description, norm):
        entry = {
            "description": fallback_description.strip(),
            "description_source": "manual",
            "github_url": known.get("github_url", ""),
            "source_url": known.get("source_url", ""),
        }
    elif known:
        entry = {key: value for key, value in known.items() if key != "tools"}
    else:
        if names:
            sample = ", ".join(names[:5])
            suffix = "..." if len(names) > 5 else ""
            description = f"MCP server with {len(names)} declared operations: {sample}{suffix}."
            source = "tool-inventory"
        else:
            description = "Configured Model Context Protocol server; no operation inventory has been reported."
            source = "omp-config"
        entry = {
            "description": description,
            "description_source": source,
            "github_url": "",
            "source_url": "",
        }
    entry["tools"] = definitions
    return entry


def mcp_node_id(name: str) -> str:
    return "mcp_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def skill_node_id(name: str) -> str:
    return "skill_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def integration_node_id(name: str) -> str:
    if name == "rtk":
        return "integration_rtk"
    return "integration_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def parse_skill_md_metadata(skill_md: Path) -> Dict[str, str]:
    """Safely extracts name and description from a SKILL.md file without executing anything."""
    name = skill_md.parent.name
    description = f"Agent skill '{name}'"
    try:
        with skill_md.open("r", encoding="utf-8", errors="ignore") as f:
            chunk = f.read(4096)
        lines = chunk.splitlines()
        start_idx = 0
        while start_idx < len(lines) and not lines[start_idx].strip():
            start_idx += 1
        if start_idx < len(lines) and lines[start_idx].strip() == "---":
            for line in lines[start_idx + 1:]:
                stripped = line.strip()
                if stripped == "---":
                    break
                if stripped.startswith("name:"):
                    raw_val = stripped.split("name:", 1)[1].strip().strip("\"'")
                    if raw_val:
                        name = raw_val
                elif stripped.startswith("description:"):
                    raw_val = stripped.split("description:", 1)[1].strip().strip("\"'")
                    if raw_val:
                        description = raw_val
        elif lines:
            for line in lines:
                s = line.strip()
                if s.startswith("# "):
                    name = s.lstrip("# ").strip()
                    break
    except OSError:
        pass
    return {"name": name, "description": description}


def _codex_cli_inventory(project_root):
    from .codex_observer import find_codex_cli_path
    executable = find_codex_cli_path()
    if not executable:
        return None
    try:
        result = subprocess.run([str(executable), "mcp", "list", "--json"],
                                cwd=project_root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=5)
        if result.returncode or len(result.stdout) > 1024 * 1024:
            return None
        entries = json.loads(result.stdout)
        if isinstance(entries, list) and all(
                isinstance(item, dict) and isinstance(item.get("name"), str)
                and item["name"].strip() and isinstance(item.get("enabled"), bool)
                for item in entries):
            return entries
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _codex_mcp_entry(name, descriptor, source, metadata=None):
    metadata = metadata or get_mcp_metadata(name, fallback_description=descriptor.get("description") if isinstance(descriptor.get("description"), str) else "")
    disabled = descriptor.get("enabled") is False
    return {"name": name, "connection": "disabled" if disabled else "configured",
            "availability": "unavailable" if disabled else "configured", "source": source,
            "tool_names": [], "tools": [], "description": metadata["description"],
            "description_source": metadata.get("description_source", source),
            **({"github_url": metadata["github_url"]} if metadata.get("github_url") else {})}


def _discover_codex_mcp(project_root):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    candidates = []
    if project_root:
        candidates.append((Path(project_root) / ".codex/config.toml", "project-codex"))
    candidates.append((Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml", "global-codex"))
    found, seen = [], set()
    for path, source in candidates:
        try:
            if not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            with path.open("rb") as stream:
                settings = tomllib.load(stream)
            servers = settings.get("mcp_servers", {})
            if not isinstance(servers, dict):
                continue
            for name, descriptor in servers.items():
                if not isinstance(name, str) or not isinstance(descriptor, dict) or not name.strip() or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                found.append(_codex_mcp_entry(name, descriptor, source))
        except (OSError, ValueError):
            continue
    effective = _codex_cli_inventory(project_root)
    if effective is not None:
        configured = {item["name"].casefold(): item for item in found}
        found = []
        for descriptor in effective:
            name = descriptor["name"].strip()
            previous = configured.get(name.casefold(), {})
            found.append(_codex_mcp_entry(name, descriptor, previous.get("source", "codex-cli"), metadata=previous))
    return found


def discover_mcp_servers(project_root: Optional[Path] = None, agent: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read static registrations without executing servers or exposing credentials."""
    if agent == "codex":
        return _discover_codex_mcp(project_root)
    servers: List[Dict[str, Any]] = []
    from .omp_observer import omp_home
    omp_disabled, omp_enabled = set(), set()
    if agent == "omp":
        try:
            controls = json.loads((omp_home() / "mcp.json").read_text())
            omp_disabled = {name.casefold() for name in controls.get("disabledServers", []) if isinstance(name, str)}
            omp_enabled = {name.casefold() for name in controls.get("enabledServers", []) if isinstance(name, str)}
        except (OSError, ValueError, TypeError):
            pass
    seen: Set[str] = set()

    candidate_files: List[tuple[Path, str]] = []
    if project_root:
        if agent not in {"cad", "generic"}:
            candidate_files.extend([
                (project_root / ".omp/mcp.json", "project-omp"),
                (project_root / ".omp/agent/mcp.json", "project-omp"),
            ])
        candidate_files.append((project_root / ".mcp.json", "project-mcp"))

    home = Path.home()
    if agent not in {"cad", "generic"}:
        candidate_files.extend([
            (omp_home() / "mcp.json", "global-omp"),
            (home / ".omp/mcp.json", "global-omp"),
            (home / ".claude/.mcp.json", "claude-mcp"),
            (home / ".claude/mcp.json", "claude-mcp"),
        ])

    for path, source_label in candidate_files:
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            mcp_servers = data.get("mcpServers") or data.get("servers") or {}
            disabled = set(data.get("disabledServers", [])) | omp_disabled
            if agent == "omp" and source_label == "global-omp" and isinstance(mcp_servers, dict):
                mcp_servers = {**mcp_servers, **{name: {"enabled": False} for name in disabled if name not in mcp_servers}}
            if isinstance(mcp_servers, dict):
                for name, cfg in sorted(mcp_servers.items(), key=lambda kv: str(kv[0])):
                    norm = str(name).strip()
                    if not norm or norm.casefold() in seen:
                        continue
                    seen.add(norm.casefold())
                    is_disabled = norm.casefold() in {str(d).casefold() for d in disabled} or (isinstance(cfg, dict) and cfg.get("enabled") is False and norm.casefold() not in omp_enabled)
                    explicit_desc = ""
                    if isinstance(cfg, dict) and isinstance(cfg.get("description"), str):
                        explicit_desc = cfg["description"].strip()
                    meta = get_mcp_metadata(norm, fallback_description=explicit_desc)
                    entry: Dict[str, Any] = {
                        "name": norm,
                        "connection": "disabled" if is_disabled else "configured",
                        "availability": "unavailable" if is_disabled else "configured",
                        "tool_names": [t["name"] for t in meta.get("tools", [])],
                        "tools": meta.get("tools", []),
                        "source": source_label,
                        "description": meta["description"],
                        "description_source": meta.get("description_source", source_label),
                    }
                    if meta.get("github_url"):
                        entry["github_url"] = meta["github_url"]
                    if meta.get("source_url"):
                        entry["source_url"] = meta["source_url"]
                    servers.append(entry)
        except (OSError, json.JSONDecodeError):
            continue
    return servers


def discover_skills(project_root: Optional[Path] = None, agent: Optional[str] = None) -> List[Dict[str, Any]]:
    """Discovers installed skills from standard filesystem locations.
    Only reads static YAML/Markdown headers, never runs external commands.
    Discovered items are marked 'discovered', not 'provided'.
    """
    skills: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    candidate_dirs: List[tuple[Path, str]] = []
    if project_root:
        candidate_dirs.extend([
            (project_root / ".omp/skills", "project-omp"),
            (project_root / ".omp/agent/skills", "project-omp"),
            (project_root / "skills", "project-skills"),
            (project_root / ".skills", "project-skills"),
            (project_root / ".claude/skills", "project-claude"),
            (project_root / ".codex/skills", "project-codex"),
            (project_root / ".agents/skills", "project-agents"),
        ])

    home = Path.home()
    candidate_dirs.extend([
        (home / ".omp/skills", "omp-skills"),
        (home / ".omp/agent/skills", "omp-skills"),
        (home / ".omp/agent/managed-skills", "omp-managed"),
        (home / ".claude/skills", "claude-skills"),
        (home / ".codex/skills", "codex-skills"),
        (home / ".agents/skills", "agents-skills"),
        (home / ".skills", "home-skills"),
        (home / ".config/skills", "config-skills"),
    ])
    if agent in {"cad", "generic"}:
        candidate_dirs = [(path, source) for path, source in candidate_dirs
                          if source in {"project-skills", "project-agents"}]
    if agent == "codex":
        candidate_dirs = [(path, source) for path, source in candidate_dirs if source in {"project-skills", "project-codex", "project-agents", "codex-skills", "agents-skills"}]

    # Direct skill file at project root (if project is itself a skill root)
    if project_root:
        for fname in ("SKILL.md", "skill.md"):
            direct_file = project_root / fname
            if direct_file.is_file():
                meta = parse_skill_md_metadata(direct_file)
                name = meta["name"]
                if name.casefold() not in seen:
                    seen.add(name.casefold())
                    skills.append({
                        "name": name,
                        "path": fname,
                        "description": meta["description"],
                        "availability": "discovered",
                        "source": "project-root",
                    })
                break

    for base_dir, source in candidate_dirs:
        if not base_dir.is_dir():
            continue
        try:
            # Check if base_dir itself is a skill directory containing SKILL.md
            for fname in ("SKILL.md", "skill.md"):
                root_skill = base_dir / fname
                if root_skill.is_file():
                    meta = parse_skill_md_metadata(root_skill)
                    name = meta["name"]
                    if name.casefold() not in seen:
                        seen.add(name.casefold())
                        try:
                            if project_root and root_skill.resolve().is_relative_to(project_root.resolve()):
                                rel_path = root_skill.resolve().relative_to(project_root.resolve()).as_posix()
                            else:
                                rel_path = f"~/{root_skill.relative_to(home).as_posix()}"
                        except Exception:
                            rel_path = str(root_skill)
                        skills.append({
                            "name": name,
                            "path": rel_path,
                            "description": meta["description"],
                            "availability": "discovered",
                            "source": source,
                        })
                    break

            for child in sorted(base_dir.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    skill_files = [child / "SKILL.md", child / "skill.md"]
                elif child.is_file() and child.suffix.lower() == ".md" and child.name.lower() != "skill.md":
                    with child.open("r", encoding="utf-8", errors="ignore") as handle:
                        header = handle.read(4096).lstrip()
                    frontmatter = header.split("---", 2)
                    if not header.startswith("---\n") or len(frontmatter) < 3 or not any(line.strip().startswith("name:") for line in frontmatter[1].splitlines()):
                        continue
                    skill_files = [child]
                else:
                    continue
                for skill_md in skill_files:
                    if not skill_md.is_file():
                        continue
                    meta = parse_skill_md_metadata(skill_md)
                    name = meta["name"]
                    if name.casefold() in seen:
                        continue
                    seen.add(name.casefold())

                    try:
                        if project_root and skill_md.resolve().is_relative_to(project_root.resolve()):
                            rel_path = skill_md.resolve().relative_to(project_root.resolve()).as_posix()
                        else:
                            rel_path = f"~/{skill_md.relative_to(home).as_posix()}"
                    except Exception:
                        rel_path = str(skill_md)

                    skills.append({
                        "name": name,
                        "path": rel_path,
                        "description": meta["description"],
                        "availability": "discovered",
                        "source": source,
                    })
                    break
        except OSError:
            continue

    return skills


def discover_rtk_integration() -> Dict[str, Any]:
    """Detects RTK availability on PATH without executing arbitrary commands."""
    rtk_bin = shutil.which("rtk")
    available = bool(rtk_bin)
    return {
        "name": "rtk",
        "type": "cli-wrapper",
        "availability": "available" if available else "unavailable",
        "source": "system-path" if available else "none",
        "description": "Token-optimized CLI proxy for shell commands with output filtering and token savings analytics",
        "source_url": "https://github.com/rtk-ai/rtk",
        "github_url": "https://github.com/rtk-ai/rtk",
        "description_source": "cli-integration",
    }

def _discovery_source(agent):
    return "codex-config" if agent == "codex" else ("project-config" if agent in {"cad", "generic"} else "omp-config")


def discover_capabilities(project_root: Optional[Path] = None, config_file: Optional[Path] = None, *, agent: Optional[str] = None) -> Dict[str, Any]:
    """Build a safe inventory for the selected agent, without executing its tools."""
    if not agent and config_file:
        try:
            path = Path(config_file)
            if path.is_file() and path.stat().st_size <= 1024 * 1024:
                config = json.loads(path.read_text(encoding="utf-8"))
                from .observer_config import project_profile
                profile = project_profile(project_root or path.parent, config)
                connection = config.get("connection", {})
                selected = connection.get("module", "project" if profile != "generic" else (
                    "codex" if config.get("agent_runtime") == "codex" else "project"))
                agent = selected if selected in {"codex", "omp"} else profile
        except (OSError, ValueError, AttributeError):
            pass
    mcp = discover_mcp_servers(project_root, agent=agent)
    skills = discover_skills(project_root, agent=agent)
    rtk = discover_rtk_integration()
    if agent == "codex" and project_root:
        from .codex_observer import read_codex_native_tools
        native_tools = read_codex_native_tools(project_root)
    elif agent == "cad" and project_root:
        from .cad_runtime import discover_cad_native_tools
        native_tools = discover_cad_native_tools(project_root)
    else:
        native_tools = [] if agent in {"cad", "generic"} else list(DEFAULT_NATIVE_TOOLS)
    extra = {}
    if agent == "omp":
        from .omp_inventory import configured_inventory
        extra = configured_inventory(project_root)
        native_tools = extra.pop("native_tools")
    return {
        "source": _discovery_source(agent),
        "mcp": mcp,
        "skills": skills,
        "integrations": [rtk],
        "native_tools": native_tools,
        **extra,
    }


def normalize_manual_capabilities(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Normalizes and safely validates manual capability registrations for project settings."""
    if not isinstance(raw, dict):
        return {"mcp": [], "skills": []}

    clean_mcp: List[Dict[str, Any]] = []
    seen_mcp: Set[str] = set()
    raw_mcp = raw.get("mcp", [])
    if isinstance(raw_mcp, list):
        for item in raw_mcp:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name.casefold() in seen_mcp:
                continue
            seen_mcp.add(name.casefold())
            tools = item.get("tool_names", [])
            clean_tools = [str(t).strip() for t in tools if isinstance(t, str) and str(t).strip()] if isinstance(tools, list) else []

            tools_objs: List[Dict[str, str]] = []
            if isinstance(item.get("tools"), list):
                for t in item["tools"]:
                    if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip():
                        tools_objs.append({
                            "name": t["name"].strip(),
                            "description": str(t.get("description", ""))[:512],
                        })

            desc_raw = str(item.get("description", "")).strip()
            has_custom_desc = bool(desc_raw and not is_generic_placeholder(desc_raw, name))

            meta = get_mcp_metadata(
                name,
                fallback_description=desc_raw if has_custom_desc else "",
                tools=tools_objs if tools_objs else None,
                tool_names=clean_tools if clean_tools else None,
            )

            final_desc = desc_raw if has_custom_desc else meta["description"]
            final_source = "manual" if has_custom_desc else meta.get("description_source", "manual")
            final_tools_objs = meta["tools"]
            final_tool_names = clean_tools or [t["name"] for t in final_tools_objs]
            github_url = str(item.get("github_url") or meta.get("github_url", ""))[:512]
            source_url = str(item.get("source_url") or meta.get("source_url", ""))[:512]

            entry: Dict[str, Any] = {
                "name": name,
                "description": final_desc[:512],
                "description_source": final_source,
                "tool_names": final_tool_names,
                "tools": final_tools_objs,
            }
            if github_url:
                entry["github_url"] = github_url
            if source_url:
                entry["source_url"] = source_url

            clean_mcp.append(entry)

    clean_skills: List[Dict[str, Any]] = []
    seen_skills: Set[str] = set()
    raw_skills = raw.get("skills", [])
    if isinstance(raw_skills, list):
        for item in raw_skills:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name.casefold() in seen_skills:
                continue
            seen_skills.add(name.casefold())
            clean_skills.append({
                "name": name,
                "path": str(item.get("path", ""))[:512],
                "description": str(item.get("description", f"Manual skill '{name}'"))[:512],
            })

    return {"mcp": clean_mcp, "skills": clean_skills}


def merge_capabilities(discovered_inventory: Dict[str, Any], manual: Dict[str, Any]) -> Dict[str, Any]:
    """Merges discovered static capability inventory with manual project capability registrations.
    Manual entries retain 'manual' source and configured availability.
    """
    norm_manual = normalize_manual_capabilities(manual)
    merged = copy.deepcopy(discovered_inventory) if isinstance(discovered_inventory, dict) else {
        "source": "omp-config",
        "mcp": [],
        "skills": [],
        "integrations": [discover_rtk_integration()],
        "native_tools": list(DEFAULT_NATIVE_TOOLS),
    }

    disc_mcp = list(merged.get("mcp", []))
    mcp_by_name = {item["name"].casefold(): item for item in disc_mcp if isinstance(item, dict) and "name" in item}

    for m in norm_manual["mcp"]:
        name_key = m["name"].casefold()
        if name_key in mcp_by_name:
            target = mcp_by_name[name_key]
            if m.get("description") and not is_generic_placeholder(m.get("description"), m["name"]):
                target["description"] = m["description"]
                target["description_source"] = m.get("description_source", "manual")
            elif not target.get("description") or is_generic_placeholder(target.get("description"), target["name"]):
                if m.get("description"):
                    target["description"] = m["description"]

            if m.get("github_url"):
                target["github_url"] = m["github_url"]
            if m.get("source_url"):
                target["source_url"] = m["source_url"]

            if m.get("tools"):
                existing_tool_names = set(target.get("tool_names", []))
                existing_tools = list(target.get("tools", []))
                for t in m["tools"]:
                    if t["name"] not in existing_tool_names:
                        existing_tools.append(t)
                        target.setdefault("tool_names", []).append(t["name"])
                        existing_tool_names.add(t["name"])
                target["tools"] = existing_tools
            elif m.get("tool_names"):
                existing_tools = set(target.get("tool_names", []))
                for t in m["tool_names"]:
                    if t not in existing_tools:
                        target.setdefault("tool_names", []).append(t)
                        existing_tools.add(t)
            target["source"] = "manual"
        else:
            disc_mcp.append({
                "name": m["name"],
                "connection": "configured",
                "availability": "configured",
                "tool_names": list(m.get("tool_names", [])),
                "tools": list(m.get("tools", [])),
                "source": "manual",
                "description": m.get("description") or f"Configured MCP server '{m['name']}'",
                "description_source": m.get("description_source", "manual"),
                **({"github_url": m["github_url"]} if m.get("github_url") else {}),
                **({"source_url": m["source_url"]} if m.get("source_url") else {}),
            })
    merged["mcp"] = disc_mcp

    disc_skills = list(merged.get("skills", []))
    skills_by_name = {item["name"].casefold(): item for item in disc_skills if isinstance(item, dict) and "name" in item}

    for s in norm_manual["skills"]:
        name_key = s["name"].casefold()
        if name_key in skills_by_name:
            target = skills_by_name[name_key]
            if s.get("description"):
                target["description"] = s["description"]
            if s.get("path"):
                target["path"] = s["path"]
            target["source"] = "manual"
            if target.get("availability") not in {"configured", "discovered"}:
                target["availability"] = "configured"
        else:
            disc_skills.append({
                "name": s["name"],
                "path": s.get("path", ""),
                "description": s.get("description") or f"Manual skill '{s['name']}'",
                "availability": "configured",
                "source": "manual",
            })
    merged["skills"] = disc_skills

    return validate_inventory_schema(merged)


def validate_inventory_schema(raw: Any) -> Dict[str, Any]:
    """Strictly validates incoming runtime or static inventory before mutation.
    Ensures safe projections and prevents state corruption or credential leakage.
    """
    if not isinstance(raw, dict):
        raise ValueError("Inventory must be a JSON object.")
    for key in ("mcp", "skills", "integrations", "native_tools", "commands"):
        items = raw.get(key, [])
        if not isinstance(items, list):
            raise ValueError(f"Inventory {key} must be an array.")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                raise ValueError(f"Inventory {key} entries require a non-empty name.")
            for field in ("path", "source", "description", "connection", "availability", "type", "github_url", "source_url", "description_source"):
                if field in item and not isinstance(item[field], str):
                    raise ValueError(f"Inventory {key}.{field} must be a string.")
            if "tool_names" in item and (not isinstance(item["tool_names"], list) or any(not isinstance(name, str) for name in item["tool_names"])):
                raise ValueError("MCP tool_names must be an array of strings.")
            if "tools" in item:
                if not isinstance(item["tools"], list):
                    raise ValueError("MCP tools must be an array of tool objects.")
                for t in item["tools"]:
                    if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"].strip():
                        raise ValueError("MCP tool entries require a non-empty string name.")
                    if "description" in t and not isinstance(t["description"], str):
                        raise ValueError("MCP tool description must be a string.")

    source = str(raw.get("source", "omp-runtime")).strip()[:64] or "omp-runtime"

    valid_connections = {"connected", "connecting", "disconnected", "configured", "disabled", "unknown"}
    clean_mcp: List[Dict[str, Any]] = []
    seen_mcp: Set[str] = set()
    for item in raw.get("mcp", []):
        if not isinstance(item, dict):
            continue
        name = item["name"].strip()
        if not name or name.casefold() in seen_mcp:
            continue
        seen_mcp.add(name.casefold())
        conn = str(item.get("connection", "configured")).strip().lower()
        if conn not in valid_connections:
            conn = "configured"
        avail = str(item.get("availability", conn)).strip()

        raw_tools = item.get("tool_names", [])
        tool_names = [str(t).strip() for t in raw_tools if isinstance(t, str) and str(t).strip()] if isinstance(raw_tools, list) else []

        raw_tools_objs = item.get("tools", [])
        clean_tools_objs: List[Dict[str, str]] = []
        if isinstance(raw_tools_objs, list):
            for t in raw_tools_objs:
                if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip():
                    clean_tools_objs.append({
                        "name": t["name"].strip(),
                        "description": str(t.get("description", ""))[:512],
                    })

        desc_raw = str(item.get("description", "")).strip()
        has_custom = bool(desc_raw and not is_generic_placeholder(desc_raw, name))
        meta = get_mcp_metadata(
            name,
            fallback_description=desc_raw if has_custom else "",
            tools=clean_tools_objs if clean_tools_objs else None,
            tool_names=tool_names if tool_names else None,
        )

        final_desc = desc_raw if has_custom else meta["description"]
        final_desc_source = str(item.get("description_source") or ("manual" if has_custom else meta.get("description_source", source)))[:64]
        final_github_url = str(item.get("github_url") or meta.get("github_url", ""))[:512]
        final_source_url = str(item.get("source_url") or meta.get("source_url", ""))[:512]
        final_tools = meta["tools"]
        final_tool_names = tool_names or [t["name"] for t in final_tools]

        clean_entry: Dict[str, Any] = {
            "name": name,
            "id": mcp_node_id(name),
            "connection": conn,
            "availability": avail,
            "tool_names": final_tool_names,
            "tools": final_tools,
            "source": str(item.get("source", source))[:128],
            "description": final_desc[:512],
            "description_source": final_desc_source,
        }
        if final_github_url:
            clean_entry["github_url"] = final_github_url
        if final_source_url:
            clean_entry["source_url"] = final_source_url

        clean_mcp.append(clean_entry)

    valid_availabilities = {"provided", "discovered", "configured", "available", "unavailable"}
    clean_skills: List[Dict[str, Any]] = []
    seen_skills: Set[str] = set()
    for item in raw.get("skills", []):
        if not isinstance(item, dict):
            continue
        name = item["name"].strip()
        if not name or name.casefold() in seen_skills:
            continue
        seen_skills.add(name.casefold())
        avail = str(item.get("availability", "provided")).strip().lower()
        if avail not in valid_availabilities:
            avail = "discovered"
        clean_skills.append({
            "id": skill_node_id(name),
            "name": name,
            "path": str(item.get("path", ""))[:512],
            "description": str(item.get("description", f"Agent skill '{name}'"))[:512],
            "availability": avail,
            "source": str(item.get("source", source))[:128],
        })

    clean_integrations: List[Dict[str, Any]] = []
    seen_integ: Set[str] = set()
    for item in raw.get("integrations", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()[:64]
        if not name or name.casefold() in seen_integ:
            continue
        seen_integ.add(name.casefold())
        avail = str(item.get("availability", "available")).strip().lower()
        if avail not in {"available", "unavailable"}:
            avail = "unavailable"

        desc = str(item.get("description", "")).strip()
        github_url = str(item.get("github_url", ""))[:512]
        source_url = str(item.get("source_url", ""))[:512]
        desc_source = str(item.get("description_source", "system-path"))[:64]
        if name.casefold() == "rtk":
            if not desc or desc.casefold() in {"rtk integration", "cli integration", "cli-wrapper"}:
                desc = "Token-optimized CLI proxy for shell commands with output filtering and token savings analytics"
            if not github_url:
                github_url = "https://github.com/rtk-ai/rtk"
            if not source_url:
                source_url = "https://github.com/rtk-ai/rtk"
            desc_source = "cli-integration"
        elif not desc:
            desc = f"{name} integration"

        clean_integ_entry: Dict[str, Any] = {
            "name": name,
            "id": integration_node_id(name),
            "type": str(item.get("type", "cli-wrapper"))[:64],
            "availability": avail,
            "source": str(item.get("source", "system-path"))[:128],
            "description": desc[:512],
            "description_source": desc_source,
        }
        if github_url:
            clean_integ_entry["github_url"] = github_url
        if source_url:
            clean_integ_entry["source_url"] = source_url

        clean_integrations.append(clean_integ_entry)

    clean_native: List[Dict[str, Any]] = []
    seen_native: Set[str] = set()
    for item in raw.get("native_tools", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip().casefold()[:64]
        if not name or name in seen_native:
            continue
        seen_native.add(name)
        desc = str(item.get("description", "")).strip()[:512]
        if not desc:
            desc = NATIVE_TOOL_DESCRIPTIONS.get(name, f"Native agent tool '{name}'")
        clean_native.append({
            "name": name,
            "description": desc,
            "source": str(item.get("source", "builtin"))[:64],
            **({"availability": str(item["availability"])[:64]} if "availability" in item else {}),
        })
    if "native_tools" not in raw:
        clean_native = list(DEFAULT_NATIVE_TOOLS)
    commands = []
    seen_commands = set()
    for item in raw.get("commands", []):
        name = item["name"].strip()[:160]
        if name in seen_commands:
            continue
        seen_commands.add(name)
        commands.append({"name": name, "description": str(item.get("description", ""))[:512],
                         "source": str(item.get("source", source))[:128],
                         "availability": str(item.get("availability", "configured"))[:64]})
    from .memory_systems import normalize_memory_systems, public_system
    memories = [public_system(item) for item in normalize_memory_systems(raw.get("memory_systems", []))]

    return {
        "source": source,
        "mcp": clean_mcp,
        "skills": clean_skills,
        "integrations": clean_integrations,
        "native_tools": clean_native,
        **({"commands": commands} if "commands" in raw else {}),
        **({"memory_systems": memories} if "memory_systems" in raw else {}),
    }


def capability_nodes(inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Converts a validated capability inventory into graph catalog nodes."""
    nodes: List[Dict[str, Any]] = []

    for item in inventory.get("mcp", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        c_id = mcp_node_id(name)
        tool_names = list(item.get("tool_names", []))
        tools = list(item.get("tools", []))
        connection = str(item.get("connection", "configured"))
        avail = str(item.get("availability", connection))
        node: Dict[str, Any] = {
            "id": c_id,
            "kind": "mcp",
            "name": name,
            "label": name,
            "group": "capabilities",
            "category": "MCP",
            "description": str(item.get("description") or f"MCP server '{name}'"),
            "connection": connection,
            "availability": avail,
            "tool_names": tool_names,
            "tools": tools,
            "match": {"tool_names": tool_names},
            "source": str(item.get("source", "omp-config")),
            "description_source": str(item.get("description_source", "")),
            "status": "idle",
            "reason": "",
            "payload": "",
            "duration_ms": 0.0,
        }
        if item.get("github_url"):
            node["github_url"] = str(item["github_url"])
        if item.get("source_url"):
            node["source_url"] = str(item["source_url"])
        nodes.append(node)

    for item in inventory.get("skills", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        s_id = skill_node_id(name)
        nodes.append({
            "id": s_id,
            "kind": "skill",
            "name": name,
            "label": name,
            "group": "capabilities",
            "category": "Skill",
            "description": str(item.get("description") or f"Agent skill '{name}'"),
            "availability": str(item.get("availability", "discovered")),
            "path": str(item.get("path", "")),
            "source": str(item.get("source", "omp-config")),
            "status": "idle",
            "reason": "",
            "payload": "",
            "duration_ms": 0.0,
        })

    for item in inventory.get("integrations", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        i_id = integration_node_id(name)
        node = {
            "id": i_id,
            "kind": "integration",
            "type": str(item.get("type", "cli-wrapper")),
            "name": name,
            "label": str(item.get("label", name.upper())),
            "group": "capabilities",
            "category": "Integration",
            "description": str(item.get("description", "CLI integration")),
            "availability": str(item.get("availability", "available")),
            "source": str(item.get("source", "system-path")),
            "description_source": str(item.get("description_source", "cli-integration")),
            "status": "idle",
            "reason": "",
            "payload": "",
            "duration_ms": 0.0,
        }
        if item.get("github_url"):
            node["github_url"] = str(item["github_url"])
        if item.get("source_url"):
            node["source_url"] = str(item["source_url"])
        nodes.append(node)

    for item in inventory.get("commands", []):
        nodes.append({"id": "omp_command_" + hashlib.sha1(item["name"].encode()).hexdigest()[:12],
                      "kind": "command", "group": "commands", "category": "OMP command",
                      "label": item["name"], "description": item["description"],
                      "source": item["source"], "availability": item["availability"],
                      "inventory_resource": True, "status": "idle", "reason": "", "payload": ""})
    from .memory_systems import memory_node
    for item in inventory.get("memory_systems", []):
        nodes.append({**memory_node(item), "inventory_resource": True})

    return nodes


def save_capabilities_snapshot(config_dir: Path, inventory: Dict[str, Any]) -> Path:
    """Atomically writes private local static capability snapshot to observer.capabilities.json.
    Restricted 0o600 permissions, validated schema, never contains credentials.
    """
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    target = config_dir / "observer.capabilities.json"
    temp = config_dir / f"observer.capabilities.json.tmp.{os.getpid()}"
    clean_inventory = validate_inventory_schema(inventory)
    clean_inventory["discovery_version"] = DISCOVERY_VERSION

    data_bytes = (json.dumps(clean_inventory, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(target)
    return target


def load_capabilities_snapshot(config_dir: Path) -> Optional[Dict[str, Any]]:
    """Loads private local static capability snapshot if it exists."""
    target = config_dir / "observer.capabilities.json"
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("discovery_version") == DISCOVERY_VERSION:
            return validate_inventory_schema(data)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None
