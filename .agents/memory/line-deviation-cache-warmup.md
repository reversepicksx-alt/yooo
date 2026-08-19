---
name: Line-deviation cache warmup
description: How prediction-time line-deviation calibration should behave when the settled ledger cache is cold or slow.
---

Line-deviation hit-rate evidence must distinguish a real zero-sized settled sample from a temporarily unavailable ledger lookup. Cold database reads can exceed the prediction response budget; coalesce and shield the refresh so it completes for later requests instead of being cancelled each time, and mark the evidence as unavailable while it warms.

**Why:** A one-second bounded read repeatedly timed out against the settled ledger and the UI rendered its default rate with `n=0`, which looked like position/prop history had been deleted despite thousands of eligible settled picks.

**How to apply:** Keep the ledger refresh asynchronous, shared, and independently cacheable. UI copy and audit output must only say “no settled sample” after a completed query; use a temporary-unavailable state for a timeout or cache-warmup condition.