import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.reversepicks.app',
  appName: 'Reverse Picks',
  webDir: 'dist',
  server: {
    androidScheme: 'https'
  }
};

export default config;
