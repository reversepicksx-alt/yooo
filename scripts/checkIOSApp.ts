import { getUncachableRevenueCatClient } from "./revenueCatClient";
import { getApp, updateApp } from "@replit/revenuecat-sdk";

async function main() {
  const client = await getUncachableRevenueCatClient();
  const result = await getApp({
    client,
    path: {
      project_id: 'proj3a3fd517',
      app_id: 'appce9b46665f',
    },
  });
  console.log(JSON.stringify(result, null, 2));
}

main().catch(err => { console.error(err); process.exit(1); });
