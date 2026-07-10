param(
    [string]$TestWorkspace = ".teamloop-test"
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

function Test-Run {
    param([string]$Name, [scriptblock]$ScriptBlock)
    $script:total++
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
    Set-Location $script:testRepoDir
    $output = & python "$scriptDir/teamloop-core.py" @fullArgs 2>$null
    return $output
}

function Invoke-PythonScriptWithExit {
    param([string]$Command, [string[]]$ExtraArgs = @())
    $fullArgs = @($Command) + $ExtraArgs + @("--workspace", $script:workspaceAbs)
    Set-Location $script:testRepoDir
    $output = & python "$scriptDir/teamloop-core.py" @fullArgs 2>$null
    $exitCode = $LASTEXITCODE
    return @{ output = $output; exitCode = $exitCode }
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
Test-Run "P0: REVIEW_FAILED next-action preserves taskId" {
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
Test-Run "P0: stale current-task.json does NOT grant scope after gate PASS" {
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
Test-Run "P0: validate-state catches stale current-task.json" {
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
Test-Run "P0: CONTINUE_LOOP clears current-task.json" {
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
Test-Run "P0: failed gate → validate-state PASS → next-action RUN_EXECUTOR" {
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
Test-Run "P0: validate-state catches invalid JSON in artifacts" {
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
Test-Run "P1: NEEDS_TASK_SLICING + READY task -> RUN_EXECUTOR" {
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
Test-Run "P1: NEEDS_TASK_SLICING no READY tasks -> RUN_TASK_SLICER" {
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
Test-Run "Memory: EmptyPasses" {
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

Test-Run "Memory: MalformedJsonlFails" {
    Init-TestWorkspace
    [System.IO.File]::WriteAllText((Join-Path $script:workspaceAbs "memory\lessons.jsonl"), '{bad json content here', [System.Text.UTF8Encoding]::new($false))
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on malformed memory JSONL, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ActiveWithoutEvidenceFails" {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on ACTIVE lesson without evidenceIds, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ActiveWithValidEvidencePasses" {
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

Test-Run "Memory: ActiveWithMissingEvidenceIdFails" {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-001","title":"A lesson","description":"Desc","status":"ACTIVE","evidenceIds":["evidence-missing"],"createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on ACTIVE lesson referencing missing evidenceId, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: DeprecatedRetainedButInactive" {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-depr","title":"Old lesson","description":"Deprecated","status":"DEPRECATED","createdAtUtc":"2024-01-01T00:00:00Z","deprecatedAtUtc":"2024-06-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass for DEPRECATED lesson without evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: SupersededWithoutEvidencePasses" {
    Init-TestWorkspace
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-sup","title":"Superseded","description":"Old way","status":"SUPERSEDED","createdAtUtc":"2024-01-01T00:00:00Z","supersededBy":"lesson-new"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass for SUPERSEDED lesson without evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: RejectedAntipatternWithoutEvidencePasses" {
    Init-TestWorkspace
    $anti = '{"schemaVersion":1,"antipatternId":"antipattern-001","title":"Old anti","description":"Rejected","status":"REJECTED","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\antipatterns.jsonl") -Content $anti
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass for REJECTED antipattern without evidence, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: MissingMemoryDirPasses" {
    Init-TestWorkspace
    Remove-Item (Join-Path $script:workspaceAbs "memory") -Recurse -Force
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass even if memory dir missing, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ProfileValidation" {
    Init-TestWorkspace
    $pp = '{"schemaVersion":1,"workspace":".teamloop","memoryVersion":"1","invalidField":"bad"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\project-profile.json") -Content $pp
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("validate-state should fail on project-profile with invalid field, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: DoctorEmptyPasses" {
    Init-TestWorkspace
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult.exitCode 0 ("memory-doctor should exit 0 on empty memory, got: " + $doctorResult.output))) { return $false }
    $doctorJson = $doctorResult.output | ConvertFrom-Json
    if (-not (Assert-Equal $doctorJson.status "PASS" ("memory-doctor output should contain PASS, got " + $doctorJson.status))) { return $false }
    if (-not (Assert-True ($doctorJson.PSObject.Properties.Name -contains "checks") "memory-doctor output should contain checks array")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: DoctorDetectsIssues" {
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

Test-Run "Memory: ActiveWithUnverifiedEvidenceFails" {
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
# SUMMARY
# ============================================================
Write-Host "`n========================================" -ForegroundColor White
Write-Host "Results: $script:passed/$script:total passed, $script:failed failed" -ForegroundColor $(if ($script:failed -eq 0) { "Green" } else { "Red" })
Write-Host "========================================" -ForegroundColor White

if ($script:failed -gt 0) {
    exit 1
}
exit 0
