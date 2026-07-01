import { getUncachableRevenueCatClient } from "./revenueCatClient";
import {
  listProjects, createProject,
  listApps, createApp,
  listAppPublicApiKeys,
  listProducts, createProduct,
  listEntitlements, createEntitlement,
  attachProductsToEntitlement,
  listOfferings, createOffering, updateOffering,
  listPackages, createPackages,
  attachProductsToPackage,
  type App, type Product, type Project,
  type Entitlement, type Offering, type Package,
  type CreateProductData,
} from "@replit/revenuecat-sdk";

const PROJECT_NAME = "Reverse Picks";
const APP_STORE_APP_NAME = "Reverse Picks iOS";
const APP_STORE_BUNDLE_ID = "com.reversepicks.app";
const PLAY_STORE_APP_NAME = "Reverse Picks Android";
const PLAY_STORE_PACKAGE_NAME = "com.reversepicks.app";

const ENTITLEMENT_IDENTIFIER = "pro";
const ENTITLEMENT_DISPLAY_NAME = "Pro Access";

const OFFERING_IDENTIFIER = "default";
const OFFERING_DISPLAY_NAME = "Default Offering";

type TestStorePricesResponse = { object: string; prices: { amount_micros: number; currency: string }[] };

const PRODUCTS = [
  {
    identifier: "reversepicks_weekly",
    playIdentifier: "reversepicks_weekly:weekly",
    displayName: "Weekly",
    duration: "P1W" as const,
    packageId: "$rc_weekly",
    packageName: "Weekly",
    prices: [
      { amount_micros: 15000000, currency: "USD" },
    ],
  },
  {
    identifier: "reversepicks_monthly",
    playIdentifier: "reversepicks_monthly:monthly",
    displayName: "Monthly",
    duration: "P1M" as const,
    packageId: "$rc_monthly",
    packageName: "Monthly",
    prices: [
      { amount_micros: 49990000, currency: "USD" },
    ],
  },
  {
    identifier: "reversepicks_quarterly",
    playIdentifier: "reversepicks_quarterly:quarterly",
    displayName: "Quarterly",
    duration: "P3M" as const,
    packageId: "$rc_three_month",
    packageName: "Quarterly",
    prices: [
      { amount_micros: 99990000, currency: "USD" },
    ],
  },
];

