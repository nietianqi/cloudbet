"""
足球直播总进球投注机器人 — 主程序
=====================================
策略: 足球 live total_goals 泊松定价 + +EV 入场

运行方式:
    python soccer_bot.py                      # 默认真实投注（USDT）
    python soccer_bot.py --dry-run            # 模拟模式
    python soccer_bot.py --play               # PLAY_EUR 测试资金
    python soccer_bot.py --real               # USDT 真实资金（与默认一致）
    python soccer_bot.py --real --edge 0.07   # 提高 edge 阈值到 7%

资金管理:
    - 动态分数 Kelly（基础 1/5 Kelly）
    - 回撤约束（Drawdown-aware sizing）
    - 波动率约束（Volatility targeting）
    - 单轮风险预算 + 未结算在途敞口上限
"""

import argparse
import json
import logging
import os
import statistics
import sys
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

from cloudbet_client import CloudbetClient, CloudbetAPIError
import live_db
from soccer_strategy import generate_soccer_signals, log_soccer_signal
import settings

# ── 配置（统一从 settings.py 读取，修改配置请编辑 settings.py）────
SOCCER_CONFIG = {
    # ── API ──────────────────────────────────────────────────
    # "api_key": os.environ.get("CLOUDBET_API_KEY", ""),
    "api_key": "eyJhbGciOiJSUzI1NiIsImtpZCI6IkhKcDkyNnF3ZXBjNnF3LU9rMk4zV05pXzBrRFd6cEdwTzAxNlRJUjdRWDAiLCJ0eXAiOiJKV1QifQ.eyJhY2Nlc3NfdGllciI6InRyYWRpbmciLCJleHAiOjE5OTYyMzk5ODIsImlhdCI6MTY4MDg3OTk4MiwianRpIjoiNDM2Yzc1NjgtMTM0Ny00MDJhLTg4ZDMtZDlhZmU3OGQ1MDdiIiwic3ViIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIiwidGVuYW50IjoiY2xvdWRiZXQiLCJ1dWlkIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIn0.4eI0AK7z17EyutBgx_0FLUc9r5nWR_oUuiurGPyNlcGSz3853wkipm1ul_-oIlijPbaIha1UoD_2v3u-X48cJsmQglLNyst-2UPie9qQ3t8bzQUlhnHjcye7Kc-msGHNi-ML5twdRI-42sESiAECTccsB6NVebHgCqZfAh9-PVT-Hmao4c9AJiyJ2NA5QOTcBz7BJR06MTC0ZMW5Yklm001eEaDYxpBAorDmvRg5GDldlCBuQfVcvip8Zkp0uPHuAu2TJTJrw7tMYXSn7CUWWlQ_oQ7Alb-AchSOLkk7y-eUfUtu7plYJnj50wBLs-NLBzjnV3ifUhDk0etB9HNebA",

    "af_key": settings.API_FOOTBALL_KEY,   # 可选，来自 settings.py
    "currency": "USDT",
    "dry_run": False,                   # 默认真实投注

    # ── 信号阈值 ─────────────────────────────────────────────
    "edge_threshold": 0.06,             # 6% edge 才入场（足球 margin 比篮球高）
    "min_remaining_minutes": 8.0,       # 至少剩 8 分钟（避免末段暴力反弹）
    "min_elapsed_minutes": 1.0,       # ?? 1 ??????????
    "stable_window_secs": 25,           # 稳定性检测窗口
    "jump_threshold": 0.10,             # 盘口跳动阈值
    "prior_weight_live": 0.65,          # 实时 xG 最大权重

    # ── 赛前先验（理想应接 Dixon-Coles 模型）────────────────
    "pre_xg_home": 1.40,                # 全联赛均值：主队预期进球
    "pre_xg_away": 1.15,                # 全联赛均值：客队预期进球

    # ── 数据质量门控（基于历史复盘）──────────────────────────
    "require_reliable_score": False,    # Feed 普遍缺比分，默认允许估算比分参与
    "allow_imputed_score": True,        # 无比分时按盘口+进度估算当前比分
    "imputed_score_min_elapsed": 10.0,  # 估算比分仅在开赛 10 分钟后启用
    "imputed_score_extra_edge": 0.05,   # 估算比分时提高入场 edge 门槛
    "imputed_score_stake_mult": 0.50,   # 估算比分时缩小仓位
    "min_market_price": 1.70,           # 过滤低赔率（历史 ROI 偏弱）
    "max_market_price": 2.10,           # 过滤高赔率（历史波动/亏损偏高）
    "blocked_elapsed_ranges": [(30.0, 45.0)],  # 历史亏损集中时段（左闭右开）
    "competition_guard_enabled": True,
    "competition_guard_window": 240,    # 最近已结算样本窗口
    "competition_guard_min_samples": 5, # 联赛最小样本
    "competition_guard_min_roi": -0.25, # 低于该 ROI 的联赛暂不下注
    "competition_guard_min_win_rate": 0.30,  # 低于该胜率的联赛暂不下注
    "competition_guard_refresh_secs": 180,
    "competition_block_keywords": ["srl", "virtual", "simulated reality", "esoccer"],
    "fifa_league_filter_enabled": True,      # FIFA 排名门控：前150国家仅一级，前40国家允许一二级
    "fifa_allow_second_tier_for_top40": True,
    "competition_country_refresh_secs": 21600,
    "entry_price_window_secs": 90,           # 入场前回看最近价格窗口（秒）
    "entry_price_worse_tolerance": 0.02,     # 若当前价较最近最佳差超 2% 则放弃（不追坏价）
    "edge_robust_price_delta": 0.08,         # 假设成交价再恶化 0.08，做鲁棒 edge 检查
    "edge_robust_min": 0.01,                 # 鲁棒 edge 至少 1%
    "external_score_enabled": True,     # 启用外部实时比分（API-FOOTBALL）
    "external_score_prefer": True,      # 外部比分可用时优先使用
    "external_football_key": settings.API_FOOTBALL_KEY,
    "external_score_min_confidence": 0.80,
    "external_score_kickoff_tolerance_mins": 240,
    "external_score_cache_ttl_secs": 45,
    "external_score_timeout_secs": 10,

    # ── 仓位 ─────────────────────────────────────────────────
    "kelly_fraction": 0.20,             # 基础 1/5 Kelly
    "kelly_fraction_floor": 0.05,       # 动态收缩下限
    "max_stake_pct": 0.01,              # 单注最大 1.0% 资金（避免低余额时小于最小注额）
    "min_stake": 1.0,                   # 最小注额（USDT）
    "edge_confidence_cap": 0.18,        # edge >= 18% 视为满信心
    "edge_confidence_floor": 0.35,      # 低 edge 时最小下注缩放

    # ── 资金管理（动态分数 Kelly）──────────────────────────
    "drawdown_soft_pct": 0.08,          # 回撤 8% 开始降仓
    "drawdown_hard_pct": 0.20,          # 回撤 20% 降到最小仓位
    "drawdown_min_factor": 0.35,        # 回撤最小仓位系数
    "vol_lookback": 80,                 # 波动率估计窗口（最近结算笔数）
    "min_vol_samples": 20,              # 波动率估计最小样本
    "target_return_vol": 0.018,         # 单笔目标收益率波动
    "vol_factor_floor": 0.35,           # 高波动时最小缩放
    "round_risk_budget_pct": 0.012,     # 单轮总下注预算上限（资金占比）
    "max_open_exposure_pct": 0.03,      # 未结算在途风险上限（资金占比）
    "min_open_exposure_limit_abs": 10.0, # 在途敞口绝对下限（避免小余额时过早冻结）
    "open_exposure_scope": "sport", # portfolio=全账户, sport=仅足球
    "open_exposure_statuses": ["ACCEPTED", "PENDING"],
    "volatility_scope": "sport",    # portfolio=全账户, sport=仅足球

    # ── 风控 ─────────────────────────────────────────────────
    
    "daily_loss_limit_pct": 0.10,       # 日内最大亏损 10%
    "max_drawdown_stop_pct": 0.30,      # 峰值回撤硬熔断
    "max_consec_losses": 6,             # 连续亏损熔断
    "max_consec_rejects": 5,            # 连续拒单熔断
    "max_pending_orders": 80,           # 未终态 PENDING 订单上限
    "max_rejection_rate": 0.70,         # 近 N 笔终态单的拒单率上限
    "rejection_rate_window": 100,       # 拒单率统计窗口
    "rejection_rate_min_samples": 30,  # 样本不足时不触发拒单率熔断

    # ── 执行控制 ──────────────────────────────────────────────
    "sleep_interval": settings.SOCCER_SLEEP_INTERVAL,
    "accept_price_change": settings.SOCCER_ACCEPT_PRICE_CHANGE,
    "max_bets_per_event": 1,            # 每赛事最多下注 1 次
    "event_dedup_statuses": ["ACCEPTED", "PENDING"],  # 同赛事未结算单去重
    "max_rejects_per_event": 2,        # 同一赛事连续拒单上限（达到后冷却）
    "event_reject_cooldown_secs": 90,  # 同一赛事拒单冷却时间
    "pending_order_cooldown_secs": 60, # 下单返回 pending 后的重试冷却
    "api_error_cooldown_secs": 90,      # API 错误后赛事冷却（防 429 连续触发）
    "pending_stale_timeout_mins": 20,   # PENDING 超时自动回收，释放在途风险
    "settle_batch_size": 40,           # ?????? 40 ????????
    "settle_min_stake": 0.01,          # ??? stake>0 ?????
    "settle_statuses": ["ACCEPTED", "PENDING"],
    "settle_status_poll_interval_secs": 0.10,  # 结算状态查询限速
    "settle_api_fail_streak_limit": 4,         # 连续 429/5xx 超限后本轮停止结算扫描
    "auto_close_zero_stake_orders": True,  # ?????? 0 ????
    # ── 数据 ─────────────────────────────────────────────────
    "db_file": "live_betting.db",
    "leagues": None,                    # None=????????
    "live_statuses": ["TRADING_LIVE"],      # ???????????? TRADING
    "scan_progress_every": 25,          # ??? N ??????????
    "prefer_bulk_events_api": True,     # ??? /odds/events ????
    "bulk_from_hours": 4,               # ??????? 4 ??
    "bulk_to_hours": 2,                 # ??????? 2 ??
    "hydrate_live_events": False,       # ????????????????/???
    "fallback_to_league_scan_on_bulk_failure": True,  # ???????????

}

