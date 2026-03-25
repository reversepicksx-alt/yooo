const fetch = require('node-fetch');
const WHOP_API_KEY = 'apik_rYUnNi4RI6ioo_C4529086_C_4eb3dadfb2c5d31387e8f5f777f714d885978da3edb684cca06628ce21ba7d';
const WHOP_COMPANY_ID = 'biz_xLCux4k1X7U3AU';

async function test() {
  const email = 'josselj001@gmail.com';
  const url = `https://api.whop.com/api/v5/memberships?email=${encodeURIComponent(email)}&company_id=${WHOP_COMPANY_ID}`;
  const res = await fetch(url, {
    headers: { 'Authorization': `Bearer ${WHOP_API_KEY}`, 'Accept': 'application/json' }
  });
  const data = await res.json();
  console.log(JSON.stringify(data, null, 2));
}
test();
