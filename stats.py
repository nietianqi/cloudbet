"""
投注统计分析工具
分析bet_log.csv中的历史数据，提供胜率、盈亏等统计信息
"""

import csv
import os
from datetime import datetime
from collections import defaultdict
from config import LOG_FILE


def load_bet_logs():
    """加载投注日志"""
    logs = []
    if not os.path.exists(LOG_FILE):
        print(f"日志文件不存在: {LOG_FILE}")
        return logs

    with open(LOG_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            logs.append(row)

    return logs


def calculate_statistics(logs):
    """
    计算统计数据

    返回:
        stats: 统计字典
    """
    if not logs:
        return None

    stats = {
        'total_bets': 0,
        'wins': 0,
        'losses': 0,
        'pushes': 0,
        'pending': 0,
        'win_rate': 0.0,
        'total_stake': 0.0,
        'total_pnl': 0.0,
        'roi': 0.0,
        'max_streak_win': 0,
        'max_streak_loss': 0,
        'avg_odds': 0.0,
        'by_league': defaultdict(lambda: {'bets': 0, 'wins': 0, 'pnl': 0.0}),
        'by_date': defaultdict(lambda: {'bets': 0, 'wins': 0, 'pnl': 0.0}),
    }

    current_streak = 0
    current_streak_type = None
    max_win_streak = 0
    max_loss_streak = 0

    odds_sum = 0.0
    odds_count = 0

    for log in logs:
        result = log.get('Result', '').strip().upper()

        # 只统计已完成的投注
        if result in ['WIN', 'LOSE', 'PUSH']:
            stats['total_bets'] += 1

            # 投注金额
            try:
                stake = float(log.get('Stake', 0))
                stats['total_stake'] += stake
            except:
                pass

            # 盈亏
            try:
                pnl = float(log.get('PnL', 0))
                stats['total_pnl'] += pnl
            except:
                pass

            # 赔率
            try:
                odds = float(log.get('AHC_Odds', 0))
                if odds > 0:
                    odds_sum += odds
                    odds_count += 1
            except:
                pass

            # 结果统计
            if result == 'WIN':
                stats['wins'] += 1
                if current_streak_type == 'WIN':
                    current_streak += 1
                else:
                    current_streak = 1
                    current_streak_type = 'WIN'
                max_win_streak = max(max_win_streak, current_streak)

            elif result == 'LOSE':
                stats['losses'] += 1
                if current_streak_type == 'LOSE':
                    current_streak += 1
                else:
                    current_streak = 1
                    current_streak_type = 'LOSE'
                max_loss_streak = max(max_loss_streak, current_streak)

            elif result == 'PUSH':
                stats['pushes'] += 1

            # 按联赛统计
            league = log.get('League', 'Unknown')
            stats['by_league'][league]['bets'] += 1
            if result == 'WIN':
                stats['by_league'][league]['wins'] += 1
            try:
                pnl = float(log.get('PnL', 0))
                stats['by_league'][league]['pnl'] += pnl
            except:
                pass

            # 按日期统计
            timestamp_str = log.get('Timestamp', '')
            if timestamp_str:
                try:
                    date = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).date()
                    date_str = date.strftime('%Y-%m-%d')
                    stats['by_date'][date_str]['bets'] += 1
                    if result == 'WIN':
                        stats['by_date'][date_str]['wins'] += 1
                    try:
                        pnl = float(log.get('PnL', 0))
                        stats['by_date'][date_str]['pnl'] += pnl
                    except:
                        pass
                except:
                    pass

        elif result == 'PENDING':
            stats['pending'] += 1

    # 计算胜率
    if stats['total_bets'] > 0:
        stats['win_rate'] = stats['wins'] / stats['total_bets'] * 100

    # 计算ROI
    if stats['total_stake'] > 0:
        stats['roi'] = stats['total_pnl'] / stats['total_stake'] * 100

    # 平均赔率
    if odds_count > 0:
        stats['avg_odds'] = odds_sum / odds_count

    stats['max_streak_win'] = max_win_streak
    stats['max_streak_loss'] = max_loss_streak

    return stats


