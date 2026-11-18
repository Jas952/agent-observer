"""Authenticated document operations, restricted to the configured Markdown catalogue."""
import base64
import hashlib
import html
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_document_lock = threading.Lock()
ALLOWED_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".rst", ".ini", ".cfg", ".conf"}

class DocumentError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)

def get_document_context(settings, catalog, relative):
    if not isinstance(relative, str) or not relative.strip():
        raise DocumentError(400, "A valid document path is required.")
    clean_rel = relative.strip()
    clean_sub = clean_rel[len("@vault/"):] if clean_rel.startswith("@vault/") else clean_rel
    if Path(clean_sub).is_absolute() or ".." in Path(clean_sub).parts:
        raise DocumentError(400, "Document path must not contain path traversal.")
    node = next((n for n in catalog.get("nodes", [])
                 if n.get("kind") in {"document", "prompt_source"} and (n.get("path") == clean_rel or n.get("label") == clean_rel)), None)
    if not node:
        raise DocumentError(404, "This document is not in the current project catalogue.")
    if node.get("source_root") == "vault" or clean_rel.startswith("@vault/"):
        vault_str = settings.get("vault_path")
        if not vault_str:
            raise DocumentError(403, "Obsidian vault path is not configured.")
        vault_root = Path(vault_str).expanduser().resolve()
        if not vault_root.is_dir():
            raise DocumentError(404, "Configured vault directory does not exist.")
        sub = clean_rel[len("@vault/"):] if clean_rel.startswith("@vault/") else (node.get("source_relative") or clean_rel)
        target = (vault_root / sub).resolve()
        if not target.is_relative_to(vault_root):
            raise DocumentError(403, "Document escapes the configured vault root.")
        if target.suffix.lower() not in {".md", ".markdown"}:
            raise DocumentError(403, "Only Markdown documents in the vault can be opened.")
        if not target.is_file():
            raise DocumentError(404, "The recorded document no longer exists at this path.")
        return target, vault_root, "markdown", "Obsidian vault file", node
    root = settings["project_root"].resolve()
    sub = node.get("source_relative") or clean_rel
    target = (root / sub).resolve()
    if not target.is_relative_to(root):
        raise DocumentError(403, "Document escapes the configured project root.")
    if node.get("kind") == "prompt_source":
        if target.suffix.lower() not in ALLOWED_TEXT_EXTENSIONS:
            raise DocumentError(403, "Only supported text and Markdown documents can be opened.")
        doc_format = "markdown" if target.suffix.lower() in {".md", ".markdown"} else "text"
        source_label = "Project prompt source"
    else:
        if target.suffix.lower() not in {".md", ".markdown"}:
            raise DocumentError(403, "Only Markdown documents inside this project can be opened.")
        doc_format = "markdown"
        source_label = "Current project file"
    if not target.is_file():
        raise DocumentError(404, "The recorded document no longer exists at this path.")
    return target, root, doc_format, source_label, node

def resolve_document(settings, catalog, relative):
    target, _, _, _, _ = get_document_context(settings, catalog, relative)
    return target
def _read(path):
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise DocumentError(413, "This document exceeds the 2 MB editor limit; no text was truncated.")
        return data.decode("utf-8"), data
    except UnicodeDecodeError:
        raise DocumentError(415, "The editor supports UTF-8 Markdown documents.")
    except OSError:
        raise DocumentError(403, "The document cannot be read with the current permissions.")

