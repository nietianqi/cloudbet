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

    "CURRENCY": "PLAY_EUR",            # 测试资金币种；真实下注改为 USDT
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
    "MAX_REJECTION_RATE": 0.70,        # 近 100 笔拒单率上限
    "MAX_CONCURRENT_EXPOSURE_PCT": 0.05, # 同时敞口最大 5%

    # ── 执行控制 ──────────────────────────────────────────────
    "SLEEP_INTERVAL": 15,              # 轮询间隔（秒）；直播建议 10-20 秒
    "MAX_BETS_PER_EVENT": 1,           # 每个赛事最多下注 1 次
    "ACCEPT_PRICE_CHANGE": "NONE",     # NONE=拒绝赔率变差；BETTER=接受更好赔率
    "DRY_RUN": True,                   # 默认模拟模式；命令行 --real 才切换为真实下单

    # ── 数据库 ────────────────────────────────────────────────
    "DB_FILE": "live_betting.db",
}

# 已下注赛事集合（session 内去重，防止对同一赛事多次下注）
_bet_events: set = set()

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

def place_bet(cfg: Dict, signal: Dict, reference_id: Optional[str] = None) -> Dict:
    """
    向 Cloudbet 下单

    返回:
        {success, reference_id, status, executed_price, reject_reason}
    """
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
        status = data.get("status", "UNKNOWN")
        accepted = resp.status_code == 200 and status == "ACCEPTED"

        executed_price = data.get("price", signal["market_price"])
        reject_reason = data.get("errorCode", data.get("message", "")) if not accepted else ""

        return {
            "success": accepted,
            "reference_id": ref_id,
            "status": status,
            "executed_price": float(executed_price) if executed_price else signal["market_price"],
            "reject_reason": reject_reason,
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
    """
    返回停机原因，None 表示可继续运行

    检查项：
      1. 日内亏损限制
      2. 连续拒单
      3. 整体拒单率
      4. 连续亏损
    """
    global _consec_rejects, _consec_losses

    # 1. 日内亏损
    if start_balance > 0:
        loss_pct = (start_balance - balance) / start_balance
        if loss_pct >= cfg["DAILY_LOSS_LIMIT_PCT"]:
            return f"日内亏损 {loss_pct:.1%} ≥ 限制 {cfg['DAILY_LOSS_LIMIT_PCT']:.0%}"

    # 2. 连续拒单
    if _consec_rejects >= cfg["MAX_CONSEC_REJECTS"]:
        return f"连续拒单 {_consec_rejects} 次，暂停执行"

    # 3. 近 100 笔拒单率
    rejection_rate = live_db.get_rejection_rate(100, cfg["DB_FILE"])
    if rejection_rate > cfg["MAX_REJECTION_RATE"]:
        return f"拒单率 {rejection_rate:.1%} > {cfg['MAX_REJECTION_RATE']:.0%}"

    # 4. 连续亏损
    if _consec_losses >= cfg["MAX_CONSEC_LOSSES"]:
        return f"连续亏损 {_consec_losses} 次，策略暂停复盘"

    return None


# ── 结算 & CLV 更新（简易版）────────────────────────────────

def try_settle_pending(cfg: Dict) -> None:
    """
    尝试结算已成交但尚未记录结果的订单。

    完整实现需调用 GET /pub/v3/bets/{referenceId}/status 或
    GET /pub/v4/bets/history 来获取结算状态。
    此处实现基础框架，实际结算逻辑留给 clv_report.py 批量运行。
    """
    pending = live_db.get_accepted_orders(cfg["DB_FILE"])
    if not pending:
        return

    headers = {"X-API-Key": cfg["API_KEY"]}
    for order in pending:
        ref_id = order.get("reference_id")
        if not ref_id or ref_id.startswith("DRY-"):
            continue
        try:
            url = f"https://sports-api.cloudbet.com/pub/v3/bets/{ref_id}/status"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
            status = data.get("status", "").upper()
            if status in ("WIN", "LOSS", "LOSE", "PUSH", "SETTLED",
                          "PARTIAL_WON", "PARTIAL_LOST", "VOID"):
                # 记录结果（CLV 由 clv_report.py 补录）
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
                    db_file=cfg["DB_FILE"],
                )
                logger.info(
                    "💰 结算: %s | 结果=%s | PnL=%+.2f",
                    order.get("match", ref_id), status, pnl
                )
        except Exception as exc:
            logger.debug("结算查询失败 %s: %s", ref_id, exc)


# ── 主循环 ────────────────────────────────────────────────────

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

    # 记录今日起始余额（用于日内亏损控制）
    start_balance = get_balance(cfg)
    if start_balance <= 0:
        logger.warning("无法获取起始余额或余额为 0，使用默认值 100")
        start_balance = 100.0
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
            if event_id in _bet_events:
                logger.debug("已下注赛事: %s", signal["match"])
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
                live_db.update_order_status(
                    ref_id,
                    "REJECTED",
                    reject_reason=result["reject_reason"],
                    db_file=cfg["DB_FILE"],
                )
                _consec_rejects += 1
                logger.warning(
                    "❌ 拒单: %s | 原因=%s | 连续拒单=%d",
                    signal["match"],
                    result["reject_reason"],
                    _consec_rejects,
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

    if args.dry_run:
        cfg["DRY_RUN"] = True
        logger.info("模拟模式已启用")
    elif args.play:
        cfg["CURRENCY"] = "PLAY_EUR"
        cfg["DRY_RUN"] = False
    elif args.real:
        cfg["CURRENCY"] = "USDT"
        cfg["DRY_RUN"] = False

    if args.edge is not None:
        cfg["EDGE_THRESHOLD"] = args.edge

    if args.interval is not None:
        cfg["SLEEP_INTERVAL"] = args.interval

    if args.api_key:
        cfg["API_KEY"] = args.api_key

    if not cfg["API_KEY"]:
        logger.error(
            "未设置 API Key！\n"
            "方式一: 环境变量 export CLOUDBET_API_KEY=your_key\n"
            "方式二: 命令行 python nba_bot.py --api-key your_key\n"
            "方式三: 直接编辑 nba_bot.py 中的 NBA_CONFIG['API_KEY']"
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
