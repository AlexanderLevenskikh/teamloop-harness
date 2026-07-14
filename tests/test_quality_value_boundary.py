import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import quality_value_boundary as qvb


class QualityValueBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / ".teamloop"
        (self.workspace / "policies").mkdir(parents=True)
        shutil.copy2(ROOT / "templates" / "workspace" / "policies" / "boundary-policy.json", self.workspace / "policies" / "boundary-policy.json")
        (self.root / "src").mkdir()
        (self.root / "evidence").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_artifact(self, text="working implementation"):
        (self.root / "src" / "result.txt").write_text(text, encoding="utf-8")

    def write_validation(self, status="PASS", input_fp=""):
        data = {"status": status}
        if input_fp:
            data["inputFingerprint"] = input_fp
        (self.root / "evidence" / "validation.json").write_text(json.dumps(data), encoding="utf-8")

    def write_findings(self, findings):
        (self.root / "evidence" / "findings.json").write_text(json.dumps({"findings": findings}), encoding="utf-8")

    def contract(self, boundary_id="boundary-1", profile="standard", predecessor="", findings=False, candidates=None, bind_validation=False):
        contract = {
            "boundaryId": boundary_id,
            "taskId": f"task-{boundary_id}",
            "runId": f"run-{boundary_id}",
            "profile": profile,
            "adapterId": "generic-software-task",
            "expectedDeliverables": [
                {"id": "result", "path": "src/result.txt", "required": True, "minBytes": 1}
            ],
            "validationEvidence": [
                {"id": "tests", "path": "evidence/validation.json", "required": True, "statusField": "status", "passValues": ["PASS"], "bindToPrimaryArtifacts": bind_validation, "inputFingerprintField": "inputFingerprint"}
            ],
            "findingSources": ([{"id": "review", "path": "evidence/findings.json", "authority": "authoritative"}] if findings else []),
            "improvementCandidates": candidates or [],
            "predecessorBoundaryId": predecessor,
        }
        return qvb.create_contract(self.workspace, contract, project_root=self.root)

    def measure(self, boundary_id="boundary-1"):
        return qvb.measure_boundary(self.workspace, boundary_id, project_root=self.root)

    def accept(self, boundary_id="boundary-1", debt=()):
        decision = "ACCEPT_WITH_RECORDED_SOFT_DEBT" if debt else "ACCEPT_BOUNDARY"
        return qvb.record_decision(self.workspace, boundary_id, decision, soft_debt_ids=debt, project_root=self.root)

    def test_hard_gate_failure_cannot_be_accepted(self):
        self.write_artifact()
        self.write_validation("FAIL")
        self.contract()
        packet = self.measure()
        self.assertTrue(packet["hardInvariants"])
        with self.assertRaisesRegex(qvb.BoundaryError, "hard invariants"):
            self.accept()

    def test_soft_debt_requires_complete_explicit_receipt(self):
        self.write_artifact()
        self.write_validation()
        self.write_findings([{"issueId": "debt-1", "severity": "low", "blocking": False, "summary": "Polish docs", "rootPatternId": "docs-polish"}])
        self.contract(findings=True)
        self.measure()
        with self.assertRaisesRegex(qvb.BoundaryError, "soft debt exists"):
            self.accept()
        with self.assertRaisesRegex(qvb.BoundaryError, "explicit debt list"):
            qvb.record_decision(self.workspace, "boundary-1", "ACCEPT_WITH_RECORDED_SOFT_DEBT", project_root=self.root)
        result = self.accept(debt=["debt-1"])
        self.assertEqual("ACCEPTED", result["boundaryState"]["mode"])
        self.assertEqual("PASS", qvb.verify_acceptance(self.workspace, "boundary-1", project_root=self.root)["status"])

    def test_high_reach_root_fix_outranks_leaf_fix(self):
        self.write_artifact()
        self.write_validation()
        self.write_findings([
            {"issueId": "root", "severity": "high", "blocking": True, "summary": "Shared schema broken", "rootPatternId": "shared-schema", "affectedItems": ["a", "b", "c", "d"], "repetition": 4, "confidence": 0.9, "estimatedCost": 2},
            {"issueId": "leaf", "severity": "medium", "blocking": False, "summary": "One typo", "rootPatternId": "leaf-typo", "affectedItems": ["z"], "repetition": 1, "confidence": 0.9, "estimatedCost": 1},
        ])
        self.contract(findings=True)
        packet = self.measure()
        self.assertEqual("shared-schema", packet["rootPatterns"][0]["rootPatternId"])
        self.assertGreater(packet["rootPatterns"][0]["expectedPayoff"], packet["rootPatterns"][1]["expectedPayoff"])

    def test_improvement_requires_measured_before_after_progress(self):
        self.write_artifact("TODO")
        self.write_validation()
        self.contract(candidates=[{"candidateId": "fix-result", "rootPatternId": "placeholder_implementation", "summary": "Implement result", "affectedItems": 1, "repetitionOrReuse": 1, "severity": "high", "confidenceOfSafeFix": 1, "estimatedCost": 1}])
        packet = self.measure()
        self.assertTrue(packet["hardInvariants"])
        qvb.record_decision(self.workspace, "boundary-1", "IMPROVE_CURRENT_BOUNDARY", selected_candidate_id="fix-result", project_root=self.root)
        self.write_artifact("real implementation")
        outcome = qvb.complete_improvement(self.workspace, "boundary-1", project_root=self.root)
        self.assertEqual("COMPLETED", outcome["status"])
        self.assertGreater(outcome["record"]["measuredDelta"], 0)

    def test_no_progress_and_budget_exhaustion_stop_honestly(self):
        self.write_artifact("TODO")
        self.write_validation()
        self.contract(profile="fast", candidates=[{"candidateId": "fix-result", "rootPatternId": "placeholder_implementation", "summary": "Implement result", "affectedItems": 1, "repetitionOrReuse": 1, "severity": "high", "confidenceOfSafeFix": 1, "estimatedCost": 1}])
        self.measure()
        qvb.record_decision(self.workspace, "boundary-1", "IMPROVE_CURRENT_BOUNDARY", selected_candidate_id="fix-result", project_root=self.root)
        first = qvb.complete_improvement(self.workspace, "boundary-1", project_root=self.root)
        self.assertEqual("NO_PROGRESS", first["status"])
        state = qvb.load_state(self.workspace, "boundary-1")
        self.assertEqual(1, state["noProgressStreak"])
        result = qvb.record_decision(self.workspace, "boundary-1", "STOP_BUDGET_EXHAUSTED", reason="No progress threshold reached", project_root=self.root)
        self.assertEqual("STOPPED_BUDGET_EXHAUSTED", result["boundaryState"]["mode"])

    def test_fast_changes_cycles_not_hard_quality(self):
        self.write_artifact("TODO")
        self.write_validation()
        self.contract(profile="fast")
        packet = self.measure()
        state = qvb.load_state(self.workspace, "boundary-1")
        self.assertEqual(2, state["maxImprovementCycles"])
        self.assertTrue(any(x["type"] == "PLACEHOLDER_IMPLEMENTATION" for x in packet["hardInvariants"]))

    def test_later_stage_is_locked_without_acceptance(self):
        self.write_artifact()
        self.write_validation()
        self.contract()
        self.measure()
        status = qvb.advancement_lock_status(self.workspace, project_root=self.root)
        self.assertEqual("FAIL", status["status"])

    def test_artifact_drift_invalidates_acceptance(self):
        self.write_artifact()
        self.write_validation()
        self.contract()
        self.measure()
        self.accept()
        self.write_artifact("changed after acceptance")
        with self.assertRaisesRegex(qvb.BoundaryError, "drift"):
            qvb.verify_acceptance(self.workspace, "boundary-1", project_root=self.root)

    def test_edited_packet_cannot_grant_acceptance(self):
        self.write_artifact("TODO")
        self.write_validation()
        self.contract()
        packet = self.measure()
        packet["hardInvariants"] = []
        packet["metrics"]["hardFailureCount"] = 0
        qvb.save_json(qvb.packet_path(self.workspace, "boundary-1"), packet)
        with self.assertRaisesRegex(qvb.BoundaryError, "hard invariants"):
            self.accept()

    def test_copied_validation_result_cannot_validate_changed_input(self):
        self.write_artifact()
        self.contract(bind_validation=True)
        current = qvb.current_primary_artifact_fingerprint(self.workspace, "boundary-1", project_root=self.root)
        self.write_validation("PASS", current)
        self.measure()
        self.accept()
        self.write_artifact("changed while the old PASS evidence was copied forward")
        with self.assertRaisesRegex(qvb.BoundaryError, "drift|hard invariants"):
            qvb.verify_acceptance(self.workspace, "boundary-1", project_root=self.root)

    def test_evidence_only_edit_cannot_count_as_improvement(self):
        self.write_artifact("real implementation")
        self.write_validation()
        self.write_findings([{"issueId": "root", "severity": "high", "blocking": True, "summary": "Shared failure", "rootPatternId": "shared-failure"}])
        self.contract(findings=True, candidates=[{"candidateId": "fix-root", "rootPatternId": "shared-failure", "summary": "Fix shared failure", "affectedItems": 3, "repetitionOrReuse": 2, "severity": "high", "confidenceOfSafeFix": 1, "estimatedCost": 1}])
        self.measure()
        qvb.record_decision(self.workspace, "boundary-1", "IMPROVE_CURRENT_BOUNDARY", selected_candidate_id="fix-root", project_root=self.root)
        self.write_findings([])  # maliciously edits evidence, but leaves primary artifacts unchanged
        result = qvb.complete_improvement(self.workspace, "boundary-1", project_root=self.root)
        self.assertEqual("FAILED", result["status"])
        self.assertFalse(result["record"]["primaryArtifactChanged"])
        self.assertIn("evidence-only", result["record"]["outcomeReason"])

    def test_contract_paths_cannot_escape_project_root(self):
        contract = {
            "boundaryId": "boundary-escape", "profile": "standard", "adapterId": "generic-software-task",
            "expectedDeliverables": [{"id": "escape", "path": "../outside.txt", "required": True}],
            "validationEvidence": [], "findingSources": [], "improvementCandidates": []
        }
        with self.assertRaisesRegex(qvb.BoundaryError, "escapes project root"):
            qvb.create_contract(self.workspace, contract, project_root=self.root)

    def test_authoritative_high_finding_cannot_be_relabelled_soft(self):
        self.write_artifact()
        self.write_validation()
        self.write_findings([{"issueId": "critical-ish", "severity": "high", "blocking": False, "summary": "Agent tried to soften it", "rootPatternId": "hard-root"}])
        self.contract(findings=True)
        packet = self.measure()
        self.assertIn("critical-ish", [item["issueId"] for item in packet["hardInvariants"]])
        with self.assertRaisesRegex(qvb.BoundaryError, "hard invariants"):
            self.accept()


    def test_manager_decision_alone_cannot_grant_acceptance(self):
        self.write_artifact()
        self.write_validation()
        self.contract()
        self.measure()
        qvb.save_json(qvb.boundary_dir(self.workspace, "boundary-1") / "boundary-decision.json", {"decision": "ACCEPT_BOUNDARY"})
        with self.assertRaisesRegex(qvb.BoundaryError, "receipt missing"):
            qvb.verify_acceptance(self.workspace, "boundary-1", project_root=self.root)

    def test_stale_or_replayed_role_receipt_is_rejected(self):
        self.write_artifact()
        self.write_validation()
        self.contract()
        self.measure()
        self.accept()
        role_path = qvb.boundary_dir(self.workspace, "boundary-1") / "manager-role-receipt.json"
        role = qvb.load_json(role_path)
        role["packetFingerprint"] = "0" * 64
        role["receiptFingerprint"] = qvb.fingerprint(qvb._without_fingerprint(role, "receiptFingerprint"))
        qvb.save_json(role_path, role)
        with self.assertRaisesRegex(qvb.BoundaryError, "role receipt"):
            qvb.verify_acceptance(self.workspace, "boundary-1", project_root=self.root)

    def test_manipulated_history_fails_closed(self):
        self.write_artifact("TODO")
        self.write_validation()
        self.contract(candidates=[{"candidateId": "fix", "rootPatternId": "placeholder_implementation", "summary": "Fix", "affectedItems": 1, "repetitionOrReuse": 1, "severity": "high", "confidenceOfSafeFix": 1, "estimatedCost": 1}])
        self.measure()
        qvb.record_decision(self.workspace, "boundary-1", "IMPROVE_CURRENT_BOUNDARY", selected_candidate_id="fix", project_root=self.root)
        history = qvb.boundary_dir(self.workspace, "boundary-1") / "decision-history.jsonl"
        records = qvb.load_jsonl(history)
        records[0]["decision"] = "ACCEPT_BOUNDARY"
        history.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(qvb.BoundaryError, "recordHash"):
            qvb.verify_histories(self.workspace, "boundary-1")

    def test_early_boundary_drift_invalidates_predecessor_chain(self):
        self.write_artifact()
        self.write_validation()
        self.contract("boundary-1")
        self.measure("boundary-1")
        self.accept("boundary-1")
        (self.root / "src" / "second.txt").write_text("ok", encoding="utf-8")
        contract2 = {
            "boundaryId": "boundary-2", "taskId": "task-2", "runId": "run-2", "profile": "standard", "adapterId": "generic-software-task",
            "expectedDeliverables": [{"id": "second", "path": "src/second.txt", "required": True}],
            "validationEvidence": [{"id": "tests", "path": "evidence/validation.json", "required": True, "statusField": "status", "passValues": ["PASS"]}],
            "findingSources": [], "improvementCandidates": [], "predecessorBoundaryId": "boundary-1"
        }
        qvb.create_contract(self.workspace, contract2, project_root=self.root)
        self.measure("boundary-2")
        self.accept("boundary-2")
        self.write_artifact("drifted predecessor")
        with self.assertRaisesRegex(qvb.BoundaryError, "predecessor|drift"):
            qvb.verify_acceptance(self.workspace, "boundary-2", project_root=self.root)

    def test_active_or_incomplete_work_cannot_pass_as_final(self):
        self.write_artifact("TODO")
        self.write_validation()
        self.contract()
        self.measure()
        status = qvb.advancement_lock_status(self.workspace, project_root=self.root)
        self.assertEqual("FAIL", status["status"])
        self.assertIn("NEEDS_DECISION", status["blockingBoundaries"][0]["reason"])

    def test_restart_restores_same_boundary_state(self):
        self.write_artifact("TODO")
        self.write_validation()
        self.contract(candidates=[{"candidateId": "fix", "rootPatternId": "placeholder_implementation", "summary": "Fix", "affectedItems": 1, "repetitionOrReuse": 1, "severity": "high", "confidenceOfSafeFix": 1, "estimatedCost": 1}])
        self.measure()
        qvb.record_decision(self.workspace, "boundary-1", "IMPROVE_CURRENT_BOUNDARY", selected_candidate_id="fix", project_root=self.root)
        before = qvb.load_state(self.workspace, "boundary-1")
        del sys.modules["quality_value_boundary"]
        import quality_value_boundary as reloaded
        after = reloaded.load_state(self.workspace, "boundary-1")
        self.assertEqual(before["mode"], after["mode"])
        self.assertEqual(before["nextPermittedAction"], after["nextPermittedAction"])
        self.assertEqual(before["remainingImprovementCycles"], after["remainingImprovementCycles"])


    def test_boundary_manager_cannot_weaken_trusted_writer_policy(self):
        policy_path = self.workspace / "policies" / "boundary-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["managerMayWriteReceipts"] = True
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaisesRegex(qvb.BoundaryError, "trusted-writer contract"):
            qvb.load_policy(self.workspace)

    def test_canonical_final_gate_component_tracks_receipt_chain(self):
        self.write_artifact()
        self.write_validation()
        self.contract()
        self.measure()
        before = qvb.final_gate_check(self.workspace, project_root=self.root)
        self.assertEqual("FAIL", before["status"])
        self.accept()
        after = qvb.final_gate_check(self.workspace, project_root=self.root)
        self.assertEqual("PASS", after["status"])
        self.assertEqual(["boundary-1"], after["acceptedBoundaries"])

    def test_dashboard_keeps_draft_and_accepted_progress_distinct(self):
        self.write_artifact()
        self.write_validation()
        self.contract()
        self.measure()
        draft = qvb.dashboard_status(self.workspace, "boundary-1", project_root=self.root)
        self.assertEqual(1.0, draft["draftCoverage"])
        self.assertEqual(0.0, draft["acceptedProgress"])
        self.accept()
        accepted = qvb.dashboard_status(self.workspace, "boundary-1", project_root=self.root)
        self.assertEqual(1.0, accepted["draftCoverage"])
        self.assertEqual(1.0, accepted["acceptedProgress"])
        html = qvb.render_dashboard_html(accepted)
        self.assertIn("Accepted progress", html)
        self.assertIn("Draft coverage", html)
        self.assertIn('class="hint"', html)
        self.assertIn("Generated files, closed tickets", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