async function seedRevenueCat() {
  const client = await getUncachableRevenueCatClient();

  // ── Project ──────────────────────────────────────────────────────────────
  let project: Project;
  const { data: existingProjects, error: listProjectsError } = await listProjects({ client, query: { limit: 20 } });
  if (listProjectsError) throw new Error("Failed to list projects: " + JSON.stringify(listProjectsError));

  const existingProject = existingProjects.items?.find((p) => p.name === PROJECT_NAME);
  if (existingProject) {
    console.log("Project already exists:", existingProject.id);
    project = existingProject;
  } else {
    const { data: newProject, error } = await createProject({ client, body: { name: PROJECT_NAME } });
    if (error) throw new Error("Failed to create project: " + JSON.stringify(error));
    console.log("Created project:", newProject.id);
    project = newProject;
  }

  // ── Apps ─────────────────────────────────────────────────────────────────
  const { data: apps, error: listAppsError } = await listApps({ client, path: { project_id: project.id }, query: { limit: 20 } });
  if (listAppsError || !apps || apps.items.length === 0) throw new Error("No apps found");

  let testApp: App | undefined = apps.items.find((a) => a.type === "test_store");
  let appStoreApp: App | undefined = apps.items.find((a) => a.type === "app_store");
  let playStoreApp: App | undefined = apps.items.find((a) => a.type === "play_store");

  if (!testApp) throw new Error("No test_store app found — project should have been auto-created with one");
  console.log("Test store app:", testApp.id);

  if (!appStoreApp) {
    const { data: newApp, error } = await createApp({
      client, path: { project_id: project.id },
      body: { name: APP_STORE_APP_NAME, type: "app_store", app_store: { bundle_id: APP_STORE_BUNDLE_ID } },
    });
    if (error) throw new Error("Failed to create App Store app: " + JSON.stringify(error));
    appStoreApp = newApp;
    console.log("Created App Store app:", appStoreApp.id);
  } else {
    console.log("App Store app found:", appStoreApp.id);
  }

  if (!playStoreApp) {
    const { data: newApp, error } = await createApp({
      client, path: { project_id: project.id },
      body: { name: PLAY_STORE_APP_NAME, type: "play_store", play_store: { package_name: PLAY_STORE_PACKAGE_NAME } },
    });
    if (error) throw new Error("Failed to create Play Store app: " + JSON.stringify(error));
    playStoreApp = newApp;
    console.log("Created Play Store app:", playStoreApp.id);
  } else {
    console.log("Play Store app found:", playStoreApp.id);
  }

  // ── Products ─────────────────────────────────────────────────────────────
  const { data: existingProducts, error: listProductsError } = await listProducts({
    client, path: { project_id: project.id }, query: { limit: 100 },
  });
  if (listProductsError) throw new Error("Failed to list products: " + JSON.stringify(listProductsError));

  const ensureProduct = async (targetApp: App, label: string, storeId: string, isTestStore: boolean, duration?: string): Promise<Product> => {
    const existing = existingProducts.items?.find((p) => p.store_identifier === storeId && p.app_id === targetApp.id);
    if (existing) {
      console.log(`${label} product already exists:`, existing.id);
      return existing;
    }
    const body: CreateProductData["body"] = {
      store_identifier: storeId,
      app_id: targetApp.id,
      type: "subscription",
      display_name: label,
    };
    if (isTestStore && duration) {
      (body as any).subscription = { duration };
      (body as any).title = label;
    }
    const { data: created, error } = await createProduct({ client, path: { project_id: project.id }, body });
    if (error) throw new Error(`Failed to create ${label} product: ` + JSON.stringify(error));
    console.log(`Created ${label} product:`, created.id);
    return created;
  };

  const productIds: { testId: string; appId: string; playId: string }[] = [];

  for (const prod of PRODUCTS) {
    const testP = await ensureProduct(testApp, prod.displayName, prod.identifier, true, prod.duration);
    const appP = await ensureProduct(appStoreApp, `${prod.displayName} (iOS)`, prod.identifier, false);
    const playP = await ensureProduct(playStoreApp, `${prod.displayName} (Android)`, prod.playIdentifier, false);

    // Test store prices
    const { error: priceError } = await client.post<TestStorePricesResponse>({
      url: "/projects/{project_id}/products/{product_id}/test_store_prices",
      path: { project_id: project.id, product_id: testP.id },
      body: { prices: prod.prices },
    } as any);
    if (priceError && (priceError as any)?.type !== "resource_already_exists") {
      console.warn(`Warning: Could not set test prices for ${prod.displayName}:`, priceError);
    } else {
      console.log(`Set test store price for ${prod.displayName}`);
    }

    productIds.push({ testId: testP.id, appId: appP.id, playId: playP.id });
  }

  // ── Entitlement ──────────────────────────────────────────────────────────
  let entitlement: Entitlement;
  const { data: existingEntitlements, error: listEntitleError } = await listEntitlements({
    client, path: { project_id: project.id }, query: { limit: 20 },
  });
  if (listEntitleError) throw new Error("Failed to list entitlements");

  const existingEnt = existingEntitlements.items?.find((e) => e.lookup_key === ENTITLEMENT_IDENTIFIER);
  if (existingEnt) {
    console.log("Entitlement already exists:", existingEnt.id);
    entitlement = existingEnt;
  } else {
    const { data: newEnt, error } = await createEntitlement({
      client, path: { project_id: project.id },
      body: { lookup_key: ENTITLEMENT_IDENTIFIER, display_name: ENTITLEMENT_DISPLAY_NAME },
    });
    if (error) throw new Error("Failed to create entitlement");
    console.log("Created entitlement:", newEnt.id);
    entitlement = newEnt;
  }

  const allProductIds = productIds.flatMap((p) => [p.testId, p.appId, p.playId]);
  const { error: attachEntErr } = await attachProductsToEntitlement({
    client,
    path: { project_id: project.id, entitlement_id: entitlement.id },
    body: { product_ids: allProductIds },
  });
  if (attachEntErr && (attachEntErr as any)?.type !== "unprocessable_entity_error") {
    throw new Error("Failed to attach products to entitlement");
  }
  console.log("Products attached to entitlement");

  // ── Offering ──────────────────────────────────────────────────────────────
  let offering: Offering;
  const { data: existingOfferings, error: listOfferErr } = await listOfferings({
    client, path: { project_id: project.id }, query: { limit: 20 },
  });
  if (listOfferErr) throw new Error("Failed to list offerings");

  const existingOffering = existingOfferings.items?.find((o) => o.lookup_key === OFFERING_IDENTIFIER);
  if (existingOffering) {
    console.log("Offering already exists:", existingOffering.id);
    offering = existingOffering;
  } else {
    const { data: newOff, error } = await createOffering({
      client, path: { project_id: project.id },
      body: { lookup_key: OFFERING_IDENTIFIER, display_name: OFFERING_DISPLAY_NAME },
    });
    if (error) throw new Error("Failed to create offering");
    console.log("Created offering:", newOff.id);
    offering = newOff;
  }

  if (!offering.is_current) {
    const { error } = await updateOffering({
      client, path: { project_id: project.id, offering_id: offering.id },
      body: { is_current: true },
    });
    if (error) throw new Error("Failed to set offering as current");
    console.log("Set offering as current");
  }

  // ── Packages ──────────────────────────────────────────────────────────────
  const { data: existingPkgs, error: listPkgErr } = await listPackages({
    client, path: { project_id: project.id, offering_id: offering.id }, query: { limit: 20 },
  });
  if (listPkgErr) throw new Error("Failed to list packages");

  for (let i = 0; i < PRODUCTS.length; i++) {
    const prod = PRODUCTS[i];
    const ids = productIds[i];

    let pkg: Package;
    const existingPkg = existingPkgs.items?.find((p) => p.lookup_key === prod.packageId);
    if (existingPkg) {
      console.log(`Package ${prod.packageId} already exists:`, existingPkg.id);
      pkg = existingPkg;
    } else {
      const { data: newPkg, error } = await createPackages({
        client, path: { project_id: project.id, offering_id: offering.id },
        body: { lookup_key: prod.packageId, display_name: prod.packageName },
      });
      if (error) throw new Error(`Failed to create package ${prod.packageId}`);
      console.log(`Created package ${prod.packageId}:`, newPkg.id);
      pkg = newPkg;
    }

    const { error: attachPkgErr } = await attachProductsToPackage({
      client, path: { project_id: project.id, package_id: pkg.id },
      body: {
        products: [
          { product_id: ids.testId, eligibility_criteria: "all" },
          { product_id: ids.appId, eligibility_criteria: "all" },
          { product_id: ids.playId, eligibility_criteria: "all" },
        ],
      },
    });
    if (attachPkgErr && !(attachPkgErr as any)?.message?.includes("Cannot attach product")) {
      throw new Error(`Failed to attach products to package ${prod.packageId}`);
    }
    console.log(`Products attached to package ${prod.packageId}`);
  }

  // ── API Keys ──────────────────────────────────────────────────────────────
  const { data: testKeys } = await listAppPublicApiKeys({ client, path: { project_id: project.id, app_id: testApp.id } });
  const { data: iosKeys } = await listAppPublicApiKeys({ client, path: { project_id: project.id, app_id: appStoreApp.id } });
  const { data: androidKeys } = await listAppPublicApiKeys({ client, path: { project_id: project.id, app_id: playStoreApp.id } });

  console.log("\n==================================================");
  console.log("RevenueCat setup complete!");
  console.log("==================================================");
  console.log("\nStore these as environment variables:\n");
  console.log("REVENUECAT_PROJECT_ID=" + project.id);
  console.log("REVENUECAT_TEST_STORE_APP_ID=" + testApp.id);
  console.log("REVENUECAT_APPLE_APP_STORE_APP_ID=" + appStoreApp.id);
  console.log("REVENUECAT_GOOGLE_PLAY_STORE_APP_ID=" + playStoreApp.id);
  console.log("EXPO_PUBLIC_REVENUECAT_TEST_API_KEY=" + (testKeys?.items[0]?.key ?? "N/A"));
  console.log("EXPO_PUBLIC_REVENUECAT_IOS_API_KEY=" + (iosKeys?.items[0]?.key ?? "N/A"));
  console.log("EXPO_PUBLIC_REVENUECAT_ANDROID_API_KEY=" + (androidKeys?.items[0]?.key ?? "N/A"));
  console.log("\nEntitlement identifier: " + ENTITLEMENT_IDENTIFIER);
  console.log("==================================================\n");
}

seedRevenueCat().catch((err) => {
  console.error("Seed failed:", err);
  process.exit(1);
});
