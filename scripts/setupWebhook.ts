import { getUncachableRevenueCatClient } from "./revenueCatClient";

const PROJECT_ID = "proj3a3fd517";
const WEBHOOK_URL = "https://reversepicks.com/api/webhooks/revenuecat";
const WEBHOOK_SECRET = "1a2236b240fd405fb3f5d7ee86970813";

async function main() {
  const client = await getUncachableRevenueCatClient();

  // List existing webhooks first
  console.log("Checking existing webhooks...");
  try {
    const existing = await client.GET("/projects/{project_id}/webhooks" as any, {
      params: { path: { project_id: PROJECT_ID } },
    } as any);
    const webhooks = (existing as any)?.data?.items ?? [];
    console.log(`Found ${webhooks.length} existing webhook(s)`);
    for (const wh of webhooks) {
      console.log("  Existing:", wh.id, wh.url);
      if (wh.url === WEBHOOK_URL) {
        console.log("  ✅ Webhook already configured for this URL — nothing to do.");
        return;
      }
    }
  } catch (e) {
    console.log("Could not list webhooks (may not exist yet):", e);
  }

  // Create the webhook
  console.log("Creating webhook...");
  const res = await (client as any).POST(`/projects/${PROJECT_ID}/webhooks`, {
    body: {
      url: WEBHOOK_URL,
      authorization: WEBHOOK_SECRET,
    },
  });

  console.log("Response status:", res?.response?.status);
  console.log("Response data:", JSON.stringify(res?.data, null, 2));
  console.log("Response error:", JSON.stringify(res?.error, null, 2));
}

main().catch(console.error);
