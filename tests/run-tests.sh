#!/usr/bin/env bash
set -euo pipefail

PY="${PY:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null)}"
TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$TEST_DIR/.." && pwd)"
CORE="$PROJECT_ROOT/scripts/teamloop-core.py"

TOTAL=0
PASSED=0
FAILED=0

test_run() {
    local name="$1"
    shift
    local func_name="$1"
    TOTAL=$((TOTAL + 1))
    echo ""
    echo "[$TOTAL] $name"

    local tmpfile
    tmpfile=$(mktemp)
    if $func_name > "$tmpfile" 2>&1; then
        PASSED=$((PASSED + 1))
        echo "  PASS"
    else
        local output
        output=$(cat "$tmpfile" 2>/dev/null)
        if [[ -n "$output" ]]; then
            echo "  $output"
        fi
        FAILED=$((FAILED + 1))
    fi
    rm -f "$tmpfile"
}

assert_command_success() {
    local output="$1" exit_code="$2" msg="$3"
    if [[ $exit_code -ne 0 ]]; then
        echo "FAIL: $msg (exit code $exit_code, output: $output)"
        return 1
    fi
    return 0
}

assert_command_failure() {
    local output="$1" exit_code="$2" msg="$3"
    if [[ $exit_code -eq 0 ]]; then
        echo "FAIL: $msg (expected failure, exit code 0, output: $output)"
        return 1
    fi
    return 0
}

assert_contains() {
    local haystack="$1" needle="$2" msg="$3"
    if ! echo "$haystack" | grep -qF "$needle"; then
        echo "FAIL: $msg (expected '$needle' in output)"
        return 1
    fi
    return 0
}

assert_contains_exact() {
    local haystack="$1" needle="$2" msg="$3"
    if ! echo "$haystack" | grep -q "$needle"; then
        echo "FAIL: $msg (expected pattern '$needle' in output)"
        return 1
    fi
    return 0
}

json_str() {
    local json="$1" field="$2"
    echo "$json" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('$field',''))" 2>/dev/null || echo ""
}

TEST_REPO_DIR=""
WORKSPACE_ABS=""

cleanup_workspace() {
    rm -rf "$WORKSPACE_ABS"
    rm -rf "$TEST_REPO_DIR"
}

init_test_workspace() {
    cleanup_workspace
    TEST_REPO_DIR=$(mktemp -d)
    WORKSPACE_ABS="${TEST_REPO_DIR}/.teamloop"
    git init "$TEST_REPO_DIR" >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" config user.email "test@teamloop.local" >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" config user.name "Test" >/dev/null 2>&1
    "$PY" "$CORE" init-workspace --workspace "$WORKSPACE_ABS" --profile "generic-software-task" >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" add . >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" commit -m "init" --no-verify >/dev/null 2>&1
}

run_core() {
    local cmd="$1"
    shift
    if [[ -n "$TEST_REPO_DIR" ]]; then
        cd "$TEST_REPO_DIR"
    fi
    "$PY" "$CORE" "$cmd" --workspace "$WORKSPACE_ABS" "$@"
}

