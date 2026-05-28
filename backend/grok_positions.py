"""Gemini-powered position resolution for players."""
import httpx
import json
from config import db, GEMINI_API_KEY

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_FLASH = "gemini-2.5-flash"


async def resolve_position_ai(player_name: str, sport: str = "soccer") -> dict:
    """Resolve a single player's position using cache first, then Gemini fallback.
    Returns {"position": "XX", "role": "..."} or empty strings if failed."""

    # Try cache first
    cached = await db.player_positions.find_one(
        {"playerName": player_name}, {"_id": 0, "specificPosition": 1, "role": 1}
    )
    if cached and cached.get("specificPosition"):
        return {"position": cached["specificPosition"], "role": cached.get("role", "")}

    if not GEMINI_API_KEY:
        return {"position": "", "role": ""}

    return await _gemini_resolve_batch([{"playerName": player_name, "sport": sport}])


async def resolve_positions_ai_batch(players: list) -> dict:
    """Batch-resolve positions for multiple players.
    Input: [{"playerName": "...", "sport": "soccer", "playerId": optional}]
    Returns: {"PlayerName": {"position": "XX", "role": "..."}, ...}
    """
    if not players:
        return {}

    results = {}
    unresolved = []
    for p in players:
        name = p.get("playerName", "")
        if not name:
            continue
        cached = await db.player_positions.find_one(
            {"playerName": name}, {"_id": 0, "specificPosition": 1, "role": 1}
        )
        if cached and cached.get("specificPosition"):
            results[name] = {"position": cached["specificPosition"], "role": cached.get("role", "")}
        else:
            unresolved.append(p)

    if not unresolved or not GEMINI_API_KEY:
        return results

    gemini_results = await _gemini_resolve_batch(unresolved)
    results.update(gemini_results)
    return results


async def _gemini_resolve_batch(players: list) -> dict:
    """Call Gemini API to resolve positions for a list of players."""
    if not players or not GEMINI_API_KEY:
        return {}

    results = {}
    player_lines = []
    for idx, pl in enumerate(players):
        player_lines.append(f"{idx+1}. {pl['playerName']} ({pl.get('sport', 'soccer')})")

    prompt = f"""For each player below, return ONLY their primary position abbreviation and a short tactical role.

Soccer positions: GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST
Basketball positions: PG, SG, SF, PF, C

Players:
{chr(10).join(player_lines)}

Return JSON array: [{{"name":"exact player name","position":"XX","role":"short tactical role"}}]
Only the JSON array, no markdown, no explanation."""

    try:
        url = f"{GEMINI_BASE}/{GEMINI_FLASH}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 500,
                                 "responseMimeType": "application/json",
                                 "thinkingConfig": {"thinkingBudget": 0}},
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                content = "".join(p.get("text", "") for p in parts).strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    content = content.rsplit("```", 1)[0]
                resolved = json.loads(content.strip())
                for r in resolved:
                    rname = r.get("name", "")
                    rpos = r.get("position", "")
                    rrole = r.get("role", "")
                    if rname and rpos:
                        results[rname] = {"position": rpos, "role": rrole}
                        matching = [p for p in players if p["playerName"] == rname]
                        pid = matching[0].get("playerId") if matching else None
                        cache_doc = {"playerName": rname, "specificPosition": rpos, "role": rrole}
                        if pid:
                            cache_doc["playerId"] = pid
                        await db.player_positions.update_one(
                            {"playerName": rname},
                            {"$set": cache_doc},
                            upsert=True
                        )
    except Exception as e:
        print(f"[GEMINI-POS] Error: {e}")

    return results
