import os, time, json, urllib.request, urllib.error
import jwt

KEY_ID       = os.environ["ASC_KEY_ID"]
ISSUER_ID    = os.environ["ASC_ISSUER_ID"]
PRIVATE_KEY  = os.environ["ASC_PRIVATE_KEY"]
BUNDLE_ID    = "com.reversepicks.app"
APP_ID       = "6781092173"

PLANS = [
    {"product_id": "reversepicks_monthly", "name": "Monthly", "duration": "ONE_MONTH", "price": "36.99"},
]

def make_token():
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER_ID, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        PRIVATE_KEY, algorithm="ES256", headers={"kid": KEY_ID},
    )

def asc(method, path, body=None):
    token = make_token()
    data  = json.dumps(body).encode() if body else None
    req   = urllib.request.Request(
        f"https://api.appstoreconnect.apple.com{path}",
        data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def ok(status): return status in (200, 201)

# ── 1. Find or create subscription group ──────────────────────────────────────
print("── Subscription group ───────────────────")
status, resp = asc("GET", f"/v1/apps/{APP_ID}/subscriptionGroups?fields[subscriptionGroups]=referenceName")
groups = resp.get("data", [])
print(f"Existing groups: {len(groups)}")

group_id = None
for g in groups:
    print(f"  Found: {g['attributes']['referenceName']} ({g['id']})")
    group_id = g["id"]  # use first existing

if not group_id:
    print("Creating subscription group…")
    status, resp = asc("POST", "/v1/subscriptionGroups", {
        "data": {
            "type": "subscriptionGroups",
            "attributes": {"referenceName": "Reverse Picks Pro"},
            "relationships": {"app": {"data": {"type": "apps", "id": APP_ID}}},
        }
    })
    if ok(status):
        group_id = resp["data"]["id"]
        print(f"✅ Group created: {group_id}")
    else:
        print(f"❌ Group creation failed (HTTP {status}):", json.dumps(resp, indent=2))
        exit(1)

# ── 2. Find or create each subscription ───────────────────────────────────────
print("\n── Subscriptions ────────────────────────")
status, resp = asc("GET", f"/v1/subscriptionGroups/{group_id}/subscriptions?fields[subscriptions]=productId,name,state")
existing_subs = {s["attributes"]["productId"]: s["id"] for s in resp.get("data", [])}
print(f"Existing subs: {list(existing_subs.keys())}")

sub_ids = {}
for plan in PLANS:
    pid = plan["product_id"]
    if pid in existing_subs:
        sub_ids[pid] = existing_subs[pid]
        print(f"  ✅ Already exists: {pid} ({sub_ids[pid]})")
        continue

    print(f"  Creating {pid}…")
    status, resp = asc("POST", "/v1/subscriptions", {
        "data": {
            "type": "subscriptions",
            "attributes": {
                "productId":             pid,
                "name":                  plan["name"],
                "subscriptionPeriod":    plan["duration"],
                "reviewNote":            "Reverse Picks Pro subscription — soccer player props analytics.",
                "familySharable":        False,
            },
            "relationships": {
                "group": {"data": {"type": "subscriptionGroups", "id": group_id}},
            },
        }
    })
    if ok(status):
        sub_ids[pid] = resp["data"]["id"]
        print(f"  ✅ Created: {pid} ({sub_ids[pid]})")
    else:
        print(f"  ❌ Failed (HTTP {status}):", json.dumps(resp, indent=2))

# ── 3. Add English localization (required before review) ──────────────────────
print("\n── Localizations ────────────────────────")
DESCRIPTIONS = {
    "reversepicks_weekly":  "7-day Pro access. Soccer player props analytics.",
    "reversepicks_monthly": "Monthly Pro access. Soccer player props analytics.",
}
for plan in PLANS:
    pid = plan["product_id"]
    if pid not in sub_ids: continue
    sub_id = sub_ids[pid]

    # check existing
    status, resp = asc("GET", f"/v1/subscriptions/{sub_id}/subscriptionLocalizations")
    locs = resp.get("data", [])
    has_en = any(l["attributes"].get("locale","").startswith("en") for l in locs)
    if has_en:
        print(f"  ✅ {pid}: English localization exists")
        continue

    print(f"  Adding English localization for {pid}…")
    status, resp = asc("POST", "/v1/subscriptionLocalizations", {
        "data": {
            "type": "subscriptionLocalizations",
            "attributes": {
                "locale":      "en-US",
                "name":        plan["name"],
                "description": DESCRIPTIONS[pid],
            },
            "relationships": {
                "subscription": {"data": {"type": "subscriptions", "id": sub_id}},
            },
        }
    })
    if ok(status):
        print(f"  ✅ Localization added for {pid}")
    else:
        print(f"  ❌ Localization failed (HTTP {status}):", json.dumps(resp, indent=2))

# ── 4. Set prices ─────────────────────────────────────────────────────────────
print("\n── Prices ───────────────────────────────")
for plan in PLANS:
    pid = plan["product_id"]
    if pid not in sub_ids: continue
    sub_id = sub_ids[pid]
    target_price = plan["price"]

    print(f"  Fetching price points for {pid}…")
    all_pps = []
    next_url = f"/v1/subscriptions/{sub_id}/pricePoints?filter[territory]=USA&fields[subscriptionPricePoints]=customerPrice,proceeds&limit=200"
    while next_url:
        # next_url may be a full URL or a path
        path = next_url if next_url.startswith("/") else "/" + next_url.split("appstoreconnect.apple.com", 1)[-1]
        status, resp = asc("GET", path)
        all_pps.extend(resp.get("data", []))
        raw_next = resp.get("links", {}).get("next")
        next_url = raw_next if raw_next and raw_next != next_url else None
    pps = all_pps
    print(f"  Total price points fetched: {len(pps)}")
    pp = next((p for p in pps if p["attributes"]["customerPrice"] == target_price), None)
    if not pp:
        available = sorted([p["attributes"]["customerPrice"] for p in pps], key=float)
        print(f"  ❌ ${target_price} not found. Sample (highest 10): {available[-10:]}")
        continue
    print(f"  ✅ Found ${target_price} price point: {pp['id']}")

    from datetime import date, timedelta
    start_date = (date.today() + timedelta(days=3)).isoformat()
    status, resp = asc("POST", "/v1/subscriptionPrices", {
        "data": {
            "type": "subscriptionPrices",
            "attributes": {"preserveCurrentPrice": False, "startDate": start_date},
            "relationships": {
                "subscription":           {"data": {"type": "subscriptions",           "id": sub_id}},
                "subscriptionPricePoint": {"data": {"type": "subscriptionPricePoints", "id": pp["id"]}},
            },
        }
    })
    if ok(status):
        print(f"  ✅ {pid} → ${target_price} (effective {start_date})")
    else:
        print(f"  ❌ Price failed (HTTP {status}):", json.dumps(resp, indent=2))

print("\n✅ Done")
