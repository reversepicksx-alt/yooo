"""Current-news intelligence for the independent JARVIS soccer audit.

This module deliberately runs *after* the Reverse Picks prediction has been
computed.  Its output is provenance-labelled, bounded, and shadow-only: no
finding is allowed to change RP projection math, probabilities, recommendation,
saved picks, or calibration inputs.

The runtime search adapter uses the public Google News RSS feed so deployed
audits do not depend on agent-only tools or a new paid credential.  Structured
fixture lineups and injuries are accepted as independent evidence from the
existing API-Football integration.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
import aiohttp
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup


UNKNOWN = "UNKNOWN"
NEWS_SCHEMA_VERSION = "jarvis-news-intelligence.v1"
_SEARCH_URL = "https://news.google.com/rss/search"
_USER_AGENT = "ReversePicks-NewsIntelligence/1.0 (+https://reversepicks.com)"
_MAX_SEARCH_RESULTS = 10
_MAX_ARTICLE_FETCHES = 8
_MAX_ARTICLE_BYTES = 1_000_000
_MAX_REDIRECTS = 4
_registry_index_attempted = False

_TIER_LABELS = {
    1: "official_club_or_competition",
    2: "direct_manager_player_quote_or_verified_beat",
    3: "reputable_local_media",
    4: "established_national_football_media",
    5: "specialist_reporter_or_outlet",
    6: "generic_preview_or_aggregator",
}
_TIER_BASE_CONFIDENCE = {1: 0.94, 2: 0.88, 3: 0.79, 4: 0.73, 5: 0.63, 6: 0.46}

_OFFICIAL_COMPETITION_DOMAINS: dict[int, set[str]] = {
    1: {"fifa.com"},
    2: {"uefa.com"},
    3: {"uefa.com"},
    39: {"premierleague.com"},
    40: {"efl.com"},
    61: {"ligue1.com"},
    71: {"cbf.com.br"},
    78: {"bundesliga.com"},
    88: {"eredivisie.nl"},
    94: {"ligaportugal.pt"},
    135: {"legaseriea.it"},
    140: {"laliga.com"},
    253: {"mlssoccer.com"},
    254: {"nwsl.com"},
    262: {"ligamx.net"},
}
_NATIONAL_DOMAINS = {
    "bbc.com",
    "bbc.co.uk",
    "skysports.com",
    "espn.com",
    "theguardian.com",
    "reuters.com",
    "apnews.com",
    "cbssports.com",
    "foxsports.com",
    "nbcsports.com",
    "nytimes.com",
    "theathletic.com",
    "si.com",
}
_SPECIALIST_DOMAINS = {
    "goal.com",
    "transfermarkt.com",
    "football-italia.net",
    "getfootballnewsfrance.com",
    "espnfc.com",
    "worldsoccertalk.com",
    "mlssoccer.com",
    "equalizersoccer.com",
}
_AGGREGATOR_MARKERS = {
    "sportsmole",
    "forebet",
    "predictz",
    "whoscored",
    "sofascore",
    "futbol24",
    "betting",
    "oddschecker",
    "tips.gg",
    "365scores",
    "flashscore",
}
_LOCAL_MEDIA_MARKERS = {
    "chronicle",
    "gazette",
    "herald",
    "tribune",
    "courier",
    "journal",
    "times",
    "post",
    "observer",
    "standard",
    "evening news",
    "local",
}
_DIRECT_QUOTE_PATTERNS = (
    r"\b(manager|coach|head coach)\s+(said|confirmed|revealed|told)\b",
    r"\b(said|confirmed|revealed|told reporters|told the media)\b",
    r"[“”\"'][^“”\"']{12,160}[“”\"']",
)
_FACT_PATTERNS = (
    r"\bconfirmed\b",
    r"\bruled out\b",
    r"\bwill miss\b",
    r"\bsuspended\b",
    r"\bhas returned\b",
    r"\breturned to training\b",
    r"\btravel(?:led|ing) squad\b",
    r"\bstarting xi\b",
    r"\bofficial lineup\b",
)
_SPECULATION_PATTERNS = (
    r"\bcould\b",
    r"\bmay\b",
    r"\bmight\b",
    r"\blikely\b",
    r"\bexpected\b",
    r"\bpredicted\b",
    r"\bpossible\b",
    r"\brumou?r\b",
    r"\breportedly\b",
    r"\bdoubtful\b",
)
_TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "expected_lineup": ("expected xi", "predicted xi", "predicted lineup", "starting xi", "lineup"),
    "formation": ("formation", "shape", "system", "back three", "back four"),
    "training_status": ("training", "trained", "fitness test", "return to training"),
    "injury": ("injury", "injured", "ruled out", "fitness", "knock", "illness"),
    "suspension": ("suspended", "suspension", "ban", "red card"),
    "return": (
        "returns from injury",
        "return from injury",
        "returns to action",
        "back available",
        "back in training",
        "recovered",
        "fit again",
    ),
    "rotation": ("rotation", "rotate", "rested", "rest players", "changes to the side"),
    "travel_squad": ("travel squad", "travelling squad", "traveling squad", "squad list"),
    "manager_comments": ("manager said", "coach said", "press conference", "told reporters"),
    "role_change": ("role change", "new role", "deeper role", "advanced role", "position change"),
    "tactical_change": ("tactical change", "change of shape", "new system", "switch formation", "pressing"),
    "workload": ("workload", "minutes restriction", "managed minutes", "fixture congestion", "fatigue"),
    "transfer": (
        "transfer for",
        "transfer to",
        "transfer from",
        "transfer window",
        "signed",
        "signing",
        "loan",
        "departed",
        "sold",
        "joined",
    ),
    "teammate_availability": ("absence", "unavailable", "returns", "ruled out", "suspended"),
}
_NEGATIVE_AVAILABILITY = (
    "ruled out",
    "will miss",
    "set to miss",
    "suspended",
    "not travelled",
    "not traveled",
    "unavailable",
    "out injured",
    "remains out",
)
_POSITIVE_AVAILABILITY = (
    "available",
    "fit",
    "returns",
    "returned",
    "back in training",
    "expected to start",
    "starting xi",
    "will start",
)

SearchFn = Callable[[str], Awaitable[list[dict[str, Any]]]]
FetchFn = Callable[[str], Awaitable[dict[str, Any]]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _domain(url: Any) -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _domain_matches(domain: str, candidates: set[str]) -> bool:
    return any(domain == item or domain.endswith(f".{item}") for item in candidates)


def _safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            return False
        if (
            host in {"localhost", "127.0.0.1", "::1", "metadata.google.internal"}
            or host.endswith((".local", ".internal", ".localhost", ".home"))
        ):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        )
    except (TypeError, ValueError):
        return False


async def _validate_public_url(url: str) -> bool:
    """Resolve the host and reject any private/reserved address before fetch."""
    return bool(await _resolve_public_addresses(url))


async def _resolve_public_addresses(url: str) -> list[tuple[str, int]]:
    """Return validated public addresses for a URL, preserving address family."""
    if not _safe_public_url(url):
        return []
    parsed = urlparse(url)
    host = parsed.hostname
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        return []
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return []
    if port not in {80, 443}:
        return []
    try:
        literal = ipaddress.ip_address(host)
        if not literal.is_global:
            return []
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        return [(str(literal), family)]
    except ValueError:
        pass

    try:
        addresses = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                0,
                socket.SOCK_STREAM,
            ),
            timeout=1.5,
        )
    except Exception:
        return []
    resolved = {
        (item[4][0], item[0])
        for item in addresses
        if len(item) >= 5 and item[4]
    }
    if not resolved:
        return []
    validated: list[tuple[str, int]] = []
    for raw_address, family in resolved:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            return []
        if not address.is_global:
            return []
        validated.append((str(address), family))
    return validated


class _PinnedResolver(AbstractResolver):
    """Force aiohttp to connect to the exact addresses validated above."""

    def __init__(self, hostname: str, addresses: list[tuple[str, int]]):
        self._hostname = hostname.lower().rstrip(".")
        self._addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        if host.lower().rstrip(".") != self._hostname:
            return []
        selected = [
            (address, address_family)
            for address, address_family in self._addresses
            if family in {socket.AF_UNSPEC, 0, address_family}
        ] or self._addresses
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": address_family,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for address, address_family in selected
        ]

    async def close(self) -> None:
        return None


def _normalized_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    tokens = [token for token in text.split() if token not in {"fc", "sc", "afc", "cf", "club"}]
    return " ".join(tokens)


def _text_contains_name(text: str, name: str) -> bool:
    normalized = _normalized_name(name)
    if not normalized:
        return False
    haystack = _normalized_name(text)
    return normalized in haystack


def _entity_aliases(name: Any) -> set[str]:
    normalized = _normalized_name(name)
    tokens = normalized.split()
    if not tokens:
        return set()
    aliases = {normalized}
    if len(tokens) >= 3:
        acronym = "".join(token[0] for token in tokens)
        if len(acronym) >= 3:
            aliases.add(acronym)
        aliases.add(f"{tokens[0]} {tokens[1]} {''.join(token[0] for token in tokens[2:])}")
        aliases.add(f"{tokens[0][0]}{tokens[1][0]} {' '.join(tokens[2:])}")
    return {alias.strip() for alias in aliases if alias.strip()}


def _text_matches_entity(text: Any, entity_name: Any) -> bool:
    haystack = _normalized_name(text)
    words = set(haystack.split())
    for alias in _entity_aliases(entity_name):
        if " " in alias and alias in haystack:
            return True
        if " " not in alias and alias in words:
            return True
    return False


def _record_relevant(record: dict[str, Any], context: dict[str, Any]) -> bool:
    searchable = " ".join(
        str(record.get(field) or "")
        for field in ("title", "snippet", "content", "source_name")
    )
    side = str(record.get("entity_side") or "")
    if side == "target":
        return (
            _text_matches_entity(searchable, context.get("team_name"))
            or _text_contains_name(searchable, str(context.get("player_name") or ""))
        )
    if side == "opponent":
        return _text_matches_entity(searchable, context.get("opponent_name"))
    if side == "competition":
        return (
            _text_matches_entity(searchable, context.get("league_name"))
            or (
                _text_matches_entity(searchable, context.get("team_name"))
                and _text_matches_entity(searchable, context.get("opponent_name"))
            )
        )
    return False


def _entity_key(kind: str, entity_id: Any) -> str:
    return f"{kind}:{entity_id or 'unknown'}"


def _entities(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "club",
            "id": context.get("team_id"),
            "name": context.get("team_name") or "Target club",
            "side": "target",
        },
        {
            "kind": "club",
            "id": context.get("opponent_id"),
            "name": context.get("opponent_name") or "Opponent club",
            "side": "opponent",
        },
        {
            "kind": "competition",
            "id": context.get("league_id"),
            "name": context.get("league_name") or f"Competition {context.get('league_id') or ''}".strip(),
            "side": "competition",
        },
    ]


def unknown_news_intelligence(reason: str) -> dict[str, Any]:
    """Return the mandatory response shape without inventing evidence."""
    return {
        "schema_version": NEWS_SCHEMA_VERSION,
        "status": "unavailable",
        "generated_at": _iso(),
        "source": "dynamic_news_research_and_confirmed_lineups",
        "projection_influence": "shadow_only",
        "math_unchanged": True,
        "reason": reason,
        "expected_lineup": UNKNOWN,
        "target_start_probability": UNKNOWN,
        "minutes_risk": UNKNOWN,
        "expected_role": UNKNOWN,
        "formation": UNKNOWN,
        "important_teammate_changes": UNKNOWN,
        "lineup_confidence": UNKNOWN,
        "regime_changes": UNKNOWN,
        "news_warnings": [
            {
                "code": "NEWS_EVIDENCE_UNAVAILABLE",
                "severity": "medium",
                "message": reason,
            }
        ],
        "news_brief": "Current team news could not be verified; lineup, role, and availability remain UNKNOWN.",
        "findings": [],
        "contradictions": [],
        "source_registry": {
            "target_club": [],
            "opponent_club": [],
            "competition": [],
        },
        "confirmed_lineup_comparison": {
            "status": UNKNOWN,
            "material_difference": UNKNOWN,
            "rerun_required": False,
            "flag_prediction": False,
            "reasons": [],
        },
    }


def _official_domains(context: dict[str, Any]) -> set[str]:
    result = set(_OFFICIAL_COMPETITION_DOMAINS.get(int(context.get("league_id") or 0), set()))
    for key in ("team_official_domain", "opponent_official_domain", "competition_official_domain"):
        domain = _domain(context.get(key)) or str(context.get(key) or "").lower().strip()
        if domain:
            result.add(domain)
    return result


def _source_tier(
    record: dict[str, Any],
    *,
    context: dict[str, Any],
    registry_by_domain: dict[str, dict[str, Any]],
    text: str,
) -> tuple[int, str]:
    domain = record.get("domain") or ""
    registered = registry_by_domain.get(domain) or {}
    try:
        registered_rank = int(registered.get("source_tier_rank") or registered.get("tier_rank"))
    except (TypeError, ValueError):
        registered_rank = 0
    if registered_rank in _TIER_LABELS:
        return registered_rank, str(registered.get("source_tier") or _TIER_LABELS[registered_rank])

    source_name = str(record.get("source_name") or "")
    entity_name = str(record.get("entity_name") or "")
    official_domains = _official_domains(context)
    normalized_source = _normalized_name(source_name)
    normalized_entity = _normalized_name(entity_name)
    looks_official = (
        _domain_matches(domain, official_domains)
        or (
            normalized_source
            and normalized_entity
            and (
                normalized_source == normalized_entity
                or normalized_source == f"{normalized_entity} official"
            )
        )
    )
    if looks_official:
        return 1, _TIER_LABELS[1]

    lowered = f"{domain} {source_name}".lower()
    if any(marker in lowered for marker in _AGGREGATOR_MARKERS):
        return 6, _TIER_LABELS[6]
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _DIRECT_QUOTE_PATTERNS):
        return 2, _TIER_LABELS[2]
    if _domain_matches(domain, _NATIONAL_DOMAINS):
        return 4, _TIER_LABELS[4]
    if any(marker in lowered for marker in _LOCAL_MEDIA_MARKERS):
        return 3, _TIER_LABELS[3]
    if _domain_matches(domain, _SPECIALIST_DOMAINS):
        return 5, _TIER_LABELS[5]
    return 5, _TIER_LABELS[5]


def _classification(text: str, tier_rank: int) -> str:
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _FACT_PATTERNS):
        return "fact"
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _SPECULATION_PATTERNS):
        return "speculation"
    if tier_rank == 1:
        return "fact"
    return "analysis"


def _freshness_multiplier(published_at: Any, now: datetime) -> float:
    published = _parse_datetime(published_at)
    if not published:
        return 0.78
    age_days = max(0.0, (now - published).total_seconds() / 86400.0)
    if age_days <= 1:
        return 1.0
    if age_days <= 3:
        return 0.97
    if age_days <= 7:
        return 0.92
    if age_days <= 14:
        return 0.82
    if age_days <= 30:
        return 0.65
    return 0.45


def _published_sort_value(value: Any) -> float:
    parsed = _parse_datetime(value)
    return parsed.timestamp() if parsed else float("-inf")


def _evidence_weights(
    *,
    text: str,
    tier_rank: int,
    published_at: Any,
    now: datetime,
    context: dict[str, Any],
    subject: str,
) -> dict[str, float]:
    if subject == "target_player":
        relevance = 1.0
    elif _text_contains_name(text, str(context.get("team_name") or "")) or _text_contains_name(
        text,
        str(context.get("opponent_name") or ""),
    ):
        relevance = 0.9
    elif _text_contains_name(text, str(context.get("league_name") or "")):
        relevance = 0.8
    else:
        relevance = 0.65
    return {
        "relevance": round(relevance, 2),
        "freshness": round(_freshness_multiplier(published_at, now), 2),
        "reliability": round(_TIER_BASE_CONFIDENCE[tier_rank], 2),
    }


def _confidence(
    tier_rank: int,
    classification: str,
    published_at: Any,
    now: datetime,
    *,
    full_text_fetched: bool,
) -> float:
    score = _TIER_BASE_CONFIDENCE[tier_rank] * _freshness_multiplier(published_at, now)
    if classification == "fact":
        score += 0.03
    elif classification == "speculation":
        score -= 0.11
    if full_text_fetched:
        score += 0.025
    if tier_rank == 6:
        score = min(score, 0.55)
    return round(max(0.20, min(0.99, score)), 2)


def _topics(text: str) -> list[str]:
    lowered = text.lower()
    matches = [
        topic
        for topic, markers in _TOPIC_PATTERNS.items()
        if any(marker in lowered for marker in markers)
    ]
    # A current result without one of the required concepts is not evidence.
    return matches[:4]


def _topic_is_current(topic: str, published_at: Any, now: datetime) -> bool:
    published = _parse_datetime(published_at)
    if not published:
        # Web articles without a parseable publication time cannot satisfy a
        # current-news contract. They may still inform source discovery, but
        # never lineup/availability conclusions or start probability.
        return False
    age_days = max(0.0, (now - published).total_seconds() / 86400.0)
    max_age_days = 45 if topic in {"transfer", "role_change", "tactical_change"} else 21
    return age_days <= max_age_days


def _finding_statement(text: str, title: str, topic: str) -> str:
    cleaned = re.sub(r"\s+", " ", BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True))
    markers = _TOPIC_PATTERNS.get(topic, ())
    for sentence in re.split(r"(?<=[.!?])\s+", cleaned):
        if 25 <= len(sentence) <= 360 and any(marker in sentence.lower() for marker in markers):
            return sentence
    return re.sub(r"\s+", " ", title).strip()[:360]


def _assertion(text: str, topic: str, context: dict[str, Any]) -> tuple[str, str]:
    lowered = text.lower()
    player_name = str(context.get("player_name") or "")
    player_subject = _text_contains_name(text, player_name)
    subject = "target_player" if player_subject else str(context.get("_evidence_side") or "match")

    if player_subject:
        if any(marker in lowered for marker in _NEGATIVE_AVAILABILITY):
            return subject, "unavailable"
        if "bench" in lowered or "substitute" in lowered:
            return subject, "bench"
        if "expected to start" in lowered or "will start" in lowered or "starting xi" in lowered:
            return subject, "starts"
        if any(marker in lowered for marker in _POSITIVE_AVAILABILITY):
            return subject, "available"
    if topic == "formation":
        formation = re.search(r"\b([3-5]-[1-5](?:-[1-5]){1,3})\b", text)
        if formation:
            return f"{subject}_formation", formation.group(1)
    return subject, topic


def _build_web_findings(
    records: list[dict[str, Any]],
    *,
    context: dict[str, Any],
    registry_by_domain: dict[str, dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for record in records:
        if not _record_relevant(record, context):
            continue
        title = str(record.get("title") or "").strip()
        snippet = str(record.get("snippet") or "").strip()
        content = str(record.get("content") or "").strip()
        evidence_text = " ".join(part for part in (title, snippet, content[:7000]) if part)
        if not evidence_text:
            continue
        evidence_context = {**context, "_evidence_side": record.get("entity_side") or "match"}
        tier_rank, tier_label = _source_tier(
            record,
            context=context,
            registry_by_domain=registry_by_domain,
            text=evidence_text,
        )
        classification = _classification(evidence_text, tier_rank)
        topics = _topics(evidence_text)
        for topic in topics:
            if not _topic_is_current(topic, record.get("published_at"), now):
                continue
            subject, assertion = _assertion(evidence_text, topic, evidence_context)
            weights = _evidence_weights(
                text=evidence_text,
                tier_rank=tier_rank,
                published_at=record.get("published_at"),
                now=now,
                context=context,
                subject=subject,
            )
            findings.append({
                "topic": topic,
                "subject": subject,
                "assertion": assertion,
                "statement": _finding_statement(evidence_text, title, topic),
                "source": {
                    "name": record.get("source_name") or record.get("domain") or UNKNOWN,
                    "url": record.get("url") or UNKNOWN,
                    "domain": record.get("domain") or UNKNOWN,
                },
                "timestamp": {
                    "published_at": record.get("published_at") or UNKNOWN,
                    "retrieved_at": record.get("retrieved_at") or _iso(now),
                },
                "source_tier": {
                    "rank": tier_rank,
                    "label": tier_label,
                },
                "classification": classification,
                "evidence_weights": weights,
                "confidence": _confidence(
                    tier_rank,
                    classification,
                    record.get("published_at"),
                    now,
                    full_text_fetched=bool(content),
                ),
                "entity": {
                    "type": record.get("entity_type"),
                    "id": record.get("entity_id"),
                    "name": record.get("entity_name"),
                    "side": record.get("entity_side"),
                },
            })
    # Avoid returning four near-identical topic matches from the same headline.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for finding in findings:
        key = (
            str((finding.get("source") or {}).get("url")),
            str(finding.get("topic")),
            str(finding.get("assertion")),
        )
        if key not in unique or finding["confidence"] > unique[key]["confidence"]:
            unique[key] = finding
    return sorted(
        unique.values(),
        key=lambda row: (
            int((row.get("source_tier") or {}).get("rank") or 9),
            -_published_sort_value((row.get("timestamp") or {}).get("published_at")),
            -float(row.get("confidence") or 0),
        ),
    )[:40]


def _structured_finding(
    *,
    topic: str,
    subject: str,
    assertion: str,
    statement: str,
    retrieved_at: str,
    entity: dict[str, Any],
    confidence: float = 0.96,
    tier_rank: int = 2,
    tier_label: str = "confirmed_match_data",
    source_name: str = "API-Football confirmed fixture data",
) -> dict[str, Any]:
    return {
        "topic": topic,
        "subject": subject,
        "assertion": assertion,
        "statement": statement,
        "source": {
            "name": source_name,
            "url": "https://www.api-football.com/",
            "domain": "api-football.com",
        },
        "timestamp": {
            "published_at": UNKNOWN,
            "observed_at": retrieved_at,
            "retrieved_at": retrieved_at,
        },
        "source_tier": {
            "rank": tier_rank,
            "label": tier_label,
        },
        "classification": "fact",
        "evidence_weights": {
            "relevance": 1.0,
            "freshness": 1.0,
            "reliability": confidence,
        },
        "confidence": confidence,
        "entity": entity,
    }


def _response_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("response", [])
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return []


def _confirmed_lineup_evidence(
    context: dict[str, Any],
    lineups_payload: Any,
    *,
    retrieved_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _response_rows(lineups_payload)
    result = {
        "available": False,
        "target_lineup_complete": False,
        "opponent_lineup_complete": False,
        "target_team": None,
        "opponent_team": None,
        "target_status": UNKNOWN,
        "target_role": UNKNOWN,
    }
    findings: list[dict[str, Any]] = []
    player_id = context.get("player_id")
    team_id = context.get("team_id")
    opponent_id = context.get("opponent_id")

    for row in rows:
        team = row.get("team") or {}
        current_team_id = team.get("id")
        side = "target" if current_team_id == team_id else "opponent" if current_team_id == opponent_id else "other"
        if side == "other":
            continue
        formation = row.get("formation") or UNKNOWN
        starters = []
        substitutes = []
        target_status = None
        target_role = None
        for item in row.get("startXI") or []:
            player = item.get("player") or {}
            starters.append({
                "id": player.get("id"),
                "name": player.get("name") or UNKNOWN,
                "position": player.get("pos") or UNKNOWN,
                "grid": player.get("grid") or UNKNOWN,
            })
            if player.get("id") == player_id:
                target_status = "STARTER"
                target_role = {
                    "position": player.get("pos") or UNKNOWN,
                    "grid": player.get("grid") or UNKNOWN,
                }
        for item in row.get("substitutes") or []:
            player = item.get("player") or {}
            substitutes.append({
                "id": player.get("id"),
                "name": player.get("name") or UNKNOWN,
                "position": player.get("pos") or UNKNOWN,
            })
            if player.get("id") == player_id:
                target_status = "SUBSTITUTE"
                target_role = {
                    "position": player.get("pos") or UNKNOWN,
                    "grid": UNKNOWN,
                }

        block = {
            "team_id": current_team_id,
            "team_name": team.get("name") or UNKNOWN,
            "formation": formation,
            "starting_xi": starters,
            "substitutes": substitutes,
            "coach": (row.get("coach") or {}).get("name") or UNKNOWN,
        }
        result[f"{side}_team"] = block
        lineup_complete = len(starters) == 11
        result[f"{side}_lineup_complete"] = lineup_complete
        target_directly_observed = target_status in {"STARTER", "SUBSTITUTE"}
        if side == "target" and (lineup_complete or target_directly_observed):
            result["available"] = True
        entity = {
            "type": "club",
            "id": current_team_id,
            "name": team.get("name"),
            "side": side,
        }
        if lineup_complete:
            findings.append(_structured_finding(
                topic="expected_lineup",
                subject=f"{side}_lineup",
                assertion="confirmed",
                statement=f"Confirmed starting XI is available for {team.get('name') or side}.",
                retrieved_at=retrieved_at,
                entity=entity,
                confidence=0.99,
                tier_rank=1,
            ))
        if formation != UNKNOWN and lineup_complete:
            findings.append(_structured_finding(
                topic="formation",
                subject=f"{side}_formation",
                assertion=str(formation),
                statement=f"{team.get('name') or side} is confirmed in a {formation} formation.",
                retrieved_at=retrieved_at,
                entity=entity,
                confidence=0.99,
                tier_rank=1,
            ))
        if side == "target":
            # Positive presence in the XI or substitutes is trustworthy even
            # if the provider row is partial. Absence is not: API-Football can
            # return truncated substitute arrays, so never invent "not in
            # squad" from omission alone.
            result["target_status"] = target_status or UNKNOWN
            result["target_role"] = target_role or UNKNOWN
            if target_directly_observed:
                target_assertion = (
                    "starts"
                    if target_status == "STARTER"
                    else "bench"
                )
                findings.append(_structured_finding(
                    topic="expected_lineup",
                    subject="target_player",
                    assertion=target_assertion,
                    statement=(
                        f"{context.get('player_name') or 'Target player'} is confirmed as "
                        f"{target_status.lower()}."
                    ),
                    retrieved_at=retrieved_at,
                    entity=entity,
                    confidence=0.99,
                    tier_rank=1,
                ))
    return result, findings


def _injury_findings(
    context: dict[str, Any],
    injuries_payload: Any,
    *,
    retrieved_at: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[Any, str, str]] = set()
    for row in _response_rows(injuries_payload):
        team = row.get("team") or {}
        if team.get("id") not in {context.get("team_id"), context.get("opponent_id")}:
            continue
        player = row.get("player") or {}
        player_name = player.get("name") or UNKNOWN
        reason = player.get("reason") or player.get("type") or "availability issue"
        identity = (
            team.get("id"),
            str(player.get("id") or _normalized_name(player_name)),
            _normalized_name(reason),
        )
        if identity in seen:
            continue
        seen.add(identity)
        is_target = player.get("id") == context.get("player_id") or _normalized_name(player_name) == _normalized_name(context.get("player_name"))
        side = "target" if team.get("id") == context.get("team_id") else "opponent"
        findings.append(_structured_finding(
            topic="injury",
            subject="target_player" if is_target else f"{side}_teammate",
            assertion="unavailable",
            statement=f"{player_name} is listed with {reason}.",
            retrieved_at=retrieved_at,
            entity={
                "type": "club",
                "id": team.get("id"),
                "name": team.get("name"),
                "side": side,
            },
            confidence=0.88,
            tier_rank=3,
            tier_label="structured_injury_provider",
            source_name="API-Football injury feed",
        ))
    return findings


def _contradictory(left: str, right: str) -> bool:
    pairs = {
        ("available", "unavailable"),
        ("starts", "unavailable"),
        ("starts", "bench"),
        ("bench", "unavailable"),
    }
    return (left, right) in pairs or (right, left) in pairs


def _resolve_contradictions(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        by_subject.setdefault(str(finding.get("subject") or "match"), []).append(finding)

    contradictions: list[dict[str, Any]] = []
    resolutions: dict[str, dict[str, Any]] = {}
    for subject, rows in by_subject.items():
        ranked = sorted(
            rows,
            key=lambda row: (
                int((row.get("source_tier") or {}).get("rank") or 9),
                -int(
                    (row.get("source_tier") or {}).get("label")
                    == "confirmed_match_data"
                ),
                -_published_sort_value((row.get("timestamp") or {}).get("published_at")),
                -float(row.get("confidence") or 0),
            ),
        )
        if ranked:
            winner = ranked[0]
            # Generic previews cannot become a decisive interpretation without
            # corroboration from a separate source.
            winner_domain = (winner.get("source") or {}).get("domain")
            corroborated = any(
                row.get("assertion") == winner.get("assertion")
                and (row.get("source") or {}).get("domain") != winner_domain
                for row in ranked[1:]
            )
            if int((winner.get("source_tier") or {}).get("rank") or 9) == 6 and not corroborated:
                resolutions[subject] = {
                    "status": "unresolved",
                    "assertion": UNKNOWN,
                    "reason": "A generic preview or aggregator was not independently corroborated.",
                }
            else:
                resolutions[subject] = {
                    "status": "resolved",
                    "assertion": winner.get("assertion"),
                    "winning_finding": winner,
                    "reason": "Selected by source tier, freshness, and evidence confidence.",
                }
        assertions = {str(row.get("assertion")) for row in rows}
        has_conflict = any(_contradictory(a, b) for a in assertions for b in assertions if a != b)
        formation_conflict = subject.endswith("_formation") and len(assertions - {"formation"}) > 1
        if has_conflict or formation_conflict:
            winner = (resolutions.get(subject) or {}).get("winning_finding")
            contradictions.append({
                "subject": subject,
                "status": (resolutions.get(subject) or {}).get("status", "unresolved"),
                "winning_assertion": (resolutions.get(subject) or {}).get("assertion", UNKNOWN),
                "winning_finding": winner,
                "discarded_findings": [row for row in rows if row is not winner],
                "resolution_rule": "source_quality_then_freshness_then_confidence",
            })
    return contradictions, resolutions


def _extract_expected_xi(findings: list[dict[str, Any]], side: str) -> tuple[list[str] | None, dict[str, Any] | None]:
    candidates = [
        finding
        for finding in findings
        if finding.get("topic") == "expected_lineup"
        and (finding.get("entity") or {}).get("side") == side
        and int((finding.get("source_tier") or {}).get("rank") or 9) <= 5
    ]
    for finding in candidates:
        statement = str(finding.get("statement") or "")
        match = re.search(
            r"(?:predicted|expected|probable)\s+(?:starting\s+)?(?:xi|lineup)\s*[:\-]\s*(.{40,520})",
            statement,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        names = [
            re.sub(r"\s*\([^)]*\)\s*", "", item).strip(" .")
            for item in re.split(r"[,;|]", match.group(1))
        ]
        names = [name for name in names if 2 <= len(name.split()) <= 5]
        if len(names) >= 7:
            return names[:11], finding
    return None, None


def _target_probability(
    confirmed: dict[str, Any],
    resolution: dict[str, Any] | None,
) -> float | str:
    status = confirmed.get("target_status")
    if status == "STARTER":
        return 1.0
    if status in {"SUBSTITUTE", "NOT_IN_MATCHDAY_SQUAD"}:
        return 0.0
    assertion = (resolution or {}).get("assertion")
    if assertion == "starts":
        return 0.82
    if assertion == "available":
        return 0.68
    if assertion == "bench":
        return 0.18
    if assertion == "unavailable":
        return 0.0
    return UNKNOWN


def _minutes_risk(
    confirmed: dict[str, Any],
    target_resolution: dict[str, Any] | None,
    findings: list[dict[str, Any]],
) -> str:
    if confirmed.get("target_status") == "STARTER":
        risky = any(
            finding.get("subject") == "target_player"
            and finding.get("topic") in {"workload", "injury", "return", "rotation"}
            and float(finding.get("confidence") or 0) >= 0.6
            for finding in findings
        )
        return "MEDIUM" if risky else "LOW"
    if confirmed.get("target_status") in {"SUBSTITUTE", "NOT_IN_MATCHDAY_SQUAD"}:
        return "HIGH"
    assertion = (target_resolution or {}).get("assertion")
    if assertion in {"bench", "unavailable"}:
        return "HIGH"
    if any(
        finding.get("subject") == "target_player"
        and finding.get("topic") in {"workload", "injury", "return", "rotation"}
        for finding in findings
    ):
        return "MEDIUM"
    return UNKNOWN


def _best_formation(
    side: str,
    confirmed: dict[str, Any],
    resolutions: dict[str, dict[str, Any]],
) -> str:
    block = confirmed.get(f"{side}_team")
    if isinstance(block, dict) and block.get("formation") not in {None, UNKNOWN}:
        return str(block["formation"])
    resolved = resolutions.get(f"{side}_formation") or {}
    assertion = resolved.get("assertion")
    return str(assertion) if assertion and assertion != UNKNOWN else UNKNOWN


def _broad_role(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("position") or value.get("role") or value.get("grid")
    text = str(value or "").strip().lower()
    if not text or text == UNKNOWN.lower():
        return UNKNOWN
    if text in {"g", "gk", "goalkeeper", "keeper"}:
        return "GK"
    if text in {"d", "def", "defender"} or any(
        marker in text
        for marker in ("back", "defender", "centre-back", "center-back", "cb", "lwb", "rwb")
    ):
        return "D"
    if text in {"m", "mid", "midfielder"} or any(
        marker in text
        for marker in ("midfield", "number 6", "number 8", "dm", "cm", "am")
    ):
        return "M"
    if text in {"f", "fw", "forward"} or any(
        marker in text
        for marker in ("forward", "striker", "winger", "number 9", "st", "lw", "rw")
    ):
        return "F"
    return UNKNOWN


def _prediction_target_assumption(prediction: dict[str, Any]) -> dict[str, Any]:
    tactical = prediction.get("tacticalContext") if isinstance(prediction.get("tacticalContext"), dict) else {}
    status_value = (
        prediction.get("targetLineupStatus")
        or prediction.get("playerLineupStatus")
        or prediction.get("starterStatus")
        or tactical.get("targetLineupStatus")
    )
    is_starter = (
        prediction.get("isStarter")
        if isinstance(prediction.get("isStarter"), bool)
        else tactical.get("isStarter")
        if isinstance(tactical.get("isStarter"), bool)
        else None
    )
    status_text = str(status_value or "").strip().lower()
    if is_starter is True or status_text in {"starter", "starting", "starts", "start"}:
        target_status = "starts"
    elif is_starter is False or status_text in {"bench", "substitute", "sub"}:
        target_status = "bench"
    elif status_text in {"out", "unavailable", "not_in_squad", "not in squad"}:
        target_status = "unavailable"
    elif (
        str(tactical.get("lineupStatus") or prediction.get("lineupStatus") or "").lower() == "confirmed"
        and tactical.get("targetLineupPosition")
    ):
        target_status = "starts"
    else:
        target_status = UNKNOWN

    explicit_probability = (
        prediction.get("targetStartProbability")
        if prediction.get("targetStartProbability") is not None
        else tactical.get("targetStartProbability")
    )
    try:
        probability: float | str = float(explicit_probability)
        if probability > 1:
            probability /= 100.0
        probability = round(max(0.0, min(1.0, probability)), 2)
    except (TypeError, ValueError):
        probability = UNKNOWN
    return {
        "lineup_status": (
            prediction.get("lineupStatus")
            or tactical.get("lineupStatus")
            or UNKNOWN
        ),
        "target_status": target_status,
        "target_start_probability": probability,
        "expected_role": (
            prediction.get("exactTacticalRole")
            or prediction.get("tacticalRole")
            or tactical.get("role")
            or prediction.get("playerPosition")
            or tactical.get("position")
            or UNKNOWN
        ),
    }


def _pre_match_baseline(
    *,
    prediction: dict[str, Any],
    target_resolution: dict[str, Any] | None,
    expected_team_xi: list[str] | None,
    expected_opponent_xi: list[str] | None,
    expected_target_formation: str,
    expected_opponent_formation: str,
) -> dict[str, Any]:
    baseline = _prediction_target_assumption(prediction)
    news_assertion = (target_resolution or {}).get("assertion")
    if news_assertion and news_assertion != UNKNOWN:
        baseline["target_status"] = news_assertion
        baseline["target_start_probability"] = _target_probability({}, target_resolution)
        baseline["availability_source"] = (target_resolution or {}).get("winning_finding")
    elif baseline["target_start_probability"] == UNKNOWN:
        synthetic_resolution = (
            {"assertion": baseline["target_status"]}
            if baseline["target_status"] != UNKNOWN
            else None
        )
        baseline["target_start_probability"] = _target_probability({}, synthetic_resolution)
    baseline["expected_xi"] = expected_team_xi or UNKNOWN
    baseline["expected_opponent_xi"] = expected_opponent_xi or UNKNOWN
    baseline["expected_formation"] = expected_target_formation
    baseline["expected_opponent_formation"] = expected_opponent_formation
    return baseline


def _lineup_comparison(
    context: dict[str, Any],
    confirmed: dict[str, Any],
    pre_match_baseline: dict[str, Any],
) -> dict[str, Any]:
    if not confirmed.get("available"):
        return {
            "status": "PENDING_CONFIRMED_LINEUPS",
            "material_difference": UNKNOWN,
            "rerun_required": False,
            "flag_prediction": False,
            "reasons": [],
            "pre_match_assumption": pre_match_baseline,
            "confirmed": {
                "target_status": UNKNOWN,
                "formation": UNKNOWN,
                "opponent_formation": UNKNOWN,
                "role": UNKNOWN,
            },
            "action": "NO_ACTION",
        }

    reasons: list[str] = []
    target_status = confirmed.get("target_status")
    if target_status in {"SUBSTITUTE", "NOT_IN_MATCHDAY_SQUAD"}:
        reasons.append(
            f"Confirmed lineup lists {context.get('player_name') or 'the target player'} as "
            f"{target_status.lower().replace('_', ' ')}."
        )
    elif (
        target_status == "STARTER"
        and pre_match_baseline.get("target_status") in {"bench", "unavailable"}
    ):
        reasons.append(
            f"Confirmed lineup lists {context.get('player_name') or 'the target player'} as a starter, "
            f"but the pre-match assumption was {pre_match_baseline.get('target_status')}."
        )

    confirmed_team = confirmed.get("target_team") if isinstance(confirmed.get("target_team"), dict) else {}
    confirmed_names = {
        _normalized_name(row.get("name"))
        for row in confirmed_team.get("starting_xi") or []
        if isinstance(row, dict)
    }
    expected_team_xi = pre_match_baseline.get("expected_xi")
    if isinstance(expected_team_xi, list) and expected_team_xi and confirmed_names:
        expected_names = {_normalized_name(name) for name in expected_team_xi}
        changed = sorted(name for name in expected_names.symmetric_difference(confirmed_names) if name)
        if len(changed) >= 4:
            reasons.append(f"Confirmed XI materially differs from the expected XI ({len(changed)} player-name changes).")

    confirmed_opponent = (
        confirmed.get("opponent_team")
        if isinstance(confirmed.get("opponent_team"), dict)
        else {}
    )
    confirmed_opponent_names = {
        _normalized_name(row.get("name"))
        for row in confirmed_opponent.get("starting_xi") or []
        if isinstance(row, dict)
    }
    expected_opponent_xi = pre_match_baseline.get("expected_opponent_xi")
    if (
        isinstance(expected_opponent_xi, list)
        and expected_opponent_xi
        and confirmed_opponent_names
    ):
        expected_names = {_normalized_name(name) for name in expected_opponent_xi}
        changed = sorted(
            name
            for name in expected_names.symmetric_difference(confirmed_opponent_names)
            if name
        )
        if len(changed) >= 4:
            reasons.append(
                "Confirmed opponent XI materially differs from the expected opponent XI "
                f"({len(changed)} player-name changes)."
            )

    confirmed_formation = confirmed_team.get("formation")
    expected_formation = pre_match_baseline.get("expected_formation")
    if (
        expected_formation not in {None, UNKNOWN}
        and confirmed_formation not in {None, UNKNOWN}
        and str(expected_formation) != str(confirmed_formation)
    ):
        reasons.append(f"Confirmed formation {confirmed_formation} differs from expected {expected_formation}.")

    confirmed_opponent_formation = confirmed_opponent.get("formation")
    expected_opponent_formation = pre_match_baseline.get("expected_opponent_formation")
    if (
        expected_opponent_formation not in {None, UNKNOWN}
        and confirmed_opponent_formation not in {None, UNKNOWN}
        and str(expected_opponent_formation) != str(confirmed_opponent_formation)
    ):
        reasons.append(
            f"Confirmed opponent formation {confirmed_opponent_formation} differs from "
            f"expected {expected_opponent_formation}."
        )

    expected_role = _broad_role(pre_match_baseline.get("expected_role"))
    confirmed_role = _broad_role(confirmed.get("target_role"))
    if expected_role != UNKNOWN and confirmed_role != UNKNOWN and expected_role != confirmed_role:
        reasons.append(f"Confirmed broad role {confirmed_role} differs from expected {expected_role}.")

    if target_status == UNKNOWN and not reasons:
        return {
            "status": "PENDING_CONFIRMED_TARGET_STATUS",
            "material_difference": UNKNOWN,
            "rerun_required": False,
            "flag_prediction": False,
            "reasons": [],
            "pre_match_assumption": pre_match_baseline,
            "confirmed": {
                "target_status": UNKNOWN,
                "formation": confirmed_formation or UNKNOWN,
                "opponent_formation": confirmed_opponent_formation or UNKNOWN,
                "role": UNKNOWN,
            },
            "action": "NO_ACTION",
        }

    material = bool(reasons)
    return {
        "status": "MATERIAL_DIFFERENCE" if material else "MATCHES_OR_NO_MATERIAL_DIFFERENCE",
        "material_difference": material,
        "rerun_required": material,
        "flag_prediction": material,
        "reasons": reasons,
        "pre_match_assumption": pre_match_baseline,
        "confirmed": {
            "target_status": target_status,
            "formation": confirmed_formation or UNKNOWN,
            "opponent_formation": confirmed_opponent_formation or UNKNOWN,
            "role": confirmed.get("target_role") or UNKNOWN,
        },
        "action": "FLAG_FOR_RERUN" if material else "NO_ACTION",
    }


def analyze_news_evidence(
    *,
    context: dict[str, Any],
    prediction: dict[str, Any],
    records: list[dict[str, Any]],
    registry_documents: list[dict[str, Any]] | None = None,
    lineups_payload: Any = None,
    injuries_payload: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the deterministic news packet from normalized research evidence."""
    now = (now or _now()).astimezone(timezone.utc)
    retrieved_at = _iso(now)
    registry_documents = registry_documents or []
    registry_by_domain = {
        str(doc.get("domain") or ""): doc
        for doc in registry_documents
        if doc.get("domain")
    }
    web_findings = _build_web_findings(
        records,
        context=context,
        registry_by_domain=registry_by_domain,
        now=now,
    )
    injury_findings = _injury_findings(context, injuries_payload, retrieved_at=retrieved_at)
    pre_match_findings = sorted(
        [*injury_findings, *web_findings],
        key=lambda row: (
            int((row.get("source_tier") or {}).get("rank") or 9),
            -_published_sort_value((row.get("timestamp") or {}).get("published_at")),
            -float(row.get("confidence") or 0),
        ),
    )
    _pre_match_contradictions, pre_match_resolutions = _resolve_contradictions(
        pre_match_findings
    )
    reported_team_xi, expected_team_source = _extract_expected_xi(
        pre_match_findings,
        "target",
    )
    reported_opponent_xi, expected_opponent_source = _extract_expected_xi(
        pre_match_findings,
        "opponent",
    )
    tactical_context = (
        prediction.get("tacticalContext")
        if isinstance(prediction.get("tacticalContext"), dict)
        else {}
    )
    reported_target_formation = str(
        (pre_match_resolutions.get("target_formation") or {}).get("assertion")
        or tactical_context.get("lineupFormation")
        or UNKNOWN
    )
    reported_opponent_formation = str(
        (pre_match_resolutions.get("opponent_formation") or {}).get("assertion")
        or tactical_context.get("opponentFormation")
        or UNKNOWN
    )
    pre_match_baseline = _pre_match_baseline(
        prediction=prediction,
        target_resolution=pre_match_resolutions.get("target_player"),
        expected_team_xi=reported_team_xi,
        expected_opponent_xi=reported_opponent_xi,
        expected_target_formation=reported_target_formation,
        expected_opponent_formation=reported_opponent_formation,
    )

    confirmed, lineup_findings = _confirmed_lineup_evidence(
        context,
        lineups_payload,
        retrieved_at=retrieved_at,
    )
    findings = sorted(
        [*lineup_findings, *injury_findings, *web_findings],
        key=lambda row: (
            int((row.get("source_tier") or {}).get("rank") or 9),
            -_published_sort_value((row.get("timestamp") or {}).get("published_at")),
            -float(row.get("confidence") or 0),
        ),
    )
    contradictions, resolutions = _resolve_contradictions(findings)
    target_resolution = resolutions.get("target_player")
    target_probability = _target_probability(confirmed, target_resolution)
    minutes_risk = _minutes_risk(confirmed, target_resolution, findings)

    expected_team_xi = list(reported_team_xi) if reported_team_xi else None
    expected_opponent_xi = (
        list(reported_opponent_xi)
        if reported_opponent_xi
        else None
    )
    if isinstance(confirmed.get("target_team"), dict):
        expected_team_xi = [
            row.get("name") or UNKNOWN
            for row in confirmed["target_team"].get("starting_xi") or []
        ]
    if isinstance(confirmed.get("opponent_team"), dict):
        expected_opponent_xi = [
            row.get("name") or UNKNOWN
            for row in confirmed["opponent_team"].get("starting_xi") or []
        ]

    target_formation = _best_formation("target", confirmed, resolutions)
    opponent_formation = _best_formation("opponent", confirmed, resolutions)
    if confirmed.get("target_lineup_complete"):
        confirmed_lineup_status = "CONFIRMED"
    elif confirmed.get("available"):
        confirmed_lineup_status = "PARTIAL_CONFIRMED"
    else:
        confirmed_lineup_status = "EXPECTED"
    expected_lineup = (
        {
            "status": confirmed_lineup_status,
            "target_team": expected_team_xi or UNKNOWN,
            "opponent_team": expected_opponent_xi or UNKNOWN,
            "source": (
                "api_football_confirmed_lineups"
                if confirmed.get("available")
                else {
                    "target": expected_team_source,
                    "opponent": expected_opponent_source,
                }
            ),
        }
        if expected_team_xi or expected_opponent_xi or confirmed.get("available")
        else UNKNOWN
    )

    target_role: Any = confirmed.get("target_role")
    if target_role is None or target_role == UNKNOWN:
        role_finding = next(
            (
                finding
                for finding in findings
                if finding.get("subject") == "target_player"
                and finding.get("topic") == "role_change"
            ),
            None,
        )
        target_role = (
            {
                "status": "REPORTED",
                "statement": role_finding.get("statement"),
                "source": role_finding.get("source"),
                "confidence": role_finding.get("confidence"),
            }
            if role_finding
            else UNKNOWN
        )

    teammate_changes = [
        {
            "statement": finding.get("statement"),
            "topic": finding.get("topic"),
            "source": finding.get("source"),
            "confidence": finding.get("confidence"),
        }
        for finding in findings
        if finding.get("subject") == "target_teammate"
        or (
            (finding.get("entity") or {}).get("side") == "target"
            and finding.get("topic") in {"injury", "suspension", "return", "transfer", "rotation"}
            and finding.get("subject") != "target_player"
        )
    ][:8]
    regime_changes = [
        {
            "statement": finding.get("statement"),
            "topic": finding.get("topic"),
            "source": finding.get("source"),
            "confidence": finding.get("confidence"),
        }
        for finding in findings
        if finding.get("topic") in {"formation", "role_change", "tactical_change", "rotation", "transfer"}
        and float(finding.get("confidence") or 0) >= 0.58
    ][:8]

    team_lineup_topics = {
        "expected_lineup",
        "formation",
        "training_status",
        "rotation",
        "travel_squad",
        "manager_comments",
        "role_change",
        "workload",
    }
    target_availability_topics = {"injury", "suspension", "return"}
    lineup_findings_for_confidence = [
        finding
        for finding in findings
        if (
            finding.get("topic") in team_lineup_topics
            or (
                finding.get("topic") in target_availability_topics
                and finding.get("subject") == "target_player"
            )
        )
    ]
    lineup_high_quality = [
        finding
        for finding in lineup_findings_for_confidence
        if int((finding.get("source_tier") or {}).get("rank") or 9) <= 4
    ]
    if confirmed.get("target_lineup_complete"):
        lineup_confidence: Any = {
            "level": "HIGH",
            "score": 0.99,
            "reason": "Confirmed fixture lineup data is available.",
        }
    elif confirmed.get("available"):
        lineup_confidence = {
            "level": "MEDIUM",
            "score": 0.90,
            "reason": "The target player was directly observed in a partial confirmed lineup payload.",
        }
    elif lineup_high_quality:
        average = sum(
            float(item.get("confidence") or 0)
            for item in lineup_high_quality
        ) / len(lineup_high_quality)
        lineup_confidence = {
            "level": "MEDIUM" if average < 0.82 else "HIGH",
            "score": round(average, 2),
            "reason": "Current high-quality sources were available, but the official XI was not confirmed.",
        }
    elif lineup_findings_for_confidence:
        lineup_confidence = {
            "level": "LOW",
            "score": round(
                max(
                    float(item.get("confidence") or 0)
                    for item in lineup_findings_for_confidence
                ),
                2,
            ),
            "reason": "Only specialist, speculative, or aggregator evidence was available.",
        }
    else:
        lineup_confidence = UNKNOWN

    comparison = _lineup_comparison(
        context,
        confirmed,
        pre_match_baseline,
    )
    warnings: list[dict[str, Any]] = []
    if not findings:
        warnings.append({
            "code": "NO_CURRENT_NEWS_EVIDENCE",
            "severity": "medium",
            "message": "No current source produced a verifiable lineup or availability finding.",
        })
    if contradictions:
        warnings.append({
            "code": "CONTRADICTORY_NEWS",
            "severity": "medium",
            "message": f"{len(contradictions)} source contradiction(s) were resolved or left UNKNOWN.",
        })
    if comparison.get("rerun_required"):
        warnings.append({
            "code": "CONFIRMED_LINEUP_MATERIAL_DRIFT",
            "severity": "high",
            "message": "Confirmed lineup materially differs from pre-match assumptions; flag this prediction for rerun.",
        })
    if target_probability == UNKNOWN:
        warnings.append({
            "code": "TARGET_START_STATUS_UNKNOWN",
            "severity": "medium",
            "message": "No sufficiently strong current evidence established the target player's start probability.",
        })

    source_registry: dict[str, list[dict[str, Any]]] = {
        "target_club": [],
        "opponent_club": [],
        "competition": [],
    }
    registry_key_by_side = {
        "target": "target_club",
        "opponent": "opponent_club",
        "competition": "competition",
    }
    seen_registry: set[tuple[str, str]] = set()
    entity_side_by_key = {
        _entity_key(entity["kind"], entity["id"]): entity["side"]
        for entity in _entities(context)
    }
    for document in registry_documents:
        side = entity_side_by_key.get(str(document.get("entity_key") or ""))
        key = registry_key_by_side.get(str(side or ""))
        domain = str(document.get("domain") or "")
        if not key or not domain or (key, domain) in seen_registry:
            continue
        seen_registry.add((key, domain))
        source_registry[key].append({
            "domain": domain,
            "source_name": document.get("source_name") or domain,
            "source_tier_rank": int(document.get("source_tier_rank") or 5),
            "source_tier": document.get("source_tier") or _TIER_LABELS[5],
            "latest_published_at": document.get("last_published_at") or UNKNOWN,
            "latest_url": document.get("last_article_url") or UNKNOWN,
        })
    for record in records:
        side = str(record.get("entity_side") or "")
        key = registry_key_by_side.get(side)
        domain = str(record.get("domain") or "")
        if not key or not domain or (key, domain) in seen_registry:
            continue
        seen_registry.add((key, domain))
        text = " ".join(
            str(record.get(field) or "")
            for field in ("title", "snippet", "content")
        )
        rank, label = _source_tier(
            record,
            context=context,
            registry_by_domain=registry_by_domain,
            text=text,
        )
        source_registry[key].append({
            "domain": domain,
            "source_name": record.get("source_name") or domain,
            "source_tier_rank": rank,
            "source_tier": label,
            "latest_published_at": record.get("published_at") or UNKNOWN,
            "latest_url": record.get("url") or UNKNOWN,
        })
    for values in source_registry.values():
        values.sort(key=lambda item: (item["source_tier_rank"], str(item["source_name"])))

    high_quality_findings = [
        finding
        for finding in findings
        if int((finding.get("source_tier") or {}).get("rank") or 9) <= 4
        and float(finding.get("confidence") or 0) >= 0.55
    ]
    status = (
        "available"
        if confirmed.get("available") or high_quality_findings
        else "partial"
        if findings
        else "unavailable"
    )
    if comparison.get("rerun_required"):
        brief = (
            f"Confirmed lineup materially changed the pre-match assumptions for "
            f"{context.get('player_name') or 'the target player'}; flag the prediction for rerun. "
            f"News remains shadow-only and has not changed RP math."
        )
    elif target_probability != UNKNOWN:
        brief = (
            f"Current evidence gives {context.get('player_name') or 'the target player'} a "
            f"{round(float(target_probability) * 100)}% start probability with {minutes_risk} minutes risk. "
            f"News remains shadow-only."
        )
    elif findings:
        brief = (
            "Current sources produced match-news evidence, but the target player's start status "
            "remains UNKNOWN. News remains shadow-only."
        )
    else:
        brief = (
            "Current news could not be verified; lineup, role, and availability remain UNKNOWN. "
            "RP math is unchanged."
        )

    return {
        "schema_version": NEWS_SCHEMA_VERSION,
        "status": status,
        "generated_at": retrieved_at,
        "source": "dynamic_news_research_and_confirmed_lineups",
        "projection_influence": "shadow_only",
        "math_unchanged": True,
        "expected_lineup": expected_lineup,
        "target_start_probability": target_probability,
        "minutes_risk": minutes_risk,
        "expected_role": target_role,
        "formation": (
            {
                "target_team": target_formation,
                "opponent_team": opponent_formation,
            }
            if target_formation != UNKNOWN or opponent_formation != UNKNOWN
            else UNKNOWN
        ),
        "important_teammate_changes": teammate_changes or UNKNOWN,
        "lineup_confidence": lineup_confidence,
        "regime_changes": regime_changes or UNKNOWN,
        "news_warnings": warnings,
        "news_brief": brief,
        "findings": findings,
        "contradictions": contradictions,
        "source_registry": source_registry,
        "confirmed_lineup_comparison": comparison,
    }


