---
name: Deployment .replitignore and zero-dep proxy
description: Replit VM re-applies repl layer after build, wiping node_modules; proxy.js must use zero npm deps
---

## Rule
`mobile/proxy.js` must NEVER require npm packages. Use only Node.js built-in modules (`http`, `path`, `fs`, `url`).

**Why:** Replit VM deployments re-apply the repl layer AFTER the build step runs. `.replitignore` excludes `mobile/node_modules` to prevent 30-min push timeouts (~372 MB). Even if the build step runs `npm install`, those modules get wiped when the repl layer is re-applied before `start.sh` runs. Result: proxy crashes instantly with "Cannot find module 'express'" → health check failure → "Internal Server Error" or "deployment could not be reached".

**How to apply:**
- `mobile/proxy.js` is fully rewritten to use zero npm deps (built-in `http`, `path`, `fs`, `url`).
- `start.sh` must NOT run `npm install` before starting the proxy (delays port 5000 bind by 30-60s, causing health check timeout).
- Build command in `.replit` still runs `npm install + npx expo export` for the frontend bundle — that's fine because it runs before the repl layer re-apply.
- `mobile/node_modules` stays in `.replitignore` (needed to avoid push timeout).
