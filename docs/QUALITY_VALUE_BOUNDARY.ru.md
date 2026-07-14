# Управление границей качества и ценности

В YourAITeam 0.5 между успешными gate-проверками и продвижением workflow появляется детерминированная граница качества/ценности.

## Зачем она нужна

Автономная разработка часто падает в одну из двух крайностей:

- **бесконечная тщательность** — исследование, ревью и исправления повторяются без разумного условия остановки;
- **жадное завершение** — тикеты и стадии закрыты, но в результате остались заглушки, потерянные проверки, блокирующий долг или подменённые evidence.

Главный инвариант:

```text
Жёсткие проверки определяют, что запрещено.
Граничный менеджер выбирает наиболее ценное действие только среди разрешённых вариантов.
```

Менеджер не может ослабить hard gate, менять код или evidence, редактировать бюджет и policy либо разблокировать продвижение одним собственным JSON.

## Место в lifecycle

Для задачи с boundary contract:

```text
execute -> review -> deterministic gates PASS
        -> NEEDS_BOUNDARY_DECISION
        -> quality-value-manager
        -> runtime receipt -> advancement
```

Gate PASS необходим, но недостаточен. Task и run остаются активными, пока runtime не проверит актуальный acceptance receipt.

## Boundary packet

Команда `boundary-measure` заново вычисляет факты из первичных артефактов:

- ожидаемые и фактические deliverables;
- hard invariant failures и видимый soft debt;
- validation evidence для текущего input;
- корневые паттерны и каскадные симптомы;
- before/after delta;
- стоимость, уверенность, охват, повторяемость и payoff;
- остаток бюджета и no-progress streak;
- fingerprints артефактов, policy, config, tools и evidence.

Редактируемые отчёты и счётчики агента не являются источником приёмки.

## Закрытая модель решений

```text
ACCEPT_BOUNDARY
ACCEPT_WITH_RECORDED_SOFT_DEBT
IMPROVE_CURRENT_BOUNDARY
SPLIT_CURRENT_BOUNDARY
STOP_BUDGET_EXHAUSTED
REQUEST_HUMAN_DECISION
```

Runtime отклоняет невозможные решения. Нельзя принять результат с hard failures. Soft-debt acceptance требует явного списка долга. Improvement требует выбранного авторитетного кандидата. Budget stop допускается только при исчерпанном бюджете или достигнутом пороге no-progress.

## Профили

| Профиль | Циклы улучшения |
|---|---:|
| fast | 2 |
| standard | 4 |
| audit | 6 |

Профиль меняет объём церемонии и конечный бюджет, но не снижает жёсткие требования качества.

## Приоритет исправлений

```text
expected payoff =
  affected items
  x repetition/reuse
  x blocking severity
  x confidence of a safe fix
  / estimated cost
```

Корневое переиспользуемое исправление должно обгонять локальную косметику, если его измеренный payoff выше.

## Доверенная история и receipt

История решений и улучшений hash-chained и проверяется fail-closed. Acceptance receipt связан с текущими артефактами, metrics, policy/config, версиями runtime/tools, validation evidence, manager role receipt и всей цепочкой предшествующих границ.

Drift артефактов, скопированное evidence, replay receipt, редактирование истории или изменение ранней границы снова блокируют продвижение.

## Команды

```bash
bash scripts/boundary-create.sh --workspace .teamloop --contract boundary.json
bash scripts/boundary-measure.sh --workspace .teamloop --boundary-id boundary-001
bash scripts/boundary-status.sh --workspace .teamloop --boundary-id boundary-001
bash scripts/boundary-decide.sh --workspace .teamloop --boundary-id boundary-001 \
  --decision ACCEPT_BOUNDARY --reason "Ограниченный результат соответствует контракту"
bash scripts/boundary-verify.sh --workspace .teamloop --boundary-id boundary-001
bash scripts/boundary-lock-status.sh --workspace .teamloop --boundary-id boundary-001
```

После `IMPROVE_CURRENT_BOUNDARY` выполняется ровно одно выбранное ограниченное улучшение, затем результат повторно измеряется через `boundary-complete-improvement`.

Пример domain adapter находится в `adapters/generic-software-task/`.

## Контракт доверенного writer

Acceptance receipt и role receipt создаёт только `teamloop-core`; менеджер не может записывать их напрямую. Политика фиксирует `trustedWriterCommand=teamloop-core`, `managerMayWriteReceipts=false`, `requireManagerRoleReceipt=true` и `historyMode=append-only-hash-chain`. Runtime дополнительно пересчитывает fingerprint текущих primary artifacts и evidence, поэтому один self-hash никогда не является достаточным основанием для acceptance.

## Read-only dashboard границы

```bash
python scripts/teamloop-core.py boundary-status --workspace .teamloop --boundary-id <id> --format html --output boundary-dashboard.html
```

HTML без внешних зависимостей показывает accepted progress отдельно от draft coverage и содержит контекстные `?`-подсказки. Это только presentation surface: authority остаётся у primary artifacts, authoritative packet и проверки receipt chain.
