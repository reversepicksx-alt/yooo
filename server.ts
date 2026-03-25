import express from 'express';
import { MongoClient } from 'mongodb';
import { v4 as uuidv4 } from 'uuid';
import cors from 'cors';
import { createServer as createViteServer } from 'vite';
import path from 'path';
import dotenv from 'dotenv';
import bcrypt from 'bcryptjs';
import fs from 'fs';

dotenv.config();

const app = express();
const PORT = 3000;

app.use(express.json());
app.use(cors());

// Environment Variables
const WHOP_API_KEY = process.env.WHOP_API_KEY || 'apik_rYUnNi4RI6ioo_C4529086_C_4eb3dadfb2c5d31387e8f5f777f714d885978da3edb684cca06628ce21ba7d';
const WHOP_COMPANY_ID = process.env.WHOP_COMPANY_ID || 'biz_xLCux4k1X7U3AU';
const OWNER_EMAIL = (process.env.OWNER_EMAIL || 'josselj001@gmail.com').toLowerCase().trim();
const MONGO_URL = process.env.MONGO_URL || '';
const DB_NAME = process.env.DB_NAME || 'reversepicks_v3';
const DEV_MODE = process.env.DEV_MODE === 'true';

// Hardcoded Access Lists
const TEAM_EMAILS = [
].map(e => e.toLowerCase());

const LIFETIME_SUB_EMAILS = [
  "faron2allen@gmail.com", "jossel0701@gmail.com",
  "brayanfgaleas@icloud.com", "odr310@gmail.com"
].map(e => e.toLowerCase());

// In-memory fallback if MongoDB is not connected
const GRANTS_FILE = path.join(process.cwd(), 'grants.json');
let inMemoryGrants = [];

try {
  if (fs.existsSync(GRANTS_FILE)) {
    inMemoryGrants = JSON.parse(fs.readFileSync(GRANTS_FILE, 'utf-8'));
  } else {
    inMemoryGrants = [
      { email: OWNER_EMAIL, access_type: 'Owner' },
      ...TEAM_EMAILS.map(e => ({ email: e, access_type: 'Team' })),
      ...LIFETIME_SUB_EMAILS.map(e => ({ email: e, access_type: 'Lifetime' }))
    ];
    fs.writeFileSync(GRANTS_FILE, JSON.stringify(inMemoryGrants, null, 2));
  }
} catch (e) {
  console.error('Error loading grants file:', e);
  inMemoryGrants = [
    { email: OWNER_EMAIL, access_type: 'Owner' },
    ...TEAM_EMAILS.map(e => ({ email: e, access_type: 'Team' })),
    ...LIFETIME_SUB_EMAILS.map(e => ({ email: e, access_type: 'Lifetime' }))
  ];
}

function saveGrants() {
  try {
    fs.writeFileSync(GRANTS_FILE, JSON.stringify(inMemoryGrants, null, 2));
  } catch (e) {
    console.error('Error saving grants file:', e);
  }
}

let inMemorySessions: any[] = [];
let inMemoryUsers: any[] = []; // { email, passwordHash }

let whopCache: any[] | null = null;
let whopCacheTime: number = 0;

// --- Helpers ---
async function fetchWhopMemberships() {
  const now = Date.now();
  if (whopCache && (now - whopCacheTime < 60000)) { // 1 minute cache
    return whopCache;
  }

  console.log('[WHOP] Fetching all memberships...');
  let allMemberships: any[] = [];
  let page = 1;
  let hasMore = true;

  try {
    while (hasMore) {
      const url = `https://api.whop.com/api/v2/memberships?company_id=${WHOP_COMPANY_ID}&per_page=50&page=${page}`;
      const res = await fetchWithTimeout(url, {
        headers: { 
          'Authorization': `Bearer ${WHOP_API_KEY}`,
          'Accept': 'application/json'
        }
      }, 10000);

      if (!res.ok) {
        console.error(`[WHOP] API Error (${res.status}):`, await res.text());
        break;
      }

      const data = await res.json();
      const memberships = data.data || [];
      allMemberships = allMemberships.concat(memberships);

      if (page >= (data.pagination?.total_page || 1)) {
        hasMore = false;
      } else {
        page++;
      }
    }

    whopCache = allMemberships;
    whopCacheTime = now;
    console.log(`[WHOP] Cached ${allMemberships.length} memberships.`);
    return allMemberships;
  } catch (err) {
    console.error('[WHOP] Error fetching memberships:', err);
    return whopCache || []; // Return stale cache if available
  }
}

async function fetchWithTimeout(resource: string, options: any = {}, timeout = 8000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);
  const response = await fetch(resource, {
    ...options,
    signal: controller.signal
  });
  clearTimeout(id);
  return response;
}

// MongoDB Setup
let db: any = null;

