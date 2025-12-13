"""
检查投注历史和状态工具
根据Cloudbet API文档查询投注记录
"""

import requests
import json
from datetime import datetime, timedelta
from config import API_KEY, CURRENCY

# API端点
BET_HISTORY_URL = "https://sports-api.cloudbet.com/pub/v4/bets/history"
BET_STATUS_URL = "https://sports-api.cloudbet.com/pub/v3/bets/{}/status"
ACCOUNT_URL = "https://sports-api.cloudbet.com/pub/v1/account/currencies/{}/balance"

def get_bet_history(limit=50):
    """
    获取投注历史
    使用 v4 API: GET /pub/v4/bets/history
    """
    headers = {"X-API-Key": API_KEY}
    params = {
        "limit": limit,
        "currency": CURRENCY
    }

    try:
        response = requests.get(BET_HISTORY_URL, headers=headers, params=params, timeout=15)
        print(f"\n📡 投注历史API响应: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            return data
        else:
            print(f"❌ 错误响应: {response.text}")
            return None

    except Exception as e:
        print(f"❌ 请求异常: {e}")
        return None


def get_bet_status(reference_id):
    """
    查询单个投注状态
    使用 v3 API: GET /pub/v3/bets/{referenceId}/status
    """
    headers = {"X-API-Key": API_KEY}
    url = BET_STATUS_URL.format(reference_id)

    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    except:
        return None


def get_account_balance():
    """获取账户余额"""
    headers = {"X-API-Key": API_KEY}
    url = ACCOUNT_URL.format(CURRENCY)

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data.get("amount", 0))
        else:
            return None
    except:
        return None


def analyze_bet_history(history_data):
    """分析投注历史数据"""
    if not history_data:
        print("\n❌ 无法获取投注历史")
        return

    bets = history_data.get("bets", [])

    print("\n" + "="*80)
    print("📊 投注历史分析")
    print("="*80)

    if not bets:
        print("\n⚠️  没有任何投注记录！")
        print("\n可能的原因：")
        print("  1. DRY_RUN=True，处于模拟模式，没有实际下注")
        print("  2. 一直没有找到符合条件的比赛")
        print("  3. 投注请求被拒绝（赔率变化、余额不足等）")
        print("  4. API密钥权限不足")
        return

    print(f"\n总投注数: {len(bets)}")

    # 统计各状态
    status_count = {}
    total_stake = 0
    total_return = 0

    for bet in bets:
        status = bet.get("status", "UNKNOWN")
        status_count[status] = status_count.get(status, 0) + 1

        stake = float(bet.get("stake", 0))
        total_stake += stake

        # 如果已结算
        if bet.get("return"):
            total_return += float(bet.get("return", 0))

    print("\n【状态分布】")
    for status, count in status_count.items():
        print(f"  {status}: {count}")

    print("\n【资金统计】")
    print(f"  总投注: {total_stake:.2f} {CURRENCY}")
    print(f"  总返还: {total_return:.2f} {CURRENCY}")
    print(f"  盈亏: {total_return - total_stake:+.2f} {CURRENCY}")

    # 显示最近5笔
    print("\n【最近5笔投注】")
    for i, bet in enumerate(bets[:5]):
        print(f"\n  {i+1}. 参考ID: {bet.get('referenceId', 'N/A')}")
        print(f"     状态: {bet.get('status', 'N/A')}")
        print(f"     投注: {bet.get('stake', 0)} {CURRENCY}")
        print(f"     赔率: {bet.get('price', 'N/A')}")
        print(f"     市场: {bet.get('marketUrl', 'N/A')}")

        created = bet.get("createTime")
        if created:
            print(f"     时间: {created}")


def check_current_config():
    """检查当前配置"""
    from config import DRY_RUN, LEAGUE_MODE, MATCH_TIME_BEFORE, TARGET_ODDS_LOW, TARGET_ODDS_HIGH

    print("\n" + "="*80)
    print("⚙️  当前配置检查")
    print("="*80)

    print(f"\n  模式: {'🟡 模拟模式' if DRY_RUN else '🔴 真实下单'}")
    print(f"  联赛: {LEAGUE_MODE}")
    print(f"  时间窗口: {MATCH_TIME_BEFORE}分钟")
    print(f"  赔率范围: {TARGET_ODDS_LOW} - {TARGET_ODDS_HIGH}")

    if DRY_RUN:
        print("\n  ⚠️  当前是模拟模式，不会实际下注！")
        print("  如需真实下注，请修改 config.py: DRY_RUN = False")


def main():
    """主函数"""
    print("\n" + "="*80)
    print("🔍 Cloudbet 投注状态检查工具")
    print("="*80)

    # 检查配置
    check_current_config()

    # 检查余额
    balance = get_account_balance()
    if balance is not None:
        print(f"\n💰 当前余额: {balance:.2f} {CURRENCY}")
    else:
        print("\n⚠️  无法获取账户余额")

    # 获取投注历史
    print("\n📜 正在获取投注历史...")
    history = get_bet_history(limit=50)

    # 分析历史
    analyze_bet_history(history)

    # 诊断建议
    print("\n" + "="*80)
    print("💡 诊断建议")
    print("="*80)

    print("\n如果一直没有成交，请检查：")
    print("\n1. 【配置问题】")
    print("   - DRY_RUN 是否设置为 False（真实下单）")
    print("   - 运行: python bot.py 查看是否找到比赛")

    print("\n2. 【筛选问题】")
    print("   - 运行: python debug_matcher.py 查看筛选统计")
    print("   - 可能时间窗口太窄（MATCH_TIME_BEFORE 太小）")
    print("   - 可能联赛范围太窄（当前没有符合的比赛）")

    print("\n3. 【投注问题】")
    print("   - 查看 bot.log 文件，搜索 '投注失败'")
    print("   - 可能赔率已变化")
    print("   - 可能余额不足")
    print("   - 可能API限流（2 RPS）")

    print("\n4. 【API问题】")
    print("   - API密钥是否正确")
    print("   - API密钥是否过期")
    print("   - 是否在限制地区（美国、英国、中国等）")

    print("\n5. 【查看实时日志】")
    print("   - tail -f bot.log")
    print("   - 或查看最新日志: tail -100 bot.log")

    print("\n" + "="*80)


if __name__ == '__main__':
    main()
