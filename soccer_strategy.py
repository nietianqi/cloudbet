"""
足球直播总进球信号生成模块
=============================
职责:
  1. 从 Cloudbet Feed API 拉取足球直播赛事 + total_goals 盘口
  2. 从 API-Football (可选) 拉取实时统计 → 近似 xG
  3. 调用 InPlayGoalsModel 计算公平赔率 + edge
  4. 应用入场过滤规则，输出候选信号
  5. 记录赔率快照到 SQLite（供 CLV 追踪使用）

入场规则（同时满足）:
  ① event.status == TRADING_LIVE
  ② 距结束剩余时间 ≥ MIN_REMAINING_MINUTES（避免末段高波动）
  ③ 盘口最近 STABLE_WINDOW 秒内无剧烈跳线（防抢封盘/危险时刻）
  ④ 模型 edge ≥ EDGE_THRESHOLD
  ⑤ 建议 stake ≤ market maxStake

盘口稳定性说明:
  危险时刻（任意球、点球、红牌判决过程）Cloudbet 会暂停市场
  (MARKET_SUSPENDED) 并在恢复时大幅调整赔率。跳线检测可在
  这些暂停解除后避免立刻入场（通常赔率调整后 edge 已消失）。
"""

import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from cloudbet_client import CloudbetClient
from soccer_model import InPlayGoalsModel, kelly_stake
from xg_client import create_xg_client, estimate_xg_from_score_and_time, APIFootballClient
from external_scores import fetch_football_live_scores, match_external_score_for_event
import live_db

logger = logging.getLogger(__name__)

# ── 赔率历史缓存（用于稳定性检测）────────────────────────────
# {event_id: [(timestamp, over_price, under_price), ...]}
_odds_cache: Dict[str, List[Tuple[float, float, float]]] = defaultdict(list)
_CACHE_TTL = 300   # 5 分钟后清理过期赛事
_competition_guard_cache = {"ts": 0.0, "bad": set(), "stats": {}}
_competition_country_cache = {"ts": 0.0, "by_key": {}, "country_slugs": set()}

# ── 赛前先验来源（简化）─────────────────────────────────────
# 正式部署应接入 Dixon-Coles 模型（基于历史数据）
# 这里使用保守的均值先验：主队 1.4 / 客队 1.15（接近欧洲顶级联赛均值）
_DEFAULT_PRE_XG_HOME = 1.40
_DEFAULT_PRE_XG_AWAY = 1.15

# 重点扫描联赛（Trading key 全扫描成本高，先聚焦主要联赛）
PRIORITY_LEAGUES = [
    "soccer-england-premier-league",
    "soccer-spain-la-liga",
    "soccer-germany-bundesliga",
    "soccer-italy-serie-a",
    "soccer-france-ligue-1",
    "soccer-uefa-champions-league",
    "soccer-uefa-europa-league",
    "soccer-netherlands-eredivisie",
    "soccer-portugal-primeira-liga",
]

# FIFA 排名筛选（数据快照来源：7m FIFA Men's Ranking）
# 规则：前 150 国家仅一级联赛；前 40 国家允许一级和二级联赛
_FIFA_TOP40_COUNTRY_SLUGS = {
    "spain", "argentina", "france", "england", "brazil", "portugal",
    "netherlands", "morocco", "belgium", "germany", "croatia", "senegal",
    "italy", "colombia", "usa", "mexico", "uruguay", "switzerland", "japan",
    "iran", "denmark", "korea-republic", "ecuador", "austria", "turkiye",
    "nigeria", "australia", "algeria", "canada", "ukraine", "egypt", "norway",
    "panama", "poland", "wales", "russia", "cote-d-ivoire", "scotland",
    "serbia", "paraguay",
}

_FIFA_TOP150_COUNTRY_SLUGS = {
    "spain", "argentina", "france", "england", "brazil", "portugal", "netherlands", "morocco", "belgium",
    "germany", "croatia", "senegal", "italy", "colombia", "usa", "mexico", "uruguay", "switzerland", "japan",
    "iran", "denmark", "korea-republic", "ecuador", "austria", "turkiye", "nigeria", "australia", "algeria",
    "canada", "ukraine", "egypt", "norway", "panama", "poland", "wales", "russia", "cote-d-ivoire", "scotland",
    "serbia", "paraguay", "hungary", "sweden", "czech-republic", "slovakia", "cameroon", "greece", "tunisia",
    "democratic-rep-congo", "romania", "venezuela", "costa-rica", "uzbekistan", "peru", "mali", "chile",
    "qatar", "slovenia", "iraq", "republic-of-ireland", "south-africa", "saudi-arabia", "burkina-faso", "albania",
    "jordan", "honduras", "north-macedonia", "cape-verde", "united-arab-emirates", "northern-ireland", "jamaica",
    "bosnia-herzegovina", "ghana", "georgia", "iceland", "finland", "bolivia", "israel", "oman", "kosovo",
    "guinea", "curacao", "montenegro", "haiti", "syria", "new-zealand", "gabon", "bulgaria", "uganda", "angola",
    "bahrain", "zambia", "benin", "china", "guatemala", "palestine", "thailand", "trinidad-tobago", "belarus",
    "el-salvador", "tajikistan", "mozambique", "luxembourg", "kyrgyzstan", "madagascar", "armenia", "comoros",
    "equatorial-guinea", "vietnam", "lebanon", "tanzania", "niger", "libya", "kenya", "kazakhstan", "mauritania",
    "gambia", "sudan", "namibia", "north-korea", "sierra-leone", "malaysia", "indonesia", "suriname", "togo",
    "faroe-islands", "malawi", "azerbaijan", "cyprus", "estonia", "rwanda", "nicaragua", "zimbabwe",
    "guinea-bissau", "congo", "kuwait", "philippines", "turkmenistan", "central-african-republic", "latvia",
    "liberia", "india", "dominican-republic", "lesotho", "botswana", "burundi", "lithuania", "ethiopia",
    "singapore", "yemen", "new-caledonia",
}

