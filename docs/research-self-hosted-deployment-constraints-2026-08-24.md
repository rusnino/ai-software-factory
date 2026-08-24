# Research: Self-Hosted Deployment Constraints (Hermes VM)

Date: 2026-08-24
Status: research memo, external infrastructure — no code/spec changes

## Scope note (important)

This research was conducted on **a separate, unrelated server** — the user's own
existing VM that already runs a real, live instance of Hermes Agent alongside
Nautobot, PostgreSQL, Redis, Firecrawl, Playwright, RabbitMQ, FoundationDB, and
UniFi. It is **not** the planned deployment target for the AI Software Factory
Governance Controller or macro-agent, and the resource constraints documented below
do **not** apply to, or block, this project's architecture or roadmap.

It is included in `docs/` for one reason: it resolves an open question raised in
`docs/research-proposed-orchestration-stack-2026-08-23.md` §4.2/§5 — namely, what
concretely "Hermes" referred to in the originally-proposed stack diagram. The
answer is now confirmed: it is this real, already-running Hermes Agent instance
(gateway, Telegram, skills, memory, MCP, delegation, cron, profiles, subagents,
toolsets, model/provider switching), not a hypothetical evaluation candidate. That
confirmation does not change the architectural conclusion already reached — an
autonomous dispatcher-style meta-agent still cannot sit above the Governance
Controller as an approval authority — but it does replace speculation with fact
about what the diagram's author meant.

Separately, this memo's resource-constraint findings are useful **corroborating
evidence** (not a new finding specific to ai-software-factory) for the general
principle already reached independently in the orchestration-stack memo: Temporal,
Hatchet, full Plane deployments, and OpenHands-with-Docker-socket are all
heavyweight, multi-service stacks that should not be casually co-located with other
production workloads on constrained hardware — here demonstrated concretely on a
real 16 CPU / 15 GB RAM / 32 GB disk host that is already at 89% disk usage with
~5.2 GB RAM available and no swap.

The content below is preserved as delivered, describing constraints specific to
that VM.

---

## Ограничения self-hosted-развёртывания

Исследование завершено. В ходе него система не изменялась и сервисы не перезапускались.

### Критичный вывод по текущему серверу

На сервере сейчас:

- Debian 13;
- 16 CPU;
- 15 ГБ RAM;
- около **5.2 ГБ доступной RAM**;
- swap отсутствует;
- root filesystem: **32 ГБ, свободно около 3.5 ГБ, занято 89%**;
- уже работают Nautobot, PostgreSQL, Redis, Firecrawl, Playwright, RabbitMQ, FoundationDB, UniFi и Hermes;
- Firecrawl использует опубликованный порт `3002`;
- Nautobot использует `8080`;
- UniFi использует `8443`, `8843`, `8880`, `18080` и UDP-порты;
- `3000` занят локальным Node-процессом;
- Docker socket доступен root-пользователю.

Поэтому текущий сервер **не подходит для безопасного размещения ещё одного тяжёлого production control plane**. Особенно опасно добавлять сюда OpenHands с Docker socket, Temporal с полной инфраструктурой, Hatchet с RabbitMQ/PostgreSQL или Plane с несколькими сервисами.

### 1. Frameworks и orchestration runtime

#### LangGraph

**Self-hosted grade:**

- SDK/один сервис: **Easy/Moderate**;
- LangGraph Server: **Moderate**;
- полный LangSmith/Platform-подобный control plane: **Heavy/Kubernetes-oriented**.

Минимальный LangGraph — это обычное Python-приложение. Ему не нужны обязательные PostgreSQL, Redis или RabbitMQ.

Но полноценная эксплуатация требует самостоятельно добавить:

- API server;
- persistence/checkpoint storage;
- auth;
- TLS/reverse proxy;
- retries и idempotency;
- worker model;
- observability;
- backup и migration strategy.

Платформенный вариант заметно тяжелее: PostgreSQL, ClickHouse, Redis/Valkey, object storage и несколько backend-сервисов.[1][2]

**Вывод:** лучший кандидат для небольшого PoC на отдельном Python-сервисе, но не готовая Kanban/execution-платформа.

---

#### CrewAI

**Self-hosted grade:**

- один Crew/Flow: **Easy**;
- durable production workloads: **Moderate/Heavy**.

CrewAI удобно запускается в одном Python-процессе. Но он не предоставляет полностью готовую эксплуатационную платформу:

