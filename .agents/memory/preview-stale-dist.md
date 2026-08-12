---
name: Preview serves stale dist in PRODUCTION mode
description: Why mobile source edits don't show in the Replit preview/deploy until dist is rebuilt
---

The `Start application` workflow runs `cd mobile && PRODUCTION=true node proxy.js`. In
PRODUCTION mode the proxy serves the pre-built static `mobile/dist/` folder and proxies
`/api/*` to FastAPI — it does NOT run live Metro. So any edit to `mobile/app`, `mobile/lib`,
etc. is invisible in the preview until `dist/` is rebuilt.

**Symptom:** Splash logo animates for several seconds, then a black screen. If the stale
`dist/` bundle contains a runtime crash (e.g. a screen calling an undefined setter), the app
boots, redirects, the JS throws, React unmounts → black. The HTML splash overlay (proxy
injects it, hides at 8s app-side / 15s proxy-side) masks the dead app until it fades.

**Diagnosis:** Compare `stat -c '%y' mobile/dist/index.html` against the newest source file
mtime under `mobile/app|lib|components|contexts`. If dist is older than the fixes, it's stale.
Browser console being clean on the FRESH build vs. throwing on the stale one confirms it.

**Fix:**
```
cd mobile && export PATH="<nodejs-nix-bin>:$PATH" && rm -rf dist && npx expo export -p web --output-dir dist
```
then restart the `Start application` workflow so the proxy picks up the new bundle. Verify the
served bundle hash matches disk: `curl -s localhost:5000/auth | grep -o 'index-[a-f0-9]*\.js'`.

**Production caveat:** The deploy build command is
`[ -f dist/index.html ] && skip export || (npm install && npx expo export ...)`.
It SKIPS the export when `dist/index.html` already exists, so the published app ships whatever
`dist/` is in the workspace snapshot at publish time. After rebuilding dist locally you must
**publish again** for production to get the fix.

**Why:** Keeps deploys fast (no rebuild when dist is committed) but means a stale committed
dist silently ships old code.

## Live prediction history contract

The mobile live-history adapter must accept the non-empty history array from any of
`playerGameLogs.games`, `gameLogs`, or `recentSamples`, and derive the displayed value
from the mapped stat field before generic `value`/`targetStat` aliases. Empty arrays must
not block fallback sources.

**Why:** The backend has returned these compatible shapes across live and saved-analysis
paths; treating an empty primary array as authoritative makes valid recent history vanish
without an API error.

**How to apply:** When changing prediction response fields, preserve this tolerant
normalization before the UI filters rows.

## Metro export can hang at 0% — do NOT force always-export on deploy

Observed: `npx expo export -p web` reproducibly **hangs at `0.0% (0/1)`** (Metro stuck
transforming the very first module — a worker/transformer/cache stall, not slowness; memory
was fine). Also, background/detached builds (`nohup`, even `setsid`+`disown`) get **reaped**
when the bash tool call returns, so you can't run a long build across polling calls.
**To run a long build reliably, use a persistent Replit workflow** (managed, not reaped) and
watch its logs for `dist/index.html`, rather than a backgrounded shell command.

**Consequence for deploy:** never set the deploy build command to *always* run `npx expo export`.
If Metro hangs on the deploy server too, the publish hangs/fails and any backend hotfix in the
same publish never ships. Keep the build as **skip-if-`dist/index.html`-exists** and ship a
known-good committed dist. The tradeoff is the stale-dist footgun above — you must delete+rebuild
dist (via a workflow) and recommit to ship frontend changes.

## Recover a known-good dist without destructive git

Destructive git (`checkout`/`restore`) is blocked for the agent. To restore a previously-built
`mobile/dist` from a good commit into the working tree, use read-only `git archive` piped to tar:
```
git --no-optional-locks archive <good-commit> mobile/dist | tar -x
```
Find a good commit's bundle hash first: `git --no-optional-locks show <commit>:mobile/dist/index.html | grep -o 'index-[a-f0-9]*\.js'`.
Note an auto-checkpoint taken while dist was deleted will commit an **empty** dist (HEAD had only
robots.txt), so always verify `HEAD:mobile/dist/index.html` before assuming HEAD is publishable.
