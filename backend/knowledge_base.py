"""
knowledge_base.py — Stage 1 of the app-owned knowledge layer.

Compiles durable tactical facts from existing cache collections into two
purpose-built MongoDB collections:
  • knowledge_teams   — team possession profile and build-up style
  • knowledge_players — player role, pass-volume by venue, prop tendencies

Design constraints (based on what is actually available in the DB):
  • team_avg_poss: season-average possession keyed by
      team_avg_poss_{team_id}_{league_id}_{season}; reliable source.
  • team_fixture_history: keyed by teamId only, contains multi-competition
      raw API-Football fixtures (fixture{}, league{}, teams{}, goals{},
      score{}).  Does NOT include per-fixture statistics (shots/passes/
      possession) — those require a separate fixtures/statistics call.
      Filter by league.id to get league-scoped fixture counts.
  • player_positions: stores specificPosition + genericPosition (not
      "position"); also stores role.
  • player_season_stats: stores API-Football players?team=… response;
      statistics[] has passes/shots/games sub-dicts.

Public API
----------
compile_team_style(team_id, league_id, season)  → dict | None
compile_player_profile(player_id, league_id)    → dict | None
fire_and_forget_compile(...)                    → None (schedules background)
get_team_kb(team_id, league_id, season)         → dict | None
get_player_kb(player_id, league_id)             → dict | None
kb_stats()                                      → dict
"""

from __future__ import annotations

import asyncio as aio
import hashlib
import json
import re
import unicodedata
from html.parser import HTMLParser
from datetime import datetime, timezone
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import db, CURRENT_SEASON

# ── TTL: recompile if older than 24 hours ────────────────────────────────────
_KB_TTL_SECONDS: int = 24 * 3600
_FBREF_TTL_SECONDS: int = 48 * 3600
_FBREF_USER_AGENT = (
    "Mozilla/5.0 (compatible; ReversePicksKnowledge/1.0; "
    "+https://fbref.com/)"
)

# FBref competition IDs are different from API-Football IDs.  Keep this map
# intentionally small and explicit: an unknown competition must fail open,
# never silently borrow another league's tactical data.
_FBREF_COMPETITIONS: dict[int, tuple[int, str]] = {
    39: (9, "Premier-League"),
    40: (10, "Championship"),
    140: (12, "La-Liga"),
    135: (11, "Serie-A"),
    78: (20, "Bundesliga"),
    61: (13, "Ligue-1"),
    253: (22, "Major-League-Soccer"),
    262: (31, "Liga-MX"),
    254: (182, "NWSL"),
    94: (32, "Primeira-Liga"),
    203: (26, "Super-Lig"),
    88: (23, "Eredivisie"),
}

# ── Minimum data thresholds ───────────────────────────────────────────────────
_MIN_APPEARANCES_FOR_TENDENCIES: int = 3  # need ≥3 appearances for per-90 flags

# ── Build-up style thresholds (possession %) ─────────────────────────────────
_POSSESSION_HIGH: float = 55.0   # >55% = possession-dominant
_POSSESSION_LOW: float  = 42.0   # <42% = counter-attacking / low-block


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _ts_now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _is_stale(doc: dict) -> bool:
    return (_ts_now() - (doc.get("_ts") or 0)) > _KB_TTL_SECONDS


