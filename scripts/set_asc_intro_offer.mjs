// Configure the 3-day introductory free trial for reversepicks_weekly.
// This is intentionally idempotent: it lists existing offers first and only
// creates the requested offer when the same offer is not already present.
import crypto from 'crypto';
import https from 'https';

const KEY_ID = process.env.ASC_KEY_ID;
const ISSUER_ID = process.env.ASC_ISSUER_ID;
const PRIVATE_KEY = process.env.ASC_PRIVATE_KEY;
const BUNDLE_ID = 'com.reversepicks.app';
const PRODUCT_ID = 'reversepicks_weekly';

if (!KEY_ID || !ISSUER_ID || !PRIVATE_KEY) {
  console.error('Missing ASC_KEY_ID / ASC_ISSUER_ID / ASC_PRIVATE_KEY');
  process.exit(1);
}

function normalizedPrivateKey(value) {
  const body = value
    .replace(/-----BEGIN[^-]+-----/g, '')
    .replace(/-----END[^-]+-----/g, '')
    .replace(/\s+/g, '');
  if (!body) throw new Error('ASC_PRIVATE_KEY body is empty');
  const lines = body.match(/.{1,64}/g) ?? [];
  return `-----BEGIN PRIVATE KEY-----\n${lines.join('\n')}\n-----END PRIVATE KEY-----\n`;
}

function makeJwt() {
  const header = { alg: 'ES256', kid: KEY_ID, typ: 'JWT' };
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    iss: ISSUER_ID,
    iat: now,
    exp: now + 1200,
    aud: 'appstoreconnect-v1',
  };
  const b64 = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const unsigned = `${b64(header)}.${b64(payload)}`;
  const signer = crypto.createSign('SHA256');
  signer.update(unsigned);
  const signature = signer.sign({
    key: crypto.createPrivateKey({ key: normalizedPrivateKey(PRIVATE_KEY), format: 'pem' }),
    dsaEncoding: 'ieee-p1363',
  });
  return `${unsigned}.${signature.toString('base64url')}`;
}

function asc(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const request = https.request({
      hostname: 'api.appstoreconnect.apple.com',
      path,
      method,
      headers: {
        Authorization: `Bearer ${makeJwt()}`,
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, (response) => {
      let raw = '';
      response.on('data', (chunk) => { raw += chunk; });
      response.on('end', () => {
        let parsed;
        try {
          parsed = raw ? JSON.parse(raw) : {};
        } catch {
          parsed = { raw };
        }
        resolve({ status: response.statusCode ?? 0, body: parsed });
      });
    });
    request.on('error', reject);
    if (data) request.write(data);
    request.end();
  });
}

function fail(label, response) {
  console.error(`${label} failed (HTTP ${response.status}): ${JSON.stringify(response.body)}`);
  process.exit(1);
}

async function main() {
  const apps = await asc(
    'GET',
    `/v1/apps?filter[bundleId]=${encodeURIComponent(BUNDLE_ID)}&fields[apps]=name,bundleId`,
  );
  if (apps.status !== 200 || !apps.body?.data?.length) fail('App lookup', apps);
  const appId = apps.body.data[0].id;
  console.log(`Found app ${BUNDLE_ID}`);

  const groups = await asc(
    'GET',
    `/v1/apps/${appId}/subscriptionGroups?fields[subscriptionGroups]=referenceName`,
  );
  if (groups.status !== 200) fail('Subscription-group lookup', groups);

  let subscriptionId;
  for (const group of groups.body?.data ?? []) {
    const subscriptions = await asc(
      'GET',
      `/v1/subscriptionGroups/${group.id}/subscriptions?fields[subscriptions]=productId,name,state`,
    );
    if (subscriptions.status !== 200) fail('Subscription lookup', subscriptions);
    const match = (subscriptions.body?.data ?? []).find(
      (subscription) => subscription.attributes?.productId === PRODUCT_ID,
    );
    if (match) {
      subscriptionId = match.id;
      console.log(`Found ${PRODUCT_ID} in subscription group ${group.id}`);
      break;
    }
  }
  if (!subscriptionId) {
    console.error(`${PRODUCT_ID} was not found in App Store Connect.`);
    process.exit(1);
  }

  const existing = await asc(
    'GET',
    `/v1/subscriptions/${subscriptionId}/introductoryOffers?fields[subscriptionIntroductoryOffers]=duration,offerMode,numberOfPeriods`,
  );
  if (existing.status !== 200) fail('Introductory-offer lookup', existing);

  const sameOffer = (existing.body?.data ?? []).find((offer) => {
    const attrs = offer.attributes ?? {};
    return attrs.duration === 'THREE_DAYS'
      && attrs.offerMode === 'FREE_TRIAL'
      && attrs.numberOfPeriods === 1;
  });
  if (sameOffer) {
    console.log(`3-day free trial already configured (${sameOffer.id}); no changes needed.`);
    return;
  }

  const created = await asc('POST', '/v1/subscriptionIntroductoryOffers', {
    data: {
      type: 'subscriptionIntroductoryOffers',
      attributes: {
        duration: 'THREE_DAYS',
        offerMode: 'FREE_TRIAL',
        numberOfPeriods: 1,
      },
      relationships: {
        subscription: {
          data: { type: 'subscriptions', id: subscriptionId },
        },
        territory: {
          data: { type: 'territories', id: 'USA' },
        },
      },
    },
  });
  if (![200, 201].includes(created.status)) fail('Introductory-offer creation', created);
  console.log('Created 3-day free trial for reversepicks_weekly.');
}

main().catch((error) => {
  console.error(`App Store Connect request failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});