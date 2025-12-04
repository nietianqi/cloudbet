"""
资金管理模块 - 2.5倍马丁格尔策略
"""

import csv
from datetime import datetime
from config import R, Q, N, MAX_ONE, MIN_BET, DAILY_LOSS_LIMIT, DAILY_PROFIT_TARGET


def calc_base_stake(balance, r=R, q=Q, n=N):
    """
    倒推首注金额公式：
    s0 = r * B * (q - 1) / (q^(N+1) - 1)

    参数:
        balance: 当前余额
        r: 单序列占资金比例 (默认0.3)
        q: 倍率 (默认2.5)
        n: 最大连输次数 (默认4)

    返回:
        首注金额
    """
    return r * balance * (q - 1) / (q ** (n + 1) - 1)


def current_loss_streak(logs):
    """
    从日志末尾往前计算连续亏损次数

    规则:
        - 遇到 LOSE: 计数+1
        - 遇到 WIN: 停止计数
        - 遇到 PUSH/其他: 跳过不计入

    参数:
        logs: 投注日志列表

    返回:
        连续亏损次数
    """
    streak = 0
    for log in reversed(logs):
        result = log.get("Result", "").strip().upper()
        if result == "LOSE":
            streak += 1
        elif result == "WIN":
            break
        # PUSH 或 PENDING 不计入，继续往前
    return streak


def calc_today_pnl(logs, balance):
    """
    计算今日盈亏

    参数:
        logs: 投注日志列表
        balance: 当前余额

    返回:
        today_pnl: 今日盈亏金额
        day_start_balance: 今日开始余额
    """
    today = datetime.utcnow().date()
    day_pnl = 0.0
    day_start_balance = balance

    # 找到今天第一笔记录之前的余额
    first_today_log = None
    for log in logs:
        timestamp_str = log.get("Timestamp", "")
        if not timestamp_str:
            continue

        try:
            log_date = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).date()
            if log_date == today:
                if first_today_log is None:
                    first_today_log = log
                    # 今日开始余额 = 当前余额 - 今日所有盈亏
                    break
        except:
            continue

    # 计算今日所有盈亏
    for log in logs:
        timestamp_str = log.get("Timestamp", "")
        if not timestamp_str:
            continue

        try:
            log_date = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).date()
            if log_date == today:
                pnl_str = log.get("PnL", "0")
                try:
                    day_pnl += float(pnl_str)
                except:
                    pass
        except:
            continue

    # 倒推今日开始余额
    day_start_balance = balance - day_pnl

    return day_pnl, day_start_balance


def next_bet_amount(logs, balance, r=R, q=Q, n=N, max_one=MAX_ONE):
    """
    计算下一注金额（2.5倍马丁格尔策略）

    流程:
        1. 检查日内风控（超过亏损限制则停止）
        2. 计算当前连输次数
        3. 检查是否超过最大连输次数
        4. 计算首注金额 s0
        5. 计算当前注金额 = s0 * (q ^ loss_streak)
        6. 应用最小和最大限制

    参数:
        logs: 投注日志
        balance: 当前余额
        r: 单序列资金比例
        q: 倍率
        n: 最大连输次数
        max_one: 单场最大投注比例

    返回:
        stake: 下注金额（0表示停止下注）
        loss_streak: 当前连输次数
        stop_reason: 停止原因（如果有）
    """

    # 1. 日内风控检查
    day_pnl, day_start_balance = calc_today_pnl(logs, balance)

    # 检查单日亏损限制
    if day_start_balance > 0:
        daily_loss_pct = abs(day_pnl) / day_start_balance
        if day_pnl < 0 and daily_loss_pct >= DAILY_LOSS_LIMIT:
            return 0.0, 0, f"达到单日亏损限制 {daily_loss_pct*100:.1f}%"

    # 检查单日盈利目标
    if DAILY_PROFIT_TARGET > 0 and day_start_balance > 0:
        daily_profit_pct = day_pnl / day_start_balance
        if day_pnl > 0 and daily_profit_pct >= DAILY_PROFIT_TARGET:
            return 0.0, 0, f"达到单日盈利目标 {daily_profit_pct*100:.1f}%"

    # 2. 计算连输次数
    loss_streak = current_loss_streak(logs)

    # 3. 检查是否超过最大连输
    if loss_streak > n:
        return 0.0, loss_streak, f"超过最大连输次数 {n}"

    # 4. 计算首注金额
    s0 = calc_base_stake(balance, r=r, q=q, n=n)

    # 5. 计算当前注金额
    stake = s0 * (q ** loss_streak)

    # 6. 应用限制
    stake = max(stake, MIN_BET)                    # 最小限制
    stake = min(stake, balance * max_one)          # 最大限制
    stake = min(stake, balance - 1.0)              # 留1 USDT余额

    # 检查余额是否充足
    if stake < MIN_BET or balance < MIN_BET * 2:
        return 0.0, loss_streak, "余额不足"

    return round(stake, 2), loss_streak, None


def format_bet_stats(balance, stake, loss_streak, day_pnl=0):
    """
    格式化投注统计信息（用于日志输出）

    返回:
        格式化的字符串
    """
    lines = [
        f"余额: {balance:.2f} USDT",
        f"连败: {loss_streak}次",
        f"本次: {stake:.2f} USDT",
        f"今日: {day_pnl:+.2f} USDT",
    ]
    return " | ".join(lines)


# ========================================
# 测试代码
# ========================================
if __name__ == '__main__':
    # 测试资金管理逻辑
    print("="*60)
    print("资金管理测试 - 2.5倍马丁格尔")
    print("="*60)

    test_balance = 100
    print(f"\n假设余额: {test_balance} USDT")
    print(f"配置: R={R}, Q={Q}, N={N}, MAX_ONE={MAX_ONE}")

    # 计算首注
    s0 = calc_base_stake(test_balance)
    print(f"\n首注金额 s0 = {s0:.4f} USDT")

    # 模拟连败序列
    print(f"\n连败序列:")
    print(f"{'轮次':<6} {'连败':<6} {'投注':<10} {'累计':<10} {'占比':<8}")
    print("-"*50)

    total_risk = 0
    for k in range(N+1):
        stake = s0 * (Q ** k)
        stake = min(stake, test_balance * MAX_ONE)
        total_risk += stake
        risk_pct = total_risk / test_balance * 100

        print(f"{k+1:<6} {k:<6} {stake:<10.2f} {total_risk:<10.2f} {risk_pct:<8.1f}%")

    print(f"\n最坏情况: 连败{N+1}次，损失 {total_risk:.2f} USDT ({total_risk/test_balance*100:.1f}%)")

    # 测试连败检测
    print(f"\n" + "="*60)
    print("连败检测测试")
    print("="*60)

    test_logs = [
        {"Result": "WIN"},
        {"Result": "LOSE"},
        {"Result": "LOSE"},
        {"Result": "PENDING"},
        {"Result": "LOSE"},
    ]

    streak = current_loss_streak(test_logs)
    print(f"测试日志: {[log['Result'] for log in test_logs]}")
    print(f"连败次数: {streak} (预期: 3)")
