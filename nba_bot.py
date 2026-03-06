"""
NBA 直播总分投注机器人 — 主程序
=================================
策略: 篮球 live totals 贝叶斯定价 + 正期望值 (+EV) 入场

运行方式:
    python nba_bot.py               # 真实下单（需填写 API_KEY）
    python nba_bot.py --dry-run     # 模拟模式（只记录信号，不下单）
    python nba_bot.py --play        # 使用 PLAY_EUR 测试资金

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

    "CURRENCY": "USDT",            # 测试资金币种；真实下注改为 USDT
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
    "KELLY_FRACTION": 0.25,            # 1/4 Kelly（保守）
    "MAX_STAKE_PCT": 0.005,            # 单注最大 0.5% 资金
    "MIN_STAKE": 1.0,                  # 最小注额（平台要求）

    # ── 风控 ─────────────────────────────────────────────────
    "DAILY_LOSS_LIMIT_PCT": 0.10,      # 日内最大亏损 10%
    "MAX_CONSEC_LOSSES": 5,            # 连续亏损熔断
    "MAX_CONSEC_REJECTS": 5,           # 连续拒单熔断
    "MAX_REJECTION_RATE": 0.70,        # 近 N 笔终态单拒单率上限
    "REJECTION_RATE_WINDOW": 100,      # rejection-rate window
    "REJECTION_RATE_MIN_SAMPLES": 30,  # min samples for rejection-rate circuit breaker
    "MAX_CONCURRENT_EXPOSURE_PCT": 0.05, # 同时敞口最大 5%

    # ── 执行控制 ──────────────────────────────────────────────
    "SLEEP_INTERVAL": 15,              # 轮询间隔（秒）；直播建议 10-20 秒
    "MAX_BETS_PER_EVENT": 1,           # 每个赛事最多下注 1 次
    "PENDING_ORDER_COOLDOWN_SECS": 60, # cooldown after pending response
    "ACCEPT_PRICE_CHANGE": "NONE",     # NONE=拒绝赔率变差；BETTER=接受更好赔率
    "DRY_RUN": False,                   # 默认模拟模式；命令行 --real 才切换为真实下单
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

    # 1. 日内亏损
    if start_balance > 0:
        loss_pct = (start_balance - balance) / start_balance
        if loss_pct >= cfg["DAILY_LOSS_LIMIT_PCT"]:
            return f"日内亏损 {loss_pct:.1%} ≥ 限制 {cfg['DAILY_LOSS_LIMIT_PCT']:.0%}"

    # 2. 连续拒单
    if _consec_rejects >= cfg["MAX_CONSEC_REJECTS"]:
        return f"连续拒单 {_consec_rejects} 次，暂停执行"

    # 3. 近 N 笔终态单拒单率（按篮球维度）
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

    # 4. 连续亏损
    if _consec_losses >= cfg["MAX_CONSEC_LOSSES"]:
        return f"连续亏损 {_consec_losses} 次，策略暂停复盘"

    return None
def try_settle_pending(cfg: Dict) -> None:
    """结算已接单和待受理订单，并同步最终状态。"""
    if cfg["DRY_RUN"]:
        return

    db_file = cfg["DB_FILE"]

    if cfg.get("AUTO_CLOSE_ZERO_STAKE_ORDERS", True):
        cleaned = live_db.auto_close_zero_stake_accepted_orders(db_file=db_file)
        if cleaned > 0:
            logger.warning("自动关闭 0 注额 ACCEPTED 订单: %d", cleaned)

    settle_min_stake = float(cfg.get("SETTLE_MIN_STAKE", 0.01))
    settle_batch_size = max(1, int(cfg.get("SETTLE_BATCH_SIZE", 40)))
    settle_statuses = [str(s).upper() for s in cfg.get("SETTLE_STATUSES", ["ACCEPTED", "PENDING"]) if str(s).strip()]
    if not settle_statuses:
        settle_statuses = ["ACCEPTED", "PENDING"]

    total_pending = live_db.count_unsettled_accepted_orders(
        db_file=db_file,
        min_stake=settle_min_stake,
        statuses=settle_statuses,
    )
    if total_pending <= 0:
        return

    pending = live_db.get_accepted_orders(
        db_file=db_file,
        min_stake=settle_min_stake,
        limit=settle_batch_size,
        statuses=settle_statuses,
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
    logger.info("  策略: 贝叶斯定价 + +EV 入场 + 1/4 Kelly")
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

    # 记录今日起始余额（用于日内亏损控制）
    start_balance = get_balance(cfg)
    if start_balance <= 0:
        if cfg["DRY_RUN"]:
            logger.warning("????????? 0?????????? 100")
            start_balance = 100.0
        else:
            raise RuntimeError("??????????? 0?????")
    cfg["BANKROLL"] = start_balance
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
            # 重置连续计数器（允许自动恢复，但需要人工复盘）
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

        # 执行信号（每个赛事只下一次）
        for signal in signals:
            event_id = signal["event_id"]

            # 去重
            # 去重
            if event_id in _bet_events:
                logger.debug("已下注赛事: %s", signal["match"])
                continue

            retry_after = float(_event_retry_after.get(event_id, 0.0) or 0.0)
            if retry_after > time.time():
                continue

            # 记录赔率快照
            live_db.insert_odds_snapshot(
                {
                    "event_id": event_id,
                    "competition": signal["competition"],
                    "home_team": signal["home"],
                    "away_team": signal["away"],
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

            # 记录订单（下单前写入，防止 crash 丢失记录）
            ref_id = str(uuid.uuid4())
            live_db.insert_order(
                {
                    "reference_id": ref_id,
                    "event_id": event_id,
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
                        help="模拟模式：只记录信号，不实际下单")
    parser.add_argument("--play", action="store_true",
                        help="使用 PLAY_EUR 测试资金（默认）")
    parser.add_argument("--real", action="store_true",
                        help="使用 USDT 真实资金")
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

    if args.play and args.real:
        logger.error("--play ? --real ??????")
        sys.exit(2)

    if args.dry_run:
        cfg["DRY_RUN"] = True
        cfg["CURRENCY"] = "PLAY_EUR" if args.play else "USDT"
    else:
        if args.play:
            cfg["CURRENCY"] = "PLAY_EUR"
            cfg["DRY_RUN"] = False
        else:
            # ???????USDT?
            cfg["CURRENCY"] = "USDT"
            cfg["DRY_RUN"] = False

    if args.real:
        cfg["CURRENCY"] = "USDT"
        if not args.dry_run:
            cfg["DRY_RUN"] = False

    if args.edge is not None:
        cfg["EDGE_THRESHOLD"] = args.edge

    if args.interval is not None:
        cfg["SLEEP_INTERVAL"] = args.interval

    if args.api_key:
        cfg["API_KEY"] = args.api_key

    if not cfg["API_KEY"]:
        logger.error(
            "??? API Key?\n"
            "???: ???? export CLOUDBET_API_KEY=your_key\n"
            "???: ??? python nba_bot.py --api-key your_key\n"
            "???: ???? nba_bot.py ?? NBA_CONFIG['API_KEY']"
        )
        sys.exit(1)

    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("\n??????????")
        sys.exit(0)
    except Exception as exc:
        logger.error("????: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()





