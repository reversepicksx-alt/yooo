---
name: Settlement prop aliases
description: Canonicalization rule for empirical history when multiple request labels settle from one provider field.
---

Labels such as `passes` and `pass_attempts` can be distinct request or display vocabulary while sharing the same provider settlement field. Their settled-history buckets must be canonicalized together before building or reading empirical safety/calibration data. The same applies to equivalent goalkeeper-save labels.

**Why:** A separate bucket can report no historical evidence even when thousands of equivalent settled events exist, which creates a false JARVIS “history unavailable” warning and weakens the safety signal.

**How to apply:** When adding or changing a prop label, compare its provider settlement field and result contract first. If they are identical, update the canonical alias map and test both cache-build merging and lookup fallback.