_FIFA_COUNTRY_ALIASES = {
    "republic-of-korea": "korea-republic",
    "south-korea": "korea-republic",
    "korea-south": "korea-republic",
    "turkey": "turkiye",
    "czechia": "czech-republic",
    "czech-rep": "czech-republic",
    "bosnia-and-herzegovina": "bosnia-herzegovina",
    "ivory-coast": "cote-d-ivoire",
    "cote-divoire": "cote-d-ivoire",
    "us": "usa",
    "united-states": "usa",
}

_LEAGUE_EXCLUDE_MARKERS = {
    "women", "womens", "female", "friendly", "friendlies", "cup", "playoff", "playoffs", "qualification",
    "qualifier", "champions-league", "europa-league", "conference-league", "libertadores", "nations-league",
    "world-cup", "super-cup", "u17", "u18", "u19", "u20", "u21", "u23", "youth", "junior", "juniori", "academy",
    "reserve", "srl", "virtual", "simulated", "regionalliga", "tercera", "federacion", "league-one", "league-two",
    "national-league", "state-league", "npl", "southern-league", "isthmian", "northern-premier", "amateur",
    "primavera", "3rd", "third-division", "danmarksserien", "cfl", "msfl",
}

_LEAGUE_TIER2_MARKERS = {
    "championship", "2nd", "second", "serie-b", "liga-2", "ligue-2", "laliga-2", "segunda-division", "primera-b",
    "primera-nacional", "j-league-2", "2-liga", "2-mfl", "fnl", "esiliiga", "challenge-league", "ascenso",
    "expansion-mx", "first-division", "super-league-2", "first-division-b",
}

_LEAGUE_TIER1_MARKERS = {
    "premier-league", "super-league", "superliga", "bundesliga", "serie-a", "laliga", "la-liga", "ligue-1",
    "eredivisie", "primeira-liga", "a-league", "hnl", "nb-i", "parva-liga", "virsliga", "a-lyga", "primera-division",
    "primera-a", "pro-league", "j-league", "league-1", "1-liga", "first-professional-league",
}

_LEAGUE_TIER2_EXACT_KEYS = {
    "soccer-england-championship",
    "soccer-germany-2nd-bundesliga",
    "soccer-italy-serie-b",
    "soccer-spain-laliga-2",
    "soccer-france-ligue-2",
    "soccer-japan-j-league-2",
    "soccer-belgium-first-division-b",
    "soccer-austria-2-liga",
    "soccer-denmark-1st-division",
    "soccer-croatia-2-hnl",
    "soccer-czech-republic-fnl",
    "soccer-cyprus-2nd-division",
    "soccer-estonia-esiliiga",
    "soccer-republic-of-ireland-first-division",
    "soccer-malta-first-division",
    "soccer-greece-super-league-2",
    "soccer-bulgaria-second-prof-league",
    "soccer-algeria-ligue-2",
    "soccer-chile-primera-b",
    "soccer-costa-rica-liga-de-ascenso-apertura",
    "soccer-mexico-liga-de-ascenso-apertura",
    "soccer-macedonia-2-mfl",
    "soccer-chinese-taipei-t816d-second-league",
}

_FIRST_DIVISION_AS_TIER2_COUNTRIES = {
    "denmark", "republic-of-ireland", "malta", "estonia", "chinese-taipei",
}

_CUP_MARKERS = {
    "cup", "pokal", "coppa", "copa", "trophy", "beker", "super-cup", "league-cup",
}

_CUP_EXCLUDE_MARKERS = {
    "qualification", "qualifier", "friendly", "friendlies", "playoff", "playoffs",
    "women", "womens", "u17", "u18", "u19", "u20", "u21", "u23", "youth", "junior",
}

_INTERNATIONAL_CUP_MARKERS = {
    "champions-league", "europa-league", "conference-league", "libertadores",
    "sudamericana", "nations-league", "world-cup", "afc-champions", "concacaf-champions",
}


def _slugify_text(value: str) -> str:
    text = str(value or "").lower().strip()
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _normalize_country_slug_for_fifa(country_slug: str) -> str:
    slug = _slugify_text(country_slug)
    return _FIFA_COUNTRY_ALIASES.get(slug, slug)


def _extract_country_from_comp_key(comp_key: str, known_country_slugs: set) -> str:
    key = str(comp_key or "").lower()
    if not key.startswith("soccer-"):
        return ""
    tail = key[len("soccer-"):]
    if not tail:
        return ""

    for country_slug in sorted(known_country_slugs or set(), key=len, reverse=True):
        prefix = f"{country_slug}-"
        if tail.startswith(prefix) or tail == country_slug:
            return country_slug

    parts = tail.split("-")
    if len(parts) < 2:
        return ""
    return parts[0]


def _get_competition_country_map(client: CloudbetClient, refresh_secs: int = 21600) -> Tuple[Dict[str, str], set]:
    now_ts = time.time()
    if (
        _competition_country_cache["by_key"]
        and (now_ts - float(_competition_country_cache["ts"])) < float(max(60, refresh_secs))
    ):
        return (
            dict(_competition_country_cache["by_key"]),
            set(_competition_country_cache["country_slugs"]),
        )

    by_key: Dict[str, str] = {}
    country_slugs: set = set()
    try:
        payload = client.get_competitions("soccer")
        for category in payload.get("categories", []) or []:
            raw_country = category.get("name", "")
            country_slug = _normalize_country_slug_for_fifa(raw_country)
            if country_slug:
                country_slugs.add(country_slug)
            for comp in category.get("competitions", []) or []:
                key = str(comp.get("key") or "")
                if key:
                    by_key[key] = country_slug
        if by_key:
            _competition_country_cache["ts"] = now_ts
            _competition_country_cache["by_key"] = dict(by_key)
            _competition_country_cache["country_slugs"] = set(country_slugs)
            return by_key, country_slugs
    except Exception as exc:
        logger.debug("读取足球联赛国家映射失败: %s", exc)

    return (
        dict(_competition_country_cache.get("by_key", {})),
        set(_competition_country_cache.get("country_slugs", set())),
    )


