"""
足球直播总进球投注机器人 — 主程序
=====================================
策略: 足球 live total_goals 泊松定价 + +EV 入场

运行方式:
    python soccer_bot.py --dry-run             # 模拟模式（推荐先跑 1-2 周）
    python soccer_bot.py --play                # PLAY_EUR 测试资金
    python soccer_bot.py --real                # USDT 真实资金（须先验证 CLV > 0）
    python soccer_bot.py --real --edge 0.07    # 提高 edge 阈值到 7%

风控熔断:
    - 日内亏损 ≥ 10% → 停机
    - 连续拒单 ≥ 5 次 → 暂停（Cloudbet 限制：最近 100 笔 >75% 拒单会被封号）
    - 连续亏损 ≥ 6 次 → 暂停复盘
    - 近 100 笔拒单率 > 70% → 告警并降频

数据流:
    Cloudbet Feed → 赔率快照 → 模型计算 → 信号过滤
    → 下单(v3) → 状态更新 → 结算 → CLV 写库
    → clv_report.py 分析（离线）
"""

import argparse
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

from cloudbet_client import CloudbetClient, CloudbetAPIError
import live_db
from soccer_strategy import generate_soccer_signals, log_soccer_signal

# ── 配置 ─────────────────────────────────────────────────────
SOCCER_CONFIG = {
    # ── API ──────────────────────────────────────────────────
    "api_key": os.environ.get("CLOUDBET_API_KEY", ""),
    "af_key": os.environ.get("API_FOOTBALL_KEY", ""),   # 可选
    "currency": "PLAY_EUR",
    "dry_run": True,                    # 默认模拟模式

    # ── 信号阈值 ─────────────────────────────────────────────
    "edge_threshold": 0.06,             # 6% edge 才入场（足球 margin 比篮球高）
    "min_remaining_minutes": 8.0,       # 至少剩 8 分钟（避免末段暴力反弹）
    "stable_window_secs": 25,           # 稳定性检测窗口
    "jump_threshold": 0.10,             # 盘口跳动阈值
    "prior_weight_live": 0.65,          # 实时 xG 最大权重

    # ── 赛前先验（理想应接 Dixon-Coles 模型）────────────────
    "pre_xg_home": 1.40,                # 全联赛均值：主队预期进球
    "pre_xg_away": 1.15,                # 全联赛均值：客队预期进球

    # ── 仓位 ─────────────────────────────────────────────────
    "kelly_fraction": 0.25,             # 1/4 Kelly
    "max_stake_pct": 0.005,             # 单注最大 0.5% 资金
    "min_stake": 1.0,                   # 最小注额（USDT）

    # ── 风控 ─────────────────────────────────────────────────
    "daily_loss_limit_pct": 0.10,       # 日内最大亏损 10%
    "max_consec_losses": 6,             # 连续亏损熔断
    "max_consec_rejects": 5,            # 连续拒单熔断
    "max_rejection_rate": 0.70,         # 近 100 笔拒单率上限

    # ── 执行控制 ──────────────────────────────────────────────
    "sleep_interval": 20,               # 轮询间隔（秒）；足球建议 15-30s
    "accept_price_change": "BETTER",    # NONE/BETTER/ALL
    "max_bets_per_event": 1,            # 每赛事最多下注 1 次

    # ── 数据 ─────────────────────────────────────────────────
    "db_file": "live_betting.db",
}

