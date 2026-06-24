// App Store Connect API — set reversepicks_weekly to $12.99
// Uses only Node.js built-ins (crypto + https) — no extra packages needed.

import crypto from 'crypto';
import https from 'https';

const KEY_ID     = process.env.ASC_KEY_ID;
const ISSUER_ID  = process.env.ASC_ISSUER_ID;
const PRIVATE_KEY = process.env.ASC_PRIVATE_KEY;

if (!KEY_ID || !ISSUER_ID || !PRIVATE_KEY) {
  console.error('Missing ASC_KEY_ID / ASC_ISSUER_ID / ASC_PRIVATE_KEY');
  process.exit(1);
}

// ── JWT ───────────────────────────────────────────────────────────────────────
function makeJwt() {
  const header  = { alg: 'ES256', kid: KEY_ID, typ: 'JWT' };
  const now     = Math.floor(Date.now() / 1000);
  const payload = { iss: ISSUER_ID, iat: now, exp: now + 1200, aud: 'appstoreconnect-v1' };

  const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');
  const unsigned = `${b64(header)}.${b64(payload)}`;

  const sign = crypto.createSign('SHA256');
  sign.update(unsigned);
  const keyObj = crypto.createPrivateKey({ key: PRIVATE_KEY, format: 'pem' });
  const sigDer = sign.sign({ key: keyObj, dsaEncoding: 'ieee-p1363' });
  const sig = sigDer.toString('base64url');
  return `${unsigned}.${sig}`;
}

// ── HTTPS helper ──────────────────────────────────────────────────────────────
function asc(method, path, body) {
  return new Promise((resolve, reject) => {
    const token = makeJwt();
    const data  = body ? JSON.stringify(body) : null;
    const opts  = {
      hostname: 'api.appstoreconnect.apple.com',
      path,
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    };
    const req = https.request(opts, (res) => {
      let raw = '';
      res.on('data', (c) => (raw += c));
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(raw) }); }
        catch { resolve({ status: res.statusCode, body: raw }); }
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  // 1. Find the app
  console.log('Looking up apps…');
  const apps = await asc('GET', '/v1/apps?filter[bundleId]=com.reversepicks.app&fields[apps]=name,bundleId', null);
  const appData = apps.body?.data;
  if (!appData?.length) {
    console.error('App not found. Response:', JSON.stringify(apps.body, null, 2));
    process.exit(1);
  }
  const appId = appData[0].id;
  console.log(`✅ App: ${appData[0].attributes?.name} (${appId})`);

  // 2. Find subscription groups
  console.log('\nLooking up subscription groups…');
  const groups = await asc('GET', `/v1/apps/${appId}/subscriptionGroups?fields[subscriptionGroups]=referenceName`, null);
  const groupData = groups.body?.data ?? [];
  console.log(`Found ${groupData.length} group(s)`);
  if (!groupData.length) {
    console.log('No subscription groups found yet — you need to create them in App Store Connect first.');
    console.log('Tip: go to appstoreconnect.apple.com → your app → Monetization → Subscriptions → create a group.');
    process.exit(0);
  }

  // 3. Find reversepicks_weekly subscription in any group
  let weeklySubId = null;
  for (const group of groupData) {
    const subs = await asc('GET', `/v1/subscriptionGroups/${group.id}/subscriptions?fields[subscriptions]=productId,name,state`, null);
    const subData = subs.body?.data ?? [];
    console.log(`  Group "${group.attributes?.referenceName}": ${subData.length} subscription(s)`);
    for (const s of subData) {
      console.log(`    → ${s.attributes?.productId} | ${s.attributes?.name} | ${s.attributes?.state}`);
      if (s.attributes?.productId === 'reversepicks_weekly') {
        weeklySubId = s.id;
      }
    }
  }

  if (!weeklySubId) {
    console.log('\n⚠️  reversepicks_weekly not found in App Store Connect yet.');
    console.log('You need to create it first in the App Store Connect dashboard.');
    console.log('Once created, re-run this script to set the $12.99 price.');
    process.exit(0);
  }

  console.log(`\n✅ Found reversepicks_weekly: ${weeklySubId}`);

  // 4. List existing prices
  console.log('Checking existing prices…');
  const existingPrices = await asc('GET', `/v1/subscriptions/${weeklySubId}/prices?include=subscriptionPricePoint&limit=5`, null);
  const priceData = existingPrices.body?.data ?? [];
  console.log(`Found ${priceData.length} existing price(s)`);

  // 5. Get the price point for $12.99 USD
  console.log('\nFetching price points for USD…');
  const ppRes = await asc('GET', `/v1/subscriptions/${weeklySubId}/pricePoints?filter[territory]=USA&fields[subscriptionPricePoints]=customerPrice,proceeds`, null);
  const pricePoints = ppRes.body?.data ?? [];
  const target = pricePoints.find(pp => pp.attributes?.customerPrice === '12.99');
  if (!target) {
    console.log('$12.99 price point not found. Available USD prices:');
    pricePoints.slice(0, 10).forEach(pp => console.log(`  $${pp.attributes?.customerPrice}`));
    // Try $12.99 by finding closest
    const sorted = pricePoints
      .filter(pp => pp.attributes?.customerPrice)
      .sort((a, b) => Math.abs(parseFloat(a.attributes.customerPrice) - 12.99) - Math.abs(parseFloat(b.attributes.customerPrice) - 12.99));
    console.log(`\nClosest match: $${sorted[0]?.attributes?.customerPrice} (id=${sorted[0]?.id})`);
    process.exit(1);
  }
  console.log(`✅ Found $12.99 price point: ${target.id}`);

  // 6. Set the price
  console.log('\nSetting price to $12.99…');
  const setRes = await asc('POST', '/v1/subscriptionPrices', {
    data: {
      type: 'subscriptionPrices',
      attributes: { preserveCurrentPrice: false, startDate: null },
      relationships: {
        subscription: { data: { type: 'subscriptions', id: weeklySubId } },
        subscriptionPricePoint: { data: { type: 'subscriptionPricePoints', id: target.id } },
      },
    },
  });

  if (setRes.status === 201 || setRes.status === 200) {
    console.log('✅ Price set to $12.99 successfully!');
  } else {
    console.error('❌ Failed to set price:', JSON.stringify(setRes.body, null, 2));
  }
}

main().catch(console.error);
