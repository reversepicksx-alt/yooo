import { MongoClient } from 'mongodb';
import dotenv from 'dotenv';
dotenv.config();

const MONGO_URL = process.env.MONGO_URL || '';
const DB_NAME = process.env.DB_NAME || 'reversepicks';

async function checkMongo() {
  if (!MONGO_URL) {
    console.log('No MONGO_URL provided.');
    return;
  }
  
  try {
    const client = new MongoClient(MONGO_URL);
    await client.connect();
    const db = client.db(DB_NAME);
    console.log(`Connected to MongoDB: ${DB_NAME}`);
    
    const grants = await db.collection('manual_access_grants').find({}).toArray();
    console.log(`Found ${grants.length} manual access grants:`);
    console.log(JSON.stringify(grants, null, 2));
    
    await client.close();
  } catch (err) {
    console.error('MongoDB Error:', err);
  }
}

checkMongo();
