from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CORE = SCRIPTS / "teamloop-core.py"


def run_core(repo: Path, workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, str(CORE), *args, "--workspace", str(workspace)],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if check and proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    return proc


def load_core_module():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("your_ai_team_core_test", CORE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptValidationTests(unittest.TestCase):
    def test_unified_validator_accepts_repository_scripts(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "validate_scripts.py"), "--root", str(ROOT), "--json"],
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual("PASS", report["status"])
        self.assertGreater(report["files"]["powershell"], 0)
        self.assertGreater(report["files"]["bash"], 0)
        self.assertGreater(report["files"]["python"], 0)
        self.assertGreater(report["files"]["shims"], 0)

    def test_validator_rejects_bad_powershell_attribute_without_powershell_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "scripts").mkdir()
            (root / "tests").mkdir()
            (root / "scripts" / "bad.ps1").write_text(
                "param([Parameter(ValueFromRemaining=$true)][string[]]$Args)\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_scripts.py"), "--root", str(root), "--json"],
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertNotEqual(0, proc.returncode)
            report = json.loads(proc.stdout)
            self.assertEqual("FAIL", report["status"])
            self.assertTrue(any("ValueFromRemaining" in item["detail"] for item in report["results"]))


class SentinelCacheEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo, check=True)
        self.workspace = self.repo / ".teamloop"
        run_core(self.repo, self.workspace, "init-workspace", "--profile", "generic-software-task")

    def tearDown(self):
        self.temp.cleanup()

    def test_policy_change_invalidates_sentinel_cache_without_manual_clear(self):
        policy_path = self.workspace / "policies" / "scope-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        for key in ("forbiddenWrites", "alwaysForbiddenWrites"):
            if isinstance(policy.get(key), list):
                policy[key] = [item for item in policy[key] if item != ".git/**"]
        policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        first = json.loads(run_core(self.repo, self.workspace, "run-sentinel").stdout)
        scope_first = next(item for item in first["findings"] if item["category"] == "scope-policy-weakening")
        self.assertEqual("CRITICAL", scope_first["severity"])

        policy.setdefault("forbiddenWrites", []).append(".git/**")
        if "node_modules/**" not in policy["forbiddenWrites"]:
            policy["forbiddenWrites"].append("node_modules/**")
        policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        second = json.loads(run_core(self.repo, self.workspace, "run-sentinel").stdout)
        scope_second = next(item for item in second["findings"] if item["category"] == "scope-policy-weakening")
        self.assertEqual("INFO", scope_second["severity"])
        self.assertEqual("PASS", second["overallStatus"])
        self.assertGreater(second["cacheSummary"]["misses"], 0)

    def test_cached_nonpass_is_rechecked_fresh_and_replaced(self):
        core = load_core_module()
        cache = core._create_cache(str(self.workspace), str(ROOT))
        self.assertIsNotNone(cache)
        host = core.WorkspaceContext.__new__(core.WorkspaceContext)
        host.workspace = str(self.workspace)
        host.project_root = str(ROOT)
        host._WorkspaceContext__cache = {}
        host._validation_cache = cache
        host._state_store = None
        inputs = core._sentinel_cache_inputs(host, "scope-policy-weakening", git_dependent=False)
        key = cache.build_key("sentinel:scope-policy-weakening", inputs=inputs, schemas={})
        stale = {
            "category": "scope-policy-weakening",
            "severity": "CRITICAL",
            "title": "stale cached failure",
            "description": "fixture stale cache entry",
            "evidence": [{"type": "FILE_PATH", "detail": "fixture"}],
            "resolutionHint": "fixture",
        }
        cache.store(key, stale, check_id="scope-policy-weakening")

        report = json.loads(run_core(self.repo, self.workspace, "run-sentinel").stdout)
        finding = next(item for item in report["findings"] if item["category"] == "scope-policy-weakening")
        self.assertEqual("INFO", finding["severity"])
        self.assertEqual("STALE_ENTRY_RECOMPUTED", report["cacheSummary"]["action"])
        self.assertEqual(1, report["cacheSummary"]["freshRetries"])
        self.assertEqual(1, report["cacheSummary"]["staleEntriesBypassed"])


if __name__ == "__main__":
    unittest.main()
