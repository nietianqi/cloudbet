"""
NBA 直播总分策略 — 信号生成模块
==================================
职责：
  1. 从 Cloudbet API 拉取篮球直播赛事
  2. 提取 totals（大小分）市场赔率 + 盘口
  3. 估算已进行时间 + 当前总得分（从 API 响应中解析）
  4. 调用 nba_model 计算 edge
  5. 应用入场规则过滤，输出候选信号列表

入场规则（同时满足）:
  - event.status == TRADING_LIVE
  - 距比赛名义结束时间 remaining_minutes >= MIN_REMAINING
  - 盘口最近 STABLE_WINDOW 秒内未剧烈跳线（通过历史缓存判断）
  - edge >= EDGE_THRESHOLD
  - 若能获取 maxStake，stake <= maxStake

API 说明:
  - 使用 Trading key（实时，非 Affiliate Feed 缓存）
  - GET /pub/v2/odds/events?sport=basketball&status=TRADING_LIVE
  - 事件数据中的 scores / periods 字段包含实时比分（若平台有返回）
  - 若比分不在 API 内，回退到用 cutoffTime + 当前时间推算进度
"""

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from nba_model import compute_live_total_edge, pick_best_side, kelly_stake
from cloudbet_client import CloudbetClient, CloudbetAPIError
from external_scores import fetch_basketball_live_scores, match_external_score_for_event

logger = logging.getLogger(__name__)

# ── 需要从 config 导入的参数（稍后 nba_bot 统一引入）────────────
# 这里用默认值，nba_bot 会覆盖
_DEFAULT_CONFIG = {
    "API_KEY": "",
    "CURRENCY": "PLAY_EUR",
    "EDGE_THRESHOLD": 0.05,       # 5% edge 才入场
    "MIN_REMAINING_MINUTES": 6.0, # 至少剩余 6 分钟
    "STABLE_WINDOW_SECS": 20,     # 20 秒内盘口不能大跳
    "JUMP_THRESHOLD": 0.08,       # 盘口跳动 > 0.08 视为不稳定
    "PRIOR_WEIGHT": 0.45,         # 贝叶斯先验权重
    "KELLY_FRACTION": 0.25,       # 1/4 Kelly
    "MAX_STAKE_PCT": 0.01,        # 单注最大 1.0% 资金
    "MIN_STAKE": 1.0,             # 最小注额
    "EXTERNAL_SCORE_ENABLED": True,
    "EXTERNAL_SCORE_PREFER": True,
    "EXTERNAL_BASKETBALL_KEY": "",
    "EXTERNAL_SCORE_MIN_CONFIDENCE": 0.80,
    "EXTERNAL_SCORE_KICKOFF_TOLERANCE_MINS": 240,
    "EXTERNAL_SCORE_CACHE_TTL_SECS": 45,
    "EXTERNAL_SCORE_TIMEOUT_SECS": 10,
}

# 全局赔率历史缓存（event_id → [(ts, over_price, under_price), ...]）
_odds_cache: Dict[str, List[Tuple[float, float, float]]] = defaultdict(list)
_CACHE_TTL = 300  # 5 分钟后清理过时事件

# ── API 相关 ──────────────────────────────────────────────────
EVENTS_URL = "https://sports-api.cloudbet.com/pub/v2/odds/events"
BET_HISTORY_URL = "https://sports-api.cloudbet.com/pub/v4/bets/history"


