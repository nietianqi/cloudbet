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
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from nba_model import compute_live_total_edge, pick_best_side, kelly_stake
from cloudbet_client import CloudbetClient, CloudbetAPIError

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
    "MAX_STAKE_PCT": 0.005,       # 单注最大 0.5% 资金
    "MIN_STAKE": 1.0,             # 最小注额
}

# 全局赔率历史缓存（event_id → [(ts, over_price, under_price), ...]）
_odds_cache: Dict[str, List[Tuple[float, float, float]]] = defaultdict(list)
_CACHE_TTL = 300  # 5 分钟后清理过时事件

# ── API 相关 ──────────────────────────────────────────────────
EVENTS_URL = "https://sports-api.cloudbet.com/pub/v2/odds/events"
BET_HISTORY_URL = "https://sports-api.cloudbet.com/pub/v4/bets/history"


def fetch_live_basketball_events(api_key: str) -> List[Dict]:
    """
    拉取所有 TRADING_LIVE 篮球赛事

    说明：
      直接请求 /pub/v2/odds/events 在部分参数组合下会触发 400。
      这里改为“先拉 basketball competitions，再逐联赛拉 TRADING_LIVE”
      的稳定路径，与 soccer 策略保持一致。

    返回：competition list（原始 API 结构）
    """
    client = CloudbetClient(api_key)
    competitions: List[Dict] = []

    try:
        comp_resp = client.get_competitions("basketball")
    except CloudbetAPIError as exc:
        logger.warning("拉取篮球联赛失败: %s", exc)
        return []

    comp_keys = [
        c.get("key", "")
        for c in comp_resp.get("competitions", [])
        if c.get("key") and "virtual" not in c.get("name", "").lower()
    ]

    for comp_key in comp_keys:
        try:
            data = client.get_events(
                comp_key,
                markets=["basketball.totals"],
                status="TRADING_LIVE",
            )
        except CloudbetAPIError as exc:
            logger.debug("扫描联赛失败 %s: %s", comp_key, exc)
            continue

        events = [e for e in data.get("events", []) if e.get("status") == "TRADING_LIVE"]
        if not events:
            continue

        competitions.append(
            {
                "key": comp_key,
                "name": data.get("name", comp_key),
                "events": events,
            }
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

    competitions = fetch_live_basketball_events(api_key)
    if not competitions:
        logger.info("无直播篮球赛事")
        return []

    signals = []

    for comp in competitions:
        comp_name = comp.get("name", "")
        if "virtual" in comp_name.lower():
            continue

        for event in comp.get("events", []):
            event_id = str(event.get("id", ""))
            home_name = event.get("home", {}).get("name", "N/A")
            away_name = event.get("away", {}).get("name", "N/A")
            match_name = f"{home_name} vs {away_name}"
            status = event.get("status", "")

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
            if current_score is None:
                # 无法获取实时比分，用先验节奏估算（保守处理）
                expected_so_far = (elapsed_minutes / 48.0) * line
                current_score = round(expected_so_far)
                logger.debug(
                    "[%s] 无实时比分，估算当前总分=%d", match_name, current_score
                )

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
            signal_info = pick_best_side(model_result, min_edge=edge_threshold)
            if signal_info is None:
                logger.debug(
                    "[%s] 无信号: edge_over=%.3f edge_under=%.3f",
                    match_name,
                    model_result["edge_over"],
                    model_result["edge_under"],
                )
                continue

            side = signal_info["side"]
            market_price = over_price if side == "over" else under_price
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
            )
            min_stake = cfg.get("MIN_STAKE", 1.0)
            if stake < min_stake:
                stake = min_stake   # 满足平台最小注额

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
                    "model_result": model_result,
                    "stable_reason": stable_reason,
                }
            )

    # 按 edge 降序排列，优先执行最强信号
    signals.sort(key=lambda s: s["edge"], reverse=True)
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
