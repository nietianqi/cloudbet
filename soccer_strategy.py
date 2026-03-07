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
_competition_guard_cache = {"ts": 0.0, "bad": set(), "stats": {}}

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
    skipped_for_edge = 0
    skipped_for_imputed_early = 0
    max_edge_seen = float("-inf")
    bad_competitions, comp_stats = _get_bad_competitions(cfg)
    if bad_competitions:
        logger.info("联赛风控门控: bad_competitions=%d (样本窗=%d)", len(bad_competitions), int(cfg.get("competition_guard_window", 240)))

    for event in live_events:
        if not isinstance(event, dict):
            continue

        event_id = str(event.get("id", ""))
        home_obj = event.get("home") or {}
        away_obj = event.get("away") or {}
        home_name = home_obj.get("name", "?")
        away_name = away_obj.get("name", "?")
        match_name = f"{home_name} vs {away_name}"
        comp_name = event.get("_competition_name", "") or event.get("competition", "")

        if _contains_blocked_keyword(comp_name, match_name, keywords=competition_block_keywords):
            skipped_for_competition_keyword += 1
            logger.debug("[%s] 命中联赛关键词过滤: %s", match_name, comp_name)
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
                "max_stake": signal_info.get("max_stake", 9999),
                "min_stake": signal_info.get("min_stake", min_stake),
                "elapsed_minutes": elapsed,
                "remaining_minutes": round(remaining_minutes, 1),
                "goals_home": goals_home,
                "goals_away": goals_away,
                "score_source": score_source,
                "score_imputed": score_imputed,
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
        "足球扫描完成: %d 场直播 → %d 个候选信号 (skip:score=%d imputed_early=%d elapsed=%d edge=%d block=%d comp=%d comp_kw=%d price=%d max_edge=%.3f)",
        len(live_events),
        len(signals),
        skipped_for_unreliable_score,
        skipped_for_imputed_early,
        skipped_for_elapsed,
        skipped_for_edge,
        skipped_for_elapsed_block,
        skipped_for_competition,
        skipped_for_competition_keyword,
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
