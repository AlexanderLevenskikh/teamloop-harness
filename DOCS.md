# YourAITeam — Документация

Файл-ориентированный рантайм для управляемых команд AI-агентов. Без UI, без облака, без демонов. Только JSON-файлы, скрипты и строгие инварианты переходов состояний.

---

> **Актуальное дополнение:** профили `fast` / `standard` / `audit`, immutable execution manifest, no-progress detector, event-driven role routing и performance trace описаны в [docs/FAST_EXECUTION.md](docs/FAST_EXECUTION.md). Командный справочник и тестовые totals ниже в этом историческом документе могут быть менее полными, чем README/TESTING.


## Архитектура

```
your-ai-team/
├── scripts/                 # Core runtime scripts (.ps1 + .sh)
│   ├── init-workspace.*     # Инициализация workspace .teamloop/
│   ├── validate-state.*     # Валидация всех файлов состояния
│   ├── next-action.*        # Диспетчеризация: текущее состояние → следующее действие
│   ├── write-event.*        # Аппенд события в events.jsonl
│   ├── check-scope.*        # Проверка git-изменений по scope-policy
│   └── run-gates.*          # Запуск gate-чеков из gate-policy
├── schemas/                 # 10 JSON Schema (team-state, task, event, и др.)
├── templates/workspace/     # Шаблоны для init-workspace
├── profiles/                # Профили задач (generic-software-task, и др.)
├── adapters/opencode/       # Интеграция с OpenCode
│   ├── agents/              # 7 ролей-агентов (supervisor, executor, и др.)
│   ├── commands/            # /supervised-task команда
│   ├── opencode.jsonc       # Конфигурация OpenCode
│   └── AGENTS.md            # Инструкции по установке адаптера
└── tests/
    ├── run-tests.ps1        # 21 регрессионный тест (PowerShell 5.1)
    └── run-tests.sh         # 21 регрессионный тест (Bash 4+)
```

---

## Концепция

Команда из 7 ролей-агентов выполняет задачу через строго определённые фазы. Каждое состояние хранится в файлах `.teamloop/`. Скрипты — это thin wrapper-ы, которые читают JSON, применяют матрицу переходов и записывают результат.

### Роли

| Роль | Файл | Назначение |
|---|---|---|
| **Supervisor** | `agents/supervisor.md` | Координатор: читает состояние, решает, кому передать, обновляет state |
| **Researcher** | `agents/researcher.md` | Исследование: собирает факты, пишет research-report |
| **Research Lead** | `agents/research-lead.md` | Проверка исследований: утверждает/отклоняет findings |
| **Task Slicer** | `agents/task-slicer.md` | Разбивает research на атомарные задачи в backlog |
| **Executor** | `agents/executor.md` | Выполняет конкретную задачу из backlog |
| **Change Reviewer** | `agents/change-reviewer.md` | Review изменений: проверяет scope, forbidden actions |
| **Gatekeeper** | `agents/gatekeeper.md` | Запускает gate-чеки (scope, shell-команды) |

### Фазы (State Machine)

```
NEW → NEEDS_DISCOVERY → NEEDS_PLAN → NEEDS_RESEARCH
                                      → NEEDS_RESEARCH_REVIEW
                                      → NEEDS_TASK_SLICING
                                      → READY_FOR_NEXT_TASK
                                          → EXECUTING_TASK
                                              → NEEDS_CHANGE_REVIEW
                                                  → NEEDS_GATE
                                                      → SAFE_CHECKPOINT
                                                          → READY_FOR_NEXT_TASK (loop)
                                                      → GATE_FAILED
                                                          → FIX → EXECUTING_TASK
                                                          → RESEARCH → NEEDS_RESEARCH
                                                          → HUMAN → HUMAN_DECISION_REQUIRED
DONE
```

### Критические инварианты

```
MANUAL_REVIEW ≠ HUMAN_REQUIRED
SAFE_CHECKPOINT ≠ DONE
RESEARCH_COMPLETE ≠ DONE
DONE с open-задачами → ошибка
HUMAN_DECISION_REQUIRED без blocker → ошибка
```

---

## Workspace `.teamloop/`

Структура после `init-workspace`:

