"""AI-powered position resolution for players."""
import json
from config import db, XAI_API_KEY

GROK_POS_PROMPT_VERSION = 4


async def resolve_position_ai(player_name: str, sport: str = "soccer") -> dict:
    """Resolve a single player's position using cache first, then AI fallback.
    Returns {"position": "XX", "role": "..."} or empty strings if failed."""

    cached = await db.player_positions.find_one(
        {"playerName": player_name}, {"_id": 0, "specificPosition": 1, "role": 1}
    )
    if cached and cached.get("specificPosition"):
        return {"position": cached["specificPosition"], "role": cached.get("role", "")}

    if not XAI_API_KEY:
        return {"position": "", "role": ""}

    return await _grok_resolve_batch([{"playerName": player_name, "sport": sport}])


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

    if not unresolved or not XAI_API_KEY:
        return results

    grok_results = await _grok_resolve_batch(unresolved)
    results.update(grok_results)
    return results


async def _grok_resolve_batch(players: list) -> dict:
    """Call Gemini to resolve positions for a list of players.
    Uses the same rich vocabulary as the main predict.py resolver so that
    role strings flow directly into positional_baseline._role_variant().
    """
    if not players or not XAI_API_KEY:
        return {}

    results = {}
    player_lines = []
    for idx, pl in enumerate(players):
        player_lines.append(f"{idx+1}. {pl['playerName']} ({pl.get('sport', 'soccer')})")

    prompt = f"""You are a football/soccer tactical analyst. For each player below, return their primary specific position code and their exact tactical role label.

Soccer position codes: GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST

Role labels — pick EXACTLY one from this list:
  GK:  Shot-Stopper | Sweeper Keeper
  CB:  Ball-Playing CB | Stopper
  LB/RB: Fullback | Wing-Back | Inverted Fullback
  LWB/RWB: Wing-Back | Fullback
  CDM: Deep-Lying Playmaker | Anchor | Ball Winner
  CM:  Box-to-Box | Mezzala | Advanced Playmaker | Deep-Lying Playmaker
  CAM: Advanced Playmaker | Wide Playmaker | Shadow Striker
  LM/RM: Wide Playmaker | Traditional Winger
  LW/RW: Traditional Winger | Inverted Winger | Inside Forward | Progressive Carrier
  CF:  Complete Forward | False 9 | Target Man | Pressing Forward
  ST:  Poacher | Target Man | Complete Forward | Pressing Forward

Key role guidance:
  • CDM / Deep-Lying Playmaker (regista): highest pass volume on team, sits deepest, orchestrates build-up, LOW shots/dribbles. Examples: Vitinha (PSG), Rodri (Man City), Casemiro.
  • CDM / Anchor or Ball Winner: high tackles/interceptions, lower pass volume than DLP.
  • CM / Box-to-Box: balanced tackles + forward runs + key passes + moderate shots. Only when player visibly gets forward.
  • CAM / Advanced Playmaker: high key passes, plays ahead of midfield, low tackles.
  • LW/RW / Inverted Winger: cuts inside, high shots, low crosses. Examples: Salah, Gnabry, Sané.
  • LW/RW / Traditional Winger or Progressive Carrier: runs channels, high crosses, low shots.

Players:
{chr(10).join(player_lines)}

Return a JSON array — one object per player. Use EXACTLY the position codes and role labels from the lists above:
[{{"name":"exact player name","position":"XX","role":"exact role label"}}]
Only the JSON array, no markdown, no explanation."""

    try:
        from ai_engine import _ai_call
        raw = await _ai_call(prompt, temperature=0, max_tokens=800, timeout=20, json_mode=True)
        if not raw:
            return {}
        content = raw.strip()
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
                from datetime import datetime, timezone
                cache_doc = {
                    "playerName": rname,
                    "specificPosition": rpos,
                    "role": rrole,
                    "promptVersion": GROK_POS_PROMPT_VERSION,
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
                if pid:
                    cache_doc["playerId"] = pid
                await db.player_positions.update_one(
                    {"playerName": rname},
                    {"$set": cache_doc},
                    upsert=True
                )
    except Exception as e:
        print(f"[GROK-POS] Error: {e}")

    return results
