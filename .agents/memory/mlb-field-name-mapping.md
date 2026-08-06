---
name: MLB player search field name mapping
description: Backend MLB search returns camelCase but api.ts was mapping snake_case, silently producing empty fullName for every player.
---

The `/api/mlb/players/search` endpoint returns camelCase fields: `firstName`, `lastName`, `fullName`, `id`, `position`, `team`.

`searchMlbPlayers` in `lib/api.ts` was mapping `p.first_name`, `p.last_name`, `p.full_name` (snake_case). All three resolved to `undefined`, so `fullName` became `''`. The all_players universal search then filtered out every MLB result with `.filter(p => p.playerName)` since `playerName` was empty.

**Fix**: Always try camelCase first, snake_case as fallback:
```js
firstName: p.firstName ?? p.first_name ?? '',
lastName:  p.lastName  ?? p.last_name  ?? '',
fullName:  p.fullName  ?? p.full_name  ?? `${p.firstName ?? p.first_name ?? ''} ${p.lastName ?? p.last_name ?? ''}`.trim(),
```

**Why**: The BDL/ESPN backend normalizes to camelCase at the route level; never assume snake_case for player name fields returned from our own backend.

**How to apply**: Any new sport player search function in api.ts must check camelCase first when mapping backend player rows, and always fallback to snake_case for compatibility.
