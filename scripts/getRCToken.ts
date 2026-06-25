import { listConnections } from "replit";

async function main() {
  const conns = await listConnections('revenuecat');
  if (conns.length === 0) {
    console.log("No RevenueCat connection found");
    return;
  }
  const conn = conns[0];
  console.log("Connection ID:", conn.id);
  console.log("Status:", conn.status);
  console.log("Settings keys:", Object.keys(conn.settings || {}));
  console.log("Settings:", JSON.stringify(conn.settings, null, 2));
  console.log("Metadata:", JSON.stringify(conn.metadata, null, 2));
  console.log("\nClient:", JSON.stringify(conn.getClient(), null, 2));

  // Try to get the access token
  const settings = conn.settings || {};
  let token = settings.access_token;
  if (!token && settings.oauth && typeof settings.oauth === 'object') {
    if (settings.oauth.credentials) {
      token = settings.oauth.credentials.access_token;
    }
  }
  console.log("\n✅ Token found:", !!token);
  if (token) {
    console.log("Token prefix:", token.slice(0, 20) + '...');
  }
}

main().catch(err => { console.error(err); process.exit(1); });
