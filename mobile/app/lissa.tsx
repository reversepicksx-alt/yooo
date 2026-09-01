import React from 'react';
import { Redirect } from 'expo-router';

// Lissa is a persistent voice layer in the authenticated app shell, not a
// destination or a chat page. Keep old deep links from reopening the retired
// standalone screen.
export default function LissaRedirect() {
  return <Redirect href="/(tabs)/picks" />;
}