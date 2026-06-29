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