def print_statistics(stats):
    """打印统计报告"""
    if not stats:
        print("无统计数据")
        return

    print("\n" + "="*70)
    print("📊 投注统计报告")
    print("="*70)

    # 总体统计
    print("\n【总体统计】")
    print(f"  总投注数: {stats['total_bets']}")
    print(f"  胜: {stats['wins']} | 负: {stats['losses']} | 平: {stats['pushes']} | 待定: {stats['pending']}")
    print(f"  胜率: {stats['win_rate']:.2f}%")
    print(f"  总投注额: {stats['total_stake']:.2f} USDT")
    print(f"  总盈亏: {stats['total_pnl']:+.2f} USDT")
    print(f"  ROI: {stats['roi']:+.2f}%")
    print(f"  平均赔率: {stats['avg_odds']:.3f}")
    print(f"  最大连胜: {stats['max_streak_win']} | 最大连败: {stats['max_streak_loss']}")

    # 按联赛统计（前10）
    if stats['by_league']:
        print("\n【联赛统计】（前10）")
        league_stats = sorted(
            stats['by_league'].items(),
            key=lambda x: x[1]['bets'],
            reverse=True
        )[:10]

        print(f"{'联赛':<40} {'投注':<6} {'胜':<6} {'胜率':<8} {'盈亏':<10}")
        print("-"*70)
        for league, data in league_stats:
            win_rate = (data['wins'] / data['bets'] * 100) if data['bets'] > 0 else 0
            league_short = league[:38] if len(league) > 38 else league
            print(f"{league_short:<40} {data['bets']:<6} {data['wins']:<6} {win_rate:<8.1f}% {data['pnl']:>+10.2f}")

    # 按日期统计（最近7天）
    if stats['by_date']:
        print("\n【日期统计】（最近7天）")
        date_stats = sorted(
            stats['by_date'].items(),
            key=lambda x: x[0],
            reverse=True
        )[:7]

        print(f"{'日期':<12} {'投注':<6} {'胜':<6} {'胜率':<8} {'盈亏':<10}")
        print("-"*50)
        for date, data in sorted(date_stats):
            win_rate = (data['wins'] / data['bets'] * 100) if data['bets'] > 0 else 0
            print(f"{date:<12} {data['bets']:<6} {data['wins']:<6} {win_rate:<8.1f}% {data['pnl']:>+10.2f}")

    print("\n" + "="*70)


def main():
    """主函数"""
    print("\n" + "="*70)
    print("📈 Cloudbet 投注统计分析")
    print("="*70)

    # 加载日志
    logs = load_bet_logs()

    if not logs:
        print("\n暂无投注记录")
        return

    print(f"\n加载了 {len(logs)} 条记录")

    # 计算统计
    stats = calculate_statistics(logs)

    # 打印报告
    print_statistics(stats)

    # 建议
    print("\n【优化建议】")
    if stats and stats['total_bets'] >= 10:
        if stats['win_rate'] < 50:
            print("  ⚠️  胜率较低，建议:")
            print("     - 收紧筛选条件（提高MIN_MATCH_SCORE）")
            print("     - 只投注TIER_1联赛")
            print("     - 缩小赔率范围到1.60-1.80")
        elif stats['win_rate'] >= 60:
            print("  ✅ 胜率良好！可以考虑:")
            print("     - 扩大联赛范围到EXPANDED模式")
            print("     - 适当增加投注窗口")

        if stats['roi'] < -10:
            print("  ⚠️  ROI过低，建议暂停投注，复查策略")
        elif stats['roi'] > 10:
            print("  ✅ ROI优秀！继续保持当前策略")

        if stats['max_streak_loss'] >= 3:
            print(f"  ⚠️  最大连败{stats['max_streak_loss']}次，建议检查:")
            print("     - 资金管理参数（Q值可能过大）")
            print("     - 筛选条件是否过于宽松")
    else:
        print("  数据量不足（<10场），需要更多样本")

    print()


if __name__ == '__main__':
    main()
