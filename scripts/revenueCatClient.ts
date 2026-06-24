import { ReplitConnectors } from "@replit/connectors-sdk";
import { createClient } from "@replit/revenuecat-sdk/client";

export async function getUncachableRevenueCatClient() {
  const connectors = new ReplitConnectors();

  const customFetch = async (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    // RevenueCat connector proxy base is https://api.revenuecat.com (no version).
    // The @replit/revenuecat-sdk generates paths without the /v2 prefix,
    // so we prepend it here before proxying.
    const path = "/v2" + url.pathname + url.search;

    let body: string | undefined;
    try {
      const text = await request.text();
      if (text) body = text;
    } catch {}

    const headers: Record<string, string> = {};
    request.headers.forEach((value: string, key: string) => {
      if (key.toLowerCase() !== "authorization") {
        headers[key] = value;
      }
    });

    const response = await connectors.proxy("revenuecat", path, {
      method: request.method as any,
      headers,
      body,
    });

    return response as unknown as Response;
  };

  return createClient({
    baseUrl: "https://api.revenuecat.com",
    fetch: customFetch,
  });
}
