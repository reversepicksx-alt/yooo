import { getUncachableRevenueCatClient } from "./revenueCatClient";
import {
  listOfferings, listPackages, deletePackageFromOffering,
  listProducts,
  listWebhookIntegrations, createWebhookIntegration,
} from "@replit/revenuecat-sdk";

const PROJECT_ID = "proj3a3fd517";
const WEBHOOK_URL = "https://reversepicks.com/api/webhooks/revenuecat";
const WEBHOOK_SECRET = "1a2236b240fd405fb3f5d7ee86970813";

async function main() {
  const client = await getUncachableRevenueCatClient();

  // ── 1. Webhook ─────────────────────────────────────────────────────────────
  console.log("\n── Webhook ──────────────────────────────");
  const existingHooks = await listWebhookIntegrations({ client, path: { project_id: PROJECT_ID } });
  const hooks = (existingHooks.data as any)?.items ?? [];
  console.log(`Found ${hooks.length} existing webhook(s)`);
  const alreadySet = hooks.find((h: any) => h.url === WEBHOOK_URL);
  if (alreadySet) {
    console.log("✅ Webhook already configured:", alreadySet.id);
  } else {
    const res = await createWebhookIntegration({
      client,
      path: { project_id: PROJECT_ID },
      body: {
        name: "Reverse Picks Production",
        url: WEBHOOK_URL,
        authorization_header: WEBHOOK_SECRET,
        environment: "production",
      } as any,
    });
    if (res.error) {
      console.error("❌ Webhook creation failed:", JSON.stringify(res.error, null, 2));
    } else {
      console.log("✅ Webhook created:", (res.data as any)?.id ?? JSON.stringify(res.data));
    }
  }

  // ── 2. Find real offering ID ───────────────────────────────────────────────
  console.log("\n── Offerings ────────────────────────────");
  const offsRes = await listOfferings({ client, path: { project_id: PROJECT_ID } });
  const offerings = (offsRes.data as any)?.items ?? [];
  console.log(`Found ${offerings.length} offering(s):`);
  for (const o of offerings) {
    console.log(`  id=${o.id} | identifier=${o.identifier} | name=${o.display_name}`);
  }
  const defaultOffering = offerings.find((o: any) => o.identifier === "default") ?? offerings[0];
  if (!defaultOffering) {
    console.error("❌ No offering found — aborting package removal");
    return;
  }
  console.log(`Using offering: ${defaultOffering.id} (${defaultOffering.identifier})`);

  // ── 3. Remove quarterly package ────────────────────────────────────────────
  console.log("\n── Packages ─────────────────────────────");
  const pkgsRes = await listPackages({
    client,
    path: { project_id: PROJECT_ID, offering_id: defaultOffering.id },
  });
  const pkgs = (pkgsRes.data as any)?.items ?? [];
  console.log(`Found ${pkgs.length} package(s):`);
  for (const p of pkgs) {
    console.log(`  id=${p.id} | identifier=${p.identifier} | name=${p.display_name}`);
  }

  const quarterly = pkgs.find((p: any) =>
    p.identifier === "$rc_three_month" ||
    (p.display_name ?? "").toLowerCase().includes("quarter")
  );
  if (!quarterly) {
    console.log("ℹ️  Quarterly package not found / already removed");
  } else {
    console.log(`Removing quarterly: ${quarterly.id}`);
    const delRes = await deletePackageFromOffering({
      client,
      path: { project_id: PROJECT_ID, offering_id: defaultOffering.id, package_id: quarterly.id },
    });
    if (delRes.error) {
      console.error("❌ Delete failed:", JSON.stringify(delRes.error, null, 2));
    } else {
      console.log("✅ Quarterly package removed from offering");
    }
  }

  // ── 4. Show products (price update note) ──────────────────────────────────
  console.log("\n── Products ─────────────────────────────");
  const prodsRes = await listProducts({ client, path: { project_id: PROJECT_ID } });
  const prods = (prodsRes.data as any)?.items ?? [];
  console.log(`Found ${prods.length} product(s):`);
  for (const p of prods) {
    console.log(`  id=${p.id} | name=${p.display_name} | store_identifier=${p.store_identifier ?? "—"}`);
  }
  const weeklyProd = prods.find((p: any) =>
    (p.display_name ?? "").toLowerCase() === "weekly" &&
    !(p.display_name ?? "").includes("Android") &&
    !(p.display_name ?? "").includes("iOS")
  );
  if (weeklyProd) {
    console.log(`\nWeekly product ID: ${weeklyProd.id}`);
    console.log("ℹ️  Test store price → update via RC dashboard (Projects → Products → Weekly → Edit price)");
    console.log("ℹ️  App Store Connect price → set $13 under Subscriptions → reversepicks_weekly → Prices");
  }

  console.log("\n✅ Script complete");
}

main().catch(console.error);
