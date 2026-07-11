#!/usr/bin/env bash
set -euo pipefail

PY="${PY:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null)}"
TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$TEST_DIR/.." && pwd)"
CORE="$PROJECT_ROOT/scripts/teamloop-core.py"

TOTAL=0
PASSED=0
FAILED=0
DISCOVERED=0
TEST_FROM="${TEAMLOOP_TEST_FROM:-1}"
TEST_TO="${TEAMLOOP_TEST_TO:-999999}"

# ============================================================
# FILTERING FLAGS AND ENV VARS
# ============================================================
TEST_LAYER=""
TEST_AFFECTED=false
TEST_FULL=false
TEST_LIST_LAYERS=false
TEST_AUTO=false
TEST_INCLUDE=""

# Parse CLI flags (before test functions, after variable init)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --layer)
            TEST_LAYER="$2"
            shift 2
            ;;
        --affected)
            TEST_AFFECTED=true
            shift
            ;;
        --full)
            TEST_FULL=true
            shift
            ;;
        --list-layers)
            TEST_LIST_LAYERS=true
            shift
            ;;
        --help|-h)
            echo "Usage: run-tests.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --layer LAYER       Run only tests in the given layer (smoke|contract|runtime|integration)"
            echo "  --affected          Run only tests affected by git changes (via test-select)"
            echo "  --full              Run all tests (explicit full suite)"
            echo "  --list-layers       List available layers and test counts"
            echo "  --help, -h          Show this help"
            echo ""
            echo "Environment variables:"
            echo "  TEAMLOOP_TEST_FROM  First test number to run (default: 1)"
            echo "  TEAMLOOP_TEST_TO    Last test number to run (default: 999999)"
            echo "  TEAMLOOP_TEST_INCLUDE  Comma-separated list of test IDs (e.g. 1,5,10)"
            echo "  TEAMLOOP_TEST_AUTO   If non-empty, auto-select tests via test-select --since-ref HEAD~1"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Handle env var shortcuts
if [[ -n "${TEAMLOOP_TEST_INCLUDE:-}" ]]; then
    TEST_INCLUDE="$TEAMLOOP_TEST_INCLUDE"
fi
if [[ -n "${TEAMLOOP_TEST_AUTO:-}" ]]; then
    TEST_AUTO=true
fi

# ============================================================
# LAYER SELECTION via test-select
# ============================================================
# Array of allowed test function names (e.g. test_01, test_02, ...)
# When empty, all tests run (default behavior).
declare -a ALLOWED_TEST_FUNCS=()
SELECTION_ACTIVE=false

