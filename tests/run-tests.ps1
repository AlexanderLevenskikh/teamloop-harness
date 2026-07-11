param(
    [string]$TestWorkspace = ".teamloop-test",
    [string]$Layer,
    [switch]$Affected,
    [switch]$Full,
    [switch]$ListLayers
)

$ErrorActionPreference = "Continue"
[System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$testScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $testScriptDir
$scriptDir = Join-Path $projectRoot "scripts"
$script:total = 0
$script:passed = 0
$script:failed = 0
$script:workspaceAbs = ""
$script:testRepoDir = ""

# ============================================================
# FILTERING STATE
# ============================================================
$script:selectedLayers = @()      # layers to include (empty = all)
$script:autoMode = $false
$script:includeTests = @()        # test numbers from TEAMLOOP_TEST_INCLUDE
$script:selectionActive = $false

# Resolve test selection from flags + env vars
function Resolve-TestSelection {
    if ($ListLayers) {
        & python "$scriptDir/teamloop-core.py" test-select --list-layers
        exit 0
    }

    if ($Full) {
        return # explicit full — no filtering
    }

    if (-not [string]::IsNullOrWhiteSpace($env:TEAMLOOP_TEST_AUTO)) {
        $script:autoMode = $true
        try {
            $selOutput = & python "$scriptDir/teamloop-core.py" test-select --affected 2>$null | Out-String
            $selData = $selOutput | ConvertFrom-Json
            # Extract unique layers from selectedTests
            $layerSet = @{}
            foreach ($t in $selData.selectedTests) {
                foreach ($layer in $t.layers) {
                    $layerSet[$layer] = $true
                }
            }
            $script:selectedLayers = @($layerSet.Keys)
            $script:selectionActive = $true
            Write-Host "[test-select] Auto-selected layers: $($script:selectedLayers -join ', ')" -ForegroundColor Yellow
        } catch {
            Write-Host "[test-select] Failed to resolve affected tests, running all" -ForegroundColor Yellow
        }
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($env:TEAMLOOP_TEST_INCLUDE)) {
        $ids = $env:TEAMLOOP_TEST_INCLUDE -split ',' | ForEach-Object { $_.Trim() }
        foreach ($id in $ids) {
            if ($id -match '^\d+$') {
                $script:includeTests += [int]$id
            }
        }
        if ($script:includeTests.Count -gt 0) {
            $script:selectionActive = $true
            Write-Host "[test-select] Included test(s) via TEAMLOOP_TEST_INCLUDE: $($script:includeTests -join ', ')" -ForegroundColor Yellow
        }
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($Layer)) {
        try {
            $selOutput = & python "$scriptDir/teamloop-core.py" test-select --layer $Layer 2>$null | Out-String
            $selData = $selOutput | ConvertFrom-Json
            # Extract unique layers from selected tests
            $layerSet = @{}
            foreach ($t in $selData.selectedTests) {
                foreach ($l in $t.layers) {
                    $layerSet[$l] = $true
                }
            }
            $script:selectedLayers = @($layerSet.Keys)
            $script:selectionActive = $true
            Write-Host "[test-select] Layer '$Layer' selected layers: $($script:selectedLayers -join ', ')" -ForegroundColor Yellow
        } catch {
            Write-Host "[test-select] Failed to resolve layer '$Layer'" -ForegroundColor Red
            exit 1
        }
        return
    }

    if ($Affected) {
        try {
            $selOutput = & python "$scriptDir/teamloop-core.py" test-select --affected 2>$null | Out-String
            $selData = $selOutput | ConvertFrom-Json
            # Extract unique layers from selectedTests
            $layerSet = @{}
            foreach ($t in $selData.selectedTests) {
                foreach ($layer in $t.layers) {
                    $layerSet[$layer] = $true
                }
            }
            $script:selectedLayers = @($layerSet.Keys)
            $script:selectionActive = $true
            Write-Host "[test-select] Affected selection picked layers: $($script:selectedLayers -join ', ')" -ForegroundColor Yellow
        } catch {
            Write-Host "[test-select] Failed to resolve affected tests" -ForegroundColor Red
            exit 1
        }
        return
    }

    # Default: no filtering, run all tests
}

Resolve-TestSelection

# Check if a test with given layers should run
function Is-TestAllowed {
    param([string[]]$TestLayers)
    if (-not $script:selectionActive) {
        return $true  # no filter active
    }
    if ($script:includeTests.Count -gt 0) {
        # For include mode, check against test number (passed via total counter)
        $testNum = $script:testNumForInclude
        foreach ($id in $script:includeTests) {
            if ($testNum -eq $id) { return $true }
        }
        return $false
    }
    # Layer-based selection: test runs if any of its layers are in selectedLayers
    foreach ($tl in $TestLayers) {
        foreach ($sl in $script:selectedLayers) {
            if ($tl -eq $sl) { return $true }
        }
    }
    return $false
}

function Test-Run {
    param([string]$Name, [string[]]$Layers = @("runtime"), [scriptblock]$ScriptBlock)
    $script:total++
    $script:testNumForInclude = $script:total
    # Layer/affected/include selection filter
    if (-not (Is-TestAllowed -TestLayers $Layers)) {
        return
    }
    Write-Host "`n[$script:total] $Name" -ForegroundColor Cyan
    try {
        $result = & $ScriptBlock
        if ($result -eq $true) {
            $script:passed++
            Write-Host "  PASS" -ForegroundColor Green
        } else {
            $script:failed++
        }
    } catch {
        $script:failed++
        Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Cleanup-Workspace {
    if ($script:workspaceAbs -and (Test-Path $script:workspaceAbs)) {
        Remove-Item -LiteralPath $script:workspaceAbs -Recurse -Force
    }
    if ($script:testRepoDir -and (Test-Path $script:testRepoDir)) {
        Remove-Item -LiteralPath $script:testRepoDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Init-TestWorkspace {
    Cleanup-Workspace
    $tempRepo = Join-Path $env:TEMP "tl-ps-test-$([System.Guid]::NewGuid().ToString('N').Substring(0,8))"
    New-Item -ItemType Directory -Path $tempRepo -Force | Out-Null
    $script:testRepoDir = $tempRepo
    $script:workspaceAbs = Join-Path $tempRepo ".teamloop"
    Set-Location $tempRepo
    & git init 2>$null
    & git config user.email "test@teamloop.local" 2>$null
    & git config user.name "Test" 2>$null
    & python "$scriptDir/teamloop-core.py" init-workspace --workspace "$script:workspaceAbs" --profile "generic-software-task" 2>$null | Out-Null
    & git add . 2>$null
    & git commit -m "init" --no-verify 2>$null
}

function Invoke-PythonScript {
    param([string]$Command, [string[]]$ExtraArgs = @())
    $fullArgs = @($Command) + $ExtraArgs + @("--workspace", $script:workspaceAbs)
    Push-Location $script:testRepoDir
    try {
        return & python "$scriptDir/teamloop-core.py" @fullArgs 2>$null
    } finally {
        Pop-Location
    }
}

function Invoke-PythonScriptWithExit {
    param([string]$Command, [string[]]$ExtraArgs = @())
    $fullArgs = @($Command) + $ExtraArgs + @("--workspace", $script:workspaceAbs)
    Push-Location $script:testRepoDir
    try {
        $output = & python "$scriptDir/teamloop-core.py" @fullArgs 2>$null
        $exitCode = $LASTEXITCODE
        return @{ output = $output; exitCode = $exitCode }
    } finally {
        Pop-Location
    }
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        Write-Host "  FAIL: $Message" -ForegroundColor Red
        return $false
    }
    return $true
}

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -ne $Expected) {
        Write-Host "  FAIL: $Message (expected '$Expected', got '$Actual')" -ForegroundColor Red
        return $false
    }
    return $true
}

function Assert-Match {
    param([string]$Text, [string]$Pattern, [string]$Message)
    if ($Text -notmatch $Pattern) {
        Write-Host "  FAIL: $Message (text: $Text)" -ForegroundColor Red
        return $false
    }
    return $true
}

function Write-JsonFile {
    param([string]$Path, [string]$Content)
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Append-JsonLine {
    param([string]$Path, [string]$Line)
    [System.IO.File]::AppendAllText($Path, $Line + "`n", [System.Text.UTF8Encoding]::new($false))
}

# ============================================================
# P0 TEST 1: REVIEW_FAILED next-action preserves taskId
# ============================================================
Test-Run "P0: REVIEW_FAILED next-action preserves taskId" -Layers @("smoke") {
    Init-TestWorkspace

    $taskJson = '{"schemaVersion":1,"taskId":"task-001","title":"Review test","status":"READY","scope":["src/**"],"successCriteria":["Works"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson

    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-001" | Out-Null
    Invoke-PythonScript "apply-transition" "--action", "RUN_CHANGE_REVIEWER" | Out-Null
    Invoke-PythonScript "apply-transition" "--action", "REQUEST_CHANGES" | Out-Null

    $naResult = Invoke-PythonScript "next-action"
    $naData = $naResult | ConvertFrom-Json

    if (-not (Assert-Equal $naData.taskId "task-001" "REVIEW_FAILED next-action preserves taskId")) { return $false }
    if (-not (Assert-Equal $naData.nextAction "RUN_EXECUTOR" "next-action should be RUN_EXECUTOR")) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# P0 TEST 2: stale current-task.json does NOT grant scope after gate PASS
# ============================================================
Test-Run "P0: stale current-task.json does NOT grant scope after gate PASS" -Layers @("smoke") {
    Init-TestWorkspace

    $taskJson = '{"schemaVersion":1,"taskId":"task-001","title":"Scope test","status":"READY","scope":["src/**"],"successCriteria":["Works"],"allowedWrites":["src/**", ".teamloop/**"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson

    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-001" | Out-Null
    Invoke-PythonScript "apply-transition" "--action", "RUN_GATEKEEPER" | Out-Null

    $gp = '{"gates":[{"name":"ok","type":"shell","command":"cmd /c exit 0","required":true}]}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "policies\gate-policy.json") -Content $gp

    Invoke-PythonScript "run-gates" | Out-Null

    # current-task.json should be removed
    $ctPath = Join-Path $script:workspaceAbs "state\current-task.json"
    if (Test-Path $ctPath) {
        Write-Host "  FAIL: current-task.json should be removed after gate PASS" -ForegroundColor Red
        return $false
    }

    # Create file outside default scope
    $srcDir = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
    $f = Join-Path $srcDir "unauthorized.txt"
    New-Item -ItemType File -Path $f -Force | Out-Null
    Push-Location $script:testRepoDir
    & git add src/unauthorized.txt 2>$null
    Pop-Location

    $csResult = Invoke-PythonScript "check-scope"
    $csData = $csResult | ConvertFrom-Json
    if (-not (Assert-Equal $csData.status "FAIL" ("check-scope should FAIL after task completion, got " + $csData.status))) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# P0 TEST 3: validate-state catches stale current-task.json
# ============================================================
Test-Run "P0: validate-state catches stale current-task.json" -Layers @("smoke") {
    Init-TestWorkspace

    $stale = '{"schemaVersion":1,"taskId":"task-999","title":"Stale","status":"IN_PROGRESS","scope":["src/**"],"successCriteria":["X"]}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "state\current-task.json") -Content $stale

    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on stale current-task, got: " + $valResult.output))) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# P0 TEST 4: CONTINUE_LOOP clears current-task.json
# ============================================================
Test-Run "P0: CONTINUE_LOOP clears current-task.json" -Layers @("smoke") {
    Init-TestWorkspace

    $taskJson = '{"schemaVersion":1,"taskId":"task-001","title":"Continue test","status":"READY","scope":["src/**"],"successCriteria":["Works"],"allowedWrites":["src/**"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson

    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-001" | Out-Null

    $ctPath = Join-Path $script:workspaceAbs "state\current-task.json"
    if (-not (Assert-True (Test-Path $ctPath) "current-task.json should exist during EXECUTING_TASK")) { return $false }

    Invoke-PythonScript "apply-transition" "--action", "CONTINUE_LOOP" | Out-Null

    if (Test-Path $ctPath) {
        Write-Host "  FAIL: current-task.json should be removed after CONTINUE_LOOP" -ForegroundColor Red
        return $false
    }

    $stateContent = Get-Content $ctPath -Raw -ErrorAction SilentlyContinue
    $stateFile = Join-Path $script:workspaceAbs "state\team-state.json"
    $stateData = Get-Content $stateFile -Raw | ConvertFrom-Json
    if (-not (Assert-Equal $stateData.currentTaskId "" ("team-state currentTaskId should be empty after CONTINUE_LOOP, got " + $stateData.currentTaskId))) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# P0 TEST 5: failed gate → validate-state PASS → next-action RUN_EXECUTOR
# ============================================================
Test-Run "P0: failed gate → validate-state PASS → next-action RUN_EXECUTOR" -Layers @("smoke") {
    Init-TestWorkspace

    $taskJson = '{"schemaVersion":1,"taskId":"task-001","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson

    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-001" | Out-Null
    Invoke-PythonScript "apply-transition" "--action", "RUN_GATEKEEPER" | Out-Null

    $gp = '{"gates":[{"name":"always-fail","type":"shell","command":"cmd /c exit 1","required":true}]}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "policies\gate-policy.json") -Content $gp

    $gateResult = Invoke-PythonScriptWithExit "run-gates"
    if (-not (Assert-Equal $gateResult.exitCode 1 "run-gates should fail")) { return $false }

    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass after failed gate, got: " + $valResult.output))) { return $false }

    $naResult = Invoke-PythonScript "next-action"
    $naData = $naResult | ConvertFrom-Json
    if (-not (Assert-Equal $naData.nextAction "RUN_EXECUTOR" ("next-action should be RUN_EXECUTOR, got " + $naData.nextAction))) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# P0 TEST 6: validate-state catches invalid JSON in artifacts
# ============================================================
Test-Run "P0: validate-state catches invalid JSON in artifacts" -Layers @("smoke", "contract") {
    Init-TestWorkspace

    $researchDir = Join-Path $script:workspaceAbs "research"
    New-Item -ItemType Directory -Path $researchDir -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $researchDir "broken.json"), '{bad json content', [System.Text.UTF8Encoding]::new($false))

    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on invalid JSON, got: " + $valResult.output))) { return $false }

    Remove-Item (Join-Path $researchDir "broken.json") -Force
    $valResult2 = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult2.exitCode 0 ("validate-state should pass after cleanup, got: " + $valResult2.output))) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# P1 TEST 7: NEEDS_TASK_SLICING + READY task -> RUN_EXECUTOR
# ============================================================
Test-Run "P1: NEEDS_TASK_SLICING + READY task -> RUN_EXECUTOR" -Layers @("contract") {
    Init-TestWorkspace

    Invoke-PythonScript "apply-transition" "--action", "RUN_TASK_SLICER" | Out-Null

    $taskJson = '{"schemaVersion":1,"taskId":"task-001","title":"Ready","status":"READY","scope":["src/**"],"successCriteria":["ok"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson

    $naResult = Invoke-PythonScript "next-action"
    $naData = $naResult | ConvertFrom-Json

    if (-not (Assert-Equal $naData.nextAction "RUN_EXECUTOR" ("NEEDS_TASK_SLICING with READY task should return RUN_EXECUTOR, got " + $naData.nextAction))) { return $false }
    if (-not (Assert-Equal $naData.taskId "task-001" ("taskId should be task-001, got " + $naData.taskId))) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# P1 TEST 8: NEEDS_TASK_SLICING no READY tasks -> RUN_TASK_SLICER
# ============================================================
Test-Run "P1: NEEDS_TASK_SLICING no READY tasks -> RUN_TASK_SLICER" -Layers @("contract") {
    Init-TestWorkspace

    Invoke-PythonScript "apply-transition" "--action", "RUN_TASK_SLICER" | Out-Null

    $naResult = Invoke-PythonScript "next-action"
    $naData = $naResult | ConvertFrom-Json

    if (-not (Assert-Equal $naData.nextAction "RUN_TASK_SLICER" ("NEEDS_TASK_SLICING without READY tasks should return RUN_TASK_SLICER, got " + $naData.nextAction))) { return $false }

    Cleanup-Workspace
    return $true
}

# ============================================================
# MEMORY REGRESSION TESTS
# ============================================================
Test-Run "Memory: EmptyPasses" -Layers @("runtime") {
    Init-TestWorkspace
    $memDir = Join-Path $script:workspaceAbs "memory"
    if (-not (Test-Path $memDir)) {
        Write-Host "  FAIL: memory directory should exist after init" -ForegroundColor Red
        return $false
    }
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("Fresh workspace with empty memory should validate, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: MalformedJsonlFails" -Layers @("runtime") {
    Init-TestWorkspace
    [System.IO.File]::WriteAllText((Join-Path $script:workspaceAbs "memory\lessons.jsonl"), '{bad json content here', [System.Text.UTF8Encoding]::new($false))
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on malformed memory JSONL, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ActiveWithoutEvidenceFails" -Layers @("runtime") {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on ACTIVE lesson without evidenceIds, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ActiveWithValidEvidencePasses" -Layers @("runtime") {
    Init-TestWorkspace
    $evidence = '{"schemaVersion":1,"evidenceId":"evidence-001","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z"}'
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-001"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\evidence-map.jsonl") -Content $evidence
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass with ACTIVE lesson + valid evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ActiveWithMissingEvidenceIdFails" -Layers @("runtime") {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-missing"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on ACTIVE lesson referencing missing evidenceId, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: DeprecatedRetainedButInactive" -Layers @("runtime") {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-depr","title":"Old lesson","description":"Deprecated","status":"DEPRECATED","createdAtUtc":"2024-01-01T00:00:00Z","deprecatedAtUtc":"2024-06-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass for DEPRECATED lesson without evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: SupersededWithoutEvidencePasses" -Layers @("runtime") {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-sup","title":"Superseded","description":"Old way","status":"SUPERSEDED","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass for SUPERSEDED lesson without evidence or supersededBy, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: RejectedAntipatternWithoutEvidencePasses" -Layers @("runtime") {
    Init-TestWorkspace
    $anti = '{"schemaVersion":1,"antipatternId":"antipattern-001","title":"Old anti","description":"Rejected","status":"REJECTED","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\antipatterns.jsonl") -Content $anti
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass for REJECTED antipattern without evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: MissingMemoryDirPasses" -Layers @("runtime") {
    Init-TestWorkspace
    Remove-Item (Join-Path $script:workspaceAbs "memory") -Recurse -Force
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass even if memory dir missing, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ProfileValidation" -Layers @("contract", "runtime") {
    Init-TestWorkspace
    $pp = '{"schemaVersion":1,"workspace":".teamloop","memoryVersion":"1","invalidField":"bad"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\project-profile.json") -Content $pp
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on project-profile with invalid field, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: DoctorEmptyPasses" -Layers @("runtime") {
    Init-TestWorkspace
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult.exitCode 0 ("memory-doctor should exit 0 on empty memory, got: " + $doctorResult.output))) { return $false }
    $doctorJson = $doctorResult.output | ConvertFrom-Json
    if (-not (Assert-Equal $doctorJson.status "PASS" ("memory-doctor output should contain PASS, got " + $doctorJson.status))) { return $false }
    if (-not (Assert-True ($doctorJson.PSObject.Properties.Name -contains "checks") "memory-doctor output should contain checks array")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: DoctorDetectsIssues" -Layers @("runtime") {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult.exitCode 1 ("memory-doctor should exit 1 when issues exist, got: " + $doctorResult.output))) { return $false }
    $doctorJson = $doctorResult.output | ConvertFrom-Json
    if (-not (Assert-Equal $doctorJson.status "FAIL" ("memory-doctor output should contain FAIL, got " + $doctorJson.status))) { return $false }
    if (-not (Assert-True ($doctorJson.PSObject.Properties.Name -contains "checks") "memory-doctor output should contain checks array")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ActiveWithUnverifiedEvidenceFails" -Layers @("runtime") {
    Init-TestWorkspace
    $evidence = '{"schemaVersion":1,"evidenceId":"evidence-001","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z","status":"UNVERIFIED"}'
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-001"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\evidence-map.jsonl") -Content $evidence
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on ACTIVE lesson with UNVERIFIED evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

# ============================================================
# NEW REGRESSION TESTS (PowerShell)
# ============================================================
Test-Run "WriteEvent: InvalidTypeRejected" -Layers @("contract") {
    Init-TestWorkspace
    $eventsFile = Join-Path $script:workspaceAbs "state\events.jsonl"
    $linesBefore = (Get-Content $eventsFile).Count
    $result = Invoke-PythonScriptWithExit "write-event" "--type", "INVALID_TYPE", "--actor", "test", "--summary", "Should fail"
    if (-not (Assert-Equal $result.exitCode 1 ("write-event with INVALID_TYPE should exit 1, got " + $result.exitCode))) { return $false }
    $linesAfter = (Get-Content $eventsFile).Count
    if (-not (Assert-Equal $linesAfter $linesBefore ("events.jsonl should be unchanged, before=$linesBefore after=$linesAfter"))) { return $false }
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should still pass after rejected write-event, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ActiveWithUnverifiedEvidenceFailsSchemaValid" -Layers @("contract", "runtime") {
    Init-TestWorkspace
    $evidence = '{"schemaVersion":1,"evidenceId":"evidence-001","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z","status":"UNVERIFIED"}'
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-001"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\evidence-map.jsonl") -Content $evidence
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on UNVERIFIED evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: SupersededByFailsBothValidateStateAndMemoryDoctor" -Layers @("runtime") {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-sup","title":"Superseded","description":"Old way","status":"SUPERSEDED","createdAtUtc":"2024-01-01T00:00:00Z","supersededBy":"lesson-nonexistent"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on orphaned supersededBy, got: " + $valResult.output))) { return $false }
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult.exitCode 1 ("memory-doctor should fail on orphaned supersededBy, got: " + $doctorResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "MemoryDoctor: MissingDirectoryFails" -Layers @("runtime") {
    Init-TestWorkspace
    Remove-Item (Join-Path $script:workspaceAbs "memory") -Recurse -Force
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult.exitCode 1 ("memory-doctor should exit 1 when memory dir missing, got: " + $doctorResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "MemoryDoctor: EmptySubsystemWarns" -Layers @("runtime") {
    Init-TestWorkspace
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    $doctorOutput = $doctorResult.output -join "`n"
    if (-not (Assert-Match $doctorOutput "WARNING" "memory-doctor should report WARNING for empty subsystem")) { return $false }
    if (-not (Assert-Equal $doctorResult.exitCode 0 ("memory-doctor should exit 0 for WARNING-level finding, got: " + $doctorOutput))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ProfileDeprecatedFieldsRejected" -Layers @("contract") {
    Init-TestWorkspace
    $pp = '{"schemaVersion":1,"workspace":".teamloop","memoryVersion":"1","activeGuidanceRequiresEvidence":true,"maxActiveLessons":5}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\project-profile.json") -Content $pp
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on project-profile with deprecated fields, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

# ============================================================
# CONTINUATION-DECISION TESTS (PowerShell)
# ============================================================
Test-Run "ContinuationDecision: ValidDecisionWrite" -Layers @("contract") {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "write-continuation-decision" "--decision", "SAFE_CHECKPOINT", "--phase", "EXECUTING_TASK"
    if (-not (Assert-Equal $result.exitCode 0 ("write-continuation-decision SAFE_CHECKPOINT should exit 0, got: " + $result.exitCode))) { return $false }
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (-not (Assert-True (Test-Path $cdPath) "continuation-decision.json should be created")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: InvalidDecisionRejected" -Layers @("contract") {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "write-continuation-decision" "--decision", "INVALID", "--phase", "EXECUTING_TASK"
    if (-not (Assert-Equal $result.exitCode 1 ("write-continuation-decision with INVALID should exit 1, got: " + $result.exitCode))) { return $false }
    # Note: Invoke-PythonScriptWithExit suppresses stderr, so we only verify the exit code
    # (the writer prints the error message to stderr before exiting)
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: MissingDecisionFilePassesValidation" -Layers @("contract") {
    Init-TestWorkspace
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (Test-Path $cdPath) {
        Write-Host "  FAIL: decision file should not exist on fresh workspace" -ForegroundColor Red
        return $false
    }
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("Missing decision file should pass validate-state, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: DoneRequiresDonePhase" -Layers @("contract") {
    Init-TestWorkspace
    Invoke-PythonScript "write-continuation-decision" "--decision", "DONE", "--phase", "EXECUTING_TASK" | Out-Null
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("DONE decision with EXECUTING_TASK phase should fail, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: WriteThenValidate" -Layers @("contract") {
    Init-TestWorkspace
    Invoke-PythonScript "write-continuation-decision" "--decision", "SAFE_CHECKPOINT", "--phase", "INITIALIZED" | Out-Null
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass after writing valid SAFE_CHECKPOINT, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: SchemaExists" -Layers @("contract") {
    $schemaPath = Join-Path $projectRoot "schemas\continuation-decision.schema.json"
    if (-not (Test-Path $schemaPath)) {
        Write-Host "  FAIL: continuation-decision.schema.json missing" -ForegroundColor Red
        return $false
    }
    try {
        $schemaContent = Get-Content $schemaPath -Raw
        $null = $schemaContent | ConvertFrom-Json
    } catch {
        Write-Host "  FAIL: continuation-decision.schema.json is not valid JSON" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

# ============================================================
# AUTO-DECISION REGRESSION TESTS (PowerShell)
# ============================================================
Test-Run "AutoDecision: SetCheckpointWritesDecision" -Layers @("runtime") {
    Init-TestWorkspace
    Invoke-PythonScript "apply-transition" "--action", "SET_SAFE_CHECKPOINT" | Out-Null
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (-not (Assert-True (Test-Path $cdPath) "SET_SAFE_CHECKPOINT should create continuation-decision.json")) { return $false }
    $cdContent = Get-Content $cdPath -Raw | ConvertFrom-Json
    if (-not (Assert-Equal $cdContent.decision "SAFE_CHECKPOINT" ("decision should be SAFE_CHECKPOINT, got " + $cdContent.decision))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "AutoDecision: TransientSkipsWrite" -Layers @("runtime") {
    Init-TestWorkspace
    $taskJson = '{"schemaVersion":1,"taskId":"task-001","title":"Ready","status":"READY","scope":["src/**"],"successCriteria":["Works"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson
    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-001" | Out-Null
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (Test-Path $cdPath) {
        Write-Host "  FAIL: RUN_EXECUTOR should NOT create continuation-decision.json" -ForegroundColor Red
        return $false
    }
    Invoke-PythonScript "apply-transition" "--action", "RUN_CHANGE_REVIEWER" | Out-Null
    if (Test-Path $cdPath) {
        Write-Host "  FAIL: RUN_CHANGE_REVIEWER should NOT create continuation-decision.json" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "AutoDecision: DecisionFileValidJson" -Layers @("runtime", "contract") {
    Init-TestWorkspace
    Invoke-PythonScript "apply-transition" "--action", "SET_SAFE_CHECKPOINT" | Out-Null
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (-not (Assert-True (Test-Path $cdPath) "decision file should exist")) { return $false }
    try {
        $cdContent = Get-Content $cdPath -Raw | ConvertFrom-Json
        $requiredFields = @("schemaVersion", "decision", "phase", "justification", "checks", "createdAtUtc")
        foreach ($field in $requiredFields) {
            if (-not $cdContent.PSObject.Properties.Name.Contains($field)) {
                Write-Host "  FAIL: decision file missing required field '$field'" -ForegroundColor Red
                return $false
            }
        }
    } catch {
        Write-Host "  FAIL: continuation-decision.json is not valid JSON" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "AutoDecision: SetDoneWritesDoneDecision" -Layers @("runtime") {
    Init-TestWorkspace
    Invoke-PythonScript "apply-transition" "--action", "SET_DONE" | Out-Null
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (-not (Assert-True (Test-Path $cdPath) "SET_DONE should create continuation-decision.json")) { return $false }
    $cdContent = Get-Content $cdPath -Raw | ConvertFrom-Json
    if (-not (Assert-Equal $cdContent.decision "DONE" ("decision should be DONE, got " + $cdContent.decision))) { return $false }
    Cleanup-Workspace
    return $true
}

# ============================================================
# GUARD INTEGRITY REGRESSION TESTS (PowerShell)
# ============================================================
Test-Run "GuardIntegrity: CommandExists" -Layers @("runtime") {
    $helpOut = & python "$scriptDir/teamloop-core.py" --help 2>$null
    if ($helpOut -join "`n" -notmatch "check-guard-integrity") {
        Write-Host "  FAIL: check-guard-integrity should appear in --help" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "GuardIntegrity: MissingPolicyPasses" -Layers @("runtime") {
    Init-TestWorkspace
    Remove-Item (Join-Path $script:workspaceAbs "policies\protected-paths.json") -Force -ErrorAction SilentlyContinue
    $result = Invoke-PythonScriptWithExit "check-guard-integrity"
    if (-not (Assert-Equal $result.exitCode 0 ("check-guard-integrity without policy should exit 0, got: " + $result.exitCode))) { return $false }
    $output = $result.output -join "`n"
    if ($output -notmatch '"status"\s*:\s*"NOT_CONFIGURED"') {
        Write-Host "  FAIL: check-guard-integrity should report NOT_CONFIGURED without policy, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "GuardIntegrity: CleanWorkspacePasses" -Layers @("runtime") {
    Init-TestWorkspace
    # Install policy but no modifications
    $policy = '{"schemaVersion":1,"protectedPaths":["src/**"],"enforcementLevel":"error","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "policies\protected-paths.json") -Content $policy
    $result = Invoke-PythonScriptWithExit "check-guard-integrity"
    if (-not (Assert-Equal $result.exitCode 0 ("check-guard-integrity should pass on clean workspace, got: " + $result.exitCode))) { return $false }
    $output = $result.output -join "`n"
    if ($output -notmatch '"status".*PASS|"status": "PASS"') {
        Write-Host "  FAIL: check-guard-integrity should return PASS on clean workspace, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "GuardIntegrity: WrapperPSExists" -Layers @("contract") {
    $wrapper = Join-Path $scriptDir "check-guard-integrity.ps1"
    if (-not (Test-Path $wrapper)) {
        Write-Host "  FAIL: check-guard-integrity.ps1 wrapper missing" -ForegroundColor Red
        return $false
    }
    $content = Get-Content $wrapper -Raw
    if ($content -notmatch 'check-guard-integrity') {
        Write-Host "  FAIL: Wrapper should invoke check-guard-integrity command" -ForegroundColor Red
        return $false
    }
    if ($content -notmatch 'PSScriptRoot') {
        Write-Host "  FAIL: Wrapper should use PSScriptRoot to locate core script" -ForegroundColor Red
        return $false
    }
    return $true
}

# ============================================================
# SENTINEL REGRESSION TESTS (PowerShell)
# ============================================================
Test-Run "Sentinel: CommandExists" -Layers @("runtime") {
    $helpOut = & python "$scriptDir/teamloop-core.py" --help 2>$null
    if ($helpOut -join "`n" -notmatch "run-sentinel") {
        Write-Host "  FAIL: run-sentinel should appear in --help" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: CleanWorkspacePasses" -Layers @("runtime") {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "run-sentinel"
    if (-not (Assert-Equal $result.exitCode 0 ("run-sentinel should exit 0 on clean workspace, got: " + $result.exitCode))) { return $false }
    $output = $result.output -join "`n"
    $sentinelJson = $output | ConvertFrom-Json
    if (-not (Assert-Equal $sentinelJson.overallStatus "PASS" ("overallStatus should be PASS, got " + $sentinelJson.overallStatus))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: OutputIsValidJson" -Layers @("runtime", "contract") {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "run-sentinel"
    $output = $result.output -join "`n"
    try {
        $data = $output | ConvertFrom-Json
        if (-not $data.schemaVersion) {
            Write-Host "  FAIL: Output JSON missing schemaVersion" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "  FAIL: Sentinel output is not valid JSON: $_" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: OutputMatchesSchema" -Layers @("runtime", "contract") {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "run-sentinel"
    $output = $result.output -join "`n"
    $data = $output | ConvertFrom-Json
    # Verify required top-level fields
    $requiredFields = @("schemaVersion", "runId", "inspectedAtUtc", "findings", "overallStatus", "summary")
    foreach ($field in $requiredFields) {
        if (-not $data.PSObject.Properties.Name.Contains($field)) {
            Write-Host "  FAIL: Output missing required field '$field'" -ForegroundColor Red
            return $false
        }
    }
    # Verify summary fields
    $summaryFields = @("totalFindings", "criticalCount", "warningCount", "infoCount")
    foreach ($field in $summaryFields) {
        if (-not $data.summary.PSObject.Properties.Name.Contains($field)) {
            Write-Host "  FAIL: Summary missing required field '$field'" -ForegroundColor Red
            return $false
        }
    }
    # Verify findings have correct structure
    $findings = $data.findings
    if ($findings.Count -ne 9) {
        Write-Host "  FAIL: Expected 9 findings, got $($findings.Count)" -ForegroundColor Red
        return $false
    }
    foreach ($finding in $findings) {
        $findingRequired = @("category", "severity", "title", "description", "evidence")
        foreach ($field in $findingRequired) {
            if (-not $finding.PSObject.Properties.Name.Contains($field)) {
                Write-Host "  FAIL: Finding missing required field '$field'" -ForegroundColor Red
                return $false
            }
        }
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: WrapperPSExists" -Layers @("contract") {
    $wrapper = Join-Path $scriptDir "run-sentinel.ps1"
    if (-not (Test-Path $wrapper)) {
        Write-Host "  FAIL: run-sentinel.ps1 wrapper missing" -ForegroundColor Red
        return $false
    }
    $content = Get-Content $wrapper -Raw
    if ($content -notmatch 'run-sentinel') {
        Write-Host "  FAIL: Wrapper should invoke run-sentinel command" -ForegroundColor Red
        return $false
    }
    if ($content -notmatch 'PSScriptRoot') {
        Write-Host "  FAIL: Wrapper should use PSScriptRoot to locate core script" -ForegroundColor Red
        return $false
    }
    return $true
}

Test-Run "Sentinel: NineFindings" -Layers @("runtime") {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "run-sentinel"
    $output = $result.output -join "`n"
    $data = $output | ConvertFrom-Json
    if (-not (Assert-Equal $data.findings.Count 9 ("Should have exactly 9 findings, got $($data.findings.Count)"))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: SchemaFileExists" -Layers @("contract") {
    $schemaPath = Join-Path $projectRoot "schemas\sentinel-inspection.schema.json"
    if (-not (Test-Path $schemaPath)) {
        Write-Host "  FAIL: sentinel-inspection.schema.json missing" -ForegroundColor Red
        return $false
    }
    try {
        $schemaContent = Get-Content $schemaPath -Raw
        $null = $schemaContent | ConvertFrom-Json
    } catch {
        Write-Host "  FAIL: sentinel-inspection.schema.json is not valid JSON" -ForegroundColor Red
        return $false
    }
    return $true
}

Test-Run "Sentinel: ValidateStatePasses" -Layers @("runtime") {
    Init-TestWorkspace
    # Run sentinel (creates report with PASS status)
    Invoke-PythonScript "run-sentinel" | Out-Null
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass after clean sentinel run, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

# ============================================================
# E2E SMOKE SCENARIO TESTS
# ============================================================
Test-Run "E2E: SuccessfulBoundedTask" -Layers @("integration") {
    Init-TestWorkspace
    # 1. Create READY task in backlog
    $taskJson = '{"schemaVersion":1,"taskId":"task-e2e-1","title":"E2E task","status":"READY","scope":["src/**"],"allowedWrites":["src/**", ".teamloop/**"],"successCriteria":["src/hello.txt exists"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson
    # 2. Apply RUN_EXECUTOR
    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-e2e-1" | Out-Null
    # 3. Create file in scope
    $srcDir = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $srcDir "hello.txt"), "hello`n", [System.Text.UTF8Encoding]::new($false))
    Push-Location $script:testRepoDir
    & git add src/hello.txt 2>$null
    Pop-Location
    # 4. Verify check-scope passes
    $csResult = Invoke-PythonScript "check-scope"
    $csData = $csResult | ConvertFrom-Json
    if (-not (Assert-Equal $csData.status "PASS" ("check-scope should PASS for in-scope file, got " + $csData.status))) { return $false }
    # 5. Verify run-gates passes (no gate policy = no gates = PASS)
    $gateResult = Invoke-PythonScriptWithExit "run-gates"
    if (-not (Assert-Equal $gateResult.exitCode 0 ("run-gates should PASS with no failing gates, got: " + $gateResult.exitCode))) { return $false }
    # 6. Verify validate-state passes
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should PASS after successful bounded task, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "E2E: ScopeViolation" -Layers @("integration") {
    Init-TestWorkspace
    # 1. Create READY task with allowedWrites: ["src/**"]
    $taskJson = '{"schemaVersion":1,"taskId":"task-e2e-2","title":"Scope violation test","status":"READY","scope":["src/**"],"allowedWrites":["src/**", ".teamloop/**"],"successCriteria":["scope"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson
    # 2. Run RUN_EXECUTOR
    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-e2e-2" | Out-Null
    # 3. Create a file OUTSIDE scope (foo.txt at root)
    [System.IO.File]::WriteAllText((Join-Path $script:testRepoDir "foo.txt"), "out of scope`n", [System.Text.UTF8Encoding]::new($false))
    Push-Location $script:testRepoDir
    & git add foo.txt 2>$null
    Pop-Location
    # 4. Verify check-scope FAILS
    $csResult = Invoke-PythonScript "check-scope"
    $csData = $csResult | ConvertFrom-Json
    if (-not (Assert-Equal $csData.status "FAIL" ("check-scope should FAIL for out-of-scope file, got " + $csData.status))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "E2E: GateFailure" -Layers @("integration") {
    Init-TestWorkspace
    # Set up a task and start a run (run-gates needs currentRunId)
    $taskJson = '{"schemaVersion":1,"taskId":"task-e2e-g","title":"Gate test","status":"READY","scope":["src/**"],"successCriteria":["Pass"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $taskJson
    Invoke-PythonScript "apply-transition" "--action", "RUN_EXECUTOR", "--task-id", "task-e2e-g" | Out-Null
    Invoke-PythonScript "apply-transition" "--action", "RUN_GATEKEEPER" | Out-Null
    # 1. Create gate-policy.json with required gate that fails
    $gp = '{"gates":[{"name":"always-fail","type":"shell","command":"cmd /c exit 1","required":true}]}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "policies\gate-policy.json") -Content $gp
    # 2. Verify run-gates FAILS
    $gateResult = Invoke-PythonScriptWithExit "run-gates"
    if (-not (Assert-Equal $gateResult.exitCode 1 ("run-gates with required fail gate should exit 1, got: " + $gateResult.exitCode))) { return $false }
    $output = $gateResult.output -join "`n"
    $gateData = $output | ConvertFrom-Json
    if (-not (Assert-Equal $gateData.status "FAIL" ("Gate status should be FAIL, got " + $gateData.status))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "E2E: HumanBlocker" -Layers @("integration") {
    Init-TestWorkspace
    # 1. Create a valid blocker in blockers.jsonl
    $blocker = '{"schemaVersion":1,"blockerId":"blocker-e2e","type":"HUMAN_DECISION_REQUIRED","category":"PRODUCT_BEHAVIOR_AMBIGUITY","summary":"Need approval for E2E","evidence":["evidence"],"questionsForHuman":["Should we proceed?"]}'
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\blockers.jsonl") -Line $blocker
    # 2. Attempt SET_DONE via apply-transition
    Invoke-PythonScript "apply-transition" "--action", "SET_DONE" | Out-Null
    # 3. Verify validate-state FAILS (open blocker prevents DONE)
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should FAIL with open blocker, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "E2E: ProtectedChange" -Layers @("integration") {
    Init-TestWorkspace
    # 1. Copy protected-paths.json to workspace (protect scripts/**)
    $policy = '{"schemaVersion":1,"protectedPaths":["scripts/**"],"enforcementLevel":"error","evidenceRequired":{"fullTestSuite":true,"independentReview":true}}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "policies\protected-paths.json") -Content $policy
    # 2. Create a file in scripts/ and stage it
    $scriptsDir = Join-Path $script:testRepoDir "scripts"
    New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $scriptsDir "new-script.py"), "# test`n", [System.Text.UTF8Encoding]::new($false))
    Push-Location $script:testRepoDir
    & git add scripts/new-script.py 2>$null
    Pop-Location
    # 3. Verify check-guard-integrity detects the protected change
    $guardResult = Invoke-PythonScriptWithExit "check-guard-integrity"
    $output = $guardResult.output -join "`n"
    if ($output -notmatch "protected-paths") {
        Write-Host "  FAIL: check-guard-integrity should detect protected path change, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "E2E: MemoryIntegrity" -Layers @("integration") {
    Init-TestWorkspace
    # 1. Create valid memory (lesson + evidence in evidence-map.jsonl)
    $evidence = '{"schemaVersion":1,"evidenceId":"evidence-e2e","type":"TEST_RESULT","reference":"tests/run-tests.sh","createdAtUtc":"2024-01-01T00:00:00Z"}'
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-e2e","title":"E2E lesson","description":"Memory integrity test","status":"ACTIVE","evidenceIds":["evidence-e2e"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\evidence-map.jsonl") -Content $evidence
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    # 2. Verify memory-doctor passes
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult.exitCode 0 ("memory-doctor should PASS with valid memory, got: " + $doctorResult.output))) { return $false }
    $doctorOutput = $doctorResult.output -join "`n"
    $doctorJson = $doctorOutput | ConvertFrom-Json
    if (-not (Assert-Equal $doctorJson.status "PASS" ("memory-doctor output should contain PASS, got " + $doctorJson.status))) { return $false }
    # 3. Replace with invalid memory (active lesson without evidence)
    $badLesson = '{"schemaVersion":1,"lessonId":"lesson-bad","title":"Bad lesson","description":"No evidence","status":"ACTIVE","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $badLesson
    # 4. Verify memory-doctor fails
    $doctorResult2 = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult2.exitCode 1 ("memory-doctor should FAIL with invalid memory, got: " + $doctorResult2.output))) { return $false }
    $doctorOutput2 = $doctorResult2.output -join "`n"
    $doctorJson2 = $doctorOutput2 | ConvertFrom-Json
    if (-not (Assert-Equal $doctorJson2.status "FAIL" ("memory-doctor output should contain FAIL, got " + $doctorJson2.status))) { return $false }
    Cleanup-Workspace
    return $true
}

# ============================================================
# CAMPAIGN REGRESSION TESTS
# ============================================================

Test-Run "Campaign: FinalGate_Pass" -Layers @("integration") {
    Init-TestWorkspace
    # Write minimal continuation-decision so validate-state passes
    $cd = '{"schemaVersion":1,"decision":"SAFE_CHECKPOINT","phase":"SAFE_CHECKPOINT","justification":"test checkpoint","checks":[{"name":"test","status":"PASS"}],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "state\continuation-decision.json") -Content $cd
    $result = Invoke-PythonScriptWithExit "final-gate"
    if (-not (Assert-Equal $result.exitCode 0 ("final-gate should exit 0 on valid workspace, got: " + $result.output))) { return $false }
    $output = $result.output -join "`n"
    if ($output -notmatch '"overallStatus":\s*"PASS"') {
        Write-Host "  FAIL: final-gate output should contain PASS, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: FinalGate_FailValidation" -Layers @("integration") {
    Init-TestWorkspace
    # Corrupt team-state.json
    '{"schemaVersion":1}' | Set-Content (Join-Path $script:workspaceAbs "state\team-state.json") -Encoding UTF8
    $result = Invoke-PythonScriptWithExit "final-gate"
    if (-not (Assert-True ($result.exitCode -ne 0) ("final-gate should fail on corrupted state, got exit " + $result.exitCode))) { return $false }
    $output = $result.output -join "`n"
    if ($output -notmatch '"overallStatus":\s*"FAIL"') {
        Write-Host "  FAIL: final-gate output should contain FAIL, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: FinalGate_SchemaValid" -Layers @("integration") {
    Init-TestWorkspace
    $cd = '{"schemaVersion":1,"decision":"SAFE_CHECKPOINT","phase":"SAFE_CHECKPOINT","justification":"test checkpoint","checks":[{"name":"test","status":"PASS"}],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "state\continuation-decision.json") -Content $cd
    $result = Invoke-PythonScriptWithExit "final-gate"
    if (-not (Assert-Equal $result.exitCode 0 ("final-gate should pass for valid workspace"))) { return $false }
    $resultFile = Join-Path $script:workspaceAbs "state\final-gate-result.json"
    if (-not (Test-Path $resultFile)) {
        Write-Host "  FAIL: final-gate-result.json should exist at state/" -ForegroundColor Red
        return $false
    }
    try {
        $content = Get-Content $resultFile -Raw
        $null = $content | ConvertFrom-Json
    } catch {
        Write-Host "  FAIL: final-gate-result.json should be valid JSON" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: ReviewEvidence_ContentMissing" -Layers @("integration") {
    Init-TestWorkspace
    $commit = & git rev-parse HEAD 2>$null
    $evidence = '{"schemaVersion":1,"taskId":"task-missing","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedCommit":"' + $commit + '","reviewedFiles":[{"path":"src/nonexistent.txt","hash":"0000000000000000000000000000000000000000000000000000000000000000","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "state\review-evidence.json") -Content $evidence
    $result = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-True ($result.exitCode -ne 0) ("validate-state should FAIL when reviewed content is missing, exit=" + $result.exitCode))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: ReviewEvidence_ContentChanged" -Layers @("integration") {
    Init-TestWorkspace
    # Create a file in the test repo, write evidence with a wrong hash
    $srcDir = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
    $filePath = Join-Path $srcDir "hashed.txt"
    [System.IO.File]::WriteAllText($filePath, "original content`n", [System.Text.UTF8Encoding]::new($false))
    Push-Location $script:testRepoDir
    & git add src/hashed.txt 2>$null
    & git commit -m "add hashed file" --quiet 2>$null
    Pop-Location
    $wrongHash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    $evidence = '{"schemaVersion":1,"taskId":"task-changed","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedFiles":[{"path":"src/hashed.txt","hash":"' + $wrongHash + '","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "state\review-evidence.json") -Content $evidence
    $result = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-True ($result.exitCode -ne 0) ("validate-state should FAIL when reviewed content hash differs, exit=" + $result.exitCode))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: ReviewEvidence_ValidContent" -Layers @("integration") {
    Init-TestWorkspace
    # Create a file in the test repo, compute its actual hash
    $srcDir = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
    $filePath = Join-Path $srcDir "valid.txt"
    [System.IO.File]::WriteAllText($filePath, "valid content`n", [System.Text.UTF8Encoding]::new($false))
    Push-Location $script:testRepoDir
    & git add src/valid.txt 2>$null
    & git commit -m "add valid file" --quiet 2>$null
    Pop-Location
    $hash = (Get-FileHash $filePath -Algorithm SHA256).Hash.ToLower()
    $evidence = '{"schemaVersion":1,"taskId":"task-valid","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedFiles":[{"path":"src/valid.txt","hash":"' + $hash + '","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "state\review-evidence.json") -Content $evidence
    $result = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $result.exitCode 0 ("validate-state should PASS with matching reviewed content hash, exit=" + $result.exitCode + " output=" + $result.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: GuardNotConfigured" -Layers @("integration") {
    Init-TestWorkspace
    Remove-Item (Join-Path $script:workspaceAbs "policies\protected-paths.json") -Force -ErrorAction SilentlyContinue
    # No protected-paths.json — should report NOT_CONFIGURED
    $result = Invoke-PythonScriptWithExit "check-guard-integrity"
    $output = $result.output -join "`n"
    if ($output -notmatch '"NOT_CONFIGURED"') {
        Write-Host "  FAIL: check-guard-integrity should report NOT_CONFIGURED when policy missing, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: OrphanedInProgressDetected" -Layers @("integration") {
    Init-TestWorkspace
    $task = '{"schemaVersion":1,"taskId":"task-orphan","title":"Orphan task","status":"IN_PROGRESS","priority":"P1","origin":"manual","scope":["src/**"],"allowedWrites":["src/**"],"successCriteria":["should be detected as orphan"]}'
    $task | Set-Content (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Encoding UTF8
    $result = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-True ($result.exitCode -ne 0) ("validate-state should FAIL with orphaned IN_PROGRESS task, exit=" + $result.exitCode))) { return $false }
    $output = $result.output -join "`n"
    if ($output -notmatch '(?i)(orphan|IN_PROGRESS|inconsisten|stale)') {
        Write-Host "  FAIL: validate-state should mention orphan/IN_PROGRESS issue, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: MojibakeDetection" -Layers @("contract") {
    $teamloopFile = Join-Path $projectRoot "TEAMLOOP.md"
    if (-not (Test-Path $teamloopFile)) {
        Write-Host "  FAIL: TEAMLOOP.md should exist" -ForegroundColor Red
        return $false
    }
    $content = Get-Content $teamloopFile -Raw -Encoding UTF8
    if ($content -notmatch '\u2260') {
        Write-Host "  FAIL: TEAMLOOP.md should contain the ≠ (U+2260) symbol" -ForegroundColor Red
        return $false
    }
    if ($content -match 'тЙа|тАФ|тЖТ|тЦ╝') {
        Write-Host "  FAIL: TEAMLOOP.md contains known encoding corruption" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: CrossTaskCleanup_Preserved" -Layers @("integration") {
    Init-TestWorkspace
    # Use TEAMLOOP.md with a wrong hash to simulate tampered cross-task content
    $wrongHash = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    $evidence = '{"schemaVersion":1,"taskId":"task-cross","reviewedAtUtc":"2024-01-01T00:00:00Z","reviewedFiles":[{"path":"TEAMLOOP.md","hash":"' + $wrongHash + '","status":"TRACKED"}],"reviewResult":"PASS","reviewer":"change-reviewer"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "state\review-evidence.json") -Content $evidence
    $result = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-True ($result.exitCode -ne 0) ("validate-state should FAIL when reviewed content was tampered, exit=" + $result.exitCode))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: FinalGate_BashWrapperExists" -Layers @("contract") {
    $wrapper = Join-Path $scriptDir "final-gate.sh"
    if (-not (Test-Path $wrapper)) {
        Write-Host "  FAIL: final-gate.sh wrapper should exist" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Campaign: FinalGate_PSWrapperExists" -Layers @("contract") {
    $wrapper = Join-Path $scriptDir "final-gate.ps1"
    if (-not (Test-Path $wrapper)) {
        Write-Host "  FAIL: final-gate.ps1 wrapper should exist" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}


# ============================================================
# FAST EXECUTION CONTRACT TESTS
# ============================================================
$script:fastRunId = ""

function Start-FastExecutionTask {
    param(
        [string]$TaskId,
        [string]$Priority = "P2",
        [string]$Scope = "src/**",
        [string]$Allowed = "src/**"
    )
    $task = @{
        schemaVersion = 1
        taskId = $TaskId
        title = "Fast execution PowerShell test"
        status = "READY"
        priority = $Priority
        origin = "fast-execution-powershell-tests"
        scope = @($Scope)
        allowedWrites = @($Allowed, ".teamloop/**")
        requiredEvidence = @("test evidence")
        successCriteria = @("scenario passes")
        forbiddenActions = @("do not weaken gates")
        humanRequired = $false
        blockers = @()
    } | ConvertTo-Json -Compress -Depth 8
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $task
    $output = Invoke-PythonScript -Command "apply-transition" -ExtraArgs @("--action", "RUN_EXECUTOR", "--task-id", $TaskId)
    $data = ($output -join "`n") | ConvertFrom-Json
    $script:fastRunId = $data.runId
    return -not [string]::IsNullOrWhiteSpace($script:fastRunId)
}

Test-Run "FastExecution PS: LowRiskResolvesFast" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-fast" -Priority "P2")) { return $false }
    $output = Invoke-PythonScript -Command "prepare-execution"
    $data = ($output -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.profile "fast" "low-risk task should resolve fast")) { return $false }
    $policy = Get-Content (Join-Path $script:workspaceAbs "runs\$script:fastRunId\execution-policy.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not (Assert-Equal $policy.requiredRoles.Count 1 "fast should require one executor-like role")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: StandardRequiresReviewer" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-standard" -Priority "P1")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $output = Invoke-PythonScript -Command "route-role" -ExtraArgs @("--event", "implementation-complete")
    $data = ($output -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.nextAction "RUN_CHANGE_REVIEWER" "standard should route reviewer")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: AuditRequiresAllRoles" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-audit" -Priority "P0")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $policy = Get-Content (Join-Path $script:workspaceAbs "runs\$script:fastRunId\execution-policy.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not (Assert-Equal $policy.selectedProfile "audit" "P0 should resolve audit")) { return $false }
    foreach ($role in @("executor", "change-reviewer", "watchdog", "sentinel")) {
        if ($policy.requiredRoles -notcontains $role) {
            Write-Host "  FAIL: audit profile missing required role $role" -ForegroundColor Red
            return $false
        }
    }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: ProtectedScopeEscalatesAudit" -Layers @("runtime") {
    Init-TestWorkspace
    Copy-Item (Join-Path $projectRoot "templates\workspace\policies\protected-paths.json") (Join-Path $script:workspaceAbs "policies\protected-paths.json") -Force
    if (-not (Start-FastExecutionTask -TaskId "task-ps-protected" -Priority "P2" -Scope "scripts/**" -Allowed "scripts/**")) { return $false }
    $output = Invoke-PythonScript -Command "prepare-execution" -ExtraArgs @("--profile", "fast")
    $data = ($output -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.profile "audit" "protected fast request must escalate")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: ManifestIdempotent" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-idempotent")) { return $false }
    $first = ((Invoke-PythonScript -Command "prepare-execution") -join "`n") | ConvertFrom-Json
    $second = ((Invoke-PythonScript -Command "prepare-execution") -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $second.policyReused $true "second policy materialization should be reused")) { return $false }
    if (-not (Assert-Equal $first.manifestFingerprint $second.manifestFingerprint "manifest fingerprint should remain stable")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: ManualManifestMutationFails" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-mutation")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $manifestPath = Join-Path $script:workspaceAbs "runs\$script:fastRunId\execution-manifest.json"
    $manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $manifest.executionProfile = "audit"
    Write-JsonFile -Path $manifestPath -Content ($manifest | ConvertTo-Json -Depth 12)
    $result = Invoke-PythonScriptWithExit -Command "validate-execution-contract"
    if (-not (Assert-True ($result.exitCode -ne 0) "manual manifest mutation must fail")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: IdenticalSnapshotsDetectNoProgress" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-no-progress")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    Invoke-PythonScript -Command "record-progress" | Out-Null
    $output = Invoke-PythonScript -Command "record-progress"
    $data = ($output -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.result.status "NO_PROGRESS_DETECTED" "identical snapshots should trigger no-progress")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: MaterialChangeResetsStreak" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-material")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    Invoke-PythonScript -Command "record-progress" | Out-Null
    $src = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $src -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $src "change.txt"), "material`n", [System.Text.UTF8Encoding]::new($false))
    $data = ((Invoke-PythonScript -Command "record-progress") -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.result.status "PROGRESS_OBSERVED" "material scoped diff should reset streak")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: SuppressionOnlyIsNotProgress" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-suppression")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $src = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $src -Force | Out-Null
    $file = Join-Path $src "work.py"
    [System.IO.File]::WriteAllText($file, "# TODO: restore behavior`n", [System.Text.UTF8Encoding]::new($false))
    Invoke-PythonScript -Command "record-progress" | Out-Null
    [System.IO.File]::WriteAllText($file, "", [System.Text.UTF8Encoding]::new($false))
    $data = ((Invoke-PythonScript -Command "record-progress") -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.result.progressClassification "SUPPRESSION_ONLY_NOT_PROGRESS" "TODO deletion alone must not count as progress")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: WatchdogRecoveryDoesNotLoop" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-watchdog")) { return $false }
    $originalRun = $script:fastRunId
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    Invoke-PythonScript -Command "record-progress" | Out-Null
    Invoke-PythonScript -Command "record-progress" | Out-Null
    $route = ((Invoke-PythonScript -Command "route-role" -ExtraArgs @("--event", "watchdog-complete")) -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $route.nextAction "RETRY_EXECUTOR" "watchdog completion should require changed retry")) { return $false }
    $retry = ((Invoke-PythonScript -Command "apply-transition" -ExtraArgs @("--action", "RETRY_EXECUTOR")) -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $retry.runId $originalRun "retry should preserve run identity")) { return $false }
    $next = ((Invoke-PythonScript -Command "next-action") -join "`n") | ConvertFrom-Json
    if (-not (Assert-True ($next.nextAction -ne "RUN_WATCHDOG") "watchdog must not route to itself after strategy acknowledgement")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: FakeClockTrace" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-clock")) { return $false }
    $oldClock = $env:TEAMLOOP_FAKE_CLOCK_MS
    try {
        $env:TEAMLOOP_FAKE_CLOCK_MS = '[100,125]'
        Invoke-PythonScript -Command "prepare-execution" | Out-Null
    } finally {
        $env:TEAMLOOP_FAKE_CLOCK_MS = $oldClock
    }
    $trace = Get-Content (Join-Path $script:workspaceAbs "runs\$script:fastRunId\performance-trace.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $phase = $trace.phases | Where-Object { $_.phase -eq "execution-contract-creation-validation" } | Select-Object -Last 1
    if (-not (Assert-Equal $phase.durationMs 25 "fake clock should produce deterministic 25ms duration")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: SchemasWrappersAndPrompt" -Layers @("contract") {
    foreach ($name in @("execution-policy", "execution-manifest", "execution-manifest-validation", "performance-trace", "progress-snapshot", "no-progress-result", "role-routing-decision")) {
        if (-not (Test-Path (Join-Path $projectRoot "schemas\$name.schema.json"))) {
            Write-Host "  FAIL: missing schema $name" -ForegroundColor Red
            return $false
        }
    }
    foreach ($name in @("prepare-execution", "resolve-execution-policy", "materialize-execution-manifest", "validate-execution-contract", "record-progress", "route-role", "record-performance", "performance-report")) {
        if (-not (Test-Path (Join-Path $scriptDir "$name.ps1"))) {
            Write-Host "  FAIL: missing PowerShell wrapper $name.ps1" -ForegroundColor Red
            return $false
        }
    }
    $prompt = Get-Content (Join-Path $projectRoot ".opencode\commands\supervised-task.md") -Raw -Encoding UTF8
    if ($prompt -notmatch 'prepare-execution' -or $prompt -notmatch 'record-progress' -or $prompt -notmatch 'final-gate\.sh') {
        Write-Host "  FAIL: supervised-task prompt is not runtime-bound" -ForegroundColor Red
        return $false
    }
    return $true
}

Test-Run "FastExecution PS: OptimizedFinalGateRequiresSentinel" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-final")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $result = Invoke-PythonScriptWithExit -Command "final-gate"
    $output = $result.output -join "`n"
    if (-not (Assert-True ($result.exitCode -ne 0) "optimized final gate must require final sentinel")) { return $false }
    if ($output -notmatch 'requires a final sentinel inspection') {
        Write-Host "  FAIL: mandatory sentinel failure missing: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}


Test-Run "FastExecution PS: OptimizedFinalGatePass" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-optimized-pass")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $src = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $src -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $src "ok.txt"), "ok`n", [System.Text.UTF8Encoding]::new($false))
    Invoke-PythonScript -Command "record-progress" | Out-Null
    Invoke-PythonScript -Command "apply-transition" -ExtraArgs @("--action", "RUN_GATEKEEPER") | Out-Null
    Invoke-PythonScript -Command "run-gates" | Out-Null
    Invoke-PythonScript -Command "run-sentinel" | Out-Null
    $result = Invoke-PythonScriptWithExit -Command "final-gate"
    if (-not (Assert-Equal $result.exitCode 0 "optimized final gate should pass after same-run sentinel and gates")) { return $false }
    $data = ($result.output -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.overallStatus "PASS" "optimized final gate result")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: StaleSentinelCannotSatisfyCurrentRun" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-old-sentinel")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    Invoke-PythonScript -Command "run-sentinel" | Out-Null
    $task = @{
        schemaVersion = 1; taskId = "task-ps-current-no-sentinel"; title = "Current run"
        status = "READY"; priority = "P2"; origin = "fast-execution-powershell-tests"
        scope = @("src/**"); allowedWrites = @("src/**", ".teamloop/**")
        requiredEvidence = @("test"); successCriteria = @("test")
        forbiddenActions = @(); humanRequired = $false; blockers = @()
    } | ConvertTo-Json -Compress -Depth 8
    Append-JsonLine -Path (Join-Path $script:workspaceAbs "state\backlog.jsonl") -Line $task
    $newRun = ((Invoke-PythonScript -Command "apply-transition" -ExtraArgs @("--action", "RUN_EXECUTOR", "--task-id", "task-ps-current-no-sentinel")) -join "`n") | ConvertFrom-Json
    $script:fastRunId = $newRun.runId
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $result = Invoke-PythonScriptWithExit -Command "final-gate"
    $output = $result.output -join "`n"
    if (-not (Assert-True ($result.exitCode -ne 0) "stale sentinel from another run must not satisfy final gate")) { return $false }
    if ($output -notmatch [regex]::Escape("runs/$script:fastRunId/sentinel-inspection.json is missing")) {
        Write-Host "  FAIL: final gate did not require same-run sentinel: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: MaterialImplementationAfterTodoCountsProgress" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-material-after-todo")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $src = Join-Path $script:testRepoDir "src"
    New-Item -ItemType Directory -Path $src -Force | Out-Null
    $file = Join-Path $src "work.py"
    [System.IO.File]::WriteAllText($file, "# TODO: restore behavior`n", [System.Text.UTF8Encoding]::new($false))
    Invoke-PythonScript -Command "record-progress" | Out-Null
    [System.IO.File]::WriteAllText($file, "value = 1`n", [System.Text.UTF8Encoding]::new($false))
    $data = ((Invoke-PythonScript -Command "record-progress") -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $data.result.status "PROGRESS_OBSERVED" "material implementation should count as progress")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "FastExecution PS: AuditWatchdogRoutesProjectGates" -Layers @("runtime") {
    Init-TestWorkspace
    if (-not (Start-FastExecutionTask -TaskId "task-ps-audit-route" -Priority "P0")) { return $false }
    Invoke-PythonScript -Command "prepare-execution" | Out-Null
    $review = ((Invoke-PythonScript -Command "route-role" -ExtraArgs @("--event", "review-complete")) -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $review.nextAction "RUN_WATCHDOG" "audit review should route watchdog")) { return $false }
    $watchdog = ((Invoke-PythonScript -Command "route-role" -ExtraArgs @("--event", "watchdog-complete")) -join "`n") | ConvertFrom-Json
    if (-not (Assert-Equal $watchdog.nextAction "RUN_GATEKEEPER" "audit watchdog must route project gates before final sentinel")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Guard PS: UnstagedProtectedPathParsing" -Layers @("runtime") {
    Init-TestWorkspace
    $scriptsDir = Join-Path $script:testRepoDir "scripts"
    New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
    $scriptFile = Join-Path $scriptsDir "demo.sh"
    [System.IO.File]::WriteAllText($scriptFile, "#!/usr/bin/env bash`necho ok`n", [System.Text.UTF8Encoding]::new($false))
    git -C $script:testRepoDir add scripts/demo.sh | Out-Null
    git -C $script:testRepoDir commit -m "add protected script" --no-verify | Out-Null
    Add-Content -Path $scriptFile -Value "# changed" -Encoding UTF8
    $policyPath = Join-Path $script:workspaceAbs "policies\protected-paths.json"
    $policy = Get-Content $policyPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $policy.enforcementLevel = "error"
    Write-JsonFile -Path $policyPath -Content ($policy | ConvertTo-Json -Depth 12)
    $result = Invoke-PythonScriptWithExit -Command "check-guard-integrity"
    $output = $result.output -join "`n"
    if (-not (Assert-True ($result.exitCode -ne 0) "unstaged protected modification must fail guard")) { return $false }
    if ($output -notmatch 'scripts/demo\.sh') {
        Write-Host "  FAIL: guard corrupted unstaged path parsing: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "OpenCode PS: ReviewerRoutingIsRuntimeBound" -Layers @("integration") {
    $reviewer = Join-Path $projectRoot ".opencode\agents\change-reviewer.md"
    $content = Get-Content $reviewer -Raw -Encoding UTF8
    if ($content -notmatch 'route-role\.sh.*review-complete') {
        Write-Host "  FAIL: reviewer prompt must use runtime routing" -ForegroundColor Red
        return $false
    }
    if ($content -match 'On APPROVED: use .*RUN_GATEKEEPER') {
        Write-Host "  FAIL: reviewer prompt still bypasses audit watchdog" -ForegroundColor Red
        return $false
    }
    return $true
}


Test-Run "OpenCode PS: FastExecutionAgentCopiesSynchronized" -Layers @("integration") {
    foreach ($name in @("executor", "change-reviewer", "gatekeeper", "watchdog", "sentinel")) {
        $runtime = Join-Path $projectRoot ".opencode\agents\$name.md"
        $adapter = Join-Path $projectRoot "adapters\opencode\agents\$name.md"
        $runtimeBytes = [System.IO.File]::ReadAllBytes($runtime)
        $adapterBytes = [System.IO.File]::ReadAllBytes($adapter)
        if ($runtimeBytes.Length -ne $adapterBytes.Length -or (Compare-Object $runtimeBytes $adapterBytes)) {
            Write-Host "  FAIL: OpenCode agent copy drifted: $name" -ForegroundColor Red
            return $false
        }
    }
    return $true
}

# ============================================================
# SUMMARY
# ============================================================
Write-Host "`n========================================" -ForegroundColor White
Write-Host "Results: $script:passed/$script:total passed, $script:failed failed" -ForegroundColor $(if ($script:failed -eq 0) { "Green" } else { "Red" })
Write-Host "========================================" -ForegroundColor White

if ($script:failed -gt 0) {
    exit 1
}
exit 0
