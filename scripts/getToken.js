const { ReplitConnectors } = require('@replit/connectors-sdk');

async function main() {
  try {
    const connectors = new ReplitConnectors();
    const connections = await connectors.listConnections();
    const rc = connections.find(c => c.connector === 'revenuecat');
    if (!rc) {
      console.error('No RevenueCat connection found');
      return;
    }
    const settings = rc.settings || {};
    console.log('Settings keys:', Object.keys(settings));
    console.log('Has access_token:', !!settings.access_token);
    console.log('Has oauth:', !!settings.oauth);
    console.log('OAuth type:', typeof settings.oauth);
    if (typeof settings.oauth === 'string') {
      try {
        const parsed = JSON.parse(settings.oauth);
        console.log('OAuth parsed keys:', Object.keys(parsed));
        if (parsed.credentials) {
          console.log('OAuth credentials keys:', Object.keys(parsed.credentials));
          console.log('Has oauth.access_token:', !!parsed.credentials.access_token);
        }
      } catch(e) {
        console.log('OAuth string is not JSON');
      }
    } else if (typeof settings.oauth === 'object') {
      console.log('OAuth object keys:', Object.keys(settings.oauth));
      if (settings.oauth.credentials) {
        console.log('OAuth credentials keys:', Object.keys(settings.oauth.credentials));
      }
    }
  } catch(e) {
    console.error('Error:', e.message);
  }
}

main();
