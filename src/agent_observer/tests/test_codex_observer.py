"""Behavioral tests for the passive read-only Codex observer.

Tests scope isolation, source classification, honest completion-only tool semantics,
partial record delivery, safe cursors, usage deduplication, and replay_latest.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_observer.codex_observer import (
    CodexObserver,
    classify_source,
    extract_explicit_paths,
    is_safe_rollout_path,
    make_event_id,
)


class CodexObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name).resolve()

        self.project_a = self.base / "projects/project-a"
        self.project_a.mkdir(parents=True, exist_ok=True)

        self.project_b = self.base / "projects/project-b"
        self.project_b.mkdir(parents=True, exist_ok=True)

        self.state_dir = self.base / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.codex_home = self.base / "codex_home"
        self.sessions_dir = self.codex_home / "sessions/2026/10/24"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.archived_dir = self.codex_home / "archived_sessions"
        self.archived_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.codex_home / "state_5.sqlite"
        self._init_sqlite()

        self.emitted: list[dict] = []

    def _init_sqlite(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                path TEXT NOT NULL,
                PRIMARY KEY (project_id, position)
            )
        """)
        conn.execute("""
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                project_id TEXT,
                originator TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _insert_thread(self, thread_id: str, rollout_path: Path, cwd: Path, source="vscode", originator=None, project_id=None, updated_at=1000):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider, cwd, title, sandbox_policy, approval_mode, project_id, originator)
            VALUES (?, ?, ?, ?, ?, 'openai', ?, 'title', '{}', 'never', ?, ?)
        """, (thread_id, str(rollout_path), 1000, updated_at, source, str(cwd), project_id, originator))
        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # 1. Source classification
    # -------------------------------------------------------------------------

    def test_source_classification(self):
        """Desktop sessions have source=vscode AND originator=codex_work_desktop; do not misclassify."""
        self.assertEqual(classify_source("vscode", "codex_work_desktop"), "codex-desktop")
        self.assertEqual(classify_source("vscode", "codex_desktop"), "codex-desktop")
        self.assertEqual(classify_source("vscode", None), "vscode")
        self.assertEqual(classify_source("cli", None), "codex-cli")
        self.assertEqual(classify_source("vscode", "codex_cli"), "codex-cli")
        self.assertEqual(classify_source("terminal", None), "codex-cli")
        self.assertEqual(classify_source(None, None), "unknown")

    # -------------------------------------------------------------------------
    # 2. Scope isolation and project matching
    # -------------------------------------------------------------------------

    def test_scope_isolation_and_project_matching(self):
        """Only threads matching the specified project root are observed."""
        rollout_a = self.sessions_dir / "rollout-a.jsonl"
        rollout_a.touch()
        self._insert_thread("thread-a", rollout_a, self.project_a, originator="codex_work_desktop", updated_at=2000)

        rollout_b = self.sessions_dir / "rollout-b.jsonl"
        rollout_b.touch()
        self._insert_thread("thread-b", rollout_b, self.project_b, originator="codex_work_desktop", updated_at=1500)

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        matching = observer._get_matching_threads()

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["id"], "thread-a")


    def test_discovery_reads_project_tool_declarations_without_replaying_activity(self):
        from agent_observer.capabilities import discover_capabilities
        description = (
            "Execute orchestration code. Mentioning tools.not_a_declaration() adds no tool.\n"
            "### `exec_command`\nRun a terminal command.\n\nexec tool declaration:\n"
            "```ts\ndeclare const tools: { exec_command(args: {cmd: string}): Promise<unknown>; };\n```\n"
            "### `apply_patch`\nApply a file patch.\n\nexec tool declaration:\n"
            "```ts\ndeclare const tools: { apply_patch(input: string): Promise<unknown>; };\n```\n"
        )
        declarations = {"type": "response_item", "payload": {"type": "additional_tools", "tools": [
            {"name": "functions", "tools": [{"name": "exec", "description": description}]},
            {"name": "collaboration", "tools": [{"name": "send_message"}, {"name": "followup_task"}]},
            {"name": "clock", "tools": [{"name": "sleep"}]},
            {"name": "mcp__project_server", "tools": [{"name": "remote_only"}]},
        ]}}
        rollout = self.sessions_dir / "project-catalog.jsonl"
        records = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "previous"}},
            declarations,
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
             "content": [{"text": 'additional_tools {"name": "private_non_tool"}'}]}},
            {"type": "event_msg", "payload": {"type": "item_completed",
             "item": {"type": "CommandExecution", "id": "old-call", "command": ["pwd"]}}},
        ]
        rollout.write_text("".join(json.dumps(record) + "\n" for record in records))
        self._insert_thread("project-catalog", rollout, self.project_a, updated_at=2000)
        other = self.sessions_dir / "other-project.jsonl"
        other.write_text(json.dumps({"type": "response_item", "payload": {"type": "additional_tools",
            "tools": [{"name": "functions", "tools": [{"name": "other_project_only"}]}]}}) + "\n")
        self._insert_thread("other-project", other, self.project_b, updated_at=9000)
        before = rollout.read_bytes()
        with patch.dict(os.environ, {"HOME": str(self.base), "CODEX_HOME": str(self.codex_home)}), \
                patch("agent_observer.capabilities._codex_cli_inventory", return_value=None):
            inventory = discover_capabilities(self.project_a, agent="codex")
        names = {tool["name"] for tool in inventory["native_tools"]}
        self.assertEqual(names, {"exec", "exec_command", "apply_patch", "send_message", "followup_task", "clock.sleep"})
        self.assertEqual(rollout.read_bytes(), before)

    def test_declaration_discovery_uses_latest_project_session_and_ignores_partial_metadata(self):
        from agent_observer.codex_observer import read_codex_native_tools
        old = self.sessions_dir / "old-catalog.jsonl"
        old.write_text(json.dumps({"type": "response_item", "payload": {"type": "additional_tools",
            "tools": [{"name": "functions", "tools": [{"name": "old_session_only"}]}]}}) + "\n")
        self._insert_thread("old-catalog", old, self.project_a, updated_at=1000)
        latest = self.sessions_dir / "latest-catalog.jsonl"
        latest.touch()
        self._insert_thread("latest-catalog", latest, self.project_a, updated_at=2000)
        self.assertEqual(read_codex_native_tools(self.project_a, codex_home=self.codex_home), [])
        latest.write_text(json.dumps({"type": "response_item", "payload": {"type": "additional_tools",
            "tools": [{"name": "functions", "tools": [{"name": "current_session_tool"}]}]}}) + "\n" +
            '{"type":"response_item","payload":{"type":"additional_tools","tools":[')
        self.assertEqual([tool["name"] for tool in read_codex_native_tools(self.project_a, codex_home=self.codex_home)],
                         ["current_session_tool"])

    def test_incremental_declarations_retain_native_tools_and_detach_prior_inventory_events(self):
        rollout = self.sessions_dir / "live-catalog.jsonl"
        rollout.touch()
        thread = {"id": "live-catalog", "rollout_path": str(rollout), "source": "cli", "cwd": str(self.project_a)}
        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        for index, namespace in enumerate([
            {"name": "functions", "tools": [{"name": "send_message"}]},
            {"name": "mcp__project_server", "tools": [{"name": "inspect"}]},
            {"name": "functions", "tools": [{"name": "followup_task"}]},
        ]):
            observer._process_record(thread, {"type": "response_item",
                "payload": {"type": "additional_tools", "tools": [namespace]}}, f"declaration-{index}")
        inventories = [event["inventory"] for event in self.emitted if event["type"] == "RUNTIME_INVENTORY"]
        self.assertEqual([tool["name"] for tool in inventories[0]["native_tools"]], ["send_message"])
        self.assertEqual({tool["name"] for tool in inventories[-1]["native_tools"]}, {"send_message", "followup_task"})
        self.assertEqual(inventories[-1]["mcp"][0]["tool_names"], ["inspect"])
        self.assertFalse(any(event["type"] in {"PROMPT_START", "TOOL_CALL_START", "TOOL_CALL_END"} for event in self.emitted))

    # -------------------------------------------------------------------------
    # 3. Baseline at start and no historical leak
    # -------------------------------------------------------------------------

    def test_baseline_at_start_and_no_historical_leak(self):
        """Completed conversations stay historical; later live turns are observed."""
        rollout_a = self.sessions_dir / "rollout-a.jsonl"
        hist_rec = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-hist"}}
        completed = {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-hist"}}
        rollout_a.write_text(json.dumps(hist_rec) + "\n" + json.dumps(completed) + "\n", encoding="utf-8")

        self._insert_thread("thread-a", rollout_a, self.project_a, originator="codex_work_desktop")

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home, poll_interval=0.1)
        observer.start()
        try:
            time.sleep(0.2)
            # Historical record was baselined at start and must NOT be emitted
            self.assertEqual(len(self.emitted), 0)

            # Append new record after start()
            new_rec = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-live"}}
            with open(rollout_a, "a", encoding="utf-8") as f:
                f.write(json.dumps(new_rec) + "\n")

            time.sleep(0.3)
            # New record must be emitted
            live_starts = [e for e in self.emitted if e.get("type") == "PROMPT_START"]
            self.assertEqual(len(live_starts), 1)
            self.assertEqual(live_starts[0]["turn_id"], "turn-live")
            self.assertFalse(live_starts[0]["replay"])
        finally:
            observer.stop()

    # -------------------------------------------------------------------------
    # 4. Partial record delivery and safe cursors
    # -------------------------------------------------------------------------

    def test_partial_record_delivery_and_safe_cursors(self):
        """Partial JSON lines are buffered until newline arrives, committing byte offset safely."""
        rollout_a = self.sessions_dir / "rollout-a.jsonl"
        rollout_a.touch()
        self._insert_thread("thread-a", rollout_a, self.project_a, originator="codex_work_desktop")

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home, poll_interval=0.1)
        observer.start()
        try:
            rec = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-partial"}}
            full_line = json.dumps(rec) + "\n"
            part1 = full_line[:15]
            part2 = full_line[15:]

            with open(rollout_a, "a", encoding="utf-8") as f:
                f.write(part1)

            time.sleep(0.2)
            # Incomplete line should not produce an event
            self.assertEqual(len(self.emitted), 0)

            with open(rollout_a, "a", encoding="utf-8") as f:
                f.write(part2)

            time.sleep(0.3)
            # Completed line now parsed and emitted
            self.assertTrue(any(e.get("type") == "PROMPT_START" for e in self.emitted))

            # Cursor file must exist and contain committed offset equal to file size
            self.assertTrue(observer._cursor_file.is_file())
            saved_cursors = json.loads(observer._cursor_file.read_text(encoding="utf-8"))
            self.assertIn("thread-a", saved_cursors)
            self.assertEqual(saved_cursors["thread-a"]["offset"], rollout_a.stat().st_size)
            self.assertEqual(saved_cursors["thread-a"]["partial_line"], "")
        finally:
            observer.stop()

    # -------------------------------------------------------------------------
    # 5. Honest completed-tool normalization
    # -------------------------------------------------------------------------

    def test_honest_completion_only_semantics(self):
        """Tool completion records emit TOOL_CALL_END directly; never invent in-progress TOOL_CALL_START."""
        rollout_a = self.sessions_dir / "rollout-a.jsonl"
        rollout_a.touch()
        self._insert_thread("thread-a", rollout_a, self.project_a, originator="codex_work_desktop")

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home, poll_interval=0.1)
        observer.start()
        try:
            # 1. Start turn
            turn_ev = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-tool"}}
            # 2. CommandExecution
            cmd_ev = {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-tool",
                    "item": {
                        "type": "CommandExecution",
                        "id": "exec-1",
                        "command": ["rtk", "proxy", "cat", "config.json"],
                        "cwd": str(self.project_a),
                        "status": "completed",
                        "exit_code": 0,
                        "duration": 0.45,
                        "parsed_cmd": [{"type": "read", "path": str(self.project_a / "config.json")}],
                    },
                },
            }
            # 3. McpToolCall
            mcp_ev = {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-tool",
                    "item": {
                        "type": "McpToolCall",
                        "id": "mcp-1",
                        "server": "codebase-memory-mcp",
                        "tool": "list_projects",
                        "arguments": {"path": str(self.project_a)},
                        "status": "completed",
                        "duration": 0.12,
                        "result": {"status": "ok"},
                    },
                },
            }
            # 4. FileChange
            file_ev = {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-tool",
                    "item": {
                        "type": "FileChange",
                        "id": "file-1",
                        "status": "completed",
                        "changes": {str(self.project_a / "model.scad"): {"type": "edit"}},
                    },
                },
            }

            with open(rollout_a, "a", encoding="utf-8") as f:
                for ev in (turn_ev, cmd_ev, mcp_ev, file_ev):
                    f.write(json.dumps(ev) + "\n")

            time.sleep(0.3)

            # Assert NO TOOL_CALL_START was fabricated
            starts = [e for e in self.emitted if e.get("type") == "TOOL_CALL_START"]
            self.assertEqual(len(starts), 0)

            # Assert TOOL_CALL_END records
            ends = [e for e in self.emitted if e.get("type") == "TOOL_CALL_END"]
            self.assertEqual(len(ends), 3)

            # Command execution tool
            cmd_tool = next(e for e in ends if e.get("call_id") == "exec-1")
            self.assertTrue(cmd_tool["native_tool"])
            self.assertEqual(cmd_tool["tool_name"], "exec")
            self.assertIn(str(self.project_a / "config.json"), cmd_tool["paths"])
            self.assertEqual(cmd_tool["duration_ms"], 450.0)

            # MCP tool
            mcp_tool = next(e for e in ends if e.get("call_id") == "mcp-1")
            self.assertFalse(mcp_tool["native_tool"])
            self.assertEqual(mcp_tool["mcp_server"], "codebase-memory-mcp")
            self.assertEqual(mcp_tool["tool_name"], "list_projects")
            self.assertEqual(mcp_tool["status"], "completed")

            # FileChange tool
            f_tool = next(e for e in ends if e.get("call_id") == "file-1")
            self.assertTrue(f_tool["native_tool"])
            self.assertIn(str(self.project_a / "model.scad"), f_tool["paths"])
        finally:
            observer.stop()

    # -------------------------------------------------------------------------
    # 6. Explicit file paths (never infer from arbitrary command text)
    # -------------------------------------------------------------------------

    def test_explicit_paths_extraction(self):
        """Only parsed read paths or file changes count as working files."""
        # Unparsed command with arbitrary text must not produce paths
        item_arbitrary = {
            "type": "CommandExecution",
            "command": ["echo", "check", "/unparsed/path/should/not/match"],
            "parsed_cmd": [{"type": "unknown", "cmd": "echo"}],
        }
        self.assertEqual(extract_explicit_paths(item_arbitrary), [])

        # Parsed read command
        item_parsed = {
            "type": "CommandExecution",
            "parsed_cmd": [{"type": "read", "path": "/workspace/sample/file.txt"}],
        }
        self.assertEqual(extract_explicit_paths(item_parsed), ["/workspace/sample/file.txt"])

        # FileChange
        item_filechange = {
            "type": "FileChange",
            "changes": {"/workspace/sample/out.txt": {"type": "create"}},
        }
        self.assertEqual(extract_explicit_paths(item_filechange), ["/workspace/sample/out.txt"])

    def test_native_rtk_read_links_literal_operands_using_file_uri_cwd(self):
        cwd = self.project_a / "folder with spaces"
        cwd.mkdir()
        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        observer._process_record(
            {"id": "thread", "source": "exec", "originator": "codex_exec"},
            {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "turn",
             "item": {"type": "CommandExecution", "id": "read", "cwd": cwd.as_uri(),
                      "command": ["/bin/zsh", "-lc", "rtk proxy cat 'notes/part one.md'"],
                      "parsed_cmd": [{"type": "unknown", "cmd": "rtk proxy cat 'notes/part one.md'"}],
                      "status": "completed", "exit_code": 0}}},
            "record")
        completion = next(event for event in self.emitted if event["type"] == "TOOL_CALL_END")
        expected = str(cwd / "notes/part one.md")
        self.assertEqual(completion["paths"], [expected])
        self.assertEqual(completion["file_actions"], [{"path": expected, "action": "read"}])

    def test_native_command_does_not_invent_script_or_dynamic_file_reads(self):
        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        for index, command in enumerate((
                "echo 'cat secret.md'",
                "python -c \"open('secret.md').read()\"",
                "cat \"$DOCUMENT\"",
                "cat *.md",
                "cat README.md && echo done")):
            with self.subTest(command=command):
                observer._process_record(
                    {"id": "thread", "source": "exec"},
                    {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "turn",
                     "item": {"type": "CommandExecution", "id": str(index), "cwd": self.project_a.as_uri(),
                              "parsed_cmd": [{"type": "unknown", "cmd": command}],
                              "status": "completed", "exit_code": 0}}},
                    str(index))
                completion = [event for event in self.emitted if event["type"] == "TOOL_CALL_END"][-1]
                self.assertEqual(completion["paths"], [])
                self.assertEqual(completion["file_actions"], [])

    # -------------------------------------------------------------------------
    # 7. Token usage deduplication
    # -------------------------------------------------------------------------

    def test_usage_deduplication(self):
        """Duplicate response_id records must not produce duplicate USAGE_UPDATE events."""
        rollout_a = self.sessions_dir / "rollout-a.jsonl"
        rollout_a.touch()
        self._insert_thread("thread-a", rollout_a, self.project_a, originator="codex_work_desktop")

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home, poll_interval=0.1)
        observer.start()
        try:
            u1 = {
                "type": "token_usage_record",
                "payload": {
                    "turn_id": "turn-usage",
                    "response_id": "resp-100",
                    "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 50, "reasoning_output_tokens": 0, "total_tokens": 120},
                },
            }
            # Append same usage record twice
            with open(rollout_a, "a", encoding="utf-8") as f:
                f.write(json.dumps(u1) + "\n")
                f.write(json.dumps(u1) + "\n")

            time.sleep(0.3)
            usage_events = [e for e in self.emitted if e.get("type") == "USAGE_UPDATE"]
            # Deduplicated: exactly 1 USAGE_UPDATE event
            self.assertEqual(len(usage_events), 1)
            self.assertEqual(usage_events[0]["total_tokens"], 120)
            self.assertEqual(usage_events[0]["response_id"], "resp-100")
        finally:
            observer.stop()

    # -------------------------------------------------------------------------
    # 8. Mid-turn attachment
    # -------------------------------------------------------------------------

    def test_mid_turn_attachment(self):
        """Attaching mid-turn emits contextual PROMPT_START with observed_mid_turn=true."""
        rollout_a = self.sessions_dir / "rollout-a.jsonl"
        rollout_a.touch()
        self._insert_thread("thread-a", rollout_a, self.project_a, originator="codex_work_desktop")

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home, poll_interval=0.1)
        observer.start()
        try:
            # Emit tool completion without a preceding task_started
            cmd_ev = {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-mid",
                    "item": {
                        "type": "CommandExecution",
                        "id": "exec-mid",
                        "command": ["ls"],
                        "status": "completed",
                        "exit_code": 0,
                    },
                },
            }
            with open(rollout_a, "a", encoding="utf-8") as f:
                f.write(json.dumps(cmd_ev) + "\n")

            time.sleep(0.3)
            prompt_starts = [e for e in self.emitted if e.get("type") == "PROMPT_START"]
            self.assertEqual(len(prompt_starts), 1)
            self.assertTrue(prompt_starts[0]["observed_mid_turn"])
            self.assertEqual(prompt_starts[0]["turn_id"], "turn-mid")
        finally:
            observer.stop()

    # -------------------------------------------------------------------------
    # 9. Replay latest completed turn
    # -------------------------------------------------------------------------

    def test_replay_latest_completed_turn(self):
        """replay_latest() replays completed turn with replay=true and ends with GENERATION_END."""
        rollout_a = self.sessions_dir / "rollout-a.jsonl"
        records = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-1",
                    "item": {"type": "UserMessage", "content": [{"text": "Build part A"}]},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-1",
                    "item": {"type": "AgentMessage", "phase": "final_answer", "content": [{"text": "Part A ready."}]},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-1",
                    "duration_ms": 1200,
                    "last_agent_message": "Part A ready.",
                },
            },
        ]
        with open(rollout_a, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        self._insert_thread("thread-a", rollout_a, self.project_a, originator="codex_work_desktop", updated_at=3000)

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        receipt = observer.replay_latest()

        self.assertEqual(receipt["status"], "ok")
        self.assertEqual(receipt["thread_id"], "thread-a")
        self.assertEqual(receipt["turn_id"], "turn-1")
        self.assertGreater(receipt["events_replayed"], 0)

        # All emitted events must have replay=True
        self.assertTrue(all(e.get("replay") is True for e in self.emitted))

        # Must end with GENERATION_END and status completed (no running state)
        gen_ends = [e for e in self.emitted if e.get("type") == "GENERATION_END"]
        self.assertEqual(len(gen_ends), 1)
        self.assertEqual(gen_ends[0]["status"], "completed")
        self.assertEqual(gen_ends[0]["result"], "Part A ready.")

    # -------------------------------------------------------------------------
    # 10. Unsupported compressed rollouts & safe paths
    # -------------------------------------------------------------------------

    def test_unsupported_compressed_rollouts(self):
        """Compressed rollouts (.zst, .gz) are surfaced as unsupported, not treated as empty success."""
        compressed_path = self.sessions_dir / "rollout-compressed.jsonl.zst"
        compressed_path.touch()
        self._insert_thread("thread-zst", compressed_path, self.project_a, originator="codex_work_desktop")

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        observer.start()
        try:
            status = observer.status()
            unsupported = status.get("unsupported_rollouts", [])
            self.assertTrue(any(e.get("path") == str(compressed_path) for e in unsupported))
        finally:
            observer.stop()

    def test_safe_rollout_paths(self):
        """Rollouts outside sessions or archived_sessions must be rejected."""
        safe_sess = self.sessions_dir / "test.jsonl"
        safe_arch = self.archived_dir / "test.jsonl"
        unsafe_ext = self.base / "external/test.jsonl"

        self.assertTrue(is_safe_rollout_path(safe_sess, self.codex_home))
        self.assertTrue(is_safe_rollout_path(safe_arch, self.codex_home))
        self.assertFalse(is_safe_rollout_path(unsafe_ext, self.codex_home))


    # -------------------------------------------------------------------------
    # 11. Active turn recovery & session discovery isolation
    # -------------------------------------------------------------------------

    def test_attaching_during_active_turn_recovers_evidence_and_subsequent_events(self):
        """Active turn in rollout with recent mtime evidence is recovered on attach."""
        rollout_a = self.sessions_dir / "rollout-active.jsonl"
        records = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-active-1"}},
            {"type": "turn_context", "payload": {"turn_id": "turn-active-1", "model": "o3-mini", "cwd": str(self.project_a)}},
            {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "turn-active-1", "item": {"type": "UserMessage", "content": [{"text": "Fix active bug"}]}}},
        ]
        with open(rollout_a, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        self._insert_thread("thread-active", rollout_a, self.project_a, originator="codex_work_desktop", updated_at=int(time.time() * 1000))

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home, poll_interval=0.05)
        observer.start()
        try:
            time.sleep(0.2)
            starts = [e for e in self.emitted if e.get("type") == "PROMPT_START"]
            self.assertTrue(len(starts) >= 1)
            self.assertEqual(starts[0]["turn_id"], "turn-active-1")
            updates = [e for e in self.emitted if e.get("type") == "PROMPT_UPDATE"]
            self.assertTrue(any(u.get("prompt") == "Fix active bug" for u in updates))

            sessions = observer.sessions()
            matching = next(s for s in sessions if s["session_id"] == "thread-active")
            self.assertEqual(matching["status"], "running")
            self.assertEqual(matching["run_id"], "codex:thread-active:turn-active-1")

            complete_ev = {
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "turn-active-1", "last_agent_message": "Bug fixed"}
            }
            with open(rollout_a, "a", encoding="utf-8") as f:
                f.write(json.dumps(complete_ev) + "\n")

            time.sleep(0.2)
            gen_ends = [e for e in self.emitted if e.get("type") == "GENERATION_END"]
            self.assertEqual(len(gen_ends), 1)
            self.assertEqual(gen_ends[0]["status"], "completed")
            self.assertEqual(gen_ends[0]["result"], "Bug fixed")
        finally:
            observer.stop()

    def test_stale_unmatched_turn_without_evidence_is_not_fabricated_active(self):
        """Stale uncompleted turn without recent evidence reports unknown and does not baseline active."""
        rollout_stale = self.sessions_dir / "rollout-stale.jsonl"
        records = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-stale"}},
            {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "turn-stale", "item": {"type": "UserMessage", "content": [{"text": "Old query"}]}}},
        ]
        with open(rollout_stale, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        stale_time = time.time() - 10800
        os.utime(rollout_stale, (stale_time, stale_time))

        self._insert_thread("thread-stale", rollout_stale, self.project_a, originator="codex_work_desktop", updated_at=int(stale_time * 1000))

        observer = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home, poll_interval=0.05)
        sessions = observer.sessions()
        matching = next(s for s in sessions if s["session_id"] == "thread-stale")
        self.assertEqual(matching["status"], "unknown")

        observer.start()
        try:
            time.sleep(0.15)
            starts = [e for e in self.emitted if e.get("type") == "PROMPT_START"]
            self.assertEqual(len(starts), 0)
        finally:
            observer.stop()

    def test_scope_isolation_no_cross_project_leak(self):
        """Sessions for project A never leak into sessions discovered for project B."""
        rollout_a = self.sessions_dir / "rollout-a-scope.jsonl"
        rollout_a.touch()
        self._insert_thread("thread-proj-a", rollout_a, self.project_a, originator="codex_work_desktop", updated_at=2000)

        rollout_b = self.sessions_dir / "rollout-b-scope.jsonl"
        rollout_b.touch()
        self._insert_thread("thread-proj-b", rollout_b, self.project_b, originator="codex_work_desktop", updated_at=3000)

        observer_a = CodexObserver(self.project_a, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        sessions_a = observer_a.sessions()
        session_ids_a = {s["session_id"] for s in sessions_a}
        self.assertIn("thread-proj-a", session_ids_a)
        self.assertNotIn("thread-proj-b", session_ids_a)

        observer_b = CodexObserver(self.project_b, self.state_dir, self.emitted.append, codex_home=self.codex_home)
        sessions_b = observer_b.sessions()
        session_ids_b = {s["session_id"] for s in sessions_b}
        self.assertIn("thread-proj-b", session_ids_b)
        self.assertNotIn("thread-proj-a", session_ids_b)
    # -------------------------------------------------------------------------
    # 11. Status contract shape
    # -------------------------------------------------------------------------



if __name__ == "__main__":
    unittest.main()
