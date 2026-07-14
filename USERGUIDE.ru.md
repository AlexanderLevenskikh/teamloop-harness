# Руководство пользователя YourAITeam

Практическое руководство для первого запуска YourAITeam без предварительного чтения всей спецификации рантайма.

> Начинаете впервые? Используйте **OpenCode + профиль `standard`**. Для мелких правок выбирайте обычный Build, для обсуждения команды и бюджета — `/your-ai-team`, для контролируемого выполнения — `/supervised-task`.

## 1. Три разных вещи, которые легко назвать «режимом»

В YourAITeam есть три независимых уровня. Они связаны, но это не одна настройка.

### A. Режим работы

| Режим | Что делает | Когда применять |
|---|---|---|
| **Обычный Build** | Обычный диалог с coding-агентом. Lifecycle `.teamloop` не обязателен. | Маленькие правки, эксперименты, ремонт самого YourAITeam. |
| **Проектирование команды** (`/your-ai-team`) | Предлагает роли, их уровень и бюджет токенов. Не начинает реализацию до принятия предложения. | Дорогие, неоднозначные задачи и торг по бюджету. |
| **Контролируемое выполнение** (`/supervised-task`) | Ведёт задачу через долговечное состояние `.teamloop`, review, gates, sentinel и финальные проверки. | Многошаговые, рискованные или долгие задачи. |

### B. Профиль исполнения

Профиль меняет количество церемонии и конечный бюджет улучшений. Он не отключает жёсткие проверки качества.

| Профиль | Обычное поведение | Бюджет boundary-улучшений |
|---|---|---:|
| `fast` | Минимальная команда, review по триггерам. | 2 цикла |
| `standard` | Executor + reviewer; рекомендуемый вариант по умолчанию. | 4 цикла |
| `audit` | Самые строгие требования к reviewer, watchdog и sentinel. | 6 циклов |

Запрошенный `fast` может автоматически повыситься до более строгого профиля, если затронуты защищённые файлы, найдены серьёзные проблемы или повторяется отсутствие прогресса.

### C. Решение Quality/Value Boundary Manager

После успешных детерминированных gate-проверок опциональная граница качества и ценности может всё ещё блокировать продвижение. Менеджер выбирает только один из разрешённых рантаймом исходов:

- `ACCEPT_BOUNDARY` — ограниченный результат достаточно хорош;
- `ACCEPT_WITH_RECORDED_SOFT_DEBT` — результат принят с явным списком некритичного долга;
- `IMPROVE_CURRENT_BOUNDARY` — выполнить ровно одно самое ценное ограниченное улучшение;
- `SPLIT_CURRENT_BOUNDARY` — текущий объём слишком велик и его нужно разделить;
- `STOP_BUDGET_EXHAUSTED` — честно остановиться после исчерпания бюджета или лимита no-progress;
- `REQUEST_HUMAN_DECISION` — требуется настоящее решение пользователя.

Менеджер не может отменить hard gate или самостоятельно выписать себе acceptance receipt.

---

## 2. Что потребуется

Рекомендуется:

- Python 3.10 или новее;
- Git;
- OpenCode для интерактивного сценария;
- PowerShell 7 (`pwsh`) на Windows;
- Bash на Linux/macOS, Git Bash или WSL.

Проверьте инструменты:

```powershell
python --version
git --version
pwsh --version
opencode --version
```

В Linux/WSL:

```bash
python3 --version
git --version
opencode --version
```

После распаковки ZIP в Linux/WSL восстановите executable-права:

```bash
bash scripts/install.sh
```

PowerShell-скриптам `.ps1` Unix-права не нужны.

---

## 3. Разместите YourAITeam в корне проекта

В текущей alpha-версии файлы YourAITeam должны лежать в корне репозитория, из которого запускается OpenCode.

В этой же директории должны находиться:

```text
AGENTS.md
opencode.jsonc
.opencode/
scripts/
schemas/
templates/
tests/
```

Копируйте в корень проекта полный пакет YourAITeam, а не только отдельные wrapper-скрипты. Папка `templates/` нужна для создания нового workspace `.teamloop`, а `tests/` — для проверки установки и будущих обновлений. Если одной из этих папок нет, инициализация или проверка могут быть неполными, даже если сами скрипты на месте.

