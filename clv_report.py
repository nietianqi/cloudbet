"""
CLV（收盘线价值）分析报告工具
================================
CLV 是衡量投注系统是否有真实优势的核心 KPI：

  CLV = bet_price - closing_price  （对 Over 而言）
  CLV% = (bet_price / closing_price - 1) × 100

解读：
  avg_CLV% > 0 → 你长期下单价优于收盘价 → 正期望，盈利只是时间问题
  avg_CLV% ≤ 0 → 策略无 edge，无论如何调仓位都无法长期盈利

运行方式:
  python clv_report.py                    # 分析默认数据库
  python clv_report.py --settle           # 批量查询未结算订单
  python clv_report.py --db other.db      # 指定数据库文件
"""

import argparse
import logging
import sys
from datetime import datetime
from typing import Dict, List, Optional

import requests

import live_db

logger = logging.getLogger(__name__)


# ── 从 Cloudbet API 拉取单笔下注的结算信息 ────────────────────

def fetch_bet_status(reference_id: str, api_key: str) -> Optional[Dict]:
    """查询单笔投注状态（GET /pub/v3/bets/{referenceId}/status）"""
    url = f"https://sports-api.cloudbet.com/pub/v3/bets/{reference_id}/status"
    try:
        resp = requests.get(url, headers={"X-API-Key": api_key}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException as exc:
        logger.debug("查询 %s 失败: %s", reference_id, exc)
    return None


def fetch_bet_history(api_key: str, limit: int = 100) -> List[Dict]:
    """批量拉取投注历史（GET /pub/v4/bets/history）"""
    url = "https://sports-api.cloudbet.com/pub/v4/bets/history"
    try:
        resp = requests.get(
            url,
            headers={"X-API-Key": api_key},
            params={"limit": limit},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("bets", [])
        logger.warning("获取历史失败: %d", resp.status_code)
    except requests.RequestException as exc:
        logger.error("批量获取失败: %s", exc)
    return []


# ── CLV 计算 ──────────────────────────────────────────────────

def compute_clv(bet_price: float, closing_price: float, side: str = "over") -> Dict:
    """
    计算 CLV

    对 Over：下注赔率越高越好（你买到了更高赔率）
    对 Under：同理

    CLV   = bet_price - closing_price   （正值 = 你占优）
    CLV%  = (bet_price / closing_price - 1) × 100
    """
    if closing_price <= 1.0 or bet_price <= 1.0:
        return {"clv": 0.0, "clv_percent": 0.0}

    clv = round(bet_price - closing_price, 4)
    clv_pct = round((bet_price / closing_price - 1.0) * 100, 3)
    return {"clv": clv, "clv_percent": clv_pct}


# ── 批量结算（settle 模式）───────────────────────────────────

def settle_pending_orders(api_key: str, db_file: str) -> int:
    """
    查询所有已成交但未结算的订单，写入结算结果

    返回:
        已结算的订单数量
    """
    pending = live_db.get_accepted_orders(db_file)
    if not pending:
        print("无待结算订单")
        return 0

    print(f"发现 {len(pending)} 笔待结算订单，开始查询...")
    settled_count = 0

    for order in pending:
        ref_id = order.get("reference_id", "")
        if not ref_id or ref_id.startswith("DRY-"):
            continue

        data = fetch_bet_status(ref_id, api_key)
        if not data:
            continue

        status = data.get("status", "")
        if status not in ("WIN", "LOSE", "PUSH", "SETTLED"):
            continue

        stake = float(order.get("stake") or 0)
        bet_price = float(order.get("executed_price") or order.get("requested_price") or 1.0)
        closing_price = float(data.get("closingPrice") or data.get("price") or bet_price)

        # 计算 CLV
        side = order.get("side", "over")
        clv_info = compute_clv(bet_price, closing_price, side)

        # 计算 PnL
        pnl = 0.0
        if status == "WIN":
            pnl = stake * (bet_price - 1.0)
        elif status == "LOSE":
            pnl = -stake

        # 写入 results 表
        live_db.insert_result(
            {
                "reference_id": ref_id,
                "event_id": order.get("event_id", ""),
                "match": order.get("match", ""),
                "side": side,
                "stake": stake,
                "bet_price": bet_price,
                "closing_price": closing_price,
                "clv": clv_info["clv"],
                "clv_percent": clv_info["clv_percent"],
                "outcome": status,
                "pnl": round(pnl, 4),
                "final_score": data.get("score"),
                "final_total": data.get("line"),
            },
            db_file=db_file,
        )
        settled_count += 1
        print(
            f"  ✅ 结算: {order.get('match', ref_id)} | "
            f"结果={status} | PnL={pnl:+.2f} | CLV={clv_info['clv']:+.3f}"
        )

    return settled_count


# ── 报告生成 ──────────────────────────────────────────────────

def print_clv_report(db_file: str) -> None:
    """打印 CLV + 盈亏综合报告"""
    stats = live_db.get_clv_summary(db_file)

    print("\n" + "=" * 70)
    print("  CLV（收盘线价值）分析报告")
    print("=" * 70)

    if not stats or not stats.get("total_settled"):
        print("\n  暂无已结算记录（运行 --settle 先拉取结算数据）")
        print("=" * 70)
        return

    total = stats.get("total_settled", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    avg_clv = stats.get("avg_clv", 0) or 0
    avg_clv_pct = stats.get("avg_clv_pct", 0) or 0
    pos_clv = stats.get("positive_clv_count", 0)
    total_pnl = stats.get("total_pnl", 0) or 0
    total_stake = stats.get("total_stake", 0) or 0
    roi = stats.get("roi", 0) or 0

    win_rate = (wins / total * 100) if total > 0 else 0
    pos_clv_rate = (pos_clv / total * 100) if total > 0 else 0

    print(f"\n  已结算: {total} 笔 | 胜: {wins} | 负: {losses}")
    print(f"  胜率: {win_rate:.1f}%")
    print()
    print(f"  ── CLV（核心 KPI）────────────────────────────────")
    clv_symbol = "✅" if avg_clv_pct > 0 else "⚠️ "
    print(f"  {clv_symbol} 平均 CLV:     {avg_clv:+.4f} 赔率单位")
    print(f"  {clv_symbol} 平均 CLV%:    {avg_clv_pct:+.3f}%")
    print(f"  正 CLV 占比:  {pos_clv_rate:.1f}% ({pos_clv}/{total})")
    print()
    print(f"  ── 盈亏 ───────────────────────────────────────────")
    pnl_symbol = "✅" if total_pnl >= 0 else "🔴"
    print(f"  {pnl_symbol} 总盈亏:       {total_pnl:+.2f} USDT")
    print(f"  总投注额:     {total_stake:.2f} USDT")
    roi_symbol = "✅" if roi >= 0 else "🔴"
    print(f"  {roi_symbol} ROI:          {roi * 100:+.2f}%")
    print()
    print(f"  ── 解读 ────────────────────────────────────────────")

    if avg_clv_pct > 1.0:
        print("  ✅ 策略有明确优势（avg CLV% > 1%），可考虑逐步加注")
    elif avg_clv_pct > 0:
        print("  ✅ 策略方向正确（CLV% > 0），继续积累样本验证")
    elif avg_clv_pct > -1.0:
        print("  ⚠️  CLV 接近零，需要更多样本或收紧 edge 阈值")
    else:
        print("  🔴 CLV 显著为负，信号质量不足，建议暂停并回测策略")

    if total < 30:
        print(f"\n  ⚠️  样本量 {total} < 30，统计结论参考价值有限")

    print("=" * 70)


def print_order_stats(db_file: str) -> None:
    """打印订单统计（拒单率等）"""
    rejection_rate = live_db.get_rejection_rate(100, db_file)
    recent_accepted = live_db.get_recent_orders_count(30, db_file)

    print("\n  ── 执行质量 ────────────────────────────────────────")
    rr_symbol = "✅" if rejection_rate < 0.3 else ("⚠️ " if rejection_rate < 0.7 else "🔴")
    print(f"  {rr_symbol} 近 100 笔拒单率: {rejection_rate:.1%}")
    print(f"  近 30 分钟成交数: {recent_accepted}")
    if rejection_rate > 0.7:
        print("  🔴 拒单率过高！Cloudbet 可能已标记账户，建议降低下注频率")
    print()


# ── CLI 入口 ──────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="CLV 分析报告工具")
    parser.add_argument("--settle", action="store_true",
                        help="批量查询并写入未结算订单")
    parser.add_argument("--db", default="live_betting.db",
                        help="数据库文件路径（默认: live_betting.db）")
    parser.add_argument("--api-key", default=None,
                        help="Cloudbet API Key（--settle 时需要）")
    return parser.parse_args()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()
    db_file = args.db

    # 确保数据库存在
    import os
    if not os.path.exists(db_file):
        print(f"数据库文件不存在: {db_file}，请先运行 nba_bot.py")
        sys.exit(1)

    # 结算模式
    if args.settle:
        api_key = args.api_key or os.environ.get("CLOUDBET_API_KEY", "")
        if not api_key:
            print("--settle 模式需要 API Key：--api-key YOUR_KEY 或 CLOUDBET_API_KEY 环境变量")
            sys.exit(1)
        settled = settle_pending_orders(api_key, db_file)
        print(f"\n本次结算: {settled} 笔")

    # 报告
    print_order_stats(db_file)
    print_clv_report(db_file)


if __name__ == "__main__":
    main()