def _infer_domestic_league_tier(comp_key: str, comp_name: str, country_slug: str) -> Optional[int]:
    key = str(comp_key or "").lower()
    name = str(comp_name or "").lower()
    text = f"{key} {name}"
    if not key.startswith("soccer-"):
        return None

    if key in _LEAGUE_TIER2_EXACT_KEYS:
        return 2

    for marker in _LEAGUE_EXCLUDE_MARKERS:
        if marker in text:
            return None

    if (
        "first-division" in key
        or "first division" in name
        or "1st-division" in key
        or "1st division" in name
    ):
        if country_slug in _FIRST_DIVISION_AS_TIER2_COUNTRIES:
            return 2
        return 1

    for marker in _LEAGUE_TIER2_MARKERS:
        if marker in text:
            return 2

    for marker in _LEAGUE_TIER1_MARKERS:
        if marker in text:
            return 1

    return None


def _is_cup_competition(comp_key: str, comp_name: str) -> bool:
    key = str(comp_key or "").lower()
    name = str(comp_name or "").lower()
    text = f"{key} {name}"
    if not any(marker in text for marker in _CUP_MARKERS):
        return False
    if any(marker in text for marker in _CUP_EXCLUDE_MARKERS):
        return False
    return True


def _is_international_competition(comp_key: str, country_slug: str) -> bool:
    key = str(comp_key or "").lower()
    if key.startswith("soccer-international-"):
        return True
    return country_slug in {"international", "international-clubs"}


def _is_international_cup_competition(comp_key: str, comp_name: str, country_slug: str) -> bool:
    if not _is_international_competition(comp_key, country_slug):
        return False
    text = f"{str(comp_key or '').lower()} {str(comp_name or '').lower()}"
    if any(marker in text for marker in _CUP_EXCLUDE_MARKERS):
        return False
    if _is_cup_competition(comp_key, comp_name):
        return True
    if any(marker in text for marker in _INTERNATIONAL_CUP_MARKERS):
        return True
    return False


def _is_allowed_by_fifa_country_tier(
    country_slug: str,
    tier: Optional[int],
    allow_second_tier_for_top40: bool = True,
    comp_key: str = "",
    comp_name: str = "",
) -> Tuple[bool, str]:
    if _is_international_cup_competition(comp_key, comp_name, country_slug):
        return True, "ok_international_cup"

    if _is_cup_competition(comp_key, comp_name):
        if not country_slug:
            return False, "no_country"
        if country_slug not in _FIFA_TOP150_COUNTRY_SLUGS:
            return False, "country_outside_top150"
        return True, "ok_domestic_cup_top150"

    if not country_slug:
        return False, "no_country"
    if tier not in (1, 2):
        return False, "no_tier"
    if country_slug not in _FIFA_TOP150_COUNTRY_SLUGS:
        return False, "country_outside_top150"
    if country_slug in _FIFA_TOP40_COUNTRY_SLUGS:
        if tier == 1 or (tier == 2 and allow_second_tier_for_top40):
            return True, "ok_top40"
        return False, "tier_not_allowed"
    if tier == 1:
        return True, "ok_top150_tier1"
    return False, "tier_not_allowed"


def _update_odds_cache(event_id: str, over_price: float, under_price: float) -> None:
    """更新赔率历史，并清理 TTL 过期数据"""
    now = time.time()
    _odds_cache[event_id].append((now, over_price, under_price))

    cutoff = now - _CACHE_TTL
    _odds_cache[event_id] = [
        entry for entry in _odds_cache[event_id] if entry[0] >= cutoff
    ]
    # 收集过期 key 后统一删除（避免 dict-deletion-during-iteration 崩溃）
    stale_keys = [
        eid for eid, hist in _odds_cache.items()
        if not hist or (now - hist[-1][0]) > _CACHE_TTL
    ]
    for eid in stale_keys:
        _odds_cache.pop(eid, None)


def _is_odds_stable(
    event_id: str,
    window_secs: float,
    jump_threshold: float,
) -> Tuple[bool, str]:
    """
    检查最近 window_secs 内赔率是否稳定

    返回:
        (is_stable, reason_str)
    """
    history = _odds_cache.get(event_id, [])
    now = time.time()
    recent = [(ts, op, up) for ts, op, up in history if now - ts <= window_secs]

    if len(recent) < 2:
        return True, "数据不足，默认稳定"

    over_prices = [op for _, op, _ in recent]
    under_prices = [up for _, _, up in recent]
    max_jump_over = max(over_prices) - min(over_prices)
    max_jump_under = max(under_prices) - min(under_prices)
    max_jump = max(max_jump_over, max_jump_under)

    if max_jump > jump_threshold:
        return False, f"盘口跳动 {max_jump:.3f} > 阈值 {jump_threshold}"

    return True, f"稳定 (最大跳动 {max_jump:.3f})"


def _recent_best_price(event_id: str, side: str, window_secs: float) -> Optional[float]:
    """在最近窗口内获取该方向的最佳可成交价格（十进制赔率，越大越好）。"""
    history = _odds_cache.get(str(event_id or ""), [])
    if not history:
        return None
    now = time.time()
    recent = [(op, up) for ts, op, up in history if now - ts <= max(1.0, float(window_secs))]
    if not recent:
        return None
    side_norm = str(side or "").lower()
    prices = [op if side_norm == "over" else up for op, up in recent]
    prices = [float(p) for p in prices if p and float(p) > 1.0]
    return max(prices) if prices else None


def _is_price_chasing_recent_best(
    *,
    current_price: float,
    recent_best_price: Optional[float],
    worse_tolerance: float,
) -> bool:
    """
    判断是否在追坏价：
    - 若当前价格显著低于最近窗口最佳价格，则视为追价，建议放弃。
    """
    if recent_best_price is None or recent_best_price <= 1.0:
        return False
    tol = max(0.0, float(worse_tolerance))
    threshold = recent_best_price * (1.0 - tol)
    return float(current_price) < threshold