- workers;
- scheduler;
- rate limits;
- durable queue;
- multi-tenancy;
- полноценные retries;
- backup;
- reverse proxy;
- auth.

Persisted Flows требуют database backend. Для долгих или параллельных задач всё равно понадобится собственная очередь и execution-модель.[3]

**Вывод:** хорош для декларативного multi-agent workflow, но не заменяет Temporal/Hatchet.

---

#### Microsoft Agent Framework

**Self-hosted grade:**

- SDK: **Easy/Moderate**;
- готовый production control plane: **не подтверждён**.

Framework можно запустить как обычное Python-, .NET- или Go-приложение. Он не требует встроенного PostgreSQL, Redis или broker для базового сценария.

Всё production-окружение нужно проектировать самостоятельно:

- state;
- auth;
- worker scaling;
- retries;
- persistence;
- observability;
- deployment;
- security boundaries.

Он выглядит перспективнее AutoGen как новое направление Microsoft, но это именно framework/runtime, а не готовая self-hosted платформа.[4]

---

#### PydanticAI, Haystack, LlamaIndex, smolagents, CAMEL-AI, MetaGPT

Общий класс:

**Self-hosted grade: Easy для SDK, Low как готовая orchestration platform.**

Их можно запускать в одном процессе, но за пределами базового agent loop нужно самостоятельно строить:

- task queue;
- planner;
- scheduler;
- durable state;
- retries;
- API;
- auth;
- sandbox;
- monitoring;
- workers.

Особенно важно для `smolagents`: локальное выполнение Python-кода нельзя считать полноценной security boundary. Для production нужен отдельный Docker/VM sandbox.

### 2. Coding-agent harness

#### OpenHands

**Self-hosted grade: B — пригоден после hardening, но не на текущем production-хосте.**

OpenHands поддерживает Docker, VM, Agent Server, workspaces и несколько backend-ов.[5][6]

Основные ограничения:

- запуск без sandbox даёт агенту доступ к файловой системе;
- Docker quickstart монтирует host project directory;
- подключение `/var/run/docker.sock` фактически даёт агенту контроль над Docker и потенциально над хостом;
- опубликованные quickstart-конфигурации не являются полноценной security policy;
- нужно самостоятельно настроить seccomp/AppArmor, rootless, CPU/RAM/PID limits и network egress;
- API/webhook surface требует TLS, auth и rate limiting;
- общие mounts могут привести к утечке между несколькими агентами.

**Для текущего сервера OpenHands нельзя запускать с:**

```text
/var/run/docker.sock
/root/projects
/root/.hermes
/var/lib/docker
Nautobot volumes
Firecrawl volumes
UniFi volumes
```

Безопасный PoC — только отдельная VM или полностью disposable host.

---

#### Aider, Goose, Cline, Continue, SWE-agent

Эти проекты в основном являются локальными coding-agent инструментами.

Они проще в запуске, но обычно не предоставляют:

- полноценный multi-tenant server;
- task queue;
- durable execution;
- централизованный worker scheduler;
- isolation между несколькими агентами;
- production API control plane.

Они подходят как **worker/harness**, но не как центральная система оркестрации.

#### Hermes Agent

Hermes уже закрывает значительную часть control-plane задач:

- gateway;
- Telegram;
- skills;
- memory;
- MCP;
- delegation;
- cron;
- profiles;
- subagents;
- toolsets;
- model/provider switching.

Ограничение: Hermes сам по себе не является полноценной заменой Temporal или Kubernetes scheduler для большого количества независимых production workers.

### 3. Durable execution

#### Temporal

**Self-hosted grade: A, но инфраструктурно тяжёлый.**

Temporal даёт:

- durable workflow history;
- task queues;
- retries;
- timeouts;
- heartbeats;
- worker model;
- recovery;
- versioning.

Но production требует:

- PostgreSQL или Cassandra;
- persistence;
- backups;
- schema management;
- worker versioning;
- workflow replay checks;
- TLS/auth;
- observability;
- HA datastore.

Temporal Server не исполняет пользовательский код напрямую — код выполняют внешние workers.[5]

На текущем сервере есть прямой конфликт: Temporal UI обычно использует порт `8080`, занятый Nautobot. Полный Compose-стек также существенно увеличит расход диска и RAM.

**Рекомендация:** только отдельная VM или development server для PoC.

---

#### Hatchet

