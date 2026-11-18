"""Description precedence must not change the actual capability inventory."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_observer.capabilities import (
    capability_nodes,
    discover_mcp_servers,
    discover_rtk_integration,
    get_mcp_metadata,
    merge_capabilities,
    normalize_manual_capabilities,
    validate_inventory_schema,
)


class CapabilitiesDescriptionsTests(unittest.TestCase):
    def test_configured_servers_do_not_register_documented_operations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".omp").mkdir()
            (root / ".omp" / "mcp.json").write_text(json.dumps({
                "mcpServers": {"codegraph": {"command": "/fixture/codegraph"}}
            }), encoding="utf-8")
            with patch("agent_observer.capabilities.Path.home", return_value=root):
                discovered = discover_mcp_servers(root)
            server = next(item for item in discovered if item["name"] == "codegraph")
            self.assertEqual(server["connection"], "configured")
            self.assertEqual(server["tool_names"], [])
            self.assertEqual(server["tools"], [])
            self.assertEqual(capability_nodes({"mcp": [server]})[0]["tool_names"], [])

    def test_description_lookup_does_not_expand_reported_operations(self):
        provider_description = "Inspect the symbols exposed by this restricted workspace."
        metadata = get_mcp_metadata(
            "codegraph",
            tool_names=["codegraph_explore"],
            tools=[
                {"name": "codegraph_explore", "description": provider_description},
                {"name": "codegraph_search", "description": "Documentation for an unregistered operation"},
            ],
        )
        self.assertEqual(metadata["tools"], [
            {"name": "codegraph_explore", "description": provider_description}
        ])
        normalized = normalize_manual_capabilities({"mcp": [{
            "name": "codegraph", "tool_names": ["workspace_inspect"]
        }]})
        self.assertEqual(normalized["mcp"][0]["tool_names"], ["workspace_inspect"])
        self.assertEqual(
            [tool["name"] for tool in normalized["mcp"][0]["tools"]],
            ["workspace_inspect"],
        )

    def test_manual_purpose_survives_runtime_rescan_without_losing_provider_metadata(self):
        manual_description = "Workspace-specific navigation of the mechanical assembly sources."
        provider_description = "Explore this server's restricted assembly symbol index."
        runtime = validate_inventory_schema({
            "source": "omp-runtime",
            "mcp": [{
                "name": "codegraph", "connection": "connected", "availability": "available",
                "description": "Runtime MCP server 'codegraph'",
                "tool_names": ["codegraph_explore"],
                "tools": [{"name": "codegraph_explore", "description": provider_description}],
            }],
        })
        manual = {"mcp": [{"name": "codegraph", "description": manual_description}]}
        merged = merge_capabilities(runtime, manual)
        node = capability_nodes(merged)[0]
        self.assertEqual(node["description"], manual_description)
        self.assertEqual(node["description_source"], "manual")
        self.assertEqual(node["connection"], "connected")
        self.assertEqual(node["tool_names"], ["codegraph_explore"])
        self.assertEqual(node["tools"], [
            {"name": "codegraph_explore", "description": provider_description}
        ])

    def test_removed_runtime_operations_do_not_return_from_reference_metadata(self):
        def inventory(names):
            return validate_inventory_schema({"source": "omp-runtime", "mcp": [{
                "name": "apple-docs", "connection": "connected", "tool_names": names, "tools": []
            }]})
        previous = capability_nodes(inventory(["search_apple_docs"]))[0]
        self.assertEqual(previous["tool_names"], ["search_apple_docs"])
        current = capability_nodes(inventory([]))[0]
        self.assertEqual(current["tool_names"], [])
        self.assertEqual(current["tools"], [])

    def test_rtk_stays_a_cli_integration_in_a_mixed_inventory(self):
        with patch("agent_observer.capabilities.shutil.which", return_value="/fixture/rtk"):
            rtk = discover_rtk_integration()
        inventory = validate_inventory_schema({
            "mcp": [{"name": "codegraph", "connection": "configured"}],
            "integrations": [rtk],
        })
        nodes = capability_nodes(inventory)
        self.assertEqual({node["kind"] for node in nodes}, {"mcp", "integration"})
        wrapper = next(node for node in nodes if node["id"] == "integration_rtk")
        self.assertEqual(wrapper["kind"], "integration")
        self.assertEqual(wrapper["type"], "cli-wrapper")
        self.assertFalse(any(node["kind"] == "mcp" and node["name"] == "rtk" for node in nodes))

    def test_invalid_description_inventory_shapes_are_rejected(self):
        for item in [
            {"name": "custom", "tools": "not-an-array"},
            {"name": "custom", "tools": [{"name": 123}]},
            {"name": "custom", "github_url": 999},
        ]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_inventory_schema({"mcp": [item]})


if __name__ == "__main__":
    unittest.main()
