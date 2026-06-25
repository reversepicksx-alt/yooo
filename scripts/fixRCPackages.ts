import { getUncachableRevenueCatClient } from "./revenueCatClient";
import { attachProductsToPackage } from "@replit/revenuecat-sdk";

const PROJECT_ID  = "proj3a3fd517";
const PKG_WEEKLY  = "pkgeb49f7dcb82";
const PKG_MONTHLY = "pkge121fc35a48";

// Only iOS app_store products are missing — test_store + play_store already attached
async function fix() {
  const client = await getUncachableRevenueCatClient();

  console.log("Attaching iOS product to $rc_weekly…");
  const { error: e1 } = await attachProductsToPackage({
    client,
    path: { project_id: PROJECT_ID, package_id: PKG_WEEKLY },
    body: { products: [{ product_id: "proddc2bda2346", eligibility_criteria: "all" }] },
  });
  if (e1) console.error("❌ Weekly:", JSON.stringify(e1));
  else console.log("✅ Weekly iOS attached");

  console.log("Attaching iOS product to $rc_monthly…");
  const { error: e2 } = await attachProductsToPackage({
    client,
    path: { project_id: PROJECT_ID, package_id: PKG_MONTHLY },
    body: { products: [{ product_id: "prod2577d37395", eligibility_criteria: "all" }] },
  });
  if (e2) console.error("❌ Monthly:", JSON.stringify(e2));
  else console.log("✅ Monthly iOS attached");
}

fix().catch(err => { console.error("Script failed:", err); process.exit(1); });