# Session 级别状态
_bet_events: set = set()
_event_rejects: Dict[str, int] = {}
_event_retry_after: Dict[str, float] = {}
_consec_rejects: int = 0
_consec_losses: int = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("soccer_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ── 风控 ──────────────────────────────────────────────────────

def check_risk_limits(cfg: Dict, balance: float, start_balance: float) -> Optional[str]:
    """返回停机原因，None 表示可继续运行"""
    global _consec_rejects, _consec_losses

    peak = max(float(cfg.get("_peak_bankroll", balance) or balance), balance)
    cfg["_peak_bankroll"] = peak
    drawdown = 0.0 if peak <= 0 else max(0.0, (peak - balance) / peak)

    # 1) 日内亏损
    if start_balance > 0:
        loss_pct = (start_balance - balance) / start_balance
        if loss_pct >= cfg["daily_loss_limit_pct"]:
            return f"日内亏损 {loss_pct:.1%} ≥ 限制 {cfg['daily_loss_limit_pct']:.0%}"

    # 2) 峰值回撤硬熔断
    max_dd_stop = float(cfg.get("max_drawdown_stop_pct", 0.30))
    if drawdown >= max_dd_stop:
        return f"账户回撤 {drawdown:.1%} ≥ 限制 {max_dd_stop:.0%}"

    # 3) 连续拒单
    if _consec_rejects >= cfg["max_consec_rejects"]:
        return f"连续拒单 {_consec_rejects} 次，暂停执行（检查拒单原因后重启）"

    # 4) 近 N 笔终态单拒单率（按 sport 维度）
    rej_stats = live_db.get_rejection_stats(
        window=int(cfg.get("rejection_rate_window", 100)),
        db_file=cfg["db_file"],
        sport="soccer",
        include_statuses=["ACCEPTED", "REJECTED"],
        rejected_statuses=["REJECTED"],
    )
    min_samples = int(cfg.get("rejection_rate_min_samples", 30))
    rejection_rate = float(rej_stats.get("rate", 0.0))
    total_samples = int(rej_stats.get("total", 0))
    if total_samples >= min_samples and rejection_rate > cfg["max_rejection_rate"]:
        return (
            f"拒单率 {rejection_rate:.1%} > {cfg['max_rejection_rate']:.0%} "
            f"(样本 {total_samples}/{cfg.get('rejection_rate_window', 100)})，Cloudbet 可能已标记账户！"
        )

    # 5) 待受理积压熔断
    pending_cap = int(cfg.get("max_pending_orders", 80))
    if pending_cap > 0:
        pending_cnt = live_db.count_unsettled_accepted_orders(
            db_file=cfg["db_file"],
            min_stake=0.0,
            statuses=["PENDING"],
            sport="soccer",
        )
        if pending_cnt >= pending_cap:
            return f"PENDING 积压 {pending_cnt} 笔 ≥ 上限 {pending_cap}"

    # 6) 连续亏损
    if _consec_losses >= cfg["max_consec_losses"]:
        return f"连续亏损 {_consec_losses} 次，策略暂停，请复盘信号质量"

    return None

# ── 资金管理 ──────────────────────────────────────────────────

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _drawdown_multiplier(cfg: Dict, drawdown: float) -> float:
    soft = max(0.0, cfg.get("drawdown_soft_pct", 0.08))
    hard = max(soft + 1e-6, cfg.get("drawdown_hard_pct", 0.20))
    floor = _clamp(cfg.get("drawdown_min_factor", 0.35), 0.05, 1.0)

    if drawdown <= soft:
        return 1.0
    if drawdown >= hard:
        return floor

    progress = (drawdown - soft) / (hard - soft)
    return 1.0 - progress * (1.0 - floor)


def compute_bankroll_profile(cfg: Dict, bankroll: float) -> Dict:
    """
    动态资金管理画像：回撤约束 + 波动率约束 + 风险预算。
    """
    peak = max(float(cfg.get("_peak_bankroll", bankroll) or bankroll), bankroll)
    cfg["_peak_bankroll"] = peak
    drawdown = 0.0 if peak <= 0 else max(0.0, (peak - bankroll) / peak)

    dd_mult = _drawdown_multiplier(cfg, drawdown)

    vol_scope = str(cfg.get("volatility_scope", "portfolio")).lower()
    vol_sport = "soccer" if vol_scope == "sport" else None
    returns = live_db.get_recent_result_returns(
        window=cfg.get("vol_lookback", 80),
        db_file=cfg["db_file"],
        sport=vol_sport,
    )
    min_samples = int(cfg.get("min_vol_samples", 20))
    realized_vol = None
    if len(returns) >= min_samples:
        try:
            realized_vol = float(statistics.pstdev(returns))
        except statistics.StatisticsError:
            realized_vol = None

    vol_mult = 1.0
    target_vol = max(float(cfg.get("target_return_vol", 0.018)), 1e-6)
    if realized_vol and realized_vol > 1e-9:
        vol_mult = _clamp(
            target_vol / realized_vol,
            float(cfg.get("vol_factor_floor", 0.35)),
            1.0,
        )

    base_kelly = max(float(cfg.get("kelly_fraction", 0.2)), 1e-6)
    dynamic_kelly = base_kelly * dd_mult * vol_mult
    dynamic_kelly = _clamp(
        dynamic_kelly,
        float(cfg.get("kelly_fraction_floor", 0.05)),
        base_kelly,
    )

    exposure_scope = str(cfg.get("open_exposure_scope", "portfolio")).lower()
    exposure_sport = "soccer" if exposure_scope == "sport" else None
    open_exposure = live_db.get_open_exposure(
        db_file=cfg["db_file"],
        include_statuses=cfg.get("open_exposure_statuses", ["ACCEPTED", "PENDING"]),
        sport=exposure_sport,
    )
    round_budget = bankroll * float(cfg.get("round_risk_budget_pct", 0.012))
    open_limit_pct = bankroll * float(cfg.get("max_open_exposure_pct", 0.03))
    open_limit_floor = max(0.0, float(cfg.get("min_open_exposure_limit_abs", 0.0)))
    open_limit = max(open_limit_pct, open_limit_floor)
    available_by_open = max(0.0, open_limit - open_exposure)
    available_round_budget = max(0.0, min(round_budget, available_by_open))

    return {
        "bankroll": bankroll,
        "peak": peak,
        "drawdown": drawdown,
        "drawdown_mult": dd_mult,
        "realized_vol": realized_vol,
        "vol_mult": vol_mult,
        "base_kelly": base_kelly,
        "kelly_fraction": dynamic_kelly,
        "open_exposure": open_exposure,
        "open_limit": open_limit,
        "round_budget": round_budget,
        "available_round_budget": available_round_budget,
    }


def size_stake_scientific(cfg: Dict, signal: Dict, profile: Dict, used_round_budget: float) -> tuple:
    """
    基于动态分数 Kelly 计算本信号最终下注额。

    返回:
        (stake, meta)
    """
    base_stake = float(signal.get("stake") or 0.0)
    if base_stake <= 0:
        return 0.0, {"reason": "base_stake_non_positive"}

    threshold = float(cfg.get("edge_threshold", 0.06))
    edge_cap = max(float(cfg.get("edge_confidence_cap", 0.18)), threshold + 1e-6)
    edge = float(signal.get("edge") or 0.0)
    edge_norm = _clamp((edge - threshold) / (edge_cap - threshold), 0.0, 1.0)
    edge_floor = _clamp(float(cfg.get("edge_confidence_floor", 0.35)), 0.05, 1.0)
    edge_mult = edge_floor + (1.0 - edge_floor) * edge_norm

    base_kelly = max(float(profile.get("base_kelly", cfg.get("kelly_fraction", 0.2))), 1e-6)
    dyn_kelly = float(profile.get("kelly_fraction", base_kelly))
    kelly_mult = dyn_kelly / base_kelly

    stake_raw = base_stake * kelly_mult * edge_mult
    bankroll_cap = float(profile.get("bankroll", 0.0)) * float(cfg.get("max_stake_pct", 0.01))
    market_cap = float(signal.get("max_stake", bankroll_cap) or bankroll_cap)
    stake = min(stake_raw, bankroll_cap, market_cap)

    remaining_round_budget = max(0.0, float(profile.get("available_round_budget", 0.0)) - used_round_budget)
    stake = min(stake, remaining_round_budget)

    min_required = max(
        float(cfg.get("min_stake", 1.0)),
        float(signal.get("min_stake", cfg.get("min_stake", 1.0)) or cfg.get("min_stake", 1.0)),
    )

    can_floor_to_min = (
        min_required <= bankroll_cap
        and min_required <= market_cap
        and min_required <= remaining_round_budget
    )

    if stake < min_required:
        if can_floor_to_min:
            final_stake = round(min_required, 2)
            return final_stake, {
                "reason": "floored_to_min_stake",
                "stake_base": round(base_stake, 4),
                "stake_raw": round(stake_raw, 4),
                "stake_after_caps": round(stake, 4),
                "min_required": min_required,
                "bankroll_cap": round(bankroll_cap, 4),
                "market_cap": round(market_cap, 4),
                "remaining_round_budget": round(remaining_round_budget, 4),
                "edge_mult": round(edge_mult, 4),
                "kelly_mult": round(kelly_mult, 4),
            }

        return 0.0, {
            "reason": "below_min_stake_or_budget",
            "stake_base": round(base_stake, 4),
            "stake_raw": round(stake_raw, 4),
            "stake_after_caps": round(stake, 4),
            "min_required": min_required,
            "bankroll_cap": round(bankroll_cap, 4),
            "market_cap": round(market_cap, 4),
            "remaining_round_budget": round(remaining_round_budget, 4),
            "edge_mult": round(edge_mult, 4),
            "kelly_mult": round(kelly_mult, 4),
        }



    final_stake = round(stake, 2)
    return final_stake, {
        "reason": "ok",
        "edge_mult": round(edge_mult, 4),
        "kelly_mult": round(kelly_mult, 4),
        "dynamic_kelly": round(dyn_kelly, 4),
        "used_budget_after": round(used_round_budget + final_stake, 2),
        "round_budget": round(float(profile.get("available_round_budget", 0.0)), 2),
    }


def _extract_reject_reason(result: Dict) -> str:
    """统一提取拒单原因，避免 API 字段变化导致空原因。"""
    for key in ("error", "errorCode", "rejectionReason", "reason", "message", "description", "statusMessage"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (dict, list)) and value:
            try:
                return json.dumps(value, ensure_ascii=False)
            except TypeError:
                return str(value)

    status = str(result.get("status") or "UNKNOWN")
    try:
        compact = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        compact = str(result)
    return f"status={status}; raw={compact[:400]}"


# ── 下单执行 ──────────────────────────────────────────────────

def execute_signal(client: CloudbetClient, cfg: Dict, signal: Dict,
                   db_ref_id: str) -> Dict:
    """
    执行单笔下注信号

    参数:
        db_ref_id: 预先写入 DB 的 reference_id，同一个 UUID 也作为
                   Cloudbet referenceId，确保 DB 记录与 API 响应一致。

    返回:
        {success, reference_id, status, executed_price, reject_reason}
    """
    if cfg["dry_run"]:
        logger.info(
            "[模拟] %s | %s %.2f @ %.3f | ref=%s",
            signal["match"], signal["side"].upper(),
            signal["stake"], signal["market_price"], db_ref_id
        )
        return {
            "success": True, "reference_id": db_ref_id,
            "status": "ACCEPTED", "executed_price": signal["market_price"],
            "reject_reason": "",
        }

    stake = float(signal.get("stake") or 0.0)
    min_stake = float(signal.get("min_stake") or cfg.get("min_stake", 1.0) or 1.0)
    if stake < min_stake:
        return {
            "success": False,
            "reference_id": db_ref_id,
            "status": "SKIPPED",
            "executed_price": None,
            "reject_reason": f"stake_below_min({stake:.2f} < {min_stake:.2f})",
        }


    current_price = signal["market_price"]

    def get_fresh_price() -> float:
        """重试时重新拉取最新赔率"""
        try:
            event_data = client.get_event(signal["event_id"])
            market = CloudbetClient.extract_total_goals_market(event_data)
            if market:
                return (market["over_price"]
                        if signal["side"] == "over"
                        else market["under_price"])
        except Exception as exc:
            logger.debug("刷新赔率失败: %s", exc)
        return current_price

    try:
        # place_bet 使用 db_ref_id 作为 referenceId，保持 DB 与 API 一致
        price = get_fresh_price()
        if price <= 1.01:
            return {
                "success": False,
                "reference_id": db_ref_id,
                "status": "SKIPPED",
                "executed_price": None,
                "reject_reason": "赔率无效",
            }

        result = client.place_bet(
            event_id=signal["event_id"],
            market_url=signal["market_url"],
            price=price,
            stake=signal["stake"],
            currency=cfg["currency"],
            accept_price_change=cfg.get("accept_price_change", "BETTER"),
            reference_id=db_ref_id,
        )
        if not isinstance(result, dict):
            return {
                "success": False,
                "reference_id": db_ref_id,
                "status": "ERROR",
                "executed_price": None,
                "reject_reason": f"invalid_place_response_type={type(result).__name__}",
            }
        actual_api_ref = result.get("_referenceId", db_ref_id)

    except CloudbetAPIError as exc:
        return {
            "success": False,
            "reference_id": db_ref_id,
            "status": "ERROR",
            "executed_price": None,
            "reject_reason": str(exc),
        }
    except Exception as exc:
        return {
            "success": False,
            "reference_id": db_ref_id,
            "status": "ERROR",
            "executed_price": None,
            "reject_reason": f"unexpected_place_error: {exc}",
        }

    status = result.get("status", "UNKNOWN")
    try:
        executed_price = float(result.get("price") or current_price)
    except (ValueError, TypeError):
        executed_price = current_price

    reject_reason = "" if status == "ACCEPTED" else _extract_reject_reason(result)

    return {
        "success": status == "ACCEPTED",
        "reference_id": actual_api_ref,
        "status": status,
        "executed_price": executed_price,
        "reject_reason": reject_reason,
        "raw_result": result,
    }

def try_settle_pending(client: CloudbetClient, cfg: Dict) -> None:
    """结算已接单和待受理订单，并同步最终状态。"""
    global _consec_losses
    if cfg["dry_run"]:
        return

    db_file = cfg["db_file"]

    if cfg.get("auto_close_zero_stake_orders", True):
        cleaned = live_db.auto_close_zero_stake_accepted_orders(db_file=db_file)
        if cleaned > 0:
            logger.warning("自动关闭 0 注额 ACCEPTED 订单: %d", cleaned)

    stale_minutes = float(cfg.get("pending_stale_timeout_mins", 20) or 0)
    if stale_minutes > 0:
        expired = live_db.auto_expire_stale_pending_orders(
            db_file=db_file,
            stale_minutes=stale_minutes,
            sport="soccer",
        )
        if expired > 0:
            logger.warning("自动回收超时 PENDING 订单: %d (>%s 分钟)", expired, stale_minutes)

    settle_min_stake = float(cfg.get("settle_min_stake", 0.01))
    settle_batch_size = max(1, int(cfg.get("settle_batch_size", 40)))
    settle_statuses = [str(s).upper() for s in cfg.get("settle_statuses", ["ACCEPTED", "PENDING"]) if str(s).strip()]
    if not settle_statuses:
        settle_statuses = ["ACCEPTED", "PENDING"]

    total_pending = live_db.count_unsettled_accepted_orders(
        db_file=db_file,
        min_stake=settle_min_stake,
        statuses=settle_statuses,
        sport="soccer",
    )
    if total_pending <= 0:
        return

    pending = live_db.get_accepted_orders(
        db_file=db_file,
        min_stake=settle_min_stake,
        limit=settle_batch_size,
        statuses=settle_statuses,
        sport="soccer",
    )
    if not pending:
        return

    logger.info(
        "结算扫描: total=%d batch=%d statuses=%s min_stake=%.2f",
        total_pending,
        len(pending),
        ",".join(settle_statuses),
        settle_min_stake,
    )

    settled_count = 0
    status_poll_interval = max(0.0, float(cfg.get("settle_status_poll_interval_secs", 0.10) or 0.0))
    fail_streak_limit = max(1, int(cfg.get("settle_api_fail_streak_limit", 4)))
    status_fail_streak = 0
    for idx, order in enumerate(pending, start=1):
        ref_id = order.get("reference_id", "")
        if not ref_id or ref_id.startswith("DRY-"):
            continue
        try:
            status_data = client.get_bet_status(ref_id)
            status_fail_streak = 0
        except CloudbetAPIError as exc:
            if exc.status_code in (429, 500, 502, 503, 504):
                status_fail_streak += 1
                logger.warning(
                    "结算状态接口受限(%s): %s (连续失败=%d/%d)",
                    exc.status_code,
                    ref_id,
                    status_fail_streak,
                    fail_streak_limit,
                )
                if status_fail_streak >= fail_streak_limit:
                    logger.warning("结算扫描提前结束：连续接口失败过多，下轮再试")
                    break
            else:
                logger.debug("结算查询失败 %s: %s", ref_id, exc)
            if status_poll_interval > 0:
                time.sleep(status_poll_interval)
            continue
        except Exception as exc:
            logger.debug("结算查询失败 %s: %s", ref_id, exc)
            if status_poll_interval > 0:
                time.sleep(status_poll_interval)
            continue

        try:
            status = str(status_data.get("status", "")).upper()

            if status in ("", "PENDING", "PENDING_ACCEPTANCE", "PENDING_PROCESSING"):
                continue

            if status == "ACCEPTED":
                try:
                    executed_price = float(
                        status_data.get("price")
                        or order.get("executed_price")
                        or order.get("requested_price")
                        or 0.0
                    )
                except (ValueError, TypeError):
                    executed_price = float(order.get("requested_price") or 0.0)
                live_db.update_order_status(
                    ref_id,
                    "ACCEPTED",
                    executed_price=executed_price if executed_price > 0 else None,
                    db_file=db_file,
                )
                continue

            if status == "REJECTED":
                live_db.update_order_status(
                    ref_id,
                    "REJECTED",
                    reject_reason=_extract_reject_reason(status_data),
                    db_file=db_file,
                )
                continue

            if status not in ("WIN", "LOSS", "VOID", "PARTIAL_WON", "PARTIAL_LOST"):
                continue

            stake = float(order.get("stake") or 0)
            bet_price = float(order.get("executed_price") or order.get("requested_price") or 1.0)
            returned = float(status_data.get("returnAmount") or 0)

            pnl = 0.0
            outcome = status
            if returned > 0:
                pnl = returned - stake
            elif status == "WIN":
                pnl = stake * (bet_price - 1.0)
            elif status == "LOSS":
                pnl = -stake
                outcome = "LOSE"
            elif status == "PARTIAL_WON":
                pnl = returned - stake if returned > 0 else stake * (bet_price - 1.0) * 0.5
            elif status == "PARTIAL_LOST":
                pnl = returned - stake if returned > 0 else -stake * 0.5

            # 更新连续亏损计数器（用于熔断）
            global _consec_losses
            if pnl < 0:
                _consec_losses += 1
            else:
                _consec_losses = 0

            live_db.insert_result(
                {
                    "reference_id": ref_id,
                    "event_id": order.get("event_id", ""),
                    "match": order.get("match", ""),
                    "side": order.get("side", ""),
                    "stake": stake,
                    "bet_price": bet_price,
                    "outcome": outcome,
                    "pnl": round(pnl, 4),
                },
                db_file=db_file,
            )
            settled_count += 1
            if pnl < -1e-9:
                _consec_losses += 1
            elif pnl > 1e-9:
                _consec_losses = 0

            logger.info("结算: %s | outcome=%s | PnL=%+.2f", order.get("match", ref_id), outcome, pnl)
        except Exception as exc:
            logger.debug("结算查询失败 %s: %s", ref_id, exc)
        finally:
            if status_poll_interval > 0 and idx < len(pending):
                time.sleep(status_poll_interval)

        if idx % 20 == 0 and idx < len(pending):
            logger.info("结算进度: %d/%d", idx, len(pending))

    if total_pending > len(pending):
        logger.info(
            "结算剩余: 还有 %d 笔（本轮处理 %d，已结算 %d）",
            total_pending - len(pending),
            len(pending),
            settled_count,
        )



def run(cfg: Dict) -> None:
    """主运行循环"""
    global _consec_rejects, _consec_losses

    logger.info("=" * 70)
    logger.info("  足球直播总进球机器人启动")
    logger.info("  策略: 泊松定价 + +EV 入场 + 动态分数 Kelly")
    logger.info("  模式: %s", "模拟" if cfg["dry_run"] else "真实下单")
    logger.info("  货币: %s", cfg["currency"])
    logger.info("  Edge 阈值: %.0f%%", cfg["edge_threshold"] * 100)
    logger.info("  xG 来源: %s", "API-Football" if cfg.get("af_key") else "先验估算（无 xG key）")
    logger.info("=" * 70)

    live_db.init_db(cfg["db_file"])
    try:
        repaired = live_db.repair_pending_acceptance_rejections(cfg["db_file"])
        if repaired > 0:
            logger.warning("修复历史误判订单: %d 条 REJECTED -> PENDING", repaired)
    except Exception as exc:
        logger.warning("修复历史误判订单失败: %s", exc)
    client = CloudbetClient(cfg.get("api_key", ""))

    # 获取起始余额
    try:
        start_balance = client.get_balance(cfg["currency"])
    except Exception as exc:
        if cfg["dry_run"]:
            start_balance = 100.0
            logger.warning("无法获取余额，使用默认值 %.2f", start_balance)
        else:
            raise RuntimeError(f"真实模式无法获取账户余额: {exc}") from exc

    if start_balance <= 0:
        if cfg["dry_run"]:
            logger.warning("起始余额不可用，使用默认值 100")
            start_balance = 100.0
        else:
            raise RuntimeError("真实模式余额不可用或为 0，停止执行")

    cfg["bankroll"] = start_balance
    cfg["_peak_bankroll"] = start_balance
    logger.info("起始余额: %.2f %s", start_balance, cfg["currency"])
    last_reset_date = datetime.now().date()

    round_count = 0
    while True:
        round_count += 1
        logger.info("\n%s — 第 %d 轮 (%s)", "─" * 50, round_count, datetime.now().strftime("%H:%M:%S"))

        # 按日重置：新的一天 start_balance 重置为当前余额
        today = datetime.now().date()
        if today != last_reset_date:
            try:
                start_balance = client.get_balance(cfg["currency"]) or start_balance
            except Exception:
                pass
            last_reset_date = today
            _consec_losses = 0
            logger.info("🔄 新的一天，日内亏损基准重置: %.2f", start_balance)

        # 更新余额
        try:
            balance = client.get_balance(cfg["currency"])
            if balance > 0:
                cfg["bankroll"] = balance
            else:
                # API 返回 0 时沿用上次有效余额，避免误触发亏损熔断
                balance = cfg.get("bankroll", start_balance)
        except Exception:
            balance = cfg.get("bankroll", start_balance)

        # 风控检查
        stop_reason = check_risk_limits(cfg, balance, start_balance)
        if stop_reason:
            logger.warning("⚠️  熔断: %s", stop_reason)
            wait_secs = cfg["sleep_interval"] * 4
            logger.info("暂停 %d 秒后重新检查...", wait_secs)
            time.sleep(wait_secs)
            _consec_rejects = 0
            continue

        # 尝试结算
        try_settle_pending(client, cfg)

        # 生成信号
        try:
            signals = generate_soccer_signals(cfg)
        except Exception as exc:
            logger.error("信号生成异常: %s", exc, exc_info=True)
            time.sleep(cfg["sleep_interval"])
            continue

        if not signals:
            logger.info("暂无符合条件的信号")
            time.sleep(cfg["sleep_interval"])
            continue

        logger.info("发现 %d 个候选信号", len(signals))

        profile = compute_bankroll_profile(cfg, balance)
        logger.info(
            "资金管理: bank=%.2f peak=%.2f dd=%.1f%% kelly=%.3f(基=%.3f) vol=%.3f open=%.2f/%.2f round_budget=%.2f",
            profile["bankroll"],
            profile["peak"],
            profile["drawdown"] * 100,
            profile["kelly_fraction"],
            profile["base_kelly"],
            profile["realized_vol"] if profile["realized_vol"] is not None else 0.0,
            profile["open_exposure"],
            profile["open_limit"],
            profile["available_round_budget"],
        )

        used_round_budget = 0.0
        for signal in signals:
            event_id = signal["event_id"]

            if event_id in _bet_events:
                continue

            max_bets_per_event = max(1, int(cfg.get("max_bets_per_event", 1)))
            open_order_cnt = live_db.count_unsettled_orders_for_event(
                event_id=event_id,
                db_file=cfg["db_file"],
                sport="soccer",
                statuses=cfg.get("event_dedup_statuses", ["ACCEPTED", "PENDING"]),
            )
            if open_order_cnt >= max_bets_per_event:
                logger.debug(
                    "[%s] 同赛事未结算订单=%d (上限=%d)，跳过重复下单",
                    signal["match"],
                    open_order_cnt,
                    max_bets_per_event,
                )
                continue

            retry_after = float(_event_retry_after.get(event_id, 0.0) or 0.0)
            now_ts = time.time()
            if retry_after > now_ts:
                continue

            if used_round_budget >= profile["available_round_budget"]:
                logger.info("本轮资金预算已用尽，停止本轮下单")
                break

            final_stake, stake_meta = size_stake_scientific(cfg, signal, profile, used_round_budget)
            if final_stake <= 0:
                logger.info("[%s] 资金管理跳过: %s", signal.get("match", event_id), stake_meta)
                continue

            signal["stake"] = final_stake
            used_round_budget += final_stake

            log_soccer_signal(signal)
            logger.info(
                "   资金管理: kelly_mult=%.3f edge_mult=%.3f budget=%.2f/%.2f",
                stake_meta.get("kelly_mult", 0.0),
                stake_meta.get("edge_mult", 0.0),
                used_round_budget,
                profile["available_round_budget"],
            )

            # 写入赔率/模型快照
            try:
                live_db.insert_model_snapshot(
                    event_id=event_id,
                    pregame_line=signal["line"],
                    current_score=signal["goals_home"] + signal["goals_away"],
                    elapsed_minutes=signal["elapsed_minutes"],
                    model_result={
                        "p_model_over": signal["model_result"].get("over"),
                        "p_model_under": signal["model_result"].get("under"),
                        "p_mkt_over": signal.get("mkt_prob") if signal["side"] == "over" else 1 - signal.get("mkt_prob", 0.5),
                        "p_mkt_under": signal.get("mkt_prob") if signal["side"] == "under" else 1 - signal.get("mkt_prob", 0.5),
                        "edge_over": signal["edge"] if signal["side"] == "over" else -signal["edge"],
                        "edge_under": signal["edge"] if signal["side"] == "under" else -signal["edge"],
                        "fair_over_price": signal["fair_price"] if signal["side"] == "over" else None,
                        "fair_under_price": signal["fair_price"] if signal["side"] == "under" else None,
                        # DB column is "scoring_rate" (not scoring_rate_per_min)
                        "scoring_rate_per_min": (
                            signal["model_result"].get("rem_home_lambda", 0)
                            + signal["model_result"].get("rem_away_lambda", 0)
                        ),
                        "expected_remaining_score": signal["model_result"].get("remaining_lambda"),
                    },
                    db_file=cfg["db_file"],
                )
            except Exception as exc:
                logger.debug("写 model_snapshot 失败: %s", exc)

            # 写入订单记录
            pending_ref = str(uuid.uuid4())
            try:
                live_db.insert_order(
                    {
                        "reference_id": pending_ref,
                        "event_id": event_id,
                        "sport": "soccer",
                        "match": signal["match"],
                        "market_url": signal["market_url"],
                        "side": signal["side"],
                        "line": signal["line"],
                        "requested_price": signal["market_price"],
                        "stake": signal["stake"],
                        "currency": cfg["currency"],
                        "status": "PENDING",
                        "edge_at_bet": signal["edge"],
                        "p_model_at_bet": signal["model_prob"],
                    },
                    db_file=cfg["db_file"],
                )
            except Exception as exc:
                logger.debug("写 orders 失败: %s", exc)

            # 执行下单
            result = execute_signal(client, cfg, signal, pending_ref)

            # 更新 DB 状态
            if result["success"]:
                live_db.update_order_status(
                    pending_ref,
                    "ACCEPTED",
                    executed_price=result["executed_price"],
                    db_file=cfg["db_file"],
                )
                _bet_events.add(event_id)
                _event_rejects.pop(event_id, None)
                _event_retry_after.pop(event_id, None)
                _consec_rejects = 0
                logger.info(
                    "✅ 成交: %s | %s %.2f @ %.3f | db_ref=%s api_ref=%s",
                    signal["match"],
                    signal["side"].upper(),
                    signal["stake"],
                    result["executed_price"],
                    pending_ref,
                    result["reference_id"],
                )
            else:
                raw_status = str(result.get("status") or "UNKNOWN").upper()
                if raw_status.startswith("PENDING"):
                    order_status = "PENDING"
                elif raw_status in ("SKIPPED", "ERROR"):
                    order_status = raw_status
                else:
                    order_status = "REJECTED"

                live_db.update_order_status(
                    pending_ref,
                    order_status,
                    reject_reason=result["reject_reason"],
                    db_file=cfg["db_file"],
                )

                if order_status == "REJECTED":
                    _consec_rejects += 1
                    event_rejects = _event_rejects.get(event_id, 0) + 1
                    _event_rejects[event_id] = event_rejects

                    max_rejects_per_event = int(cfg.get("max_rejects_per_event", 2))
                    cooldown_secs = int(cfg.get("event_reject_cooldown_secs", 90))
                    if event_rejects >= max_rejects_per_event:
                        _event_retry_after[event_id] = time.time() + cooldown_secs
                        logger.info(
                            "[%s] 同一赛事拒单 %d 次，冷却 %d 秒",
                            signal["match"],
                            event_rejects,
                            cooldown_secs,
                        )

                    logger.warning(
                        "❌ 拒单 #%d: %s | status=%s | 原因=%s",
                        _consec_rejects,
                        signal["match"],
                        raw_status,
                        result["reject_reason"],
                    )
                elif order_status == "PENDING":
                    pending_cooldown = int(cfg.get("pending_order_cooldown_secs", 60))
                    _event_retry_after[event_id] = time.time() + pending_cooldown
                    logger.info(
                        "🕒 待受理: %s | status=%s | 冷却=%ds | 详情=%s",
                        signal["match"],
                        raw_status,
                        pending_cooldown,
                        result["reject_reason"],
                    )
                else:
                    if order_status == "ERROR":
                        api_error_cooldown = int(cfg.get("api_error_cooldown_secs", 90))
                        _event_retry_after[event_id] = time.time() + api_error_cooldown
                    logger.info(
                        "⏭️ 跳过: %s | status=%s | 原因=%s",
                        signal["match"],
                        raw_status,
                        result["reject_reason"],
                    )
        logger.info("等待 %d 秒...", cfg["sleep_interval"])
        time.sleep(cfg["sleep_interval"])


# ── CLI 入口 ──────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="足球直播总进球投注机器人")
    p.add_argument("--dry-run", action="store_true", help="模拟模式（覆盖默认真实投注）")
    p.add_argument("--play", action="store_true", help="使用 PLAY_EUR 测试资金")
    p.add_argument("--real", action="store_true", help="使用 USDT 真实资金")
    p.add_argument("--edge", type=float, default=None, help="Edge 阈值（如 0.07）")
    p.add_argument("--interval", type=int, default=None, help="轮询间隔（秒）")
    p.add_argument("--api-key", type=str, default=None, help="Cloudbet API Key")
    p.add_argument("--af-key", type=str, default=None, help="API-Football Key（可选）")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = dict(SOCCER_CONFIG)

    # 命令行参数覆盖
    if args.play and args.real:
        logger.error("--play 与 --real 不能同时使用")
        sys.exit(2)

    if args.dry_run:
        cfg["dry_run"] = True
        cfg["currency"] = "PLAY_EUR" if args.play else "USDT"
    else:
        if args.play:
            cfg["currency"] = "PLAY_EUR"
            cfg["dry_run"] = False
        else:
            # 默认真实投注（USDT）
            cfg["currency"] = "USDT"
            cfg["dry_run"] = False

    if args.real:
        cfg["currency"] = "USDT"
        if not args.dry_run:
            cfg["dry_run"] = False

    if args.edge is not None:
        cfg["edge_threshold"] = args.edge

    if args.interval is not None:
        cfg["sleep_interval"] = args.interval

    if args.api_key:
        cfg["api_key"] = args.api_key

    if args.af_key:
        cfg["af_key"] = args.af_key

    if not cfg["api_key"]:
        logger.error(
            "未设置 Cloudbet API Key！\n"
            "  方式1: export CLOUDBET_API_KEY=your_key\n"
            "  方式2: python soccer_bot.py --api-key your_key\n"
            "  方式3: 编辑 soccer_bot.py 中 SOCCER_CONFIG['api_key']"
        )
        sys.exit(1)

    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("\n用户中断，机器人退出")
        sys.exit(0)
    except Exception as exc:
        logger.error("系统异常: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()