def fetch_live_basketball_events(
    api_key: str,
    live_statuses: Optional[List[str]] = None,
    bulk_from_hours: int = 4,
    bulk_to_hours: int = 2,
    prefer_bulk_events_api: bool = True,
    fallback_to_league_scan_on_bulk_failure: bool = True,
) -> List[Dict]:
    """
    ??????????? bulk events??????????????
    """
    client = CloudbetClient(api_key)

    if live_statuses is None:
        live_statuses = ["TRADING_LIVE"]
    allowed_statuses = {str(s).upper() for s in live_statuses if s}

    if prefer_bulk_events_api:
        try:
            now_ts = int(time.time())
            payload = client.get_events_by_time(
                sport_key="basketball",
                from_ts=now_ts - int(bulk_from_hours * 3600),
                to_ts=now_ts + int(bulk_to_hours * 3600),
                markets=["basketball.totals"],
            )
            raw_comps = payload.get("competitions", []) or []
            total_comps = len(raw_comps)
            scanned_events = 0
            matched_events = 0
            status_counter: Counter = Counter()
            competitions: List[Dict] = []
            scan_start = time.time()

            logger.info(
                "Basketball bulk scan started: competitions=%d window=-%dh/+%dh allowed=%s",
                total_comps,
                bulk_from_hours,
                bulk_to_hours,
                sorted(allowed_statuses) if allowed_statuses else "ALL",
            )

            for idx, comp in enumerate(raw_comps, start=1):
                comp_key = comp.get("key") or ""
                comp_name = comp.get("name") or comp_key
                kept: List[Dict] = []

                for event in comp.get("events", []) or []:
                    status = str(event.get("status", "")).upper()
                    scanned_events += 1
                    if status:
                        status_counter[status] += 1
                    if allowed_statuses and status not in allowed_statuses:
                        continue
                    event["_competition_key"] = comp_key
                    event["_competition_name"] = comp_name
                    kept.append(event)
                    matched_events += 1

                if kept:
                    competitions.append({"key": comp_key, "name": comp_name, "events": kept})

                if idx % 20 == 0:
                    logger.info(
                        "Basketball bulk progress: %d/%d competitions events=%d matched=%d elapsed=%.1fs",
                        idx,
                        total_comps,
                        scanned_events,
                        matched_events,
                        time.time() - scan_start,
                    )

            logger.info(
                "Basketball bulk scan: competitions=%d events=%d matched=%d allowed=%s seen=%s elapsed=%.1fs",
                total_comps,
                scanned_events,
                matched_events,
                sorted(allowed_statuses) if allowed_statuses else "ALL",
                dict(status_counter),
                time.time() - scan_start,
            )
            return competitions
        except CloudbetAPIError as exc:
            if not fallback_to_league_scan_on_bulk_failure:
                logger.warning("Basketball bulk scan failed, skip this round: %s", exc)
                return []
            logger.warning("Basketball bulk scan failed, fallback to league scan: %s", exc)

    try:
        comp_resp = client.get_competitions("basketball")
    except CloudbetAPIError as exc:
        logger.warning("????????: %s", exc)
        return []

    comp_keys = CloudbetClient.extract_competition_keys(comp_resp)
    total_leagues = len(comp_keys)
    scanned_events = 0
    matched_events = 0
    status_counter: Counter = Counter()
    competitions: List[Dict] = []
    scan_start = time.time()

    logger.info(
        "Basketball league scan started: leagues=%d allowed=%s",
        total_leagues,
        sorted(allowed_statuses) if allowed_statuses else "ALL",
    )

    for idx, comp_key in enumerate(comp_keys, start=1):
        try:
            data = client.get_events(comp_key, markets=["basketball.totals"], status=None)
        except CloudbetAPIError as exc:
            logger.debug("?????? %s: %s", comp_key, exc)
            continue

        kept: List[Dict] = []
        for event in data.get("events", []) or []:
            status = str(event.get("status", "")).upper()
            scanned_events += 1
            if status:
                status_counter[status] += 1
            if allowed_statuses and status not in allowed_statuses:
                continue
            event["_competition_key"] = comp_key
            event["_competition_name"] = data.get("name", comp_key)
            kept.append(event)
            matched_events += 1

        if kept:
            competitions.append(
                {
                    "key": comp_key,
                    "name": data.get("name", comp_key),
                    "events": kept,
                }
            )

        if idx % 20 == 0:
            logger.info(
                "Basketball league progress: %d/%d leagues events=%d matched=%d elapsed=%.1fs",
                idx,
                total_leagues,
                scanned_events,
                matched_events,
                time.time() - scan_start,
            )

    logger.info(
        "Basketball league scan: leagues=%d events=%d matched=%d allowed=%s seen=%s elapsed=%.1fs",
        total_leagues,
        scanned_events,
        matched_events,
        sorted(allowed_statuses) if allowed_statuses else "ALL",
        dict(status_counter),
        time.time() - scan_start,
    )
    return competitions
