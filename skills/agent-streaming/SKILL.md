---
name: agent-streaming
description: Implement or review streaming Agent interactions across this repository's FastAPI backend and Next.js frontend.
---

# Agent Streaming

Use this skill when changing a streaming chat endpoint, the Next.js BFF proxy, browser stream consumption, or Agent run-state UI.

## Architecture boundary

- `apps/api` owns model calls, conversation persistence, and stream production.
- `apps/web` owns browser UI and the same-origin BFF route under `src/app/api/`.
- Browsers call the BFF route, never FastAPI directly. Do not expose model keys or backend-only configuration through `NEXT_PUBLIC_` variables.

## Stream preservation

- Forward FastAPI streams with `new Response(backendResponse.body, ...)`; do not call `json()`, `text()`, or `arrayBuffer()` on a successful streaming response.
- Preserve cancellation signals when forwarding browser requests.
- Persist one complete assistant message when a model stream finishes. Do not write one database message per text fragment.
- Once response bytes have started, communicate later failures as stream events or an in-band error payload; an HTTP status code cannot be changed retroactively.

## UI state

Use the project mapping in `docs/agent-ui-events.md`. A run starts in `thinking`, moves to `streaming` when text begins, and ends in exactly one terminal state: `done`, `aborted`, or `error`.

## Verification

- Keep `POST /chat` compatible when adding `POST /chat/stream`.
- Verify backend tests with `python -m pytest` and `python -m ruff check app tests` from `apps/api`.
- Verify the frontend with `pnpm lint` from `apps/web`.
- Use a short real request only when model-stream behavior itself needs verification; otherwise mock the model to avoid unnecessary API cost.
