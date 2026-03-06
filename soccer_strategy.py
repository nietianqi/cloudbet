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
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from cloudbet_client import CloudbetClient
from soccer_model import InPlayGoalsModel, kelly_stake
from xg_client import create_xg_client, estimate_xg_from_score_and_time
import live_db

logger = logging.getLogger(__name__)

# ── 赔率历史缓存（用于稳定性检测）────────────────────────────
# {event_id: [(timestamp, over_price, under_price), ...]}
_odds_cache: Dict[str, List[Tuple[float, float, float]]] = defaultdict(list)
_CACHE_TTL = 300   # 5 分钟后清理过期赛事

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

    if af_fixture_id and not isinstance(xg_client, type(None)):
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
    pre_xg_home = cfg.get("pre_xg_home", _DEFAULT_PRE_XG_HOME)
    pre_xg_away = cfg.get("pre_xg_away", _DEFAULT_PRE_XG_AWAY)
    live_weight = cfg.get("prior_weight_live", 0.65)
    db_file = cfg.get("db_file", "live_betting.db")
    # None = 扫描全量足球联赛（由 CloudbetClient.get_all_live_soccer 内部处理）
    leagues = cfg.get("leagues")

    # 拉取所有直播赛事
    live_events = client.get_all_live_soccer(
        markets=["soccer.total_goals", "soccer.match_odds"],
        priority_leagues=leagues,
    )

    if not live_events:
        logger.info("足球: 无直播赛事")
        return []

    model = InPlayGoalsModel(pre_xg_home, pre_xg_away, live_weight=live_weight)
    signals = []

    for event in live_events:
        event_id = str(event.get("id", ""))
        home_name = event.get("home", {}).get("name", "?")
        away_name = event.get("away", {}).get("name", "?")
        match_name = f"{home_name} vs {away_name}"
        comp_name = event.get("_competition_name", "")

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
        try:
            goals_home, goals_away, elapsed = CloudbetClient.extract_match_score(event)
            live_db.insert_odds_snapshot(
                {
                    "event_id": event_id,
                    "sport": "soccer",
                    "competition": comp_name,
                    "home_team": home_name,
                    "away_team": away_name,
                    "status": "TRADING_LIVE",
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
            logger.debug("写 odds_snapshot 失败: %s", exc)

        # ── 稳定性检查 ────────────────────────────────────
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

        if signal_info is None or signal_info["edge"] < edge_threshold:
            if signal_info:
                logger.debug(
                    "[%s] edge=%.3f < 阈值 %.3f，跳过",
                    match_name, signal_info["edge"], edge_threshold
                )
            continue

        # ── Kelly 仓位 ────────────────────────────────────
        stake = kelly_stake(
            edge=signal_info["edge"],
            model_prob=signal_info["model_prob"],
            odds=signal_info["market_price"],
            bankroll=bankroll,
            fraction=kelly_fraction,
            max_pct=max_stake_pct,
            min_stake=min_stake,
        )

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
                "model_prob": signal_info["model_prob"],
                "mkt_prob": signal_info["mkt_prob"],
                "fair_price": signal_info["fair_price"],
                "elapsed_minutes": elapsed,
                "remaining_minutes": round(remaining_minutes, 1),
                "goals_home": goals_home,
                "goals_away": goals_away,
                "live_xg_home": round(live_xg_h, 3),
                "live_xg_away": round(live_xg_a, 3),
                "game_state": game_state,
                "model_result": signal_info["model_result"],
                "stable_reason": stable_reason,
            }
        )

    # 按 edge 降序排列
    signals.sort(key=lambda s: s["edge"], reverse=True)
    logger.info(
        "足球扫描完成: %d 场直播 → %d 个候选信号", len(live_events), len(signals)
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