def _safe_mean(values: list) -> Optional[float]:
    vals = [v for v in values if isinstance(v, (int, float)) and v >= 0]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _parse_pct(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  FBref enrichment — cache-first, fail-open, background-only
# ═══════════════════════════════════════════════════════════════════════════════

class _FBRefTableParser(HTMLParser):
    """Small stdlib parser for FBref tables, including commented-out tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._table: Optional[dict] = None
        self._row: Optional[dict] = None
        self._cell_tag: Optional[str] = None
        self._cell_stat: Optional[str] = None
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_map = dict(attrs)
        if tag == "table" and self._table is None:
            self._table = {
                "id": attrs_map.get("id", ""),
                "rows": [],
            }
        elif tag == "tr" and self._table is not None and self._row is None:
            self._row = {}
        elif (
            tag in ("th", "td")
            and self._row is not None
            and self._cell_tag is None
        ):
            self._cell_tag = tag
            self._cell_stat = attrs_map.get("data-stat")
            self._cell_text = []

    def handle_data(self, data: str) -> None:
        if self._cell_tag is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("th", "td") and self._cell_tag == tag:
            value = " ".join("".join(self._cell_text).split())
            if value and self._row is not None:
                stat = self._cell_stat
                if stat:
                    self._row[stat] = value
                elif "_label" not in self._row:
                    self._row["_label"] = value
            self._cell_tag = None
            self._cell_stat = None
            self._cell_text = []
        elif tag == "tr" and self._row is not None:
            if self._table is not None and self._row:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _parse_fbref_tables(html: str) -> list[dict]:
    """Parse visible and HTML-comment-wrapped FBref tables."""
    if not html:
        return []
    tables: list[dict] = []
    parser = _FBRefTableParser()
    parser.feed(html)
    tables.extend(parser.tables)
    # FBref wraps many data tables in <!-- ... --> to defer rendering.
    for comment in re.findall(r"<!--(.*?)-->", html, flags=re.DOTALL):
        if "<table" not in comment.lower():
            continue
        nested = _FBRefTableParser()
        nested.feed(comment)
        tables.extend(nested.tables)
    if not tables and "|" in html:
        # The reader proxy fallback returns Markdown rather than HTML.  Keep
        # this parser deliberately conservative: only headers that map to a
        # known FBref data-stat are admitted into the row.
        markdown_lines = [
            line.strip()
            for line in html.splitlines()
            if line.strip().startswith("|") and line.strip().endswith("|")
        ]
        if len(markdown_lines) >= 3:
            headers = [
                cell.strip()
                for cell in markdown_lines[0].strip("|").split("|")
            ]
            separator = markdown_lines[1].strip("|").split("|")
            if all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in separator):
                key_map = {
                    "player": "player",
                    "squad": "squad",
                    "team": "team",
                    "pressures": "pressures",
                    "ppda": "ppda",
                    "pressures %": "pressures_pct",
                    "touches def 3rd": "touches_def_3rd",
                    "touches mid 3rd": "touches_mid_3rd",
                    "touches att 3rd": "touches_att_3rd",
                    "progressive passes": "passes_progressive",
                    "progressive carries": "carries_progressive",
                }
                rows = []
                for line in markdown_lines[2:]:
                    cells = [cell.strip() for cell in line.strip("|").split("|")]
                    row = {}
                    for header, cell in zip(headers, cells):
                        key = key_map.get(re.sub(r"\s+", " ", header.lower()).strip())
                        if key and cell:
                            row[key] = cell
                    if row:
                        rows.append(row)
                if rows:
                    tables.append({"id": "markdown-fallback", "rows": rows})
    return tables


def _fbref_number(value) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "—", "–", "NA", "N/A"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except (TypeError, ValueError):
        return None


def _fbref_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _fbref_name_score(target: str, candidate: str) -> int:
    target_norm = _fbref_name(target)
    candidate_norm = _fbref_name(candidate)
    if not target_norm or not candidate_norm:
        return 0
    if target_norm == candidate_norm:
        return 100
    if target_norm in candidate_norm or candidate_norm in target_norm:
        return 85
    target_tokens = set(target_norm.split())
    candidate_tokens = set(candidate_norm.split())
    overlap = len(target_tokens & candidate_tokens)
    return 50 + (overlap * 10) if overlap else 0


def _fbref_row_name(row: dict) -> str:
    for key in ("team", "squad", "player", "name", "_label"):
        if row.get(key):
            return str(row[key])
    return ""


def _fbref_find_row(
    rows: list[dict],
    target_name: str,
    target_team: str = "",
) -> Optional[dict]:
    best: Optional[dict] = None
    best_score = 0
    for row in rows:
        candidate = (
            row.get("player")
            or row.get("name")
            or row.get("_label")
            if target_team
            else row.get("team")
            or row.get("squad")
            or row.get("_label")
        )
        score = _fbref_name_score(target_name, candidate)
        if score <= 0:
            continue
        if target_team:
            row_team = row.get("squad") or row.get("team") or row.get("club")
            if row_team:
                team_score = _fbref_name_score(target_team, str(row_team))
                if team_score == 0:
                    score -= 30
        if score > best_score:
            best = row
            best_score = score
    return best if best_score >= 50 else None


def _fbref_competition(league_id: int) -> Optional[tuple[int, str]]:
    return _FBREF_COMPETITIONS.get(int(league_id or 0))


def _fbref_recent(value) -> bool:
    try:
        return (_ts_now() - float(value)) < _FBREF_TTL_SECONDS
    except (TypeError, ValueError):
        return False


def _fbref_fetch_sync(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": _FBREF_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


async def _fbref_fetch(url: str) -> Optional[str]:
    """Fetch one page without blocking the event loop; return None on failure."""
    # Direct FBref HTML is preferred because it retains data-stat attributes.
    # Some hosted environments receive a 403 even with a browser UA, so use
    # two bounded alternatives rather than treating that as a schema success.
    candidates = [
        url,
        f"{url}?output=1",
        "https://r.jina.ai/http://" + url.split("://", 1)[-1],
    ]
    last_error = None
    for candidate in candidates:
        try:
            html = await aio.to_thread(_fbref_fetch_sync, candidate)
            if html:
                if candidate != url:
                    print(f"[FBREF] alternate transport used: {candidate.split('/')[2]}")
                return html
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        print(f"[FBREF] fetch unavailable url={url}: {type(last_error).__name__}")
    return None


def _fbref_pressure_label_from_rank(rows: list[dict], row: dict) -> Optional[str]:
    """Fallback label from FBref pressure volume when explicit PPDA is absent."""
    values = [
        _fbref_number(r.get("pressures") or r.get("pressure"))
        for r in rows
    ]
    values = [v for v in values if v is not None]
    current = _fbref_number(row.get("pressures") or row.get("pressure"))
    if current is None or len(values) < 6:
        return None
    values.sort()
    percentile = sum(1 for value in values if value <= current) / len(values)
    if percentile >= 0.67:
        return "high_press"
    if percentile <= 0.33:
        return "low_block"
    return "mid_block"


def _fbref_ppda_label(ppda: Optional[float]) -> Optional[str]:
    if ppda is None or ppda < 0:
        return None
    if ppda <= 8:
        return "high_press"
    if ppda < 11:
        return "mid_block"
    return "low_block"


async def fetch_fbref_team_pressing(
    team_name: str,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """Fetch a team's FBref pressing profile, without inventing PPDA.

    FBref's pressing table does not expose PPDA consistently across
    competitions.  When an explicit PPDA column exists we store it and apply
    the PPDA thresholds.  Otherwise we store the observed FBref pressure
    volume and a clearly-labelled pressure-rank fallback; ``ppda`` remains
    null so the UI never presents a proxy as verified PPDA.
    """
    competition = _fbref_competition(league_id)
    if not competition or not team_name:
        return None
    fbref_id, slug = competition
    url = f"https://fbref.com/en/comps/{fbref_id}/pressing/{slug}-Stats"
    html = await _fbref_fetch(url)
    if not html:
        return None
    rows = [
        row
        for table in _parse_fbref_tables(html)
        for row in table.get("rows", [])
    ]
    row = _fbref_find_row(rows, team_name)
    if not row:
        return None

    ppda = next(
        (
            _fbref_number(row.get(key))
            for key in ("ppda", "presses_allowed_per_def_action", "passes_allowed")
            if _fbref_number(row.get(key)) is not None
        ),
        None,
    )
    label = _fbref_ppda_label(ppda)
    method = "fbref_ppda" if label else None
    if label is None:
        label = _fbref_pressure_label_from_rank(rows, row)
        method = "fbref_pressure_rank" if label else None

    return {
        "fbrefTeamName": _fbref_row_name(row),
        "ppda": round(ppda, 2) if ppda is not None else None,
        "pressIntensityLabel": label,
        "pressIntensitySource": method,
        "pressIntensityMethod": (
            "Explicit FBref PPDA thresholds"
            if method == "fbref_ppda"
            else "FBref pressure-volume percentile; PPDA unavailable"
            if method == "fbref_pressure_rank"
            else "unavailable"
        ),
        "fbrefPressures": _fbref_number(row.get("pressures") or row.get("pressure")),
        "fbrefPressureSuccessPct": _fbref_number(
            row.get("pressures_pct") or row.get("pressure_success_pct")
        ),
        "fbrefDefensiveThirdPressures": _fbref_number(row.get("pressures_def_3rd")),
        "fbrefMiddleThirdPressures": _fbref_number(row.get("pressures_mid_3rd")),
        "fbrefAttackingThirdPressures": _fbref_number(row.get("pressures_att_3rd")),
        "fbrefSource": url,
        "fbrefStatus": "available" if label or ppda is not None else "partial",
    }


async def fetch_fbref_player_zones(
    player_name: str,
    team_name: str,
    league_id: int = 0,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """Fetch FBref zone touches and progressive actions for one player."""
    competition = _fbref_competition(league_id)
    if not competition or not player_name:
        return None
    fbref_id, slug = competition
    # Zone touches live in the Possession table; progressive passes live in
    # Passing. Fetch both so a successful Standard page cannot masquerade as
    # a complete zone packet with null fields.
    possession_url = f"https://fbref.com/en/comps/{fbref_id}/possession/{slug}-Stats"
    passing_url = f"https://fbref.com/en/comps/{fbref_id}/passing/{slug}-Stats"
    possession_html, passing_html = await aio.gather(
        _fbref_fetch(possession_url),
        _fbref_fetch(passing_url),
    )
    if not possession_html and not passing_html:
        return None
    rows = [
        row
        for html in (possession_html, passing_html)
        if html
        for table in _parse_fbref_tables(html)
        for row in table.get("rows", [])
    ]
    row = _fbref_find_row(rows, player_name, team_name)
    if not row:
        return None

    def first_number(*keys: str) -> Optional[float]:
        for key in keys:
            value = _fbref_number(row.get(key))
            if value is not None:
                return value
        return None

    def third_share(value: Optional[float], total: Optional[float]) -> Optional[float]:
        if value is None or not total:
            return None
        return round(value / total * 100, 1)

    def_third = first_number("touches_def_3rd", "touches_defensive_third")
    mid_third = first_number("touches_mid_3rd", "touches_middle_third")
    att_third = first_number("touches_att_3rd", "touches_attacking_third")
    zone_total = sum(v for v in (def_third, mid_third, att_third) if v is not None)
    shares = {
        "def": third_share(def_third, zone_total),
        "mid": third_share(mid_third, zone_total),
        "att": third_share(att_third, zone_total),
    }
    zone_dominance = None
    if shares["att"] is not None and shares["def"] is not None:
        if shares["att"] >= 45:
            zone_dominance = "advanced"
        elif shares["def"] >= 45:
            zone_dominance = "deep"
        else:
            zone_dominance = "mixed"

    return {
        "fbrefPlayerName": _fbref_row_name(row),
        "touchesDefThird": def_third,
        "touchesMidThird": mid_third,
        "touchesAttThird": att_third,
        "defThirdSharePct": shares["def"],
        "midThirdSharePct": shares["mid"],
        "attThirdSharePct": shares["att"],
        "progressivePasses": first_number("passes_progressive", "progressive_passes"),
        "progressiveCarries": first_number("carries_progressive", "progressive_carries"),
        "fbrefMinutes": first_number("minutes"),
        "zoneDominance": zone_dominance,
        "zoneSource": "fbref_possession_stats",
        "fbrefSource": [possession_url, passing_url],
        "fbrefStatus": "available" if zone_dominance or zone_total else "partial",
    }
# ═══════════════════════════════════════════════════════════════════════════════
#  Team style compiler
# ═══════════════════════════════════════════════════════════════════════════════

async def compile_team_style(
    team_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """
    Compile team style from available data sources and upsert into
    knowledge_teams.

    Data sources used:
    - team_avg_poss: season-average possession % (reliable, API-computed)
    - team_fixture_history: raw fixture list keyed by teamId — filtered to
      league_id for counts only; no per-fixture stats are parsed since the
      fixtures cache does not include a statistics[] field.

    Returns the compiled public doc (Mongo internals excluded) on success,
    None on any error.
    """
    if not team_id or not league_id:
        return None

    doc_key = f"{team_id}_{league_id}_{season}"

    # Skip recompile if fresh
    try:
        existing = await db.knowledge_teams.find_one({"_key": doc_key}, {"_id": 0})
        if (
            existing
            and not _is_stale(existing)
            and (
                not _fbref_competition(league_id)
                or _fbref_recent(existing.get("fbrefAttemptedTs"))
            )
        ):
            return {k: v for k, v in existing.items() if not k.startswith("_")}
    except Exception:
        pass

    try:
        # ── 1. Season-average possession from dedicated cache ─────────────────
        # team_avg_poss is keyed exactly by team+league+season so data is
        # already league-scoped and reliable.
        poss_cache_key = f"team_avg_poss_{team_id}_{league_id}_{season}"
        poss_doc = await db.team_avg_poss.find_one(
            {"_key": poss_cache_key}, {"_id": 0, "value": 1}
        )
        season_avg_poss: Optional[float] = (poss_doc or {}).get("value")

        # ── 2. League-scoped fixture count from team_fixture_history ──────────
        # team_fixture_history is keyed by teamId only; it may contain
        # multi-competition history.  Filter by league.id to get a count that
        # is meaningful for this specific league.
        # We deliberately do NOT parse per-fixture stats here: fixtures/ API
        # responses do not include a statistics[] field — that requires a
        # separate fixtures/statistics endpoint call.
        tfh = await db.team_fixture_history.find_one(
            {"teamId": team_id},
            {"_id": 0, "fixtures": 1},
        )
        raw_fixtures: list = (tfh or {}).get("fixtures") or []

        finished_terminal = {"FT", "AET", "PEN"}
        league_fixtures = [
            fx for fx in raw_fixtures
            if (
                (fx.get("league") or {}).get("id") == league_id
                and (fx.get("fixture", {}).get("status", {}).get("short") or "") in finished_terminal
            )
        ]

        # ── 3. Classify build-up style from possession % alone ────────────────
        build_up_style = _classify_build_up(season_avg_poss, len(league_fixtures))
        def_line       = _classify_defensive_line(season_avg_poss)

        doc = {
            "_key": doc_key,
            "teamId": team_id,
            "leagueId": league_id,
            "season": season,
            # Possession signal — from league-specific season-average cache
            "seasonAvgPoss": season_avg_poss,
            # Derived classifications (possession-based; extend when per-fixture
            # stats become available via a fixtures/statistics pull)
            "buildUpStyle": build_up_style,
            "defensiveLineHeight": def_line,
            # Provenance
            "leagueFixtureCount": len(league_fixtures),
            "hasPossessionData": season_avg_poss is not None,
            "_ts": _ts_now(),
            "_compiled": datetime.now(timezone.utc).isoformat(),
        }

        # Preserve a previously verified FBref packet while a refresh is
        # unavailable.  External enrichment is advisory and must never erase
        # the possession/style facts that the rest of the KB already trusts.
        _fbref_fields = (
            "fbrefTeamName", "ppda", "pressIntensityLabel",
            "pressIntensitySource", "pressIntensityMethod", "fbrefPressures",
            "fbrefPressureSuccessPct", "fbrefDefensiveThirdPressures",
            "fbrefMiddleThirdPressures", "fbrefAttackingThirdPressures",
            "fbrefSource", "fbrefStatus", "fbrefFetchedAt", "fbrefAttemptedTs",
        )
        for field in _fbref_fields:
            if existing and field in existing:
                doc[field] = existing[field]

        # This function only runs from fire_and_forget_compile().  It may make
        # one bounded web request, but never runs on the prediction response
        # path.  A failed/blocked FBref request is recorded and retried after
        # the 48-hour external TTL.
        if (
            _fbref_competition(league_id)
            and not _fbref_recent((existing or {}).get("fbrefAttemptedTs"))
        ):
            team_name = str((existing or {}).get("teamName") or "")
            if not team_name:
                try:
                    for collection in ("teams_master", "cache_teams"):
                        team_doc = await db[collection].find_one(
                            {
                                "$or": [
                                    {"teamId": team_id},
                                    {"id": team_id},
                                ],
                                "leagueId": league_id,
                            },
                            {"_id": 0, "name": 1, "teamName": 1},
                        )
                        team_name = str(
                            (team_doc or {}).get("name")
                            or (team_doc or {}).get("teamName")
                            or ""
                        )
                        if team_name:
                            break
                except Exception:
                    team_name = ""
            fbref_packet = await fetch_fbref_team_pressing(
                team_name=team_name,
                league_id=league_id,
                season=season,
            )
            doc["fbrefAttemptedTs"] = _ts_now()
            doc["fbrefFetchedAt"] = datetime.now(timezone.utc).isoformat()
            if fbref_packet:
                doc.update(fbref_packet)
            else:
                doc["fbrefStatus"] = "unavailable"

        try:
            await db.knowledge_teams.update_one(
                {"_key": doc_key},
                {"$set": doc},
                upsert=True,
            )
        except Exception as e:
            print(f"[KB] knowledge_teams write error team={team_id} league={league_id}: {e}")

        return {k: v for k, v in doc.items() if not k.startswith("_")}

    except Exception as e:
        print(f"[KB] compile_team_style error team={team_id} league={league_id}: {e}")
        return None


def _classify_build_up(avg_poss: Optional[float], n_league_fixtures: int) -> str:
    """
    Classify build-up style from season possession average.
    Requires at least one league fixture count to confirm this is meaningful data.
    """
    if avg_poss is None:
        return "unknown"
    if avg_poss >= _POSSESSION_HIGH:
        return "possession_dominant"
    if avg_poss <= _POSSESSION_LOW:
        return "counter_attacking"
    return "balanced"


def _classify_defensive_line(avg_poss: Optional[float]) -> str:
    """Infer defensive line height from season possession only."""
    if avg_poss is None:
        return "unknown"
    if avg_poss >= 55:
        return "high_line"
    if avg_poss <= 42:
        return "deep_block"
    return "mid_block"


# ═══════════════════════════════════════════════════════════════════════════════
#  Player profile compiler
# ═══════════════════════════════════════════════════════════════════════════════

async def compile_player_profile(
    player_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """
    Read player_season_stats + player_positions, derive prop tendency flags
    and per-venue pass-volume split, and upsert into knowledge_players.

    Schema notes:
    - player_positions uses specificPosition + genericPosition (not "position")
    - player_season_stats.statistics[] holds per-league API-Football stat blobs

    Returns the compiled doc (Mongo internals excluded) on success, None on error.
    """
    if not player_id:
        return None

    doc_key = f"{player_id}_{league_id}"

    # Skip recompile if fresh
    try:
        existing = await db.knowledge_players.find_one({"_key": doc_key}, {"_id": 0})
        if (
            existing
            and not _is_stale(existing)
            and (
                not _fbref_competition(league_id)
                or _fbref_recent(existing.get("fbrefAttemptedTs"))
            )
        ):
            return {k: v for k, v in existing.items() if not k.startswith("_")}
    except Exception:
        pass

    try:
        # ── 1. Season stats ───────────────────────────────────────────────────
        stats_doc = await db.player_season_stats.find_one(
            {"playerId": player_id, "season": season},
            {"_id": 0, "statistics": 1},
        )
        if not stats_doc:
            stats_doc = await db.player_season_stats.find_one(
                {"playerId": player_id, "season": season - 1},
                {"_id": 0, "statistics": 1},
            )

        stats_list: list = (stats_doc or {}).get("statistics") or []
        # Prefer the entry whose league matches; fall back to first entry
        stat_entry = next(
            (s for s in stats_list if (s.get("league") or {}).get("id") == league_id),
            stats_list[0] if stats_list else {},
        )

        games   = stat_entry.get("games")   or {}
        passing = stat_entry.get("passes")  or {}
        shots   = stat_entry.get("shots")   or {}

        appearances  = games.get("appearences") or 0   # API-Football typo
        minutes      = games.get("minutes")      or 0
        passes_total = passing.get("total")
        passes_acc   = passing.get("accuracy")
        passes_key   = passing.get("key")
        shots_total  = shots.get("total")
        shots_on     = shots.get("on")

        # Per-90 normalisation
        per90        = (minutes / 90.0) if minutes and minutes > 0 else None
        passes_per90 = round(passes_total / per90, 1) if passes_total and per90 else None
        shots_per90  = round(shots_total  / per90, 1) if shots_total  and per90 else None

        # ── 2. Position / role from player_positions ──────────────────────────
        # Schema: specificPosition (e.g. "CB"), genericPosition ("D"), role
        pos_doc = await db.player_positions.find_one(
            {"playerId": player_id},
            {"_id": 0, "specificPosition": 1, "genericPosition": 1, "role": 1},
        )
        specific_pos = (pos_doc or {}).get("specificPosition")   # e.g. "CB", "CM", "LW"
        generic_pos  = (pos_doc or {}).get("genericPosition")    # e.g. "D", "M", "F", "GK"
        role         = (pos_doc or {}).get("role")

        # ── 3. Prop tendency flags ────────────────────────────────────────────
        tendencies = _derive_tendencies(
            specific_pos=specific_pos,
            generic_pos=generic_pos,
            role=role,
            passes_per90=passes_per90,
            shots_per90=shots_per90,
            appearances=appearances,
        )

        # ── 4. Venue pass-volume split from settled picks ─────────────────────
        home_pass_avg, away_pass_avg = await _compute_venue_pass_split(player_id)

        doc = {
            "_key": doc_key,
            "playerId": player_id,
            "leagueId": league_id,
            "season": season,
            # Identity — using exact field names from player_positions
            "specificPosition": specific_pos,
            "genericPosition": generic_pos,
            "role": role,
            # Season aggregates (per-90 where meaningful)
            "passesTotal": passes_total,
            "passesPer90": passes_per90,
            "passAccuracyPct": _parse_pct(passes_acc),
            "keyPassesSeason": passes_key,
            "shotsPer90": shots_per90,
            "shotsOnPer90": round(shots_on / per90, 2) if shots_on and per90 else None,
            "appearances": appearances,
            "minutesSeason": minutes,
            # Venue split (derived from settled pick history — pass props only)
            "homePassAvg": home_pass_avg,
            "awayPassAvg": away_pass_avg,
            # Tendency flags (presence = True; absence = not applicable)
            "tendencies": tendencies,
            "_ts": _ts_now(),
            "_compiled": datetime.now(timezone.utc).isoformat(),
        }

        _fbref_fields = (
            "fbrefPlayerName", "touchesDefThird", "touchesMidThird",
            "touchesAttThird", "defThirdSharePct", "midThirdSharePct",
            "attThirdSharePct", "progressivePasses", "progressiveCarries",
            "fbrefMinutes", "zoneDominance", "zoneSource", "fbrefSource",
            "fbrefStatus", "fbrefFetchedAt", "fbrefAttemptedTs",
        )
        for field in _fbref_fields:
            if existing and field in existing:
                doc[field] = existing[field]

        if (
            _fbref_competition(league_id)
            and not _fbref_recent((existing or {}).get("fbrefAttemptedTs"))
        ):
            player_name = str((existing or {}).get("playerName") or "")
            team_name = str((existing or {}).get("teamName") or "")
            if not player_name or not team_name:
                try:
                    from cache import COL_PLAYERS
                    identity_doc = await db[COL_PLAYERS].find_one(
                        {
                            "playerId": player_id,
                            "$or": [
                                {"leagueId": league_id},
                                {"leagueId": {"$exists": False}},
                            ],
                        },
                        {
                            "_id": 0,
                            "name": 1,
                            "playerName": 1,
                            "teamName": 1,
                        },
                    )
                    player_name = player_name or str(
                        (identity_doc or {}).get("name")
                        or (identity_doc or {}).get("playerName")
                        or ""
                    )
                    team_name = team_name or str(
                        (identity_doc or {}).get("teamName") or ""
                    )
                except Exception:
                    pass
            fbref_packet = await fetch_fbref_player_zones(
                player_name=player_name,
                team_name=team_name,
                league_id=league_id,
                season=season,
            )
            doc["fbrefAttemptedTs"] = _ts_now()
            doc["fbrefFetchedAt"] = datetime.now(timezone.utc).isoformat()
            if fbref_packet:
                doc.update(fbref_packet)
            else:
                doc["fbrefStatus"] = "unavailable"

        try:
            await db.knowledge_players.update_one(
                {"_key": doc_key},
                {"$set": doc},
                upsert=True,
            )
        except Exception as e:
            print(f"[KB] knowledge_players write error player={player_id}: {e}")

        return {k: v for k, v in doc.items() if not k.startswith("_")}

    except Exception as e:
        print(f"[KB] compile_player_profile error player={player_id} league={league_id}: {e}")
        return None


def _derive_tendencies(
    specific_pos: Optional[str],
    generic_pos: Optional[str],
    role: Optional[str],
    passes_per90: Optional[float],
    shots_per90: Optional[float],
    appearances: int,
) -> dict:
    """
    Return boolean tendency flags for Stage 2 prompt injection.

    Uses specificPosition for precise thresholds; falls back to genericPosition.
    Flags are only set when evidence is sufficient (≥ _MIN_APPEARANCES_FOR_TENDENCIES).
    """
    t: dict = {}
    spos = (specific_pos or "").upper()   # e.g. "CB", "CM", "LW"
    gpos = (generic_pos  or "").upper()   # e.g. "D", "M", "F", "GK"
    rl   = (role         or "").lower()

    # Position bucket using specificPosition first, genericPosition as fallback
    is_gk  = spos == "GK" or gpos == "GK"
    is_def = gpos == "D"  or spos in ("CB", "LB", "RB", "LWB", "RWB", "WB")
    is_mid = gpos == "M"  or spos in ("CM", "CDM", "CAM", "DM", "AM")
    is_fwd = gpos == "F"  or spos in ("ST", "LW", "RW", "CF", "SS")

    # Only derive per-90 flags when we have enough samples
    if appearances >= _MIN_APPEARANCES_FOR_TENDENCIES and passes_per90 is not None:
        # Position-specific high/low pass-volume thresholds
        if is_gk and passes_per90 >= 40:
            t["highVolumePasser"] = True
        elif is_gk and passes_per90 < 25:
            t["lowVolumePasser"] = True
        elif is_def and passes_per90 >= 55:
            t["highVolumePasser"] = True
        elif is_def and passes_per90 < 30:
            t["lowVolumePasser"] = True
        elif is_mid and passes_per90 >= 65:
            t["highVolumePasser"] = True
        elif is_mid and passes_per90 < 35:
            t["lowVolumePasser"] = True
        elif is_fwd and passes_per90 >= 40:
            t["highVolumePasser"] = True
        elif is_fwd and passes_per90 < 20:
            t["lowVolumePasser"] = True

    if appearances >= _MIN_APPEARANCES_FOR_TENDENCIES and shots_per90 is not None:
        if shots_per90 >= 2.5:
            t["frequentShooter"] = True

    # Role-based flags (from cached Gemini-resolved role — no threshold required)
    if rl:
        if "pressing" in rl or "engine" in rl:
            t["pressingRole"] = True
        if "deep" in rl or "pivot" in rl or "anchor" in rl:
            t["deepLyingRole"] = True
        if "false 9" in rl or "false9" in rl:
            t["false9"] = True
        if "winger" in rl or "wide" in rl:
            t["wingerRole"] = True
        if "sweeper" in rl or "libero" in rl:
            t["ballPlayingDefender"] = True

    if appearances < _MIN_APPEARANCES_FOR_TENDENCIES:
        t["thinSample"] = True

    return t


async def _compute_venue_pass_split(player_id: int) -> tuple[Optional[float], Optional[float]]:
    """
    Derive average passes at home vs away from settled pick history.
    Only uses pass-prop picks where actualValue is confirmed.
    """
    try:
        home_passes: list[float] = []
        away_passes: list[float] = []

        async for pick in db.picks.find(
            {
                "playerId": player_id,
                "status": {"$in": ["settled", "SETTLED", "HIT", "MISS"]},
                "propType": {"$regex": "pass", "$options": "i"},
                "actualValue": {"$ne": None},
            },
            {"_id": 0, "actualValue": 1, "venue": 1, "playerIsHome": 1},
        ).limit(40):
            av = pick.get("actualValue")
            if av is None:
                continue
            # Determine venue: prefer explicit playerIsHome, fall back to venue string
            is_home = pick.get("playerIsHome")
            if is_home is None:
                venue = (pick.get("venue") or "").lower()
                is_home = venue == "home"
            try:
                val = float(av)
                if val > 0:
                    if is_home:
                        home_passes.append(val)
                    else:
                        away_passes.append(val)
            except (ValueError, TypeError):
                pass

        return _safe_mean(home_passes), _safe_mean(away_passes)
    except Exception:
        return None, None


# ═══════════════════════════════════════════════════════════════════════════════
#  Fire-and-forget wrapper (called from predict.py)
# ═══════════════════════════════════════════════════════════════════════════════

async def fire_and_forget_compile(
    *,
    player_id: int = 0,
    team_id: int = 0,
    league_id: int = 0,
    opponent_id: int = 0,
    season: int = CURRENT_SEASON,
) -> None:
    """
    Schedule KB compilation tasks for the entities just predicted.
    Runs in the background via asyncio.ensure_future; any failure is logged
    and swallowed — it must never affect the prediction response.
    """
    tasks = []
    if team_id and league_id:
        tasks.append(_safe_compile_team(team_id, league_id, season))
    if opponent_id and league_id:
        tasks.append(_safe_compile_team(opponent_id, league_id, season))
    if player_id and league_id:
        tasks.append(_safe_compile_player(player_id, league_id, season))
    if tasks:
        aio.ensure_future(aio.gather(*tasks, return_exceptions=True))


async def _safe_compile_team(team_id: int, league_id: int, season: int) -> None:
    try:
        await compile_team_style(team_id, league_id, season)
    except Exception as e:
        print(f"[KB fire-and-forget] team compile error team={team_id}: {e}")


async def _safe_compile_player(player_id: int, league_id: int, season: int) -> None:
    try:
        await compile_player_profile(player_id, league_id, season)
    except Exception as e:
        print(f"[KB fire-and-forget] player compile error player={player_id}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Readers (Stage 2 — fast, no API calls, no recompile)
# ═══════════════════════════════════════════════════════════════════════════════

async def get_team_kb(
    team_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """Return the knowledge_teams doc for a team (read-only, no recompile)."""
    if not team_id:
        return None
    doc_key = f"{team_id}_{league_id}_{season}"
    try:
        doc = await db.knowledge_teams.find_one({"_key": doc_key}, {"_id": 0})
        if doc:
            return {k: v for k, v in doc.items() if not k.startswith("_")}
    except Exception:
        pass
    return None


async def get_player_kb(
    player_id: int,
    league_id: int = 0,
) -> Optional[dict]:
    """
    Return the knowledge_players doc for a player (read-only, no recompile).
    Falls back to any-league entry if the league-specific one is absent.
    """
    if not player_id:
        return None
    doc_key = f"{player_id}_{league_id}"
    try:
        doc = await db.knowledge_players.find_one({"_key": doc_key}, {"_id": 0})
        if not doc and league_id:
            doc = await db.knowledge_players.find_one(
                {"playerId": player_id}, {"_id": 0}
            )
        if doc:
            return {k: v for k, v in doc.items() if not k.startswith("_")}
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Stats (owner dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

async def kb_stats() -> dict:
    """Return collection-level counts and freshness metrics including miss counter."""
    try:
        cutoff = _ts_now() - _KB_TTL_SECONDS
        results = await aio.gather(
            db.knowledge_teams.count_documents({}),
            db.knowledge_teams.count_documents({"_ts": {"$gt": cutoff}}),
            db.knowledge_players.count_documents({}),
            db.knowledge_players.count_documents({"_ts": {"$gt": cutoff}}),
            db.knowledge_heuristics.count_documents({}),
            db.knowledge_stats.find_one({"_id": "kb_misses"}),
            return_exceptions=True,
        )
        team_total, team_fresh, player_total, player_fresh, h_total, miss_doc = results
        kb_misses = (miss_doc.get("count", 0) if isinstance(miss_doc, dict) else 0)
        return {
            "teamsTotal":      team_total   if isinstance(team_total,   int) else 0,
            "teamsFresh":      team_fresh   if isinstance(team_fresh,   int) else 0,
            "playersTotal":    player_total if isinstance(player_total, int) else 0,
            "playersFresh":    player_fresh if isinstance(player_fresh, int) else 0,
            "heuristicsTotal": h_total      if isinstance(h_total,      int) else 0,
            "kbMisses":        kb_misses,
            "ttlHours":        _KB_TTL_SECONDS // 3600,
        }
    except Exception as e:
        return {
            "error":           str(e),
            "teamsTotal":      0,
            "teamsFresh":      0,
            "playersTotal":    0,
            "playersFresh":    0,
            "heuristicsTotal": 0,
            "kbMisses":        0,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 2 — Heuristics seed, miss counter, and fact-bundle assembly
# ═══════════════════════════════════════════════════════════════════════════════

_HEURISTICS_SEED: list[dict] = [
    # pass_attempts / passes
    {"role": "Ball-playing CB", "opponentStyleTag": "counter_attacking",   "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent": -12.0, "confidence": "medium",
     "note": "Counter-attacks bypass CBs; time-on-ball shrinks as opponent plays direct"},
    {"role": "Ball-playing CB", "opponentStyleTag": "possession_dominant", "prop": "pass_attempts",
     "direction": "OVER",  "deltaPercent":  10.0, "confidence": "medium",
     "note": "Both teams cycle possession; ball-playing CB recycles constantly"},
    {"role": "Ball-playing CB", "opponentStyleTag": "deep_block",          "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent":  -8.0, "confidence": "low",
     "note": "Compact defense limits build-up depth; CBs take fewer forward passes"},
    {"role": "Ball-playing CB", "opponentStyleTag": "high_line",           "prop": "pass_attempts",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "low",
     "note": "High defensive line creates space for CB to play into"},
    {"role": "Anchor",          "opponentStyleTag": "deep_block",          "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent":  -8.0, "confidence": "medium",
     "note": "Low block compresses central midfield; fewer urgent distribution passes for pivot"},
    {"role": "Anchor",          "opponentStyleTag": "possession_dominant", "prop": "pass_attempts",
     "direction": "OVER",  "deltaPercent":  10.0, "confidence": "medium",
     "note": "Ball-dominant teams cycle through the pivot constantly"},
    {"role": "Anchor",          "opponentStyleTag": "high_line",           "prop": "pass_attempts",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "low",
     "note": "High press leaves space for CDM to exploit with quick distribution"},
    {"role": "Box-to-Box",      "opponentStyleTag": "possession_dominant", "prop": "pass_attempts",
     "direction": "OVER",  "deltaPercent":  12.0, "confidence": "high",
     "note": "Box-to-box midfielder is primary ball-carrier in possession systems"},
    {"role": "Box-to-Box",      "opponentStyleTag": "counter_attacking",   "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent": -10.0, "confidence": "medium",
     "note": "Fewer possession phases reduce pass opportunities for box-to-box"},
    {"role": "Pressing Forward","opponentStyleTag": "deep_block",          "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent": -10.0, "confidence": "medium",
     "note": "Low block limits touch count for pressing forwards who press high"},
    {"role": "Inverted Winger", "opponentStyleTag": "possession_dominant", "prop": "pass_attempts",
     "direction": "OVER",  "deltaPercent":   6.0, "confidence": "low",
     "note": "More possession cycles increase winger touches and pass involvement"},
    # saves
    {"role": "Goalkeeper",      "opponentStyleTag": "possession_dominant", "prop": "saves",
     "direction": "UNDER", "deltaPercent": -15.0, "confidence": "high",
     "note": "Opponent controlling ball means fewer shots on target conceded"},
    {"role": "Goalkeeper",      "opponentStyleTag": "counter_attacking",   "prop": "saves",
     "direction": "OVER",  "deltaPercent":  15.0, "confidence": "high",
     "note": "Counter-attacking teams create more direct shots; save demand rises"},
    {"role": "Goalkeeper",      "opponentStyleTag": "high_line",           "prop": "saves",
     "direction": "UNDER", "deltaPercent":  -8.0, "confidence": "low",
     "note": "High-possession opponent (high line) keeps ball away from goal; keeper faces fewer shots"},
    {"role": "Goalkeeper",      "opponentStyleTag": "deep_block",          "prop": "saves",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "low",
     "note": "Low-possession opponent (deep block) relies on direct transitions; save demand rises on the break"},
    # shots
    {"role": "Pressing Forward","opponentStyleTag": "deep_block",          "prop": "shots",
     "direction": "UNDER", "deltaPercent": -12.0, "confidence": "high",
     "note": "Parked bus blocks shooting lanes; pressing forward rarely gets a clean look on goal"},
    {"role": "Pressing Forward","opponentStyleTag": "high_line",           "prop": "shots",
     "direction": "OVER",  "deltaPercent":  12.0, "confidence": "high",
     "note": "Space behind high defensive line creates 1v1 goal opportunities for pressing forward"},
    {"role": "False 9",         "opponentStyleTag": "possession_dominant", "prop": "shots",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "medium",
     "note": "Possession phases give False 9 more interior touches and shooting chances"},
    {"role": "Inverted Winger", "opponentStyleTag": "deep_block",          "prop": "shots",
     "direction": "OVER",  "deltaPercent":   6.0, "confidence": "low",
     "note": "Opponent sitting deep cedes possession; inverted winger benefits from more touches and cut-inside opportunities"},
    {"role": "Inverted Winger", "opponentStyleTag": "counter_attacking",   "prop": "shots",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "medium",
     "note": "Fast transitions suit the inverted wide player cutting inside on the break"},
    # key_passes
    {"role": "False 9",         "opponentStyleTag": "deep_block",          "prop": "key_passes",
     "direction": "UNDER", "deltaPercent": -12.0, "confidence": "medium",
     "note": "Compact defense leaves no space for creative through-balls by False 9"},
    {"role": "Box-to-Box",      "opponentStyleTag": "possession_dominant", "prop": "key_passes",
     "direction": "OVER",  "deltaPercent":  10.0, "confidence": "medium",
     "note": "High possession cycles = more chance-creation opportunities from box-to-box"},
    {"role": "Inverted Winger", "opponentStyleTag": "possession_dominant", "prop": "key_passes",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "low",
     "note": "More touches in final third unlock winger creativity and assist opportunities"},
    # tackles
    {"role": "Pressing Forward","opponentStyleTag": "possession_dominant", "prop": "tackles",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "medium",
     "note": "Press-intensive role generates more tackle attempts in opponent half"},
    {"role": "Anchor",          "opponentStyleTag": "counter_attacking",   "prop": "tackles",
     "direction": "OVER",  "deltaPercent":   6.0, "confidence": "medium",
     "note": "Opponent transitions create more defensive duties and tackle attempts for pivot"},
    {"role": "Ball-playing CB", "opponentStyleTag": "counter_attacking",   "prop": "tackles",
     "direction": "OVER",  "deltaPercent":  10.0, "confidence": "medium",
     "note": "Counter-attacking opponent advances more; ball-playing CB makes more tackles"},
    # clearances
    {"role": "Ball-playing CB", "opponentStyleTag": "counter_attacking",   "prop": "clearances",
     "direction": "OVER",  "deltaPercent":  12.0, "confidence": "medium",
     "note": "Direct ball forward increases aerial duels and clearance demand for CB"},
    {"role": "Pressing CB",     "opponentStyleTag": "counter_attacking",   "prop": "clearances",
     "direction": "OVER",  "deltaPercent":  15.0, "confidence": "high",
     "note": "Aggressive pressing CB + direct counter opponent = highest clearance volume"},
    {"role": "Ball-playing CB", "opponentStyleTag": "possession_dominant", "prop": "clearances",
     "direction": "UNDER", "deltaPercent":  -8.0, "confidence": "medium",
     "note": "Possession opponents rarely reach danger zones requiring clearances from CB"},
    # composite
    {"role": "Target Forward",  "opponentStyleTag": "possession_dominant", "prop": "shots",
     "direction": "OVER",  "deltaPercent":   8.0, "confidence": "medium",
     "note": "Possession teams generate more crosses; target forward gets more headers and shots"},
]

# FBref-derived style tags remain a separate seed set so the original Stage 2
# contract (30 possession/style rules) stays stable.  These are explanatory
# rules only; they do not alter the deterministic projection until settled-pick
# replay validates the relationship.
_FBREF_HEURISTICS_SEED: list[dict] = [
    {"role": "Ball-playing CB", "opponentStyleTag": "high_press", "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent": -12.0, "confidence": "medium",
     "note": "High press compresses the first-phase window; the CB has less settled time to recycle passes"},
    {"role": "Ball-playing CB", "opponentStyleTag": "mid_block", "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent": -5.0, "confidence": "low",
     "note": "A compact mid-block reduces uncontested progression lanes without removing all circulation"},
    {"role": "Ball-playing CB", "opponentStyleTag": "low_block", "prop": "clearances",
     "direction": "OVER", "deltaPercent": 10.0, "confidence": "low",
     "note": "Low-block opponents invite territory and transitions that increase defensive clearance demand"},
    {"role": "Anchor", "opponentStyleTag": "high_press", "prop": "pass_attempts",
     "direction": "UNDER", "deltaPercent": -8.0, "confidence": "medium",
     "note": "High pressure closes the pivot's receiving window and forces quicker release decisions"},
    {"role": "Anchor", "opponentStyleTag": "low_block", "prop": "pass_attempts",
     "direction": "OVER", "deltaPercent": 8.0, "confidence": "low",
     "note": "A low block leaves the pivot as a frequent circulation outlet in settled possession"},
    {"role": "Pressing Forward", "opponentStyleTag": "high_press", "prop": "tackles",
     "direction": "OVER", "deltaPercent": 8.0, "confidence": "low",
     "note": "Both teams contest buildup earlier, increasing tackle opportunities for a pressing forward"},
    {"role": "Pressing Forward", "opponentStyleTag": "low_block", "prop": "shots",
     "direction": "UNDER", "deltaPercent": -8.0, "confidence": "low",
     "note": "A low block reduces clean central shooting lanes even when the forward has territory"},
    {"role": "Inverted Winger", "opponentStyleTag": "high_press", "prop": "shots",
     "direction": "UNDER", "deltaPercent": -6.0, "confidence": "low",
     "note": "High pressure can trap the wide player in earlier buildup phases and reduce final-third entries"},
    {"role": "Inverted Winger", "opponentStyleTag": "low_block", "prop": "shots",
     "direction": "OVER", "deltaPercent": 6.0, "confidence": "low",
     "note": "A low block can concede repeated wide-to-inside touches and cut-in shooting attempts"},
    {"role": "Goalkeeper", "opponentStyleTag": "high_press", "prop": "saves",
     "direction": "UNDER", "deltaPercent": -6.0, "confidence": "low",
     "note": "High pressing without sustained final-third possession can reduce the opponent's settled shot volume"},
]


async def seed_knowledge_heuristics() -> int:
    """
    Idempotent upsert of curated heuristics into knowledge_heuristics.
    Keyed by role|opponentStyleTag|prop|direction.
    Returns the number of upserted/updated documents.
    """
    count = 0
    for rule in [*_HEURISTICS_SEED, *_FBREF_HEURISTICS_SEED]:
        key = f"{rule['role']}|{rule['opponentStyleTag']}|{rule['prop']}|{rule['direction']}"
        doc = {**rule, "_key": key, "source": "curated", "version": 1}
        try:
            await db.knowledge_heuristics.update_one(
                {"_key": key},
                {"$set": doc},
                upsert=True,
            )
            count += 1
        except Exception as e:
            print(f"[KB seed] heuristic upsert failed key={key}: {e}")
    if count:
        print(f"[KB seed] knowledge_heuristics: {count} rules seeded/updated")
    return count


async def _bump_kb_miss_counter() -> None:
    """Atomically increment the lifetime KB-miss counter in knowledge_stats."""
    try:
        await db.knowledge_stats.update_one(
            {"_id": "kb_misses"},
            {"$inc": {"count": 1}},
            upsert=True,
        )
    except Exception:
        pass  # miss counter is advisory; never block the prediction path


def _normalize_prop_for_kb(prop_type: str) -> str:
    """Map raw propType values to the canonical KB prop keys used in heuristics."""
    pt = (prop_type or "").lower().replace("_", " ")
    if "pass" in pt and "key" not in pt:
        return "pass_attempts"
    if "save" in pt:
        return "saves"
    if "shot" in pt:
        return "shots"
    if "key" in pt or "chance creat" in pt:
        return "key_passes"
    if "tackle" in pt or "intercept" in pt:
        return "tackles"
    if "clear" in pt:
        return "clearances"
    return pt.replace(" ", "_")


def _role_matches(heuristic_role: str, player_role: str) -> bool:
    """Return True if the heuristic's role tag fuzzy-matches the player's KB role.

    Uses substring matching first (fast path), then falls back to a strict
    token-subset check: ALL tokens in the heuristic role must appear in the
    player role.  This prevents "Pressing CB" from matching "Ball-playing CB"
    just because both share the token "CB".
    """
    if not heuristic_role or heuristic_role.lower() in ("any", ""):
        return True
    if not player_role:
        return False
    pl = player_role.lower()
    hr = heuristic_role.lower()
    # Fast path: direct substring match (either direction)
    if hr in pl or pl in hr:
        return True
    # Token-level: ALL heuristic tokens must be present in player role tokens.
    # Using issubset (not intersection) stops shared positional suffixes like
    # "CB" from causing false positives across distinct roles.
    _stop = {"the", "a", "an", "of", "in", "and"}
    hr_toks = set(hr.replace("-", " ").split()) - _stop
    pl_toks = set(pl.replace("-", " ").split()) - _stop
    return bool(hr_toks) and hr_toks.issubset(pl_toks)


async def assemble_fact_bundle(
    player_id: int,
    team_id: int,
    opponent_id: int,
    prop_type: str,
    venue: str,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> dict:
    """
    Assemble a verified fact bundle for Stage 2 Gemini prompt injection.

    Reads player KB + opponent team KB + curated heuristics — all fast indexed
    MongoDB reads with no external API calls.

    Returns:
        {
          "hit":     bool — True if at least one KB source had data,
          "bundle":  dict — raw bundle fields for downstream logic,
          "text":    str  — pre-rendered FACT BUNDLE block for the prompt,
          "version": str  — 12-char hash of stable content (for cache versioning),
        }
    """
    try:
        # ── 1. Read KB docs in parallel ───────────────────────────────────────
        player_kb, opponent_kb = await aio.gather(
            get_player_kb(player_id, league_id),
            get_team_kb(opponent_id, league_id, season),
            return_exceptions=True,
        )
        player_kb   = player_kb   if isinstance(player_kb,   dict) else None
        opponent_kb = opponent_kb if isinstance(opponent_kb, dict) else None

        # ── 2. Match curated heuristics ───────────────────────────────────────
        normalized_prop = _normalize_prop_for_kb(prop_type)
        opponent_tags: set[str] = set()
        if opponent_kb:
            for tag_key in (
                "buildUpStyle",
                "defensiveLineHeight",
                "pressIntensityLabel",
            ):
                tag = opponent_kb.get(tag_key)
                if tag and tag != "unknown":
                    opponent_tags.add(tag)

        player_role = (player_kb or {}).get("role") or ""
        matched_heuristics: list[dict] = []

        if opponent_tags:
            try:
                async for h in db.knowledge_heuristics.find(
                    {
                        "prop": normalized_prop,
                        "opponentStyleTag": {"$in": list(opponent_tags)},
                    },
                    {"_id": 0},
                ).limit(10):
                    if _role_matches(h.get("role", "any"), player_role):
                        matched_heuristics.append(h)
                        if len(matched_heuristics) >= 2:
                            break
            except Exception as _h_exc:
                print(f"[KB] heuristics query failed: {_h_exc}")

        # ── 3. Decide hit / miss ──────────────────────────────────────────────
        hit = bool(player_kb or opponent_kb or matched_heuristics)

        if not hit:
            await _bump_kb_miss_counter()
            print(
                f"[KB MISS] player={player_id} opponent={opponent_id}"
                f" prop={normalized_prop} league={league_id}"
            )
            return {"hit": False, "bundle": {}, "text": "", "version": "no_bundle"}

        # ── 4. Render the FACT BUNDLE text block ──────────────────────────────
        lines: list[str] = []

        if player_kb:
            pos_label = " / ".join(
                p for p in [player_kb.get("specificPosition"), player_kb.get("role")] if p
            ) or "unknown"
            passes90 = player_kb.get("passesPer90")
            apps = player_kb.get("appearances") or 0
            lines.append(
                f"Player profile: {pos_label}"
                + (f" | Season passes/90: {passes90}" if passes90 is not None else "")
                + f" | Appearances: {apps}"
            )
            home_avg = player_kb.get("homePassAvg")
            away_avg = player_kb.get("awayPassAvg")
            if home_avg is not None or away_avg is not None:
                lines.append(
                    f"Venue pass split:"
                    f" home avg {home_avg if home_avg is not None else 'n/a'}"
                    f" | away avg {away_avg if away_avg is not None else 'n/a'}"
                    + (f" (playing {venue})" if venue else "")
                )
            tend_flags = [k for k, v in (player_kb.get("tendencies") or {}).items() if v]
            if tend_flags:
                lines.append(f"Tendencies: {', '.join(tend_flags)}")
            zone = player_kb.get("zoneDominance")
            zone_parts = []
            for label, key in (
                ("def-third", "defThirdSharePct"),
                ("mid-third", "midThirdSharePct"),
                ("att-third", "attThirdSharePct"),
            ):
                value = player_kb.get(key)
                if value is not None:
                    zone_parts.append(f"{label} {value}%")
            if zone or zone_parts:
                lines.append(
                    "Player zones:"
                    + (f" {zone}" if zone else "")
                    + (f" | {', '.join(zone_parts)}" if zone_parts else "")
                    + (
                        f" | Progressive passes: {player_kb['progressivePasses']}"
                        if player_kb.get("progressivePasses") is not None
                        else ""
                    )
                    + (
                        f" | Progressive carries: {player_kb['progressiveCarries']}"
                        if player_kb.get("progressiveCarries") is not None
                        else ""
                    )
                )
        else:
            lines.append("Player profile: no KB data available")

        if opponent_kb:
            poss_label = (
                f"{opponent_kb['seasonAvgPoss']:.1f}% avg possession"
                if opponent_kb.get("seasonAvgPoss") is not None
                else "possession unknown"
            )
            lines.append(
                f"Opponent style: {opponent_kb.get('buildUpStyle', 'unknown')}"
                f" | Defensive line: {opponent_kb.get('defensiveLineHeight', 'unknown')}"
                f" | {poss_label}"
            )
            press_label = opponent_kb.get("pressIntensityLabel")
            if press_label and opponent_kb.get("fbrefStatus") == "available":
                press_method = opponent_kb.get("pressIntensityMethod") or "FBref"
                ppda = opponent_kb.get("ppda")
                lines.append(
                    f"Opponent pressure: {press_label}"
                    + (f" | PPDA: {ppda}" if ppda is not None else "")
                    + f" | source: {press_method}"
                )
        else:
            lines.append("Opponent style: no KB data available")

        if matched_heuristics:
            for h in matched_heuristics:
                conf  = h.get("confidence", "")
                delta = h.get("deltaPercent")
                sign  = "+" if (delta or 0) > 0 else ""
                delta_str = f" ({sign}{delta}%)" if delta is not None else ""
                lines.append(
                    f"Matchup rule [{h.get('prop')} × {h.get('opponentStyleTag')}]:"
                    f" {h.get('note', '')} → lean {h.get('direction')}{delta_str}"
                    + (f" | confidence: {conf}" if conf else "")
                )
        else:
            lines.append(
                "Matchup rule: no curated rule for this role × opponent style combination"
            )

        bundle_text = "\n".join(lines)

        # ── 5. Stable version hash (excludes TTL timestamps) ──────────────────
        hash_input = {
            "playerRole":  player_role,
            "playerPos":   (player_kb or {}).get("specificPosition"),
            "passes90":    (player_kb or {}).get("passesPer90"),
            "appearances": (player_kb or {}).get("appearances"),  # rendered in bundle text
            "homePass":    (player_kb or {}).get("homePassAvg"),
            "awayPass":    (player_kb or {}).get("awayPassAvg"),
            "tendencies":  sorted((player_kb or {}).get("tendencies", {}).keys()),
            "zone":        (player_kb or {}).get("zoneDominance"),
            "zoneShares":  {
                "def": (player_kb or {}).get("defThirdSharePct"),
                "mid": (player_kb or {}).get("midThirdSharePct"),
                "att": (player_kb or {}).get("attThirdSharePct"),
            },
            "progressivePasses": (player_kb or {}).get("progressivePasses"),
            "progressiveCarries": (player_kb or {}).get("progressiveCarries"),
            "oppStyle":    (opponent_kb or {}).get("buildUpStyle"),
            "defLine":     (opponent_kb or {}).get("defensiveLineHeight"),
            "oppPoss":     (opponent_kb or {}).get("seasonAvgPoss"),
            "pressLabel":  (opponent_kb or {}).get("pressIntensityLabel"),
            "ppda":        (opponent_kb or {}).get("ppda"),
            "pressSource": (opponent_kb or {}).get("pressIntensitySource"),
            # Full heuristic content — any rule edit (note/direction/delta) must invalidate cache.
            "heuristics": sorted(
                [
                    {
                        "key":        h.get("_key", ""),
                        "note":       h.get("note", ""),
                        "direction":  h.get("direction", ""),
                        "delta":      h.get("deltaPercent"),
                        "confidence": h.get("confidence", ""),
                        "version":    h.get("version", 1),
                    }
                    for h in matched_heuristics
                ],
                key=lambda x: x["key"],
            ),
        }
        version = hashlib.sha256(
            json.dumps(hash_input, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]

        bundle = {
            "playerKb":   player_kb,
            "opponentKb": opponent_kb,
            "heuristics": matched_heuristics,
        }
        return {"hit": True, "bundle": bundle, "text": bundle_text, "version": version}

    except Exception as e:
        print(f"[KB] assemble_fact_bundle error: {e}")
        return {"hit": False, "bundle": {}, "text": "", "version": "error"}