if (MONGO_URL) {
  const client = new MongoClient(MONGO_URL, { serverSelectionTimeoutMS: 5000 });
  client.connect().then(async () => {
    db = client.db(DB_NAME);
    console.log('Connected to MongoDB');
    
    // Check if already seeded
    const seeded = await db.collection('app_meta').findOne({ key: 'manual_access_seed_v2' });
    if (!seeded) {
      // Seed manual access grants
      const seedData = [
        { email: OWNER_EMAIL, access_type: 'Owner' },
        ...TEAM_EMAILS.map(e => ({ email: e, access_type: 'Team' })),
        ...LIFETIME_SUB_EMAILS.map(e => ({ email: e, access_type: 'Lifetime' }))
      ];
      
      const bulkOps = seedData.map(data => ({
        updateOne: {
          filter: { email: data.email },
          update: { $setOnInsert: data },
          upsert: true
        }
      }));
      
      await db.collection('manual_access_grants').bulkWrite(bulkOps);
      await db.collection('app_meta').updateOne(
        { key: 'manual_access_seed_v2' },
        { $set: { seeded_at: new Date().toISOString() } },
        { upsert: true }
      );
      console.log('Seeded manual access grants');
    }
  }).catch(err => {
    console.error('\n=========================================');
    console.error('❌ MongoDB Connection Error');
    console.error('=========================================');
    console.error('The server could not connect to your MongoDB database.');
    console.error('This is almost always caused by MongoDB Atlas IP Whitelisting.');
    console.error('\nTo fix this:');
    console.error('1. Go to your MongoDB Atlas Dashboard');
    console.error('2. Click "Network Access" on the left sidebar');
    console.error('3. Click "Add IP Address"');
    console.error('4. Click "ALLOW ACCESS FROM ANYWHERE" (0.0.0.0/0)');
    console.error('5. Click "Confirm" and wait for it to deploy');
    console.error('\nFalling back to in-memory storage for now.');
    console.error('=========================================\n');
    console.error('Technical details:', err.message);
  });
}

// Access Check Helper
async function checkAccess(emailLower: string): Promise<string | null> {
  console.log(`[AUTH] Starting access check for: ${emailLower}`);
  if (emailLower === OWNER_EMAIL) {
    console.log(`[AUTH] Match found: OWNER_EMAIL`);
    return 'Owner';
  }

  // 1. Check Manual Grants (MongoDB or In-Memory)
  if (db) {
    const manual = await db.collection('manual_access_grants').findOne({ email: emailLower });
    if (manual) {
      console.log(`[AUTH] Match found: Manual Grant (MongoDB) - ${manual.access_type}`);
      return manual.access_type;
    }
  } else {
    const manual = inMemoryGrants.find(g => g.email === emailLower);
    if (manual) {
      console.log(`[AUTH] Match found: Manual Grant (In-Memory) - ${manual.access_type}`);
      return manual.access_type;
    }
  }

  // 2. Real-time Whop API Check
  try {
    console.log(`[AUTH] Checking Whop for ${emailLower}...`);
    
    const allMemberships = await fetchWhopMemberships();
    
    const memberships = allMemberships.filter((m: any) => m.email?.toLowerCase() === emailLower);
    
    console.log(`[AUTH] Whop found ${memberships.length} memberships for ${emailLower}`);
    
    const activeMembership = memberships.find((m: any) => {
      // Double check company ID match
      const companyMatch = m.company_id === WHOP_COMPANY_ID || m.page_id === WHOP_COMPANY_ID;
      if (!companyMatch) return false;

      const status = (m.status || '').toLowerCase();
      const isValid = ['active', 'trialing', 'completed'].includes(status) || m.valid === true;
      
      if (isValid) {
        console.log(`[AUTH] Found VALID membership for ${emailLower}: ${m.id} (Status: ${status})`);
      }
      return isValid;
    });

    if (activeMembership) {
      return 'Premium';
    }
    console.log(`[AUTH] No active memberships found for ${emailLower}`);
  } catch (error) {
    console.error('[AUTH] Whop check failed:', error);
  }

  // 4. Final Fallback: Removed insecure Dev Mode fallback to ensure only authorized users can enter.
  console.log(`[AUTH] No access found for ${emailLower} after all checks.`);
  return null;
}

// Session Creation Helper
async function createSession(email: string, accessType: string) {
  const sessionToken = uuidv4();
  if (db) {
    await db.collection('sessions').updateOne(
      { email },
      { $set: { email, session_token: sessionToken, access_type: accessType, last_active: new Date().toISOString() } },
      { upsert: true }
    );
  } else {
    const existing = inMemorySessions.find(s => s.email === email);
    if (existing) {
      existing.session_token = sessionToken;
      existing.access_type = accessType;
      existing.last_active = new Date().toISOString();
    } else {
      inMemorySessions.push({ email, session_token: sessionToken, access_type: accessType, last_active: new Date().toISOString() });
    }
  }
  return sessionToken;
}

