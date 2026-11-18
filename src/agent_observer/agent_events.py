"""Durable, paged source-event history, separate from expensive graph snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from contextlib import closing


_SECRET_KEYS = {"apikey", "authorization", "proxyauthorization", "password", "passwd", "secret", "clientsecret", "token", "accesstoken", "refreshtoken", "idtoken", "authtoken", "headers", "env", "environment", "credentials", "auth", "authentication", "bearer", "cookie", "setcookie"}
_PRIVATE_KEYS = {"reasoning", "reasoningcontent", "reasoningtext", "thinking", "analysis", "encryptedcontent", "reasoningsummary", "chainofthought", "thought", "thoughts"}
_SECRET_VALUE = re.compile(r"(?i)(\b(?:api[_ -]?key|access_token|refresh_token|id_token|authorization|password|secret|token)[\"']?\s*[:=]\s*)([\"'])(.*?)\2")
_BARE_SECRET = re.compile(r"(?i)(\b(?:api[_ -]?key|access_token|refresh_token|id_token|authorization|password|secret|token)[\"']?\s*[:=]\s*)[^\s\"'&,]+")


def sanitize_event_data(value, *, name="", secrets=()):
    """Preserve event identity, never persist auth or private reasoning payloads."""
    truncated = False
    private_event = bool(re.search(r"reasoning|thinking|analysis(?:/|_|$)", name, re.I))
    auth_event = bool(re.search(r"(?:account/)?login|authentication|auth/token", name, re.I))
    metadata_keys = {"id", "itemId", "threadId", "turnId", "agent_id", "session_id", "type", "status", "timestamp", "reason", "agentId"}

    def visit(item, depth=0):
        nonlocal truncated
        if depth > 24:
            truncated = True
            return {"truncated": True}
        if isinstance(item, dict):
            private_item = str(item.get("type", "")) in {"reasoning", "thinking"} or str(item.get("channel", "")).casefold() == "analysis"
            output = {}
            for key, data in item.items():
                normalized = re.sub(r"[_\- ]", "", str(key)).casefold()
                secret_key = normalized in _SECRET_KEYS or any(part in normalized for part in ("password", "passwd", "apikey", "privatekey", "secret", "credential")) or normalized.endswith("token")
                if secret_key or normalized in _PRIVATE_KEYS:
                    output[str(key)] = "[redacted]"
                elif private_item and key not in metadata_keys:
                    output[str(key)] = "[private reasoning omitted]"
                else:
                    output[str(key)] = visit(data, depth + 1)
            return output
        if isinstance(item, list):
            output = [visit(entry, depth + 1) for entry in item[:2048]]
            if len(item) > 2048:
                truncated = True
                output.append({"truncated": True, "omitted_items": len(item) - 2048})
            return output
        if isinstance(item, str):
            result = item
            for secret in secrets:
                if isinstance(secret, str) and len(secret) >= 4:
                    result = result.replace(secret, "[redacted]")
            result = _SECRET_VALUE.sub(r"\1\2[redacted]\2", result)
            result = _BARE_SECRET.sub(r"\1[redacted]", result)
            result = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[redacted]", result)
            if len(result) > 65536:
                truncated = True
                return result[:65536] + "\n[truncated]"
            return result
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else None
        return str(item)

    if private_event or auth_event:
        source = value if isinstance(value, dict) else {}
        cleaned = {key: visit(data) for key, data in source.items() if key in metadata_keys}
        cleaned["redacted"] = "private reasoning" if private_event else "authentication"
    else:
        cleaned = visit(value)
    return cleaned, truncated


class AgentEventJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._ready = False

    def _connect(self):
        if not self._ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
        connection = sqlite3.connect(str(self.path), timeout=10)
        os.chmod(self.path, 0o600)
        connection.row_factory = sqlite3.Row
        if not self._ready:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                application TEXT NOT NULL,
                session_id TEXT NOT NULL,
                run_id TEXT,
                event_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                name TEXT NOT NULL,
                direction TEXT NOT NULL,
                data TEXT NOT NULL,
                truncated INTEGER NOT NULL DEFAULT 0,
                UNIQUE(application,session_id,event_id)
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS events_session ON events(application,session_id,seq)")
            connection.commit()
            os.chmod(self.path, 0o600)
            self._ready = True
        return connection

    @staticmethod
    def _identifier(value, label, limit=512):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"Agent event requires a bounded {label}.")
        return value

    def append(self, event):
        if not isinstance(event, dict):
            raise ValueError("Agent event must be an object.")
        application = self._identifier(event.get("application", ""), "application", 80)
        session = self._identifier(event.get("session_id", ""), "session_id")
        event_id = self._identifier(event.get("event_id", ""), "event_id")
        payload = event.get("agent_event")
        if not isinstance(payload, dict):
            raise ValueError("agent_event must contain name, direction and data.")
        name = self._identifier(payload.get("name", ""), "event name", 200)
        direction = payload.get("direction", "server")
        if direction not in {"client", "server", "hook"}:
            raise ValueError("Invalid agent-event direction.")
        run = event.get("run_id")
        if run is not None and (not isinstance(run, str) or len(run) > 512):
            raise ValueError("Invalid agent-event run_id.")
        known_secrets = tuple(value for key, value in os.environ.items() if re.search(r"(?:API_KEY|TOKEN|PASSWORD|SECRET)$", key, re.I) and len(value) >= 4)
        data, truncated = sanitize_event_data(payload.get("data"), name=name, secrets=known_secrets)
        truncated = truncated or payload.get("truncated") is True
        serialized = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(serialized.encode()) > 1024 * 1024:
            data = {"truncated": True, "reason": "Source event exceeded the one-megabyte journal record limit."}
            serialized, truncated = json.dumps(data), True
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, (str, int, float)) or isinstance(timestamp, bool):
            timestamp = datetime.now(timezone.utc).isoformat()
        timestamp = str(timestamp)[:100]
        with self.lock:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute("INSERT OR IGNORE INTO events(application,session_id,run_id,event_id,timestamp,name,direction,data,truncated) VALUES(?,?,?,?,?,?,?,?,?)", (application, session, run, event_id, timestamp, name, direction, serialized, int(truncated)))
                inserted = cursor.rowcount == 1
                row = connection.execute("SELECT seq FROM events WHERE application=? AND session_id=? AND event_id=?", (application, session, event_id)).fetchone()
                return {"seq": row["seq"], "inserted": inserted, "truncated": truncated}

    def read(self, *, application="Codex", session_id="", after=0, limit=200):
        if not isinstance(after, int) or after < 0 or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Choose a nonnegative cursor and a page size from 1 to 500.")
        self._identifier(application, "application", 80)
        if not isinstance(session_id, str) or len(session_id) > 512:
            raise ValueError("Invalid session filter.")
        conditions, args = ["application=?", "seq>?"], [application, after]
        if session_id:
            conditions.append("session_id=?")
            args.append(session_id)
        where = " AND ".join(conditions)
        with self.lock:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(f"SELECT * FROM events WHERE {where} ORDER BY seq LIMIT ?", [*args, limit + 1]).fetchall()
                sessions = connection.execute("SELECT session_id,MAX(seq) AS latest_seq FROM events WHERE application=? GROUP BY session_id ORDER BY latest_seq DESC LIMIT 100", (application,)).fetchall()
        events = []
        for row in rows[:limit]:
            events.append({"seq": row["seq"], "event_id": row["event_id"], "application": row["application"], "session_id": row["session_id"], "run_id": row["run_id"], "timestamp": row["timestamp"], "agent_event": {"name": row["name"], "direction": row["direction"], "data": json.loads(row["data"])}, "truncated": bool(row["truncated"])})
        return {"events": events, "next_cursor": events[-1]["seq"] if events else after,
                "has_more": len(rows) > limit, "truncated": any(event["truncated"] for event in events),
                "sessions": [dict(row) for row in sessions]}

    def latest(self, application="Codex"):
        """Read last receipt without creating storage during installation diagnostics."""
        self._identifier(application, "application", 80)
        if not self.path.is_file():
            return None
        with self.lock, closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT seq,timestamp,session_id,name FROM events WHERE application=? ORDER BY seq DESC LIMIT 1", (application,)).fetchone()
        return dict(row) if row else None
