const { execSync } = require('child_process');
const https = require('https');

const PROJECT_ID = 'proj3a3fd517';
const APP_ID = 'appce9b46665f';

function getEnv(name) {
  try {
    return execSync(`echo "${name}"`, { encoding: 'utf8' }).trim();
  } catch {
    return process.env[name] || '';
  }
}

// Actually, just read from process.env directly - the shell env should have these
const issuerId = process.env.ASC_ISSUER_ID;
const keyId = process.env.ASC_KEY_ID;
const privateKey = process.env.ASC_PRIVATE_KEY;

if (!issuerId || !keyId || !privateKey) {
  console.error('❌ Missing ASC secrets. Set ASC_ISSUER_ID, ASC_KEY_ID, and ASC_PRIVATE_KEY.');
  process.exit(1);
}

console.log('✅ ASC secrets loaded');
console.log(`   Issuer ID: ${issuerId.slice(0, 20)}...`);
console.log(`   Key ID: ${keyId}`);
console.log(`   Private Key length: ${privateKey.length} chars`);

// Now we need the RevenueCat access token. We'll get it via listConnections in the sandbox.
// Since this is a shell script, we can't use listConnections. We need to either:
// a) Run the setup via the sandbox
// b) Use the ReplitConnectors proxy

// Since the proxy doesn't support PUT, we need to get the actual access token.
// The access token is available in the RevenueCat connection settings.
// We can get it via a separate script that runs in the sandbox.

console.log('\n⚠️  This script requires the RevenueCat access token to make a direct API call.');
console.log('Please run: npx tsx scripts/getRevenueCatToken.ts');
console.log('Then run:   node scripts/setupASCKey.js <token>');