// Auth Endpoints
async function verifyOwner(req: any, res: any, next: any) {
  const email = (req.query.email || req.body.email || '').toLowerCase().trim();
  const token = req.headers['x-session-token'];
  if (!email || !token) return res.status(401).json({ error: 'Unauthorized' });
  if (email !== OWNER_EMAIL) return res.status(403).json({ error: 'Forbidden' });
  
  if (db) {
    const session = await db.collection('sessions').findOne({ email, session_token: token });
    if (!session || session.access_type !== 'Owner') return res.status(401).json({ error: 'Invalid session' });
  } else {
    const session = inMemorySessions.find(s => s.email === email && s.session_token === token);
    if (!session || session.access_type !== 'Owner') return res.status(401).json({ error: 'Invalid session' });
  }
  next();
}

app.get('/api/admin/grants', verifyOwner, async (req, res) => {
  if (!db) {
    return res.json(inMemoryGrants);
  }
  const grants = await db.collection('manual_access_grants').find({}).toArray();
  res.json(grants);
});

app.post('/api/admin/grants', verifyOwner, async (req, res) => {
  const { targetEmail, accessType } = req.body;
  if (!targetEmail || !accessType) return res.status(400).json({ error: 'Missing fields' });
  
  if (!db) {
    const existing = inMemoryGrants.find(g => g.email === targetEmail.toLowerCase().trim());
    if (existing) {
      existing.access_type = accessType;
    } else {
      inMemoryGrants.push({ email: targetEmail.toLowerCase().trim(), access_type: accessType });
    }
    saveGrants();
    return res.json({ success: true });
  }

  await db.collection('manual_access_grants').updateOne(
    { email: targetEmail.toLowerCase().trim() },
    { $set: { email: targetEmail.toLowerCase().trim(), access_type: accessType, added_at: new Date().toISOString() } },
    { upsert: true }
  );
  res.json({ success: true });
});

app.delete('/api/admin/grants/:targetEmail', verifyOwner, async (req, res) => {
  if (!db) {
    inMemoryGrants = inMemoryGrants.filter(g => g.email !== req.params.targetEmail.toLowerCase().trim());
    saveGrants();
    return res.json({ success: true });
  }
  await db.collection('manual_access_grants').deleteOne({ email: req.params.targetEmail.toLowerCase().trim() });
  res.json({ success: true });
});

app.post('/api/auth/verify-session', async (req, res) => {
  const { email, session_token } = req.body;
  if (!email || !session_token) return res.json({ valid: false });
  
  const emailLower = email.toLowerCase().trim();
  
  if (db) {
    const session = await db.collection('sessions').findOne({ email: emailLower, session_token });
    if (!session) return res.json({ valid: false });
  } else {
    const session = inMemorySessions.find(s => s.email === emailLower && s.session_token === session_token);
    if (!session) return res.json({ valid: false });
  }
  
  // Verify they still have access
  const accessType = await checkAccess(emailLower);
  if (!accessType) {
    // Revoke session if they lost access
    if (db) {
      await db.collection('sessions').deleteOne({ email: emailLower, session_token });
    } else {
      inMemorySessions = inMemorySessions.filter(s => s.session_token !== session_token);
    }
    return res.json({ valid: false });
  }

  res.json({ valid: true, access_type: accessType });
});

app.post('/api/auth/verify-whop', async (req, res) => {
  try {
    const { email, device_id } = req.body;
    if (!email) return res.status(400).json({ verified: false, message: 'Email required' });
    
    const emailLower = email.toLowerCase().trim();

    // 1. ALWAYS check access first, even if they have a record. 
    // This prevents expired users from even seeing the password screen.
    const accessType = await checkAccess(emailLower);
    
    if (!accessType) {
      console.log(`[AUTH] verify-whop: Access DENIED for ${emailLower}`);
      return res.json({ verified: false, email: emailLower, message: 'No active membership found. Please subscribe via Whop to gain access.' });
    }

    console.log(`[AUTH] verify-whop: Access GRANTED (${accessType}) for ${emailLower}`);

    // 2. Owner bypasses password
    if (emailLower === OWNER_EMAIL) {
      const token = await createSession(emailLower, 'Owner');
      return res.json({ verified: true, email: emailLower, session_token: token, access_type: 'Owner', message: 'Premium access granted' });
    }

    // 3. Check if user has a password set
    let userRecord = null;
    if (db) {
      userRecord = await db.collection('users').findOne({ email: emailLower });
    } else {
      userRecord = inMemoryUsers.find(u => u.email === emailLower);
    }

    if (userRecord && userRecord.passwordHash) {
      return res.json({ requires_password: true, email: emailLower });
    }

    // 4. No password set, but they have access. Let them set one.
    return res.json({ requires_password_setup: true, email: emailLower, access_type: accessType });
  } catch (error) {
    console.error('Auth error:', error);
    res.status(500).json({ verified: false, message: 'Internal server error' });
  }
});

