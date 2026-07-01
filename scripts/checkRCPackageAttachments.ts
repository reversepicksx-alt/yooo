import { getUncachableRevenueCatClient } from "./revenueCatClient";
import { listProjects, listApps, listOfferings, listPackages, listProducts } from "@replit/revenuecat-sdk";

async function check() {
  const client = await getUncachableRevenueCatClient();

  const { data: projects } = await listProjects({ client, query: { limit: 20 } });
  const project = projects.items?.find((p: any) => p.name === "Reverse Picks") ?? projects.items?.[0];
  console.log(`Project: ${project.id}`);

  const { data: apps } = await listApps({ client, path: { project_id: project.id }, query: { limit: 20 } });
  const appStoreApp = apps.items.find((a: any) => a.type === "app_store")!;
  console.log(`iOS App ID: ${appStoreApp.id} bundle: ${(appStoreApp as any).app_store?.bundle_id}`);

  const { data: prods } = await listProducts({ client, path: { project_id: project.id }, query: { limit: 100 } });
  const iosProds = prods.items.filter((p: any) => p.app_id === appStoreApp.id);
  console.log(`\niOS Products:`);
  for (const p of iosProds) console.log(`  ${p.store_identifier} → ${p.id}`);

  const { data: offerings } = await listOfferings({ client, path: { project_id: project.id }, query: { limit: 20 } });
  const defaultOff = offerings.items.find((o: any) => o.lookup_key === "default")!;

  const { data: pkgs } = await listPackages({ client, path: { project_id: project.id, offering_id: defaultOff.id }, query: { limit: 20 } });
  console.log(`\nPackages in "default" offering:`);
  for (const pkg of pkgs.items) {
    // Fetch package detail to see attached products
    const resp = await client.get<any>({
      url: "/projects/{project_id}/packages/{package_id}",
      path: { project_id: project.id, package_id: pkg.id },
      query: { expand: ["products", "products.app"] },
    } as any);
    const products = resp.data?.products?.items ?? [];
    console.log(`\n  📌 ${pkg.lookup_key} (${pkg.id}):`);
    if (products.length === 0) {
      console.log(`    ⚠️  NO PRODUCTS ATTACHED`);
    }
    for (const p of products) {
      const storeType = p.app?.type ?? "?";
      console.log(`    [${storeType}] ${p.store_identifier} — eligibility: ${p.eligibility_criteria}`);
    }
  }
}

check().catch(err => { console.error("Failed:", err); process.exit(1); });