def render_markdown(content, relative, root):
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        raise DocumentError(503, "Install the updated Observer requirements, then restart Observer.")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise DocumentError(413, "The document exceeds the 2 MB editor limit.")
    md = MarkdownIt("js-default", {"html": False})
    image_budget = [0]
    def image(tokens, index, options, env):
        token = tokens[index]
        src, alt = token.attrGet("src") or "", token.content
        parsed = urlparse(src)
        if not parsed.scheme and not parsed.netloc:
            target = (root / Path(relative).parent / unquote(parsed.path)).resolve()
            types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
            mime = types.get(target.suffix.lower())
            try:
                if mime and target.is_relative_to(root) and target.is_file() and target.stat().st_size <= MAX_DOCUMENT_BYTES and image_budget[0] < 8 * MAX_DOCUMENT_BYTES:
                    data = target.read_bytes(); image_budget[0] += len(data)
                    return '<img alt="' + html.escape(alt, quote=True) + '" src="data:' + mime + ';base64,' + base64.b64encode(data).decode() + '">'
            except OSError:
                pass
        if md.validateLink(src) and parsed.scheme in {"http", "https"}:
            return '<a href="' + html.escape(src, quote=True) + '" class="md-image-link">Image: ' + html.escape(alt or src) + '</a>'
        return '<span class="md-image-link">Image: ' + html.escape(alt or src) + '</span>'
    md.renderer.rules["image"] = image
    metadata = ""
    lines = content.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            metadata = '<details class="md-frontmatter"><summary>Document metadata</summary><pre>' + html.escape("".join(lines[1:end])) + '</pre></details>'
            content = "".join(lines[end + 1:])
    tokens = md.parse(content)
    used = {}
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and i + 1 < len(tokens):
            slug = re.sub(r"[^\w-]+", "-", tokens[i + 1].content.casefold()).strip("-") or "heading"
            count = used.get(slug, 0); used[slug] = count + 1
            token.attrSet("id", "md-" + slug + ("-" + str(count) if count else ""))
    return metadata + md.renderer.render(tokens, md.options, {})

def document_payload(settings, catalog, relative):
    target, root, doc_format, source_label, node = get_document_context(settings, catalog, relative)
    content, data = _read(target)
    if doc_format == "markdown":
        doc_rel = relative[len("@vault/"):] if relative.startswith("@vault/") else relative
        html_val = render_markdown(content, doc_rel, root)
    else:
        html_val = '<pre class="observer-text-preview">' + html.escape(content) + '</pre>'
    return {"path": relative, "content": content, "revision": hashlib.sha256(data).hexdigest(), "html": html_val, "source": source_label, "editable": True, "format": doc_format}

def save_document(settings, catalog, relative, content, revision):
    if not isinstance(content, str) or not isinstance(revision, str):
        raise DocumentError(400, "Content and the original document revision are required.")
    data = content.encode("utf-8")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError(413, "The document exceeds the 2 MB editor limit.")
    target, root, doc_format, source_label, node = get_document_context(settings, catalog, relative)
    if doc_format == "markdown":
        doc_rel = relative[len("@vault/"):] if relative.startswith("@vault/") else relative
        rendered = render_markdown(content, doc_rel, root)
    else:
        rendered = '<pre class="observer-text-preview">' + html.escape(content) + '</pre>'
    with _document_lock:
        path, _, _, _, _ = get_document_context(settings, catalog, relative)
        _, previous = _read(path)
        if hashlib.sha256(previous).hexdigest() != revision:
            raise DocumentError(409, "This file changed on disk. Reload it before saving; your draft is still in the editor.")
        backup_dir = settings["storage"] / "document-backups" / hashlib.sha256(relative.encode()).hexdigest()[:20]
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = backup_dir / (revision + path.suffix)
        if not backup.exists():
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(previous)
        temporary = None
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name); os.fchmod(stream.fileno(), mode)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(path)
        except OSError:
            raise DocumentError(403, "The document could not be saved. The original file and your draft were kept.")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {"path": relative, "content": content, "revision": hashlib.sha256(data).hexdigest(), "html": rendered, "source": source_label, "editable": True, "format": doc_format}

