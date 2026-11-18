"""Configured memory resources and bounded, read-only metadata inspection."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from http.client import HTTPException
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _text(value, label, *, optional=False):
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError(f"Memory {label} must be a non-empty text value.")
    return value.strip()


def _endpoint(value, *, optional=False):
    value = _text(value, "endpoint", optional=optional)
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None
                 and parsed.password is None and not parsed.query and not parsed.fragment
                 and not any(c.isspace() for c in value) and parsed.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Memory endpoints require HTTP(S) URLs without credentials, queries or fragments.")
    return value.rstrip("/")


def _github_url(value, *, optional=True):
    value = _text(value, "GitHub URL", optional=optional)
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname in {"github.com", "www.github.com", "gist.github.com"}
                 and parsed.port in {None, 443} and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment
                 and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("GitHub URL must be an HTTPS URL without credentials, queries or fragments.")
    return value.rstrip("/")

def _strings(value, label, limit=64):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"Memory {label} must be a list of at most {limit} text values.")
    return list(dict.fromkeys(_text(item, label) for item in value))


def _fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"Invalid memory {label} fields. Store credentials only in a private token file.")


def normalize_memory_systems(raw):
    if not isinstance(raw, list) or len(raw) > 16:
        raise ValueError("'memory_systems' must be a list of at most 16 systems.")
    result, ids = [], set()
    for item in raw:
        _fields(item, {"id", "name", "provider", "endpoint", "experience_endpoint", "bank_ids", "token_file", "categories", "stores", "match", "github_url", "model", "enabled", "availability", "source", "description"}, "system")
        identity = _text(item.get("id"), "system ID")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", identity) or identity in ids:
            raise ValueError("Memory system IDs must be unique safe identifiers.")
        ids.add(identity)
        provider = item.get("provider", "hindsight")
        if provider not in {"hindsight", "custom"}:
            raise ValueError("Choose a Hindsight or manually described memory system.")
        categories = item.get("categories", [])
        stores = item.get("stores", [])
        if not isinstance(categories, list) or len(categories) > 256 or not isinstance(stores, list) or len(stores) > 16:
            raise ValueError("Memory categories or stores exceed their supported limits.")
        clean_categories, category_ids = [], set()
        for category in categories:
            _fields(category, {"id", "label", "description", "tags", "parent_id", "parent_slug_path"}, "category")
            cid = _text(category.get("id"), "category ID")
            if cid in category_ids:
                raise ValueError("Memory category IDs must be unique within a system.")
            category_ids.add(cid)
            parent_id = _text(category.get("parent_id") or category.get("parent_slug_path", ""), "category parent ID", optional=True) or None
            if not parent_id and "/" in cid:
                parent_id = cid.rsplit("/", 1)[0]
            clean_categories.append({"id": cid, "label": _text(category.get("label"), "category label"),
                                     "description": _text(category.get("description", ""), "category description", optional=True),
                                     "parent_id": parent_id,
                                     "tags": _strings(category.get("tags", []), "category tags")})
        clean_stores, store_ids = [], set()
        for store in stores:
            _fields(store, {"id", "label", "engine", "path", "host", "port", "database", "schema", "tables", "role"}, "store")
            sid = _text(store.get("id"), "store ID")
            if sid in store_ids:
                raise ValueError("Memory store IDs must be unique within a system.")
            store_ids.add(sid)
            engine = store.get("engine")
            if engine not in {"postgresql", "sqlite", "other"}:
                raise ValueError("Choose PostgreSQL, SQLite or another described storage engine.")
            clean = {"id": sid, "label": _text(store.get("label"), "store label"), "engine": engine,
                     "role": store.get("role", "primary")}
            if clean["role"] not in {"primary", "index", "legacy"}:
                raise ValueError("Memory store role must be primary, index or legacy.")
            for key in ("path", "host", "database", "schema"):
                if key in store:
                    clean[key] = _text(store[key], f"store {key}", optional=True)
                    if key in {"database", "schema"} and clean[key] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", clean[key]):
                        raise ValueError("Memory database and schema fields accept identifiers, not connection strings.")
                    if key == "host" and clean[key] and not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", clean[key]):
                        raise ValueError("Memory store host must not contain credentials or a connection string.")
            if "port" in store:
                port = store["port"]
                if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                    raise ValueError("Memory store port must be an integer from 1 through 65535.")
                clean["port"] = port
            if "tables" in store:
                clean["tables"] = _strings(store["tables"], "table names")
            clean_stores.append(clean)
        match = item.get("match", {})
        _fields(match, {"tool_names"}, "match")
        github_val = item.get("github_url")
        if provider == "hindsight":
            if github_val is None or "github_url" not in item or github_val == "":
                github_url = "https://github.com/vectorize-io/hindsight"
            else:
                github_url = _github_url(github_val, optional=True)
        else:
            github_url = _github_url(github_val, optional=True)
        model_val = item.get("model")
        clean_model = None
        if model_val is not None:
            _fields(model_val, {"name", "base_url", "provider"}, "model")
            clean_model = {
                "name": _text(model_val.get("name", ""), "model name", optional=True),
                "base_url": _endpoint(model_val.get("base_url", ""), optional=True),
                "provider": _text(model_val.get("provider", ""), "model provider", optional=True),
            }
        system = {"id": identity, "name": _text(item.get("name"), "name"), "provider": provider,
                  "endpoint": _endpoint(item.get("endpoint", ""), optional=provider == "custom"),
                  "experience_endpoint": _endpoint(item.get("experience_endpoint", ""), optional=True),
                  "bank_ids": _strings(item.get("bank_ids", []), "bank IDs", 8),
                  "token_file": _text(item.get("token_file", ""), "token file path", optional=True),
                  "github_url": github_url,
                  "model": clean_model,
                  "categories": clean_categories, "stores": clean_stores,
                  "match": {"tool_names": _strings(match.get("tool_names", []), "tool names")}}
        if provider != "hindsight" and system["experience_endpoint"]:
            raise ValueError("The CAD experience endpoint belongs to a Hindsight integration.")
        if provider == "hindsight" and any(urlsplit(value).hostname not in {"127.0.0.1", "localhost", "::1"} for value in (system["endpoint"], system["experience_endpoint"]) if value):
            raise ValueError("Automatic memory inspection requires local loopback endpoints; use a manual system for remote descriptions.")
        if "enabled" in item:
            if not isinstance(item["enabled"], bool):
                raise ValueError("Memory enabled must be boolean.")
            system["enabled"] = item["enabled"]
        for key in ("availability", "source", "description"):
            if key in item:
                system[key] = _text(item[key], key, optional=True)
        result.append(system)
    return result


def public_system(system):
    return {key: value for key, value in system.items() if key != "token_file"}


def memory_node(system):
    return {"id": "memory_" + hashlib.sha1(system["id"].encode()).hexdigest()[:12],
            "kind": "memory", "group": "memory", "category": "Memory systems", "label": system["name"],
            "memory_system_id": system["id"], "provider": system["provider"], "endpoint": system["endpoint"],
            "bank_ids": system["bank_ids"], "github_url": system.get("github_url", ""),
            "model": system.get("model"), "match": system["match"],
            "description": system.get("description") or ("Long-term experience memory" if system["provider"] == "hindsight" else "Configured agent memory"),
            **{key: system[key] for key in ("enabled", "availability", "source") if key in system},
            "status": "idle", "reason": "", "payload": ""}


def matches_memory(node, event):
    """Require actual operation and unambiguous ownership, never a mention of memory."""
    if node.get("enabled") is False:
        return False
    operation = event.get("memory_operation")
    if operation not in ("recall", "retain", "reflect", "learn"):
        return False
    identity, endpoint, bank = (event.get(key) for key in ("memory_system_id", "memory_endpoint", "memory_bank_id"))
    if identity and identity != node.get("memory_system_id"):
        return False
    if endpoint and str(endpoint).rstrip("/") != node.get("endpoint"):
        return False
    if bank and node.get("bank_ids") and bank not in node["bank_ids"]:
        return False
    name = str(event.get("tool") or event.get("tool_name") or "").lower()
    aliases = {str(value).lower() for value in node.get("match", {}).get("tool_names", [])}
    native_name = re.sub(r"^(?:functions|collaboration)\.", "", name)
    qualified_alias = name in aliases and any(separator in name for separator in "._:/") and native_name not in {"read", "write", "recall", "retain", "reflect", "learn"}
    return bool(identity or endpoint and bank or qualified_alias)


def resolve_memory(nodes, event):
    matches = [node for node in nodes if node.get("kind") == "memory" and matches_memory(node, event)]
    return matches[0] if len(matches) == 1 else None


class _MetadataError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def _get_json(endpoint, path, token, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _MetadataError("unavailable", "Metadata inspection deadline reached.")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
        headers["X-CAD-Memory-Token"] = token
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(endpoint + path, headers=headers)
    try:
        with opener.open(request, timeout=min(2.0, remaining)) as response:
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise _MetadataError("unavailable", "Metadata inspection deadline reached.")
                chunk = response.read1(min(65536, 262145 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > 262144:
                    raise _MetadataError("unavailable", "Metadata response exceeds the inspection limit.")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            return data
    except HTTPError as error:
        status = "unauthorized" if error.code in {401, 403} else "not_found" if error.code == 404 else "unavailable"
        error.close()
        raise _MetadataError(status, "Metadata authentication required." if status == "unauthorized" else
                             "Configured bank has not been created." if status == "not_found" and "/banks/" in path else "Metadata API endpoint was not found." if status == "not_found" else "Metadata API request failed.") from None
    except (URLError, OSError, ValueError, HTTPException, RecursionError):
        raise _MetadataError("unavailable", "Metadata API is unreachable or returned invalid JSON.") from None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _counts(value):
    return {str(key)[:128]: count for key, item in value.items() if (count := _count(item)) is not None} if isinstance(value, dict) else {}


def _categories(raw):
    result = []
    seen = set()
    def visit(items, depth=0):
        if not isinstance(items, list) or depth > 12:
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            identity = item.get("slug_path") if isinstance(item.get("slug_path"), str) else item.get("id") if isinstance(item.get("id"), str) else None
            label = item.get("name") if isinstance(item.get("name"), str) else item.get("label") if isinstance(item.get("label"), str) else None
            if identity and label:
                clean_id = identity[:2048]
                if clean_id not in seen:
                    seen.add(clean_id)
                    parent_id = item.get("parent_slug_path") if isinstance(item.get("parent_slug_path"), str) else item.get("parent_id") if isinstance(item.get("parent_id"), str) else None
                    if not parent_id and "/" in clean_id:
                        parent_id = clean_id.rsplit("/", 1)[0]
                    result.append({"id": clean_id, "label": label[:2048],
                                   "description": str(item.get("description") or "")[:2048],
                                   "parent_id": parent_id[:2048] if parent_id else None,
                                   "lessons_count": _count(item.get("lessons_count")),
                                   "tags": []})
            visit(item.get("children"), depth + 1)
    visit(raw)
    return result


def _inspect(system, root, deadline):
    result = {**public_system(system), "node_id": memory_node(system)["id"], "status": "configured",
              "checked_at": datetime.now(timezone.utc).isoformat(), "banks": [],
              "categories_source": "configuration", "stores": [{**store, "status": "configured", "evidence": "configuration"} for store in system["stores"]]}
    if system.get("enabled") is False:
        result["status"] = "disabled"
        return result
    if system["provider"] == "custom":
        return result
    token = ""
    try:
        if system["token_file"]:
            path = Path(system["token_file"]).expanduser()
            if not path.is_absolute():
                path = root / path
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                raise OSError()
            with path.open("r", encoding="utf-8") as stream:
                token = stream.read(8193).strip()
            if not token or len(token) > 8192 or any(not 32 < ord(char) < 127 for char in token):
                raise OSError()
    except (OSError, UnicodeError):
        result.update(status="unauthorized", error="The configured private token file cannot be used.")
        return result
    try:
        listing = _get_json(system["endpoint"], "/v1/default/banks?limit=100&offset=0", token, deadline)
        if not isinstance(listing.get("banks"), list):
            raise _MetadataError("unavailable", "Memory API returned an unsupported bank listing.")
        result["status"] = "available"
    except _MetadataError as error:
        result.update(status=error.status if error.status != "not_found" else "unavailable", error=error.message)
        listing = {"banks": []}
    names = {item["bank_id"]: item["name"][:2048] for item in listing["banks"] if isinstance(item, dict) and isinstance(item.get("bank_id"), str) and isinstance(item.get("name"), str)}
    discovered = [item["bank_id"] for item in listing["banks"] if isinstance(item, dict) and isinstance(item.get("bank_id"), str)]
    bank_ids = system["bank_ids"] or discovered[:8]
    result["banks_total"] = _count(listing.get("total"))
    result["banks_truncated"] = not system["bank_ids"] and (len(discovered) > 8 or result["banks_total"] is not None and result["banks_total"] > len(bank_ids))
    for bank_id in bank_ids:
        bank = {"id": bank_id, "name": names.get(bank_id) or bank_id, "status": "available", "stats": None, "memory_types": [], "tags": [], "tags_total": None, "tags_truncated": False}
        if result["status"] != "available":
            bank.update(status=result["status"], error=result.get("error", "Metadata inspection unavailable."))
            result["banks"].append(bank)
            continue
        path = "/v1/default/banks/" + quote(bank_id, safe="")
        try:
            stats = _get_json(system["endpoint"], path + "/stats", token, deadline)
            if stats.get("bank_id") != bank_id or "total_nodes" not in stats or not isinstance(stats.get("nodes_by_fact_type"), dict):
                raise _MetadataError("unavailable", "Memory API returned unsupported bank statistics.")
            bank["stats"] = {key: _count(stats.get(key)) for key in ("total_nodes", "total_links", "total_documents", "pending_operations", "failed_operations", "pending_consolidation", "failed_consolidation", "total_observations")}
            bank["stats"]["nodes_by_fact_type"] = _counts(stats.get("nodes_by_fact_type"))
            bank["stats"]["operations_by_status"] = _counts(stats.get("operations_by_status"))
            bank["memory_types"] = [{"id": key, "count": value} for key, value in bank["stats"]["nodes_by_fact_type"].items()]
            tags = _get_json(system["endpoint"], path + "/tags?limit=100&offset=0", token, deadline)
            if not isinstance(tags.get("items"), list):
                raise _MetadataError("unavailable", "Memory API returned unsupported tags.")
            bank["tags"] = [{"tag": item["tag"][:2048], "count": _count(item.get("count"))} for item in tags.get("items", []) if isinstance(item, dict) and isinstance(item.get("tag"), str)][:100]
            bank["tags_total"] = _count(tags.get("total"))
            bank["tags_truncated"] = bank["tags_total"] is not None and bank["tags_total"] > len(bank["tags"])
        except _MetadataError as error:
            bank.update(status=error.status, error=error.message)
        result["banks"].append(bank)
    if system.get("model"):
        result["model"] = system["model"]
    else:
        result["model"] = None
    if system["experience_endpoint"]:
        try:
            taxonomy = _get_json(system["experience_endpoint"], "/categories", token, deadline)
            if not isinstance(taxonomy.get("categories"), list):
                raise _MetadataError("unavailable", "Experience API returned unsupported categories.")
            result["categories"] = _categories(taxonomy["categories"])
            result["categories_source"] = "CAD experience API"
            assignment_counts = [c["lessons_count"] for c in result["categories"] if c.get("lessons_count") is not None]
            result["taxonomy_assignments_total"] = sum(assignment_counts) if len(assignment_counts) == len(result["categories"]) else None
        except _MetadataError as error:
            result["categories_error"] = error.message
            result["taxonomy_assignments_total"] = None
        try:
            health = _get_json(system["experience_endpoint"], "/health", token, deadline)
            if isinstance(health, dict):
                result["taxonomy_unique_lessons"] = _count(health.get("lessons_count"))
                result["taxonomy_indexed_lessons"] = _count(health.get("indexed_count"))
                result["taxonomy_unindexed_lessons"] = _count(health.get("unindexed_count"))
                result["taxonomy_categories_count"] = _count(health.get("categories_count"))
                result["taxonomy_status"] = str(health.get("status") or "")
        except _MetadataError:
            result["taxonomy_unique_lessons"] = None
    return result


def inspect_memory_systems(settings):
    systems = normalize_memory_systems(settings.get("memory_systems", []))
    if not systems:
        return {"systems": []}
    deadline = time.monotonic() + 8.0
    root = Path(settings["project_root"])
    with ThreadPoolExecutor(max_workers=min(4, len(systems)), thread_name_prefix="memory-metadata") as pool:
        results = list(pool.map(lambda system: _inspect(system, root, deadline), systems))
    return {"systems": results}