def _extract_totals_market(markets: Dict) -> Optional[Dict]:
    """
    从市场字典中提取 basketball.totals（大小分）市场数据

    返回:
        {line, over_price, under_price, market_url_over, market_url_under}
        或 None
    """
    from urllib.parse import parse_qs

    for market_key, market in markets.items():
        if "totals" not in market_key.lower():
            continue

        for _, sub in market.get("submarkets", {}).items():
            over_price = under_price = None
            over_url = under_url = ""
            line = None
            line_key = "points"

            for sel in sub.get("selections", []):
                if sel.get("status") not in (None, "SELECTION_ENABLED"):
                    continue

                outcome = str(sel.get("outcome", "")).lower()
                if outcome not in ("over", "under"):
                    continue

                try:
                    price = float(sel.get("price"))
                except (TypeError, ValueError):
                    continue

                params = str(sel.get("params", ""))
                if line is None and params:
                    qs = parse_qs(params)
                    for key in ("points", "total", "line"):
                        if key in qs:
                            try:
                                line = float(qs[key][0])
                                line_key = key
                                break
                            except (TypeError, ValueError, IndexError):
                                continue

                if outcome == "over":
                    over_price = price
                    over_url = str(sel.get("url") or "")
                else:
                    under_price = price
                    under_url = str(sel.get("url") or "")

            if over_price is None or under_price is None or line is None:
                continue

            if not over_url:
                over_url = f"{market_key}/over?{line_key}={line}"
            if not under_url:
                under_url = f"{market_key}/under?{line_key}={line}"

            return {
                "line": line,
                "over_price": over_price,
                "under_price": under_price,
                "market_key": market_key,
                "market_url_over": over_url,
                "market_url_under": under_url,
            }
    return None

def _parse_current_score(event: Dict) -> Optional[int]:
    """
    尝试从 event 数据中解析当前总得分（home + away）

    Cloudbet API 可能在 'scores'、'home'/'away' score 字段或 'periods' 中返回比分。
    若无法解析则返回 None（调用方将跳过该赛事或使用估算值）。
    """
    # 方式 1: event.scores 字典（部分联赛有）
    scores = event.get("scores", {})
    if scores:
        home_score = scores.get("home") or scores.get("1")
        away_score = scores.get("away") or scores.get("2")
        if home_score is not None and away_score is not None:
            try:
                return int(home_score) + int(away_score)
            except (ValueError, TypeError):
                pass

    # 方式 2: event.home.score / event.away.score
    home_s = event.get("home", {}).get("score")
    away_s = event.get("away", {}).get("score")
    if home_s is not None and away_s is not None:
        try:
            return int(home_s) + int(away_s)
        except (ValueError, TypeError):
            pass

    # 方式 3: periods 累计
    periods = event.get("periods", [])
    if periods:
        try:
            total = sum(
                int(p.get("homeScore", 0)) + int(p.get("awayScore", 0))
                for p in periods
                if p.get("homeScore") is not None
            )
            if total > 0:
                return total
        except (ValueError, TypeError):
            pass

    return None


def _estimate_elapsed_minutes(event: Dict) -> float:
    """
    估算已进行分钟数

    优先使用 event.clock / elapsedTime；退而使用 cutoffTime（开赛时间）推算。
    NBA 常规时间 48 分钟，超过按 48 处理（进加时）。
    """
    # 方式 1: API 直接给出 clock / elapsedSeconds
    clock = event.get("clock", {})
    elapsed_secs = clock.get("elapsedSeconds") or clock.get("elapsed")
    if elapsed_secs is not None:
        try:
            return min(float(elapsed_secs) / 60.0, 48.0)
        except (ValueError, TypeError):
            pass

    # 方式 2: 用开赛时间推算（粗略）
    cutoff = event.get("cutoffTime")
    if cutoff:
        try:
            kickoff_dt = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            elapsed_mins = (now_dt - kickoff_dt).total_seconds() / 60.0
            # 限制在合理范围内
            return max(0.0, min(elapsed_mins, 48.0))
        except (ValueError, TypeError):
            pass

    return 0.0


def _is_odds_stable(event_id: str, over_price: float,
                    under_price: float, window_secs: float,
                    jump_threshold: float) -> Tuple[bool, str]:
    """
    检查最近 window_secs 秒内盘口是否稳定

    返回:
        (is_stable, reason)
    """
    history = _odds_cache.get(event_id, [])
    if len(history) < 2:
        return True, "数据不足，默认稳定"

    now = time.time()
    recent = [(ts, op, up) for ts, op, up in history if now - ts <= window_secs]
    if len(recent) < 2:
        return True, "窗口内数据不足"

    over_prices = [op for _, op, _ in recent]
    under_prices = [up for _, _, up in recent]
    over_jump = max(over_prices) - min(over_prices)
    under_jump = max(under_prices) - min(under_prices)
    max_jump = max(over_jump, under_jump)
    if max_jump > jump_threshold:
        side = "Over" if over_jump >= under_jump else "Under"
        return False, f"{side} 盘口跳动 {max_jump:.3f} > {jump_threshold}"

    return True, f"稳定 (最大跳动 {max_jump:.3f})"


