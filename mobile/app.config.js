export default {
  expo: {
    name: "Reverse Picks",
    slug: "reversepicks",
    version: "1.0.0",
    orientation: "portrait",
    icon: "./assets/rp-icon.png",
    userInterfaceStyle: "dark",
    newArchEnabled: true,
    scheme: "reversepicks",
    splash: { backgroundColor: "#050505" },
    ios: {
      supportsTablet: false,
      bundleIdentifier: "com.reversepicks.app",
      buildNumber: "116",
      icon: "./assets/rp-icon.png",
      infoPlist: {
        ITSAppUsesNonExemptEncryption: false,
        NSCameraUsageDescription: "Reverse Picks needs camera access to scan prop slips.",
        NSPhotoLibraryUsageDescription: "Reverse Picks needs access to your photos to scan prop slips.",
        NSPhotoLibraryAddUsageDescription: "Reverse Picks needs access to save images.",
        NSUserNotificationsUsageDescription: "Reverse Picks sends you pick alerts and result notifications.",
        CFBundleURLTypes: [{ CFBundleURLSchemes: ["reversepicks"] }],
      },
    },
    android: {
      adaptiveIcon: { foregroundImage: "./assets/rp-icon.png", backgroundColor: "#050505" },
      package: "com.reversepicks.app",
    },
    web: { bundler: "metro" },
    plugins: ["expo-router", "expo-secure-store", "expo-image-picker", "expo-notifications", "expo-font"],
    extra: { router: {}, eas: { projectId: "cb70df32-f8c3-4bbd-9190-fb9cfd8b1599" } },
    owner: "josselgoateds-organization"
  }
};
