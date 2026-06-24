import os, time, json, urllib.request, urllib.error
import jwt  # PyJWT

KEY_ID      = os.environ["ASC_KEY_ID"]
ISSUER_ID   = os.environ["ASC_ISSUER_ID"]
PRIVATE_KEY = os.environ["ASC_PRIVATE_KEY"]
BUNDLE_ID   = "com.reversepicks.app"
PRODUCT_ID  = "reversepicks_weekly"
TARGET_PRICE = "12.99"

def make_token():
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER_ID, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": KEY_ID},
    )

def asc(method, path, body=None):
    token = make_token()
    url = f"https://api.appstoreconnect.apple.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# ── 1. Find app ───────────────────────────────────────────────────────────────
print("Looking up app…")
status, resp = asc("GET", f"/v1/apps?filter[bundleId]={BUNDLE_ID}&fields[apps]=name,bundleId")
apps = resp.get("data", [])
if not apps:
    print("❌ App not found:", json.dumps(resp, indent=2))
    exit(1)
app_id = apps[0]["id"]
print(f"✅ App: {apps[0]['attributes']['name']} ({app_id})")

# ── 2. Find subscription groups ───────────────────────────────────────────────
print("\nFetching subscription groups…")
status, resp = asc("GET", f"/v1/apps/{app_id}/subscriptionGroups?fields[subscriptionGroups]=referenceName")
groups = resp.get("data", [])
print(f"Found {len(groups)} group(s)")
if not groups:
    print("⚠️  No subscription groups — create them in App Store Connect first.")
    exit(0)

# ── 3. Find reversepicks_weekly ───────────────────────────────────────────────
weekly_id = None
for g in groups:
    status, resp = asc("GET", f"/v1/subscriptionGroups/{g['id']}/subscriptions?fields[subscriptions]=productId,name,state")
    subs = resp.get("data", [])
    print(f"  Group '{g['attributes'].get('referenceName')}': {len(subs)} sub(s)")
    for s in subs:
        attr = s["attributes"]
        print(f"    → {attr.get('productId')} | {attr.get('name')} | {attr.get('state')}")
        if attr.get("productId") == PRODUCT_ID:
            weekly_id = s["id"]

if not weekly_id:
    print(f"\n⚠️  '{PRODUCT_ID}' not found in App Store Connect.")
    print("Create it first (Monetization → Subscriptions), then re-run this script.")
    exit(0)
print(f"\n✅ Found {PRODUCT_ID}: {weekly_id}")

# ── 4. Get $12.99 price point for USA ─────────────────────────────────────────
print("\nFetching USD price points…")
status, resp = asc("GET", f"/v1/subscriptions/{weekly_id}/pricePoints?filter[territory]=USA&fields[subscriptionPricePoints]=customerPrice,proceeds")
price_points = resp.get("data", [])
target = next((p for p in price_points if p["attributes"]["customerPrice"] == TARGET_PRICE), None)
if not target:
    available = sorted([p["attributes"]["customerPrice"] for p in price_points], key=float)
    print(f"❌ ${TARGET_PRICE} price point not found. Available: {available[:15]}")
    exit(1)
print(f"✅ Found ${TARGET_PRICE} price point: {target['id']}")

# ── 5. Set the price ──────────────────────────────────────────────────────────
print(f"\nSetting {PRODUCT_ID} to ${TARGET_PRICE}…")
status, resp = asc("POST", "/v1/subscriptionPrices", {
    "data": {
        "type": "subscriptionPrices",
        "attributes": {"preserveCurrentPrice": False, "startDate": None},
        "relationships": {
            "subscription": {"data": {"type": "subscriptions", "id": weekly_id}},
            "subscriptionPricePoint": {"data": {"type": "subscriptionPricePoints", "id": target["id"]}},
        },
    }
})
if status in (200, 201):
    print(f"✅ Price set to ${TARGET_PRICE} successfully!")
else:
    print(f"❌ Failed (HTTP {status}):", json.dumps(resp, indent=2))
