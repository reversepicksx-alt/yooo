import { listConnections } from "replit";

const PROJECT_ID = 'proj3a3fd517';
const APP_ID = 'appce9b46665f';

async function main() {
  const issuerId = process.env.ASC_ISSUER_ID;
  const keyId = process.env.ASC_KEY_ID;
  const privateKey = process.env.ASC_PRIVATE_KEY;

  if (!issuerId || !keyId || !privateKey) {
    console.error("❌ Missing ASC secrets. Set ASC_ISSUER_ID, ASC_KEY_ID, and ASC_PRIVATE_KEY.");
    process.exit(1);
  }

  // Get RevenueCat access token from the connection
  const conns = await listConnections('revenuecat');
  if (conns.length === 0) {
    console.error("❌ No RevenueCat connection found.");
    process.exit(1);
  }
  const settings = conns[0].settings || {};
  let token: string | undefined = settings.access_token;
  if (!token && settings.oauth?.credentials?.access_token) {
    token = settings.oauth.credentials.access_token;
  }
  if (!token) {
    console.error("❌ Could not get RevenueCat access token.");
    process.exit(1);
  }

  const body = {
    app_store: {
      app_store_connect_api_key: {
        key: privateKey,
        key_id: keyId,
        issuer_id: issuerId,
      },
      bundle_id: "com.reversepicks.app",
    },
  };

  console.log(`📤 Updating app ${APP_ID} with ASC key...`);
  console.log(`   Issuer ID: ${issuerId.slice(0, 20)}...`);
  console.log(`   Key ID: ${keyId}`);
  console.log(`   Private Key length: ${privateKey.length} chars`);

  const url = `https://api.revenuecat.com/v2/projects/${PROJECT_ID}/apps/${APP_ID}`;
  const resp = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const data = await resp.json().catch(() => null);
  console.log("\n📊 Status:", resp.status);
  console.log("📊 Response:", JSON.stringify(data, null, 2));

  if (resp.status === 200 || resp.status === 201) {
    console.log("\n✅ ASC key configured successfully!");
  } else {
    console.error("\n❌ Failed to configure ASC key.");
    process.exit(1);
  }
}

main().catch(err => { console.error("❌ Error:", err); process.exit(1); });
