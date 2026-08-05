// Close the USA 3-day introductory offer for reversepicks_weekly at a
// caller-provided UTC deadline. Existing subscriptions and already-granted
// trials are not changed.
import crypto from 'crypto';
import https from 'https';

const KEY_ID = process.env.ASC_KEY_ID;
const ISSUER_ID = process.env.ASC_ISSUER_ID;
const PRIVATE_KEY = process.env.ASC_PRIVATE_KEY;
const BUNDLE_ID = 'com.reversepicks.app';
const PRODUCT_ID = 'reversepicks_weekly';
const TARGET_UTC = process.env.TARGET_UTC;
const DRY_RUN = process.argv.includes('--dry-run');
const NO_WAIT = process.argv.includes('--no-wait');

if (!KEY_ID || !ISSUER_ID || !PRIVATE_KEY) {
  console.error('Missing ASC_KEY_ID / ASC_ISSUER_ID / ASC_PRIVATE_KEY');
  process.exit(1);
}

if (!TARGET_UTC || Number.isNaN(Date.parse(TARGET_UTC))) {
  console.error('TARGET_UTC must be a valid ISO-8601 UTC timestamp');
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

function asc(method, path) {
  return new Promise((resolve, reject) => {
    const request = https.request({
      hostname: 'api.appstoreconnect.apple.com',
      path,
      method,
      headers: {
        Authorization: `Bearer ${makeJwt()}`,
        Accept: 'application/json',
      },
    }, (response) => {
      let raw = '';
      response.on('data', (chunk) => { raw += chunk; });
      response.on('end', () => {
        let parsed = {};
        try {
          parsed = raw ? JSON.parse(raw) : {};
        } catch {
          parsed = { raw };
        }
        resolve({ status: response.statusCode ?? 0, body: parsed });
      });
    });
    request.on('error', reject);
    request.end();
  });
}

function fail(label, response) {
  throw new Error(`${label} failed (HTTP ${response.status}): ${JSON.stringify(response.body)}`);
}

function isTargetOffer(offer) {
  const attrs = offer.attributes ?? {};
  const territoryId = offer.relationships?.territory?.data?.id;
  let encodedTerritory = null;
  try {
    const decoded = JSON.parse(Buffer.from(offer.id, 'base64url').toString('utf8'));
    encodedTerritory = decoded?.i ?? null;
  } catch {
    // Some Apple identifiers are not JSON-encoded; relationship data is used.
  }
  return attrs.duration === 'THREE_DAYS'
    && attrs.offerMode === 'FREE_TRIAL'
    && attrs.numberOfPeriods === 1
    && (territoryId === 'USA' || territoryId === 'US' || encodedTerritory === 'US');
}

async function waitForCutoff() {
  const target = Date.parse(TARGET_UTC);
  const remaining = target - Date.now();
  if (remaining <= 0 || NO_WAIT) return;
  console.log(`Scheduled for ${new Date(target).toISOString()} (waiting ${Math.ceil(remaining / 1000)}s).`);
  await new Promise((resolve) => setTimeout(resolve, remaining));
}

async function main() {
  await waitForCutoff();
  console.log(`Processing Apple introductory-offer cutoff at ${new Date().toISOString()}.`);

  const apps = await asc(
    'GET',
    `/v1/apps?filter[bundleId]=${encodeURIComponent(BUNDLE_ID)}&fields[apps]=name,bundleId`,
  );
  if (apps.status !== 200 || !apps.body?.data?.length) fail('App lookup', apps);
  const appId = apps.body.data[0].id;

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
      break;
    }
  }
  if (!subscriptionId) throw new Error(`${PRODUCT_ID} was not found in App Store Connect.`);

  const offers = await asc(
    'GET',
    `/v1/subscriptions/${subscriptionId}/introductoryOffers?include=territory&fields[subscriptionIntroductoryOffers]=duration,offerMode,numberOfPeriods`,
  );
  if (offers.status !== 200) fail('Introductory-offer lookup', offers);

  const targets = (offers.body?.data ?? []).filter(isTargetOffer);
  if (DRY_RUN) {
    console.log(JSON.stringify((offers.body?.data ?? []).map((offer) => ({
      id: offer.id,
      attributes: offer.attributes,
      territoryId: offer.relationships?.territory?.data?.id ?? null,
    })), null, 2));
  }
  if (!targets.length) {
    console.log('USA 3-day introductory offer is already closed; no changes needed.');
    return;
  }

  console.log(`Found ${targets.length} USA 3-day introductory offer(s) for ${PRODUCT_ID}.`);
  if (DRY_RUN) {
    console.log('Dry run: no offers deleted.');
    return;
  }

  for (const offer of targets) {
    const deleted = await asc('DELETE', `/v1/subscriptionIntroductoryOffers/${offer.id}`);
    if (deleted.status !== 204) fail(`Introductory-offer deletion ${offer.id}`, deleted);
    console.log(`Deleted USA 3-day introductory offer ${offer.id}.`);
  }
}

main().catch((error) => {
  console.error(`Apple introductory-offer cutoff failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});