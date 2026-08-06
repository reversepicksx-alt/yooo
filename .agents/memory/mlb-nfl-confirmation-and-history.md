---
name: MLB/NFL identity confirmation and evidence history
description: MLB and NFL manual prediction flows require explicit identity confirmation before fixture enrichment, while results expose broader labelled evidence than model input.
---

MLB/NFL player selection is a two-step interaction: selecting a search result creates a pending identity card; only explicit confirmation may resolve the player, fetch the next game, and expose matchup inputs. Search controls must invalidate in-flight requests on selection and require both request identity and current query text before painting results. Editing or changing the player must clear all pending, confirmed, opponent, venue, and next-game state. Active NFL season and forward schedule lookup must remain provider-aware rather than fixed to one past season. Keep model samples bounded, but show the larger multi-season labelled evidence history with its count/range in the analysis UI. Soccer players with multiple contexts should default to their current club, fetch its next fixture immediately, and retain national-team options in the context picker.

**Why:** Ambiguous player matches and stale club/fixture context undermine trust. Out-of-order keystroke responses made valid MLB/NFL results appear as “No results,” while multi-context soccer players were left at manual-match entry even when their club had a valid fixture. Fixed one-season history also hid useful evidence and caused current-season NFL players to appear to have no next game.

**How to apply:** Preserve the pending/confirmed split in mobile MLB/NFL forms, derive next-game enrichment only after confirmation, use active-season-aware NFL client lookup, and keep `historyGameCount`, `historySeasons`, and `historyRange` visible to the frontend.