Не запускайте OpenCode на уровень выше или ниже.

Проверка на Windows:

```powershell
Get-Location
Get-ChildItem -Force
Get-ChildItem -Force .opencode\agents, .opencode\commands
opencode agent list
```

После изменения агентов или команд полностью перезапускайте OpenCode.

---

## 4. Инициализируйте долговечный workspace

Состояние рантайма хранится в `.teamloop/`.

### Windows

```powershell
.\scripts\init-workspace.ps1 -Workspace ".teamloop" -Profile "generic-software-task"
.\scripts\validate-state.ps1 -Workspace ".teamloop"
```

### Bash / WSL

```bash
bash scripts/init-workspace.sh --workspace .teamloop --profile generic-software-task
bash scripts/validate-state.sh --workspace .teamloop
```

Не редактируйте вручную runtime-owned JSON/JSONL-файлы в `.teamloop/state`. Используйте команды и переходы рантайма.

`generic-software-task` — это **доменный профиль workspace**. Он не равен профилям исполнения `fast`, `standard` и `audit`.

---

## 5. Рекомендуемый пользовательский путь в OpenCode

### Путь 1 — маленькая обычная задача

Выберите агента **Build**.

Примеры:

- переименовать переменную;
- посмотреть файл;
- сделать маленькую локальную правку;
- ремонтировать сам YourAITeam без self-hosting.

В текущей alpha-конфигурации основным агентом по умолчанию является `orchestrator`. Если lifecycle не нужен, перед отправкой промпта переключитесь на **Build** клавишей `Tab`.

Slash-команда не включает вечный режим. Если обычный промпт продолжает старый supervised-run, скорее всего, активным primary agent остался `orchestrator` либо текущая `.teamloop` всё ещё содержит незавершённое состояние.

### Путь 2 — подобрать команду и поторговаться

В OpenCode:

```text
/your-ai-team Исправь flaky Playwright-тест и уложись в 25 тысяч токенов
```

Team manager должен предложить:

- минимально достаточные роли;
- уровни ролей (`economy`, `balanced`, `premium`);
- оценочный диапазон токенов;
- риски удаления или удешевления ролей.

Можно ответить обычным языком:

```text
Оставь reviewer только на финальную проверку и уложись в 20 тысяч токенов.
```

После торга явно примите предложение.

Важно: проектирование команды ещё не означает, что реализация началась. В alpha-версии материализованные файлы команды являются артефактами; уже открытая сессия OpenCode не подхватывает новую команду на лету.

### Путь 3 — контролируемое выполнение

Запуск:

```text
/supervised-task Реализуй изменение с профилем standard.
```

Продолжение существующего run:

```text
/supervised-task Продолжи текущий run.
```

Оркестратор должен подчиняться рантайму:

```text
next-action
→ одно ограниченное действие роли
→ scope и contract checks
→ измерение прогресса
→ review/gates при необходимости
→ sentinel/final gate перед handoff
```

Он не должен безусловно запускать все роли или бесконечно повторять одно и то же действие без прогресса.

---

## 6. Как выбрать профиль

По умолчанию выбирайте `standard`.

### `fast`

Подходит для:

- маленьких и хорошо понятных изменений;
- низкорискового scope;
- задач с сильными детерминированными тестами.

Он уменьшает церемонию, но не требования качества. Финальные hard checks остаются включёнными.

### `standard`

Подходит для:

- обычных багфиксов и фич;
- изменений, которым полезен reviewer;
- большинства повседневных задач.

### `audit`

Подходит для:

- рантайма, permissions, security, CI и релизов;
- широких рефакторингов;
- задач, где дорого принять stale evidence или подменённые результаты;
- изменений защищённой части самого YourAITeam.

Профили влияют на routing ролей и максимальное число boundary-улучшений, но не разрешают принять результат с hard failures.

---

## 7. Что происходит на границе качества и ценности

Boundary management в текущей alpha-версии **включается отдельно для конкретной задачи/run**. Задача без boundary contract сохраняет совместимый старый путь от gate к checkpoint.

С boundary contract:

```text
gates PASS
→ NEEDS_BOUNDARY_DECISION
→ повторное измерение текущих артефактов
→ менеджер выбирает разрешённое действие
→ рантайм проверяет цепочку receipts
→ продвижение разблокируется или остаётся закрытым
```

