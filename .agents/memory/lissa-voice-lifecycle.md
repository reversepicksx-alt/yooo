---
name: Lissa voice lifecycle
description: Rules for keeping the owner-only voice layer continuously available without self-triggering or freezing.
---

The voice layer must supervise recognition rather than trust one continuous session: iOS and Android can end a session after a final result, and transient busy/network/audio errors are recoverable. Do not restart recognition until spoken output has emitted `onDone`/`onStopped`, otherwise Lissa can capture her own answer or enter a busy loop.

**Why:** Native speech recognition sessions are segmented and speech synthesis is asynchronous even when its API call returns immediately.

**How to apply:** Keep one global owner-only recognizer in the authenticated shell, guard starts with refs/timers, treat final natural sentences as questions when global mode is enabled, and reserve the visible control for permission recovery rather than normal stop/start interaction.