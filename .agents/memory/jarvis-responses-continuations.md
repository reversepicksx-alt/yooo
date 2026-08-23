---
name: JARVIS Responses continuations
description: Correct Responses JSON/tool continuation protocol for owner orchestration.
---

Responses JSON-object mode requires an explicit JSON instruction in an input message, not only in system instructions. After a function call, continue with `previous_response_id` and only the function output; replaying function/reasoning output items causes a 400 because hidden reasoning items cannot be reconstructed client-side.

**Why:** The minimal model and tool schemas were valid, but the first full owner turn failed on request-shape and manual continuation protocol errors.

**How to apply:** Record sanitized provider failures for owner diagnostics, preserve a bounded turn state, and report `provider_used=openai` only when the response chain returns a real response ID without fallback.