async def _google_news_search(query: str, client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(
        _SEARCH_URL,
        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
    )
    response.raise_for_status()
    if len(response.content) > _MAX_ARTICLE_BYTES:
        return []
    root = ElementTree.fromstring(response.text)
    rows: list[dict[str, Any]] = []
    for item in root.findall("./channel/item")[:_MAX_SEARCH_RESULTS]:
        source = item.find("source")
        description = item.findtext("description") or ""
        source_url = source.attrib.get("url") if source is not None else ""
        rows.append({
            "title": item.findtext("title") or "",
            "url": item.findtext("link") or "",
            "snippet": BeautifulSoup(description, "html.parser").get_text(" ", strip=True),
            "published_at": _iso(_parse_datetime(item.findtext("pubDate"))) if _parse_datetime(item.findtext("pubDate")) else UNKNOWN,
            "source_name": source.text if source is not None else "",
            "source_url": source_url,
            "domain": _domain(source_url) or _domain(item.findtext("link")),
        })
    return rows


async def _fetch_article(url: str) -> dict[str, Any]:
    current_url = url
    body = b""
    final_url = url
    encoding = "utf-8"
    for _hop in range(_MAX_REDIRECTS + 1):
        addresses = await _resolve_public_addresses(current_url)
        if not addresses:
            return {}
        parsed = urlparse(current_url)
        resolver = _PinnedResolver(str(parsed.hostname), addresses)
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            use_dns_cache=False,
            ttl_dns_cache=0,
        )
        timeout = aiohttp.ClientTimeout(total=4.0, connect=2.5, sock_read=3.0)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
            trust_env=False,
        ) as session:
            async with session.get(current_url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return {}
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = str(response.headers.get("content-type") or "").lower()
                if "html" not in content_type:
                    return {}
                try:
                    content_length = int(response.headers.get("content-length") or 0)
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > _MAX_ARTICLE_BYTES:
                    return {}
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > _MAX_ARTICLE_BYTES:
                        return {}
                    chunks.append(chunk)
                body = b"".join(chunks)
                final_url = str(response.url)
                encoding = response.charset or "utf-8"
                break
    else:
        return {}

    page = body.decode(encoding, errors="replace")
    soup = BeautifulSoup(page, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    published = None
    for selector in (
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "pubdate"}),
        ("time", {}),
    ):
        node = soup.find(selector[0], attrs=selector[1])
        if node:
            published = node.get("content") or node.get("datetime") or node.get_text(" ", strip=True)
            if published:
                break
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return {
        "content": text[:12000],
        "url": final_url,
        "published_at": _iso(_parse_datetime(published)) if _parse_datetime(published) else None,
    }


