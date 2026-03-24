const https = require('https');

const options = {
  hostname: 'v3.football.api-sports.io',
  path: '/players?search=messi',
  method: 'GET',
  headers: {
    'x-apisports-key': '8154742f66d14cb52548c73c3edfbee3'
  }
};

const req = https.request(options, res => {
  let data = '';
  res.on('data', chunk => data += chunk);
  res.on('end', () => console.log(data));
});

req.on('error', error => console.error(error));
req.end();
