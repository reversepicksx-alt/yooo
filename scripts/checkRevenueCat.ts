import { getUncachableRevenueCatClient } from "./revenueCatClient";
import {
  listProjects, listApps, listAppPublicApiKeys,
  listProducts, listEntitlements, listOfferings, listPackages,
} from "@replit/revenuecat-sdk";

async function checkRevenueCat() {
  const client = await getUncachableRevenueCatClient();

  // Project
  const { data: projects } = await listProjects({ client, query: { limit: 20 } });
  const project = projects.items?.find((p: any) => p.name === "Reverse Picks") ?? projects.items?.[0];
  if (!project) { console.log("❌ No project found"); return; }
  console.log(`✅ Project: ${project.name} (${project.id})`);

  // Apps
  const { data: apps } = await listApps({ client, path: { project_id: project.id }, query: { limit: 20 } });
  console.log(`\n📱 Apps (${apps.items.length}):`);
  for (const app of apps.items) {
    const { data: keys } = await listAppPublicApiKeys({ client, path: { project_id: project.id, app_id: app.id } });
    const key = keys?.items?.[0]?.key ?? "N/A";
    const bundleId = (app as any).app_store?.bundle_id ?? (app as any).play_store?.package_name ?? "N/A";
    console.log(`  [${app.type}] ${app.name} — bundle: ${bundleId} — key: ${key.slice(0,20)}...`);
  }

  // Products
  const { data: prods } = await listProducts({ client, path: { project_id: project.id }, query: { limit: 100 } });
  console.log(`\n📦 Products (${prods.items.length}):`);
  for (const p of prods.items) {
    const appObj = apps.items.find((a: any) => a.id === p.app_id);
    console.log(`  [${appObj?.type ?? '?'}] ${p.store_identifier} — "${p.display_name}" — id: ${p.id}`);
  }

  // Entitlements
  const { data: ents } = await listEntitlements({ client, path: { project_id: project.id }, query: { limit: 20 } });
  console.log(`\n🔑 Entitlements (${ents.items.length}):`);
  for (const e of ents.items) {
    console.log(`  ${e.lookup_key} — ${e.display_name} (${e.id})`);
  }

  // Offerings + packages
  const { data: offerings } = await listOfferings({ client, path: { project_id: project.id }, query: { limit: 20 } });
  console.log(`\n🎁 Offerings (${offerings.items.length}):`);
  for (const off of offerings.items) {
    console.log(`  ${off.lookup_key} — current: ${(off as any).is_current} (${off.id})`);
    const { data: pkgs } = await listPackages({ client, path: { project_id: project.id, offering_id: off.id }, query: { limit: 20 } });
    for (const pkg of pkgs.items) {
      console.log(`    📌 ${pkg.lookup_key} — "${pkg.display_name}" (${pkg.id})`);
    }
  }

  console.log("\n✅ Diagnostic complete");
}

checkRevenueCat().catch(err => { console.error("❌ Failed:", err); process.exit(1); });
