"""Private local-client credential shared by Swift and Python emitters."""
import os
import secrets
from pathlib import Path


def token_path():
    return Path(os.environ.get("CAD_XRAY_TOKEN_FILE", str(Path.home() / "Library/Application Support/CADStudio/xray-token")))


def load_token(create=False, path=None):
    path = Path(path).expanduser() if path is not None else token_path()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or path.is_symlink():
        raise PermissionError("X-Ray token must be a private, owned regular file")
    return path.read_text().strip()


def event_headers():
    return {"Content-Type": "application/json", "X-CAD-Token": load_token()}
