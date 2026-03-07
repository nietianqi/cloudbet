"""
NBA 直播总分投注机器人 — 主程序
=================================
策略: 篮球 live totals 贝叶斯定价 + 正期望值 (+EV) 入场

运行方式:
    python nba_bot.py               # 真实下单（USDT，需填写 API_KEY）
    python nba_bot.py --dry-run     # 已禁用（参数保留兼容，仍为真实下单）
    python nba_bot.py --play        # 已禁用（参数保留兼容，仍使用 USDT）

风控熔断（自动停机）:
    - 日内亏损 ≥ 10% 资金
    - 连续 5 笔拒单（可能被标记）
    - 拒单率（最近 100 笔）> 70%
    - 连续 5 笔亏损

CLV 追踪:
    每笔订单在下单时记录 bet_price；
    结算后记录 closing_price 并计算 CLV。
    核心 KPI：avg_clv_pct > 0 才说明策略有真实优势。
"""

import argparse
import json
import logging
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

import requests

import live_db
from nba_strategy import generate_signals, log_signal_summary

# ── 配置 ─────────────────────────────────────────────────────
# 在此填写你的 Trading API Key（或通过环境变量传入）
import os

NBA_CONFIG = {
    # ── API ──────────────────────────────────────────────────
    # "API_KEY": os.environ.get("CLOUDBET_API_KEY", ""),
    "API_KEY":"eyJhbGciOiJSUzI1NiIsImtpZCI6IkhKcDkyNnF3ZXBjNnF3LU9rMk4zV05pXzBrRFd6cEdwTzAxNlRJUjdRWDAiLCJ0eXAiOiJKV1QifQ.eyJhY2Nlc3NfdGllciI6InRyYWRpbmciLCJleHAiOjE5OTYyMzk5ODIsImlhdCI6MTY4MDg3OTk4MiwianRpIjoiNDM2Yzc1NjgtMTM0Ny00MDJhLTg4ZDMtZDlhZmU3OGQ1MDdiIiwic3ViIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIiwidGVuYW50IjoiY2xvdWRiZXQiLCJ1dWlkIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIn0.4eI0AK7z17EyutBgx_0FLUc9r5nWR_oUuiurGPyNlcGSz3853wkipm1ul_-oIlijPbaIha1UoD_2v3u-X48cJsmQglLNyst-2UPie9qQ3t8bzQUlhnHjcye7Kc-msGHNi-ML5twdRI-42sESiAECTccsB6NVebHgCqZfAh9-PVT-Hmao4c9AJiyJ2NA5QOTcBz7BJR06MTC0ZMW5Yklm001eEaDYxpBAorDmvRg5GDldlCBuQfVcvip8Zkp0uPHuAu2TJTJrw7tMYXSn7CUWWlQ_oQ7Alb-AchSOLkk7y-eUfUtu7plYJnj50wBLs-NLBzjnV3ifUhDk0etB9HNebA",

    "CURRENCY": "USDT",            # 强制真实资金币种
    "BET_URL": "https://sports-api.cloudbet.com/pub/v3/bets/place",
    "ACCOUNT_URL": "https://sports-api.cloudbet.com/pub/v1/account/currencies",
    "BET_HISTORY_URL": "https://sports-api.cloudbet.com/pub/v4/bets/history",

    # ── 信号阈值 ─────────────────────────────────────────────
    "EDGE_THRESHOLD": 0.05,            # 最小 edge：5%（建议从 5-6% 起）
    "MIN_REMAINING_MINUTES": 6.0,      # 距结束至少剩 6 分钟
    "STABLE_WINDOW_SECS": 20,          # 盘口稳定窗口（秒）
    "JUMP_THRESHOLD": 0.08,            # 盘口跳动阈值
    "PRIOR_WEIGHT": 0.45,              # 贝叶斯先验权重

    # ── 仓位 ─────────────────────────────────────────────────
    "KELLY_FRACTION": 0.25,            # 基础 1/4 Kelly
    "KELLY_FRACTION_FLOOR": 0.05,      # 动态收缩下限
    "MAX_STAKE_PCT": 0.005,            # 单注最大 0.5% 资金
    "MIN_STAKE": 1.0,                  # 最小注额（平台要求）
    "EDGE_CONFIDENCE_CAP": 0.18,
    "EDGE_CONFIDENCE_FLOOR": 0.35,

    # ── 风控 ─────────────────────────────────────────────────
    "DAILY_LOSS_LIMIT_PCT": 0.10,      # 日内最大亏损 10%
    "MAX_CONSEC_LOSSES": 5,            # 连续亏损熔断
    "MAX_CONSEC_REJECTS": 5,           # 连续拒单熔断
    "MAX_PENDING_ORDERS": 120,        # 未终态 PENDING 订单上限
    "MAX_REJECTION_RATE": 0.70,        # 近 N 笔终态单拒单率上限
    "REJECTION_RATE_WINDOW": 100,      # rejection-rate window
    "REJECTION_RATE_MIN_SAMPLES": 30,  # min samples for rejection-rate circuit breaker
    "MAX_CONCURRENT_EXPOSURE_PCT": 0.05, # 同时敞口最大 5%
    "MIN_OPEN_EXPOSURE_LIMIT_ABS": 10.0, # 在途敞口绝对下限（避免小余额冻结）
    "DRAWDOWN_SOFT_PCT": 0.08,
    "DRAWDOWN_HARD_PCT": 0.20,
    "DRAWDOWN_MIN_FACTOR": 0.35,
    "MAX_DRAWDOWN_STOP_PCT": 0.30,
    "VOL_LOOKBACK": 80,
    "MIN_VOL_SAMPLES": 20,
    "TARGET_RETURN_VOL": 0.022,
    "VOL_FACTOR_FLOOR": 0.35,
    "ROUND_RISK_BUDGET_PCT": 0.012,
    "OPEN_EXPOSURE_SCOPE": "sport", # portfolio=全账户, sport=仅篮球
    "OPEN_EXPOSURE_STATUSES": ["ACCEPTED", "PENDING"],
    "VOLATILITY_SCOPE": "sport",    # portfolio=全账户, sport=仅篮球

    # ── 执行控制 ──────────────────────────────────────────────
    "SLEEP_INTERVAL": 15,              # 轮询间隔（秒）；直播建议 10-20 秒
    "MAX_BETS_PER_EVENT": 1,           # 每个赛事最多下注 1 次
    "PENDING_ORDER_COOLDOWN_SECS": 60, # cooldown after pending response
    "PENDING_STALE_TIMEOUT_MINS": 20, # auto-expire long-pending orders
    "ACCEPT_PRICE_CHANGE": "NONE",     # NONE=拒绝赔率变差；BETTER=接受更好赔率
    "DRY_RUN": False,               # 强制真实下单（不使用模拟模式）
    "LIVE_STATUSES": ["TRADING_LIVE"],
    "PREFER_BULK_EVENTS_API": True,
    "BULK_FROM_HOURS": 4,
    "BULK_TO_HOURS": 2,
    "FALLBACK_TO_LEAGUE_SCAN_ON_BULK_FAILURE": True,
    "SETTLE_BATCH_SIZE": 40,
    "SETTLE_MIN_STAKE": 0.01,
    "SETTLE_STATUSES": ["ACCEPTED", "PENDING"],
    "AUTO_CLOSE_ZERO_STAKE_ORDERS": True,

    # ── 数据库 ────────────────────────────────────────────────
    "DB_FILE": "live_betting.db",
}

