"""Bounded non-executing local tool discovery for Agent Observer."""
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


DISCOVERY_CANDIDATE_EXECUTABLES = [
    # CAD / 3D
    "openscad", "blender", "freecad",
    # Development / version control
    "git", "python3", "python", "node", "npm", "cargo", "rustc", "docker", "rtk",
    # Utilities
    "curl", "jq", "gh", "code",
]


MAX_FILE_BYTES = 64 * 1024
MAX_DISCOVERED_TOTAL = 100
MAX_PER_SOURCE = 20

def _tool_id(name: str) -> str:
    return "tool_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]

def _read_bounded(path: Path, max_bytes: int = MAX_FILE_BYTES) -> Optional[str]:
    try:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None

def discover_tools(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Discover tools locally without executing any commands or network requests.

    Bounded file reads and count limits. Never returns secrets, env vars, or arguments.
    """
    root = Path(settings.get("project_root", ".")).resolve()
    tools: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

    # 1. Configured tools in settings
    for tool in settings.get("tools", []):
        if len(tools) >= MAX_DISCOVERED_TOTAL: break
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name", "")).strip()
        if not name:
            continue
        seen_names.add(name.casefold())
        existing_match = tool.get("match") if isinstance(tool.get("match"), dict) else {}
        match_obj = {
            "tool_names": existing_match.get("tool_names", [name]),
            **({"command_prefixes": existing_match["command_prefixes"]} if "command_prefixes" in existing_match else {})
        }
        tools.append({
            "name": name,
            "id": str(tool.get("id") or _tool_id(name)),
            "description": str(tool.get("description") or "Configured tool in observer settings."),
            "match": match_obj,
            "category": str(tool.get("category", "Tool")),
            "group": str(tool.get("group", "Tools & services")),
            "source": "observer.project.json",
            "evidence": "configured",
        })

    # 2. Configured tools in tools_markdown (if configured)
    tools_file = settings.get("tools_markdown")
    if tools_file and len(tools) < MAX_DISCOVERED_TOTAL:
        t_path = (root / tools_file).resolve()
        if t_path.is_relative_to(root) and t_path.is_file():
            content = _read_bounded(t_path)
            if content:
                import re
                count = 0
                for line in content.splitlines():
                    if count >= MAX_PER_SOURCE or len(tools) >= MAX_DISCOVERED_TOTAL: break
                    match = re.match(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
                    if match:
                        name, command, purpose = (part.strip().strip("`") for part in match.groups())
                        if name and name.casefold() not in seen_names:
                            seen_names.add(name.casefold())
                            count += 1
                            tools.append({
                                "name": name,
                                "id": _tool_id(name),
                                "description": f"{purpose} · {command}",
                                "match": {"tool_names": [name], "command_prefixes": [command.split()[0] if command else name]},
                                "category": "Tool",
                                "group": "Tools & services",
                                "source": str(tools_file),
                                "evidence": "configured",
                                "immutable": True,
                            })

    # 3. Executable availability on PATH (shutil.which only, NEVER executed)
    for candidate in DISCOVERY_CANDIDATE_EXECUTABLES:
        if len(tools) >= MAX_DISCOVERED_TOTAL: break
        if candidate.casefold() in seen_names:
            continue
        which_path = shutil.which(candidate)
        if which_path:
            seen_names.add(candidate.casefold())
            tools.append({
                "name": candidate,
                "id": _tool_id(candidate),
                "description": f"System executable available on PATH ({candidate}).",
                "match": {"tool_names": [candidate], "command_prefixes": [candidate]},
                "category": "System",
                "group": "Discovered tools",
                "source": "PATH",
                "evidence": "available",
            })

    # 4. Conventional package.json scripts (declarative inspection only, no execution)
    pkg_json = root / "package.json"
    if pkg_json.is_file() and pkg_json.resolve().is_relative_to(root) and len(tools) < MAX_DISCOVERED_TOTAL:
        content = _read_bounded(pkg_json)
        if content:
            try:
                pkg_data = json.loads(content)
                scripts = pkg_data.get("scripts") if isinstance(pkg_data, dict) else None
                if isinstance(scripts, dict):
                    count = 0
                    for script_name in sorted(scripts.keys()):
                        if count >= MAX_PER_SOURCE or len(tools) >= MAX_DISCOVERED_TOTAL: break
                        tool_name = f"npm:{script_name}"
                        if tool_name.casefold() not in seen_names:
                            seen_names.add(tool_name.casefold())
                            count += 1
                            tools.append({
                                "name": tool_name,
                                "id": _tool_id(tool_name),
                                "description": f"Project npm script declared in package.json ({script_name}).",
                                "match": {"tool_names": [tool_name], "command_prefixes": [f"npm run {script_name}"]},
                                "category": "npm",
                                "group": "Discovered tools",
                                "source": "package.json",
                                "evidence": "available",
                            })
            except json.JSONDecodeError:
                pass

    return {
        "tools": tools,
        "tools_markdown": str(settings.get("tools_markdown") or ""),
        "notes": (
            "Bounded local discovery from project configuration, conventional manifests, and executable PATH availability. "
            "Tools marked 'configured' or 'available' represent discovery evidence only; usage is not claimed or verified until "
            "observed during an active session. No commands were executed and no secrets were scanned."
        )
    }
