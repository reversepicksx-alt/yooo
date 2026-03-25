import dotenv from 'dotenv';
dotenv.config();

const WHOP_API_KEY = process.env.WHOP_API_KEY || 'apik_rYUnNi4RI6ioo_C4529086_C_4eb3dadfb2c5d31387e8f5f777f714d885978da3edb684cca06628ce21ba7d';
const WHOP_COMPANY_ID = process.env.WHOP_COMPANY_ID || 'biz_xLCux4k1X7U3AU';

async function testMembership(email: string) {
  console.log(`Testing User for: ${email}`);
  const whopUrl = `https://api.whop.com/api/v5/company/users?email=${encodeURIComponent(email)}`;
  
  try {
    const response = await fetch(whopUrl, {
      headers: {
        'Authorization': `Bearer ${WHOP_API_KEY}`,
        'Accept': 'application/json'
      }
    });

    if (response.ok) {
      const data = await response.json();
      console.log('Response OK');
      console.log('Data:', JSON.stringify(data, null, 2));
    } else {
      const error = await response.text();
      console.error(`Whop API Error (${response.status}):`, error);
    }
  } catch (err) {
    console.error('Request Failed:', err);
  }
}

const testEmail = process.argv[2] || 'dummy@example.com';
testMembership(testEmail);