def _atomic_move_no_overwrite(src: Path, dest: Path):
    import errno, stat, shutil
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    try:
        os.link(str(src), str(dest))
        os.unlink(str(src))
        return
    except FileExistsError:
        raise DocumentError(409, "Destination file already exists; move will not overwrite.")
    except OSError as err:
        if err.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP}:
            if dest.exists():
                raise DocumentError(409, "Destination file already exists; move will not overwrite.")
            raise
    mode = stat.S_IMODE(src.stat().st_mode)
    try:
        fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        raise DocumentError(409, "Destination file already exists; move will not overwrite.")
    try:
        with os.fdopen(fd, "wb") as out_f, src.open("rb") as in_f:
            shutil.copyfileobj(in_f, out_f)
            out_f.flush()
            os.fsync(fd)
        src.unlink()
    except Exception:
        dest.unlink(missing_ok=True)
        raise

def _is_dest_covered(dest_path: Path, doc_sources: list, root: Path) -> bool:
    for d in doc_sources:
        if not isinstance(d, dict) or not isinstance(d.get("path"), str):
            continue
        s_loc = (root / d["path"]).resolve()
        if s_loc.is_file():
            if s_loc == dest_path.resolve():
                return True
        elif s_loc.is_dir():
            if dest_path.resolve().is_relative_to(s_loc):
                for pattern in d.get("include", ["**/*.md"]):
                    if any(candidate.resolve() == dest_path for candidate in s_loc.glob(pattern)):
                        return True
    return False