```
.teamloop/
├── state/
│   ├── team-state.json      # Текущее состояние команды
│   ├── events.jsonl         # Журнал событий (append-only)
│   ├── backlog.jsonl        # Задачи (JSON per line)
│   ├── current-task.json    # Текущая задача executor
│   ├── run-ledger.jsonl     # Журнал ранов
│   ├── decisions.jsonl      # Принятые решения
│   └── blockers.jsonl       # Блокеры (обязательны для HUMAN_DECISION_REQUIRED)
├── runs/
│   └── {run-id}/
│       ├── result.md        # Результат рана
│       └── gate-result.json # Результат gate-чеков
├── research/
│   └── {research-id}.json   # Исследования
├── policies/
│   ├── scope-policy.json    # Разрешённые/запрещённые файлы
│   ├── gate-policy.json     # Gate-чеки
│   └── role-policy.json     # Политики ролей
└── profiles/
    └── active-profile.json  # Активный профиль задачи
```

---

## Скрипты (Core Scripts)

Каждый скрипт существует в двух вариантах: `.ps1` (PowerShell 5.1) и `.sh` (Bash 4+).

### `init-workspace`

Создаёт workspace из шаблонов. Копирует файлы, подставляет `__PROFILE__` и `__CREATED_AT__`.

```bash
# Bash
bash scripts/init-workspace.sh --workspace ".teamloop" --profile "generic-software-task"
# или коротко
bash scripts/init-workspace.sh -w .teamloop -p generic-software-task

# PowerShell
powershell -ExecutionPolicy Bypass -File scripts\init-workspace.ps1 -Workspace .teamloop -Profile generic-software-task
```

Параметры:
- `--workspace | -w` — имя директории workspace (по умолчанию `.teamloop`). Поддерживает абсолютные и относительные пути.
- `--profile | -p` — имя профиля из `profiles/` (по умолчанию `generic-software-task`)

### `validate-state`

Валидирует все файлы workspace: JSON-валидность, допустимые статусы/фазы, инварианты.

```bash
bash scripts/validate-state.sh --workspace ".teamloop"
powershell -File scripts\validate-state.ps1 -Workspace .teamloop
```

Проверяет:
- `team-state.json`: status ∈ {NEW, IN_PROGRESS, SAFE_CHECKPOINT, HUMAN_DECISION_REQUIRED, BLOCKED, DONE, FAILED}
- `team-state.json`: currentPhase ∈ {все допустимые фазы}
- DONE → нет unresolved blockers
- DONE → нет open задач в backlog (status ∉ {DONE, CANCELLED, SKIPPED, FAILED})
- HUMAN_DECISION_REQUIRED → есть хотя бы один blocker в `blockers.jsonl`
- currentTaskId → задача найдена в backlog или current-task.json
- currentRunId → run-директория или запись в run-ledger
- Все JSONL файлы: каждая строка — валидный JSON
- `profiles/active-profile.json`, `policies/scope-policy.json`, `policies/gate-policy.json` — валидный JSON

Exit code: `0` = PASSED, `1` = FAILED

### `next-action`

Возвращает JSON с следующим действием на основе матрицы диспетчеризации.

```bash
bash scripts/next-action.sh --workspace ".teamloop"
```

Выводит JSON:
```json
{
  "nextAction": "RUN_EXECUTOR",
  "phase": "EXECUTING_TASK",
  "taskId": "task-001",
  "humanRequired": false
}
```

Матрица диспетчеризации:

| currentPhase | nextAction | newPhase |
|---|---|---|
| NEW / NEEDS_DISCOVERY | RUN_DISCOVERY | NEEDS_DISCOVERY |
| NEEDS_PLAN | RUN_RESEARCH | NEEDS_RESEARCH |
| NEEDS_RESEARCH | RUN_RESEARCHER | — |
| NEEDS_RESEARCH_REVIEW | RUN_RESEARCH_LEAD | — |
| NEEDS_TASK_SLICING | RUN_TASK_SLICER | — |
| READY_FOR_NEXT_TASK | RUN_EXECUTOR (если есть READY-задача) | EXECUTING_TASK |
| READY_FOR_NEXT_TASK | NO_READY_TASK (нет задач) | — |
| EXECUTING_TASK | RUN_EXECUTOR | — |
| NEEDS_CHANGE_REVIEW | RUN_CHANGE_REVIEWER | — |
| NEEDS_GATE | RUN_GATEKEEPER | — |
| GATE_FAILED + fixable | RUN_EXECUTOR | EXECUTING_TASK |
| GATE_FAILED + needs research | RUN_RESEARCHER | NEEDS_RESEARCH |
| GATE_FAILED + human blocker | HUMAN_DECISION | HUMAN_DECISION_REQUIRED |
| REVIEW_FAILED | RUN_EXECUTOR | EXECUTING_TASK |
| SAFE_CHECKPOINT (humanRequired=false) | CONTINUE_LOOP | READY_FOR_NEXT_TASK |
| SAFE_CHECKPOINT (humanRequired=true) | HUMAN_DECISION | HUMAN_DECISION_REQUIRED |
| HUMAN_DECISION_REQUIRED | STOP | — |
| DONE | STOP | — |