def _robust_edge_after_price_drop(
    *,
    model_prob: float,
    market_price: float,
    adverse_price_delta: float,
) -> float:
    """
    价格鲁棒性：
    假设成交价再恶化 adverse_price_delta（十进制赔率下降），
    计算该情况下的 edge，避免仅凭脆弱价差入场。
    """
    p = max(0.0, min(1.0, float(model_prob)))
    price = max(1.01, float(market_price) - max(0.0, float(adverse_price_delta)))
    return p - (1.0 / price)


def _get_live_xg(
    xg_client,
    af_fixture_id: Optional[int],
    goals_home: int,
    goals_away: int,
    minute: int,
    pre_xg_home: float,
    pre_xg_away: float,
) -> Tuple[float, float, dict]:
    """
    获取实时 xG（有 API-Football 用真实统计，否则回退估算）

    返回:
        (live_xg_home, live_xg_away, game_state_dict)
    """
    game_state = {"red_cards_home": 0, "red_cards_away": 0}

    if af_fixture_id and isinstance(xg_client, APIFootballClient):
        try:
            stats = xg_client.get_fixture_statistics(af_fixture_id)
            events_info = xg_client.get_events_info(af_fixture_id)

            if stats:
                home_xg = stats.get("home", {}).get("xg_approx", 0)
                away_xg = stats.get("away", {}).get("xg_approx", 0)
                if home_xg > 0 or away_xg > 0:
                    if events_info:
                        game_state["red_cards_home"] = events_info.get("red_cards_home", 0)
                        game_state["red_cards_away"] = events_info.get("red_cards_away", 0)
                    return (
                        max(home_xg, 0.001),
                        max(away_xg, 0.001),
                        game_state,
                    )
        except Exception as exc:
            logger.debug("xG 数据获取失败: %s", exc)

    # 回退：用比分 + 先验估算
    est_h, est_a = estimate_xg_from_score_and_time(
        goals_home, goals_away, minute, pre_xg_home, pre_xg_away
    )
    return est_h, est_a, game_state


