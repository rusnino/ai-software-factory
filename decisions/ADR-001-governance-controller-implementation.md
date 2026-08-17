# ADR-001: Governance Controller — собственная реализация вместо Temporal + OPA

## Статус

Принято.

## Контекст

В ходе ревью дизайна была предложена альтернативная архитектура Governance Layer на базе Temporal.io (workflow core) + Open Policy Agent (policy engine) + тонкий FastAPI gateway, взамен самостоятельно разрабатываемого Governance Controller (SPEC-03). Заявленный выигрыш: сокращение объёма кода ядра ~в 3 раза и промышленная надёжность к сбоям при сохранении полной изолированности архитектуры.

Это предложение было проверено основным дизайн-агентом и подтверждено данным ревью. Обнаружено, что выигрыш переоценён, а один найденный структурный риск (несовместимость pre-execution approval chain с моделью Temporal workflow) ставит под сомнение саму пригодность Temporal как "сердца Governance" для этой архитектуры.

## Рассмотренные варианты

### Вариант A: Temporal + OPA как замена Governance Controller

**Плюсы:**
- Durable execution, retries, timeouts — не нужно писать вручную.
- Human-in-the-loop через Signals — нативный примитив ожидания approval.
- Event History как встроенный источник audit trail.
- OPA — готовый декларативный policy engine, хорошо ложится на Task Contract / Project Profile валидацию (SPEC-03 §3.4–3.7).

**Минусы (решающие):**

1. **Структурная несовместимость с pre-execution approval chain.** Текущая state machine (`PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING`) требует одобрений **до** старта исполнения. Temporal signals/updates применимы только к уже запущенному workflow. Единственный жизнеспособный вариант — вести approvals вне Temporal (например, в PostgreSQL) и стартовать Temporal workflow только после `EXEC_APPROVED`. Но тогда Temporal перестаёт быть "governance authority" и становится execution-engine, применяемым уже после того, как governance-решение принято. Это меняет саму суть исходного предложения.

2. **Temporal Event History ≠ Governance Audit Log.** Запрос "кто и когда одобрил TASK-42" через workflow history traversal — не то же самое, что append-only compliance log с actor attribution. Tamper-evidence не является design-свойством Temporal history из коробки.

3. **"Сокращение кода в 3 раза" — misleading формулировка.** Temporal убирает часть кода (retry loops, state persistence, execution lifecycle tracking), но добавляет другой объём работы: workflow/activity definitions, интеграцию с Temporal service, worker pool, versioning strategy для workflow-кода, обратную совместимость, операционный мониторинг (Temporal Web UI, метрики), backup/restore persistence. Сложность не исчезает, а переносится из application-кода в distributed-systems операционку.

4. **Рост TCO и операционной поверхности.** Self-hosted Temporal Server требует PostgreSQL/Cassandra для persistence, отдельные сервисы (frontend/history/matching/worker), заметную операционную экспертизу. Elasticsearch обязателен только для advanced visibility — при масштабе этого проекта (небольшая команда, умеренный поток задач) можно обойтись без него, но остальной операционный вес остаётся. Это не drop-in зависимость по сравнению с текущим "один Python-сервис + PostgreSQL".

5. **Double-DAG и Plane-интеграция никуда не деваются.** Самые сложные части текущей архитектуры — маппинг Plane-зависимостей в opentasks runtime DAG, поддержание Plane как projection при сохранении authoritative-статуса за Controller, трансляция macro-agent workspace events обратно в state — остаются прикладной логикой независимо от того, живёт ли она в Temporal worker или в FastAPI-сервисе. Temporal не решает ни одну из них.

6. **Webhook-to-approval translation (SPEC-04 §4.3a) остаётся зоной ответственности gateway.** Строгие правила трансляции (human actor, eligible transition, non-bulk, policy validation, stale-state rejection) не предоставляются Temporal "из коробки" — их всё равно писать самостоятельно.

### Вариант B: Собственная реализация Governance Controller (Python/FastAPI/SQLAlchemy/PostgreSQL), OPA — опционально в Phase 2

**Плюсы:**
- Полный контроль над семантикой approval chain, audit log, reconciliation — без структурных компромиссов вида "часть логики вне Temporal".
- Меньше эксплуатируемых компонентов на Phase 1 (один сервис + Postgres против Controller + Temporal Server + persistence store + OPA).
- Объём работы предсказуем и оценён: ~4300–7000 строк продакшен-кода, ~57–79 инженеро-дней на разработку + сопоставимый объём на тесты — итого **3.5–5 месяцев** для одного senior backend-инженера до production-ready Phase 1, либо **6–9 недель** при двух инженерах, работающих параллельно по модулям, которые уже разграничены в SPEC-03/04/05.
- OPA может быть добавлен позже как замена embedded Policy Engine без переписывания остального Controller — это модульная, некритичная замена (SPEC-03 §3.4), а не архитектурная перестройка.

**Минусы:**
- Durable execution, retries, recovery после краша нужно писать и тестировать самостоятельно — Temporal делает это надёжнее "из коробки".
- Больше кастомного кода для команды поддерживать в долгосрочной перспективе.

## Решение

**Governance Controller реализуется как собственный сервис (Python/FastAPI/SQLAlchemy/PostgreSQL) согласно текущему SPEC-03, без Temporal на Phase 1.**

- **OPA** рассматривается как замена embedded Policy Engine в Phase 2 — модульное решение, не требующее переписывания State Machine/Approval Store/Audit Log.
- **Temporal** пересматривается не раньше Phase 3+, и только как компонент **внутри Execution Orchestration layer** (управление жизненным циклом одного уже одобренного execution), а не как замена Governance Controller. Условия пересмотра:
  - Подтверждена реальная потребность в долгоживущих (часы/дни) конкурентных execution-workflow на масштабе, где самописный retry/recovery код становится узким местом.
  - У команды есть операционная экспертиза для эксплуатации Temporal Server (persistence, versioning, мониторинг).
  - Governance-логика (approvals, policy, audit, Plane projection) остаётся владением Governance Controller независимо от того, что происходит внутри Execution layer.

## Последствия для REQUIREMENTS.md

Зафиксировать:

- **ADR-001** как источник решения — ссылка из REQUIREMENTS.md на этот файл.
- **RISK-11**: Pre-execution approval chain incompatible with workflow-per-task model — если Temporal будет рассматриваться повторно в будущем, этот риск должен быть переоценён явно, а не проигнорирован. Источник: ADR-001.
- Пункты из ранее переданного `temporal-opa-migration-risks.md` (workflow determinism, versioning, audit retention gap, OPA latency budget, Rego learning curve) — не отменяются, а помечаются как **отложенные до Phase 3+**, применимые в момент, когда/если Temporal будет вводиться для Execution layer.
- Обновить оценку трудозатрат Phase 1 в SPEC-10 (Phase Plan) с учётом оценки ~3.5–5 месяцев (1 инженер) / ~6–9 недель (2 инженера) для Governance Controller как отдельного, самого крупного по объёму компонента Phase 1.

## Примечание

Решение не отменяет ценность анализа Temporal/OPA — OPA принимается как улучшение Phase 2, а разбор рисков Temporal сохраняется как задокументированная база знаний на случай, если потребность в durable workflow engine станет реальной на более поздней фазе проекта.