def _search_queries(
    context: dict[str, Any],
    registry_documents: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    entities = _entities(context)
    target = entities[0]
    opponent = entities[1]
    competition = entities[2]
    fixture_date = str(context.get("fixture_date") or "")[:10]
    player = str(context.get("player_name") or "")

    queries: list[tuple[dict[str, Any], str]] = []
    for entity in (target, opponent):
        name = entity["name"]
        queries.extend([
            (
                entity,
                f'"{name}" ("expected XI" OR lineup OR injury OR suspension OR training OR rotation OR "travel squad") {fixture_date}',
            ),
            (
                entity,
                f'"{name}" ("manager said" OR "press conference" OR formation OR tactical OR workload OR transfer) {fixture_date}',
            ),
        ])
    if player:
        queries.append((
            target,
            f'"{player}" "{target["name"]}" (injury OR training OR starts OR lineup OR role OR workload) {fixture_date}',
        ))
    queries.append((
        competition,
        f'"{target["name"]}" "{opponent["name"]}" "{competition["name"]}" (lineup OR injury OR preview OR suspension)',
    ))

    # Known good registry domains get one targeted freshness query per entity.
    for entity in entities:
        entity_key = _entity_key(entity["kind"], entity["id"])
        domains = [
            str(doc.get("domain"))
            for doc in registry_documents
            if doc.get("entity_key") == entity_key
            and int(doc.get("source_tier_rank") or 9) <= 3
            and doc.get("domain")
        ][:2]
        if domains:
            site_query = " OR ".join(f"site:{domain}" for domain in domains)
            queries.append((
                entity,
                f'"{entity["name"]}" ({site_query}) (lineup OR injury OR training OR manager OR formation)',
            ))
    return queries[:9]


async def _load_registry(db: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        collection = getattr(db, "news_source_registry")
        keys = [_entity_key(entity["kind"], entity["id"]) for entity in _entities(context)]
        cursor = collection.find({"entity_key": {"$in": keys}}, {"_id": 0})
        if hasattr(cursor, "to_list"):
            return await cursor.to_list(length=200)
        return [row async for row in cursor]
    except Exception:
        return []


async def _persist_registry(db: Any, records: list[dict[str, Any]]) -> None:
    global _registry_index_attempted
    try:
        from pymongo import UpdateOne

        if not _registry_index_attempted:
            _registry_index_attempted = True
            try:
                await db.news_source_registry.create_index(
                    [("entity_key", 1), ("domain", 1)],
                    unique=True,
                    name="news_source_entity_domain",
                )
                await db.news_source_registry.create_index(
                    [("entity_key", 1), ("last_seen_at", -1)],
                    name="news_source_entity_freshness",
                )
            except Exception:
                # Existing duplicate rows or Atlas quota pressure must not
                # prevent the current audit packet from being returned.
                pass

        operations = []
        now = _now()
        seen: set[tuple[str, str]] = set()
        for record in records:
            domain = str(record.get("domain") or "")
            entity_key = _entity_key(str(record.get("entity_type") or "unknown"), record.get("entity_id"))
            if not domain or (entity_key, domain) in seen:
                continue
            seen.add((entity_key, domain))
            operations.append(UpdateOne(
                {"entity_key": entity_key, "domain": domain},
                {
                    "$set": {
                        "entity_key": entity_key,
                        "entity_type": record.get("entity_type"),
                        "entity_id": record.get("entity_id"),
                        "entity_name": record.get("entity_name"),
                        "domain": domain,
                        "source_name": record.get("source_name") or domain,
                        "last_seen_at": now,
                        "last_success_at": now,
                        "last_article_url": record.get("url"),
                        "last_published_at": record.get("published_at"),
                    },
                    "$setOnInsert": {
                        "first_seen_at": now,
                        "source_tier_rank": record.get("source_tier_rank", 5),
                        "source_tier": record.get("source_tier", _TIER_LABELS[5]),
                    },
                    "$inc": {"successful_discoveries": 1},
                },
                upsert=True,
            ))
        if operations:
            await db.news_source_registry.bulk_write(operations[:40], ordered=False)
    except Exception:
        # Source learning is a cache/registry optimization. It must not block
        # the mandatory audit response during Atlas or network failures.
        return


async def run_news_intelligence(
    *,
    context: dict[str, Any],
    prediction: dict[str, Any],
    db: Any,
    lineups_payload: Any = None,
    injuries_payload: Any = None,
    search_fn: SearchFn | None = None,
    fetch_fn: FetchFn | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dynamically research both clubs and competition, then build the packet."""
    now = (now or _now()).astimezone(timezone.utc)
    context = {
        **context,
        "player_id": context.get("player_id") or prediction.get("playerId"),
        "player_name": context.get("player_name") or prediction.get("playerName"),
    }
    try:
        registry_documents = await asyncio.wait_for(
            _load_registry(db, context),
            timeout=1.0,
        )
    except Exception:
        registry_documents = []
    timeout = httpx.Timeout(4.0, connect=2.5)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/rss+xml,text/html;q=0.9,*/*;q=0.8"}

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=headers,
        follow_redirects=False,
    ) as client:
        async def search(query: str) -> list[dict[str, Any]]:
            if search_fn:
                operation = search_fn(query)
            else:
                operation = _google_news_search(query, client)
            return await asyncio.wait_for(operation, timeout=4.5)

        query_pairs = _search_queries(context, registry_documents)
        search_results = await asyncio.gather(
            *(search(query) for _, query in query_pairs),
            return_exceptions=True,
        )
        records: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        retrieved_at = _iso(now)
        for (entity, _query), result in zip(query_pairs, search_results):
            if isinstance(result, Exception) or not isinstance(result, list):
                continue
            for raw in result:
                if not isinstance(raw, dict):
                    continue
                url = str(raw.get("url") or "")
                title = str(raw.get("title") or "")
                key = (f"{entity['side']}:{url}", _normalized_name(title))
                if key in seen or not title:
                    continue
                seen.add(key)
                records.append({
                    **raw,
                    "domain": raw.get("domain") or _domain(raw.get("source_url")) or _domain(url),
                    "retrieved_at": retrieved_at,
                    "entity_type": entity["kind"],
                    "entity_id": entity["id"],
                    "entity_name": entity["name"],
                    "entity_side": entity["side"],
                })

        # Search engines can return broadly related league stories. Require the
        # assigned club/competition (or the target player) to appear before a
        # result can consume fetch budget, enter the source registry, or become
        # audit evidence.
        records = [
            record
            for record in records
            if _record_relevant(record, context)
        ]

        # Rank before fetching full text so official/current sources consume the
        # limited fetch budget ahead of generic previews.
        registry_by_domain = {
            str(doc.get("domain") or ""): doc
            for doc in registry_documents
            if doc.get("domain")
        }
        for record in records:
            text = f"{record.get('title') or ''} {record.get('snippet') or ''}"
            rank, label = _source_tier(
                record,
                context=context,
                registry_by_domain=registry_by_domain,
                text=text,
            )
            record["source_tier_rank"] = rank
            record["source_tier"] = label
        records.sort(key=lambda row: (
            int(row.get("source_tier_rank") or 9),
            -_freshness_multiplier(row.get("published_at"), now),
        ))

        fetch_candidates = [
            record
            for record in records
            if _safe_public_url(str(record.get("url") or ""))
        ][:_MAX_ARTICLE_FETCHES]

        async def fetch(record: dict[str, Any]) -> dict[str, Any]:
            if fetch_fn:
                operation = fetch_fn(str(record.get("url") or ""))
            else:
                operation = _fetch_article(str(record.get("url") or ""))
            return await asyncio.wait_for(operation, timeout=5.5)

        fetched = await asyncio.gather(
            *(fetch(record) for record in fetch_candidates),
            return_exceptions=True,
        )
        for record, article in zip(fetch_candidates, fetched):
            if isinstance(article, Exception) or not isinstance(article, dict):
                continue
            if article.get("content"):
                record["content"] = article["content"]
            if article.get("published_at") and record.get("published_at") in {None, UNKNOWN}:
                record["published_at"] = article["published_at"]
            if article.get("url") and _safe_public_url(str(article["url"])):
                record["url"] = str(article["url"])
                resolved_domain = _domain(article["url"])
                if resolved_domain and not _domain_matches(
                    resolved_domain,
                    {"news.google.com", "google.com"},
                ):
                    record["domain"] = resolved_domain

    try:
        await asyncio.wait_for(_persist_registry(db, records), timeout=0.8)
    except Exception:
        pass
    packet = analyze_news_evidence(
        context=context,
        prediction=prediction,
        records=records,
        registry_documents=registry_documents,
        lineups_payload=lineups_payload,
        injuries_payload=injuries_payload,
        now=now,
    )
    packet["research_diagnostics"] = {
        "queries_attempted": len(query_pairs),
        "queries_succeeded": sum(
            1
            for result in search_results
            if isinstance(result, list)
        ),
        "sources_discovered": len(records),
        "articles_selected_for_fetch": len(fetch_candidates),
        "articles_fetched": sum(
            1
            for article in fetched
            if isinstance(article, dict) and article.get("content")
        ),
        "runtime_budget_seconds": 18,
    }
    return packet