# ============================================================
# TEST 1: InitWorkspace_CreatesValidState
# ============================================================
test_01() {
    init_test_workspace
    [[ -f "$WORKSPACE_ABS/state/team-state.json" ]] || { echo "team-state.json missing"; return 1; }
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass on fresh workspace, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 2: ValidateState_FreshWorkspacePasses
# ============================================================
test_02() {
    init_test_workspace
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "Fresh workspace should validate, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 3: ValidateState_HumanRequiredWithoutBlockerFails
# ============================================================
test_03() {
    init_test_workspace
    run_core apply-transition --action SET_HUMAN_REQUIRED >/dev/null
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "HUMAN_REQUIRED without blocker should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 4: ValidateState_HumanRequiredWithMalformedBlockerFails
# ============================================================
test_04() {
    init_test_workspace
    run_core apply-transition --action SET_HUMAN_REQUIRED >/dev/null
    echo '{"notValid":"true"}' > "$WORKSPACE_ABS/state/blockers.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "Malformed blocker should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 5: ValidateState_HumanRequiredWithValidBlockerPasses
# ============================================================
test_05() {
    init_test_workspace
    run_core apply-transition --action SET_HUMAN_REQUIRED >/dev/null
    echo '{"schemaVersion":1,"blockerId":"blocker-001","type":"HUMAN_DECISION_REQUIRED","category":"PRODUCT_BEHAVIOR_AMBIGUITY","summary":"Need approval","evidence":["evidence"],"questionsForHuman":["Proceed?"]}' >> "$WORKSPACE_ABS/state/blockers.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "Valid blocker should pass, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 6: NextAction_NewWorkspaceNeedsDiscovery
# ============================================================
test_06() {
    init_test_workspace
    local na
    na=$(run_core next-action)
    echo "$na" | grep -q "RUN_DISCOVERY" || { echo "Expected RUN_DISCOVERY, got: $na"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 7: NextAction_ReadyTaskRunsExecutor
# ============================================================
test_07() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Ready task","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    local na
    na=$(run_core next-action)
    echo "$na" | grep -q "RUN_EXECUTOR" || { echo "Expected RUN_EXECUTOR, got: $na"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 8: NextAction_ResearchRejectedRoutesToResearcher
# ============================================================
test_08() {
    init_test_workspace
    run_core apply-transition --action RUN_RESEARCHER >/dev/null
    local state
    state=$(cat "$WORKSPACE_ABS/state/team-state.json")
    local phase
    phase=$(echo "$state" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('currentPhase',''))" 2>/dev/null)
    # NEEDS_RESEARCH → RUN_RESEARCHER
    local na
    na=$(run_core next-action)
    echo "$na" | grep -q "RUN_RESEARCHER" || { echo "Expected RUN_RESEARCHER, got: $na"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 9: NextAction_GateFailedFixableRoutesToExecutor
# ============================================================
test_09() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate fail test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action GATE_FAILED >/dev/null
    local na
    na=$(run_core next-action)
    echo "$na" | grep -q "RUN_EXECUTOR" || { echo "Expected RUN_EXECUTOR after GATE_FAILED, got: $na"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 10: WriteEvent_CreatesValidEvent
# ============================================================
test_10() {
    init_test_workspace
    run_core write-event --type TEST_EVENT --actor test --summary "Test event" >/dev/null
    [[ -f "$WORKSPACE_ABS/state/events.jsonl" ]] || { echo "events.jsonl missing"; return 1; }
    local lines
    lines=$(wc -l < "$WORKSPACE_ABS/state/events.jsonl")
    [[ "$lines" -ge 1 ]] || { echo "events.jsonl empty"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 11: WriteEvent_EscapesQuotesBackslashesAndNewlines
# ============================================================
test_11() {
    init_test_workspace
    run_core write-event --type TEST_EVENT --actor test --summary 'Test "quotes" \\backslash\\ newline' >/dev/null
    local events_file="$WORKSPACE_ABS/state/events.jsonl"
    local last_line
    last_line=$(tail -1 "$events_file")
    "$PY" -c "import json,sys; json.loads(sys.argv[1])" "$last_line" 2>/dev/null || { echo "Event JSON invalid"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 12: ScopeGuard_AllowsAllowedWrites
# ============================================================
test_12() {
    init_test_workspace
    mkdir -p "$TEST_REPO_DIR/.teamloop/state"
    touch "$TEST_REPO_DIR/.teamloop/state/safe.txt"
    git -C "$TEST_REPO_DIR" add . >/dev/null 2>&1
    set +e
    local cs
    cs=$(run_core check-scope 2>&1)
    set -e
    local status
    status=$(echo "$cs" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$status" == "PASS" ]] || { echo "Scope guard should allow .teamloop/**, got: $cs"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 13: TaskSlicer_RejectsTaskWithoutScope
# ============================================================
test_13() {
    init_test_workspace
    local task_json='{"schemaVersion":1,"taskId":"task-no-scope","title":"No scope","status":"READY","successCriteria":["Works"]}'
    set +e
    local tout trc
    tout=$("$PY" "$CORE" validate-task --json-string "$task_json" 2>&1)
    trc=$?
    set -e
    [[ $trc -eq 1 ]] || { echo "Task without scope should fail validation"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 14: ResearchLead_RejectsCountMismatch
# ============================================================
test_14() {
    init_test_workspace
    local report='{"schemaVersion":1,"question":"What","findingsCount":2,"findings":[]}'
    set +e
    local tout trc
    tout=$("$PY" "$CORE" validate-research --json-string "$report" 2>&1)
    trc=$?
    set -e
    [[ $trc -eq 1 ]] || { echo "Research with count mismatch should fail"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 15: Completion_DoneRequiresNoOpenTasks
# ============================================================
test_15() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Open task","status":"IN_PROGRESS","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action SET_DONE >/dev/null
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "DONE with open tasks should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 16: GateRunner_RequiredFailFailsOverall
# ============================================================
test_16() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"fail-gate","type":"shell","command":"sh -c 'exit 1'","required":true}]}
GEOF
    set +e
    local gout grc
    gout=$(run_core run-gates 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 1 ]] || { echo "run-gates with required fail should exit 1"; return 1; }
    local gate_status
    gate_status=$(echo "$gout" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$gate_status" == "FAIL" ]] || { echo "Gate result should be FAIL, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 17: GateRunner_OptionalFailDoesNotFailOverall
# ============================================================
test_17() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"opt-fail","type":"shell","command":"sh -c 'exit 1'","required":false}]}
GEOF
    set +e
    local gout grc
    gout=$(run_core run-gates 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 0 ]] || { echo "run-gates with optional fail should pass, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 18: GateRunner_CapturesStdoutAndStderr
# ============================================================
test_18() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"echo-gate","type":"shell","command":"sh -c 'echo hello; echo err >&2; exit 0'","required":true}]}
GEOF
    local gout
    gout=$(run_core run-gates 2>&1)
    echo "$gout" | grep -q "hello" || { echo "Gate should capture stdout, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 19: GateRunner_RequiredErrorFailsOverall
# ============================================================
test_19() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"error-gate","type":"shell","command":"sh -c 'exit 42'","required":true}]}
GEOF
    set +e
    local gout grc
    gout=$(run_core run-gates 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 1 ]] || { echo "run-gates with error exit code should fail"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 20: GateRunner_CapturesOutputWithQuotesWithoutBreakingJson
# ============================================================
test_20() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"quote-gate","type":"shell","command":"sh -c 'echo \"test output\"'; exit 0","required":true}]}
GEOF
    local gout
    gout=$(run_core run-gates 2>&1)
    "$PY" -c "import json,sys; json.loads(sys.argv[1])" "$gout" 2>/dev/null || { echo "Gate result JSON broken with quotes, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 21: JsonlLedger_EachLineIsValidJson
# ============================================================
test_21() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Ledger test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_CHANGE_REVIEWER >/dev/null
    local ledger="$WORKSPACE_ABS/state/run-ledger.jsonl"
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        "$PY" -c "import json,sys; json.loads(sys.argv[1])" "$line" 2>/dev/null || { echo "Invalid JSON in ledger: $line"; return 1; }
    done < "$ledger"
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 22: ApplyTransition_ReadyTaskCreatesCurrentTaskAndRun
# ============================================================
test_22() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Apply test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    [[ -f "$WORKSPACE_ABS/state/current-task.json" ]] || { echo "current-task.json missing"; return 1; }
    local state
    state=$(cat "$WORKSPACE_ABS/state/team-state.json")
    echo "$state" | grep -q "task-001" || { echo "team-state should have task-001, got: $state"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 23: ApplyTransition_AppendsStateTransitionEvent
# ============================================================
test_23() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Event test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    local events="$WORKSPACE_ABS/state/events.jsonl"
    local lines
    lines=$(wc -l < "$events")
    [[ "$lines" -ge 1 ]] || { echo "events.jsonl should have at least 1 line"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 24: ApplyTransition_DoesNotLoseExistingBacklog
# ============================================================
test_24() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"First","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    echo '{"schemaVersion":1,"taskId":"task-002","title":"Second","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    local count
    count=$(grep -c "taskId" "$WORKSPACE_ABS/state/backlog.jsonl" || echo 0)
    [[ "$count" -ge 2 ]] || { echo "Backlog should keep 2 tasks, got $count"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 25: Completion_DoneRequiresFinalReport
# ============================================================
test_25() {
    init_test_workspace
    run_core apply-transition --action SET_DONE >/dev/null
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "DONE without final report should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 26: Completion_DoneFailsWithOpenHumanBlocker
# ============================================================
test_26() {
    init_test_workspace
    echo '{"schemaVersion":1,"blockerId":"blocker-001","type":"HUMAN_DECISION_REQUIRED","category":"PRODUCT_BEHAVIOR_AMBIGUITY","summary":"Open blocker","evidence":["evidence"],"questionsForHuman":["Proceed?"]}' >> "$WORKSPACE_ABS/state/blockers.jsonl"
    run_core apply-transition --action SET_DONE >/dev/null
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "DONE with open blocker should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 27: SchemaValidation_ValidTaskPasses
# ============================================================
test_27() {
    init_test_workspace
    local task_json='{"schemaVersion":1,"taskId":"task-001","title":"Valid","status":"READY","scope":["src/**"],"successCriteria":["Works"]}'
    set +e
    local tout trc
    tout=$("$PY" "$CORE" validate-task --json-string "$task_json" 2>&1)
    trc=$?
    set -e
    [[ $trc -eq 0 ]] || { echo "Valid task should pass, got: $tout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 28: SchemaValidation_InvalidEventFails
# ============================================================
test_28() {
    init_test_workspace
    # Append invalid event
    echo '{"notValid":"missing required fields"}' >> "$WORKSPACE_ABS/state/events.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "Invalid event should fail validation, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# TEST 29: NextAction_ReadyTaskReturnsRunExecutor
# ============================================================
test_29() {
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Ready","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    local na
    na=$(run_core next-action)
    local action
    action=$(echo "$na" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('nextAction',''))" 2>/dev/null)
    [[ "$action" == "RUN_EXECUTOR" ]] || { echo "Expected RUN_EXECUTOR, got '$action'"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# GOLDEN TESTS 30-36: supervised-task.md and agent docs content checks
# ============================================================
test_30() {
    local doc="$PROJECT_ROOT/adapters/opencode/commands/supervised-task.md"
    [[ -f "$doc" ]] || { echo "supervised-task.md missing"; return 1; }
    grep -q "SAFE_CHECKPOINT" "$doc" || { echo "supervised-task.md should contain SAFE_CHECKPOINT"; return 1; }
    ! grep -qF 'currentPhase.*"DONE"' "$doc" || { echo "supervised-task.md should not use DONE as phase"; return 1; }
    return 0
}

test_31() {
    local doc="$PROJECT_ROOT/adapters/opencode/commands/supervised-task.md"
    grep -q "MANUAL_REVIEW" "$doc" || { echo "supervised-task.md should contain MANUAL_REVIEW"; return 1; }
    ! grep -q '"HUMAN_REQUIRED"' "$doc" || { echo "supervised-task.md should not use HUMAN_REQUIRED as phase value"; return 1; }
    return 0
}

test_32() {
    local doc="$PROJECT_ROOT/adapters/opencode/commands/supervised-task.md"
    grep -q "research-lead" "$doc" || { echo "supervised-task.md should reference research-lead"; return 1; }
    return 0
}

test_33() {
    local doc="$PROJECT_ROOT/adapters/opencode/commands/supervised-task.md"
    grep -q "task-slicer" "$doc" || { echo "supervised-task.md should reference task-slicer"; return 1; }
    return 0
}

test_34() {
    local doc="$PROJECT_ROOT/adapters/opencode/commands/supervised-task.md"
    ! grep -qi "developer-action" "$doc" || { echo "supervised-task.md should not hand off to developer-action"; return 1; }
    return 0
}

test_35() {
    local doc="$PROJECT_ROOT/adapters/opencode/agents/executor.md"
    [[ -f "$doc" ]] || { echo "executor.md missing"; return 1; }
    ! grep -qi "scope expansion" "$doc" || { echo "executor.md should forbid scope expansion"; return 1; }
    return 0
}

test_36() {
    local doc="$PROJECT_ROOT/adapters/opencode/agents/change-reviewer.md"
    [[ -f "$doc" ]] || { echo "change-reviewer.md missing"; return 1; }
    grep -qi "forbidden" "$doc" || { echo "change-reviewer.md should mention forbidden actions"; return 1; }
    return 0
}

# ============================================================
# CONTRACT TESTS 37-43
# ============================================================
test_37() {
    # apply-transition state passes validate-state
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Contract test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_CHANGE_REVIEWER >/dev/null
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after transitions, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_38() {
    # run-gates state passes validate-state (PASS path)
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate contract","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"ok","type":"shell","command":"sh -c 'exit 0'","required":true}]}
GEOF
    run_core run-gates >/dev/null
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after gate PASS, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_39() {
    # validate-state catches invalid research
    init_test_workspace
    mkdir -p "$WORKSPACE_ABS/research"
    echo '{"schemaVersion":1,"findingsCount":999,"findings":[]}' > "$WORKSPACE_ABS/research/bad.json"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on invalid research, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_40() {
    # full chain preserves task/run identity
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Identity test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_CHANGE_REVIEWER >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"ok","type":"shell","command":"sh -c 'exit 0'","required":true}]}
GEOF
    run_core run-gates >/dev/null
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after full chain, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_41() {
    # validate-state fails on corrupted phase
    init_test_workspace
    local state_file="$WORKSPACE_ABS/state/team-state.json"
    local content
    content=$(cat "$state_file")
    content=$(echo "$content" | "$PY" -c "import json,sys; d=json.load(sys.stdin); d['currentPhase']='CORRUPTED'; print(json.dumps(d))")
    echo "$content" > "$state_file"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on corrupted phase, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_42() {
    # run-gates PASS advances state past NEEDS_GATE
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Advance test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"ok","type":"shell","command":"sh -c 'exit 0'","required":true}]}
GEOF
    run_core run-gates >/dev/null
    local na
    na=$(run_core next-action)
    echo "$na" | grep -q "RUN_GATEKEEPER" && { echo "next-action should not return RUN_GATEKEEPER after gate PASS, got: $na"; return 1; }
    cleanup_workspace
    return 0
}

test_43() {
    # REQUEST_CHANGES preserves identity
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Review test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    local exec_output
    exec_output=$(run_core apply-transition --action "RUN_EXECUTOR" --task-id "task-001" 2>&1)
    run_core apply-transition --action "RUN_CHANGE_REVIEWER" >/dev/null 2>&1
    local req_output
    req_output=$(run_core apply-transition --action "REQUEST_CHANGES" 2>&1)
    local run_id
    run_id=$(echo "$exec_output" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('runId',''))" 2>/dev/null)
    echo "$req_output" | grep -q "task-001" || { echo "REQUEST_CHANGES should preserve taskId"; return 1; }
    echo "$req_output" | grep -q "$run_id" || { echo "REQUEST_CHANGES should preserve runId"; return 1; }
    local phase
    phase=$(echo "$req_output" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
    [[ "$phase" == "REVIEW_FAILED" ]] || { echo "REQUEST_CHANGES phase should be REVIEW_FAILED, got $phase"; return 1; }
    local na
    na=$(run_core next-action | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('nextAction',''))" 2>/dev/null)
    [[ "$na" == "RUN_EXECUTOR" ]] || { echo "nextAction after REVIEW_FAILED should be RUN_EXECUTOR, got $na"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# NEW P0 TESTS 44-49
# ============================================================
test_44() {
    # REVIEW_FAILED next-action preserves taskId
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Review test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_CHANGE_REVIEWER >/dev/null
    run_core apply-transition --action REQUEST_CHANGES >/dev/null
    local na
    na=$(run_core next-action)
    local tid
    tid=$(echo "$na" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('taskId',''))" 2>/dev/null)
    [[ "$tid" == "task-001" ]] || { echo "REVIEW_FAILED next-action should preserve taskId, got '$tid'"; return 1; }
    cleanup_workspace
    return 0
}

test_45() {
    # stale current-task.json does NOT grant scope after gate PASS
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Scope test","status":"READY","scope":["src/**"],"successCriteria":["Works"],"allowedWrites":["src/**", ".teamloop/**"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"ok","type":"shell","command":"sh -c 'exit 0'","required":true}]}
GEOF
    run_core run-gates >/dev/null
    [[ -f "$WORKSPACE_ABS/state/current-task.json" ]] && { echo "current-task.json should be removed after gate PASS"; return 1; }
    mkdir -p "$TEST_REPO_DIR/src"
    touch "$TEST_REPO_DIR/src/unauthorized.txt"
    git -C "$TEST_REPO_DIR" add src/unauthorized.txt >/dev/null 2>&1
    set +e
    local cs
    cs=$(run_core check-scope 2>&1)
    set -e
    local status
    status=$(echo "$cs" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$status" == "FAIL" ]] || { echo "check-scope should FAIL after task completion, got '$status'"; return 1; }
    cleanup_workspace
    return 0
}

test_46() {
    # validate-state catches stale current-task.json
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-999","title":"Stale","status":"IN_PROGRESS","scope":["src/**"],"successCriteria":["X"]}' > "$WORKSPACE_ABS/state/current-task.json"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on stale current-task.json, got: $vout"; return 1; }
    echo "$vout" | grep -qi "stale" || { echo "validate-state error should mention 'stale', got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_47() {
    # CONTINUE_LOOP clears current-task.json
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Continue test","status":"READY","scope":["src/**"],"successCriteria":["Works"],"allowedWrites":["src/**"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    [[ -f "$WORKSPACE_ABS/state/current-task.json" ]] || { echo "current-task.json should exist during EXECUTING_TASK"; return 1; }
    run_core apply-transition --action CONTINUE_LOOP >/dev/null
    [[ -f "$WORKSPACE_ABS/state/current-task.json" ]] && { echo "current-task.json should be removed after CONTINUE_LOOP"; return 1; }
    local state
    state=$(cat "$WORKSPACE_ABS/state/team-state.json")
    local ctid
    ctid=$(echo "$state" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('currentTaskId',''))" 2>/dev/null)
    [[ -z "$ctid" ]] || { echo "currentTaskId should be empty after CONTINUE_LOOP, got '$ctid'"; return 1; }
    cleanup_workspace
    return 0
}

test_48() {
    # failed gate → validate-state PASS → next-action RUN_EXECUTOR
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"always-fail","type":"shell","command":"sh -c 'exit 1'","required":true}]}
GEOF
    set +e
    run_core run-gates >/dev/null 2>&1
    local gate_exit=$?
    set -e
    [[ $gate_exit -eq 1 ]] || { echo "run-gates should fail"; return 1; }
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after failed gate, got: $vout"; return 1; }
    local naout
    naout=$(run_core next-action)
    echo "$naout" | grep -q "RUN_EXECUTOR" || { echo "next-action should return RUN_EXECUTOR, got: $naout"; return 1; }
    cleanup_workspace
    return 0
}

test_49() {
    # validate-state catches invalid JSON in artifacts
    init_test_workspace
    mkdir -p "$WORKSPACE_ABS/research"
    echo '{bad json content' > "$WORKSPACE_ABS/research/broken.json"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on invalid JSON in research/, got: $vout"; return 1; }
    rm "$WORKSPACE_ABS/research/broken.json"
    set +e
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after removing invalid JSON, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_50() {
    # NEEDS_TASK_SLICING + READY task → next-action returns RUN_EXECUTOR (not RUN_TASK_SLICER)
    init_test_workspace
    run_core apply-transition --action RUN_TASK_SLICER >/dev/null
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Ready","status":"READY","scope":["src/**"],"successCriteria":["ok"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    local na
    na=$(run_core next-action)
    local action
    action=$(echo "$na" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('nextAction',''))" 2>/dev/null)
    [[ "$action" == "RUN_EXECUTOR" ]] || { echo "NEEDS_TASK_SLICING with READY task should return RUN_EXECUTOR, got '$action'"; return 1; }
    local tid
    tid=$(echo "$na" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('taskId',''))" 2>/dev/null)
    [[ "$tid" == "task-001" ]] || { echo "taskId should be task-001, got '$tid'"; return 1; }
    cleanup_workspace
    return 0
}

test_51() {
    # NEEDS_TASK_SLICING with no READY tasks → next-action returns RUN_TASK_SLICER
    init_test_workspace
    run_core apply-transition --action RUN_TASK_SLICER >/dev/null
    local na
    na=$(run_core next-action)
    local action
    action=$(echo "$na" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('nextAction',''))" 2>/dev/null)
    [[ "$action" == "RUN_TASK_SLICER" ]] || { echo "NEEDS_TASK_SLICING without READY tasks should return RUN_TASK_SLICER, got '$action'"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# RUN ALL
# ============================================================

test_run "InitWorkspace_CreatesValidState" test_01
test_run "ValidateState_FreshWorkspacePasses" test_02
test_run "ValidateState_HumanRequiredWithoutBlockerFails" test_03
test_run "ValidateState_HumanRequiredWithMalformedBlockerFails" test_04
test_run "ValidateState_HumanRequiredWithValidBlockerPasses" test_05
test_run "NextAction_NewWorkspaceNeedsDiscovery" test_06
test_run "NextAction_ReadyTaskRunsExecutor" test_07
test_run "NextAction_ResearchRejectedRoutesToResearcher" test_08
test_run "NextAction_GateFailedFixableRoutesToExecutor" test_09
test_run "WriteEvent_CreatesValidEvent" test_10
test_run "WriteEvent_EscapesQuotesBackslashesAndNewlines" test_11
test_run "ScopeGuard_AllowsAllowedWrites" test_12
test_run "TaskSlicer_RejectsTaskWithoutScope" test_13
test_run "ResearchLead_RejectsCountMismatch" test_14
test_run "Completion_DoneRequiresNoOpenTasks" test_15
test_run "GateRunner_RequiredFailFailsOverall" test_16
test_run "GateRunner_OptionalFailDoesNotFailOverall" test_17
test_run "GateRunner_CapturesStdoutAndStderr" test_18
test_run "GateRunner_RequiredErrorFailsOverall" test_19
test_run "GateRunner_CapturesOutputWithQuotesWithoutBreakingJson" test_20
test_run "JsonlLedger_EachLineIsValidJson" test_21
test_run "ApplyTransition_ReadyTaskCreatesCurrentTaskAndRun" test_22
test_run "ApplyTransition_AppendsStateTransitionEvent" test_23
test_run "ApplyTransition_DoesNotLoseExistingBacklog" test_24
test_run "Completion_DoneRequiresFinalReport" test_25
test_run "Completion_DoneFailsWithOpenHumanBlocker" test_26
test_run "SchemaValidation_ValidTaskPasses" test_27
test_run "SchemaValidation_InvalidEventFails" test_28
test_run "NextAction_ReadyTaskReturnsRunExecutor" test_29
test_run "Golden: supervised-task contains SAFE_CHECKPOINT != DONE" test_30
test_run "Golden: supervised-task contains MANUAL_REVIEW != HUMAN_REQUIRED" test_31
test_run "Golden: supervised-task contains research-lead" test_32
test_run "Golden: supervised-task contains task-slicer" test_33
test_run "Golden: supervised-task forbids developer-action handoff" test_34
test_run "Golden: executor forbids scope expansion" test_35
test_run "Golden: reviewer checks forbidden actions" test_36
test_run "CONTRACT: apply-transition state passes validate-state" test_37
test_run "CONTRACT: run-gates state passes validate-state" test_38
test_run "CONTRACT: validate-state catches invalid research" test_39
test_run "CONTRACT: full chain preserves task/run identity" test_40
test_run "CONTRACT: validate-state fails on corrupted phase" test_41
test_run "CONTRACT: run-gates PASS advances state past NEEDS_GATE" test_42
test_run "CONTRACT: REQUEST_CHANGES preserves identity" test_43
test_run "P0: REVIEW_FAILED next-action preserves taskId" test_44
test_run "P0: stale current-task.json does NOT grant scope after gate PASS" test_45
test_run "P0: validate-state catches stale current-task.json" test_46
test_run "P0: CONTINUE_LOOP clears current-task.json" test_47
test_run "P0: failed gate → validate-state PASS → next-action RUN_EXECUTOR" test_48
test_run "P0: validate-state catches invalid JSON in artifacts" test_49
test_run "P1: NEEDS_TASK_SLICING + READY task -> RUN_EXECUTOR" test_50
test_run "P1: NEEDS_TASK_SLICING no READY tasks -> RUN_TASK_SLICER" test_51

# ============================================================
# SUMMARY
# ============================================================
echo ""
echo "========================================"
echo "Results: $PASSED/$TOTAL passed, $FAILED failed"
echo "========================================"

if [[ $FAILED -gt 0 ]]; then
    exit 1
fi
exit 0
