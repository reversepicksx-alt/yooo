---
name: PrizePicks PerimeterX bypass
description: How to fetch PrizePicks public API from the server without getting 403 blocked by PerimeterX.
---

## Rule

Use `curl --http2 --compressed -A <iOS Safari UA>` via `asyncio.create_subprocess_exec`.
Python HTTP clients (aiohttp, requests, httpx without special TLS fingerprinting) return HTTP 403 because PerimeterX fingerprints the TLS handshake, not just headers.

**Why:** PerimeterX (`PXZNeitfzP`) on api.prizepicks.com checks TLS/HTTP2 fingerprint (JA3/JA4). curl's OpenSSL TLS fingerprint with `--http2` matches real browser profiles; Python ssl/httpcore do not.

**How to apply:** In `backend/prizepicks_client.py`, all fetch calls go through `_curl_get(url)` which spawns `asyncio.create_subprocess_exec("curl", "-s", "--http2", "--compressed", "-A", ios_safari_ua, ...)`. No special Python packages needed (h2 / httpx[http2] are NOT required).

iOS Safari UA string:
`Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1`

Soccer league IDs on PrizePicks: 241=WORLD CUP, 82=SOCCER, 458=WORLD CUP 1H, 14=EPL, 243=SOCCER2H, 242=SOCCER1H, 457=WC TRNY, 262=SOCCERSZN.