def _extract_match_state(event: Dict) -> Dict:
    """
    提取比赛比分与时间，并标记比分来源是否可靠。

    仅在明确拿到比分字段时才认为 score_reliable=True，
    避免把“未知比分”误判成 0-0 造成系统性偏差。
    """
    home_goals = 0
    away_goals = 0
    score_reliable = False
    score_source = "unknown"

    scores = event.get("scores") or {}
    if isinstance(scores, dict):
        h = scores.get("home")
        a = scores.get("away")
        if h is None:
            h = scores.get("1")
        if a is None:
            a = scores.get("2")
        if h is not None and a is not None:
            try:
                home_goals = int(h)
                away_goals = int(a)
                score_reliable = True
                score_source = "scores"
            except (TypeError, ValueError):
                pass

    if not score_reliable:
        home_obj = event.get("home") or {}
        away_obj = event.get("away") or {}
        h = home_obj.get("score")
        a = away_obj.get("score")
        if h is not None and a is not None:
            try:
                home_goals = int(h)
                away_goals = int(a)
                score_reliable = True
                score_source = "home_away"
            except (TypeError, ValueError):
                pass

    if not score_reliable:
        periods = event.get("periods") or []
        if periods:
            try:
                h_sum = 0
                a_sum = 0
                has_any = False
                for p in periods:
                    hs = p.get("homeScore")
                    as_ = p.get("awayScore")
                    if hs is None or as_ is None:
                        continue
                    h_sum += int(hs)
                    a_sum += int(as_)
                    has_any = True
                if has_any:
                    home_goals = h_sum
                    away_goals = a_sum
                    score_reliable = True
                    score_source = "periods"
            except (TypeError, ValueError):
                pass

    elapsed = 0
    clock = event.get("clock") or {}
    elapsed_candidates = (
        clock.get("elapsedSeconds"),
        clock.get("elapsed"),
        event.get("elapsedSeconds"),
        event.get("elapsed"),
    )
    for raw in elapsed_candidates:
        if raw in (None, ""):
            continue
        try:
            secs = float(raw)
        except (TypeError, ValueError):
            continue
        if secs > 0:
            elapsed = int(secs // 60)
            break

    if elapsed <= 0:
        kickoff = event.get("cutoffTime") or event.get("startTime")
        if kickoff:
            try:
                kickoff_dt = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
                if kickoff_dt.tzinfo is None:
                    kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
                elapsed = max(0, int((datetime.now(timezone.utc) - kickoff_dt).total_seconds() // 60))
            except (TypeError, ValueError):
                pass

    elapsed = max(0, min(elapsed, 130))
    return {
        "home_goals": home_goals,
        "away_goals": away_goals,
        "elapsed": elapsed,
        "score_reliable": score_reliable,
        "score_source": score_source,
    }


def _is_elapsed_blocked(elapsed: float, blocked_ranges: List) -> bool:
    for item in blocked_ranges or []:
        try:
            start, end = item
            if float(start) <= float(elapsed) < float(end):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _contains_blocked_keyword(*values: str, keywords: Optional[List[str]] = None) -> bool:
    if not keywords:
        return False
    lowered_keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
    if not lowered_keywords:
        return False
    combined = " ".join(str(v or "").lower() for v in values)
    return any(k in combined for k in lowered_keywords)


def _impute_score_from_line(
    line: float,
    elapsed: float,
    pre_xg_home: float,
    pre_xg_away: float,
) -> Tuple[int, int, bool]:
    """
    Conservative score imputation when feed score is missing.

    Idea:
      - Use game progress * live total line to approximate current total goals.
      - Split home/away by pre-match xG ratio.
    """
    try:
        line_val = float(line)
        elapsed_val = float(elapsed)
    except (TypeError, ValueError):
        return 0, 0, False

    if line_val <= 0 or elapsed_val <= 0:
        return 0, 0, False

    total_minutes = 90.0 if elapsed_val <= 90.0 else 96.0
    progress = max(0.0, min(elapsed_val / total_minutes, 1.0))
    estimated_total = max(0.0, progress * line_val)
    total_goals = int(round(estimated_total))

    prior_total = max(0.1, float(pre_xg_home) + float(pre_xg_away))
    home_ratio = max(0.05, min(0.95, float(pre_xg_home) / prior_total))
    home_goals = int(round(total_goals * home_ratio))
    away_goals = max(0, total_goals - home_goals)
    return home_goals, away_goals, True


def _get_bad_competitions(cfg: Dict) -> Tuple[set, Dict[str, Dict]]:
    if not cfg.get("competition_guard_enabled", True):
        return set(), {}

    refresh_secs = max(10, int(cfg.get("competition_guard_refresh_secs", 180)))
    now_ts = time.time()
    if now_ts - float(_competition_guard_cache.get("ts", 0.0)) < refresh_secs:
        return (
            set(_competition_guard_cache.get("bad", set())),
            dict(_competition_guard_cache.get("stats", {})),
        )

    stats = live_db.get_recent_competition_performance(
        sport="soccer",
        window=int(cfg.get("competition_guard_window", 240)),
        min_samples=int(cfg.get("competition_guard_min_samples", 5)),
        db_file=cfg.get("db_file", "live_betting.db"),
    )

    min_roi = float(cfg.get("competition_guard_min_roi", -0.25))
    min_wr = float(cfg.get("competition_guard_min_win_rate", 0.30))
    bad = set()
    for comp, st in stats.items():
        if float(st.get("roi", 0.0)) <= min_roi or float(st.get("win_rate", 0.0)) < min_wr:
            bad.add(str(comp))

    _competition_guard_cache["ts"] = now_ts
    _competition_guard_cache["bad"] = set(bad)
    _competition_guard_cache["stats"] = dict(stats)
    return bad, stats


def generate_soccer_signals(cfg: Dict) -> List[Dict]:
    """
    主信号生成函数（每个轮询周期调用）

    参数:
        cfg: 配置字典，需含:
            api_key, edge_threshold, min_remaining_minutes,
            stable_window_secs, jump_threshold, kelly_fraction,
            max_stake_pct, min_stake, bankroll, prior_weight_live,
            pre_xg_home (可选, 默认 1.40), pre_xg_away (可选, 默认 1.15),
            af_key (API-Football key，可选), db_file, dry_run

    返回:
        list of signal dicts，按 edge 降序排列
    """
    client = CloudbetClient(cfg["api_key"])
    xg_client = create_xg_client(cfg.get("af_key"))

    edge_threshold = cfg.get("edge_threshold", 0.06)
    min_remaining = cfg.get("min_remaining_minutes", 8.0)
    stable_window = cfg.get("stable_window_secs", 25)
    jump_threshold = cfg.get("jump_threshold", 0.10)
    bankroll = cfg.get("bankroll", 100.0)
    kelly_fraction = cfg.get("kelly_fraction", 0.25)
    max_stake_pct = cfg.get("max_stake_pct", 0.005)
    min_stake = cfg.get("min_stake", 1.0)
    min_elapsed_minutes = cfg.get("min_elapsed_minutes", 1.0)
    pre_xg_home = cfg.get("pre_xg_home", _DEFAULT_PRE_XG_HOME)
    pre_xg_away = cfg.get("pre_xg_away", _DEFAULT_PRE_XG_AWAY)
    live_weight = cfg.get("prior_weight_live", 0.65)
    db_file = cfg.get("db_file", "live_betting.db")
    require_reliable_score = bool(cfg.get("require_reliable_score", True))
    allow_imputed_score = bool(cfg.get("allow_imputed_score", True))
    imputed_score_min_elapsed = float(cfg.get("imputed_score_min_elapsed", 10.0))
    imputed_score_extra_edge = float(cfg.get("imputed_score_extra_edge", 0.04))
    imputed_score_stake_mult = max(0.1, min(1.0, float(cfg.get("imputed_score_stake_mult", 0.6))))
    min_market_price = float(cfg.get("min_market_price", 1.70))
    max_market_price = float(cfg.get("max_market_price", 2.10))
    if max_market_price < min_market_price:
        min_market_price, max_market_price = max_market_price, min_market_price
    blocked_elapsed_ranges = cfg.get("blocked_elapsed_ranges", [(30.0, 45.0)])
    competition_block_keywords = cfg.get("competition_block_keywords", [])
    fifa_league_filter_enabled = bool(cfg.get("fifa_league_filter_enabled", True))
    fifa_allow_second_tier_for_top40 = bool(cfg.get("fifa_allow_second_tier_for_top40", True))
    competition_country_refresh_secs = int(cfg.get("competition_country_refresh_secs", 21600))
    entry_price_window_secs = float(cfg.get("entry_price_window_secs", 90))
    entry_price_worse_tolerance = float(cfg.get("entry_price_worse_tolerance", 0.02))
    edge_robust_price_delta = float(cfg.get("edge_robust_price_delta", 0.08))
    edge_robust_min = float(cfg.get("edge_robust_min", 0.01))
    external_score_enabled = bool(cfg.get("external_score_enabled", True))
    external_score_prefer = bool(cfg.get("external_score_prefer", True))
    external_key = str(cfg.get("external_football_key") or cfg.get("af_key") or "")
    external_min_confidence = float(cfg.get("external_score_min_confidence", 0.80))
    external_kickoff_tolerance = int(cfg.get("external_score_kickoff_tolerance_mins", 240))
    external_cache_ttl = int(cfg.get("external_score_cache_ttl_secs", 45))
    external_timeout = int(cfg.get("external_score_timeout_secs", 10))
    # None = 扫描全量足球联赛（由 CloudbetClient.get_all_live_soccer 内部处理）
    leagues = cfg.get("leagues")
    live_statuses = cfg.get("live_statuses", ["TRADING_LIVE", "TRADING"])
    scan_progress_every = cfg.get("scan_progress_every", 25)
    prefer_bulk_events_api = cfg.get("prefer_bulk_events_api", True)
    bulk_from_hours = cfg.get("bulk_from_hours", 4)
    bulk_to_hours = cfg.get("bulk_to_hours", 2)
    hydrate_live_events = cfg.get("hydrate_live_events", False)
    fallback_to_league_scan_on_bulk_failure = cfg.get("fallback_to_league_scan_on_bulk_failure", True)

    # ????????????? TRADING_LIVE + TRADING?
    live_events = client.get_all_live_soccer(
        markets=["soccer.total_goals", "soccer.match_odds"],
        priority_leagues=leagues,
        live_statuses=live_statuses,
        progress_every=scan_progress_every,
        prefer_bulk_events_api=prefer_bulk_events_api,
        bulk_from_hours=bulk_from_hours,
        bulk_to_hours=bulk_to_hours,
        hydrate_live_events=hydrate_live_events,
        fallback_to_league_scan_on_bulk_failure=fallback_to_league_scan_on_bulk_failure,
    )

    if not live_events:
        logger.info("足球: 无符合状态赛事（过滤=%s）", live_statuses)
        return []

    status_count = defaultdict(int)
    for event in live_events:
        status_count[str(event.get("status", "UNKNOWN")).upper()] += 1
    logger.info("足球: 获取赛事 %d 场，状态分布=%s", len(live_events), dict(status_count))

    model = InPlayGoalsModel(pre_xg_home, pre_xg_away, live_weight=live_weight)
    signals = []
    skipped_for_elapsed = 0
    skipped_for_unreliable_score = 0
    skipped_for_price = 0
    skipped_for_elapsed_block = 0
    skipped_for_competition = 0
    skipped_for_competition_keyword = 0
    skipped_for_fifa_filter = 0
    skipped_for_fifa_country = 0
    skipped_for_fifa_tier = 0
    skipped_for_fifa_unknown = 0
    skipped_for_edge = 0
    skipped_for_price_chasing = 0
    skipped_for_edge_fragile = 0
    skipped_for_imputed_early = 0
    external_match_count = 0
    external_snapshot_count = 0
    max_edge_seen = float("-inf")
    bad_competitions, comp_stats = _get_bad_competitions(cfg)
    if bad_competitions:
        logger.info("联赛风控门控: bad_competitions=%d (样本窗=%d)", len(bad_competitions), int(cfg.get("competition_guard_window", 240)))

    competition_country_by_key, known_country_slugs = _get_competition_country_map(
        client,
        refresh_secs=competition_country_refresh_secs,
    )
    if fifa_league_filter_enabled:
        logger.info(
            "FIFA 赛事筛选: top150=一级联赛+国内杯赛, top40=一级+二级联赛, 国际杯赛放行(enabled=%s) country_map=%d",
            fifa_allow_second_tier_for_top40,
            len(competition_country_by_key),
        )

    external_snapshots: List[Dict] = []
    if external_score_enabled and external_key:
        try:
            external_snapshots = fetch_football_live_scores(
                api_key=external_key,
                cache_ttl_secs=external_cache_ttl,
                timeout_secs=external_timeout,
            )
            external_snapshot_count = len(external_snapshots)
            if external_snapshot_count > 0:
                logger.info("外部足球比分源: snapshots=%d", external_snapshot_count)
        except Exception as exc:
            logger.warning("外部足球比分获取失败: %s", exc)

    for event in live_events:
        if not isinstance(event, dict):
            continue

        event_id = str(event.get("id", ""))
        home_obj = event.get("home") or {}
        away_obj = event.get("away") or {}
        home_name = home_obj.get("name", "?")
        away_name = away_obj.get("name", "?")
        match_name = f"{home_name} vs {away_name}"
        comp_key = str(event.get("_competition_key") or "")
        comp_name = event.get("_competition_name", "") or event.get("competition", "")

        if _contains_blocked_keyword(comp_name, match_name, keywords=competition_block_keywords):
            skipped_for_competition_keyword += 1
            logger.debug("[%s] 命中联赛关键词过滤: %s", match_name, comp_name)
            continue

        country_slug = _normalize_country_slug_for_fifa(
            competition_country_by_key.get(comp_key, "")
        )
        if not country_slug and comp_key:
            country_slug = _normalize_country_slug_for_fifa(
                _extract_country_from_comp_key(comp_key, known_country_slugs)
            )
        league_tier = _infer_domestic_league_tier(comp_key, comp_name, country_slug)

        if fifa_league_filter_enabled:
            allowed, reason = _is_allowed_by_fifa_country_tier(
                country_slug=country_slug,
                tier=league_tier,
                allow_second_tier_for_top40=fifa_allow_second_tier_for_top40,
                comp_key=comp_key,
                comp_name=comp_name,
            )
            if not allowed:
                skipped_for_fifa_filter += 1
                if reason == "country_outside_top150":
                    skipped_for_fifa_country += 1
                elif reason == "tier_not_allowed":
                    skipped_for_fifa_tier += 1
                else:
                    skipped_for_fifa_unknown += 1
                logger.debug(
                    "[%s] FIFA 联赛筛选跳过: reason=%s country=%s tier=%s key=%s",
                    match_name,
                    reason,
                    country_slug or "?",
                    league_tier if league_tier is not None else "?",
                    comp_key or "?",
                )
                continue

        # ── 提取 total_goals 市场 ──────────────────────────
        market = CloudbetClient.extract_total_goals_market(event)
        if not market:
            logger.debug("[%s] 无 total_goals 市场", match_name)
            continue

        over_price = market["over_price"]
        under_price = market["under_price"]
        line = market["line"]

        # ── 赔率快照写库 ───────────────────────────────────
        _update_odds_cache(event_id, over_price, under_price)

        # 同时写入 odds_snapshot 表（供 CLV 回测）
        match_state = _extract_match_state(event)
        goals_home = int(match_state["home_goals"])
        goals_away = int(match_state["away_goals"])
        elapsed = float(match_state["elapsed"])
        score_reliable = bool(match_state["score_reliable"])
        score_source = str(match_state["score_source"])
        score_imputed = False
        external_confidence = None

        if external_snapshots and (external_score_prefer or not score_reliable):
            matched = match_external_score_for_event(
                event_home=home_name,
                event_away=away_name,
                event_kickoff=event.get("cutoffTime") or event.get("startTime"),
                event_competition=comp_name,
                snapshots=external_snapshots,
                min_confidence=external_min_confidence,
                kickoff_tolerance_mins=external_kickoff_tolerance,
            )
            if matched:
                snap = matched["snapshot"]
                goals_home = int(snap.get("home_score") or 0)
                goals_away = int(snap.get("away_score") or 0)
                ext_elapsed = int(snap.get("elapsed_minutes") or 0)
                if ext_elapsed > 0:
                    elapsed = float(ext_elapsed)
                score_reliable = True
                score_source = f"external:{snap.get('source', 'external')}"
                external_confidence = float(matched.get("confidence") or 0.0)
                score_imputed = False
                external_match_count += 1

        if not score_reliable and allow_imputed_score:
            imp_h, imp_a, ok = _impute_score_from_line(
                line=line,
                elapsed=elapsed,
                pre_xg_home=pre_xg_home,
                pre_xg_away=pre_xg_away,
            )
            if ok:
                goals_home, goals_away = imp_h, imp_a
                score_reliable = True
                score_source = "imputed_from_line"
                score_imputed = True

        if require_reliable_score and not score_reliable:
            skipped_for_unreliable_score += 1
            logger.debug("[%s] 比分来源不可靠(%s)，跳过", match_name, score_source)
            continue

        if score_imputed and elapsed < imputed_score_min_elapsed:
            skipped_for_imputed_early += 1
            logger.debug(
                "[%s] 比分为估算且 elapsed=%.1f < %.1f，跳过",
                match_name,
                elapsed,
                imputed_score_min_elapsed,
            )
            continue

        # ?????????? TRADING ???elapsed ??????????
        if elapsed < float(min_elapsed_minutes):
            skipped_for_elapsed += 1
            logger.debug(
                "[%s] elapsed=%.1f < %.1f???",
                match_name,
                elapsed,
                float(min_elapsed_minutes),
            )
            continue

        try:
            live_db.insert_odds_snapshot(
                {
                    "event_id": event_id,
                    "sport": "soccer",
                    "competition": comp_name,
                    "home_team": home_name,
                    "away_team": away_name,
                    "status": str(event.get("status", "UNKNOWN")).upper(),
                    "market_url": market.get("over_url", ""),
                    "line": line,
                    "over_price": over_price,
                    "under_price": under_price,
                    "elapsed_minutes": elapsed,
                    "current_score": goals_home + goals_away,
                },
                db_file=db_file,
            )
        except Exception as exc:
            logger.debug("? odds_snapshot ??: %s", exc)

        # ?? ????? ????????????????????????????????????
        stable, stable_reason = _is_odds_stable(event_id, stable_window, jump_threshold)
        if not stable:
            logger.debug("[%s] 盘口不稳定: %s，跳过", match_name, stable_reason)
            continue

        # ── 比赛时间检查 ──────────────────────────────────
        # 90 分钟正常时间，加时最多按 96 分钟估算（6 分钟补时）
        total_estimated = 90.0 if elapsed <= 90 else 96.0
        remaining_minutes = max(total_estimated - elapsed, 0.0)

        if remaining_minutes < min_remaining:
            logger.debug("[%s] 剩余 %.1f 分钟 < 最低 %.1f，跳过",
                         match_name, remaining_minutes, min_remaining)
            continue

        if _is_elapsed_blocked(elapsed, blocked_elapsed_ranges):
            skipped_for_elapsed_block += 1
            logger.debug("[%s] 分钟 %.1f 命中历史亏损时段过滤，跳过", match_name, elapsed)
            continue

        if comp_name in bad_competitions:
            skipped_for_competition += 1
            st = comp_stats.get(comp_name, {})
            logger.debug(
                "[%s] 联赛风控跳过: %s (n=%s roi=%+.3f wr=%.2f)",
                match_name,
                comp_name,
                st.get("samples", 0),
                float(st.get("roi", 0.0)),
                float(st.get("win_rate", 0.0)),
            )
            continue

        # ── 获取 xG 数据 ──────────────────────────────────
        # _af_fixture_id 需要外部将 Cloudbet 队名与 API-Football 赛程匹配后注入。
        # Cloudbet Feed API 不提供 API-Football fixture_id，默认为 None，
        # 此时 _get_live_xg() 自动回退到基于比分+时间的 xG 估算。
        af_fixture_id = event.get("_af_fixture_id")
        live_xg_h, live_xg_a, game_state = _get_live_xg(
            xg_client, af_fixture_id,
            goals_home, goals_away, elapsed,
            pre_xg_home, pre_xg_away,
        )

        # ── 模型计算 + edge 判断 ──────────────────────────
        signal_info = model.compute_edge(
            market, goals_home, goals_away,
            live_xg_h, live_xg_a, elapsed, game_state
        )

        if signal_info:
            max_edge_seen = max(max_edge_seen, float(signal_info.get("edge") or float("-inf")))
        effective_edge_threshold = edge_threshold + (imputed_score_extra_edge if score_imputed else 0.0)
        if signal_info is None or signal_info["edge"] < effective_edge_threshold:
            skipped_for_edge += 1
            if signal_info:
                logger.debug(
                    "[%s] edge=%.3f < 阈值 %.3f（%s），跳过",
                    match_name,
                    signal_info["edge"],
                    effective_edge_threshold,
                    score_source,
                )
            continue

        market_price = float(signal_info.get("market_price") or 0.0)
        if market_price < min_market_price or market_price > max_market_price:
            skipped_for_price += 1
            logger.debug(
                "[%s] 赔率 %.3f 不在策略区间 [%.2f, %.2f]，跳过",
                match_name,
                market_price,
                min_market_price,
                max_market_price,
            )
            continue

        side = str(signal_info.get("side") or "").lower()
        recent_best_price = _recent_best_price(
            event_id=event_id,
            side=side,
            window_secs=entry_price_window_secs,
        )
        if _is_price_chasing_recent_best(
            current_price=market_price,
            recent_best_price=recent_best_price,
            worse_tolerance=entry_price_worse_tolerance,
        ):
            skipped_for_price_chasing += 1
            logger.debug(
                "[%s] 追坏价过滤: side=%s 当前=%.3f 最近最佳=%.3f 容忍=%.1f%%",
                match_name,
                side,
                market_price,
                float(recent_best_price or 0.0),
                entry_price_worse_tolerance * 100.0,
            )
            continue

        robust_edge = _robust_edge_after_price_drop(
            model_prob=float(signal_info.get("model_prob") or 0.0),
            market_price=market_price,
            adverse_price_delta=edge_robust_price_delta,
        )
        if robust_edge < edge_robust_min:
            skipped_for_edge_fragile += 1
            logger.debug(
                "[%s] 脆弱 edge 过滤: edge=%.3f robust=%.3f 阈值=%.3f (delta=%.2f)",
                match_name,
                float(signal_info.get("edge") or 0.0),
                robust_edge,
                edge_robust_min,
                edge_robust_price_delta,
            )
            continue

        # ── Kelly 仓位 ────────────────────────────────────
        stake = kelly_stake(
            edge=signal_info["edge"],
            model_prob=signal_info["model_prob"],
            odds=market_price,
            bankroll=bankroll,
            fraction=kelly_fraction,
            max_pct=max_stake_pct,
            min_stake=min_stake,
        )
        if score_imputed:
            stake = max(min_stake, round(float(stake) * imputed_score_stake_mult, 2))

        # 检查平台 maxStake 限制
        max_stake = signal_info.get("max_stake", 9999)
        if stake > max_stake:
            stake = max_stake
            logger.debug("[%s] stake 被 maxStake=%.2f 限制", match_name, max_stake)

        signals.append(
            {
                "event_id": event_id,
                "match": match_name,
                "competition": comp_name,
                "country_slug": country_slug,
                "league_tier": league_tier,
                "home": home_name,
                "away": away_name,
                "sport": "soccer",
                "line": line,
                "side": signal_info["side"],
                "over_price": over_price,
                "under_price": under_price,
                "market_price": signal_info["market_price"],
                "market_url": signal_info["market_url"],
                "stake": stake,
                "edge": signal_info["edge"],
                "edge_robust": round(float(robust_edge), 4),
                "model_prob": signal_info["model_prob"],
                "mkt_prob": signal_info["mkt_prob"],
                "fair_price": signal_info["fair_price"],
                "recent_best_price": recent_best_price,
                "max_stake": signal_info.get("max_stake", 9999),
                "min_stake": signal_info.get("min_stake", min_stake),
                "elapsed_minutes": elapsed,
                "remaining_minutes": round(remaining_minutes, 1),
                "goals_home": goals_home,
                "goals_away": goals_away,
                "score_source": score_source,
                "score_imputed": score_imputed,
                "external_score_confidence": external_confidence,
                "live_xg_home": round(live_xg_h, 3),
                "live_xg_away": round(live_xg_a, 3),
                "game_state": game_state,
                "model_result": signal_info["model_result"],
                "stable_reason": stable_reason,
            }
        )

    # 按 edge 降序排列
    if not signals and skipped_for_elapsed > 0 and not cfg.get("af_key"):
        logger.warning(
            "?? Feed ?????????/???elapsed<%.1f: %d/%d?????? API_FOOTBALL_KEY????????",
            float(min_elapsed_minutes),
            skipped_for_elapsed,
            len(live_events),
        )

    signals.sort(key=lambda s: s["edge"], reverse=True)
    logger.info(
        "足球扫描完成: %d 场直播 → %d 个候选信号 (ext:%d/%d skip:score=%d imputed_early=%d elapsed=%d edge=%d fragile=%d chase=%d block=%d comp=%d comp_kw=%d fifa=%d[country=%d tier=%d unknown=%d] price=%d max_edge=%.3f)",
        len(live_events),
        len(signals),
        external_match_count,
        external_snapshot_count,
        skipped_for_unreliable_score,
        skipped_for_imputed_early,
        skipped_for_elapsed,
        skipped_for_edge,
        skipped_for_edge_fragile,
        skipped_for_price_chasing,
        skipped_for_elapsed_block,
        skipped_for_competition,
        skipped_for_competition_keyword,
        skipped_for_fifa_filter,
        skipped_for_fifa_country,
        skipped_for_fifa_tier,
        skipped_for_fifa_unknown,
        skipped_for_price,
        (max_edge_seen if max_edge_seen != float("-inf") else 0.0),
    )
    return signals


def log_soccer_signal(signal: Dict) -> None:
    """格式化输出一条足球信号摘要"""
    gs = signal.get("game_state", {})
    rc_str = ""
    if gs.get("red_cards_home") or gs.get("red_cards_away"):
        rc_str = f" | 红牌H={gs['red_cards_home']}/A={gs['red_cards_away']}"

    logger.info(
        "⚽ [%s] %s | %d'-%-4d'剩 | %d-%d | xG=%.2f/%.2f%s",
        signal["competition"][:20],
        signal["match"],
        signal["elapsed_minutes"],
        int(signal["remaining_minutes"]),
        signal["goals_home"],
        signal["goals_away"],
        signal["live_xg_home"],
        signal["live_xg_away"],
        rc_str,
    )
    logger.info(
        "   → 线=%.1f | %s | edge=%+.3f | 模型=%.3f vs 市场=%.3f | 赔率=%.3f | 注=%.2f",
        signal["line"],
        signal["side"].upper(),
        signal["edge"],
        signal["model_prob"],
        signal["mkt_prob"],
        signal["market_price"],
        signal["stake"],
    )