Обычному пользователю лучше позволить интеграции создавать и обслуживать boundary. Низкоуровневые команды полезны для диагностики:

```powershell
.\scripts\boundary-measure.ps1 -Workspace .teamloop --boundary-id boundary-001
.\scripts\boundary-status.ps1 -Workspace .teamloop --boundary-id boundary-001
.\scripts\boundary-verify.ps1 -Workspace .teamloop --boundary-id boundary-001
.\scripts\boundary-lock-status.ps1 -Workspace .teamloop
```

Bash-аналоги используют `.sh` и параметр `--boundary-id`.

### HTML-dashboard

```powershell
python scripts/teamloop-core.py boundary-status `
  --workspace .teamloop `
  --boundary-id boundary-001 `
  --format html `
  --output boundary-dashboard.html
```

Dashboard отдельно показывает:

- широкое draft-покрытие;
- действительно принятый прогресс с receipt;
- hard blockers;
- корневые проблемы и ожидаемый payoff;
- оставшийся бюджет улучшений;
- необходимость решения человека.

Dashboard только отображает данные и не является источником acceptance authority.

---

## 8. Детерминированный CLI-сценарий подбора команды

Team composer можно использовать без OpenCode.

### PowerShell

```powershell
.\scripts\your-ai-team.ps1 propose `
  --backend opencode `
  --task "Исправь flaky Playwright-тест" `
  --max-tokens 35000 `
  --output .teamloop\team\proposal.json

.\scripts\your-ai-team.ps1 negotiate `
  --proposal .teamloop\team\proposal.json `
  --request "Уложись в 25000 токенов; reviewer только в конце" `
  --output .teamloop\team\proposal-2.json

.\scripts\your-ai-team.ps1 accept `
  --proposal .teamloop\team\proposal-2.json `
  --output .teamloop\team\accepted.json

.\scripts\your-ai-team.ps1 materialize `
  --proposal .teamloop\team\accepted.json `
  --backend opencode `
  --output-dir .teamloop\generated\opencode
```

### Bash

```bash
bash scripts/your-ai-team.sh propose \
  --backend opencode \
  --task "Исправь flaky Playwright-тест" \
  --max-tokens 35000 \
  --output .teamloop/team/proposal.json
```

Затем аналогично выполните `negotiate`, `accept` и `materialize`.

Для Codex используйте `--backend codex`.

---

## 9. Какие статусы можно увидеть

| Статус | Значение |
|---|---|
| `DONE` | Полный запрошенный результат прошёл необходимую цепочку проверок. |
| `SAFE_CHECKPOINT` | Состояние безопасно для продолжения, но весь проект не обязательно завершён. |
| `NEEDS_BOUNDARY_DECISION` | Gates прошли, но продвижение ещё заблокировано boundary-решением. |
| `HUMAN_DECISION_REQUIRED` | Требуется классифицированное решение пользователя. |
| `BLOCKED` | Безопасно продолжить работу по текущему контракту нельзя. |
| `STOPPED_BUDGET_EXHAUSTED` | Конечный бюджет улучшений честно исчерпан. |
| `PARTIAL_WITH_DEBT` / `DRAFT_WITH_LIMITATIONS` | Полезный частичный результат есть, но это не полный успех. |

Помните:

```text
SAFE_CHECKPOINT != DONE
TICKET_CLOSED != USER_VALUE_ACCEPTED
```

---

## 10. Продолжение после перезапуска или compaction

Источником истины является `.teamloop`, а не краткий пересказ чата.

После перезапуска OpenCode:

1. запустите его из того же корня проекта;
2. сохраните ту же директорию `.teamloop`;
3. выберите `orchestrator`;
4. выполните:

```text
/supervised-task Продолжи текущий run из долговечного состояния.
```

Для диагностики:

```powershell
.\scripts\validate-state.ps1 -Workspace .teamloop
.\scripts\next-action.ps1 -Workspace .teamloop
```

Не «чините» состояние ручным изменением статусов, receipts, counters или evidence.

---

## 11. Типовые проблемы

### «Отправил обычный промпт, а агент продолжает говорить о старом run»

Вероятно, активным primary agent остался `orchestrator`, который обязан следовать `.teamloop`.

