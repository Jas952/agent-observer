"""Focused behavior tests for CodexStream normalizer and relay.

Covers data loss prevention, delta deduplication, parallel tool pairing,
subagent isolation, credential/reasoning sanitization, and dirty baseline snapshots.
"""
import json
import tempfile
import unittest
from pathlib import Path

from agent_observer.codex_stream import (
    CodexNormalizer,
    is_sensitive_key,
    sanitize_payload,
)


class TestCodexStreamNormalizer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.emitted = []
        self.normalizer = CodexNormalizer(
            emitter=lambda ev: self.emitted.append(ev) or True,
            project_root=self.root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_delta_deduplication_prevents_graph_step_floods(self):
        """High-volume streaming deltas must populate journal AGENT_EVENT but never spam graph steps."""
        self.normalizer.observe_server_message({
            "method": "turn/started",
            "params": {"threadId": "thread-1", "turn": {"id": "turn-1"}},
        })
        self.normalizer.observe_server_message({
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"id": "cmd-1", "type": "commandExecution", "command": "rtk git status"},
            },
        })

        # Emit 60 output deltas and 40 reasoning deltas
        for i in range(60):
            self.normalizer.observe_server_message({
                "method": "command/exec/outputDelta",
                "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "cmd-1", "delta": f"chunk {i}\n"},
            })
        for i in range(40):
            self.normalizer.observe_server_message({
                "method": "item/reasoning/textDelta",
                "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "rs-1", "delta": f"thought {i} "},
            })

        self.normalizer.observe_server_message({
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"id": "cmd-1", "type": "commandExecution", "exitCode": 0, "aggregatedOutput": "Clean tree"},
            },
        })

        agent_events = [e for e in self.emitted if e["type"] == "AGENT_EVENT"]
        tool_starts = [e for e in self.emitted if e["type"] == "TOOL_CALL_START"]
        tool_ends = [e for e in self.emitted if e["type"] == "TOOL_CALL_END"]

        # Exactly 1 start and 1 end in graph steps
        self.assertEqual(len(tool_starts), 1)
        self.assertEqual(len(tool_ends), 1)
        self.assertEqual(tool_starts[0]["call_id"], "cmd-1")
        self.assertEqual(tool_ends[0]["call_id"], "cmd-1")

        # But all 100 deltas + lifecycle events exist in the journal without data loss
        self.assertGreaterEqual(len(agent_events), 103)
        delta_events = [e for e in agent_events if e["agent_event"]["name"] == "command/exec/outputDelta"]
        self.assertEqual(len(delta_events), 60)

    def test_parallel_tool_calls_no_misattribution(self):
        """Concurrent in-flight tool calls must pair completions by item ID without cross-talk."""
        self.normalizer.observe_server_message({
            "method": "turn/started",
            "params": {"threadId": "thread-p", "turn": {"id": "turn-p"}},
        })

        # Start Tool A and Tool B in parallel
        self.normalizer.observe_server_message({
            "method": "item/started",
            "params": {
                "threadId": "thread-p",
                "turnId": "turn-p",
                "item": {"id": "tool-A", "type": "commandExecution", "command": "rtk proxy cargo build"},
            },
        })
        self.normalizer.observe_server_message({
            "method": "item/started",
            "params": {
                "threadId": "thread-p",
                "turnId": "turn-p",
                "item": {"id": "tool-B", "type": "mcpToolCall", "server": "db", "tool": "query", "arguments": {"q": "SELECT 1"}},
            },
        })

        # Complete Tool B first, then Tool A (out-of-order completion)
        self.normalizer.observe_server_message({
            "method": "item/completed",
            "params": {
                "threadId": "thread-p",
                "turnId": "turn-p",
                "item": {"id": "tool-B", "type": "mcpToolCall", "server": "db", "tool": "query", "status": "completed", "result": [{"val": 1}]},
            },
        })
        self.normalizer.observe_server_message({
            "method": "item/completed",
            "params": {
                "threadId": "thread-p",
                "turnId": "turn-p",
                "item": {"id": "tool-A", "type": "commandExecution", "exitCode": 1, "aggregatedOutput": "build error"},
            },
        })

        tool_ends = [e for e in self.emitted if e["type"] == "TOOL_CALL_END"]
        self.assertEqual(len(tool_ends), 2)

        end_b = next(e for e in tool_ends if e["call_id"] == "tool-B")
        end_a = next(e for e in tool_ends if e["call_id"] == "tool-A")

        self.assertEqual(end_b["tool"], "mcp__db__query")
        self.assertTrue(end_b["success"])

        self.assertEqual(end_a["tool"], "bash")
        self.assertFalse(end_a["success"])
        self.assertEqual(end_a["exit_code"], 1)
        self.assertEqual(end_a["result"], "build error")

    def test_subagent_isolation_and_parent_attribution(self):
        """Subagent threads must maintain isolated run_ids while preserving parent_run_id and agent_id."""
        # Main turn
        self.normalizer.observe_server_message({
            "method": "turn/started",
            "params": {"threadId": "main-thread", "turn": {"id": "turn-main"}},
        })
        self.normalizer.observe_server_message({
            "method": "item/started",
            "params": {
                "threadId": "main-thread",
                "turnId": "turn-main",
                "item": {
                    "id": "sub-1",
                    "type": "subAgentActivity",
                    "agentThreadId": "sub-thread-xyz",
                    "agentPath": "workers/reviewer",
                    "model": "gpt-4o",
                },
            },
        })

        # Subagent turn and activity
        self.normalizer.observe_server_message({
            "method": "turn/started",
            "params": {"threadId": "sub-thread-xyz", "turn": {"id": "turn-sub-1"}},
        })
        self.normalizer.observe_server_message({
            "method": "item/started",
            "params": {
                "threadId": "sub-thread-xyz",
                "turnId": "turn-sub-1",
                "item": {"id": "sub-cmd", "type": "commandExecution", "command": "rtk proxy pytest"},
            },
        })
        self.normalizer.observe_server_message({
            "method": "item/completed",
            "params": {
                "threadId": "sub-thread-xyz",
                "turnId": "turn-sub-1",
                "item": {"id": "sub-cmd", "type": "commandExecution", "exitCode": 0, "aggregatedOutput": "5 passed"},
            },
        })

        sub_events = [e for e in self.emitted if e.get("session_id") == "sub-thread-xyz"]
        self.assertTrue(len(sub_events) > 0)

        tool_start = next(e for e in sub_events if e["type"] == "TOOL_CALL_START")
        self.assertEqual(tool_start["run_id"], "codex:sub-thread-xyz:turn-sub-1")
        self.assertEqual(tool_start["parent_run_id"], "codex:main-thread:turn-main")
        self.assertEqual(tool_start["agent_id"], "sub-1")

    def test_unknown_events_approvals_and_errors_preserved(self):
        """Unknown notification names, approvals, and error responses must be captured in AGENT_EVENT."""
        # Unknown notification
        self.normalizer.observe_server_message({
            "method": "experimental/quantumFeatureNotification",
            "params": {"quantumLevel": 42},
        })
        # Server approval request
        self.normalizer.observe_server_message({
            "id": 101,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "rm -rf /tmp/test", "reason": "Cleanup"},
        })
        # Client approval response
        self.normalizer.observe_client_message({
            "id": 101,
            "result": {"decision": "accept"},
        })
        # Server error
        self.normalizer.observe_server_message({
            "id": 102,
            "error": {"code": -32603, "message": "Sandbox permission denied"},
        })

        agent_events = [e for e in self.emitted if e["type"] == "AGENT_EVENT"]
        names = [e["agent_event"]["name"] for e in agent_events]

        self.assertIn("experimental/quantumFeatureNotification", names)
        self.assertIn("item/commandExecution/requestApproval", names)
        self.assertIn("response", names)  # or method for client
        error = next(event["agent_event"]["data"]["error"] for event in agent_events if event["agent_event"]["data"].get("id") == 102)
        self.assertEqual(error["code"], -32603)

        # Check approval response captured direction 'client'
        client_ev = next(e for e in agent_events if e["agent_event"]["direction"] == "client")
        self.assertEqual(client_ev["agent_event"]["data"]["result"]["decision"], "accept")

    def test_sanitization_redacts_auth_and_reasoning_without_breaking_token_metrics(self):
        """Credentials and private thoughts must be redacted; token counts and metadata must remain intact."""
        raw = {
            "method": "item/reasoning/textDelta",
            "authorization": "Bearer super-secret-token",
            "apiKey": "sk-123456789012345678901234",
            "tokenUsage": {
                "inputTokens": 100,
                "outputTokens": 50,
                "reasoningOutputTokens": 30,
                "totalTokens": 150,
            },
            "authMode": "chatgpt",
            "thought": "This is a private internal chain of thought.",
            "huge_field": "x" * 20000,
        }

        sanitized = sanitize_payload(raw, in_reasoning=True)

        serialized = json.dumps(sanitized)
        for private_value in (raw["authorization"], raw["apiKey"], raw["thought"]):
            self.assertNotIn(private_value, serialized)

        # Token usage and non-secret auth metadata must be preserved
        self.assertEqual(sanitized["tokenUsage"]["inputTokens"], 100)
        self.assertEqual(sanitized["tokenUsage"]["totalTokens"], 150)
        self.assertEqual(sanitized["authMode"], "chatgpt")

    def test_hosted_web_search_synthesizes_tool_calls(self):
        """Hosted web search without hooks must synthesize WebSearch tool start and end events."""
        self.normalizer.observe_server_message({
            "method": "turn/started",
            "params": {"threadId": "search-thread", "turn": {"id": "turn-s"}},
        })
        self.normalizer.observe_server_message({
            "method": "item/started",
            "params": {
                "threadId": "search-thread",
                "turnId": "turn-s",
                "item": {
                    "id": "ws-1",
                    "type": "webSearch",
                    "query": "OpenSCAD difference overcut",
                    "action": {"type": "search"},
                },
            },
        })
        self.normalizer.observe_server_message({
            "method": "item/completed",
            "params": {
                "threadId": "search-thread",
                "turnId": "turn-s",
                "item": {
                    "id": "ws-1",
                    "type": "webSearch",
                    "query": "OpenSCAD difference overcut",
                    "results": [{"title": "OpenSCAD User Manual", "url": "https://en.wikibooks.org/wiki/OpenSCAD_User_Manual"}],
                },
            },
        })

        starts = [e for e in self.emitted if e["type"] == "TOOL_CALL_START"]
        ends = [e for e in self.emitted if e["type"] == "TOOL_CALL_END"]

        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["tool"], "WebSearch")
        self.assertTrue(starts[0]["native_tool"])
        self.assertEqual(starts[0]["query"], "OpenSCAD difference overcut")

        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["tool"], "WebSearch")
        self.assertTrue(ends[0]["success"])

    def test_non_whitelisted_tool_names_preserved(self):
        """Custom tool names must not be forced into a fixed OMP whitelist."""
        self.normalizer.observe_server_message({
            "method": "turn/started",
            "params": {"threadId": "custom-tool-thread", "turn": {"id": "turn-c"}},
        })
        self.normalizer.observe_server_message({
            "method": "item/started",
            "params": {
                "threadId": "custom-tool-thread",
                "turnId": "turn-c",
                "item": {
                    "id": "custom-item-99",
                    "type": "docker_compose_up",
                    "arguments": {"detached": True},
                },
            },
        })
        self.normalizer.observe_server_message({
            "method": "item/completed",
            "params": {
                "threadId": "custom-tool-thread",
                "turnId": "turn-c",
                "item": {
                    "id": "custom-item-99",
                    "type": "docker_compose_up",
                    "status": "completed",
                    "result": "Containers started",
                },
            },
        })

        tool_start = next(e for e in self.emitted if e["type"] == "TOOL_CALL_START")
        tool_end = next(e for e in self.emitted if e["type"] == "TOOL_CALL_END")

        self.assertEqual(tool_start["tool"], "docker_compose_up")
        self.assertTrue(tool_start["native_tool"])
        self.assertEqual(tool_end["tool"], "docker_compose_up")
        self.assertTrue(tool_end["success"])

    def test_file_snapshots_preserve_dirty_baseline(self):
        """True pre-mutation baseline is supplied by hook; absent baseline is never fabricated from late disk read."""
        from unittest.mock import patch

        # File on disk was mutated during tool execution
        scad = self.root / "model.scad"
        scad.write_text("size = 12;\n", encoding="utf-8")

        # 1. With true hook baseline recorded prior to mutation:
        hook_baseline = [{"path": "model.scad", "stage": "before", "content": "size = 10;\n", "exists": True}]
        with patch("agent_observer.codex_stream._get_hook_before_snapshot", return_value=hook_baseline):
            self.normalizer.observe_server_message({
                "method": "turn/started",
                "params": {"threadId": "file-thread", "turn": {"id": "turn-f"}},
            })
            self.normalizer.observe_server_message({
                "method": "item/started",
                "params": {
                    "threadId": "file-thread",
                    "turnId": "turn-f",
                    "item": {
                        "id": "patch-1",
                        "type": "fileChange",
                        "changes": [{"path": "model.scad", "kind": "modify"}],
                    },
                },
            })
            self.normalizer.observe_server_message({
                "method": "item/completed",
                "params": {
                    "threadId": "file-thread",
                    "turnId": "turn-f",
                    "item": {
                        "id": "patch-1",
                        "type": "fileChange",
                        "status": "completed",
                        "changes": [{"path": "model.scad", "kind": "modify"}],
                    },
                },
            })

            tool_end = next(e for e in self.emitted if e["type"] == "TOOL_CALL_END")
            snaps = tool_end["file_snapshots"]
            before_scad = next(s for s in snaps if s["path"] == "model.scad" and s["stage"] == "before")
            after_scad = next(s for s in snaps if Path(s["path"]).name == "model.scad" and s["stage"] == "after")
            self.assertEqual(before_scad["content"], "size = 10;\n")
            self.assertEqual(after_scad["content"], "size = 12;\n")

        # 2. Without hook baseline: must NOT fabricate a before snapshot from late disk read
        self.emitted.clear()
        with patch("agent_observer.codex_stream._get_hook_before_snapshot", return_value=None):
            self.normalizer.observe_server_message({
                "method": "item/started",
                "params": {
                    "threadId": "file-thread",
                    "turnId": "turn-f",
                    "item": {
                        "id": "patch-2",
                        "type": "fileChange",
                        "changes": [{"path": "model.scad", "kind": "modify"}],
                    },
                },
            })
            self.normalizer.observe_server_message({
                "method": "item/completed",
                "params": {
                    "threadId": "file-thread",
                    "turnId": "turn-f",
                    "item": {
                        "id": "patch-2",
                        "type": "fileChange",
                        "status": "completed",
                        "changes": [{"path": "model.scad", "kind": "modify"}],
                    },
                },
            })

            tool_end2 = next(e for e in self.emitted if e["type"] == "TOOL_CALL_END")
            snaps2 = tool_end2["file_snapshots"]
            # Only after snapshot present; no fabricated before snapshot
            self.assertEqual([s["stage"] for s in snaps2], ["after"])
    def test_token_usage_deduplication_prevents_repeated_deltas(self):
        """Repeated tokenUsage notifications for the same attempt must be deduplicated."""
        self.normalizer.observe_server_message({
            "method": "turn/started",
            "params": {"threadId": "usage-thread", "turn": {"id": "turn-u"}},
        })

        usage_payload = {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "usage-thread",
                "turnId": "turn-u",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 500,
                        "outputTokens": 100,
                        "cachedInputTokens": 200,
                        "reasoningOutputTokens": 40,
                        "totalTokens": 600,
                    },
                    "total": {
                        "inputTokens": 500,
                        "outputTokens": 100,
                        "cachedInputTokens": 200,
                        "reasoningOutputTokens": 40,
                        "totalTokens": 600,
                    },
                },
            },
        }

        # Emitted 3 times in rolling updates
        self.normalizer.observe_server_message(usage_payload)
        self.normalizer.observe_server_message(usage_payload)
        self.normalizer.observe_server_message(usage_payload)

        thinking_events = [e for e in self.emitted if e["type"] == "THINKING" and e.get("usage_kind") == "token_usage"]
        self.assertEqual(len(thinking_events), 1)
        self.assertEqual(thinking_events[0]["token_metrics"]["total_tokens"], 600)


    def test_read_operands_include_multiple_files_and_rtk_without_treating_scripts_or_echoes_as_reads(self):
        cases = [
            ("rtk sed -n '1,80p' 'docs/a b.md' demo.py", ["docs/a b.md", "demo.py"]),
            ("head -n 3 docs/a.md docs/b.md", ["docs/a.md", "docs/b.md"]),
            ("rtk proxy cat -- -draft.md docs/a.md", ["-draft.md", "docs/a.md"]),
            ("sed -f rules.sed docs/a.md", ["rules.sed", "docs/a.md"]),
            ("echo docs/a.md", []),
            ("python -c 'print(\"docs/a.md\")'", []),
            ("cat docs/a.md; echo docs/b.md", []),
            ("cat $DOCUMENT docs/*.md", []),
            ("sed -i 's/a/b/' docs/a.md", []),
        ]
        for index, (command, expected) in enumerate(cases):
            with self.subTest(command=command):
                self.normalizer.observe_server_message({
                    "method": "item/started",
                    "params": {
                        "threadId": "read-operands", "turnId": "turn-read",
                        "item": {"type": "commandExecution", "id": f"read-{index}",
                                 "cwd": str(self.root), "command": command,
                                 "commandActions": [{"type": "unknown", "command": command}]},
                    },
                })
                start = next(event for event in reversed(self.emitted) if event["type"] == "TOOL_CALL_START")
                self.assertEqual(start["paths"], [str((self.root / path).resolve()) for path in expected])


if __name__ == "__main__":
    unittest.main()