# Session 级别状态
_bet_events: set = set()
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

    # 日内亏损
    if start_balance > 0:
        loss_pct = (start_balance - balance) / start_balance
        if loss_pct >= cfg["daily_loss_limit_pct"]:
            return f"日内亏损 {loss_pct:.1%} ≥ 限制 {cfg['daily_loss_limit_pct']:.0%}"

    # 连续拒单
    if _consec_rejects >= cfg["max_consec_rejects"]:
        return f"连续拒单 {_consec_rejects} 次，暂停执行（检查拒单原因后重启）"

    # 近 100 笔拒单率
    rejection_rate = live_db.get_rejection_rate(100, cfg["db_file"])
    if rejection_rate > cfg["max_rejection_rate"]:
        return (f"拒单率 {rejection_rate:.1%} > {cfg['max_rejection_rate']:.0%}，"
                "Cloudbet 可能已标记账户！")

    # 连续亏损
    if _consec_losses >= cfg["max_consec_losses"]:
        return f"连续亏损 {_consec_losses} 次，策略暂停，请复盘信号质量"

    return None


# ── 下单执行 ──────────────────────────────────────────────────

def execute_signal(client: CloudbetClient, cfg: Dict, signal: Dict) -> Dict:
    """
    执行单笔下注信号

    包含:
    - 对赔率变化的自动重试（最多 1 次）
    - 速率限制（CloudbetClient 内部强制 1次/秒）
    - 结果记录到 SQLite

    返回:
        {success, reference_id, status, executed_price, reject_reason}
    """
    if cfg["dry_run"]:
        ref_id = f"DRY-{uuid.uuid4().hex[:8].upper()}"
        logger.info(
            "[模拟] %s | %s %.2f @ %.3f",
            signal["match"], signal["side"].upper(),
            signal["stake"], signal["market_price"]
        )
        return {
            "success": True, "reference_id": ref_id,
            "status": "ACCEPTED", "executed_price": signal["market_price"],
            "reject_reason": "",
        }

    current_price = signal["market_price"]

    def get_fresh_price():
        """重试时重新拉取赔率"""
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
        result = client.place_bet_with_retry(
            event_id=signal["event_id"],
            market_url=signal["market_url"],
            price_fn=get_fresh_price,
            stake=signal["stake"],
            currency=cfg["currency"],
            max_retries=1,
        )
    except CloudbetAPIError as exc:
        return {
            "success": False, "reference_id": "",
            "status": "ERROR", "executed_price": None,
            "reject_reason": str(exc),
        }

    status = result.get("status", "UNKNOWN")
    ref_id = result.get("_referenceId", result.get("referenceId", str(uuid.uuid4())))
    executed_price = float(result.get("price", current_price))
    error = result.get("error", result.get("errorCode", ""))

    return {
        "success": status == "ACCEPTED",
        "reference_id": ref_id,
        "status": status,
        "executed_price": executed_price,
        "reject_reason": error,
    }


# ── 结算查询 ──────────────────────────────────────────────────

def try_settle_pending(client: CloudbetClient, cfg: Dict) -> None:
    """查询并结算已成交但未记录结果的订单"""
    if cfg["dry_run"]:
        return

    pending = live_db.get_accepted_orders(cfg["db_file"])
    if not pending:
        return

    for order in pending:
        ref_id = order.get("reference_id", "")
        if not ref_id or ref_id.startswith("DRY-"):
            continue
        try:
            status_data = client.get_bet_status(ref_id)
            status = status_data.get("status", "")
            if status not in ("WIN", "LOSS", "VOID", "PARTIAL_WON", "PARTIAL_LOST"):
                continue

            stake = float(order.get("stake") or 0)
            bet_price = float(order.get("executed_price") or order.get("requested_price") or 1.0)

            pnl = 0.0
            outcome = status
            if status == "WIN":
                pnl = stake * (bet_price - 1.0)
            elif status in ("LOSS",):
                pnl = -stake
                outcome = "LOSE"
            elif status == "PARTIAL_WON":
                pnl = stake * (bet_price - 1.0) * 0.5
            elif status == "PARTIAL_LOST":
                pnl = -stake * 0.5

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
                db_file=cfg["db_file"],
            )
            logger.info(
                "💰 结算: %s | 结果=%s | PnL=%+.2f",
                order.get("match", ref_id), outcome, pnl
            )
        except Exception as exc:
            logger.debug("结算查询失败 %s: %s", ref_id, exc)