- переключитесь на **Build** клавишей `Tab`;
- создайте новую сессию OpenCode для обычной работы;
- не используйте продолжение старой сессии, если нужен чистый контекст;
- проверьте корень репозитория и активную `.teamloop`.

### «Final gate прошёл, но часть проверок была пропущена»

Смотрите раздельные счётчики. `PASS`, `SKIP`, `NOT_REQUIRED` и `UNAVAILABLE` — разные результаты. Общий PASS не означает, что каждая проверка реально запускалась.

### «Gate прошёл, но задача всё ещё заблокирована»

Для неё существует boundary contract. Проверьте:

```powershell
.\scripts\boundary-lock-status.ps1 -Workspace .teamloop
```

Затем откройте boundary packet и решение менеджера.

### «Агент заявил, что улучшение готово, а рантайм пишет NO_PROGRESS»

Рантайм сравнивает авторитетные before/after measurements. Изменение комментариев, counters, отчётов или status labels не считается улучшением результата.

### «В Windows агент внезапно расследует WSL-пути»

Используйте wrapper family той среды, которой принадлежит checkout:

- нативный Windows/OpenCode: `scripts/*.ps1`;
- Linux или репозиторий, физически расположенный внутри WSL: `scripts/*.sh`.

Не запускайте WSL Bash поверх Windows-checkout только ради проверок YourAITeam. Пути `C:\...` и `/mnt/c/...` корректны каждый в своей среде, но при смешивании создают ложные диагностические ветки. Перед сменой shell сначала смотрите sentinel `cacheSummary`.

### «PowerShell выдаёт ошибки кодировки или parser error»

Используйте PowerShell 7 (`pwsh`) и ASCII-safe wrappers из текущего релиза. Не сохраняйте `.ps1` в устаревшей кодировке.

---

### «Sentinel упал, хотя исходная проблема уже исправлена»

Теперь sentinel сначала выполняет детерминированный cache-preflight. В JSON-результате есть `cacheSummary`:

- `CACHE_BYPASSED` — cache повреждён/невалиден, поэтому проверки выполнены fresh;
- `STALE_ENTRY_RECOMPUTED` — закэшированный WARNING/CRITICAL изменился при автоматической свежей перепроверке;
- `CACHE_EMPTY` — переиспользуемых записей не было;
- `CACHE_READY` — обычное безопасное переиспользование.

Не начинайте с расследования WSL-путей, кавычек и ручного удаления cache. Сначала посмотрите:

```powershell
$result = .\scripts\run-sentinel.ps1 -Workspace .teamloop | ConvertFrom-Json
$result.cacheSummary
```

Свежий PASS авторитетен. `cache-clear` нужен как явная recovery-операция, а не как стандартный ритуал диагностики.

### Проверка всех поставляемых скриптов

Запускайте единый валидатор после копирования/обновления YourAITeam и при изменениях в `scripts/` или test launchers:

```powershell
.\scripts\validate-scripts.ps1 -Root .
```

```bash
bash scripts/validate-scripts.sh --root .
```

Он проверяет все PowerShell-, Bash-, Python-скрипты и extensionless wrappers. Отсутствующий PowerShell/Bash честно отображается как `UNAVAILABLE`, а доступные статические проверки всё равно выполняются.

---

## 12. Рекомендуемый первый запуск

Для обычной задачи в репозитории:

1. инициализируйте `.teamloop`;
2. запустите OpenCode из корня репозитория;
3. при необходимости вызовите `/your-ai-team <задача>` и поторгуйтесь за состав/бюджет;
4. запустите `/supervised-task <задача> с профилем standard`;
5. позвольте рантайму выполнять по одному ограниченному действию;
6. при блокировке продвижения откройте boundary status;
7. считайте задачу завершённой только после final gate и актуальной receipt chain.

Этот путь даёт лучший баланс между полезной автономностью и честной остановкой.

## Дополнительная документация

- [YourAITeam MVP](YOUR_AI_TEAM.md)
- [Настройка OpenCode](OPENCODE_SETUP.md)
- [Справочник рантайма](RUNTIME.md)
- [Fast / standard / audit](docs/FAST_EXECUTION.md)
- [Quality/value boundary](docs/QUALITY_VALUE_BOUNDARY.ru.md)
- [Тестирование](TESTING.md)