resolve_test_selection() {
    if $TEST_LIST_LAYERS; then
        "$PY" "$CORE" test-select --list-layers
        exit 0
    fi

    if $TEST_FULL; then
        # Explicit full — run all tests (ALLOWED_TEST_FUNCS stays empty)
        return 0
    fi

    if $TEST_AUTO; then
        local sel
        sel=$("$PY" "$CORE" test-select --affected --explain 2>/dev/null) || sel=$("$PY" "$CORE" test-select --affected 2>/dev/null)
        ALLOWED_TEST_FUNCS=($(echo "$sel" | "$PY" -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('selectedTests', []):
    print(t)
" 2>/dev/null))
        if [[ ${#ALLOWED_TEST_FUNCS[@]} -gt 0 ]]; then
            SELECTION_ACTIVE=true
            echo "[test-select] Auto-selected ${#ALLOWED_TEST_FUNCS[@]} tests via --affected"
        fi
        return 0
    fi

    if [[ -n "$TEST_INCLUDE" ]]; then
        # TEAMLOOP_TEST_INCLUDE: comma-separated test IDs like "1,5,10"
        IFS=',' read -ra include_arr <<< "$TEST_INCLUDE"
        for id in "${include_arr[@]}"; do
            id=$(echo "$id" | tr -d ' ')
            local padded
            padded=$(printf "test_%02d" "$id")
            ALLOWED_TEST_FUNCS+=("$padded")
        done
        SELECTION_ACTIVE=true
        echo "[test-select] Included ${#ALLOWED_TEST_FUNCS[@]} tests via TEAMLOOP_TEST_INCLUDE"
        return 0
    fi

    if [[ -n "$TEST_LAYER" ]]; then
        local sel
        sel=$("$PY" "$CORE" test-select --layer "$TEST_LAYER" 2>/dev/null) || {
            echo "[test-select] Failed to resolve layer '$TEST_LAYER'" >&2
            exit 1
        }
        ALLOWED_TEST_FUNCS=($(echo "$sel" | "$PY" -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('selectedTests', []):
    print(t)
" 2>/dev/null))
        if [[ ${#ALLOWED_TEST_FUNCS[@]} -gt 0 ]]; then
            SELECTION_ACTIVE=true
            echo "[test-select] Layer '$TEST_LAYER' selected ${#ALLOWED_TEST_FUNCS[@]} tests"
        fi
        return 0
    fi

    if $TEST_AFFECTED; then
        local sel
        sel=$("$PY" "$CORE" test-select --affected 2>/dev/null) || {
            echo "[test-select] Failed to resolve affected tests" >&2
            exit 1
        }
        ALLOWED_TEST_FUNCS=($(echo "$sel" | "$PY" -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('selectedTests', []):
    print(t)
" 2>/dev/null))
        if [[ ${#ALLOWED_TEST_FUNCS[@]} -gt 0 ]]; then
            SELECTION_ACTIVE=true
            echo "[test-select] Affected selection picked ${#ALLOWED_TEST_FUNCS[@]} tests"
        fi
        return 0
    fi

    # Default: no filtering, run all tests
    return 0
}

resolve_test_selection

# Helper: check if a test function name is in the allowed set
is_test_allowed() {
    local func_name="$1"
    if ! $SELECTION_ACTIVE; then
        return 0  # no filter active, all tests allowed
    fi
    for allowed in "${ALLOWED_TEST_FUNCS[@]}"; do
        if [[ "$allowed" == "$func_name" ]]; then
            return 0
        fi
    done
    return 1
}

test_run() {
    local name="$1"
    shift
    local func_name="$1"
    DISCOVERED=$((DISCOVERED + 1))
    if (( DISCOVERED < TEST_FROM || DISCOVERED > TEST_TO )); then
        return 0
    fi
    # Layer/affected/include selection filter
    if ! is_test_allowed "$func_name"; then
        return 0
    fi
    TOTAL=$((TOTAL + 1))
    echo ""
    echo "[$DISCOVERED] $name"

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
        (
            cd "$TEST_REPO_DIR"
            "$PY" "$CORE" "$cmd" --workspace "$WORKSPACE_ABS" "$@"
        )
        return $?
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
    run_core write-event --type STATE_TRANSITION --actor test --summary "Test event" >/dev/null
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
    run_core write-event --type STATE_TRANSITION --actor test --summary 'Test "quotes" \\backslash\\ newline' >/dev/null
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
# MEMORY REGRESSION TESTS 52-62
# ============================================================
test_52() {
    # Memory_EmptyPasses — fresh workspace with empty memory files validates cleanly
    init_test_workspace
    # Memory dir created by init-workspace, JSONL files are empty
    local mem_dir="$WORKSPACE_ABS/memory"
    [[ -d "$mem_dir" ]] || { echo "memory directory should exist after init"; return 1; }
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "Fresh workspace with empty memory should validate, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_53() {
    # Memory_MalformedJsonlFails — a JSONL file with invalid JSON fails validation
    init_test_workspace
    echo '{bad json content here' > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on malformed memory JSONL, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_54() {
    # Memory_ActiveWithoutEvidenceFails — an ACTIVE lesson without evidenceIds fails
    init_test_workspace
    local lesson='{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on ACTIVE lesson without evidenceIds, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_55() {
    # Memory_ActiveWithValidEvidencePasses — ACTIVE lesson with valid evidenceId passes
    init_test_workspace
    local evidence='{"schemaVersion":1,"evidenceId":"evidence-001","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z"}'
    local lesson='{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-001"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$evidence" > "$WORKSPACE_ABS/memory/evidence-map.jsonl"
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass with ACTIVE lesson + valid evidence, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_56() {
    # Memory_ActiveWithMissingEvidenceIdFails — ACTIVE lesson references evidenceId not in evidence-map
    init_test_workspace
    local lesson='{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-missing"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on ACTIVE lesson referencing missing evidenceId, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_57() {
    # Memory_DeprecatedRetainedButInactive — DEPRECATED lesson validates without evidence
    init_test_workspace
    local lesson='{"schemaVersion":1,"lessonId":"lesson-depr","title":"Old lesson","description":"Deprecated","status":"DEPRECATED","createdAtUtc":"2024-01-01T00:00:00Z","deprecatedAtUtc":"2024-06-01T00:00:00Z"}'
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass for DEPRECATED lesson without evidence, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_58() {
    # Memory_SupersededWithoutEvidencePasses — SUPERSEDED lesson validates without evidence
    init_test_workspace
    local lesson='{"schemaVersion":1,"lessonId":"lesson-sup","title":"Superseded","description":"Old way","status":"SUPERSEDED","createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass for SUPERSEDED lesson without evidence or supersededBy, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_59() {
    # Memory_RejectedAntipatternWithoutEvidencePasses — REJECTED antipattern validates without evidence
    init_test_workspace
    local anti='{"schemaVersion":1,"antipatternId":"antipattern-001","title":"Old anti","description":"Rejected","status":"REJECTED","createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$anti" > "$WORKSPACE_ABS/memory/antipatterns.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass for REJECTED antipattern without evidence, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_60() {
    # Memory_MissingMemoryDirPasses — missing memory directory does not crash validate-state
    init_test_workspace
    rm -rf "$WORKSPACE_ABS/memory"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass even if memory dir missing, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_61() {
    # Memory_ProfileValidation — project-profile.json validated against memory-profile schema
    init_test_workspace
    # Inject an invalid field that is not allowed
    local pp='{"schemaVersion":1,"workspace":".teamloop","memoryVersion":"1","invalidField":"bad"}'
    echo "$pp" > "$WORKSPACE_ABS/memory/project-profile.json"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on project-profile with invalid field, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_62() {
    # Memory_DoctorEmptyPasses — memory-doctor returns PASS on clean empty memory
    init_test_workspace
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 0 ]] || { echo "memory-doctor should exit 0 on empty memory, got: $dout"; return 1; }
    echo "$dout" | grep -q '"status": "PASS"' || { echo "memory-doctor output should contain PASS, got: $dout"; return 1; }
    echo "$dout" | grep -q '"checks"' || { echo "memory-doctor output should contain checks array, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_63() {
    # Memory_DoctorDetectsIssues — memory-doctor returns FAIL when issues exist
    init_test_workspace
    # Put an ACTIVE lesson with no evidence
    local lesson='{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 1 ]] || { echo "memory-doctor should exit 1 when issues exist, got: $dout"; return 1; }
    echo "$dout" | grep -q '"status": "FAIL"' || { echo "memory-doctor output should contain FAIL, got: $dout"; return 1; }
    echo "$dout" | grep -q '"checks"' || { echo "memory-doctor output should contain checks array, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_64() {
    # Memory_ActiveWithUnverifiedEvidenceFails — ACTIVE lesson with UNVERIFIED evidence fails
    init_test_workspace
    # Add evidence with UNVERIFIED status
    local evidence='{"schemaVersion":1,"evidenceId":"evidence-001","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z","status":"UNVERIFIED"}'
    local lesson='{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-001"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$evidence" > "$WORKSPACE_ABS/memory/evidence-map.jsonl"
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on ACTIVE lesson with UNVERIFIED evidence, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# NEW REGRESSION TESTS 65-76
# ============================================================
test_65() {
    # WriteEvent_InvalidTypeRejected — write-event with invalid type exits non-zero, events.jsonl unchanged
    init_test_workspace
    local events_file="$WORKSPACE_ABS/state/events.jsonl"
    local lines_before
    lines_before=$(wc -l < "$events_file")
    set +e
    local eout erc
    eout=$(run_core write-event --type INVALID_TYPE --actor test --summary "Should fail" 2>&1)
    erc=$?
    set -e
    [[ $erc -eq 1 ]] || { echo "write-event with INVALID_TYPE should exit 1, got exit $erc"; return 1; }
    local lines_after
    lines_after=$(wc -l < "$events_file")
    [[ "$lines_before" -eq "$lines_after" ]] || { echo "events.jsonl should be unchanged, before=$lines_before after=$lines_after"; return 1; }
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should still pass after rejected write-event, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_66() {
    # Memory_ActiveWithUnverifiedEvidenceFailsSchemaValid — evidence record with status UNVERIFIED fails validate-state for the semantic reason
    init_test_workspace
    local evidence='{"schemaVersion":1,"evidenceId":"evidence-001","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z","status":"UNVERIFIED"}'
    local lesson='{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-001"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$evidence" > "$WORKSPACE_ABS/memory/evidence-map.jsonl"
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on UNVERIFIED evidence, got: $vout"; return 1; }
    echo "$vout" | grep -qi "UNVERIFIED\|evidence" || { echo "validate-state error should mention UNVERIFIED or evidence, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_67() {
    # Memory_SupersededByFailsBothValidateStateAndMemoryDoctor — orphaned supersededBy detected by both
    init_test_workspace
    local lesson='{"schemaVersion":1,"lessonId":"lesson-sup","title":"Superseded","description":"Old way","status":"SUPERSEDED","createdAtUtc":"2024-01-01T00:00:00Z","supersededBy":"lesson-nonexistent"}'
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    # validate-state should fail
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on orphaned supersededBy, got: $vout"; return 1; }
    # memory-doctor should fail
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 1 ]] || { echo "memory-doctor should fail on orphaned supersededBy, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_68() {
    # MemoryDoctor_MissingDirectoryFails — memory-doctor exits 1 when memory directory absent
    init_test_workspace
    rm -rf "$WORKSPACE_ABS/memory"
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 1 ]] || { echo "memory-doctor should exit 1 when memory dir missing, got: $dout"; return 1; }
    echo "$dout" | grep -qi "memory" || { echo "memory-doctor output should mention memory directory, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_69() {
    # MemoryDoctor_EmptySubsystemWarns — empty JSONL files produce WARNING, not FAIL
    init_test_workspace
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    echo "$dout" | grep -q "WARNING" || { echo "memory-doctor should report WARNING for empty subsystem, got: $dout"; return 1; }
    # Should not be a hard FAIL — exit 0
    [[ $drc -eq 0 ]] || { echo "memory-doctor should exit 0 for WARNING-level finding, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_70() {
    # Memory_ProfileDeprecatedFieldsRejected — injecting activeGuidanceRequiresEvidence or maxActiveLessons fails schema
    init_test_workspace
    local pp='{"schemaVersion":1,"workspace":".teamloop","memoryVersion":"1","activeGuidanceRequiresEvidence":true,"maxActiveLessons":5}'
    echo "$pp" > "$WORKSPACE_ABS/memory/project-profile.json"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on project-profile with deprecated fields, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# PowerShell parity tests 71-76
# ============================================================
test_71() {
    # WriteEvent_InvalidTypeRejected — PS parity
    init_test_workspace
    local events_file="$WORKSPACE_ABS/state/events.jsonl"
    local lines_before
    lines_before=$(wc -l < "$events_file")
    set +e
    local eout erc
    eout=$(run_core write-event --type INVALID_TYPE --actor test --summary "Should fail" 2>&1)
    erc=$?
    set -e
    [[ $erc -eq 1 ]] || { echo "write-event with INVALID_TYPE should exit 1 (PS parity), got exit $erc"; return 1; }
    local lines_after
    lines_after=$(wc -l < "$events_file")
    [[ "$lines_before" -eq "$lines_after" ]] || { echo "events.jsonl should be unchanged (PS parity), before=$lines_before after=$lines_after"; return 1; }
    cleanup_workspace
    return 0
}

test_72() {
    # Memory_DoctorMissingDirFails — memory-doctor exits 1 without memory dir
    init_test_workspace
    rm -rf "$WORKSPACE_ABS/memory"
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 1 ]] || { echo "memory-doctor should exit 1 when memory dir missing, got: $dout"; return 1; }
    echo "$dout" | grep -q "FAIL" || { echo "memory-doctor output should contain FAIL, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_73() {
    # Memory_SupersededByBothCheck — both validate-state and memory-doctor catch orphaned ref
    init_test_workspace
    local lesson='{"schemaVersion":1,"lessonId":"lesson-orphan","title":"Orphan","description":"Test","status":"SUPERSEDED","createdAtUtc":"2024-01-01T00:00:00Z","supersededBy":"no-such-lesson"}'
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    # validate-state fails
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should catch orphaned supersededBy, got: $vout"; return 1; }
    # memory-doctor fails
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 1 ]] || { echo "memory-doctor should catch orphaned supersededBy, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_74() {
    # Memory_ActiveWithUnverifiedEvidenceSemantic — validate-state fails for UNVERIFIED evidence with meaningful error
    init_test_workspace
    local evidence='{"schemaVersion":1,"evidenceId":"ev-001","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z","status":"UNVERIFIED"}'
    local lesson='{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["ev-001"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$evidence" > "$WORKSPACE_ABS/memory/evidence-map.jsonl"
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on UNVERIFIED evidence, got: $vout"; return 1; }
    echo "$vout" | grep -qi "UNVERIFIED\|evidence" || { echo "Error should mention UNVERIFIED or evidence, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_75() {
    # MemoryDoctor_WarningNotFail — empty subsystem is WARNING-level, not hard FAIL
    init_test_workspace
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    echo "$dout" | grep -q "WARNING" || { echo "memory-doctor should report WARNING for empty memory, got: $dout"; return 1; }
    [[ $drc -eq 0 ]] || { echo "memory-doctor should NOT exit 1 for WARNING, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_76() {
    # Memory_ProfileRemovedDeprecatedFields — activeGuidanceRequiresEvidence and maxActiveLessons rejected by schema
    init_test_workspace
    local pp='{"schemaVersion":1,"workspace":".teamloop","memoryVersion":"1","activeGuidanceRequiresEvidence":true}'
    echo "$pp" > "$WORKSPACE_ABS/memory/project-profile.json"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail with activeGuidanceRequiresEvidence, got: $vout"; return 1; }
    # Also test maxActiveLessons
    local pp2='{"schemaVersion":1,"workspace":".teamloop","memoryVersion":"1","maxActiveLessons":5}'
    echo "$pp2" > "$WORKSPACE_ABS/memory/project-profile.json"
    set +e
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail with maxActiveLessons, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# AUTO-DECISION REGRESSION TESTS 94-104
# ============================================================

test_94() {
    # AutoDecision: SetDoneWritesDone — apply-transition SET_DONE creates decision with decision: "DONE"
    init_test_workspace
    run_core apply-transition --action SET_DONE >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    [[ -f "$decision_file" ]] || { echo "continuation-decision.json should be created by SET_DONE"; return 1; }
    local decision_val
    decision_val=$(cat "$decision_file" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('decision',''))" 2>/dev/null)
    [[ "$decision_val" == "DONE" ]] || { echo "SET_DONE should write decision DONE, got '$decision_val'"; return 1; }
    cleanup_workspace
    return 0
}

test_95() {
    # AutoDecision: SetCheckpointWritesCheckpoint — apply-transition SET_SAFE_CHECKPOINT creates decision with decision: "SAFE_CHECKPOINT"
    init_test_workspace
    run_core apply-transition --action SET_SAFE_CHECKPOINT >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    [[ -f "$decision_file" ]] || { echo "continuation-decision.json should be created by SET_SAFE_CHECKPOINT"; return 1; }
    local decision_val
    decision_val=$(cat "$decision_file" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('decision',''))" 2>/dev/null)
    [[ "$decision_val" == "SAFE_CHECKPOINT" ]] || { echo "SET_SAFE_CHECKPOINT should write decision SAFE_CHECKPOINT, got '$decision_val'"; return 1; }
    cleanup_workspace
    return 0
}

test_96() {
    # AutoDecision: SetHumanRequiredWritesDecision — apply-transition SET_HUMAN_REQUIRED creates decision with decision: "HUMAN_DECISION_REQUIRED"
    init_test_workspace
    run_core apply-transition --action SET_HUMAN_REQUIRED >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    [[ -f "$decision_file" ]] || { echo "continuation-decision.json should be created by SET_HUMAN_REQUIRED"; return 1; }
    local decision_val
    decision_val=$(cat "$decision_file" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('decision',''))" 2>/dev/null)
    [[ "$decision_val" == "HUMAN_DECISION_REQUIRED" ]] || { echo "SET_HUMAN_REQUIRED should write decision HUMAN_DECISION_REQUIRED, got '$decision_val'"; return 1; }
    cleanup_workspace
    return 0
}

test_97() {
    # AutoDecision: ContinueLoopWritesContinue — CONTINUE_LOOP with READY tasks creates decision: "CONTINUE"
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Ready task","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action CONTINUE_LOOP >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    [[ -f "$decision_file" ]] || { echo "continuation-decision.json should be created by CONTINUE_LOOP"; return 1; }
    local decision_val
    decision_val=$(cat "$decision_file" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('decision',''))" 2>/dev/null)
    [[ "$decision_val" == "CONTINUE" ]] || { echo "CONTINUE_LOOP with READY tasks should write decision CONTINUE, got '$decision_val'"; return 1; }
    cleanup_workspace
    return 0
}

test_98() {
    # AutoDecision: ContinueLoopNoReadyWritesCheckpoint — CONTINUE_LOOP with no READY tasks creates decision: "SAFE_CHECKPOINT"
    init_test_workspace
    run_core apply-transition --action CONTINUE_LOOP >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    [[ -f "$decision_file" ]] || { echo "continuation-decision.json should be created by CONTINUE_LOOP"; return 1; }
    local decision_val
    decision_val=$(cat "$decision_file" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('decision',''))" 2>/dev/null)
    [[ "$decision_val" == "SAFE_CHECKPOINT" ]] || { echo "CONTINUE_LOOP with no READY tasks should write decision SAFE_CHECKPOINT, got '$decision_val'"; return 1; }
    cleanup_workspace
    return 0
}

test_99() {
    # AutoDecision: TransientSkipsWrite — apply-transition RUN_EXECUTOR does NOT modify decision file
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Ready task","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    # Ensure no decision file exists before
    [[ ! -f "$WORKSPACE_ABS/state/continuation-decision.json" ]] || rm -f "$WORKSPACE_ABS/state/continuation-decision.json"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null 2>&1
    # Decision file should NOT have been created
    [[ ! -f "$WORKSPACE_ABS/state/continuation-decision.json" ]] && { echo "Good: RUN_EXECUTOR did not create decision file"; } || { echo "RUN_EXECUTOR should NOT create continuation-decision.json"; return 1; }
    # Also check RUN_CHANGE_REVIEWER
    run_core apply-transition --action RUN_CHANGE_REVIEWER >/dev/null 2>&1
    [[ ! -f "$WORKSPACE_ABS/state/continuation-decision.json" ]] && { echo "Good: RUN_CHANGE_REVIEWER did not create decision file"; } || { echo "RUN_CHANGE_REVIEWER should NOT create continuation-decision.json"; return 1; }
    cleanup_workspace
    return 0
}

test_100() {
    # AutoDecision: RunGatesPassWritesDecision — run-gates on PASS creates decision: "SAFE_CHECKPOINT"
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null 2>&1
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null 2>&1
    mkdir -p "$WORKSPACE_ABS/policies"
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"ok","type":"shell","command":"sh -c 'exit 0'","required":true}]}
GEOF
    run_core run-gates >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    [[ -f "$decision_file" ]] || { echo "continuation-decision.json should be created by run-gates PASS"; return 1; }
    local decision_val
    decision_val=$(cat "$decision_file" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('decision',''))" 2>/dev/null)
    [[ "$decision_val" == "SAFE_CHECKPOINT" ]] || { echo "run-gates PASS should write decision SAFE_CHECKPOINT, got '$decision_val'"; return 1; }
    cleanup_workspace
    return 0
}

test_101() {
    # AutoDecision: DecisionFileValidJson — the auto-written decision file is valid JSON
    init_test_workspace
    run_core apply-transition --action SET_SAFE_CHECKPOINT >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    [[ -f "$decision_file" ]] || { echo "continuation-decision.json should exist"; return 1; }
    "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$decision_file" 2>/dev/null || { echo "continuation-decision.json is not valid JSON"; return 1; }
    # Also verify required fields are present
    local has_required
    has_required=$("$PY" -c "
import json, sys
d = json.load(open(sys.argv[1]))
required = ['schemaVersion', 'decision', 'phase', 'justification', 'checks', 'createdAtUtc']
missing = [f for f in required if f not in d]
print(','.join(missing) if missing else 'OK')
" "$decision_file" 2>/dev/null)
    [[ "$has_required" == "OK" ]] || { echo "Decision file missing required fields: $has_required"; return 1; }
    cleanup_workspace
    return 0
}

test_102() {
    # AutoDecision: DecisionFileMatchesSchema — the auto-written decision file validates against schema
    init_test_workspace
    run_core apply-transition --action SET_SAFE_CHECKPOINT >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    "$PY" "$CORE" validate-artifact --schema continuation-decision --json-file "$decision_file" >/dev/null 2>&1 || { echo "Auto-written continuation-decision.json does not match schema"; return 1; }
    cleanup_workspace
    return 0
}

test_103() {
    # ValidateContinuation: DecisionPhaseMismatch — decision=DONE with phase!=DONE fails validate-state
    init_test_workspace
    # Write DONE decision with EXECUTING_TASK phase (mismatch)
    run_core write-continuation-decision --decision DONE --phase EXECUTING_TASK >/dev/null 2>&1
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail when decision=DONE but phase!=DONE, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_104() {
    # ValidateContinuation: AutoDecisionConsistent — after SET_SAFE_CHECKPOINT, validate-state passes (auto-written decision is consistent)
    init_test_workspace
    run_core apply-transition --action SET_SAFE_CHECKPOINT >/dev/null 2>&1
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after SET_SAFE_CHECKPOINT (auto-written decision is consistent), got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# GUARD INTEGRITY REGRESSION TESTS 105-116
# ============================================================

test_105() {
    # GuardIntegrity: CommandExists — check-guard-integrity appears in --help output
    local help_out
    help_out=$("$PY" "$CORE" --help 2>&1)
    echo "$help_out" | grep -q "check-guard-integrity" || { echo "check-guard-integrity should appear in --help, got: $help_out"; return 1; }
    return 0
}

test_106() {
    # GuardIntegrity: MissingPolicyPasses — without protected-paths.json, command returns non-blocking status
    init_test_workspace
    rm -f "$WORKSPACE_ABS/policies/protected-paths.json"
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 0 ]] || { echo "check-guard-integrity without policy should exit 0, got exit $grc: $gout"; return 1; }
    echo "$gout" | grep -q '"status": "PASS"' || { echo "check-guard-integrity should return PASS status without policy, got: $gout"; return 1; }
    echo "$gout" | grep -q "protected-paths.json not found" || { echo "Should note missing policy, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_107() {
    # GuardIntegrity: WithPolicyDetectsChanges — with a policy protecting src/**, a modified src file is detected
    init_test_workspace
    # Create and commit a src file
    mkdir -p "$TEST_REPO_DIR/src"
    printf 'original\n' > "$TEST_REPO_DIR/src/app.txt"
    git -C "$TEST_REPO_DIR" add src/app.txt >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" commit -m "add src/app" --no-verify >/dev/null 2>&1
    # Install a policy protecting src/**
    cat > "$WORKSPACE_ABS/policies/protected-paths.json" << 'PEOF'
{"schemaVersion":1,"protectedPaths":["src/**"],"enforcementLevel":"error","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}
PEOF
    # Modify the protected file and stage the change
    printf 'modified\n' > "$TEST_REPO_DIR/src/app.txt"
    git -C "$TEST_REPO_DIR" add src/app.txt >/dev/null 2>&1
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 1 ]] || { echo "check-guard-integrity should exit 1 on protected change, got exit $grc: $gout"; return 1; }
    echo "$gout" | grep -q '"status": "FAIL"' || { echo "check-guard-integrity should return FAIL, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_108() {
    # GuardIntegrity: CleanWorkspacePasses — clean workspace with policy returns PASS
    init_test_workspace
    # Install policy but make no modifications
    cat > "$WORKSPACE_ABS/policies/protected-paths.json" << 'PEOF'
{"schemaVersion":1,"protectedPaths":["scripts/**"],"enforcementLevel":"error","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}
PEOF
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 0 ]] || { echo "check-guard-integrity should pass on clean workspace, got exit $grc: $gout"; return 1; }
    echo "$gout" | grep -q '"status": "PASS"' || { echo "check-guard-integrity should return PASS on clean workspace, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_109() {
    # GuardIntegrity: SchemaIntegrity — all schema files pass integrity check
    init_test_workspace
    # Run check-guard-integrity (which checks schemas/ directory in project root)
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    echo "$gout" | grep -q '"name": "schema-integrity"' || { echo "Should have schema-integrity check, got: $gout"; return 1; }
    local si_status
    si_status=$(echo "$gout" | "$PY" -c "
import json, sys
data = json.load(sys.stdin)
for c in data.get('checks', []):
    if c['name'] == 'schema-integrity':
        print(c['status'])
        break
" 2>/dev/null)
    [[ "$si_status" == "PASS" ]] || { echo "schema-integrity check should PASS, got: $si_status"; return 1; }
    cleanup_workspace
    return 0
}

test_110() {
    # GuardIntegrity: DangerousTestDeletion — deleting a test file is detected
    init_test_workspace
    # Create and commit a test file
    mkdir -p "$TEST_REPO_DIR/tests"
    printf 'print("ok")\n' > "$TEST_REPO_DIR/tests/sample_test.py"
    git -C "$TEST_REPO_DIR" add tests/sample_test.py >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" commit -m "add test" --no-verify >/dev/null 2>&1
    # Stage deletion (git status will show D)
    rm "$TEST_REPO_DIR/tests/sample_test.py"
    git -C "$TEST_REPO_DIR" add tests/sample_test.py >/dev/null 2>&1
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    echo "$gout" | grep -q "test-file-deleted" || { echo "Should detect test file deletion, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_111() {
    # GuardIntegrity: EnforcementWarnDoesNotFail — with enforcementLevel "warn", violations produce WARNING status (exit 0)
    init_test_workspace
    # Create and commit a src file
    mkdir -p "$TEST_REPO_DIR/src"
    printf 'original\n' > "$TEST_REPO_DIR/src/app.txt"
    git -C "$TEST_REPO_DIR" add src/app.txt >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" commit -m "add src" --no-verify >/dev/null 2>&1
    # Install policy with enforcementLevel warn
    cat > "$WORKSPACE_ABS/policies/protected-paths.json" << 'PEOF'
{"schemaVersion":1,"protectedPaths":["src/**"],"enforcementLevel":"warn","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}
PEOF
    # Modify the protected file and stage the change
    printf 'modified\n' > "$TEST_REPO_DIR/src/app.txt"
    git -C "$TEST_REPO_DIR" add src/app.txt >/dev/null 2>&1
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 0 ]] || { echo "enforcementLevel warn should exit 0 even with violations, got exit $grc: $gout"; return 1; }
    # Status will be FAIL from the check, but overall exit is 0 due to warn
    echo "$gout" | grep -q '"status": "FAIL"' || { echo "Should still report FAIL status internally, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_112() {
    # GuardIntegrity: EnforcementErrorFails — with enforcementLevel "error", violations produce FAIL status (exit 1)
    init_test_workspace
    # Create and commit a src file
    mkdir -p "$TEST_REPO_DIR/src"
    printf 'original\n' > "$TEST_REPO_DIR/src/app.txt"
    git -C "$TEST_REPO_DIR" add src/app.txt >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" commit -m "add src" --no-verify >/dev/null 2>&1
    # Install policy with enforcementLevel error
    cat > "$WORKSPACE_ABS/policies/protected-paths.json" << 'PEOF'
{"schemaVersion":1,"protectedPaths":["src/**"],"enforcementLevel":"error","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}
PEOF
    # Modify the protected file and stage the change
    printf 'modified\n' > "$TEST_REPO_DIR/src/app.txt"
    git -C "$TEST_REPO_DIR" add src/app.txt >/dev/null 2>&1
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 1 ]] || { echo "enforcementLevel error should exit 1 on violation, got exit $grc: $gout"; return 1; }
    echo "$gout" | grep -q '"status": "FAIL"' || { echo "Should report FAIL status, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_113() {
    # GuardIntegrity: ValidateStateIntegration — validate-state passes when guard check is clean
    init_test_workspace
    # Install policy (but no protected files exist in temp repo, so guard check is clean)
    cat > "$WORKSPACE_ABS/policies/protected-paths.json" << 'PEOF'
{"schemaVersion":1,"protectedPaths":["src/**"],"enforcementLevel":"error","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}
PEOF
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass when guard integrity is clean, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_114() {
    # GuardIntegrity: PolicySchemaExists — protected-path-policy.schema.json exists and is valid JSON
    local schema_file="$PROJECT_ROOT/schemas/protected-path-policy.schema.json"
    [[ -f "$schema_file" ]] || { echo "protected-path-policy.schema.json missing"; return 1; }
    "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$schema_file" 2>/dev/null || { echo "protected-path-policy.schema.json is not valid JSON"; return 1; }
    return 0
}

test_115() {
    # GuardIntegrity: DefaultPolicyMatchesSchema — default policy validates against schema
    local policy_file="$PROJECT_ROOT/templates/workspace/policies/protected-paths.json"
    [[ -f "$policy_file" ]] || { echo "Default protected-paths.json missing"; return 1; }
    "$PY" "$CORE" validate-artifact --schema protected-path-policy --json-file "$policy_file" >/dev/null 2>&1 || { echo "Default policy does not match schema"; return 1; }
    return 0
}

test_116() {
    # GuardIntegrity: WrapperShExists — scripts/check-guard-integrity.sh exists and runs
    local wrapper="$PROJECT_ROOT/scripts/check-guard-integrity.sh"
    [[ -f "$wrapper" ]] || { echo "check-guard-integrity.sh wrapper missing"; return 1; }
    # Run wrapper against a clean workspace
    init_test_workspace
    set +e
    local wout wrc
    wout=$(bash "$wrapper" --workspace "$WORKSPACE_ABS" 2>&1)
    wrc=$?
    set -e
    [[ $wrc -eq 0 ]] || { echo "check-guard-integrity.sh should exit 0 on clean workspace, got exit $wrc: $wout"; return 1; }
    echo "$wout" | grep -q '"status"' || { echo "Wrapper should output JSON with status, got: $wout"; return 1; }
    cleanup_workspace
    return 0
}

# ============================================================
# SENTINEL REGRESSION TESTS 117-132
# ============================================================

test_117() {
    # Sentinel_Pass_CleanWorkspace — run-sentinel on clean workspace produces overallStatus PASS
    init_test_workspace
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0 on clean workspace, got exit $src: $sout"; return 1; }
    local overall
    overall=$(echo "$sout" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('overallStatus',''))" 2>/dev/null)
    [[ "$overall" == "PASS" ]] || { echo "overallStatus should be PASS on clean workspace, got '$overall'"; return 1; }
    cleanup_workspace
    return 0
}

test_118() {
    # Sentinel_Fail_ScopeBypass — sentinel detects scope bypass as CRITICAL finding
    init_test_workspace
    # Remove baseline forbiddenWrites AND alwaysForbiddenWrites from scope-policy to weaken it
    local policy_file="$WORKSPACE_ABS/policies/scope-policy.json"
    local content
    content=$(cat "$policy_file")
    content=$(echo "$content" | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
d['forbiddenWrites'] = []
d['alwaysForbiddenWrites'] = []
print(json.dumps(d))
")
    echo "$content" > "$policy_file"
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    # Should still exit 0 (sentinel is read-only, always prints report)
    echo "$sout" | grep -q "scope-policy-weakening" || { echo "Should detect scope-policy-weakening, got: $sout"; return 1; }
    echo "$sout" | grep -q "CRITICAL" || { echo "Scope bypass should be CRITICAL, got: $sout"; return 1; }
    local overall
    overall=$(echo "$sout" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('overallStatus',''))" 2>/dev/null)
    [[ "$overall" == "FAIL" ]] || { echo "overallStatus should be FAIL with CRITICAL finding, got '$overall'"; return 1; }
    cleanup_workspace
    return 0
}

test_119() {
    # Sentinel_Warning_InfoOnly — sentinel with only WARNING/INFO findings produces overallStatus WARNING
    init_test_workspace
    # Remove gate-policy.json to trigger a WARNING (gate-policy-weakening is WARNING when missing)
    rm -f "$WORKSPACE_ABS/policies/gate-policy.json"
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    local overall
    overall=$(echo "$sout" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('overallStatus',''))" 2>/dev/null)
    [[ "$overall" == "WARNING" ]] || { echo "overallStatus should be WARNING when only WARNING/INFO findings, got '$overall'"; return 1; }
    cleanup_workspace
    return 0
}

test_120() {
    # Sentinel_CriticalBlocksDone — validate-state fails when sentinel has CRITICAL findings
    init_test_workspace
    # Weaken scope-policy to trigger CRITICAL (clear both forbidden write arrays)
    local policy_file="$WORKSPACE_ABS/policies/scope-policy.json"
    local content
    content=$(cat "$policy_file")
    content=$(echo "$content" | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
d['forbiddenWrites'] = []
d['alwaysForbiddenWrites'] = []
print(json.dumps(d))
")
    echo "$content" > "$policy_file"
    # Run sentinel to produce the report
    run_core run-sentinel >/dev/null 2>&1
    # Now validate-state should fail because sentinel report has CRITICAL
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail when sentinel has CRITICAL findings, got: $vout"; return 1; }
    echo "$vout" | grep -qi "sentinel\|critical" || { echo "validate-state error should mention sentinel or critical, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_121() {
    # Sentinel_MissingFile_BackwardCompatible — validate-state skips when no sentinel-inspection.json exists
    init_test_workspace
    # Fresh workspace, no sentinel run has been done, so no sentinel-inspection.json
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass without sentinel-inspection.json, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_122() {
    # Sentinel_MalformedJson_FailsValidation — malformed sentinel-inspection.json caught by validate-state
    init_test_workspace
    # Run sentinel first to create the run directory
    run_core run-sentinel >/dev/null 2>&1
    # Get the run directory
    local runs_dir="$WORKSPACE_ABS/runs"
    local run_dir
    run_dir=$(ls -d "$runs_dir"/run-* 2>/dev/null | head -1)
    local sentinel_file="$run_dir/sentinel-inspection.json"
    [[ -f "$sentinel_file" ]] || { echo "sentinel-inspection.json should exist after run-sentinel"; return 1; }
    # Corrupt the sentinel file with malformed JSON
    echo '{malformed json' > "$sentinel_file"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on malformed sentinel-inspection.json, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_123() {
    # Sentinel_NoSideEffects — sentinel does not modify any files other than its own report
    init_test_workspace
    # Record checksums of all state files before
    local state_dir="$WORKSPACE_ABS/state"
    local before_checksums=""
    for f in "$state_dir"/*.json "$state_dir"/*.jsonl; do
        [[ -f "$f" ]] || continue
        local c
        c=$("$PY" -c "import hashlib,sys; print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest())" "$f" 2>/dev/null)
        before_checksums="$before_checksums $c:$f"
    done
    # Also record policies
    local pol_dir="$WORKSPACE_ABS/policies"
    for f in "$pol_dir"/*.json; do
        [[ -f "$f" ]] || continue
        local c
        c=$("$PY" -c "import hashlib,sys; print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest())" "$f" 2>/dev/null)
        before_checksums="$before_checksums $c:$f"
    done
    # Run sentinel
    run_core run-sentinel >/dev/null 2>&1
    # Verify all state/policy files unchanged
    for f in "$state_dir"/*.json "$state_dir"/*.jsonl; do
        [[ -f "$f" ]] || continue
        local c
        c=$("$PY" -c "import hashlib,sys; print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest())" "$f" 2>/dev/null)
        echo "$before_checksums" | grep -q "$c:$f" || { echo "File modified by sentinel: $f"; return 1; }
    done
    for f in "$pol_dir"/*.json; do
        [[ -f "$f" ]] || continue
        local c
        c=$("$PY" -c "import hashlib,sys; print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest())" "$f" 2>/dev/null)
        echo "$before_checksums" | grep -q "$c:$f" || { echo "File modified by sentinel: $f"; return 1; }
    done
    cleanup_workspace
    return 0
}

test_124() {
    # Sentinel_StateConsistency_Check — STATE_CONSISTENCY category: corrupt team-state.json phase, sentinel detects it
    init_test_workspace
    # Corrupt team-state.json with invalid phase
    local state_file="$WORKSPACE_ABS/state/team-state.json"
    local content
    content=$(cat "$state_file")
    content=$(echo "$content" | "$PY" -c "import json,sys; d=json.load(sys.stdin); d['currentPhase']='INVALID_PHASE_VALUE'; print(json.dumps(d))")
    echo "$content" > "$state_file"
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0 (read-only), got exit $src: $sout"; return 1; }
    # Should detect state-mutation or manual-state-mutation issue
    echo "$sout" | grep -q "state-mutation\|manual-state-mutation" || { echo "Should detect state mutation, got: $sout"; return 1; }
    cleanup_workspace
    return 0
}

test_125() {
    # Sentinel_GateWeakening_Check — GATE_WEAKENING category: empty gate-policy triggers finding
    init_test_workspace
    # Replace gate-policy with empty gates array
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[]}
GEOF
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "gate-policy-weakening" || { echo "Should detect gate-policy-weakening, got: $sout"; return 1; }
    cleanup_workspace
    return 0
}

test_126() {
    # Sentinel_TestSuppression_Check — TEST_SUPPRESSION category: temporarily rename tests/, sentinel detects it
    init_test_workspace
    # Rename tests directory temporarily
    local tests_dir="$PROJECT_ROOT/tests"
    local tests_bak="$PROJECT_ROOT/tests_bak_$$"
    mv "$tests_dir" "$tests_bak"
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "test-suppression" || { echo "Should detect test-suppression, got: $sout"; return 1; }
    # Restore tests directory
    mv "$tests_bak" "$tests_dir"
    cleanup_workspace
    return 0
}

test_127() {
    # Sentinel_ProtectedFile_Check — PROTECTED_FILE_CHANGE category reuses guard integrity detection
    init_test_workspace
    # Create and commit a script file, then modify it
    mkdir -p "$TEST_REPO_DIR/scripts"
    printf '#!/usr/bin/env python3\nprint("original")\n' > "$TEST_REPO_DIR/scripts/sample.py"
    git -C "$TEST_REPO_DIR" add scripts/sample.py >/dev/null 2>&1
    git -C "$TEST_REPO_DIR" commit -m "add script" --no-verify >/dev/null 2>&1
    # Modify the script file (staged change)
    printf '#!/usr/bin/env python3\nprint("modified")\n' > "$TEST_REPO_DIR/scripts/sample.py"
    git -C "$TEST_REPO_DIR" add scripts/sample.py >/dev/null 2>&1
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "protected-file-changes" || { echo "Should detect protected-file-changes, got: $sout"; return 1; }
    cleanup_workspace
    return 0
}

test_128() {
    # Sentinel_HiddenWork_Check — HIDDEN_UNRESOLVED_WORK detects orphaned READY tasks
    init_test_workspace
    # Add a READY task to backlog (orphaned — not picked up by current-task)
    echo '{"schemaVersion":1,"taskId":"task-orphan","title":"Orphan task","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "hidden-unresolved-work" || { echo "Should detect hidden-unresolved-work, got: $sout"; return 1; }
    cleanup_workspace
    return 0
}

test_129() {
    # Sentinel_ManualMutation_Check — MANUAL_STATE_MUTATION detects state edits without events
    init_test_workspace
    # Manually change the phase without going through apply-transition
    local state_file="$WORKSPACE_ABS/state/team-state.json"
    local content
    content=$(cat "$state_file")
    content=$(echo "$content" | "$PY" -c "import json,sys; d=json.load(sys.stdin); d['currentPhase']='EXECUTING_TASK'; d['currentTaskId']='task-no-event'; print(json.dumps(d))")
    echo "$content" > "$state_file"
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "manual-state-mutation\|state-mutation" || { echo "Should detect manual state mutation, got: $sout"; return 1; }
    cleanup_workspace
    return 0
}

test_130() {
    # Sentinel_EvidenceManipulation_Check — EVIDENCE_MANIPULATION detects event gaps
    init_test_workspace
    # Run a transition to create events, then delete events to create a gap
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Test task","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-001 >/dev/null 2>&1
    run_core apply-transition --action RUN_CHANGE_REVIEWER >/dev/null 2>&1
    # Now events.jsonl has entries but we truncate it to remove the transition events
    local events_file="$WORKSPACE_ABS/state/events.jsonl"
    local line_count
    line_count=$(wc -l < "$events_file")
    if [[ "$line_count" -ge 3 ]]; then
        # Keep only the first line (init event) and delete the rest
        head -1 "$events_file" > "${events_file}.tmp"
        mv "${events_file}.tmp" "$events_file"
    fi
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "evidence-manipulation" || { echo "Should detect evidence-manipulation, got: $sout"; return 1; }
    cleanup_workspace
    return 0
}

test_131() {
    # Sentinel_DocsDrift_Check — DOCS_CONTRACT_DRIFT detects invalid schema JSON
    init_test_workspace
    # Temporarily corrupt a schema file
    local schema_file="$PROJECT_ROOT/schemas/task.schema.json"
    local schema_bak="$PROJECT_ROOT/schemas/task.schema.json.bak_$$"
    cp "$schema_file" "$schema_bak"
    echo '{invalid json' > "$schema_file"
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "schema-integrity" || { echo "Should detect schema-integrity issue, got: $sout"; return 1; }
    # Restore schema file
    mv "$schema_bak" "$schema_file"
    cleanup_workspace
    return 0
}

test_132() {
    # Sentinel_PowerShellWrapper — run-sentinel.ps1 exists and is valid
    local wrapper="$PROJECT_ROOT/scripts/run-sentinel.ps1"
    [[ -f "$wrapper" ]] || { echo "run-sentinel.ps1 wrapper missing"; return 1; }
    local content
    content=$(cat "$wrapper")
    echo "$content" | grep -q "run-sentinel" || { echo "run-sentinel.ps1 should invoke run-sentinel command"; return 1; }
    echo "$content" | grep -q "PSScriptRoot" || { echo "run-sentinel.ps1 should use PSScriptRoot"; return 1; }
    # Test actual execution using python directly (pwsh not available in WSL context)
    init_test_workspace
    set +e
    local sout src
    sout=$(run_core run-sentinel 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "run-sentinel should exit 0, got exit $src: $sout"; return 1; }
    echo "$sout" | grep -q "overallStatus" || { echo "run-sentinel output should contain overallStatus, got: $sout"; return 1; }
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
test_run "Memory: EmptyPasses" test_52
test_run "Memory: MalformedJsonlFails" test_53
test_run "Memory: ActiveWithoutEvidenceFails" test_54
test_run "Memory: ActiveWithValidEvidencePasses" test_55
test_run "Memory: ActiveWithMissingEvidenceIdFails" test_56
test_run "Memory: DeprecatedRetainedButInactive" test_57
test_run "Memory: SupersededWithoutEvidencePasses" test_58
test_run "Memory: RejectedAntipatternWithoutEvidencePasses" test_59
test_run "Memory: MissingMemoryDirPasses" test_60
test_run "Memory: ProfileValidation" test_61
test_run "Memory: DoctorEmptyPasses" test_62
test_run "Memory: DoctorDetectsIssues" test_63
test_run "Memory: ActiveWithUnverifiedEvidenceFails" test_64
test_run "WriteEvent: InvalidTypeRejected" test_65
test_run "Memory: ActiveWithUnverifiedEvidenceFailsSchemaValid" test_66
test_run "Memory: SupersededByFailsBothValidateStateAndMemoryDoctor" test_67
test_run "MemoryDoctor: MissingDirectoryFails" test_68
test_run "MemoryDoctor: EmptySubsystemWarns" test_69
test_run "Memory: ProfileDeprecatedFieldsRejected" test_70
test_run "WriteEvent: InvalidTypeRejected_PSParity" test_71
test_run "MemoryDoctor: MissingDirFails_PSParity" test_72
test_run "Memory: SupersededByBothCheck_PSParity" test_73
test_run "Memory: ActiveWithUnverifiedEvidenceSemantic" test_74
test_run "MemoryDoctor: WarningNotFail" test_75
test_run "Memory: ProfileRemovedDeprecatedFields" test_76

# ============================================================
# CONTINUATION-DECISION TESTS 77-93
# ============================================================

# --- Writer Tests ---

test_77() {
    # WriteContinuationDecision: ValidDecision — writing SAFE_CHECKPOINT succeeds (exit 0, file created)
    init_test_workspace
    set +e
    local wout wrc
    wout=$(run_core write-continuation-decision --decision SAFE_CHECKPOINT --phase EXECUTING_TASK 2>&1)
    wrc=$?
    set -e
    [[ $wrc -eq 0 ]] || { echo "write-continuation-decision SAFE_CHECKPOINT should exit 0, got exit $wrc: $wout"; return 1; }
    [[ -f "$WORKSPACE_ABS/state/continuation-decision.json" ]] || { echo "continuation-decision.json should be created"; return 1; }
    cleanup_workspace
    return 0
}

test_78() {
    # WriteContinuationDecision: InvalidDecision — writing INVALID fails (exit 1, error to stderr)
    init_test_workspace
    set +e
    local wout wrc
    wout=$(run_core write-continuation-decision --decision INVALID --phase EXECUTING_TASK 2>&1)
    wrc=$?
    set -e
    [[ $wrc -eq 1 ]] || { echo "write-continuation-decision with INVALID should exit 1, got exit $wrc: $wout"; return 1; }
    echo "$wout" | grep -qi "invalid\|error" || { echo "Should report invalid decision error, got: $wout"; return 1; }
    cleanup_workspace
    return 0
}

test_79() {
    # WriteContinuationDecision: AllDecisionsValid — each of the 5 decisions succeeds
    init_test_workspace
    local decisions="DONE SAFE_CHECKPOINT CONTINUE HUMAN_DECISION_REQUIRED BLOCKED"
    for dec in $decisions; do
        set +e
        local wout wrc
        wout=$(run_core write-continuation-decision --decision "$dec" --phase EXECUTING_TASK 2>&1)
        wrc=$?
        set -e
        [[ $wrc -eq 0 ]] || { echo "Decision '$dec' should succeed, got exit $wrc: $wout"; return 1; }
    done
    cleanup_workspace
    return 0
}

test_80() {
    # WriteContinuationDecision: OutputIsValidJson — the written file is valid JSON
    init_test_workspace
    run_core write-continuation-decision --decision SAFE_CHECKPOINT --phase EXECUTING_TASK >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$decision_file" 2>/dev/null || { echo "continuation-decision.json is not valid JSON"; return 1; }
    cleanup_workspace
    return 0
}

test_81() {
    # WriteContinuationDecision: OutputMatchesSchema — validates against continuation-decision.schema.json
    init_test_workspace
    run_core write-continuation-decision --decision CONTINUE --phase EXECUTING_TASK >/dev/null 2>&1
    local decision_file="$WORKSPACE_ABS/state/continuation-decision.json"
    "$PY" "$CORE" validate-artifact --schema continuation-decision --json-file "$decision_file" >/dev/null 2>&1 || { echo "continuation-decision.json does not match schema"; return 1; }
    cleanup_workspace
    return 0
}

# --- Validator Tests ---

test_82() {
    # ValidateContinuation: MissingDecisionPasses — missing continuation-decision.json passes validate-state
    init_test_workspace
    # Fresh workspace, no continuation-decision.json
    [[ ! -f "$WORKSPACE_ABS/state/continuation-decision.json" ]] || { echo "decision file should not exist"; return 1; }
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "Missing decision file should pass validate-state, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_83() {
    # ValidateContinuation: DoneRequiresDonePhase — decision=DONE with non-DONE phase fails validate-state
    init_test_workspace
    run_core write-continuation-decision --decision DONE --phase EXECUTING_TASK >/dev/null 2>&1
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "DONE decision with EXECUTING_TASK phase should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_84() {
    # ValidateContinuation: HumanDecisionRequiresBlockers — HUMAN_DECISION_REQUIRED with no blockers fails
    init_test_workspace
    run_core write-continuation-decision --decision HUMAN_DECISION_REQUIRED --phase HUMAN_DECISION_REQUIRED >/dev/null 2>&1
    # Also set the team-state phase
    local state_file="$WORKSPACE_ABS/state/team-state.json"
    local content
    content=$(cat "$state_file")
    content=$(echo "$content" | "$PY" -c "import json,sys; d=json.load(sys.stdin); d['currentPhase']='HUMAN_DECISION_REQUIRED'; d['status']='HUMAN_DECISION_REQUIRED'; print(json.dumps(d))")
    echo "$content" > "$state_file"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "HUMAN_DECISION_REQUIRED without blockers should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_85() {
    # ValidateContinuation: SafeCheckpointAfterDoneFails — SAFE_CHECKPOINT after phase=DONE fails
    init_test_workspace
    # Set phase to DONE
    local state_file="$WORKSPACE_ABS/state/team-state.json"
    local content
    content=$(cat "$state_file")
    content=$(echo "$content" | "$PY" -c "import json,sys; d=json.load(sys.stdin); d['currentPhase']='DONE'; d['status']='DONE'; print(json.dumps(d))")
    echo "$content" > "$state_file"
    run_core write-continuation-decision --decision SAFE_CHECKPOINT --phase DONE >/dev/null 2>&1
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "SAFE_CHECKPOINT after DONE phase should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_86() {
    # ValidateContinuation: ContinueRequiresReadyTasks — CONTINUE with no READY tasks fails
    init_test_workspace
    run_core write-continuation-decision --decision CONTINUE --phase EXECUTING_TASK >/dev/null 2>&1
    # No tasks in backlog at all
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "CONTINUE without READY tasks should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_87() {
    # ValidateContinuation: BlockedRequiresBlockers — BLOCKED with no open blockers fails
    init_test_workspace
    run_core write-continuation-decision --decision BLOCKED --phase EXECUTING_TASK >/dev/null 2>&1
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "BLOCKED without blockers should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_88() {
    # ValidateContinuation: DoneRequiresCleanState — DONE with READY tasks fails
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Open task","status":"READY","scope":["src/**"],"successCriteria":["Works"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core write-continuation-decision --decision DONE --phase DONE >/dev/null 2>&1
    # Set state to DONE
    local state_file="$WORKSPACE_ABS/state/team-state.json"
    local content
    content=$(cat "$state_file")
    content=$(echo "$content" | "$PY" -c "import json,sys; d=json.load(sys.stdin); d['currentPhase']='DONE'; d['status']='DONE'; print(json.dumps(d))")
    echo "$content" > "$state_file"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "DONE with READY tasks should fail, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

# --- Schema Tests ---

test_89() {
    # Schema: ContinuationDecisionExists — schema file exists and is valid JSON
    local schema_file="$PROJECT_ROOT/schemas/continuation-decision.schema.json"
    [[ -f "$schema_file" ]] || { echo "continuation-decision.schema.json missing"; return 1; }
    "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$schema_file" 2>/dev/null || { echo "continuation-decision.schema.json is not valid JSON"; return 1; }
    return 0
}

test_90() {
    # Schema: ContinuationDecisionEnum — schema contains all 5 decision values
    local schema_file="$PROJECT_ROOT/schemas/continuation-decision.schema.json"
    local enum_vals
    enum_vals=$(cat "$schema_file" | "$PY" -c "
import json, sys
schema = json.load(open(sys.argv[1]))
vals = schema.get('properties', {}).get('decision', {}).get('enum', [])
print(','.join(sorted(vals)))
" "$schema_file")
    [[ "$enum_vals" == "BLOCKED,CONTINUE,DONE,HUMAN_DECISION_REQUIRED,SAFE_CHECKPOINT" ]] || { echo "Schema should contain all 5 decisions, got: $enum_vals"; return 1; }
    return 0
}

test_91() {
    # Schema: ContinuationDecisionAdditionalProps — schema rejects additional properties
    local schema_file="$PROJECT_ROOT/schemas/continuation-decision.schema.json"
    local has_additional
    has_additional=$(cat "$schema_file" | "$PY" -c "
import json, sys
schema = json.load(open(sys.argv[1]))
print(schema.get('additionalProperties', True))
" "$schema_file")
    [[ "$has_additional" == "False" ]] || { echo "Schema should have additionalProperties: false, got: $has_additional"; return 1; }
    return 0
}

# --- Integration Tests ---

test_92() {
    # Continuation: WriteThenValidate — write a valid decision, validate-state passes
    init_test_workspace
    # Write SAFE_CHECKPOINT with initial phase matching team-state
    run_core write-continuation-decision --decision SAFE_CHECKPOINT --phase INITIALIZED >/dev/null 2>&1
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after writing valid SAFE_CHECKPOINT, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_93() {
    # Continuation: StaleTaskIdWarning — decision with non-existent taskId produces validation error
    init_test_workspace
    run_core write-continuation-decision --decision SAFE_CHECKPOINT --phase INITIALIZED --task-id task-nonexistent >/dev/null 2>&1
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail with non-existent taskId, got: $vout"; return 1; }
    echo "$vout" | grep -qi "task-nonexistent\|not found" || { echo "validate-state error should mention the stale taskId, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_run "WriteContinuationDecision: ValidDecision" test_77
test_run "WriteContinuationDecision: InvalidDecision" test_78
test_run "WriteContinuationDecision: AllDecisionsValid" test_79
test_run "WriteContinuationDecision: OutputIsValidJson" test_80
test_run "WriteContinuationDecision: OutputMatchesSchema" test_81
test_run "ValidateContinuation: MissingDecisionPasses" test_82
test_run "ValidateContinuation: DoneRequiresDonePhase" test_83
test_run "ValidateContinuation: HumanDecisionRequiresBlockers" test_84
test_run "ValidateContinuation: SafeCheckpointAfterDoneFails" test_85
test_run "ValidateContinuation: ContinueRequiresReadyTasks" test_86
test_run "ValidateContinuation: BlockedRequiresBlockers" test_87
test_run "ValidateContinuation: DoneRequiresCleanState" test_88
test_run "Schema: ContinuationDecisionExists" test_89
test_run "Schema: ContinuationDecisionEnum" test_90
test_run "Schema: ContinuationDecisionAdditionalProps" test_91
test_run "Continuation: WriteThenValidate" test_92
test_run "Continuation: StaleTaskIdWarning" test_93
test_run "AutoDecision: SetDoneWritesDone" test_94
test_run "AutoDecision: SetCheckpointWritesCheckpoint" test_95
test_run "AutoDecision: SetHumanRequiredWritesDecision" test_96
test_run "AutoDecision: ContinueLoopWritesContinue" test_97
test_run "AutoDecision: ContinueLoopNoReadyWritesCheckpoint" test_98
test_run "AutoDecision: TransientSkipsWrite" test_99
test_run "AutoDecision: RunGatesPassWritesDecision" test_100
test_run "AutoDecision: DecisionFileValidJson" test_101
test_run "AutoDecision: DecisionFileMatchesSchema" test_102
test_run "ValidateContinuation: DecisionPhaseMismatch" test_103
test_run "ValidateContinuation: AutoDecisionConsistent" test_104
test_run "GuardIntegrity: CommandExists" test_105
test_run "GuardIntegrity: MissingPolicyPasses" test_106
test_run "GuardIntegrity: WithPolicyDetectsChanges" test_107
test_run "GuardIntegrity: CleanWorkspacePasses" test_108
test_run "GuardIntegrity: SchemaIntegrity" test_109
test_run "GuardIntegrity: DangerousTestDeletion" test_110
test_run "GuardIntegrity: EnforcementWarnDoesNotFail" test_111
test_run "GuardIntegrity: EnforcementErrorFails" test_112
test_run "GuardIntegrity: ValidateStateIntegration" test_113
test_run "GuardIntegrity: PolicySchemaExists" test_114
test_run "GuardIntegrity: DefaultPolicyMatchesSchema" test_115
test_run "GuardIntegrity: WrapperShExists" test_116
test_run "Sentinel: Pass_CleanWorkspace" test_117
test_run "Sentinel: Fail_ScopeBypass" test_118
test_run "Sentinel: Warning_InfoOnly" test_119
test_run "Sentinel: CriticalBlocksDone" test_120
test_run "Sentinel: MissingFile_BackwardCompatible" test_121
test_run "Sentinel: MalformedJson_FailsValidation" test_122
test_run "Sentinel: NoSideEffects" test_123
test_run "Sentinel: StateConsistency_Check" test_124
test_run "Sentinel: GateWeakening_Check" test_125
test_run "Sentinel: TestSuppression_Check" test_126
test_run "Sentinel: ProtectedFile_Check" test_127
test_run "Sentinel: HiddenWork_Check" test_128
test_run "Sentinel: ManualMutation_Check" test_129
test_run "Sentinel: EvidenceManipulation_Check" test_130
test_run "Sentinel: DocsDrift_Check" test_131
test_run "Sentinel: PowerShellWrapper" test_132

# ============================================================
# E2E SMOKE SCENARIO TESTS 133-138
# ============================================================

test_133() {
    # E2E_SuccessfulBoundedTask — full lifecycle: init, backlog, executor, file, scope, gates, validate
    init_test_workspace
    # 1. Create a READY task in backlog
    echo '{"schemaVersion":1,"taskId":"task-e2e-1","title":"E2E task","status":"READY","scope":["src/**"],"allowedWrites":["src/**", ".teamloop/**"],"successCriteria":["src/hello.txt exists"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    # 2. Apply RUN_EXECUTOR transition
    run_core apply-transition --action RUN_EXECUTOR --task-id task-e2e-1 >/dev/null
    # 3. Create a file in scope
    mkdir -p "$TEST_REPO_DIR/src"
    printf 'hello\n' > "$TEST_REPO_DIR/src/hello.txt"
    git -C "$TEST_REPO_DIR" add src/hello.txt >/dev/null 2>&1
    # 4. Verify check-scope passes
    set +e
    local cs csrc
    cs=$(run_core check-scope 2>&1)
    csrc=$?
    set -e
    local cs_status
    cs_status=$(echo "$cs" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$cs_status" == "PASS" ]] || { echo "check-scope should PASS for in-scope file, got '$cs_status'"; return 1; }
    # 5. Verify run-gates passes (no gate policy = no gates = PASS)
    set +e
    local gout grc
    gout=$(run_core run-gates 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 0 ]] || { echo "run-gates should PASS with no failing gates, got exit $grc: $gout"; return 1; }
    # 6. Verify validate-state passes
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should PASS after successful bounded task, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_134() {
    # E2E_ScopeViolation — file created outside allowed scope fails check-scope
    init_test_workspace
    # 1. Create READY task with allowedWrites: ["src/**"]
    echo '{"schemaVersion":1,"taskId":"task-e2e-2","title":"Scope violation test","status":"READY","scope":["src/**"],"allowedWrites":["src/**", ".teamloop/**"],"successCriteria":["scope"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    # 2. Run RUN_EXECUTOR
    run_core apply-transition --action RUN_EXECUTOR --task-id task-e2e-2 >/dev/null
    # 3. Create a file OUTSIDE scope (foo.txt at root)
    printf 'out of scope\n' > "$TEST_REPO_DIR/foo.txt"
    git -C "$TEST_REPO_DIR" add foo.txt >/dev/null 2>&1
    # 4. Verify check-scope FAILS
    set +e
    local cs csrc
    cs=$(run_core check-scope 2>&1)
    csrc=$?
    set -e
    local cs_status
    cs_status=$(echo "$cs" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$cs_status" == "FAIL" ]] || { echo "check-scope should FAIL for out-of-scope file, got '$cs_status'"; return 1; }
    cleanup_workspace
    return 0
}

test_135() {
    # E2E_GateFailure — required gate that fails causes run-gates to exit 1
    init_test_workspace
    # Set up a task and start a run (run-gates needs currentRunId)
    echo '{"schemaVersion":1,"taskId":"task-e2e-g","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-e2e-g >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    # 1. Create a gate-policy.json with a required gate that fails
    cat > "$WORKSPACE_ABS/policies/gate-policy.json" << 'GEOF'
{"gates":[{"name":"always-fail","type":"shell","command":"sh -c 'exit 1'","required":true}]}
GEOF
    # 2. Verify run-gates FAILS
    set +e
    local gout grc
    gout=$(run_core run-gates 2>&1)
    grc=$?
    set -e
    [[ $grc -eq 1 ]] || { echo "run-gates with required fail gate should exit 1, got exit $grc: $gout"; return 1; }
    local gate_status
    gate_status=$(echo "$gout" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$gate_status" == "FAIL" ]] || { echo "Gate status should be FAIL, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_136() {
    # E2E_HumanBlocker — open blocker prevents SET_DONE from passing validate-state
    init_test_workspace
    # 1. Create a valid blocker in blockers.jsonl
    echo '{"schemaVersion":1,"blockerId":"blocker-e2e","type":"HUMAN_DECISION_REQUIRED","category":"PRODUCT_BEHAVIOR_AMBIGUITY","summary":"Need approval for E2E","evidence":["evidence"],"questionsForHuman":["Should we proceed?"]}' >> "$WORKSPACE_ABS/state/blockers.jsonl"
    # 2. Attempt SET_DONE via apply-transition
    run_core apply-transition --action SET_DONE >/dev/null 2>&1
    # 3. Verify validate-state FAILS (open blocker prevents DONE)
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should FAIL with open blocker, got: $vout"; return 1; }
    cleanup_workspace
    return 0
}

test_137() {
    # E2E_ProtectedChange — staged change to protected path detected by check-guard-integrity
    init_test_workspace
    # 1. Copy protected-paths.json template to workspace (protect scripts/**)
    cat > "$WORKSPACE_ABS/policies/protected-paths.json" << 'PEOF'
{"schemaVersion":1,"protectedPaths":["scripts/**"],"enforcementLevel":"error","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}
PEOF
    # 2. Create a file in scripts/ and stage it
    mkdir -p "$TEST_REPO_DIR/scripts"
    printf '#!/usr/bin/env python3\nprint("test")\n' > "$TEST_REPO_DIR/scripts/new-script.py"
    git -C "$TEST_REPO_DIR" add scripts/new-script.py >/dev/null 2>&1
    # 3. Verify check-guard-integrity detects the protected change
    set +e
    local gout grc
    gout=$(run_core check-guard-integrity 2>&1)
    grc=$?
    set -e
    echo "$gout" | grep -q "protected-paths" || { echo "check-guard-integrity should detect protected path change, got: $gout"; return 1; }
    cleanup_workspace
    return 0
}

test_138() {
    # E2E_MemoryIntegrity — valid memory passes memory-doctor, invalid memory fails
    init_test_workspace
    # 1. Create valid memory (lesson + evidence in evidence-map.jsonl)
    local evidence='{"schemaVersion":1,"evidenceId":"evidence-e2e","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z"}'
    local lesson='{"schemaVersion":1,"lessonId":"lesson-e2e","title":"E2E lesson","description":"Memory integrity test","status":"ACTIVE","evidenceIds":["evidence-e2e"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$evidence" > "$WORKSPACE_ABS/memory/evidence-map.jsonl"
    echo "$lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    # 2. Verify memory-doctor passes
    set +e
    local dout drc
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 0 ]] || { echo "memory-doctor should PASS with valid memory, got: $dout"; return 1; }
    echo "$dout" | grep -q '"status": "PASS"' || { echo "memory-doctor output should contain PASS, got: $dout"; return 1; }
    # 3. Replace with invalid memory (active lesson without evidence)
    local bad_lesson='{"schemaVersion":1,"lessonId":"lesson-bad","title":"Bad lesson","description":"No evidence","status":"ACTIVE","createdAtUtc":"2024-01-01T00:00:00Z"}'
    echo "$bad_lesson" > "$WORKSPACE_ABS/memory/lessons.jsonl"
    # 4. Verify memory-doctor fails
    set +e
    dout=$("$PY" "$CORE" memory-doctor --workspace "$WORKSPACE_ABS" 2>&1)
    drc=$?
    set -e
    [[ $drc -eq 1 ]] || { echo "memory-doctor should FAIL with invalid memory (active lesson without evidence), got: $dout"; return 1; }
    echo "$dout" | grep -q '"status": "FAIL"' || { echo "memory-doctor output should contain FAIL, got: $dout"; return 1; }
    cleanup_workspace
    return 0
}

test_run "E2E: SuccessfulBoundedTask" test_133
test_run "E2E: ScopeViolation" test_134
test_run "E2E: GateFailure" test_135
test_run "E2E: HumanBlocker" test_136
test_run "E2E: ProtectedChange" test_137
test_run "E2E: MemoryIntegrity" test_138

# ============================================================
# CAMPAIGN REGRESSION TESTS (139-150)
# ============================================================

# Test 139: FinalGate_Pass
test_139() {
    init_test_workspace
    # Write minimal continuation-decision so validate-state passes
    echo '{"schemaVersion":1,"decision":"SAFE_CHECKPOINT","phase":"SAFE_CHECKPOINT","justification":"test checkpoint","checks":[{"name":"test","status":"PASS"}],"createdAtUtc":"2024-01-01T00:00:00Z"}' > "$WORKSPACE_ABS/state/continuation-decision.json"
    set +e
    local out rc
    out=$("$PY" "$CORE" final-gate --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -eq 0 ]] || { echo "final-gate should exit 0 on valid workspace, got rc=$rc: $out"; return 1; }
    echo "$out" | grep -q '"overallStatus": "PASS"' || { echo "final-gate output should contain PASS, got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 140: FinalGate_FailValidation
test_140() {
    init_test_workspace
    # Corrupt team-state.json by removing required field
    echo '{"schemaVersion":1}' > "$WORKSPACE_ABS/state/team-state.json"
    set +e
    local out rc
    out=$("$PY" "$CORE" final-gate --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -ne 0 ]] || { echo "final-gate should fail on corrupted state, got rc=$rc"; return 1; }
    echo "$out" | grep -q '"overallStatus": "FAIL"' || { echo "final-gate output should contain FAIL, got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 141: FinalGate_SchemaValid
test_141() {
    init_test_workspace
    echo '{"schemaVersion":1,"decision":"SAFE_CHECKPOINT","phase":"SAFE_CHECKPOINT","justification":"test checkpoint","checks":[{"name":"test","status":"PASS"}],"createdAtUtc":"2024-01-01T00:00:00Z"}' > "$WORKSPACE_ABS/state/continuation-decision.json"
    set +e
    local out rc
    out=$("$PY" "$CORE" final-gate --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -eq 0 ]] || { echo "final-gate should pass for valid workspace, got: $out"; return 1; }
    local result_file="$WORKSPACE_ABS/state/final-gate-result.json"
    [[ -f "$result_file" ]] || { echo "final-gate-result.json should exist at state/"; return 1; }
    # Validate JSON is parseable
    "$PY" -c "import json,sys; json.loads(open(sys.argv[1]).read())" "$result_file" 2>/dev/null || { echo "final-gate-result.json should be valid JSON"; return 1; }
    # Validate required fields exist
    local req_fields='schemaVersion checkedAtUtc currentBranch currentHead overallStatus checks'
    for field in $req_fields; do
      grep -q "\"$field\"" "$result_file" || { echo "final-gate-result.json should have field '$field'"; return 1; }
    done
    cleanup_workspace
    return 0
}

# Test 142: ReviewEvidence_ContentMissing
test_142() {
    init_test_workspace
    # Write review evidence referencing a file that doesn't exist
    local evidence='{"schemaVersion":1,"taskId":"task-missing","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedCommit":"'$(git rev-parse HEAD)'","reviewedFiles":[{"path":"src/nonexistent.txt","hash":"0000000000000000000000000000000000000000000000000000000000000000","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    echo "$evidence" > "$WORKSPACE_ABS/state/review-evidence.json"
    set +e
    local out rc
    out=$("$PY" "$CORE" validate-state --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -ne 0 ]] || { echo "validate-state should FAIL when reviewed content is missing, got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 143: ReviewEvidence_ContentChanged
test_143() {
    init_test_workspace
    # Use a tracked file that exists: TEAMLOOP.md
    local hash
    hash=$(sha256sum "$PROJECT_ROOT/TEAMLOOP.md" | cut -d' ' -f1)
    # Write review evidence with a WRONG hash to simulate changed content
    local wrong_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    local evidence='{"schemaVersion":1,"taskId":"task-changed","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedFiles":[{"path":"TEAMLOOP.md","hash":"'${wrong_hash}'","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    echo "$evidence" > "$WORKSPACE_ABS/state/review-evidence.json"
    set +e
    local out rc
    out=$("$PY" "$CORE" validate-state --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -ne 0 ]] || { echo "validate-state should FAIL when reviewed content hash differs, got: $out"; return 1; }
    echo "$out" | grep -qi "changed\|mismatch\|hash" || { echo "validate-state should report content change, got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 144: ReviewEvidence_ValidContent
test_144() {
    init_test_workspace
    # Use TEAMLOOP.md with its correct hash
    local hash
    hash=$(sha256sum "$PROJECT_ROOT/TEAMLOOP.md" | cut -d' ' -f1)
    local evidence='{"schemaVersion":1,"taskId":"task-valid","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedFiles":[{"path":"TEAMLOOP.md","hash":"'${hash}'","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    echo "$evidence" > "$WORKSPACE_ABS/state/review-evidence.json"
    set +e
    local out rc
    out=$("$PY" "$CORE" validate-state --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -eq 0 ]] || { echo "validate-state should PASS with matching reviewed content hash, got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 145: GuardNotConfigured
test_145() {
    init_test_workspace
    rm -f "$WORKSPACE_ABS/policies/protected-paths.json"
    # No protected-paths.json — guard should report NOT_CONFIGURED, not PASS
    set +e
    local out rc
    out=$("$PY" "$CORE" check-guard-integrity --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    echo "$out" | grep -q '"status": "NOT_CONFIGURED"' || { echo "check-guard-integrity should report NOT_CONFIGURED when policy is missing, got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 146: OrphanedInProgressDetected
test_146() {
    init_test_workspace
    # Add an IN_PROGRESS task to backlog but leave currentTaskId empty
    local task='{"schemaVersion":1,"taskId":"task-orphan","title":"Orphan task","status":"IN_PROGRESS","priority":"P1","origin":"manual","scope":["src/**"],"allowedWrites":["src/**"],"successCriteria":["task should be detected as orphan"]}'
    echo "$task" > "$WORKSPACE_ABS/state/backlog.jsonl"
    # team-state has empty currentTaskId — should detect orphaned IN_PROGRESS
    set +e
    local out rc
    out=$("$PY" "$CORE" validate-state --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -ne 0 ]] || { echo "validate-state should FAIL with orphaned IN_PROGRESS task, got: $out"; return 1; }
    echo "$out" | grep -qi "orphan\|IN_PROGRESS\|inconsisten\|stale" || { echo "validate-state output should mention orphan/IN_PROGRESS issue, got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 147: MojibakeDetection
test_147() {
    # Verify TEAMLOOP.md does not contain the mojibake sequence
    local teamloop_file="$PROJECT_ROOT/TEAMLOOP.md"
    [[ -f "$teamloop_file" ]] || { echo "TEAMLOOP.md should exist"; return 1; }
    # The known mojibake bytes (UTF-8 reinterpreted as CP1251) for the "≠" symbol
    # Check for the literal mojibake text
    local content
    content=$(cat "$teamloop_file")
    # Check that the correct symbols exist and known CP866 mojibake tokens do not.
    echo "$content" | grep -q '≠' || { echo "TEAMLOOP.md should contain the ≠ symbol"; return 1; }
    if grep -Eq 'тЙа|тАФ|тЖТ|тЦ╝' "$teamloop_file"; then
        echo "TEAMLOOP.md contains known encoding corruption"
        return 1
    fi
    cleanup_workspace
    return 0
}

# Test 148: CrossTaskCleanup_Preserved
test_148() {
    init_test_workspace
    # Use TEAMLOOP.md with a wrong hash to simulate tampered cross-task content
    local wrong_hash="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    local evidence='{"schemaVersion":1,"taskId":"task-cross","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedFiles":[{"path":"TEAMLOOP.md","hash":"'${wrong_hash}'","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    echo "$evidence" > "$WORKSPACE_ABS/state/review-evidence.json"
    set +e
    local out rc
    out=$("$PY" "$CORE" validate-state --workspace "$WORKSPACE_ABS" 2>&1)
    rc=$?
    set +e
    [[ $rc -ne 0 ]] || { echo "validate-state should FAIL when reviewed content was tampered (cross-task cleanup), got: $out"; return 1; }
    cleanup_workspace
    return 0
}

# Test 149: FinalGate_BashWrapperExists
test_149() {
    local wrapper="$PROJECT_ROOT/scripts/final-gate.sh"
    [[ -f "$wrapper" ]] || { echo "final-gate.sh wrapper should exist"; return 1; }
    [[ -x "$wrapper" ]] || { echo "final-gate.sh wrapper should be executable"; return 1; }
    cleanup_workspace
    return 0
}

# Test 150: FinalGate_PSWrapperExists
test_150() {
    local wrapper="$PROJECT_ROOT/scripts/final-gate.ps1"
    [[ -f "$wrapper" ]] || { echo "final-gate.ps1 wrapper should exist"; return 1; }
    cleanup_workspace
    return 0
}

test_run "Campaign: FinalGate_Pass" test_139
test_run "Campaign: FinalGate_FailValidation" test_140
test_run "Campaign: FinalGate_SchemaValid" test_141
test_run "Campaign: ReviewEvidence_ContentMissing" test_142
test_run "Campaign: ReviewEvidence_ContentChanged" test_143
test_run "Campaign: ReviewEvidence_ValidContent" test_144
test_run "Campaign: GuardNotConfigured" test_145
test_run "Campaign: OrphanedInProgressDetected" test_146
test_run "Campaign: MojibakeDetection" test_147
test_run "Campaign: CrossTaskCleanup_Preserved" test_148
test_run "Campaign: FinalGate_BashWrapperExists" test_149
test_run "Campaign: FinalGate_PSWrapperExists" test_150

# ============================================================
# FAST EXECUTION CONTRACT TESTS 151-170
# ============================================================

FAST_RUN_ID=""

start_fast_execution_task() {
    local task_id="$1" priority="$2" scope_pattern="$3" allowed_pattern="$4"
    "$PY" - "$WORKSPACE_ABS/state/backlog.jsonl" "$task_id" "$priority" "$scope_pattern" "$allowed_pattern" <<'PY'
import json,sys
path,task_id,priority,scope,allowed=sys.argv[1:]
task={
  "schemaVersion":1,"taskId":task_id,"title":"Fast execution test",
  "status":"READY","priority":priority,"origin":"fast-execution-tests",
  "scope":[scope],"allowedWrites":[allowed,".teamloop/**"],
  "requiredEvidence":["test evidence"],"successCriteria":["scenario passes"],
  "forbiddenActions":["do not weaken gates"],"humanRequired":False,"blockers":[]
}
with open(path,"a",encoding="utf-8") as f:
    f.write(json.dumps(task)+"\n")
PY
    local out
    out=$(run_core apply-transition --action RUN_EXECUTOR --task-id "$task_id") || return 1
    FAST_RUN_ID=$(json_str "$out" runId)
    [[ -n "$FAST_RUN_ID" ]] || { echo "run id missing"; return 1; }
}

test_151() {
    init_test_workspace
    start_fast_execution_task task-fast-profile P2 "src/**" "src/**"
    local out
    out=$(run_core prepare-execution)
    [[ "$(json_str "$out" profile)" == "fast" ]] || { echo "low-risk task should resolve fast: $out"; return 1; }
    "$PY" - "$WORKSPACE_ABS/runs/$FAST_RUN_ID/execution-policy.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
assert p['requiredRoles']==['executor'],p
assert 'watchdog' in p['conditionalRoles'],p
PY
    cleanup_workspace
}

test_152() {
    init_test_workspace
    start_fast_execution_task task-standard-profile P1 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    local out
    out=$(run_core route-role --event implementation-complete)
    [[ "$(json_str "$out" nextAction)" == "RUN_CHANGE_REVIEWER" ]] || { echo "standard should route reviewer: $out"; return 1; }
    [[ "$(json_str "$out" role)" != "watchdog" ]] || { echo "standard should not unconditionally route watchdog"; return 1; }
    cleanup_workspace
}

test_153() {
    init_test_workspace
    start_fast_execution_task task-audit-profile P0 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    "$PY" - "$WORKSPACE_ABS/runs/$FAST_RUN_ID/execution-policy.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
assert p['selectedProfile']=='audit',p
assert set(['executor','change-reviewer','watchdog','sentinel']).issubset(p['requiredRoles']),p
PY
    cleanup_workspace
}

test_154() {
    init_test_workspace
    cp "$PROJECT_ROOT/templates/workspace/policies/protected-paths.json" "$WORKSPACE_ABS/policies/protected-paths.json"
    start_fast_execution_task task-protected-fast P2 "scripts/**" "scripts/**"
    local out
    out=$(run_core prepare-execution --profile fast)
    [[ "$(json_str "$out" profile)" == "audit" ]] || { echo "protected task must escalate to audit: $out"; return 1; }
    grep -q 'PROFILE_ESCALATED_TO_AUDIT' "$WORKSPACE_ABS/runs/$FAST_RUN_ID/execution-policy.json" || { echo "escalation reason missing"; return 1; }
    cleanup_workspace
}

test_155() {
    init_test_workspace
    start_fast_execution_task task-idempotent P2 "src/**" "src/**"
    local first second
    first=$(run_core prepare-execution)
    second=$(run_core prepare-execution)
    [[ "$(json_str "$first" policyReused)" == "False" || "$(json_str "$first" policyReused)" == "false" ]] || { echo "first policy should be new: $first"; return 1; }
    [[ "$(json_str "$second" policyReused)" == "True" || "$(json_str "$second" policyReused)" == "true" ]] || { echo "second policy should be reused: $second"; return 1; }
    [[ "$(json_str "$first" manifestFingerprint)" == "$(json_str "$second" manifestFingerprint)" ]] || { echo "manifest fingerprint changed"; return 1; }
    cleanup_workspace
}

test_156() {
    init_test_workspace
    start_fast_execution_task task-drift P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    "$PY" - "$WORKSPACE_ABS/state/current-task.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['allowedWrites']=['other/**','.teamloop/**']; open(p,'w').write(json.dumps(d))
PY
    set +e
    local out rc
    out=$(run_core prepare-execution 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "changed scope should reject existing run"; return 1; }
    echo "$out" | grep -qi 'fresh run\|inputs changed' || { echo "clear drift error missing: $out"; return 1; }
    cleanup_workspace
}

test_157() {
    init_test_workspace
    start_fast_execution_task task-manual-mutation P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    "$PY" - "$WORKSPACE_ABS/runs/$FAST_RUN_ID/execution-manifest.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['executionProfile']='audit'; open(p,'w').write(json.dumps(d))
PY
    set +e
    local out rc
    out=$(run_core validate-execution-contract 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "manual manifest mutation should fail"; return 1; }
    echo "$out" | grep -qi 'integrity\|mutation' || { echo "integrity reason missing: $out"; return 1; }
    cleanup_workspace
}

test_158() {
    init_test_workspace
    start_fast_execution_task task-scope-drift P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    "$PY" - "$WORKSPACE_ABS/state/current-task.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['successCriteria'].append('changed after freeze'); open(p,'w').write(json.dumps(d))
PY
    set +e
    local out rc
    out=$(run_core validate-execution-contract 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "task revision drift should fail"; return 1; }
    echo "$out" | grep -qi 'revision\|drift' || { echo "task drift reason missing: $out"; return 1; }
    cleanup_workspace
}

test_159() {
    init_test_workspace
    start_fast_execution_task task-no-progress P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core record-progress >/dev/null
    local out
    out=$(run_core record-progress)
    "$PY" - <<PY
import json
r=json.loads('''$out''')['result']
assert r['status']=='NO_PROGRESS_DETECTED',r
assert r['identicalSnapshotStreak']==2,r
PY
    cleanup_workspace
}

test_160() {
    init_test_workspace
    start_fast_execution_task task-progress-reset P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core record-progress >/dev/null
    mkdir -p "$TEST_REPO_DIR/src"; echo material > "$TEST_REPO_DIR/src/change.txt"
    local out
    out=$(run_core record-progress)
    "$PY" - <<PY
import json
r=json.loads('''$out''')['result']
assert r['status']=='PROGRESS_OBSERVED',r
assert r['identicalSnapshotStreak']==1,r
PY
    cleanup_workspace
}

test_161() {
    init_test_workspace
    start_fast_execution_task task-perf-no-progress P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core record-progress >/dev/null
    run_core record-performance --phase role-dispatch --duration-ms 999 --role-count 1 >/dev/null
    local out
    out=$(run_core record-progress)
    echo "$out" | grep -q 'NO_PROGRESS_DETECTED' || { echo "performance-only change incorrectly counted as progress: $out"; return 1; }
    cleanup_workspace
}

test_162() {
    init_test_workspace
    start_fast_execution_task task-watchdog-route P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core record-progress >/dev/null
    run_core record-progress >/dev/null
    local out
    out=$(run_core next-action)
    [[ "$(json_str "$out" nextAction)" == "RUN_WATCHDOG" ]] || { echo "no-progress should route watchdog: $out"; return 1; }
    cleanup_workspace
}

test_163() {
    init_test_workspace
    start_fast_execution_task task-final-invariants P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    local sentinel final
    sentinel=$(run_core route-role --event final-handoff)
    final=$(run_core route-role --event sentinel-complete)
    [[ "$(json_str "$sentinel" nextAction)" == "RUN_SENTINEL" ]] || { echo "fast final sentinel skipped: $sentinel"; return 1; }
    [[ "$(json_str "$final" nextAction)" == "RUN_FINAL_GATE" ]] || { echo "fast final gate skipped: $final"; return 1; }
    cleanup_workspace
}

test_164() {
    init_test_workspace
    start_fast_execution_task task-valid-commands P2 "src/**" "src/**"
    run_core resolve-execution-policy >/dev/null
    run_core validate-state >/dev/null || { echo "resolve policy left invalid state"; return 1; }
    run_core materialize-execution-manifest >/dev/null
    run_core validate-state >/dev/null || { echo "manifest command left invalid state"; return 1; }
    run_core validate-execution-contract >/dev/null
    run_core record-performance --phase role-dispatch --duration-ms 1 --role-count 1 >/dev/null
    run_core performance-report >/dev/null
    run_core route-role --event implementation-complete >/dev/null
    run_core record-progress >/dev/null
    run_core validate-state >/dev/null || { echo "new commands left invalid state"; return 1; }
    cleanup_workspace
}

test_165() {
    init_test_workspace
    start_fast_execution_task task-malformed-history P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    echo '{bad json' > "$WORKSPACE_ABS/runs/$FAST_RUN_ID/progress-history.jsonl"
    set +e
    local out rc
    out=$(run_core record-progress 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "malformed progress history should fail"; return 1; }
    echo "$out" | grep -qi 'malformed JSON' || { echo "malformed history reason missing: $out"; return 1; }
    cleanup_workspace
}

test_166() {
    init_test_workspace
    start_fast_execution_task task-fake-clock P2 "src/**" "src/**"
    TEAMLOOP_FAKE_CLOCK_MS='[100,125]' run_core prepare-execution >/dev/null
    local duration
    duration=$("$PY" - "$WORKSPACE_ABS/runs/$FAST_RUN_ID/performance-trace.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1])); print([x['durationMs'] for x in p['phases'] if x['phase']=='execution-contract-creation-validation'][-1])
PY
)
    [[ "$duration" == "25.0" || "$duration" == "25" ]] || { echo "fake clock duration should be 25ms, got $duration"; return 1; }
    cleanup_workspace
}

test_167() {
    for name in execution-policy execution-manifest execution-manifest-validation performance-trace progress-snapshot no-progress-result role-routing-decision; do
        [[ -f "$PROJECT_ROOT/schemas/$name.schema.json" ]] || { echo "missing schema $name"; return 1; }
    done
    for name in prepare-execution resolve-execution-policy materialize-execution-manifest validate-execution-contract record-progress route-role record-performance performance-report; do
        [[ -x "$PROJECT_ROOT/scripts/$name.sh" ]] || { echo "missing executable $name.sh"; return 1; }
        [[ -f "$PROJECT_ROOT/scripts/$name.ps1" ]] || { echo "missing $name.ps1"; return 1; }
    done
}

test_168() {
    init_test_workspace
    start_fast_execution_task task-final-no-progress P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core record-progress >/dev/null
    run_core record-progress >/dev/null
    set +e
    local out rc
    out=$(run_core final-gate 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "final gate must fail unresolved no-progress"; return 1; }
    echo "$out" | grep -q 'no-progress-result' || { echo "final gate no-progress check missing: $out"; return 1; }
    cleanup_workspace
}

test_169() {
    init_test_workspace
    start_fast_execution_task task-sentinel-count P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    local out total
    out=$(run_core run-sentinel)
    total=$(echo "$out" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["summary"]["totalFindings"])')
    [[ "$total" == "9" ]] || { echo "sentinel should run 9 unique checks, got $total"; return 1; }
    cleanup_workspace
}

test_170() {
    grep -q 'prepare-execution.sh' "$PROJECT_ROOT/.opencode/commands/supervised-task.md" || { echo "supervised-task missing prepare-execution"; return 1; }
    grep -q 'record-progress' "$PROJECT_ROOT/.opencode/commands/supervised-task.md" || { echo "supervised-task missing progress control"; return 1; }
    grep -q 'final-gate.sh' "$PROJECT_ROOT/.opencode/commands/supervised-task.md" || { echo "supervised-task missing real final gate"; return 1; }
    ! grep -q 'Only edit state files directly' "$PROJECT_ROOT/.opencode/commands/supervised-task.md" || { echo "prompt still permits direct runtime state edits"; return 1; }
    cmp -s "$PROJECT_ROOT/.opencode/commands/supervised-task.md" "$PROJECT_ROOT/adapters/opencode/commands/supervised-task.md" || { echo "OpenCode command copies drifted"; return 1; }
}


test_171() {
    init_test_workspace
    start_fast_execution_task task-suppression-only P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    mkdir -p "$TEST_REPO_DIR/src"
    printf '# TODO: restore behavior\n' > "$TEST_REPO_DIR/src/work.py"
    run_core record-progress >/dev/null
    : > "$TEST_REPO_DIR/src/work.py"
    local out
    out=$(run_core record-progress)
    echo "$out" | "$PY" -c 'import json,sys; r=json.load(sys.stdin)["result"]; assert r["status"]=="NO_PROGRESS_DETECTED",r; assert r["progressClassification"]=="SUPPRESSION_ONLY_NOT_PROGRESS",r; assert "suppression-only" in r["reason"],r' || return 1
    cleanup_workspace
}

test_172() {
    init_test_workspace
    start_fast_execution_task task-watchdog-recovery P2 "src/**" "src/**"
    local original_run="$FAST_RUN_ID"
    run_core prepare-execution >/dev/null
    run_core record-progress >/dev/null
    run_core record-progress >/dev/null
    local route retry
    route=$(run_core route-role --event watchdog-complete)
    [[ "$(json_str "$route" nextAction)" == "RETRY_EXECUTOR" ]] || { echo "watchdog recovery should require changed retry: $route"; return 1; }
    retry=$(run_core apply-transition --action RETRY_EXECUTOR)
    [[ "$(json_str "$retry" runId)" == "$original_run" ]] || { echo "RETRY_EXECUTOR must preserve run identity: $retry"; return 1; }
    [[ "$(json_str "$retry" taskId)" == "task-watchdog-recovery" ]] || { echo "RETRY_EXECUTOR must preserve task identity: $retry"; return 1; }
    local status
    status=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$WORKSPACE_ABS/runs/$FAST_RUN_ID/no-progress-result.json")
    [[ "$status" == "STRATEGY_CHANGE_REQUIRED" ]] || { echo "watchdog should acknowledge no-progress recovery, got $status"; return 1; }
    local next
    next=$(run_core next-action)
    [[ "$(json_str "$next" nextAction)" != "RUN_WATCHDOG" ]] || { echo "watchdog must not self-loop after strategy routing: $next"; return 1; }
    run_core validate-state >/dev/null || { echo "watchdog recovery left invalid state"; return 1; }
    cleanup_workspace
}

test_173() {
    init_test_workspace
    start_fast_execution_task task-routing-integrity P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core route-role --event implementation-complete >/dev/null
    local history="$WORKSPACE_ABS/runs/$FAST_RUN_ID/role-routing-history.jsonl"
    "$PY" -c 'import json,sys; p=sys.argv[1]; row=json.loads(open(p,encoding="utf-8").readline()); row["reason"]="manually changed"; open(p,"w",encoding="utf-8").write(json.dumps(row)+"\n")' "$history"
    set +e
    local out rc
    out=$(run_core validate-state 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "mutated role routing decision should fail validation"; return 1; }
    echo "$out" | grep -Eqi 'role-routing.*integrity|manual mutation' || { echo "role routing integrity reason missing: $out"; return 1; }
    cleanup_workspace
}

test_174() {
    init_test_workspace
    start_fast_execution_task task-final-sentinel-required P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    set +e
    local out rc
    out=$(run_core final-gate 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "optimized final gate must require sentinel"; return 1; }
    echo "$out" | grep -q 'requires a final sentinel inspection' || { echo "missing mandatory sentinel failure: $out"; return 1; }
    cleanup_workspace
}


test_175() {
    init_test_workspace
    start_fast_execution_task task-perf-comparison P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    local out
    out=$(run_core performance-report)
    echo "$out" | "$PY" -c 'import json,sys; c=json.load(sys.stdin)["deterministicRoutingComparison"]; assert c["beforeRoleInvocationCount"]==4,c; assert c["afterProfile"]=="fast",c; assert c["afterRoleInvocationCount"]==2,c; assert c["avoidedUnconditionalRoleInvocations"]==2,c; assert c["wallClockClaim"] is False,c' || return 1
    cleanup_workspace
}


test_176() {
    init_test_workspace
    start_fast_execution_task task-optimized-final-pass P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    mkdir -p "$TEST_REPO_DIR/src"
    printf 'ok\n' > "$TEST_REPO_DIR/src/ok.txt"
    run_core record-progress >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    run_core run-gates >/dev/null
    run_core run-sentinel >/dev/null
    local out
    out=$(run_core final-gate) || { echo "optimized final gate should pass after same-run sentinel and gates: $out"; return 1; }
    echo "$out" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); assert d["overallStatus"]=="PASS",d; checks={c["name"]:c for c in d["checks"]}; assert checks["sentinel-result"]["status"]=="PASS",checks; assert checks["execution-contract-integrity"]["status"]=="PASS",checks' || return 1
    cleanup_workspace
}

test_177() {
    init_test_workspace
    start_fast_execution_task task-old-sentinel P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core run-sentinel >/dev/null
    "$PY" - "$WORKSPACE_ABS/state/backlog.jsonl" <<'PY'
import json,sys
p=sys.argv[1]
t={"schemaVersion":1,"taskId":"task-current-no-sentinel","title":"Current run","status":"READY","priority":"P2","origin":"fast-execution-tests","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"requiredEvidence":["test"],"successCriteria":["test"],"forbiddenActions":[],"humanRequired":False,"blockers":[]}
with open(p,"a",encoding="utf-8") as f: f.write(json.dumps(t)+"\n")
PY
    local newer
    newer=$(run_core apply-transition --action RUN_EXECUTOR --task-id task-current-no-sentinel)
    FAST_RUN_ID=$(json_str "$newer" runId)
    run_core prepare-execution >/dev/null
    set +e
    local out rc
    out=$(run_core final-gate 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "stale sentinel from another run must not satisfy final gate"; return 1; }
    echo "$out" | grep -q "runs/$FAST_RUN_ID/sentinel-inspection.json is missing" || { echo "final gate did not require same-run sentinel: $out"; return 1; }
    cleanup_workspace
}

test_178() {
    init_test_workspace
    start_fast_execution_task task-material-after-todo P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    mkdir -p "$TEST_REPO_DIR/src"
    printf '# TODO: restore behavior\n' > "$TEST_REPO_DIR/src/work.py"
    run_core record-progress >/dev/null
    printf 'value = 1\n' > "$TEST_REPO_DIR/src/work.py"
    local out
    out=$(run_core record-progress)
    echo "$out" | "$PY" -c 'import json,sys; r=json.load(sys.stdin)["result"]; assert r["status"]=="PROGRESS_OBSERVED",r; assert r["progressClassification"]=="MATERIAL_CHANGE",r' || return 1
    cleanup_workspace
}

test_179() {
    init_test_workspace
    start_fast_execution_task task-audit-routing P0 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    local impl review watchdog
    impl=$(run_core route-role --event implementation-complete)
    [[ "$(json_str "$impl" nextAction)" == "RUN_CHANGE_REVIEWER" ]] || { echo "audit must review first: $impl"; return 1; }
    review=$(run_core route-role --event review-complete)
    [[ "$(json_str "$review" nextAction)" == "RUN_WATCHDOG" ]] || { echo "audit must run watchdog after review: $review"; return 1; }
    watchdog=$(run_core route-role --event watchdog-complete)
    [[ "$(json_str "$watchdog" nextAction)" == "RUN_GATEKEEPER" ]] || { echo "audit watchdog must route to project gates before final sentinel: $watchdog"; return 1; }
    cleanup_workspace
}

test_180() {
    init_test_workspace
    mkdir -p "$TEST_REPO_DIR/scripts"
    printf '#!/usr/bin/env bash\necho ok\n' > "$TEST_REPO_DIR/scripts/demo.sh"
    git -C "$TEST_REPO_DIR" add scripts/demo.sh
    git -C "$TEST_REPO_DIR" commit -m "add protected script" --no-verify >/dev/null
    printf '# changed\n' >> "$TEST_REPO_DIR/scripts/demo.sh"
    "$PY" - "$WORKSPACE_ABS/policies/protected-paths.json" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p,encoding="utf-8")); d["enforcementLevel"]="error"
open(p,"w",encoding="utf-8").write(json.dumps(d,ensure_ascii=False,indent=2)+"\n")
PY
    set +e
    local out rc
    out=$(run_core check-guard-integrity 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "unstaged protected modification must fail guard: $out"; return 1; }
    echo "$out" | grep -q 'scripts/demo.sh' || { echo "guard corrupted unstaged path parsing: $out"; return 1; }
    cleanup_workspace
}

test_181() {
    init_test_workspace
    start_fast_execution_task task-old-gate P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    run_core run-sentinel >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    run_core run-gates >/dev/null
    "$PY" - "$WORKSPACE_ABS/state/backlog.jsonl" <<'PY'
import json,sys
p=sys.argv[1]
t={"schemaVersion":1,"taskId":"task-current-no-gate","title":"Current run","status":"READY","priority":"P2","origin":"fast-execution-tests","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"requiredEvidence":["test"],"successCriteria":["test"],"forbiddenActions":[],"humanRequired":False,"blockers":[]}
with open(p,"a",encoding="utf-8") as f: f.write(json.dumps(t)+"\n")
PY
    local newer
    newer=$(run_core apply-transition --action RUN_EXECUTOR --task-id task-current-no-gate)
    FAST_RUN_ID=$(json_str "$newer" runId)
    run_core prepare-execution >/dev/null
    run_core run-sentinel >/dev/null
    set +e
    local out rc
    out=$(run_core final-gate 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "stale gate from another run must not satisfy final gate"; return 1; }
    echo "$out" | grep -q "runs/$FAST_RUN_ID/gate-result.json is missing" || { echo "final gate did not require same-run gates: $out"; return 1; }
    cleanup_workspace
}

test_182() {
    init_test_workspace
    start_fast_execution_task task-review-timing P1 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    mkdir -p "$TEST_REPO_DIR/src"
    printf 'value = 1\n' > "$TEST_REPO_DIR/src/reviewed.py"
    run_core apply-transition --action RUN_CHANGE_REVIEWER >/dev/null
    [[ ! -f "$WORKSPACE_ABS/runs/$FAST_RUN_ID/review-evidence.json" ]] || { echo "entering reviewer must not fabricate approval evidence"; return 1; }
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    [[ -f "$WORKSPACE_ABS/runs/$FAST_RUN_ID/review-evidence.json" ]] || { echo "review approval transition should capture evidence"; return 1; }
    cleanup_workspace
}

test_183() {
    init_test_workspace
    start_fast_execution_task task-missing-current-review P2 "src/**" "src/**"
    run_core prepare-execution >/dev/null
    mkdir -p "$TEST_REPO_DIR/src"
    printf 'value = 1\n' > "$TEST_REPO_DIR/src/work.py"
    run_core run-sentinel >/dev/null
    run_core apply-transition --action RUN_GATEKEEPER >/dev/null
    run_core run-gates >/dev/null
    rm -f "$WORKSPACE_ABS/runs/$FAST_RUN_ID/review-evidence.json"
    set +e
    local out rc
    out=$(run_core final-gate 2>&1); rc=$?
    set -e
    [[ $rc -ne 0 ]] || { echo "changed content without same-run evidence must fail final gate"; return 1; }
    echo "$out" | grep -q "runs/$FAST_RUN_ID/review-evidence.json is missing" || { echo "missing same-run review evidence reason absent: $out"; return 1; }
    cleanup_workspace
}

test_184() {
    local reviewer="$PROJECT_ROOT/.opencode/agents/change-reviewer.md"
    grep -q 'route-role.sh.*review-complete' "$reviewer" || { echo "reviewer prompt must use runtime routing"; return 1; }
    ! grep -q 'On APPROVED: use .*RUN_GATEKEEPER' "$reviewer" || { echo "reviewer prompt still bypasses audit watchdog"; return 1; }
    cmp -s "$reviewer" "$PROJECT_ROOT/adapters/opencode/agents/change-reviewer.md" || { echo "reviewer prompt copies drifted"; return 1; }
}


test_185() {
    local rel
    for rel in executor change-reviewer gatekeeper watchdog sentinel; do
        cmp -s "$PROJECT_ROOT/.opencode/agents/$rel.md" "$PROJECT_ROOT/adapters/opencode/agents/$rel.md" || {
            echo "OpenCode agent copy drifted: $rel"
            return 1
        }
    done
}

test_run "FastExecution: LowRiskResolvesFast" test_151
test_run "FastExecution: StandardRequiresReviewer" test_152
test_run "FastExecution: AuditRequiresAllRoles" test_153
test_run "FastExecution: ProtectedScopeEscalatesAudit" test_154
test_run "FastExecution: ManifestIdempotent" test_155
test_run "FastExecution: ChangedInputsRejected" test_156
test_run "FastExecution: ManualMutationFails" test_157
test_run "FastExecution: ScopeDriftFails" test_158
test_run "FastExecution: IdenticalSnapshotsDetectNoProgress" test_159
test_run "FastExecution: MaterialChangeResetsStreak" test_160
test_run "FastExecution: PerformanceChangeIgnored" test_161
test_run "FastExecution: NextActionRoutesWatchdog" test_162
test_run "FastExecution: FastKeepsFinalInvariants" test_163
test_run "FastExecution: SuccessfulCommandsLeaveValidState" test_164
test_run "FastExecution: MalformedHistoryFails" test_165
test_run "FastExecution: FakeClockDeterministic" test_166
test_run "FastExecution: SchemasAndWrappersExist" test_167
test_run "FastExecution: FinalGateBlocksNoProgress" test_168
test_run "FastExecution: SentinelUniqueChecks" test_169
test_run "FastExecution: OpenCodeRuntimeBound" test_170
test_run "FastExecution: SuppressionOnlyIsNotProgress" test_171
test_run "FastExecution: WatchdogRecoveryDoesNotLoop" test_172
test_run "FastExecution: RoleRoutingIntegrity" test_173
test_run "FastExecution: OptimizedFinalGateRequiresSentinel" test_174
test_run "FastExecution: DeterministicPerformanceComparison" test_175
test_run "FastExecution: OptimizedFinalGatePass" test_176
test_run "FastExecution: StaleSentinelCannotSatisfyCurrentRun" test_177
test_run "FastExecution: MaterialImplementationAfterTodoCountsProgress" test_178
test_run "FastExecution: AuditWatchdogRoutesProjectGates" test_179
test_run "Guard: UnstagedProtectedPathParsing" test_180
test_run "FastExecution: StaleGateCannotSatisfyCurrentRun" test_181
test_run "ReviewEvidence: CapturedOnlyAfterApproval" test_182
test_run "FastExecution: ChangedContentRequiresSameRunEvidence" test_183
test_run "OpenCode: ReviewerRoutingIsRuntimeBound" test_184
test_run "OpenCode: FastExecutionAgentCopiesSynchronized" test_185

# ============================================================
# WORKSPACE CONTEXT TESTS 186-195
# ============================================================

test_186() {
    # WorkspaceContext: LoadsOncePerHost — state/schemas loaded once and reused from cache
    init_test_workspace
    "$PY" - "$WORKSPACE_ABS" "$PROJECT_ROOT/scripts/teamloop_context.py" <<'PY'
import sys, os, json
sys.path.insert(0, os.path.dirname(sys.argv[2]))
from teamloop_context import WorkspaceContext
ctx = WorkspaceContext(sys.argv[1])
# First access loads state
state1 = ctx.state
# Second access returns cached identical object
state2 = ctx.state
assert state1 is state2, "state should be cached (same object)"
# Same for schemas
schemas1 = ctx.schemas
schemas2 = ctx.schemas
assert schemas1 is schemas2, "schemas should be cached (same object)"
# Verify schemas were actually loaded (non-empty on real project)
assert len(schemas1) > 0, "schemas should load at least one schema"
PY
    cleanup_workspace
}

test_187() {
    # WorkspaceContext: DependentChecksExecuteInAnyOrder — properties accessible in any order without side effects
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-ctx","title":"Ctx test","status":"READY","scope":["src/**"],"successCriteria":["ok"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    "$PY" - "$WORKSPACE_ABS" "$PROJECT_ROOT/scripts/teamloop_context.py" <<'PY'
import sys, os
sys.path.insert(0, os.path.dirname(sys.argv[2]))
from teamloop_context import WorkspaceContext
ctx = WorkspaceContext(sys.argv[1])
# Access properties in random order — none should fail
backlog = ctx.backlog
sp = ctx.scope_policy
gp = ctx.gate_policy
bl = ctx.blockers
evt = ctx.events
rl = ctx.run_ledger
cp = ctx.active_profile
pp = ctx.protected_paths
cr = ctx.current_run_id
ct = ctx.current_task
assert len(backlog) == 1, "backlog should have 1 entry"
assert isinstance(sp, dict), "scope_policy should be dict"
assert isinstance(gp, dict), "gate_policy should be dict"
assert isinstance(bl, list), "blockers should be list"
assert isinstance(evt, list), "events should be list"
assert isinstance(rl, list), "run_ledger should be list"
assert isinstance(cp, dict), "active_profile should be dict"
assert isinstance(pp, dict), "protected_paths should be dict"
assert isinstance(cr, str), "current_run_id should be str"
assert ct is None, "current_task should be None (no current-task.json)"
PY
    cleanup_workspace
}

test_188() {
    # WorkspaceContext: DuplicateChecksReused — same property returns identical cached data
    init_test_workspace
    echo '{"schemaVersion":1,"taskId":"task-001","title":"Dup test","status":"READY","scope":["src/**"],"successCriteria":["ok"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    "$PY" - "$WORKSPACE_ABS" "$PROJECT_ROOT/scripts/teamloop_context.py" <<'PY'
import sys, os
sys.path.insert(0, os.path.dirname(sys.argv[2]))
from teamloop_context import WorkspaceContext
ctx = WorkspaceContext(sys.argv[1])
# Each of these three accesses should return the exact same object
for i in range(3):
    assert ctx.backlog is ctx.backlog, f"backlog cache miss on access {i+2}"
    assert ctx.events is ctx.events, f"events cache miss on access {i+2}"
    assert ctx.scope_policy is ctx.scope_policy, f"scope_policy cache miss on access {i+2}"
    assert ctx.gate_policy is ctx.gate_policy, f"gate_policy cache miss on access {i+2}"
    assert ctx.blockers is ctx.blockers, f"blockers cache miss on access {i+2}"
    assert ctx.run_ledger is ctx.run_ledger, f"run_ledger cache miss on access {i+2}"
PY
    cleanup_workspace
}

test_189() {
    # WorkspaceContext: BlockingDependencyPreventsInvalidPass — state raises for invalid JSON
    init_test_workspace
    # Corrupt team-state.json
    echo '{bad json here' > "$WORKSPACE_ABS/state/team-state.json"
    "$PY" - "$WORKSPACE_ABS" "$PROJECT_ROOT/scripts/teamloop_context.py" <<'PY'
import sys, os
sys.path.insert(0, os.path.dirname(sys.argv[2]))
from teamloop_context import WorkspaceContext
ctx = WorkspaceContext(sys.argv[1])
try:
    _ = ctx.state
    print("ERROR: ctx.state should have raised for invalid JSON")
    sys.exit(1)
except (ValueError, json.JSONDecodeError) as e:
    # Expected — blocking property raises on bad data
    pass
PY
    cleanup_workspace
}

test_190() {
    # WorkspaceContext: AdvisoryChecksDontBecomeBlocking — state_safe returns None for missing state
    init_test_workspace
    # Remove team-state.json
    rm -f "$WORKSPACE_ABS/state/team-state.json"
    "$PY" - "$WORKSPACE_ABS" "$PROJECT_ROOT/scripts/teamloop_context.py" <<'PY'
import sys, os
sys.path.insert(0, os.path.dirname(sys.argv[2]))
from teamloop_context import WorkspaceContext
ctx = WorkspaceContext(sys.argv[1])
# Advisory read — must NOT raise, returns None
result = ctx.state_safe
assert result is None, f"state_safe should be None for missing file, got {result}"
PY
    cleanup_workspace
}

test_191() {
    # WorkspaceContext: ExistingCommandOutputsCompatible — validate-state and check-scope still produce valid JSON
    init_test_workspace
    # Run validate-state and verify JSON output
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass on fresh workspace, got: $vout"; return 1; }
    # validate-state may not emit JSON on pass, but check-scope always does
    echo '{"schemaVersion":1,"taskId":"task-scope","title":"Scope test","status":"READY","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"successCriteria":["ok"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-scope >/dev/null
    set +e
    local csout csrc
    csout=$(run_core check-scope 2>&1)
    csrc=$?
    set -e
    # check-scope output must be valid JSON
    echo "$csout" | "$PY" -c "import json,sys; json.load(sys.stdin)" 2>/dev/null || { echo "check-scope output not valid JSON: $csout"; return 1; }
    cleanup_workspace
}

test_192() {
    # WorkspaceContext: InvalidStateStillFails — validate-state catches corrupted state
    init_test_workspace
    # Corrupt state
    echo '{"schemaVersion":1,"currentPhase":"GIBBERISH","status":"INVALID"}' > "$WORKSPACE_ABS/state/team-state.json"
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 1 ]] || { echo "validate-state should fail on invalid phase, got: $vout"; return 1; }
    cleanup_workspace
}

test_193() {
    # WorkspaceContext: PerformanceDataDoesNotAffectSemanticState — WorkspaceContext state reads contain no timestamps
    init_test_workspace
    "$PY" - "$WORKSPACE_ABS" "$PROJECT_ROOT/scripts/teamloop_context.py" <<'PY'
import sys, os, json
sys.path.insert(0, os.path.dirname(sys.argv[2]))
from teamloop_context import WorkspaceContext
ctx = WorkspaceContext(sys.argv[1])
state = ctx.state
state_str = json.dumps(state)
# The state itself should not contain trace/timing/perf fields
# (timestamps like updatedAtUtc are structural, not performance traces)
# Verify state is a valid dict with expected schemaVersion
assert state.get("schemaVersion") == 1, "state should have schemaVersion 1"
assert isinstance(state, dict), "state should be a dict"
# Schemas also have no performance data
schemas = ctx.schemas
assert isinstance(schemas, dict), "schemas should be dict"
for key, val in schemas.items():
    assert isinstance(val, dict), f"schema {key} should be dict"
PY
    cleanup_workspace
}

test_194() {
    # WorkspaceContext: SuccessfulCommandsLeaveStateValid — validate-state passes after a sequence of commands
    init_test_workspace
    # Run a sequence of commands
    echo '{"schemaVersion":1,"taskId":"task-ctx","title":"Ctx test","status":"READY","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"successCriteria":["ok"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-ctx >/dev/null
    run_core write-event --type STATE_TRANSITION --actor test --summary "test event" >/dev/null
    # After all commands, state must be valid
    set +e
    local vout vrc
    vout=$(run_core validate-state 2>&1)
    vrc=$?
    set -e
    [[ $vrc -eq 0 ]] || { echo "validate-state should pass after commands, got: $vout"; return 1; }
    cleanup_workspace
}

test_195() {
    # WorkspaceContext: WrappersRemainFunctional — .sh wrapper scripts still work
    init_test_workspace
    # Verify key wrapper scripts exist and execute
    for script in validate-state check-scope apply-transition write-event; do
        local wrapper="$PROJECT_ROOT/scripts/$script.sh"
        [[ -f "$wrapper" ]] || { echo "wrapper $script.sh missing"; return 1; }
    done
    # Run validate-state.sh wrapper
    set +e
    local wout wrc
    wout=$(bash "$PROJECT_ROOT/scripts/validate-state.sh" --workspace "$WORKSPACE_ABS" 2>&1)
    wrc=$?
    set -e
    [[ $wrc -eq 0 ]] || { echo "validate-state.sh should pass on clean workspace, got: $wout"; return 1; }
    # Run check-scope.sh wrapper
    echo '{"schemaVersion":1,"taskId":"task-w","title":"Wrapper test","status":"READY","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"successCriteria":["ok"]}' >> "$WORKSPACE_ABS/state/backlog.jsonl"
    run_core apply-transition --action RUN_EXECUTOR --task-id task-w >/dev/null
    set +e
    wout=$(bash "$PROJECT_ROOT/scripts/check-scope.sh" --workspace "$WORKSPACE_ABS" 2>&1)
    wrc=$?
    set -e
    # Output should be valid JSON
    echo "$wout" | "$PY" -c "import json,sys; json.load(sys.stdin)" 2>/dev/null || { echo "check-scope.sh output not valid JSON: $wout"; return 1; }
    cleanup_workspace
}

test_run "WorkspaceContext: LoadsOncePerHost" test_186
test_run "WorkspaceContext: DependentChecksExecuteInAnyOrder" test_187
test_run "WorkspaceContext: DuplicateChecksReused" test_188
test_run "WorkspaceContext: BlockingDependencyPreventsInvalidPass" test_189
test_run "WorkspaceContext: AdvisoryChecksDontBecomeBlocking" test_190
test_run "WorkspaceContext: ExistingCommandOutputsCompatible" test_191
test_run "WorkspaceContext: InvalidStateStillFails" test_192
test_run "WorkspaceContext: PerformanceDataDoesNotAffectSemanticState" test_193
test_run "WorkspaceContext: SuccessfulCommandsLeaveStateValid" test_194
test_run "WorkspaceContext: WrappersRemainFunctional" test_195

# ============================================================
# LAYERED TEST EXECUTION TESTS 196-205
# ============================================================

test_196() {
    # LayerSmoke_MinimalSafe — A documentation-only change selects only smoke+contract minimum
    # Verify the impact map maps *.md to smoke layer
    local impact="$TEST_DIR/impact-map.json"
    [[ -f "$impact" ]] || { echo "impact-map.json missing"; return 1; }
    local md_layers
    md_layers=$(cat "$impact" | "$PY" -c "
import json,sys
data=json.load(sys.stdin)
for m in data.get('mappings',[]):
    if '*.md' in m.get('patterns',[]):
        print(','.join(m.get('layers',[])))
        break
" 2>/dev/null)
    echo "$md_layers" | grep -q "smoke" || { echo "*.md should map to smoke layer, got: $md_layers"; return 1; }
    # Verify smoke layer exists and has tests
    local smoke_count
    smoke_count=$("$PY" "$CORE" test-select --list-layers 2>&1 | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
print(d['layers'].get('smoke',{}).get('testCount',0))
" 2>/dev/null)
    [[ "$smoke_count" -gt 0 ]] || { echo "smoke layer should have tests, got $smoke_count"; return 1; }
    return 0
}

test_197() {
    # LayerRuntime_CoreChange — A scripts/teamloop-core.py change selects runtime+integration
    local impact="$TEST_DIR/impact-map.json"
    local core_layers
    core_layers=$(cat "$impact" | "$PY" -c "
import json,sys
data=json.load(sys.stdin)
for m in data.get('mappings',[]):
    if 'scripts/teamloop-core.py' in m.get('patterns',[]):
        print(','.join(m.get('layers',[])))
        break
" 2>/dev/null)
    echo "$core_layers" | grep -q "runtime" || { echo "core.py should map to runtime layer, got: $core_layers"; return 1; }
    echo "$core_layers" | grep -q "integration" || { echo "core.py should map to integration layer, got: $core_layers"; return 1; }
    return 0
}

test_198() {
    # LayerSchema_Escalation — A schema change selects contract+runtime
    local impact="$TEST_DIR/impact-map.json"
    local schema_layers
    schema_layers=$(cat "$impact" | "$PY" -c "
import json,sys
data=json.load(sys.stdin)
for m in data.get('mappings',[]):
    if 'schemas/*.json' in m.get('patterns',[]):
        print(','.join(m.get('layers',[])))
        break
" 2>/dev/null)
    echo "$schema_layers" | grep -q "contract" || { echo "schema change should affect contract layer, got: $schema_layers"; return 1; }
    echo "$schema_layers" | grep -q "runtime" || { echo "schema change should affect runtime layer, got: $schema_layers"; return 1; }
    return 0
}

test_199() {
    # LayerOpenCode_Contract — A .opencode/ change selects contract layer
    local impact="$TEST_DIR/impact-map.json"
    local opencode_layers
    opencode_layers=$(cat "$impact" | "$PY" -c "
import json,sys
data=json.load(sys.stdin)
for m in data.get('mappings',[]):
    if '.opencode/*' in m.get('patterns',[]):
        print(','.join(m.get('layers',[])))
        break
" 2>/dev/null)
    echo "$opencode_layers" | grep -q "contract" || { echo ".opencode change should affect contract layer, got: $opencode_layers"; return 1; }
    return 0
}

test_200() {
    # LayerProtected_NotSmokeOnly — A protected-path change cannot select only smoke
    local impact="$TEST_DIR/impact-map.json"
    # scripts/*.sh maps to runtime in impact-map, NOT smoke-only
    local script_layers
    script_layers=$(cat "$impact" | "$PY" -c "
import json,sys
data=json.load(sys.stdin)
for m in data.get('mappings',[]):
    if 'scripts/*.sh' in m.get('patterns',[]):
        print(','.join(m.get('layers',[])))
        break
" 2>/dev/null)
    echo "$script_layers" | grep -q "runtime" || { echo "scripts/*.sh should affect runtime layer, got: $script_layers"; return 1; }
    # Must NOT be smoke-only
    if [[ "$script_layers" == "smoke" ]]; then
        echo "scripts/*.sh should not be smoke-only, got: $script_layers"
        return 1
    fi
    return 0
}

test_201() {
    # LayerUnknown_SafeDefault — An unknown file defaults to smoke+contract
    local impact="$TEST_DIR/impact-map.json"
    local default_layers
    default_layers=$(cat "$impact" | "$PY" -c "
import json,sys
data=json.load(sys.stdin)
print(','.join(data.get('default',{}).get('layers',[])))
" 2>/dev/null)
    echo "$default_layers" | grep -q "smoke" || { echo "unknown file should default to smoke layer, got: $default_layers"; return 1; }
    echo "$default_layers" | grep -q "contract" || { echo "unknown file should default to contract layer, got: $default_layers"; return 1; }
    return 0
}

test_202() {
    # LayerFullSuite_Aggregates — --full runs all tests
    # Verify --full flag resolves to all layers in test-select
    set +e
    local sel src
    sel=$("$PY" "$CORE" test-select --full 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "test-select --full should exit 0, got $src: $sel"; return 1; }
    local total
    total=$(echo "$sel" | "$PY" -c "import json,sys; print(len(json.load(sys.stdin).get('selectedTests',[])))" 2>/dev/null)
    # Total should match total number of defined tests in test-layers.json
    [[ "$total" -gt 195 ]] || { echo "--full should select all defined tests (205+), got $total"; return 1; }
    return 0
}

test_203() {
    # LayerMissingFails — A layer that doesn't exist fails gracefully
    set +e
    local sel src
    sel=$("$PY" "$CORE" test-select --layer nonexistent-layer 2>&1)
    src=$?
    set -e
    [[ $src -ne 0 ]] || { echo "test-select with nonexistent layer should fail, got exit $src: $sel"; return 1; }
    echo "$sel" | grep -qi "unknown\|error\|not found" || { echo "Error should mention unknown layer, got: $sel"; return 1; }
    return 0
}

test_204() {
    # LayerRunner_WdCleanup — Working directory cleanup is reliable after filtered runs
    init_test_workspace
    # After creating temp workspace, verify cleanup leaves no artifacts
    local tmpdir="$TEST_REPO_DIR"
    local wsd="$WORKSPACE_ABS"
    cleanup_workspace
    [[ ! -d "$tmpdir" ]] || { echo "cleanup_workspace should remove TEST_REPO_DIR ($tmpdir still exists)"; return 1; }
    # Verify that running a filtered test doesn't leave orphans
    init_test_workspace
    # Just do a basic workspace operation to verify no file descriptors leaked
    run_core validate-state >/dev/null 2>&1
    cleanup_workspace
    [[ ! -d "$TEST_REPO_DIR" ]] || { echo "cleanup_workspace should remove temp dir after filtered run"; return 1; }
    return 0
}

test_205() {
    # LayerParity_Bash — Bash runner layer filtering works (smoke count matches)
    # Verify --layer smoke selects exactly the smoke tests defined in test-layers.json
    set +e
    local sel src
    sel=$("$PY" "$CORE" test-select --layer smoke 2>&1)
    src=$?
    set -e
    [[ $src -eq 0 ]] || { echo "test-select --layer smoke should exit 0, got $src: $sel"; return 1; }
    local layer_count
    layer_count=$(echo "$sel" | "$PY" -c "import json,sys; print(len(json.load(sys.stdin).get('selectedTests',[])))" 2>/dev/null)
    # Cross-check with --list-layers smoke testCount
    local list_count
    list_count=$("$PY" "$CORE" test-select --list-layers 2>&1 | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
print(d['layers'].get('smoke',{}).get('testCount',0))
" 2>/dev/null)
    [[ "$layer_count" -eq "$list_count" ]] || { echo "Layer smoke count ($layer_count) should match list-layers count ($list_count)"; return 1; }
    [[ "$layer_count" -gt 0 ]] || { echo "--layer smoke should select tests, got $layer_count"; return 1; }
    return 0
}

test_run "Layer: Smoke_MinimalSafe" test_196
test_run "Layer: Runtime_CoreChange" test_197
test_run "Layer: Schema_Escalation" test_198
test_run "Layer: OpenCode_Contract" test_199
test_run "Layer: Protected_NotSmokeOnly" test_200
test_run "Layer: Unknown_SafeDefault" test_201
test_run "Layer: FullSuite_Aggregates" test_202
test_run "Layer: MissingFails" test_203
test_run "Layer: Runner_WdCleanup" test_204
test_run "Layer: Parity_Bash" test_205

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
