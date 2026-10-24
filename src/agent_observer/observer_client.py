"""Bounded, fail-open telemetry adapter; it never executes or reads project tools."""
import contextlib
import json
import time
import urllib.request
import uuid
from .observer_config import load_settings
from .xray_auth import load_token

class ObserverClient:
    def __init__(self, project_root=None, config_file=None, endpoint=None, timeout=0.2, enabled=True):
        self.settings = load_settings(config_file, project_root)
        self.endpoint = endpoint or f"http://127.0.0.1:{self.settings['port']}/api/event"
        from urllib.parse import urlparse
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Observer telemetry is local-only")
        self.timeout = min(max(float(timeout), 0.01), 1.0)
        self.enabled, self.last_error = enabled, None

    def emit(self, event):
        if not self.enabled: return False
        try:
            token = load_token(path=self.settings["token_file"])
            body = json.dumps(event, ensure_ascii=False).encode("utf-8")
            if len(body) > 262144: raise ValueError("Event exceeds 256 KB; send a bounded summary")
            request = urllib.request.Request(self.endpoint, data=body, headers={"Content-Type": "application/json", "X-CAD-Token": token, "X-Agent-Observer-Token": token})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                ok = response.status == 200
            self.last_error = None
            return ok
        except Exception as error:
            self.last_error = type(error).__name__
            return False

    def run(self, prompt, project=None, **metadata):
        return ObservedRun(self, prompt, project or self.settings["project_name"], metadata)

class ObservedRun:
    def __init__(self, client, prompt, project, metadata):
        self.client, self.prompt, self.project, self.metadata = client, prompt, project, metadata
        self.run_id, self.result = str(uuid.uuid4()), "Request finished"

    def event(self, kind, **fields):
        return self.client.emit({**fields, "type": kind, "run_id": self.run_id})

    def __enter__(self):
        self.event("PROMPT_START", prompt=self.prompt, project=self.project, **self.metadata)
        return self

    def __exit__(self, kind, error, traceback):
        self.event("GENERATION_END", success=kind is None, status="failed" if kind else "completed", result=(str(error) if error else self.result)[:1200])
        return False

    def phase(self, text):
        """A published progress message, never private model reasoning."""
        return self.event("THINKING", phase=str(text)[:1200])

    def document(self, path, success=True, estimated_tokens=None, reason="Document was read"):
        fields = {"path": str(path), "success": bool(success), "reason": reason}
        if estimated_tokens is not None: fields["estimated_tokens"] = int(estimated_tokens)
        return self.event("KNOWLEDGE_ACCESS", **fields)

    def usage(self, input_tokens, output_tokens, model_call_id=None):
        return self.event("THINKING", usage_kind="token_usage", input_tokens=int(input_tokens), output_tokens=int(output_tokens), model_call_id=model_call_id)

    @contextlib.contextmanager
    def tool(self, name, arguments=None):
        call_id, start = str(uuid.uuid4()), time.monotonic()
        try: argument_text = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False, default=str)
        except Exception: argument_text = "[Arguments omitted: not serializable]"
        self.event("TOOL_CALL_START", tool=name, call_id=call_id, input=argument_text[:4000])
        summary = {"result": "Command completed"}
        try:
            yield summary
        except BaseException as error:
            self.event("TOOL_CALL_END", tool=name, call_id=call_id, success=False, result=str(error)[:1200], duration_ms=(time.monotonic()-start)*1000)
            raise
        else:
            self.event("TOOL_CALL_END", tool=name, call_id=call_id, success=True, result=str(summary.get("result", "Command completed"))[:1200], duration_ms=(time.monotonic()-start)*1000)