def _update_odds_cache(event_id: str, over_price: float, under_price: float) -> None:
    """更新赔率历史缓存，清理过时数据"""
    now = time.time()
    _odds_cache[event_id].append((now, over_price, under_price))
    # 只保留最近 5 分钟
    cutoff = now - _CACHE_TTL
    _odds_cache[event_id] = [
        (ts, op, up) for ts, op, up in _odds_cache[event_id] if ts >= cutoff
    ]
    # 清理长时间没有更新的赛事（先收集 key，再删除，避免迭代中修改字典）
    stale_events = [
        eid for eid, hist in _odds_cache.items()
        if not hist or (now - hist[-1][0]) > _CACHE_TTL
    ]
    for eid in stale_events:
        _odds_cache.pop(eid, None)


def _contains_blocked_keyword(*values: str, keywords: Optional[List[str]] = None) -> bool:
    if not keywords:
        return False
    lowered_keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
    if not lowered_keywords:
        return False
    combined = " ".join(str(v or "").lower() for v in values)
    return any(k in combined for k in lowered_keywords)


def generate_signals(cfg: Dict) -> List[Dict]:
    """
    主信号生成函数 — 每个轮询周期调用一次

    参数:
        cfg: 配置字典（来自 nba_bot，合并了 _DEFAULT_CONFIG）

    返回:
        signals: 候选信号列表，每项包含完整的入场信息
    """
    api_key = cfg["API_KEY"]
    edge_threshold = cfg["EDGE_THRESHOLD"]
    min_remaining = cfg["MIN_REMAINING_MINUTES"]
    stable_window = cfg["STABLE_WINDOW_SECS"]
    jump_threshold = cfg["JUMP_THRESHOLD"]
    prior_weight = cfg["PRIOR_WEIGHT"]
    kelly_fraction = cfg["KELLY_FRACTION"]
    max_stake_pct = cfg["MAX_STAKE_PCT"]
    bankroll = cfg.get("BANKROLL", 100.0)
    require_reliable_score = bool(cfg.get("REQUIRE_RELIABLE_SCORE", True))
    blocked_keywords = cfg.get("COMPETITION_BLOCK_KEYWORDS", [])
    min_market_price = float(cfg.get("MIN_MARKET_PRICE", 1.65))
    max_market_price = float(cfg.get("MAX_MARKET_PRICE", 2.20))
    imputed_score_min_elapsed = float(cfg.get("IMPUTED_SCORE_MIN_ELAPSED", 6.0))
    imputed_score_extra_edge = float(cfg.get("IMPUTED_SCORE_EXTRA_EDGE", 0.03))
    imputed_score_stake_mult = max(0.1, min(1.0, float(cfg.get("IMPUTED_SCORE_STAKE_MULT", 0.5))))
    external_score_enabled = bool(cfg.get("EXTERNAL_SCORE_ENABLED", True))
    external_score_prefer = bool(cfg.get("EXTERNAL_SCORE_PREFER", True))
    external_key = str(cfg.get("EXTERNAL_BASKETBALL_KEY") or cfg.get("EXTERNAL_SCORE_KEY") or "")
    external_min_confidence = float(cfg.get("EXTERNAL_SCORE_MIN_CONFIDENCE", 0.80))
    external_kickoff_tolerance = int(cfg.get("EXTERNAL_SCORE_KICKOFF_TOLERANCE_MINS", 240))
    external_cache_ttl = int(cfg.get("EXTERNAL_SCORE_CACHE_TTL_SECS", 45))
    external_timeout = int(cfg.get("EXTERNAL_SCORE_TIMEOUT_SECS", 10))
    if max_market_price < min_market_price:
        min_market_price, max_market_price = max_market_price, min_market_price

    competitions = fetch_live_basketball_events(
        api_key,
        live_statuses=cfg.get("LIVE_STATUSES", ["TRADING_LIVE"]),
        bulk_from_hours=cfg.get("BULK_FROM_HOURS", 4),
        bulk_to_hours=cfg.get("BULK_TO_HOURS", 2),
        prefer_bulk_events_api=cfg.get("PREFER_BULK_EVENTS_API", True),
        fallback_to_league_scan_on_bulk_failure=cfg.get(
            "FALLBACK_TO_LEAGUE_SCAN_ON_BULK_FAILURE", True
        ),
    )
    if not competitions:
        logger.info("无直播篮球赛事")
        return []

    signals = []
    skipped_for_comp_keyword = 0
    skipped_for_unreliable_score = 0
    skipped_for_imputed_early = 0
    skipped_for_price = 0
    skipped_for_edge = 0
    external_match_count = 0
    external_snapshot_count = 0
    max_edge_seen = float("-inf")
    external_snapshots: List[Dict] = []

    if external_score_enabled and external_key:
        try:
            external_snapshots = fetch_basketball_live_scores(
                api_key=external_key,
                cache_ttl_secs=external_cache_ttl,
                timeout_secs=external_timeout,
            )
            external_snapshot_count = len(external_snapshots)
            if external_snapshot_count > 0:
                logger.info("External basketball scores loaded: snapshots=%d", external_snapshot_count)
        except Exception as exc:
            logger.warning("External basketball score fetch failed: %s", exc)

    for comp in competitions:
        comp_name = comp.get("name", "")
        if _contains_blocked_keyword(comp_name, keywords=blocked_keywords):
            continue

        for event in comp.get("events", []):
            event_id = str(event.get("id", ""))
            home_name = event.get("home", {}).get("name", "N/A")
            away_name = event.get("away", {}).get("name", "N/A")
            match_name = f"{home_name} vs {away_name}"
            status = event.get("status", "")

            if _contains_blocked_keyword(comp_name, match_name, keywords=blocked_keywords):
                skipped_for_comp_keyword += 1
                continue

            if status != "TRADING_LIVE":
                continue

            # ── 提取 totals 市场 ──────────────────────────────
            markets_data = _extract_totals_market(event.get("markets", {}))
            if not markets_data:
                logger.debug("[%s] 无 totals 市场", match_name)
                continue

            line = markets_data["line"]
            over_price = markets_data["over_price"]
            under_price = markets_data["under_price"]

            # ── 更新赔率缓存 ──────────────────────────────────
            _update_odds_cache(event_id, over_price, under_price)

            # ── 稳定性检查 ────────────────────────────────────
            stable, stable_reason = _is_odds_stable(
                event_id, over_price, under_price, stable_window, jump_threshold
            )
            if not stable:
                logger.debug("[%s] 盘口不稳定: %s", match_name, stable_reason)
                continue

            # ── 时间 & 比分 ───────────────────────────────────
            elapsed_minutes = _estimate_elapsed_minutes(event)
            remaining_minutes = max(48.0 - elapsed_minutes, 0.0)

            if remaining_minutes < min_remaining:
                logger.debug(
                    "[%s] 剩余时间不足 %.1f 分钟 (需 ≥ %.1f)",
                    match_name, remaining_minutes, min_remaining
                )
                continue

            current_score = _parse_current_score(event)
            score_reliable = current_score is not None
            score_imputed = False
            score_source = "feed" if score_reliable else "missing"
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
                    home_score = int(snap.get("home_score") or 0)
                    away_score = int(snap.get("away_score") or 0)
                    current_score = home_score + away_score
                    ext_elapsed = int(snap.get("elapsed_minutes") or 0)
                    if ext_elapsed > 0:
                        elapsed_minutes = min(float(ext_elapsed), 48.0)
                        remaining_minutes = max(48.0 - elapsed_minutes, 0.0)
                    score_reliable = True
                    score_source = f"external:{snap.get('source', 'external')}"
                    external_confidence = float(matched.get("confidence") or 0.0)
                    external_match_count += 1
            if not score_reliable and require_reliable_score:
                skipped_for_unreliable_score += 1
                continue
            if current_score is None:
                if elapsed_minutes < imputed_score_min_elapsed:
                    skipped_for_imputed_early += 1
                    continue
                expected_so_far = (elapsed_minutes / 48.0) * line
                current_score = round(expected_so_far)
                score_imputed = True
                score_source = "imputed_from_line"
                logger.debug("[%s] 无实时比分，估算当前总分=%d", match_name, current_score)

            # ── 模型计算 ──────────────────────────────────────
            try:
                model_result = compute_live_total_edge(
                    pregame_line=line,
                    current_score=current_score,
                    elapsed_minutes=elapsed_minutes,
                    cloudbet_over_price=over_price,
                    cloudbet_under_price=under_price,
                    prior_weight=prior_weight,
                )
            except Exception as exc:
                logger.error("[%s] 模型计算异常: %s", match_name, exc)
                continue

            # ── 信号筛选 ──────────────────────────────────────
            effective_edge_threshold = edge_threshold + (imputed_score_extra_edge if score_imputed else 0.0)
            signal_info = pick_best_side(model_result, min_edge=effective_edge_threshold)
            if signal_info is None:
                max_edge_seen = max(
                    max_edge_seen,
                    float(model_result.get("edge_over") or float("-inf")),
                    float(model_result.get("edge_under") or float("-inf")),
                )
                skipped_for_edge += 1
                logger.debug(
                    "[%s] 无信号: edge_over=%.3f edge_under=%.3f",
                    match_name,
                    model_result["edge_over"],
                    model_result["edge_under"],
                )
                continue

            side = signal_info["side"]
            market_price = over_price if side == "over" else under_price
            if market_price < min_market_price or market_price > max_market_price:
                skipped_for_price += 1
                continue
            market_url = (
                markets_data["market_url_over"]
                if side == "over"
                else markets_data["market_url_under"]
            )

            # ── Kelly 仓位 ────────────────────────────────────
            stake = kelly_stake(
                edge=signal_info["edge"],
                odds=market_price,
                bankroll=bankroll,
                fraction=kelly_fraction,
                max_pct=max_stake_pct,
                model_prob=signal_info.get("model_prob"),
            )
            min_stake = cfg.get("MIN_STAKE", 1.0)
            if stake < min_stake:
                stake = min_stake   # 满足平台最小注额
            if score_imputed:
                stake = max(min_stake, round(float(stake) * imputed_score_stake_mult, 2))

            signals.append(
                {
                    "event_id": event_id,
                    "match": match_name,
                    "competition": comp_name,
                    "home": home_name,
                    "away": away_name,
                    "line": line,
                    "side": side,
                    "over_price": over_price,
                    "under_price": under_price,
                    "market_price": market_price,
                    "market_url": market_url,
                    "stake": stake,
                    "edge": signal_info["edge"],
                    "model_prob": signal_info["model_prob"],
                    "mkt_prob": signal_info["mkt_prob"],
                    "fair_price": signal_info["fair_price"],
                    "elapsed_minutes": round(elapsed_minutes, 1),
                    "remaining_minutes": round(remaining_minutes, 1),
                    "current_score": current_score,
                    "score_reliable": score_reliable,
                    "score_source": score_source,
                    "score_imputed": score_imputed,
                    "external_score_confidence": external_confidence,
                    "model_result": model_result,
                    "stable_reason": stable_reason,
                }
            )

    # 按 edge 降序排列，优先执行最强信号
    signals.sort(key=lambda s: s["edge"], reverse=True)
    logger.info(
        "Basketball signal scan: candidates=%d (ext:%d/%d skip:comp_kw=%d score=%d imputed_early=%d edge=%d price=%d max_edge=%.3f)",
        len(signals),
        external_match_count,
        external_snapshot_count,
        skipped_for_comp_keyword,
        skipped_for_unreliable_score,
        skipped_for_imputed_early,
        skipped_for_edge,
        skipped_for_price,
        (max_edge_seen if max_edge_seen != float("-inf") else 0.0),
    )
    return signals


def log_signal_summary(signal: Dict) -> None:
    """打印一条信号的摘要（供 nba_bot 调用）"""
    logger.info(
        "🎯 [%s] %s | 盘口=%.1f | 方向=%s | edge=%+.3f | "
        "模型=%.3f vs 市场=%.3f | 已过=%.1f分钟 | 建议注=%.2f",
        signal["competition"],
        signal["match"],
        signal["line"],
        signal["side"].upper(),
        signal["edge"],
        signal["model_prob"],
        signal["mkt_prob"],
        signal["elapsed_minutes"],
        signal["stake"],
    )
