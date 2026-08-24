# macro-agent service

Phase 2 scaffold for the macro-agent run lifecycle.

This package is intentionally minimal: it provides an in-memory store and a
FastAPI surface that mirrors the macro-agent runs API expected by the
Governance Controller. The real macro-agent backend will replace the
`RunStore` implementation in a later phase.
