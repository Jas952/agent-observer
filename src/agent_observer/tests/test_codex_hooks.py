"""Behavior tests for Codex lifecycle hook adapter, state persistence, and sanitization."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from agent_observer.codex_hooks import (
    HOOK_EVENTS,
    handle_hook,
    sanitize_payload,
    get_pre_tool_snapshot,
    install_codex_hooks,
    remove_codex_hooks,
    inspect_codex_hooks,
    is_codex_hooks_installed,
)


class CodexHooksTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project = Path(self.temp_dir.name).resolve()
        (self.project / ".agent-observer/var/hooks").mkdir(parents=True, exist_ok=True)
        (self.project / ".agent-observer/config").mkdir(parents=True, exist_ok=True)
        self.cfg_file = self.project / ".agent-observer/config/observer.project.json"
        self.cfg_file.write_text(json.dumps({
            "schema_version": 1,
            "project": {"name": "test-project", "root": str(self.project)},
            "agent_runtime": "codex",
            "port": 8765,
        }), encoding="utf-8")
        storage = patch("agent_observer.codex_hooks.get_hook_storage", return_value=self.project / "isolated-hook-state")
        storage.start()
        self.addCleanup(storage.stop)


    def test_credentials_and_private_thoughts_are_removed_without_hiding_public_data(self):
        payload = {
            "api_key": "sk-secret123456789",
            "auth_token": "Bearer my-secret-token",
            "user_password": "super-secret-password",
            "reasoning_content": "Internal chain of thought that should not be visible",
            "headers": {"Authorization": "Bearer secret", "Content-Type": "application/json"},
            "clean_field": "hello world",
            "oversized_field": "x" * 6000,
        }
        sanitized = sanitize_payload(payload)
        serialized = json.dumps(sanitized)
        for key in ("api_key", "auth_token", "user_password", "reasoning_content"):
            self.assertNotIn(payload[key], serialized)
        self.assertNotIn("Bearer secret", serialized)
        self.assertEqual(sanitized["clean_field"], "hello world")


    @patch("agent_observer.codex_hooks.emit_events")
    def test_stream_mode_marker_suppresses_duplicate_normalized_calls(self, mock_emit):
        mock_emit.return_value = 1
        with patch.dict(os.environ, {"AGENT_OBSERVER_CODEX_CAPTURE": "stream"}):
            payload = {
                "session_id": "sess_stream",
                "turn_id": "turn_2",
                "cwd": str(self.project),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Run tests",
            }
            res = handle_hook(payload, config_file=self.cfg_file)
            self.assertEqual(res, {})

            mock_emit.assert_called_once()
            events = mock_emit.call_args[0][1]
            # Only 1 event: AGENT_EVENT, no duplicate PROMPT_START
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "AGENT_EVENT")

    @patch("agent_observer.codex_hooks.emit_events")
    def test_pre_and_post_tool_use_captures_diff_snapshot_and_native_marker(self, mock_emit):
        mock_emit.return_value = 1
        # Create a sample file in the project
        test_file = self.project / "model.scad"
        test_file.write_text("cube([10, 10, 10]);\n", encoding="utf-8")
        nested_cwd = self.project / "src"
        nested_cwd.mkdir()

        # PreToolUse
        pre_payload = {
            "session_id": "sess_tool",
            "turn_id": "turn_tool",
            "cwd": str(nested_cwd),
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_use_id": "call_abc123",
            "tool_input": {"file_path": "../model.scad", "patch": "cube([20, 20, 20]);"},
        }
        handle_hook(pre_payload, config_file=self.cfg_file)

        # Baseline snapshot should be stored as list of dicts
        stored_baseline = get_pre_tool_snapshot(self.project, "call_abc123")
        self.assertIsNotNone(stored_baseline)
        self.assertIsInstance(stored_baseline, list)
        self.assertEqual(stored_baseline[0]["path"], "model.scad")
        self.assertEqual(stored_baseline[0]["stage"], "before")
        self.assertIn("cube([10, 10, 10]);", stored_baseline[0]["content"])
        self.assertTrue(stored_baseline[0]["exists"])
        # Mutate the file as if tool executed
        test_file.write_text("cube([20, 20, 20]);\n", encoding="utf-8")

        # PostToolUse
        post_payload = {
            "session_id": "sess_tool",
            "turn_id": "turn_tool",
            "cwd": str(nested_cwd),
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_use_id": "call_abc123",
            "tool_input": {"file_path": "model.scad"},
            "tool_response": {"status": "ok"},
        }
        handle_hook(post_payload, config_file=self.cfg_file)

        # Inspect events emitted in PostToolUse
        post_events = mock_emit.call_args[0][1]
        tool_end = next(ev for ev in post_events if ev.get("type") == "TOOL_CALL_END")
        self.assertTrue(tool_end["native_tool"])
        self.assertIn("file_snapshots", tool_end)
        snaps = tool_end["file_snapshots"]
        self.assertIsInstance(snaps, list)
        before_snap = next(s for s in snaps if s["stage"] == "before")
        after_snap = next(s for s in snaps if s["stage"] == "after")
        self.assertEqual(before_snap["content"].strip(), "cube([10, 10, 10]);")
        self.assertEqual(after_snap["content"].strip(), "cube([20, 20, 20]);")
    def test_install_and_remove_hooks_lifecycle(self):
        target = self.project / ".agent-observer"
        install_codex_hooks(self.project, target)

        self.assertTrue(is_codex_hooks_installed(self.project))
        info = inspect_codex_hooks(self.project)
        self.assertTrue(info["installed"])
        self.assertTrue(info["all_14"])
        self.assertEqual(len(info["events"]), 14)

        # Idempotence: repeat install should not create duplicate groups
        install_codex_hooks(self.project, target)
        info2 = inspect_codex_hooks(self.project)
        self.assertEqual(len(info2["events"]), 14)
        raw_hooks = json.loads((self.project / ".codex/hooks.json").read_text(encoding="utf-8"))["hooks"]
        for ev in HOOK_EVENTS:
            self.assertEqual(len(raw_hooks[ev]), 1)

        # Add a custom unowned hook
        raw_hooks["PreToolUse"].append({
            "hooks": [{"type": "command", "command": "echo custom_unowned"}]
        })
        (self.project / ".codex/hooks.json").write_text(json.dumps({"hooks": raw_hooks}), encoding="utf-8")

        # Remove hooks: should only remove Observer hooks and preserve the custom one
        remove_codex_hooks(self.project)
        self.assertFalse(is_codex_hooks_installed(self.project))
        reloaded = json.loads((self.project / ".codex/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertIn("PreToolUse", reloaded)
        self.assertEqual(len(reloaded["PreToolUse"]), 1)
        self.assertIn("custom_unowned", reloaded["PreToolUse"][0]["hooks"][0]["command"])
        self.assertNotIn("SessionStart", reloaded)


if __name__ == "__main__":
    unittest.main()
