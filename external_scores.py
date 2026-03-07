"""
External live score providers and event matching helpers.

Supports:
  - Football: API-FOOTBALL (api-sports)
  - Basketball: API-BASKETBALL (api-sports)

Design goals:
  - One network pull per round (with TTL cache)
  - Conservative team-name matching with confidence scoring
  - Safe fallback when provider is unavailable
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"
_BASKETBALL_URL = "https://v1.basketball.api-sports.io/games"

_STOPWORDS = {
    "fc",
    "cf",
    "sc",
    "ac",
    "afc",
    "club",
    "football",
    "bk",
    "bc",
    "basketball",
    "team",
    "the",
    "de",
    "da",
    "la",
    "el",
}

_CACHE: Dict[str, Dict] = {
    "football": {"ts": 0.0, "key": "", "items": []},
    "basketball": {"ts": 0.0, "key": "", "items": []},
}


def _safe_int(value) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_name(name: Optional[str]) -> str:
    raw = str(name or "").strip().lower()
    if not raw:
        return ""

    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.replace("&", " and ")
    raw = re.sub(r"\(.*?\)", " ", raw)
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    tokens = [tok for tok in raw.split() if tok and tok not in _STOPWORDS]
    return " ".join(tokens)


def _name_similarity(a: Optional[str], b: Optional[str]) -> float:
    na = _normalize_name(a)
    nb = _normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    a_tokens = set(na.split())
    b_tokens = set(nb.split())
    if not a_tokens or not b_tokens:
        return 0.0

    inter = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    jaccard = inter / union if union > 0 else 0.0

    if na in nb or nb in na:
        return max(jaccard, 0.86)
    return jaccard


def _request_json(url: str, headers: Dict[str, str], params: Dict, timeout: int) -> Optional[Dict]:
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as exc:
        logger.debug("external score request failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.debug("external score request status=%s url=%s", resp.status_code, url)
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_football_snapshots(payload: Dict) -> List[Dict]:
    rows = payload.get("response") or []
    out: List[Dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        teams = row.get("teams") or {}
        home = (teams.get("home") or {}).get("name")
        away = (teams.get("away") or {}).get("name")
        goals = row.get("goals") or {}
        home_goals = _safe_int(goals.get("home"))
        away_goals = _safe_int(goals.get("away"))
        if home_goals is None or away_goals is None:
            continue

        fixture = row.get("fixture") or {}
        status = fixture.get("status") or {}
        elapsed = _safe_int(status.get("elapsed"))
        kickoff = _parse_iso_dt(fixture.get("date"))
        competition = (row.get("league") or {}).get("name", "")

        out.append(
            {
                "sport": "soccer",
                "home_name": str(home or ""),
                "away_name": str(away or ""),
                "home_score": int(home_goals),
                "away_score": int(away_goals),
                "elapsed_minutes": int(elapsed or 0),
                "kickoff": kickoff,
                "competition": str(competition or ""),
                "source": "api_football",
            }
        )
    return out


def _extract_basketball_snapshots(payload: Dict) -> List[Dict]:
    rows = payload.get("response") or []
    out: List[Dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        teams = row.get("teams") or {}
        home = (teams.get("home") or {}).get("name")
        away = (teams.get("away") or {}).get("name")

        scores = row.get("scores") or {}
        home_score = _safe_int((scores.get("home") or {}).get("total"))
        away_score = _safe_int((scores.get("away") or {}).get("total"))
        if home_score is None or away_score is None:
            continue

        status = row.get("status") or {}
        elapsed = _safe_int(status.get("elapsed"))
        kickoff = _parse_iso_dt((row.get("date") or {}).get("start"))
        competition = (row.get("league") or {}).get("name", "")

        out.append(
            {
                "sport": "basketball",
                "home_name": str(home or ""),
                "away_name": str(away or ""),
                "home_score": int(home_score),
                "away_score": int(away_score),
                "elapsed_minutes": int(elapsed or 0),
                "kickoff": kickoff,
                "competition": str(competition or ""),
                "source": "api_basketball",
            }
        )
    return out


def fetch_football_live_scores(
    api_key: str,
    cache_ttl_secs: int = 30,
    timeout_secs: int = 10,
) -> List[Dict]:
    if not api_key:
        return []

    now_ts = time.time()
    cache = _CACHE["football"]
    if cache["key"] == api_key and (now_ts - float(cache["ts"])) < float(max(1, cache_ttl_secs)):
        return list(cache["items"])

    payload = _request_json(
        url=_FOOTBALL_URL,
        headers={
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "v3.football.api-sports.io",
        },
        params={"live": "all"},
        timeout=max(3, int(timeout_secs)),
    )
    items = _extract_football_snapshots(payload or {})

    cache["ts"] = now_ts
    cache["key"] = api_key
    cache["items"] = list(items)
    return items


def fetch_basketball_live_scores(
    api_key: str,
    cache_ttl_secs: int = 30,
    timeout_secs: int = 10,
) -> List[Dict]:
    if not api_key:
        return []

    now_ts = time.time()
    cache = _CACHE["basketball"]
    if cache["key"] == api_key and (now_ts - float(cache["ts"])) < float(max(1, cache_ttl_secs)):
        return list(cache["items"])

    payload = _request_json(
        url=_BASKETBALL_URL,
        headers={
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "v1.basketball.api-sports.io",
        },
        params={"live": "all"},
        timeout=max(3, int(timeout_secs)),
    )
    items = _extract_basketball_snapshots(payload or {})

    cache["ts"] = now_ts
    cache["key"] = api_key
    cache["items"] = list(items)
    return items


def match_external_score_for_event(
    *,
    event_home: str,
    event_away: str,
    event_kickoff: Optional[str],
    event_competition: str,
    snapshots: List[Dict],
    min_confidence: float = 0.76,
    kickoff_tolerance_mins: int = 240,
) -> Optional[Dict]:
    if not snapshots:
        return None

    kickoff_dt = _parse_iso_dt(event_kickoff)
    competition_norm = _normalize_name(event_competition)
    best: Optional[Tuple[float, Dict]] = None

    for snap in snapshots:
        sim_home = _name_similarity(event_home, snap.get("home_name"))
        sim_away = _name_similarity(event_away, snap.get("away_name"))
        score = (sim_home + sim_away) / 2.0
        if score < 0.52:
            continue

        # Reject ambiguous swapped match to avoid score inversion.
        swapped = (_name_similarity(event_home, snap.get("away_name")) + _name_similarity(event_away, snap.get("home_name"))) / 2.0
        if swapped > score:
            continue

        snap_kickoff = snap.get("kickoff")
        if kickoff_dt and isinstance(snap_kickoff, datetime):
            diff_mins = abs((kickoff_dt - snap_kickoff).total_seconds()) / 60.0
            if diff_mins > float(max(30, kickoff_tolerance_mins)):
                continue
            if diff_mins <= 30:
                score += 0.07
            elif diff_mins <= 90:
                score += 0.03

        snap_comp_norm = _normalize_name(snap.get("competition"))
        if competition_norm and snap_comp_norm:
            comp_tokens_a = set(competition_norm.split())
            comp_tokens_b = set(snap_comp_norm.split())
            if comp_tokens_a and comp_tokens_b:
                overlap = len(comp_tokens_a & comp_tokens_b) / max(1, len(comp_tokens_a | comp_tokens_b))
                score += overlap * 0.05

        if best is None or score > best[0]:
            best = (score, snap)

    if not best:
        return None
    if best[0] < float(min_confidence):
        return None
    return {"confidence": round(float(best[0]), 3), "snapshot": best[1]}