### `write-event`

Аппендит событие в `events.jsonl`. Генерирует `eventId` (`evt-NNNNNN`) и timestamp.

```bash
bash scripts/write-event.sh -w .teamloop \
  --type "RUN_STARTED" \
  --actor "supervisor" \
  --summary "Starting run for task-001" \
  --run-id "run-001" \
  --task-id "task-001"
```

Обязательные параметры: `--type`, `--actor`, `--summary`
Опциональные: `--run-id`, `--task-id`, `--data` (JSON)

Выводит созданное событие в stdout.

### `check-scope`

Проверяет git-изменения против `allowedWrites` и `forbiddenWrites` из `scope-policy.json` и `current-task.json`.

```bash
bash scripts/check-scope.sh --workspace ".teamloop"
```

Выводит JSON:
```json
{
  "schemaVersion": 1,
  "status": "PASS",
  "checks": [{"name": "scope", "status": "PASS", "summary": "All changes within scope"}],
  "violations": []
}
```

Exit code: `0` = PASS, `1` = FAIL

### `run-gates`

Запускает gate-чеки из `gate-policy.json`. Поддерживает `built-in` (scope) и `shell` (произвольные команды).

```bash
bash scripts/run-gates.sh --workspace ".teamloop"
```

Записывает `gate-result.json` в `runs/{currentRunId}/`. Выводит JSON в stdout.

---

## JSON Схемы (schemas/)

| Файл | Описание |
|---|---|
| `team-state.schema.json` | Состояние команды: status, phase, task/run ID |
| `task.schema.json` | Задача: scope, allowedWrites, successCriteria |
| `event.schema.json` | Событие: type, actor, timestamp, data |
| `run.schema.json` | Рун: runId, taskId, start/end time |
| `research-report.schema.json` | Отчёт исследования: findings, categories |
| `research-review.schema.json` | Review исследования: status, comments |
| `change-review.schema.json` | Review изменений: status, violations |
| `gate-result.schema.json` | Результат gate: checks, overall status |
| `blocker.schema.json` | Блокер: category, evidence, questions |
| `profile.schema.json` | Профиль задачи: scope defaults, gate commands |

---

## Профили

Профиль — это набор дефолтов для категории задач. Расположен в `profiles/{name}/profile.json`.

Структура:
```json
{
  "profileId": "generic-software-task",
  "defaultAllowedWrites": ["src/**", "tests/**", ".teamloop/**"],
  "defaultForbiddenWrites": [".git/**", "node_modules/**"],
  "discoveryQuestions": [
    {"id": "task_goal", "question": "What should be changed?", "required": true}
  ],
  "gateCommands": [
    {"name": "scope", "type": "built-in", "required": true}
  ],
  "taskSlicing": {"defaultMaxFilesPerTask": 5, "defaultMaxRisk": "medium"},
  "completionCriteria": [
    "All tasks in backlog are DONE or CANCELLED",
    "All required gates pass",
    "No unresolved blockers"
  ]
}
```

---

## OpenCode Adapter

Адаптер подключает YourAITeam к OpenCode как набор ролей-агентов и команд.

### Установка

1. Скопируйте `adapters/opencode/opencode.jsonc` в корень проекта
2. Скопируйте `adapters/opencode/AGENTS.md` в корень проекта
3. Скопируйте `adapters/opencode/agents/` → `.opencode/agents/`
4. Скопируйте `adapters/opencode/commands/` → `.opencode/commands/`

### Команда `/supervised-task`

```
/supervised-task           # Начать или продолжить supervised-луп
/supervised-task status    # Текущее состояние
/supervised-task continue  # Продолжить из SAFE_CHECKPOINT
/supervised-task research  # Принудительно перейти к исследованию
/supervised-task fix-gate  # Исправить проваленный gate
```

### Роли в OpenCode

Каждая роль — это markdown-файл с системной инструкцией. OpenCode подгружает инструкцию при маршрутизации.

---

## Тестирование

