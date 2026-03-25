import dotenv from 'dotenv';
dotenv.config();

const WHOP_API_KEY = process.env.WHOP_API_KEY || 'apik_rYUnNi4RI6ioo_C4529086_C_4eb3dadfb2c5d31387e8f5f777f714d885978da3edb684cca06628ce21ba7d';
const WHOP_COMPANY_ID = process.env.WHOP_COMPANY_ID || 'biz_xLCux4k1X7U3AU';

async function testWhop() {
  console.log('Testing Whop Connection...');
  console.log('Company ID:', WHOP_COMPANY_ID);
  
  try {
    const response = await fetch(`https://api.whop.com/api/v5/company`, {
      headers: {
        'Authorization': `Bearer ${WHOP_API_KEY}`,
        'Accept': 'application/json'
      }
    });

    if (response.ok) {
      const data = await response.json();
      console.log('Whop Connection Successful!');
      console.log('Company Name:', data.name);
      console.log('Company Data:', JSON.stringify(data, null, 2));
    } else {
      const error = await response.text();
      console.error(`Whop API Error (${response.status}):`, error);
    }
  } catch (err) {
    console.error('Whop Request Failed:', err);
  }
}

testWhop();