def move_document(settings, catalog, path, destination, revision):
    from .observer_config import resource_catalog, save_block_settings, get_config_revision
    if not isinstance(path, str) or not path.strip():
        raise DocumentError(400, "Source document path is required.")
    if not isinstance(destination, str) or not destination.strip():
        raise DocumentError(400, "Destination path is required.")
    if not isinstance(revision, str) or not revision.strip():
        raise DocumentError(400, "Revision is required to prevent moving an out-of-date document.")
    clean_dest = destination.strip()
    if clean_dest.startswith("@vault/"):
        clean_dest = clean_dest[len("@vault/"):].lstrip("/")
    if Path(clean_dest).is_absolute() or ".." in Path(clean_dest).parts:
        raise DocumentError(400, "Destination must be a relative path without traversal.")
    if not clean_dest:
        raise DocumentError(400, "Destination must specify a file path.")
    target, root, doc_format, source_label, node = get_document_context(settings, catalog, path)
    source_sub = node.get("source_relative") or (path[len("@vault/"):] if path.startswith("@vault/") else path)
    source_entry = root / source_sub
    if source_entry.is_symlink() or os.path.islink(str(source_entry)):
        raise DocumentError(400, "Cannot move a symlink document.")
    curr_src = source_entry.parent
    while curr_src != root and curr_src.exists():
        if curr_src.is_symlink() and not curr_src.resolve().is_relative_to(root):
            raise DocumentError(403, "Source document parent escapes root via symlink.")
        curr_src = curr_src.parent
    _, previous = _read(target)
    if hashlib.sha256(previous).hexdigest() != revision:
        raise DocumentError(409, "This file changed on disk. Reload it before moving; your draft is still in the editor.")
    dest_entry = root / clean_dest
    if dest_entry.is_symlink() or os.path.islink(str(dest_entry)):
        raise DocumentError(409, "Destination already exists; move will not overwrite.")
    dest_path = dest_entry.resolve()
    if not dest_path.is_relative_to(root):
        raise DocumentError(403, "Destination must be inside the same source root.")
    curr = dest_entry.parent
    while curr != root and curr.exists():
        if curr.is_symlink() and not curr.resolve().is_relative_to(root):
            raise DocumentError(403, "Destination traverses symlink outside source root.")
        curr = curr.parent
    allowed_suffixes = {".md", ".markdown"} if doc_format == "markdown" else ALLOWED_TEXT_EXTENSIONS
    if dest_path.suffix.lower() not in allowed_suffixes:
        raise DocumentError(400, f"Destination file must have a supported extension ({', '.join(sorted(allowed_suffixes))}).")
    if dest_path.exists():
        raise DocumentError(409, "Destination file already exists; move will not overwrite.")
    if target.resolve() == dest_path:
        raise DocumentError(400, "Source and destination are the same file.")
    # Detect exact sources BEFORE moving file on disk (dual-use files can exist in both)
    exact_doc_idx = None
    exact_prompt_idx = None
    if node.get("source_root") == "project":
        for idx, d in enumerate(settings.get("documents", [])):
            if isinstance(d, dict) and isinstance(d.get("path"), str):
                d_loc = (root / d["path"]).resolve()
                if d_loc.is_file() and d_loc == target.resolve():
                    exact_doc_idx = idx
                    break
        for idx, p in enumerate(settings.get("prompt_sources", [])):
            if isinstance(p, dict) and isinstance(p.get("path"), str):
                p_loc = (root / p["path"]).resolve()
                if p_loc.is_file() and p_loc == target.resolve():
                    exact_prompt_idx = idx
                    break
    with _document_lock:
        if not target.is_file():
            raise DocumentError(404, "Source document no longer exists on disk.")
        _, cur_bytes = _read(target)
        if hashlib.sha256(cur_bytes).hexdigest() != revision:
            raise DocumentError(409, "This file changed on disk. Reload it before moving; your draft is still in the editor.")
        backup_dir = settings["storage"] / "document-backups" / hashlib.sha256(path.encode()).hexdigest()[:20]
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = backup_dir / (revision + target.suffix)
        if not backup.exists():
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(cur_bytes)
        _atomic_move_no_overwrite(target, dest_path)

    is_vault = (node.get("source_root") == "vault" or path.startswith("@vault/"))
    new_token = f"@vault/{clean_dest}" if is_vault else clean_dest

    # Commit registration with rollback on failure
    original_prompts = [dict(p) for p in settings.get("prompt_sources", [])]
    original_docs = [dict(d) for d in settings.get("documents", [])]
    try:
        config_updates = {}
        if node.get("source_root") == "project":
            # Update prompt sources if registered (dual-use support)
            prompts = [dict(p) for p in settings.get("prompt_sources", [])]
            prompt_updated = False
            if exact_prompt_idx is not None and exact_prompt_idx < len(prompts):
                prompts[exact_prompt_idx]["path"] = clean_dest
                prompt_updated = True
            else:
                for p in prompts:
                    p_loc = (root / p.get("path", "")).resolve()
                    if p_loc == target.resolve() or p.get("path") in {path, node.get("path"), node.get("source_relative")}:
                        p["path"] = clean_dest
                        prompt_updated = True
            if prompt_updated:
                config_updates["prompt_sources"] = prompts

            # Preserve registrations without indexing unrelated destination files.
            docs = [dict(d) for d in settings.get("documents", [])]
            doc_updated = False
            if exact_doc_idx is not None and exact_doc_idx < len(docs):
                docs[exact_doc_idx]["path"] = clean_dest
                doc_updated = True
            elif not _is_dest_covered(dest_path, docs, root):
                docs.append({"path": clean_dest})
                doc_updated = True
            if doc_updated:
                config_updates["documents"] = docs

            if config_updates:
                config_updates["revision"] = get_config_revision(settings["config_file"])
                save_block_settings(settings, config_updates)
        new_catalog = resource_catalog(settings)
        new_payload = document_payload(settings, new_catalog, new_token)
        return {**new_payload, "catalog": new_catalog}
    except Exception as err:
        # Rollback: move dest_path back to target
        with _document_lock:
            try:
                if dest_path.is_file() and not target.exists():
                    _atomic_move_no_overwrite(dest_path, target)
            except Exception:
                pass
            settings["prompt_sources"] = original_prompts
            settings["documents"] = original_docs
        if isinstance(err, DocumentError):
            raise
        raise DocumentError(500, f"Move failed during catalogue registration: {err}")
