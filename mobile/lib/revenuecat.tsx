import React, { createContext, useContext } from "react";
import { Platform } from "react-native";
import Purchases, { type PurchasesPackage } from "react-native-purchases";
import { useMutation, useQuery } from "@tanstack/react-query";
import Constants from "expo-constants";

const REVENUECAT_TEST_API_KEY = process.env.EXPO_PUBLIC_REVENUECAT_TEST_API_KEY;
const REVENUECAT_IOS_API_KEY = process.env.EXPO_PUBLIC_REVENUECAT_IOS_API_KEY;
const REVENUECAT_ANDROID_API_KEY = process.env.EXPO_PUBLIC_REVENUECAT_ANDROID_API_KEY;

export const REVENUECAT_ENTITLEMENT_IDENTIFIER = "pro";

function getRevenueCatApiKey(): string {
  if (Platform.OS === "web") {
    return REVENUECAT_TEST_API_KEY ?? "";
  }

  const isDev = __DEV__ || Constants.executionEnvironment === "storeClient";
  if (isDev) return REVENUECAT_TEST_API_KEY ?? "";
  if (Platform.OS === "ios") return REVENUECAT_IOS_API_KEY ?? "";
  if (Platform.OS === "android") return REVENUECAT_ANDROID_API_KEY ?? "";
  return REVENUECAT_TEST_API_KEY ?? "";
}

export function initializeRevenueCat() {
  if (Platform.OS === "web") return;
  const apiKey = getRevenueCatApiKey();
  if (!apiKey) {
    console.warn("[RevenueCat] No API key — purchases disabled");
    return;
  }
  Purchases.setLogLevel(Purchases.LOG_LEVEL.WARN);
  Purchases.configure({ apiKey });
  console.log("[RevenueCat] Configured");
}

export function setRevenueCatUserId(userId: string) {
  if (Platform.OS === "web") return;
  try {
    Purchases.logIn(userId);
  } catch (e) {
    console.warn("[RevenueCat] logIn error:", e);
  }
}

function useSubscriptionContext() {
  const customerInfoQuery = useQuery({
    queryKey: ["revenuecat", "customer-info"],
    queryFn: async () => {
      if (Platform.OS === "web") return null;
      return Purchases.getCustomerInfo();
    },
    staleTime: 60_000,
  });

  const offeringsQuery = useQuery({
    queryKey: ["revenuecat", "offerings"],
    queryFn: async () => {
      if (Platform.OS === "web") return null;
      return Purchases.getOfferings();
    },
    staleTime: 300_000,
  });

  const purchaseMutation = useMutation({
    mutationFn: async (pkg: PurchasesPackage) => {
      const { customerInfo } = await Purchases.purchasePackage(pkg);
      return customerInfo;
    },
    onSuccess: () => customerInfoQuery.refetch(),
  });

  const restoreMutation = useMutation({
    mutationFn: async () => Purchases.restorePurchases(),
    onSuccess: () => customerInfoQuery.refetch(),
  });

  const customerInfo = customerInfoQuery.data ?? null;
  const isSubscribed =
    Platform.OS !== "web" &&
    customerInfo?.entitlements.active?.[REVENUECAT_ENTITLEMENT_IDENTIFIER] !== undefined;

  const packages = offeringsQuery.data?.current?.availablePackages ?? [];

  return {
    customerInfo,
    offerings: offeringsQuery.data,
    packages,
    isSubscribed,
    isLoading: customerInfoQuery.isLoading || offeringsQuery.isLoading,
    purchase: purchaseMutation.mutateAsync,
    restore: restoreMutation.mutateAsync,
    isPurchasing: purchaseMutation.isPending,
    isRestoring: restoreMutation.isPending,
    purchaseError: purchaseMutation.error,
    refetchCustomerInfo: customerInfoQuery.refetch,
  };
}

type SubscriptionContextValue = ReturnType<typeof useSubscriptionContext>;
const Context = createContext<SubscriptionContextValue | null>(null);

export function SubscriptionProvider({ children }: { children: React.ReactNode }) {
  const value = useSubscriptionContext();
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSubscription() {
  const ctx = useContext(Context);
  if (!ctx) throw new Error("useSubscription must be used within SubscriptionProvider");
  return ctx;
}