app.post('/api/auth/login', async (req, res) => {
  try {
    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ verified: false, message: 'Email and password required' });
    const emailLower = email.toLowerCase().trim();

    let userRecord = null;
    if (db) {
      userRecord = await db.collection('users').findOne({ email: emailLower });
    } else {
      userRecord = inMemoryUsers.find(u => u.email === emailLower);
    }

    if (!userRecord || !userRecord.passwordHash) {
      return res.status(401).json({ verified: false, message: 'Invalid credentials or password not set.' });
    }

    const isValid = await bcrypt.compare(password, userRecord.passwordHash);
    if (!isValid) {
      return res.status(401).json({ verified: false, message: 'Invalid password.' });
    }

    const accessType = await checkAccess(emailLower);
    if (!accessType) {
      return res.status(401).json({ verified: false, message: 'Your subscription has expired or been revoked.' });
    }

    const token = await createSession(emailLower, accessType);
    return res.json({ verified: true, email: emailLower, session_token: token, access_type: accessType, message: 'Login successful' });
  } catch (err) {
    console.error(err);
    res.status(500).json({ verified: false, message: 'Server error' });
  }
});

app.post('/api/auth/set-password', async (req, res) => {
  try {
    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ verified: false, message: 'Email and password required' });
    if (password.length < 6) return res.status(400).json({ verified: false, message: 'Password must be at least 6 characters' });
    const emailLower = email.toLowerCase().trim();

    const accessType = await checkAccess(emailLower);
    if (!accessType) {
      return res.status(401).json({ verified: false, message: 'No active subscription found.' });
    }

    const salt = await bcrypt.genSalt(10);
    const passwordHash = await bcrypt.hash(password, salt);

    if (db) {
      await db.collection('users').updateOne(
        { email: emailLower },
        { $set: { email: emailLower, passwordHash, created_at: new Date().toISOString() } },
        { upsert: true }
      );
    } else {
      const existing = inMemoryUsers.find(u => u.email === emailLower);
      if (existing) {
        existing.passwordHash = passwordHash;
      } else {
        inMemoryUsers.push({ email: emailLower, passwordHash, created_at: new Date().toISOString() });
      }
    }

    const token = await createSession(emailLower, accessType);
    return res.json({ verified: true, email: emailLower, session_token: token, access_type: accessType, message: 'Password set successfully' });
  } catch (err) {
    console.error(err);
    res.status(500).json({ verified: false, message: 'Server error' });
  }
});

app.post('/api/auth/reset-password', async (req, res) => {
  try {
    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ verified: false, message: 'Email and password required' });
    if (password.length < 6) return res.status(400).json({ verified: false, message: 'Password must be at least 6 characters' });
    const emailLower = email.toLowerCase().trim();

    // Verify they still have access before letting them reset
    const accessType = await checkAccess(emailLower);
    if (!accessType) {
      return res.status(401).json({ verified: false, message: 'No active subscription found. Cannot reset password.' });
    }

    const salt = await bcrypt.genSalt(10);
    const passwordHash = await bcrypt.hash(password, salt);

    if (db) {
      await db.collection('users').updateOne(
        { email: emailLower },
        { $set: { email: emailLower, passwordHash, updated_at: new Date().toISOString() } },
        { upsert: true }
      );
    } else {
      const existing = inMemoryUsers.find(u => u.email === emailLower);
      if (existing) {
        existing.passwordHash = passwordHash;
      } else {
        inMemoryUsers.push({ email: emailLower, passwordHash, created_at: new Date().toISOString() });
      }
    }

    const token = await createSession(emailLower, accessType);
    return res.json({ verified: true, email: emailLower, session_token: token, access_type: accessType, message: 'Password reset successfully' });
  } catch (err) {
    console.error(err);
    res.status(500).json({ verified: false, message: 'Server error' });
  }
});

app.get('/api/auth/whop-status', (req, res) => {
  res.json({
    configured: true,
    dev_mode: DEV_MODE,
    company_id: WHOP_COMPANY_ID,
    upgrade_url: "https://whop.com/biz_xLCux4k1X7U3AU"
  });
});

app.post('/api/auth/logout', async (req, res) => {
  const { email } = req.query;
  if (email && db) {
    await db.collection('sessions').deleteOne({ email: (email as string).toLowerCase().trim() });
  }
  res.json({ success: true });
});

// Vite Middleware & Production Static Serving
async function startServer() {
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa'
    });
    app.use(vite.middlewares);
  } else {
    app.use(express.static('dist'));
    app.get('*', (req, res) => {
      res.sendFile(path.resolve('dist/index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
