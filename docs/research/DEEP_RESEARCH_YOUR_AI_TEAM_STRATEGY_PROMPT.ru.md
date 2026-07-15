# Deep Research Prompt — YourAITeam: архитектура, экономика и пути развития

Проведи глубокое независимое исследование проекта **YourAITeam** — детерминированного runtime и конструктора ограниченных AI-команд для Codex, OpenCode и будущих agent backends.

Текущая дата исследования: июль 2026 года. Используй самые свежие доступные источники и явно указывай дату каждого существенного факта.

## Контекст проекта

YourAITeam решает две связанные задачи:

1. До начала дорогой работы предлагает минимально достаточную команду AI-ролей, оценивает token budget, coordination overhead и остаточные риски, позволяет пользователю торговаться за состав и стоимость.
2. Во время выполнения не даёт агентам бесконечно улучшать результат или формально «закрывать тикеты» при плохом фактическом качестве. Детерминированные gates измеряют факты, Quality/Value Boundary Manager выбирает одну разрешённую развилку, а runtime физически блокирует продвижение без валидного acceptance receipt.

Основные принципы:

```text
Hard checks define what is forbidden.
Boundary management chooses the highest-value option among what remains allowed.

TICKET_CLOSED != USER_VALUE_ACCEPTED
SAFE_CHECKPOINT != DONE
EXPECTATION != REALITY
```

Текущие составляющие:

- роли delivery-manager, explorer, researcher, architect, implementer, reviewer, verifier, quality-value-manager и другие;
- градации economy / balanced / premium;
- execution profiles fast / standard / audit;
- детерминированные proposal → negotiation → acceptance → materialization;
- adapters для OpenCode и Codex;
- durable `.teamloop` state, gates, sentinel, no-progress, boundary receipts и final gate;
- Codex custom agents, project skill, model compatibility mode и Codex doctor;
- OpenCode primary/subagent roles и permission map;
- CLI-first workflow и read-only HTML dashboard.

Недавний реальный smoke-test Codex подтвердил, что custom agent thread запускается, но старая генерация пинила `gpt-5.6`, который оказался недоступен конкретному ChatGPT-account Codex. Это привело к переходу на безопасный default: наследовать модель родительского Codex turn, а конкретные модели пинить только при подтверждённой совместимости.

## Главная цель исследования

Определи, насколько удачны текущие архитектурные решения и экономическая модель YourAITeam, какие существующие проекты и исследования дают лучшие паттерны, и какой путь развития создаст реальную пользовательскую ценность без превращения системы в дорогой церемониальный оркестратор.

Исследование должно ответить:

> Где YourAITeam действительно экономит деньги, время и контекст, а где координация команды стоит дороже одного сильного агента?

> Какие шаблоны команд, менеджмента качества и lifecycle стоит сохранить, изменить или удалить?

> Каким должен быть следующий продуктовый интерфейс: CLI, skill/plugin, IDE integration, desktop control plane, web dashboard или комбинация?

> Какие logical capabilities дадут максимальный reusable payoff в ближайших трёх итерациях?

## Обязательные направления исследования

### 1. Карта современного рынка и решений

Изучи актуальные возможности и практики:

- OpenAI Codex: custom agents, subagents, skills, plugins, hooks, rules, permissions, app server, SDK, non-interactive mode, worktrees, model discovery и authentication modes;
- OpenCode и его agent/command/permission модель;
- Claude Code, Cursor, GitHub Copilot coding agent, Gemini CLI, Aider, Devin и другие актуальные coding agents;
- agent orchestration frameworks и control planes: LangGraph, AutoGen, CrewAI, OpenAI Agents SDK, Temporal-подобные durable workflows, state-machine и event-sourced подходы;
- multi-agent research о coordination overhead, context rot, parallelism, delegation quality, verifier/reviewer loops, stopping conditions и reward hacking;
- системы evals, policy enforcement, provenance, receipts, append-only ledgers и artifact-bound validation.

Не составляй каталог ради каталога. Для каждого решения укажи:

- какой конкретный паттерн полезен YourAITeam;
- какие ограничения или неудачные идеи не стоит копировать;
- уровень зрелости и стабильности API;
- применимость к Codex, OpenCode и portable core;
- стоимость интеграции и ожидаемый payoff.

### 2. Экономика AI-команды

Построй честную unit-economics модель.

Разделяй:

- input tokens;
- cached input;
- output tokens;
- reasoning/compute или credits, если провайдер использует другую модель расчёта;
- coordination overhead;
- повторное чтение одинакового контекста разными ролями;
- стоимость failed/retried threads;
- latency и wall-clock cost;
- стоимость человеческого review;
- выгоду от предотвращённого дефекта;
- выгоду от reusable root fix;
- стоимость context pollution в основном thread;
- subscription credits против API billing.

Проанализируй минимум следующие сценарии:

1. Один сильный агент выполняет маленький bugfix.
2. Delivery manager + один economy worker.
3. Explorer + implementer + verifier.
4. Полная команда для cross-cutting/high-risk изменения.
5. Параллельные read-only subagents.
6. Параллельные write agents с конфликтами.
7. Повторный цикл после NO_PROGRESS.
8. Boundary Manager принимает хороший результат без дополнительного цикла.
9. Boundary Manager выбирает один reusable root fix вместо множества leaf fixes.
10. Команда останавливается с ограничениями вместо бесконечного улучшения.

Для каждого сценария оцени:

- break-even point;
- диапазон токенов/стоимости;
- coordination ratio;
- ожидаемую экономию времени;
- вероятность повышения качества;
- риск ложной экономии;
- чувствительность к стоимости модели и размеру контекста.

Не выдавай точные деньги без источника. Где нет надёжных данных, используй интервалы и sensitivity analysis.

### 3. Ревью текущих шаблонов команд

Оцени текущий role catalog и правила формирования команды.

Отдельно проверь:

- всегда ли нужен delivery-manager;
- всегда ли для mutating task нужен quality-value-manager;
- где менеджеры могут стоить дороже самой работы;
- стоит ли объединять роли для малых задач;
- когда explorer реально защищает main context;
- когда reviewer и verifier дублируют друг друга;
- насколько оправданы architect и senior-engineer как разные роли;
- нужны ли writer, visual-checker и security-reviewer как постоянные шаблоны;
- корректны ли engagement modes full / final-only;
- удачны ли градации economy / balanced / premium;
- не смешиваются ли grade, reasoning effort, model choice и step budget;
- следует ли role grade определять модель жёстко или через capability/availability negotiation;
- нужны ли dynamic roles, или закрытый каталог безопаснее.

Для каждого шаблона выдай:

```text
KEEP
MODIFY
MERGE
SPLIT
REMOVE
EXPERIMENT
```

и обоснование с ожидаемым экономическим эффектом.

### 4. Ревью execution profiles

Проанализируй fast / standard / audit.

Ответь:

- должны ли профили менять только ceremony и budgets, сохраняя hard quality thresholds;
- какие роли и проверки можно объединять в fast;
- какие доказательства обязательны всегда;
- нужен ли отдельный exploratory/prototype profile;
- нужен ли emergency/incident profile;
- стоит ли профиль выбирать детерминированно, предлагать пользователю или разрешить negotiation;
- как не превратить fast в loophole;
- как calibrate improvement cycle limits 2/4/6;
- как учитывать реальную стоимость моделей, размер diff, risk и blast radius.

Предложи улучшенную таблицу профилей и конкретные policy fields.

### 5. Логическая архитектура

Оцени текущую границу:

```text
deterministic measurement
→ managerial judgment
→ runtime enforcement
```

Исследуй и предложи лучшие решения для:

- authoritative boundary packet;
- root/cascade normalization;
- expected-payoff scoring;
- before/after measurement;
- no-progress detection;
- budget exhaustion;
- receipt chain и artifact drift;
- model/provider compatibility;
- restart/compaction recovery;
- team-contract versioning;
- cross-backend parity;
- adapter capability negotiation;
- learning from completed runs без unsafe self-modification;
- конфигурации, которая не позволяет текущему агенту ослабить свои gates;
- автоматического выявления infrastructure failures до расходования agent tokens;
- cache preflight и fresh retry;
- graceful degradation при недоступной модели/роли/tool.

Определи, где достаточно hashes и protected writers, а где нужна внешняя подпись, OS permissions, remote control plane или независимый verifier.

### 6. Полноценная интеграция с Codex

С учётом актуальной официальной документации Codex оцени:

- custom agent TOML;
- model inheritance и model discovery;
- ChatGPT auth против API-key auth;
- skills и plugins;
- project trust;
- AGENTS.md discovery;
- hooks и rules;
- permissions profiles;
- root thread как delivery manager;
- subagent depth/thread limits;
- app/CLI/IDE parity;
- worktrees для write-heavy parallelism;
- Codex SDK и app server;
- model/list и capability negotiation;
- live integration smoke/evals.

Предложи, какой уровень enforcement реально достижим внутри Codex, а что должно оставаться authority YourAITeam runtime.

Раздели рекомендации на:

- можно сделать сейчас стабильными API;
- experimental, допустимо за feature flag;
- не делать до стабилизации платформы.

### 7. Полноценная интеграция с OpenCode

Сравни текущую OpenCode-интеграцию с Codex и найди:

- где OpenCode предоставляет более сильные permissions;
- где current primary orchestrator загрязняет обычный режим;
- как изолировать `/supervised-task`;
- какие команды/roles должны быть primary или subagent;
- как избежать «залипшего оркестратора»;
- как синхронизировать adapter capabilities без lowest-common-denominator дизайна.

### 8. Интерфейсные пути развития

Сравни минимум пять продуктовых вариантов:

1. CLI-only expert tool.
2. Codex/OpenCode skill/plugin-first integration.
3. IDE panel.
4. Desktop multi-repository control plane.
5. Web dashboard / YourAITeam Inbox.
6. Hybrid local runtime + lightweight UI.

Для каждого оцени:

- целевую аудиторию;
- главное пользовательское действие;
- onboarding cost;
- time-to-first-value;
- observability;
- возможность торговаться за бюджет;
- human decision UX;
- implementation cost;
- maintenance burden;
- platform dependence;
- monetization potential;
- privacy/security implications.

Предложи один основной интерфейсный путь и один дешёвый промежуточный.

### 9. Метрики продукта и evals

Определи, как измерять успех YourAITeam.

Не использовать как главные метрики:

- количество ролей;
- количество generated files;
- количество закрытых tickets;
- количество workflow stages;
- длину agent run.

Предложи метрики вроде:

- accepted user value / total tokens;
- defect escape rate;
- false-green rate;
- unnecessary-role rate;
- coordination overhead ratio;
- time to authoritative blocker;
- time to accepted boundary;
- reusable root-fix payoff;
- no-progress cycles avoided;
- human interventions per task;
- stale-evidence detection rate;
- percentage small tasks completed by one worker;
- cost versus single-agent baseline;
- user acceptance/rework rate.

Разработай eval matrix с задачами:

- tiny docs fix;
- obvious bugfix;
- ambiguous bug;
- dependency upgrade;
- CI repair;
- architecture task;
- security-sensitive change;
- intentionally manipulated evidence;
- stale cache;
- unsupported model;
- interrupted/restarted run.

### 10. Риски и антицели

Проведи red-team анализ:

- orchestration theater;
- manager loops;
- agents optimizing metrics;
- policy inflation;
- excessive artifact generation;
- stale receipts;
- provider-specific lock-in;
- prompt drift;
- hidden cost multiplication;
- parallel-write conflicts;
- human fatigue from excessive decisions;
- dashboard creating false confidence;
- model availability changing after materialization;
- roles becoming anthropomorphic labels without measurable value.

Сформулируй kill criteria: при каких данных функцию, роль или весь подход следует удалить, а не продолжать улучшать.

## Требования к источникам

- Используй прежде всего официальную документацию, primary research papers, исходные репозитории и опубликованные технические материалы разработчиков систем.
- Для быстро меняющихся возможностей Codex/OpenCode обязательно проверяй актуальность на дату исследования.
- Для цен и моделей указывай регион, auth mode, plan/API и дату.
- Разделяй:
  - подтверждённый факт;
  - наблюдение из практики;
  - собственную гипотезу;
  - рекомендацию.
- Не опирайся на маркетинговые заявления без технического подтверждения.
- Приводи прямые ссылки и citations для всех существенных внешних утверждений.
- Если данных для точного экономического вывода нет, прямо сообщи об этом и используй диапазоны.

## Требуемые результаты

Подготовь один связный отчёт со следующими разделами:

1. **Executive summary** — максимум две страницы.
2. **Current architecture map** — фактическая схема YourAITeam и authority boundaries.
3. **Competitive/pattern matrix** — решения, полезные паттерны, стоимость интеграции.
4. **Economics model** — формулы, assumptions, сценарии, sensitivity analysis.
5. **Role-template review** — KEEP/MODIFY/MERGE/SPLIT/REMOVE/EXPERIMENT.
6. **Profile review** — улучшенные fast/standard/audit и дополнительные профили при необходимости.
7. **Codex strategy** — stable/experimental/defer.
8. **OpenCode strategy** — parity и platform-specific advantages.
9. **Interface strategy** — выбранный основной и промежуточный UX.
10. **Logical roadmap** — capabilities по ожидаемому payoff.
11. **Three-horizon roadmap**:
    - 0–6 недель;
    - 2–4 месяца;
    - 6–12 месяцев.
12. **Top 10 experiments** — дешёвые проверяемые гипотезы с success/failure criteria.
13. **Eval plan** — набор задач, baseline, метрики и способ сравнения.
14. **Risks and kill criteria**.
15. **Final recommendation** — что строить следующим, чего не строить и почему.

## Формат каждой рекомендации

Для каждой существенной рекомендации указывай:

```text
Recommendation
Problem solved
Evidence
Expected user value
Expected economic effect
Implementation cost
Risk
Dependencies
Smallest experiment
Success metric
Failure / kill criterion
Confidence
```

## Ограничение на размах

Не предлагай одновременно строить desktop app, web platform, marketplace, distributed orchestrator и новый agent framework.

Приоритизируй решения по:

```text
expected payoff
= user reach
× repetition/reuse
× severity of avoided failure
× confidence
÷ implementation and coordination cost
```

Главный результат исследования — не максимальное число идей, а **небольшой последовательный путь**, который докажет или опровергнет экономическую ценность YourAITeam.
