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
    $lesson = '{"schemaVersion":1,"lessonId":"lesson-sup","title":"Superseded","description":"Old way","status":"SUPERSEDED","createdAtUtc":"2024-01-01T00:00:00Z"}'
    Write-JsonFile -Path (Join-Path $script:workspaceAbs "memory\lessons.jsonl") -Content $lesson
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass for SUPERSEDED lesson without evidence or supersededBy, got: " + $valResult.output))) { return $false }
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
# NEW REGRESSION TESTS (PowerShell)
# ============================================================
Test-Run "WriteEvent: InvalidTypeRejected" {
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

Test-Run "Memory: ActiveWithUnverifiedEvidenceFailsSchemaValid" {
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

Test-Run "Memory: SupersededByFailsBothValidateStateAndMemoryDoctor" {
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

Test-Run "MemoryDoctor: MissingDirectoryFails" {
    Init-TestWorkspace
    Remove-Item (Join-Path $script:workspaceAbs "memory") -Recurse -Force
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    if (-not (Assert-Equal $doctorResult.exitCode 1 ("memory-doctor should exit 1 when memory dir missing, got: " + $doctorResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "MemoryDoctor: EmptySubsystemWarns" {
    Init-TestWorkspace
    $doctorResult = Invoke-PythonScriptWithExit "memory-doctor"
    $doctorOutput = $doctorResult.output -join "`n"
    if (-not (Assert-Match $doctorOutput "WARNING" "memory-doctor should report WARNING for empty subsystem")) { return $false }
    if (-not (Assert-Equal $doctorResult.exitCode 0 ("memory-doctor should exit 0 for WARNING-level finding, got: " + $doctorOutput))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Memory: ProfileDeprecatedFieldsRejected" {
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
Test-Run "ContinuationDecision: ValidDecisionWrite" {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "write-continuation-decision" "--decision", "SAFE_CHECKPOINT", "--phase", "EXECUTING_TASK"
    if (-not (Assert-Equal $result.exitCode 0 ("write-continuation-decision SAFE_CHECKPOINT should exit 0, got: " + $result.exitCode))) { return $false }
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (-not (Assert-True (Test-Path $cdPath) "continuation-decision.json should be created")) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: InvalidDecisionRejected" {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "write-continuation-decision" "--decision", "INVALID", "--phase", "EXECUTING_TASK"
    if (-not (Assert-Equal $result.exitCode 1 ("write-continuation-decision with INVALID should exit 1, got: " + $result.exitCode))) { return $false }
    # Note: Invoke-PythonScriptWithExit suppresses stderr, so we only verify the exit code
    # (the writer prints the error message to stderr before exiting)
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: MissingDecisionFilePassesValidation" {
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

Test-Run "ContinuationDecision: DoneRequiresDonePhase" {
    Init-TestWorkspace
    Invoke-PythonScript "write-continuation-decision" "--decision", "DONE", "--phase", "EXECUTING_TASK" | Out-Null
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 1 ("DONE decision with EXECUTING_TASK phase should fail, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: WriteThenValidate" {
    Init-TestWorkspace
    Invoke-PythonScript "write-continuation-decision" "--decision", "SAFE_CHECKPOINT", "--phase", "INITIALIZED" | Out-Null
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass after writing valid SAFE_CHECKPOINT, got: " + $valResult.output))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "ContinuationDecision: SchemaExists" {
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
Test-Run "AutoDecision: SetCheckpointWritesDecision" {
    Init-TestWorkspace
    Invoke-PythonScript "apply-transition" "--action", "SET_SAFE_CHECKPOINT" | Out-Null
    $cdPath = Join-Path $script:workspaceAbs "state\continuation-decision.json"
    if (-not (Assert-True (Test-Path $cdPath) "SET_SAFE_CHECKPOINT should create continuation-decision.json")) { return $false }
    $cdContent = Get-Content $cdPath -Raw | ConvertFrom-Json
    if (-not (Assert-Equal $cdContent.decision "SAFE_CHECKPOINT" ("decision should be SAFE_CHECKPOINT, got " + $cdContent.decision))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "AutoDecision: TransientSkipsWrite" {
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

Test-Run "AutoDecision: DecisionFileValidJson" {
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

Test-Run "AutoDecision: SetDoneWritesDoneDecision" {
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
Test-Run "GuardIntegrity: CommandExists" {
    $helpOut = & python "$scriptDir/teamloop-core.py" --help 2>$null
    if ($helpOut -join "`n" -notmatch "check-guard-integrity") {
        Write-Host "  FAIL: check-guard-integrity should appear in --help" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "GuardIntegrity: MissingPolicyPasses" {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "check-guard-integrity"
    if (-not (Assert-Equal $result.exitCode 0 ("check-guard-integrity without policy should exit 0, got: " + $result.exitCode))) { return $false }
    $output = $result.output -join "`n"
    if ($output -notmatch '"status".*PASS|"status": "PASS"') {
        Write-Host "  FAIL: check-guard-integrity should return PASS status without policy, got: $output" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "GuardIntegrity: CleanWorkspacePasses" {
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

Test-Run "GuardIntegrity: WrapperPSExists" {
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
Test-Run "Sentinel: CommandExists" {
    $helpOut = & python "$scriptDir/teamloop-core.py" --help 2>$null
    if ($helpOut -join "`n" -notmatch "run-sentinel") {
        Write-Host "  FAIL: run-sentinel should appear in --help" -ForegroundColor Red
        return $false
    }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: CleanWorkspacePasses" {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "run-sentinel"
    if (-not (Assert-Equal $result.exitCode 0 ("run-sentinel should exit 0 on clean workspace, got: " + $result.exitCode))) { return $false }
    $output = $result.output -join "`n"
    $sentinelJson = $output | ConvertFrom-Json
    if (-not (Assert-Equal $sentinelJson.overallStatus "PASS" ("overallStatus should be PASS, got " + $sentinelJson.overallStatus))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: OutputIsValidJson" {
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

Test-Run "Sentinel: OutputMatchesSchema" {
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

Test-Run "Sentinel: WrapperPSExists" {
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

Test-Run "Sentinel: NineFindings" {
    Init-TestWorkspace
    $result = Invoke-PythonScriptWithExit "run-sentinel"
    $output = $result.output -join "`n"
    $data = $output | ConvertFrom-Json
    if (-not (Assert-Equal $data.findings.Count 9 ("Should have exactly 9 findings, got $($data.findings.Count)"))) { return $false }
    Cleanup-Workspace
    return $true
}

Test-Run "Sentinel: SchemaFileExists" {
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

Test-Run "Sentinel: ValidateStatePasses" {
    Init-TestWorkspace
    # Run sentinel (creates report with PASS status)
    Invoke-PythonScript "run-sentinel" | Out-Null
    $valResult = Invoke-PythonScriptWithExit "validate-state"
    if (-not (Assert-Equal $valResult.exitCode 0 ("validate-state should pass after clean sentinel run, got: " + $valResult.output))) { return $false }
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