**Self-hosted grade: Moderate/Heavy.**

Типовой deployment включает:

- PostgreSQL;
- RabbitMQ;
- migration/setup jobs;
- Hatchet engine;
- dashboard;
- workers.

Документированный Compose-пример использует несколько портов, включая `8080`, который уже занят Nautobot.[6]

Также нужно заменить небезопасные quickstart-настройки:

- default credentials;
- insecure gRPC/auth;
- широкие connection limits;
- публично доступные dashboard/API.

**Рекомендация:** Hatchet-lite или минимальный development mode в отдельной VM. Не добавлять полный стек на текущий сервер.

### 4. Kanban и task boards

#### Plane

**Self-hosted grade: Moderate/Heavy.**

Plane подходит как:

- task system;
- backlog;
- Kanban;
- cycles;
- dependencies;
- priorities;
- comments;
- API/webhooks.

Но Plane не является execution engine. Он не решает:

- worker scheduling;
- retries;
- leases;
- task locking;
- durable execution;
- sandboxing.

Self-hosted deployment добавляет PostgreSQL, Redis, workers, storage и web-приложение. Для production рекомендуются отдельные database/storage и резервное копирование.[7]

На текущем сервере свободно всего 3.5 ГБ диска и около 5 ГБ доступной RAM — этого недостаточно для комфортного постоянного Plane deployment.

#### OpenProject, Taiga, Vikunja, Leantime

Это полезные human-oriented project-management системы, но они ещё дальше от autonomous execution:

```text
Kanban board ≠ worker queue
Kanban board ≠ scheduler
Kanban board ≠ retry engine
```

Их можно использовать как system of record, но рядом нужен orchestration/execution слой.

### 5. LLM gateway

#### LiteLLM

**Self-hosted grade: High для небольшого gateway, Moderate/Heavy для production.**

LiteLLM лучше всего подходит для vendor-neutral model routing:

- OpenAI;
- Anthropic;
- Google;
- Azure;
- Bedrock;
- vLLM;
- Ollama;
- OpenAI-compatible endpoints.

Для простой маршрутизации возможен один gateway-процесс.

Production-функции добавляют:

- PostgreSQL;
- Redis для multi-instance/rate limiting/router state;
- master key;
- salt key;
- spend tracking;
- virtual keys;
- auth;
- backups;
- HA.

Критичный момент: `LITELLM_SALT_KEY` нужно сохранять постоянно. При его потере зашифрованные credentials могут стать нечитаемыми.[8][9][11]

**Вывод:** LiteLLM — лучший кандидат для отдельного маленького PoC, но не стоит пока устанавливать его на текущем сервере без очистки диска и лимитов ресурсов.

### 6. MCP

**Self-hosted grade:**

- stdio MCP: **Easy**;
- Streamable HTTP MCP: **Moderate**;
- публичный multi-tenant MCP gateway: **Heavy**.

Текущий локальный Firecrawl MCP через stdio — как раз наиболее безопасный с точки зрения инфраструктурной сложности:

```text
Hermes
  └── subprocess firecrawl-mcp
        └── localhost:3002
```

Порты не нужны, но остаются риски:

- subprocess запускается с правами gateway;
- MCP tools могут давать доступ к filesystem/network/browser;
- нужно ограничивать tool allowlist;
- нельзя давать Docker socket;
- нужно контролировать subprocess count и RSS;
- HTTP MCP требует auth, Origin validation, private binding и lifecycle control.[10]

MCP — это protocol layer, а не authorization boundary и не execution engine.

### Текущая совместимость с сервером

| Компонент | Совместимость сейчас |
|---|---|
| LangGraph SDK | Возможен изолированный PoC |
| CrewAI SDK | Возможен изолированный PoC |
| Microsoft Agent Framework | Возможен изолированный PoC |
| LiteLLM | Возможен только маленький localhost PoC с лимитами |
| MCP stdio | Хорошо подходит, уже работает |
| OpenHands | Не запускать на production host без VM/sandbox |
| Temporal full stack | Не размещать на текущем сервере |
| Hatchet full stack | Не размещать на текущем сервере |
| Plane persistent deployment | Отдельная VM предпочтительна |
| OpenProject/Taiga | Не критичны, но добавят stateful stack |
| vLLM/Ollama | Только если есть отдельный GPU/ресурсный бюджет |

### Минимально безопасный PoC

Наиболее консервативная схема:

```text
Hermes
  └── один Python-сервис
        └── LangGraph или Microsoft Agent Framework
              └── только read-only stdio MCP
```

Ограничения:

- отдельный Compose project/network;
- отдельный Python environment;
- без Docker socket;
- без `/root/projects`;
- без production volumes;
- один worker;
- один bounded workflow;
- localhost-only endpoint;
- LLM API key с жёстким spending limit;
- read-only tools;
- без browser/shell на первом этапе;
- pinned dependencies;
- SQLite/process-local state для первого теста.

### Когда нужна отдельная VM

Отдельная VM обязательна, если появляется хотя бы один пункт:

- Docker socket;
- nested Docker sandboxes;
- запуск недоверенного кода;
- host networking;
- публичный ingress;
- Temporal/Hatchet production stack;
- Elasticsearch/visibility store;
- durable user data;
- несколько concurrent coding agents;
- публичный MCP HTTP endpoint;
- restart/upgrade experiment, способный затронуть текущие сервисы.

### Итоговая рекомендация (для этого VM)

На текущем сервере пока **не устанавливать** OpenHands, Temporal, Hatchet или Plane.

Порядок безопасного PoC:

1. LangGraph или Microsoft Agent Framework как библиотека.
2. Один bounded agent workflow.
3. Текущий Firecrawl MCP только в read-only режиме.
4. LiteLLM — только после освобождения диска и проверки RAM.
5. OpenHands — только в отдельной VM.
6. Plane и Temporal — только после определения реальной потребности в persistent task control и durable execution.

Оптимальная будущая композиция:

```text
Hermes
+ LiteLLM
+ LangGraph или Microsoft Agent Framework
+ OpenHands workers в отдельных VM
+ Plane как system of record
+ Temporal/Hatchet для durable execution
+ MCP для tool integration
```

Но это уже не «добавить несколько контейнеров». Это отдельная распределённая платформа с собственной:

- security model;
- backup strategy;
- resource budget;
- observability;
- upgrade policy;
- rollback procedure.

На данном сервере разумно начать только с library-level PoC и не пересекать production Docker stacks.

### Sources

[1] [OpenHands local setup](https://docs.openhands.dev/openhands/usage/run-openhands/local-setup)
[2] [OpenHands Docker sandboxes](https://docs.openhands.dev/openhands/usage/sandboxes/docker)
[3] [CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture)
[4] [Microsoft Agent Framework overview](https://learn.microsoft.com/en-us/agent-framework/overview)
[5] [Temporal self-hosted deployment](https://docs.temporal.io/self-hosted-guide/deployment)
[6] [Hatchet Docker Compose deployment](https://docs.hatchet.run/self-hosting/docker-compose)
[7] [Plane Docker Compose self-hosting](https://developers.plane.so/self-hosting/methods/docker-compose)
[8] [LiteLLM deployment](https://docs.litellm.ai/docs/proxy/deploy)
[9] [LiteLLM Docker quickstart](https://docs.litellm.ai/docs/proxy/docker_quick_start)
[10] [MCP transport specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
[11] [LiteLLM virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys)
[12] [TaskingAI](https://github.com/TaskingAI/TaskingAI)
[13] [Open WebUI](https://github.com/open-webui/open-webui)
[14] [TensorZero](https://github.com/tensorzero/tensorzero)
[15] [Portkey Gateway](https://github.com/Portkey-AI/gateway)

## Relevance to ai-software-factory

- **Resolves** the "what is Hermes" open question from
  `docs/research-proposed-orchestration-stack-2026-08-23.md`: confirmed to be a
  real, already-running Hermes Agent instance on unrelated infrastructure, not a
  hypothetical candidate to evaluate for adoption into this project.
- **Does not change** any architectural recommendation for ai-software-factory:
  none of this project's Governance Controller, macro-agent, or Phase 1-3 plans are
  hosted on, or constrained by, this VM.
- **Does corroborate**, with concrete numbers on a real host, the general caution
  already independently reached in the orchestration-stack memo: Temporal, Hatchet,
  full Plane deployments, and Docker-socket-connected harnesses like OpenHands are
  all heavyweight enough that they warrant dedicated infrastructure and a real
  resource budget — worth keeping in mind once ai-software-factory's own Phase 3+
  work (Docker sandboxing, optional Temporal/Hatchet execution-layer component,
  real Plane deployment) reaches the point of choosing where those services
  actually run.
