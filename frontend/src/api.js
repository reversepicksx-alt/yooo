const API_URL = process.env.REACT_APP_BACKEND_URL || '';

async function apiCall(endpoint, options = {}) {
  let resp;
  try {
    resp = await fetch(`${API_URL}${endpoint}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
  } catch (e) {
    throw new Error('Network error — check your connection and try again.');
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  return resp.json();
}

// Auth APIs
export async function verifyWhop(email) {
  return apiCall('/api/auth/verify-whop', { method: 'POST', body: JSON.stringify({ email }) });
}

export async function authLogin(email, password) {
  return apiCall('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
}

export async function setPassword(email, password) {
  return apiCall('/api/auth/set-password', { method: 'POST', body: JSON.stringify({ email, password }) });
}

export async function resetPassword(email, new_password) {
  return apiCall('/api/auth/reset-password', { method: 'POST', body: JSON.stringify({ email, new_password }) });
}

export async function verifySession(email, session_token) {
  return apiCall('/api/auth/verify-session', { method: 'POST', body: JSON.stringify({ email, session_token }) });
}

export async function authLogout(email, session_token) {
  return apiCall('/api/auth/logout', { method: 'POST', body: JSON.stringify({ email, session_token }) });
}

export async function getLeagues() {
  return apiCall('/api/leagues');
}

export async function getTeamsByLeague(leagueId) {
  return apiCall(`/api/leagues/${leagueId}/teams`);
}

export async function searchPlayers(query, leagueId = null) {
  return apiCall('/api/players/search', {
    method: 'POST',
    body: JSON.stringify({ query, league_id: leagueId }),
  });
}

export async function getPlayerStats(playerId) {
  return apiCall(`/api/player/${playerId}/stats`);
}

export async function predict(request) {
  return apiCall('/api/predict', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

export async function predictCombo(request) {
  // Start the job
  const job = await apiCall('/api/predict-combo', {
    method: 'POST',
    body: JSON.stringify(request),
  });
  // Poll until done
  const jobId = job.jobId;
  for (let i = 0; i < 40; i++) { // poll up to ~2 minutes
    await new Promise(r => setTimeout(r, 3000));
    const status = await apiCall(`/api/predict-combo/${jobId}`);
    if (status.status === 'done') return status.result;
  }
  throw new Error('Combo prediction timed out after 2 minutes.');
}

export async function startChat(sessionId = null) {
  return apiCall('/api/chat/start', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function sendChatMessage(sessionId, message) {
  return apiCall('/api/chat/message', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

// Reverse Tactical APIs
export async function startTactical(sessionId = null) {
  return apiCall('/api/tactical/start', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function sendTacticalMessage(sessionId, message, imageBase64 = null) {
  const body = { session_id: sessionId, message };
  if (imageBase64) body.image_base64 = imageBase64;
  return apiCall('/api/tactical/message', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function parseNaturalQuery(query) {
  return apiCall('/api/parse-query', {
    method: 'POST',
    body: JSON.stringify({ query }),
  });
}

export async function checkApiStatus() {
  try {
    const data = await apiCall('/api/football/status');
    return data.status === 'online';
  } catch {
    return false;
  }
}

export async function getPickOfTheDay() {
  return apiCall('/api/pick-of-the-day');
}

export async function settlePicks(picks) {
  return apiCall('/api/settle-picks', { method: 'POST', body: JSON.stringify({ picks }) });
}

export async function savePick(email, token, pick) {
  return apiCall('/api/picks/save', { method: 'POST', body: JSON.stringify({ email, token, pick }) });
}

export async function listPicks(email, token) {
  return apiCall('/api/picks/list', { method: 'POST', body: JSON.stringify({ email, token }) });
}

export async function deletePick(email, token, pickId) {
  return apiCall('/api/picks/delete', { method: 'POST', body: JSON.stringify({ email, token, pickId }) });
}

export async function correctPick(email, token, pickId, actualValue) {
  return apiCall('/api/picks/correct', { method: 'POST', body: JSON.stringify({ email, token, pickId, actualValue }) });
}


export async function liveUpdatePicks(email, token) {
  return apiCall('/api/picks/live-update', { method: 'POST', body: JSON.stringify({ email, token }) });
}

export async function scanProp(imageBase64) {
  return apiCall('/api/scan-prop', {
    method: 'POST',
    body: JSON.stringify({ image_base64: imageBase64 }),
  });
}

export const SUPPORTED_LEAGUES = [
  { id: 39, name: "Premier League", type: "Domestic" },
  { id: 140, name: "La Liga", type: "Domestic" },
  { id: 135, name: "Serie A", type: "Domestic" },
  { id: 78, name: "Bundesliga", type: "Domestic" },
  { id: 61, name: "Ligue 1", type: "Domestic" },
  { id: 40, name: "Championship", type: "Domestic" },
  { id: 188, name: "A-League", type: "Domestic" },
  { id: 253, name: "MLS", type: "Domestic" },
  { id: 262, name: "Liga MX", type: "Domestic" },
  { id: 128, name: "Liga Profesional Argentina", type: "Domestic" },
  { id: 71, name: "Brasileirao", type: "Domestic" },
  { id: 307, name: "Saudi Pro League", type: "Domestic" },
  { id: 254, name: "NWSL", type: "Domestic" },
  { id: 2, name: "Champions League", type: "International Club" },
  { id: 3, name: "Europa League", type: "International Club" },
  { id: 1, name: "World Cup", type: "International Team" },
  { id: 32, name: "World Cup Qualifiers (UEFA)", type: "International Team" },
  { id: 34, name: "World Cup Qualifiers (CONMEBOL)", type: "International Team" },
  { id: 31, name: "World Cup Qualifiers (CONCACAF)", type: "International Team" },
  { id: 29, name: "World Cup Qualifiers (CAF)", type: "International Team" },
  { id: 30, name: "World Cup Qualifiers (AFC)", type: "International Team" },
  { id: 33, name: "World Cup Qualifiers (OFC)", type: "International Team" },
  { id: 4, name: "Euro Championship", type: "International Team" },
  { id: 960, name: "Euro Qualifiers", type: "International Team" },
  { id: 9, name: "Copa America", type: "International Team" },
  { id: 5, name: "UEFA Nations League", type: "International Team" },
  { id: 13, name: "CONCACAF Nations League", type: "International Team" },
  { id: 6, name: "Africa Cup of Nations", type: "International Team" },
  { id: 115, name: "AFCON Qualifiers", type: "International Team" },
  { id: 7, name: "Asian Cup", type: "International Team" },
  { id: 10, name: "International Friendlies", type: "International Team" },
];
