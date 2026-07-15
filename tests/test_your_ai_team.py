import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import your_ai_team as team
import codex_support


class YourAITeamTests(unittest.TestCase):
    def test_landing_page_uses_vibe_coder_not_full_department(self):
        p = team.propose("Сделай простой лендинг-визитку для кофейни", backend="codex")
        ids = [r["roleId"] for r in p["team"]]
        self.assertIn("delivery-manager", ids)
        self.assertIn("vibe-coder", ids)
        self.assertNotIn("senior-engineer", ids)
        self.assertNotIn("researcher", ids)

    def test_research_task_has_no_developer(self):
        p = team.propose("Исследуй варианты оркестрации AI-команд и сравни риски")
        ids = [r["roleId"] for r in p["team"]]
        self.assertIn("researcher", ids)
        self.assertFalse(any(i in ids for i in ("implementer", "senior-engineer", "vibe-coder")))

    def test_migration_is_richer_and_more_expensive(self):
        simple = team.propose("Сделай лендинг")
        migration = team.propose("Мигрируй production Selenium suite на Playwright с архитектурными изменениями")
        self.assertGreater(migration["budget"]["expectedTokens"], simple["budget"]["expectedTokens"])
        self.assertIn("architect", [r["roleId"] for r in migration["team"]])

    def test_coordination_overhead_is_explicit(self):
        p = team.propose("Мигрируй Selenium в Playwright")
        self.assertGreater(p["budget"]["coordinationOverheadTokens"], 0)
        self.assertGreater(p["budget"]["expectedTokens"], p["budget"]["directRoleTokens"])

    def test_bugfix_intent_beats_tool_name(self):
        p = team.propose("Почини flaky Playwright тест в CI")
        self.assertEqual("bugfix", p["analysis"]["kind"])
        self.assertNotIn("architect", [r["roleId"] for r in p["team"]])

    def test_budget_optimizer_downgrades_and_reports_tradeoffs(self):
        p = team.propose("Почини flaky Playwright тест в CI", max_tokens=25000, preference="cost")
        self.assertTrue(p["tradeoffs"] or p["budget"]["expectedTokens"] <= 25000)

    def test_impossible_budget_is_not_green(self):
        p = team.propose("Исправь критическую уязвимость авторизации в production", max_tokens=3000)
        self.assertEqual("BUDGET_UNSATISFIED", p["status"])
        self.assertTrue(p["unmetConstraints"])

    def test_mutating_task_has_required_quality_value_manager(self):
        p = team.propose("Почини баг в production")
        role = next(r for r in p["team"] if r["roleId"] == "quality-value-manager")
        self.assertTrue(role["required"])
        self.assertEqual("final-only", role["engagement"])
        self.assertFalse(role["mutatesWorkspace"])

    def test_quality_value_manager_cannot_be_removed(self):
        p = team.propose("Почини баг в production")
        n = team.negotiate(p, request="Убери boundary manager")
        self.assertIn("quality-value-manager", [r["roleId"] for r in n["team"]])
        self.assertTrue(any(t["action"] == "refused" for t in n["tradeoffs"]))

    def test_materialized_quality_value_manager_is_narrow_and_read_only(self):
        p = team.accept(team.propose("Почини баг в production", backend="opencode"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "opencode", td)
            config = json.loads((Path(td) / "opencode.json").read_text())
            permission = config["agent"]["quality-value-manager"]["permission"]
            self.assertEqual("deny", permission["edit"])
            self.assertEqual("deny", permission["task"]["*"])
            self.assertEqual("allow", permission["bash"]["scripts/boundary-status.sh *"])
            self.assertEqual("allow", permission["bash"]["scripts/boundary-decide.sh *"])
            self.assertEqual("deny", permission["bash"]["*"])

    def test_mutating_task_has_required_quality_value_manager(self):
        p = team.propose("Почини баг в production")
        role = next(r for r in p["team"] if r["roleId"] == "quality-value-manager")
        self.assertTrue(role["required"])
        self.assertEqual("final-only", role["engagement"])
        self.assertFalse(role["mutatesWorkspace"])

    def test_quality_value_manager_cannot_be_removed(self):
        p = team.propose("Почини баг в production")
        n = team.negotiate(p, request="Убери boundary manager")
        self.assertIn("quality-value-manager", [r["roleId"] for r in n["team"]])
        self.assertTrue(any(t["action"] == "refused" for t in n["tradeoffs"]))

    def test_materialized_quality_value_manager_is_narrow_and_read_only(self):
        p = team.accept(team.propose("Почини баг в production", backend="opencode"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "opencode", td)
            config = json.loads((Path(td) / "opencode.json").read_text())
            permission = config["agent"]["quality-value-manager"]["permission"]
            self.assertEqual("deny", permission["edit"])
            self.assertEqual("deny", permission["task"]["*"])
            self.assertEqual("allow", permission["bash"]["scripts/boundary-status.sh *"])
            self.assertEqual("allow", permission["bash"]["scripts/boundary-decide.sh *"])
            self.assertEqual("deny", permission["bash"]["*"])

    def test_manager_cannot_be_removed(self):
        p = team.propose("Сделай простой лендинг")
        n = team.negotiate(p, request="Убери менеджера")
        self.assertIn("delivery-manager", [r["roleId"] for r in n["team"]])
        self.assertTrue(any(t["action"] == "refused" for t in n["tradeoffs"]))

    def test_required_role_needs_explicit_risk_acceptance(self):
        p = team.propose("Мигрируй production Selenium suite на Playwright")
        n = team.negotiate(p, request="Убери senior")
        self.assertIn("senior-engineer", [r["roleId"] for r in n["team"]])
        n2 = team.negotiate(p, request="Убери senior, принимаю риск")
        self.assertNotIn("senior-engineer", [r["roleId"] for r in n2["team"]])

    def test_natural_bargain_parses_token_cap(self):
        p = team.propose("Почини flaky Playwright тест")
        n = team.negotiate(p, request="Влезь в максимум 25к токенов, ревьюер только в конце")
        self.assertEqual(25000, n["constraints"]["maxTokens"])

    def test_unaccepted_contract_cannot_materialize(self):
        p = team.propose("Сделай лендинг")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                team.materialize(p, "codex", td)

    def test_codex_materialization_is_bounded(self):
        p = team.accept(team.propose("Почини flaky Playwright тест", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            result = team.materialize(p, "codex", td)
            config = (Path(td) / ".codex" / "config.toml").read_text()
            self.assertIn("max_depth = 1", config)
            self.assertTrue((Path(td) / ".agents" / "skills" / "your-ai-team" / "SKILL.md").exists())
            self.assertTrue(any(f.startswith(".codex/agents/") for f in result["files"]))

    def test_codex_default_inherits_parent_model_for_chatgpt_compatibility(self):
        p = team.accept(team.propose("Обнови README", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            result = team.materialize(p, "codex", td)
            self.assertEqual("inherit", result["modelMode"])
            agent_files = list((Path(td) / ".codex" / "agents").glob("*.toml"))
            self.assertTrue(agent_files)
            for path in agent_files:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("model", data)
                self.assertNotEqual("gpt-5.6", data.get("model"))

    def test_codex_chatgpt_mode_uses_sol_terra_luna_not_generic_model(self):
        p = team.accept(team.propose("Почини production баг", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "codex", td, codex_model_mode="chatgpt")
            models = set()
            for path in (Path(td) / ".codex" / "agents").glob("*.toml"):
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                models.add(data["model"])
                self.assertIn(data["model"], set(team.CODEX_CHATGPT_GRADE_MODELS.values()))
                self.assertNotEqual("gpt-5.6", data["model"])
            self.assertTrue(models)

    def test_codex_materialization_merges_existing_project_config(self):
        p = team.accept(team.propose("Обнови README", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / ".codex" / "config.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                'model = "gpt-5.4"\napproval_policy = "on-request"\n\n[agents]\nmax_threads = 99\n',
                encoding="utf-8",
            )
            team.materialize(p, "codex", td)
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("gpt-5.4", data["model"])
            self.assertEqual("on-request", data["approval_policy"])
            self.assertEqual(1, data["agents"]["max_depth"])
            self.assertEqual(p["budget"]["maxConcurrentWorkers"], data["agents"]["max_threads"])

    def test_codex_materialization_preserves_project_agents_and_adds_root_delivery_guidance(self):
        p = team.accept(team.propose("Обнови README", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            agents = Path(td) / "AGENTS.md"
            agents.write_text("# Existing project guidance\n\n- Keep this rule.\n", encoding="utf-8")
            team.materialize(p, "codex", td)
            text = agents.read_text(encoding="utf-8")
            self.assertIn("Keep this rule", text)
            self.assertIn(team.CODEX_GUIDANCE_BEGIN, text)
            self.assertIn("root Codex thread acts as the Delivery Manager", text)
            # Re-materialization updates one managed block instead of duplicating it.
            team.materialize(p, "codex", td)
            self.assertEqual(1, agents.read_text(encoding="utf-8").count(team.CODEX_GUIDANCE_BEGIN))

    def test_codex_doctor_requires_root_guidance_for_full_materialization(self):
        p = team.accept(team.propose("Обнови README", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "codex", td)
            report = codex_support.inspect_codex_project(td, run_cli=False)
            check = next(item for item in report["checks"] if item["id"] == "root-delivery-guidance")
            self.assertEqual("PASS", check["status"])

    def test_codex_live_smoke_reports_unsupported_custom_agent_model(self):
        p = team.accept(team.propose("Обнови README", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "codex", td)
            fake = SimpleNamespace(
                returncode=1,
                stdout='{"type":"turn.failed"}\n',
                stderr="The 'gpt-5.6' model is not supported when using Codex with a ChatGPT account.",
            )
            with patch.object(codex_support, "inspect_codex_project", return_value={"status": "PASS"}), \
                 patch.object(codex_support.shutil, "which", return_value="codex"), \
                 patch.object(codex_support.subprocess, "run", return_value=fake):
                report = codex_support.run_live_smoke(td, role="writer")
            self.assertEqual("FAIL", report["status"])
            self.assertEqual("UNSUPPORTED_AGENT_MODEL", report["code"])
            self.assertEqual("RUN_CODEX_DOCTOR_FIX_MODELS_INHERIT_AND_RESTART", report["recommendedNextAction"])

    def test_codex_skill_is_full_delivery_lifecycle_not_only_materialization(self):
        p = team.accept(team.propose("Почини баг", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "codex", td)
            skill = (Path(td) / ".agents" / "skills" / "your-ai-team" / "SKILL.md").read_text(encoding="utf-8")
            for marker in ("root Delivery Manager", "final-gate", "boundary", "only accepted roles", "unsupported model"):
                self.assertIn(marker.lower(), skill.lower())

    def test_codex_doctor_detects_generic_model_and_can_remove_pins(self):
        p = team.accept(team.propose("Обнови README", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "codex", td, codex_model_mode="chatgpt")
            agent = next((Path(td) / ".codex" / "agents").glob("*.toml"))
            text = agent.read_text(encoding="utf-8")
            text = __import__("re").sub(r'^model\s*=.*$', 'model = "gpt-5.6"', text, flags=__import__("re").MULTILINE)
            agent.write_text(text, encoding="utf-8")
            report = codex_support.inspect_codex_project(td, run_cli=False)
            self.assertEqual("WARN", report["status"])
            self.assertTrue(any("failed on some ChatGPT-account" in warning for warning in report["warnings"]))
            codex_support.apply_model_mode(td, "inherit")
            data = tomllib.loads(agent.read_text(encoding="utf-8"))
            self.assertNotIn("model", data)

    def test_codex_materialization_writes_provenance_manifest(self):
        p = team.accept(team.propose("Исследуй архитектуру", backend="codex"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "codex", td)
            manifest = json.loads((Path(td) / "your-ai-team-codex.json").read_text(encoding="utf-8"))
            self.assertEqual("codex", manifest["backend"])
            self.assertEqual("inherit", manifest["modelMode"])
            self.assertEqual(1, manifest["maxDepth"])
            self.assertTrue(manifest["rootDeliveryManager"])
            self.assertEqual(len(p["team"]), len(manifest["agents"]))

    def test_opencode_materialization_allows_only_selected_subagents(self):
        p = team.accept(team.propose("Исследуй архитектуру проекта", backend="opencode"))
        with tempfile.TemporaryDirectory() as td:
            team.materialize(p, "opencode", td)
            config = json.loads((Path(td) / "opencode.json").read_text())
            permissions = config["agent"]["delivery-manager"]["permission"]["task"]
            self.assertEqual("deny", permissions["*"])
            self.assertEqual("allow", permissions["researcher"])
            self.assertNotIn("implementer", permissions)

    def test_accept_freezes_contract(self):
        p = team.accept(team.propose("Обнови README"))
        self.assertEqual("ACCEPTED", p["status"])
        self.assertTrue(p["acceptance"]["accepted"])
        self.assertTrue(p["acceptance"]["acceptedFingerprint"])

    def test_each_example_has_manager(self):
        tasks = [
            "Сделай лендинг",
            "Исследуй библиотеку",
            "Почини баг",
            "Проведи code review",
            "Мигрируй Selenium на Playwright",
            "Обнови зависимости в Yarn проекте",
        ]
        for task_text in tasks:
            with self.subTest(task=task_text):
                self.assertEqual("delivery-manager", team.propose(task_text)["team"][0]["roleId"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