# 已下注赛事集合（session 内去重，防止对同一赛事多次下注）
_bet_events: set = set()
_event_retry_after: Dict[str, float] = {}

# 连续拒单计数
_consec_rejects: int = 0
_consec_losses: int = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("nba_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ── 账户 ──────────────────────────────────────────────────────

def get_balance(cfg: Dict) -> float:
    """获取账户余额"""
    url = f"{cfg['ACCOUNT_URL']}/{cfg['CURRENCY']}/balance"
    headers = {"X-API-Key": cfg["API_KEY"]}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return float(resp.json().get("amount", 0))
        logger.error("获取余额失败: %d", resp.status_code)
    except requests.RequestException as exc:
        logger.error("获取余额异常: %s", exc)
    return 0.0


# ── 下单 ──────────────────────────────────────────────────────
def _extract_reject_reason(result: Dict) -> str:
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


# ── 下单 ──────────────────────────────────────────────────────

def place_bet(cfg: Dict, signal: Dict, reference_id: Optional[str] = None) -> Dict:
    """向 Cloudbet 下单。"""
    if cfg["DRY_RUN"]:
        ref_id = reference_id or f"DRY-{uuid.uuid4().hex[:8].upper()}"
        logger.info("[模拟] 下注 %.2f @ %.3f (%s)",
                    signal["stake"], signal["market_price"], signal["side"])
        return {
            "success": True,
            "reference_id": ref_id,
            "status": "ACCEPTED",
            "executed_price": signal["market_price"],
            "reject_reason": "",
        }

    stake = float(signal.get("stake") or 0.0)
    min_stake = float(cfg.get("MIN_STAKE", 1.0))
    if stake < min_stake:
        return {
            "success": False,
            "reference_id": reference_id or str(uuid.uuid4()),
            "status": "SKIPPED",
            "executed_price": None,
            "reject_reason": f"stake_below_min({stake:.2f} < {min_stake:.2f})",
        }

    headers = {
        "X-API-Key": cfg["API_KEY"],
        "Content-Type": "application/json",
    }
    ref_id = reference_id or str(uuid.uuid4())
    payload = {
        "referenceId": ref_id,
        "stake": str(round(signal["stake"], 2)),
        "price": str(signal["market_price"]),
        "eventId": str(signal["event_id"]),
        "marketUrl": signal["market_url"],
        "currency": cfg["CURRENCY"],
        "acceptPriceChange": cfg["ACCEPT_PRICE_CHANGE"],
    }

    try:
        resp = requests.post(cfg["BET_URL"], headers=headers, json=payload, timeout=15)
        data = resp.json()
        status = str(data.get("status", "UNKNOWN")).upper()
        accepted = resp.status_code == 200 and status == "ACCEPTED"

        executed_price = data.get("price", signal["market_price"])
        reject_reason = "" if accepted else _extract_reject_reason(data)

        return {
            "success": accepted,
            "reference_id": ref_id,
            "status": status,
            "executed_price": float(executed_price) if executed_price else signal["market_price"],
            "reject_reason": reject_reason,
            "raw_result": data,
        }
    except requests.RequestException as exc:
        return {
            "success": False,
            "reference_id": ref_id,
            "status": "ERROR",
            "executed_price": None,
            "reject_reason": str(exc),
        }

# ── 风控检查 ──────────────────────────────────────────────────

def check_risk_limits(cfg: Dict, balance: float, start_balance: float) -> Optional[str]:
    """返回停机原因，None 表示可继续运行。"""
    global _consec_rejects, _consec_losses

    peak = max(float(cfg.get("_PEAK_BANKROLL", balance) or balance), balance)
    cfg["_PEAK_BANKROLL"] = peak
    drawdown = 0.0 if peak <= 0 else max(0.0, (peak - balance) / peak)

    # 1. 日内亏损
    if start_balance > 0:
        loss_pct = (start_balance - balance) / start_balance
        if loss_pct >= cfg["DAILY_LOSS_LIMIT_PCT"]:
            return f"日内亏损 {loss_pct:.1%} ≥ 限制 {cfg['DAILY_LOSS_LIMIT_PCT']:.0%}"

    # 2. 峰值回撤止损
    max_dd_stop = float(cfg.get("MAX_DRAWDOWN_STOP_PCT", 0.30))
    if drawdown >= max_dd_stop:
        return f"账户回撤 {drawdown:.1%} ≥ 限制 {max_dd_stop:.0%}"

    # 3. 连续拒单
    if _consec_rejects >= cfg["MAX_CONSEC_REJECTS"]:
        return f"连续拒单 {_consec_rejects} 次，暂停执行"

    # 4. 近 N 笔终态单拒单率（按篮球维度）
    rej_stats = live_db.get_rejection_stats(
        window=int(cfg.get("REJECTION_RATE_WINDOW", 100)),
        db_file=cfg["DB_FILE"],
        sport="basketball",
        include_statuses=["ACCEPTED", "REJECTED"],
        rejected_statuses=["REJECTED"],
    )
    min_samples = int(cfg.get("REJECTION_RATE_MIN_SAMPLES", 30))
    rejection_rate = float(rej_stats.get("rate", 0.0))
    total_samples = int(rej_stats.get("total", 0))
    if total_samples >= min_samples and rejection_rate > cfg["MAX_REJECTION_RATE"]:
        return (
            f"拒单率 {rejection_rate:.1%} > {cfg['MAX_REJECTION_RATE']:.0%} "
            f"(样本 {total_samples}/{cfg.get('REJECTION_RATE_WINDOW', 100)})"
        )

    # 5. 待受理积压
    pending_cap = int(cfg.get("MAX_PENDING_ORDERS", 120))
    if pending_cap > 0:
        pending_cnt = live_db.count_unsettled_accepted_orders(
            db_file=cfg["DB_FILE"],
            min_stake=0.0,
            statuses=["PENDING"],
            sport="basketball",
        )
        if pending_cnt >= pending_cap:
            return f"PENDING 积压 {pending_cnt} 笔 ≥ 上限 {pending_cap}"

    # 6. 连续亏损
    if _consec_losses >= cfg["MAX_CONSEC_LOSSES"]:
        return f"连续亏损 {_consec_losses} 次，策略暂停复盘"

    return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _drawdown_multiplier(cfg: Dict, drawdown: float) -> float:
    soft = max(0.0, cfg.get("DRAWDOWN_SOFT_PCT", 0.08))
    hard = max(soft + 1e-6, cfg.get("DRAWDOWN_HARD_PCT", 0.20))
    floor = _clamp(cfg.get("DRAWDOWN_MIN_FACTOR", 0.35), 0.05, 1.0)

    if drawdown <= soft:
        return 1.0
    if drawdown >= hard:
        return floor

    progress = (drawdown - soft) / (hard - soft)
    return 1.0 - progress * (1.0 - floor)


def compute_bankroll_profile(cfg: Dict, bankroll: float) -> Dict:
    """动态资金管理画像：回撤约束 + 波动率约束 + 风险预算。"""
    peak = max(float(cfg.get("_PEAK_BANKROLL", bankroll) or bankroll), bankroll)
    cfg["_PEAK_BANKROLL"] = peak
    drawdown = 0.0 if peak <= 0 else max(0.0, (peak - bankroll) / peak)

    dd_mult = _drawdown_multiplier(cfg, drawdown)

    vol_scope = str(cfg.get("VOLATILITY_SCOPE", "portfolio")).lower()
    vol_sport = "basketball" if vol_scope == "sport" else None
    returns = live_db.get_recent_result_returns(
        window=int(cfg.get("VOL_LOOKBACK", 80)),
        db_file=cfg["DB_FILE"],
        sport=vol_sport,
    )

    realized_vol = None
    min_samples = int(cfg.get("MIN_VOL_SAMPLES", 20))
    if len(returns) >= min_samples:
        try:
            realized_vol = float(statistics.pstdev(returns))
        except statistics.StatisticsError:
            realized_vol = None

    vol_mult = 1.0
    target_vol = max(float(cfg.get("TARGET_RETURN_VOL", 0.022)), 1e-6)
    if realized_vol and realized_vol > 1e-9:
        vol_mult = _clamp(
            target_vol / realized_vol,
            float(cfg.get("VOL_FACTOR_FLOOR", 0.35)),
            1.0,
        )

    base_kelly = max(float(cfg.get("KELLY_FRACTION", 0.25)), 1e-6)
    dynamic_kelly = base_kelly * dd_mult * vol_mult
    dynamic_kelly = _clamp(
        dynamic_kelly,
        float(cfg.get("KELLY_FRACTION_FLOOR", 0.05)),
        base_kelly,
    )

    exposure_scope = str(cfg.get("OPEN_EXPOSURE_SCOPE", "portfolio")).lower()
    exposure_sport = "basketball" if exposure_scope == "sport" else None
    open_exposure = live_db.get_open_exposure(
        db_file=cfg["DB_FILE"],
        include_statuses=cfg.get("OPEN_EXPOSURE_STATUSES", ["ACCEPTED", "PENDING"]),
        sport=exposure_sport,
    )

    round_budget = bankroll * float(cfg.get("ROUND_RISK_BUDGET_PCT", 0.012))
    open_limit_pct = bankroll * float(cfg.get("MAX_CONCURRENT_EXPOSURE_PCT", 0.05))
    open_limit_floor = max(0.0, float(cfg.get("MIN_OPEN_EXPOSURE_LIMIT_ABS", 0.0)))
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
    """基于动态分数 Kelly 计算本信号最终下注额。"""
    base_stake = float(signal.get("stake") or 0.0)
    if base_stake <= 0:
        return 0.0, {"reason": "base_stake_non_positive"}

    threshold = float(cfg.get("EDGE_THRESHOLD", 0.05))
    edge_cap = max(float(cfg.get("EDGE_CONFIDENCE_CAP", 0.18)), threshold + 1e-6)
    edge = float(signal.get("edge") or 0.0)
    edge_norm = _clamp((edge - threshold) / (edge_cap - threshold), 0.0, 1.0)
    edge_floor = _clamp(float(cfg.get("EDGE_CONFIDENCE_FLOOR", 0.35)), 0.05, 1.0)
    edge_mult = edge_floor + (1.0 - edge_floor) * edge_norm

    base_kelly = max(float(profile.get("base_kelly", cfg.get("KELLY_FRACTION", 0.25))), 1e-6)
    dyn_kelly = float(profile.get("kelly_fraction", base_kelly))
    kelly_mult = dyn_kelly / base_kelly

    stake_raw = base_stake * kelly_mult * edge_mult
    bankroll_cap = float(profile.get("bankroll", 0.0)) * float(cfg.get("MAX_STAKE_PCT", 0.005))
    market_cap = float(signal.get("max_stake", bankroll_cap) or bankroll_cap)
    stake = min(stake_raw, bankroll_cap, market_cap)

    remaining_round_budget = max(0.0, float(profile.get("available_round_budget", 0.0)) - used_round_budget)
    stake = min(stake, remaining_round_budget)

    min_required = max(
        float(cfg.get("MIN_STAKE", 1.0)),
        float(signal.get("min_stake", cfg.get("MIN_STAKE", 1.0)) or cfg.get("MIN_STAKE", 1.0)),
    )

    can_floor_to_min = (
        min_required <= bankroll_cap
        and min_required <= market_cap
        and min_required <= remaining_round_budget
    )

    if stake < min_required:
        if can_floor_to_min:
            return round(min_required, 2), {
                "reason": "floored_to_min_stake",
                "edge_mult": round(edge_mult, 4),
                "kelly_mult": round(kelly_mult, 4),
            }
        return 0.0, {
            "reason": "below_min_stake_or_budget",
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


def try_settle_pending(cfg: Dict) -> None:
    """结算已接单和待受理订单，并同步最终状态。"""
    global _consec_losses
    if cfg["DRY_RUN"]:
        return

    db_file = cfg["DB_FILE"]

    if cfg.get("AUTO_CLOSE_ZERO_STAKE_ORDERS", True):
        cleaned = live_db.auto_close_zero_stake_accepted_orders(db_file=db_file)
        if cleaned > 0:
            logger.warning("自动关闭 0 注额 ACCEPTED 订单: %d", cleaned)

    stale_minutes = float(cfg.get("PENDING_STALE_TIMEOUT_MINS", 20) or 0)
    if stale_minutes > 0:
        expired = live_db.auto_expire_stale_pending_orders(
            db_file=db_file,
            stale_minutes=stale_minutes,
            sport="basketball",
        )
        if expired > 0:
            logger.warning("自动回收超时 PENDING 订单: %d (>%s 分钟)", expired, stale_minutes)

    settle_min_stake = float(cfg.get("SETTLE_MIN_STAKE", 0.01))
    settle_batch_size = max(1, int(cfg.get("SETTLE_BATCH_SIZE", 40)))
    settle_statuses = [str(s).upper() for s in cfg.get("SETTLE_STATUSES", ["ACCEPTED", "PENDING"]) if str(s).strip()]
    if not settle_statuses:
        settle_statuses = ["ACCEPTED", "PENDING"]

    total_pending = live_db.count_unsettled_accepted_orders(
        db_file=db_file,
        min_stake=settle_min_stake,
        statuses=settle_statuses,
        sport="basketball",
    )
    if total_pending <= 0:
        return

    pending = live_db.get_accepted_orders(
        db_file=db_file,
        min_stake=settle_min_stake,
        limit=settle_batch_size,
        statuses=settle_statuses,
        sport="basketball",
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

    headers = {"X-API-Key": cfg["API_KEY"]}
    settled_count = 0
    for idx, order in enumerate(pending, start=1):
        ref_id = order.get("reference_id")
        if not ref_id or str(ref_id).startswith("DRY-"):
            continue
        try:
            url = f"https://sports-api.cloudbet.com/pub/v3/bets/{ref_id}/status"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
            status = str(data.get("status", "")).upper()

            if status in ("", "PENDING", "PENDING_ACCEPTANCE", "PENDING_PROCESSING"):
                continue

            if status == "ACCEPTED":
                executed_price = data.get("price") or order.get("executed_price") or order.get("requested_price")
                try:
                    executed_price_val = float(executed_price) if executed_price is not None else None
                except (ValueError, TypeError):
                    executed_price_val = None
                live_db.update_order_status(
                    ref_id,
                    "ACCEPTED",
                    executed_price=executed_price_val,
                    db_file=db_file,
                )
                continue

            if status == "REJECTED":
                live_db.update_order_status(
                    ref_id,
                    "REJECTED",
                    reject_reason=_extract_reject_reason(data),
                    db_file=db_file,
                )
                continue

            if status not in ("WIN", "LOSS", "LOSE", "PUSH", "SETTLED", "PARTIAL_WON", "PARTIAL_LOST", "VOID"):
                continue

            pnl = 0.0
            stake = float(order.get("stake") or 0)
            bet_price = float(order.get("executed_price") or order.get("requested_price") or 1.0)
            returned = float(data.get("returnAmount") or 0)
            if returned > 0:
                pnl = returned - stake
            elif status == "WIN":
                pnl = stake * (bet_price - 1.0)
            elif status in ("LOSS", "LOSE"):
                pnl = -stake

            live_db.insert_result(
                {
                    "reference_id": ref_id,
                    "event_id": order.get("event_id", ""),
                    "match": order.get("match", ""),
                    "side": order.get("side", ""),
                    "stake": stake,
                    "bet_price": bet_price,
                    "outcome": status,
                    "pnl": round(pnl, 4),
                },
                db_file=db_file,
            )
            settled_count += 1
            if pnl < -1e-9:
                _consec_losses += 1
            elif pnl > 1e-9:
                _consec_losses = 0

            logger.info("结算: %s | outcome=%s | PnL=%+.2f", order.get("match", ref_id), status, pnl)
        except Exception as exc:
            logger.debug("结算查询失败 %s: %s", ref_id, exc)

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
    logger.info("  NBA 直播总分机器人启动")
    logger.info("  策略: 贝叶斯定价 + +EV 入场 + 动态分数 Kelly")
    logger.info("  模式: %s", "模拟" if cfg["DRY_RUN"] else "真实下单")
    logger.info("  货币: %s", cfg["CURRENCY"])
    logger.info("  Edge 阈值: %.0f%%", cfg["EDGE_THRESHOLD"] * 100)
    logger.info("  轮询间隔: %d 秒", cfg["SLEEP_INTERVAL"])
    logger.info("=" * 70)

    # 初始化数据库
    live_db.init_db(cfg["DB_FILE"])
    try:
        repaired = live_db.repair_pending_acceptance_rejections(cfg["DB_FILE"])
        if repaired > 0:
            logger.warning("修复历史误判订单: %d 条 REJECTED -> PENDING", repaired)
    except Exception as exc:
        logger.warning("修复历史误判订单失败: %s", exc)

    # 记录起始余额（用于日内亏损控制）
    start_balance = get_balance(cfg)
    if start_balance <= 0:
        if cfg["DRY_RUN"]:
            logger.warning("起始余额不可用，使用默认值 100")
            start_balance = 100.0
        else:
            raise RuntimeError("真实模式余额不可用或为 0，停止执行")

    cfg["BANKROLL"] = start_balance
    cfg["_PEAK_BANKROLL"] = start_balance
    logger.info("起始余额: %.2f %s", start_balance, cfg["CURRENCY"])

    round_count = 0
    while True:
        round_count += 1
        now_str = datetime.now().strftime("%H:%M:%S")

        logger.info("\n%s — 第 %d 轮扫描 (%s)", "─" * 50, round_count, now_str)

        # 更新余额
        balance = get_balance(cfg)
        if balance > 0:
            cfg["BANKROLL"] = balance
        else:
            # API 读取失败或返回 0 时，沿用上次有效余额，避免误触发亏损熔断
            balance = cfg.get("BANKROLL", start_balance)

        # 风控检查
        stop_reason = check_risk_limits(cfg, balance, start_balance)
        if stop_reason:
            logger.warning("⚠️  熔断停机: %s", stop_reason)
            logger.info("等待 %d 秒后重新检查...", cfg["SLEEP_INTERVAL"] * 4)
            time.sleep(cfg["SLEEP_INTERVAL"] * 4)
            continue

        # 尝试结算待处理订单
        try_settle_pending(cfg)

        # 生成信号
        try:
            signals = generate_signals(cfg)
        except Exception as exc:
            logger.error("信号生成异常: %s", exc, exc_info=True)
            time.sleep(cfg["SLEEP_INTERVAL"])
            continue

        if not signals:
            logger.info("暂无符合条件的信号")
            time.sleep(cfg["SLEEP_INTERVAL"])
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

        # 执行信号（每个赛事只下一次）
        for signal in signals:
            event_id = signal["event_id"]

            if event_id in _bet_events:
                logger.debug("已下注赛事: %s", signal["match"])
                continue

            retry_after = float(_event_retry_after.get(event_id, 0.0) or 0.0)
            if retry_after > time.time():
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

            # 记录赔率快照
            live_db.insert_odds_snapshot(
                {
                    "event_id": event_id,
                    "competition": signal["competition"],
                    "home_team": signal["home"],
                    "away_team": signal["away"],
                    "sport": "basketball",
                    "status": "TRADING_LIVE",
                    "market_url": signal["market_url"],
                    "line": signal["line"],
                    "over_price": signal["over_price"],
                    "under_price": signal["under_price"],
                    "elapsed_minutes": signal["elapsed_minutes"],
                    "current_score": signal["current_score"],
                },
                db_file=cfg["DB_FILE"],
            )

            # 记录模型快照
            live_db.insert_model_snapshot(
                event_id=event_id,
                pregame_line=signal["line"],
                current_score=signal["current_score"],
                elapsed_minutes=signal["elapsed_minutes"],
                model_result=signal["model_result"],
                db_file=cfg["DB_FILE"],
            )

            log_signal_summary(signal)
            logger.info(
                "   资金管理: kelly_mult=%.3f edge_mult=%.3f budget=%.2f/%.2f",
                stake_meta.get("kelly_mult", 0.0),
                stake_meta.get("edge_mult", 0.0),
                used_round_budget,
                profile["available_round_budget"],
            )

            # 记录订单（下单前写入，防止 crash 丢失记录）
            ref_id = str(uuid.uuid4())
            live_db.insert_order(
                {
                    "reference_id": ref_id,
                    "event_id": event_id,
                    "sport": "basketball",
                    "match": signal["match"],
                    "market_url": signal["market_url"],
                    "side": signal["side"],
                    "line": signal["line"],
                    "requested_price": signal["market_price"],
                    "stake": signal["stake"],
                    "currency": cfg["CURRENCY"],
                    "status": "PENDING",
                    "edge_at_bet": signal["edge"],
                    "p_model_at_bet": signal["model_prob"],
                },
                db_file=cfg["DB_FILE"],
            )

            # 执行下单
            result = place_bet(cfg, signal, reference_id=ref_id)
            actual_ref_id = result["reference_id"]

            if result["success"]:
                live_db.update_order_status(
                    ref_id,
                    "ACCEPTED",
                    executed_price=result["executed_price"],
                    db_file=cfg["DB_FILE"],
                )
                _bet_events.add(event_id)
                _event_retry_after.pop(event_id, None)
                _consec_rejects = 0
                logger.info(
                    "✅ 成交: %s | %s %.2f @ %.3f | ref=%s",
                    signal["match"],
                    signal["side"].upper(),
                    signal["stake"],
                    result["executed_price"],
                    actual_ref_id,
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
                    ref_id,
                    order_status,
                    reject_reason=result["reject_reason"],
                    db_file=cfg["DB_FILE"],
                )

                if order_status == "REJECTED":
                    _consec_rejects += 1
                    logger.warning(
                        "❌ 拒单: %s | status=%s | 原因=%s | 连续拒单=%d",
                        signal["match"],
                        raw_status,
                        result["reject_reason"],
                        _consec_rejects,
                    )
                elif order_status == "PENDING":
                    cooldown = int(cfg.get("PENDING_ORDER_COOLDOWN_SECS", 60))
                    _event_retry_after[event_id] = time.time() + cooldown
                    logger.info(
                        "🕒 待受理: %s | status=%s | 冷却=%ds | 详情=%s",
                        signal["match"],
                        raw_status,
                        cooldown,
                        result["reject_reason"],
                    )
                else:
                    logger.info(
                        "⏭️ 跳过: %s | status=%s | 原因=%s",
                        signal["match"],
                        raw_status,
                        result["reject_reason"],
                    )

        logger.info("等待 %d 秒...", cfg["SLEEP_INTERVAL"])
        time.sleep(cfg["SLEEP_INTERVAL"])


# ── CLI 入口 ──────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="NBA 直播总分投注机器人")
    parser.add_argument("--dry-run", action="store_true",
                        help="已禁用：始终真实下单（参数仅保留兼容）")
    parser.add_argument("--play", action="store_true",
                        help="已禁用：始终使用 USDT（参数仅保留兼容）")
    parser.add_argument("--real", action="store_true",
                        help="真实资金模式（默认，无需显式传入）")
    parser.add_argument("--edge", type=float, default=None,
                        help="覆盖 edge 阈值（例：0.06 = 6%%）")
    parser.add_argument("--interval", type=int, default=None,
                        help="覆盖轮询间隔（秒）")
    parser.add_argument("--api-key", type=str, default=None,
                        help="覆盖 API Key")
    return parser.parse_args()


def main():
    args = parse_args()
    cfg = dict(NBA_CONFIG)

    if args.dry_run or args.play:
        logger.warning("篮球机器人已强制真实投注，忽略 --dry-run/--play 参数")

    # 强制真实投注（USDT）
    cfg["DRY_RUN"] = False
    cfg["CURRENCY"] = "USDT"

    if args.edge is not None:
        cfg["EDGE_THRESHOLD"] = args.edge

    if args.interval is not None:
        cfg["SLEEP_INTERVAL"] = args.interval

    if args.api_key:
        cfg["API_KEY"] = args.api_key

    if not cfg["API_KEY"]:
        logger.error(
            "未设置 API Key！\n"
            "方式1: 设置环境变量 CLOUDBET_API_KEY\n"
            "方式2: 运行 python nba_bot.py --api-key your_key\n"
            "方式3: 编辑 nba_bot.py 中 NBA_CONFIG['API_KEY']"
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




















