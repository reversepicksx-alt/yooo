---
name: Plain pytest async convention
description: Backend tests do not load pytest-asyncio automatically.
---

Use plain pytest tests with `asyncio.run(...)` for async helper coverage unless the test environment explicitly gains an async plugin.

**Why:** The backend test environment currently runs async test functions without pytest-asyncio, causing collection-time failures.

**How to apply:** Keep new async unit tests synchronous at the pytest boundary and run the coroutine explicitly.