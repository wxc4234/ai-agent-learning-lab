---
name: agent-runtime
description: Implement or review Tool Calling, validated tool execution, tool dispatch, and Agent loops in this repository's FastAPI backend.
---

# Agent Runtime

Use this skill when changing tool definitions, model-supplied tool arguments,
tool dispatch, or Agent execution loops in `apps/api`.

## Trust boundary

- A model may request a tool, but it never selects executable Python code directly.
- Resolve tool names only through an explicit registry. Never use dynamic imports,
  `eval`, or attribute lookup based on model output.
- Treat `function.arguments` as untrusted JSON. Validate it with the Pydantic model
  registered for that tool before calling its executor.
- Reject unknown fields and constrain values where the tool has a safe operating
  range.

## Schema ownership

- Keep one Pydantic argument model per tool as the runtime source of truth.
- Generate the model-facing JSON Schema from that Pydantic model instead of
  maintaining a second handwritten parameter schema.
- A tool is executable only when both its argument model and executor are
  registered.

## Error contract

- Unknown tools and invalid arguments must not reach an executor.
- Return a structured tool error containing `tool_call_id`, `tool_name`, a stable
  error code, and safe validation details when relevant.
- Model-generated argument errors are Agent runtime outcomes, not malformed
  FastAPI request bodies; do not mislabel them as request-body HTTP 422 errors.

## Loop control

- Represent model output as an explicit tool action or final answer. Keep hidden
  model reasoning out of runtime state and logs.
- Feed successful tool results and structured tool errors back as observations so
  a later step can finish or correct an earlier action.
- Every loop must enforce a positive hard step limit and return an explicit
  `max_steps_exceeded` result when it is exhausted.
- Give each tool a positive execution timeout. Run synchronous executors outside
  the event loop, and convert timeouts or ordinary executor exceptions into safe
  error observations.
- Do not include raw executor exception messages in observations. They may expose
  secrets or internal paths.
- Let cancellation propagate out of tool execution so higher-level run handling
  can preserve the existing user and timeout cancellation semantics.

## Verification

- Unit-test argument models and pure tool functions without calling a model.
- Test Tool Calling routes with mocked model responses so error paths are
  deterministic and do not consume API quota.
- From `apps/api`, run the focused pytest tests and
  `python -m ruff check app tests`.
