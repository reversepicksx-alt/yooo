---
name: Lissa voice lifecycle
description: Rules for keeping the owner-only voice layer continuously available without self-triggering or freezing.
---

The voice layer must supervise recognition rather than trust one continuous session: iOS and Android can end a session after a final result, and transient busy/network/audio errors are recoverable. Do not restart recognition until spoken output has emitted `onDone`/`onStopped`, otherwise Lissa can capture her own answer or enter a busy loop. The message route must also cap optional AI generation and return the deterministic answer when the provider stalls. Browser speech synthesis may require a user-gesture unlock even after microphone permission is granted. Tab-local analysis context must be explicitly cleared or ignored outside My Picks because Expo tabs remain mounted. The global assistant must require the wake word; auto-submitting arbitrary final recognition fragments causes unsolicited answers.

**Why:** Native speech recognition sessions are segmented and speech synthesis is asynchronous even when its API call returns immediately.

**How to apply:** Keep one global owner-only recognizer in the authenticated shell, guard starts with refs/timers, require “Lissa” before submitting a question, reserve the visible control for permission recovery rather than normal stop/start interaction, provide a tap-to-unlock spoken welcome on web, pass only the active tab's context, and keep browser response latency bounded independently of the general prediction timeout.