### PowerShell (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File tests\run-tests.ps1
```

### Bash (Linux/macOS/WSL)

```bash
bash tests/run-tests.sh
```

### Ключи покрытия (21 тест)

| # | Тест | Что проверяет |
|---|---|---|
| 1 | InitWorkspace_CreatesValidState | Структура workspace, JSON, статус NEW |
| 2 | ValidateState_FreshWorkspacePasses | Валидация чистого workspace |
| 3 | ValidateState_HumanRequiredWithoutBlockerFails | HUMAN_DECISION_REQUIRED требует blocker |
| 4 | NextAction_NewWorkspaceNeedsDiscovery | NEW → RUN_DISCOVERY |
| 5 | NextAction_ReadyTaskRunsExecutor | READY_FOR_NEXT_TASK → RUN_EXECUTOR |
| 6 | NextAction_ResearchRejectedRoutesToResearcher | NEEDS_RESEARCH → RUN_RESEARCHER |
| 7 | NextAction_GateFailedFixableRoutesToExecutor | GATE_FAILED + FIX → RUN_EXECUTOR |
| 8 | WriteEvent_CreatesValidEvent | Создание события, eventId, events.jsonl |
| 9 | ScopeGuard_AllowsAllowedWrites | check-scope PASS без изменений |
| 10 | TaskSlicer_RejectsTaskWithoutScope | Задача без scope — reject |
| 11 | ResearchLead_RejectsCountMismatch | Несоответствие findings count |
| 12 | Completion_DoneRequiresNoOpenTasks | DONE с open задачами — ошибка |
| 13 | GateRunner_RequiredFailFailsOverall | required gate fail → FAIL |
| 14 | GateRunner_OptionalFailDoesNotFailOverall | optional gate fail → PASS |
| 15-21 | Golden prompt tests | Проверка invariant-текстов в prompt-файлах |

---

## Скрипты: совместимость

| Скрипт | PowerShell | Bash |
|---|---|---|
| init-workspace | .ps1 (PS 5.1) | .sh (Bash 4+) |
| validate-state | .ps1 (PS 5.1) | .sh (Bash 4+, python3 или jq) |
| next-action | .ps1 (PS 5.1) | .sh (Bash 4+, python3 или jq) |
| write-event | .ps1 (PS 5.1) | .sh (Bash 4+) |
| check-scope | .ps1 (PS 5.1) | .sh (Bash 4+, git) |
| run-gates | .ps1 (PS 5.1) | .sh (Bash 4+, python3 или jq) |

Bash-скрипты используют `python3` в первую очередь, с fallback на `jq`. Если ни один не доступен — скрипты работают с упрощённым парсингом.

---

## Как это работает (End-to-End)

```
1. /supervised-task "Добавить фильтрацию пользователей"
   │
2. Supervisor читает team-state.json → NEW / NEEDS_DISCOVERY
   │
3. Supervisor задаёт discovery-вопросы из профиля
   │
4. Transition → NEEDS_PLAN
   │
5. Supervisor/Researcher создаёт research-задачу
   │
6. Transition → NEEDS_RESEARCH
   │
7. Researcher собирает findings → research/{id}.json
   │
8. Transition → NEEDS_RESEARCH_REVIEW
   │
9. Research Lead проверяет findings
   │  ├─ APPROVED → NEEDS_TASK_SLICING
   │  └─ REQUEST_CHANGES → NEEDS_RESEARCH
   │
10. Task Slicer разбивает findings на задачи → backlog.jsonl
    │
11. Transition → READY_FOR_NEXT_TASK
    │
12. Executor берёт READY-задачу → EXECUTING_TASK
    │
13. Executor выполняет → NEEDS_CHANGE_REVIEW
    │
14. Change Reviewer проверяет → APPROVED / REJECTED
    │  ├─ APPROVED → NEEDS_GATE
    │  └─ REJECTED → REVIEW_FAILED → EXECUTING_TASK
    │
15. Gatekeeper запускает gate-чеки
    │  ├─ ALL PASS → SAFE_CHECKPOINT → READY_FOR_NEXT_TASK (loop)
    │  ├─ FIXABLE FAIL → GATE_FAILED → EXECUTING_TASK
    │  └─ HUMAN BLOCKER → HUMAN_DECISION_REQUIRED
    │
16. Когда все задачи DONE, все gate PASS, нет blockers:
    │
17. → DONE (валидация проходит только если backlog пуст или все DONE)
```

---

## Файлы-контакты

| Что сделать | Какой файл/скрипт |
|---|---|
| Создать workspace | `scripts/init-workspace.*` |
| Проверить состояние | `scripts/validate-state.*` |
| Узнать следующее действие | `scripts/next-action.*` |
| Записать событие | `scripts/write-event.*` |
| Проверить scope | `scripts/check-scope.*` |
| Запустить gate-чеки | `scripts/run-gates.*` |
| Добавить новый профиль | `profiles/{name}/profile.json` |
| Изменить роль-агент | `adapters/opencode/agents/{role}.md` |
| Запустить тесты | `tests/run-tests.ps1` / `tests/run-tests.sh` |
