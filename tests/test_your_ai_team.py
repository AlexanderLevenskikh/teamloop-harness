import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import your_ai_team as team


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
