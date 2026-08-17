# SPEC-07: Intake and Idea Ingestion

## 7.1 Purpose

Lower the barrier for humans to submit ideas, feedback, bugs, and improvements. Capture from multiple channels and normalize into structured Plane drafts.

## 7.2 Supported Channels

| Channel | Use Case | Priority |
|---|---|---|
| Telegram bot | Quick text/voice/photo ideas | P1 |
| Email | Forwarded requests, customer feedback | P2 |
| Slack | Team discussions | P2 |
| API | External systems | P1 |
| Voice | Mobile hands-free capture | P3 |
| Plane UI | Manual structured input | P0 |

## 7.3 Intake Adapter

Each channel adapter normalizes input to:

```yaml
raw_idea_id: RI-123
source: telegram | email | slack | api | voice | plane
author: user@example.com
received_at: ISO8601
title: "<extracted or provided>"
description: "<raw text or transcription>"
attachments:
  - url: ...
    type: image | audio | document
context:
  project_hint: "vpn-combiner" | null
  domain_hints: [networking, docker]
```

## 7.4 Idea Ingestion Service

Classifies raw ideas:

```yaml
current_status: needs-triage | existing-project | new-project | spam
confidence: 0.0 .. 1.0
classification_reason: "..."
plane_action:
  type: create_draft | create_new_project_proposal | create_bug | create_improvement
  target_project_id: PROJECT-1 | null
```

### Classification Rules

- `confidence > 0.85` and project identified → create item in existing project.
- `confidence > 0.85` and no project match → create `New Project Proposal`.
- `0.5 < confidence <= 0.85` → create `Needs Triage` item.
- `confidence <= 0.5` → create `Needs Triage` item with low-priority label.

## 7.5 Plane Drafts

Created items have:

- status = `PROPOSED`
- source label (telegram, email, etc.)
- author field
- `raw_idea_id` custom field

## 7.6 META ORCH Trigger

META ORCH periodically reads Plane items with:

- status = `PROPOSED`
- type = `New Project Proposal` or `Epic Draft`
- not recently processed

Then decomposes into Epic + Task DAG.

## 7.7 Human Triage

`Needs Triage` items appear in dedicated Plane view:

- Human sets project or marks spam.
- On classification, Idea Ingestion creates appropriate draft.

## 7.8 Source of Truth

- Raw intake logs stored in Controller DB.
- Plane holds normalized drafts.
- Intake does not bypass governance approval.
