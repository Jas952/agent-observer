"""Behavior tests for installation manager, safe plan generation, and revision guarding."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_observer.installation import InstallationManager, InstallationError


class InstallationManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project = Path(self.temp_dir.name).resolve()
        registry = patch("agent_observer.cli.REGISTRY", self.project / "isolated-registry.json")
        registry.start()
        self.addCleanup(registry.stop)
        self.manager = InstallationManager()

    def test_analyze_plan_shape_and_validation(self):
        # Invalid project rejection
        with self.assertRaises(InstallationError):
            self.manager.analyze({"project": ""})
        with self.assertRaises(InstallationError):
            self.manager.analyze({"project": "/nonexistent/path/for/sure/12345"})

        # Valid plan analysis
        plan = self.manager.analyze({"project": str(self.project), "agent": "codex"})
        self.assertFalse(plan["installed"])
        self.assertTrue(plan["can_install"])
        self.assertFalse(plan["can_start"])
        self.assertFalse(plan["can_disconnect"])

    def test_install_and_reanalyze_workflow(self):
        plan = self.manager.analyze({"project": str(self.project), "agent": "codex"})
        revision = plan["revision"]

        # Intervening change simulation: bad revision should be rejected
        with self.assertRaises(InstallationError) as ctx:
            self.manager.install({"project": str(self.project), "agent": "codex", "revision": "stale_rev_123"})
        self.assertEqual(ctx.exception.status, 409)

        # Install with correct revision
        installed_plan = self.manager.install({
            "project": str(self.project),
            "agent": "codex",
            "revision": revision,
        })
        self.assertTrue(installed_plan["installed"])
        self.assertTrue(installed_plan["can_disconnect"])

        # Project config must persist agent_runtime = "codex"
        cfg_file = self.project / ".agent-observer/config/observer.project.json"
        self.assertTrue(cfg_file.is_file())
        cfg_data = json.loads(cfg_file.read_text(encoding="utf-8"))
        self.assertEqual(cfg_data.get("agent_runtime"), "codex")

        # Hooks should be installed in .codex/hooks.json
        hooks_file = self.project / ".codex/hooks.json"
        self.assertTrue(hooks_file.is_file())
        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertIn("hooks", hooks_data)
        self.assertEqual(len(hooks_data["hooks"]), 14)

    def test_diagnose_is_read_only(self):
        plan = self.manager.diagnose(str(self.project))
        self.assertFalse(plan["installed"])
        self.assertFalse((self.project / ".agent-observer").exists())

    def test_disconnect_removes_hooks_and_guards_revisions(self):
        plan = self.manager.analyze({"project": str(self.project), "agent": "codex"})
        installed_plan = self.manager.install({"project": str(self.project), "agent": "codex", "revision": plan["revision"]})
        self.assertTrue(installed_plan["installed"])

        # Stale revision refusal
        with self.assertRaises(InstallationError) as ctx:
            self.manager.disconnect({"project": str(self.project), "revision": "outdated_rev"})
        self.assertEqual(ctx.exception.status, 409)

        # Disconnect with valid revision
        disconnected_plan = self.manager.disconnect({"project": str(self.project), "revision": installed_plan["revision"]})
        self.assertFalse(disconnected_plan["can_disconnect"])

        # Hooks should be cleaned from .codex/hooks.json
        hooks_data = json.loads((self.project / ".codex/hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(hooks_data.get("hooks"), {})


    def test_missing_revision_rejected(self):
        with self.assertRaises(InstallationError) as ctx:
            self.manager.install({"project": str(self.project), "agent": "codex"})
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(InstallationError) as ctx:
            self.manager.disconnect({"project": str(self.project)})
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(InstallationError) as ctx:
            self.manager.start_graph({"project": str(self.project)})
        self.assertEqual(ctx.exception.status, 400)

    def test_project_settings_cannot_execute_a_program_during_analysis(self):
        configuration = self.project / ".agent-observer/config/observer.project.json"
        configuration.parent.mkdir(parents=True)
        marker = self.project / "must-not-execute"
        program = self.project / "untrusted-codex"
        program.write_text("#!/bin/sh\n" + f"touch '{marker}'\n" + "echo 'codex-cli 0.166.0'\n")
        program.chmod(0o755)
        configuration.write_text(json.dumps({"schema_version": 1, "project": {"root": str(self.project)}, "port": 38123, "codex": {"executable": str(program)}}))
        self.manager.analyze({"project": str(self.project), "agent": "codex"})
        self.assertFalse(marker.exists())

    def test_codex_disconnect_preserves_the_omp_extension(self):
        from agent_observer.codex_hooks import install_codex_hooks
        shim = self.project / ".omp/extensions/agent-observer.ts"
        shim.parent.mkdir(parents=True)
        content = "// Agent Observer OMP extension shim\n// Managed by Agent Observer.\n"
        shim.write_text(content)
        install_codex_hooks(self.project, self.project / ".agent-observer")
        plan = self.manager.analyze({"project": str(self.project), "agent": "codex"})
        self.manager.disconnect({"project": str(self.project), "agent": "codex", "revision": plan["revision"]})
        self.assertEqual(shim.read_text(), content)

    def test_revisiting_folder_preserves_connection_role_and_disabled_state(self):
        plan = self.manager.analyze({"project": str(self.project), "agent": "codex"})
        self.manager.install({"project": str(self.project), "agent": "codex", "revision": plan["revision"]})
        cfg_file = self.project / ".agent-observer/config/observer.project.json"
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        data["connection"] = {"module": "codex", "repository_mode": "working_repository", "enabled": False}
        cfg_file.write_text(json.dumps(data))
        diag = self.manager.diagnose(str(self.project))
        self.assertTrue(diag["installed"])
        re_data = json.loads(cfg_file.read_text(encoding="utf-8"))
        self.assertEqual(re_data["connection"]["repository_mode"], "working_repository")
        self.assertFalse(re_data["connection"]["enabled"])

    def test_sync_adopts_identical_new_reference_and_preserves_later_local_edits(self):
        from agent_observer import cli

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, project = base / 'source', base / 'project'
            target = project / '.agent-observer'
            source.mkdir()
            target.mkdir(parents=True)
            reference = 'config/omp-reference.json'
            for root in (source, target):
                (root / reference).parent.mkdir(exist_ok=True)
                (root / reference).write_text('{"version": 1}', encoding='utf-8')
            manifest = target / 'observer.install.json'
            manifest.write_text(json.dumps({'source': str(source), 'files': {}}), encoding='utf-8')
            registry = base / 'installations.json'
            registry.write_text(json.dumps({'installations': [{'project': str(project), 'target': str(target)}]}), encoding='utf-8')
            with patch.object(cli, 'ROOT', source), patch.object(cli, 'REGISTRY', registry), patch.object(cli, 'shared_bundle', return_value={reference: reference}):
                cli.sync(quiet=True)
                (source / reference).write_text('{"version": 2}', encoding='utf-8')
                cli.sync(quiet=True)
                self.assertEqual((target / reference).read_text(), '{"version": 2}')
                (target / reference).write_text('{"user": "keep"}', encoding='utf-8')
                (source / reference).write_text('{"version": 3}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Locally edited installation'):
                    cli.sync(quiet=True)
                self.assertEqual((target / reference).read_text(), '{"user": "keep"}')

if __name__ == "__main__":
    unittest.main()