# ── 主循环 ────────────────────────────────────────────────────

def run(cfg: Dict) -> None:
    """主运行循环"""
    global _consec_rejects, _consec_losses

    logger.info("=" * 70)
    logger.info("  足球直播总进球机器人启动")
    logger.info("  策略: 泊松定价 + +EV 入场 + 1/4 Kelly")
    logger.info("  模式: %s", "模拟" if cfg["dry_run"] else "真实下单")
    logger.info("  货币: %s", cfg["currency"])
    logger.info("  Edge 阈值: %.0f%%", cfg["edge_threshold"] * 100)
    logger.info("  xG 来源: %s", "API-Football" if cfg.get("af_key") else "先验估算（无 xG key）")
    logger.info("=" * 70)

    live_db.init_db(cfg["db_file"])
    client = CloudbetClient(cfg["api_key"])

    # 获取起始余额
    try:
        start_balance = client.get_balance(cfg["currency"])
    except Exception:
        start_balance = 100.0
        logger.warning("无法获取余额，使用默认值 %.2f", start_balance)

    if start_balance <= 0:
        start_balance = 100.0

    cfg["bankroll"] = start_balance
    logger.info("起始余额: %.2f %s", start_balance, cfg["currency"])

    round_count = 0
    while True:
        round_count += 1
        logger.info("\n%s — 第 %d 轮 (%s)", "─" * 50, round_count,
                    datetime.now().strftime("%H:%M:%S"))

        # 更新余额
        try:
            balance = client.get_balance(cfg["currency"])
            if balance > 0:
                cfg["bankroll"] = balance
        except Exception:
            balance = cfg.get("bankroll", start_balance)

        # 风控检查
        stop_reason = check_risk_limits(cfg, balance, start_balance)
        if stop_reason:
            logger.warning("⚠️  熔断: %s", stop_reason)
            wait_secs = cfg["sleep_interval"] * 4
            logger.info("暂停 %d 秒后重新检查...", wait_secs)
            time.sleep(wait_secs)
            # 部分计数器自动恢复（人工复盘后重启程序重置）
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

        for signal in signals:
            event_id = signal["event_id"]

            if event_id in _bet_events:
                continue

            log_soccer_signal(signal)

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
                        "scoring_rate_per_min": signal["model_result"].get("rem_home_lambda", 0) + signal["model_result"].get("rem_away_lambda", 0),
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
            result = execute_signal(client, cfg, signal)

            # 更新状态
            if result["success"]:
                live_db.update_order_status(
                    pending_ref, "ACCEPTED",
                    executed_price=result["executed_price"],
                    db_file=cfg["db_file"],
                )
                _bet_events.add(event_id)
                _consec_rejects = 0
                logger.info(
                    "✅ 成交: %s | %s %.2f @ %.3f | ref=%s",
                    signal["match"],
                    signal["side"].upper(),
                    signal["stake"],
                    result["executed_price"],
                    result["reference_id"],
                )
            else:
                live_db.update_order_status(
                    pending_ref, "REJECTED",
                    reject_reason=result["reject_reason"],
                    db_file=cfg["db_file"],
                )
                _consec_rejects += 1
                logger.warning(
                    "❌ 拒单 #%d: %s | 原因=%s",
                    _consec_rejects, signal["match"], result["reject_reason"]
                )

        logger.info("等待 %d 秒...", cfg["sleep_interval"])
        time.sleep(cfg["sleep_interval"])


# ── CLI 入口 ──────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="足球直播总进球投注机器人")
    p.add_argument("--dry-run", action="store_true", help="模拟模式（默认）")
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
    if args.dry_run or (not args.real and not args.play):
        cfg["dry_run"] = True

    if args.real:
        cfg["currency"] = "USDT"
        cfg["dry_run"] = False

    if args.play:
        cfg["currency"] = "PLAY_EUR"
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
