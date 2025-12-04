"""
快速配置向导
帮助用户配置和测试系统
"""

import os
import sys

def print_banner():
    print("\n" + "="*70)
    print("  Cloudbet 投注机器人 - 配置向导")
    print("="*70)

def check_api_key():
    """检查 API Key"""
    print("\n📌 步骤 1: 检查 API 配置")
    print("-" * 70)

    try:
        from config import API_KEY
        if len(API_KEY) > 50:
            print("✅ API Key 已配置")
            return True
        else:
            print("❌ API Key 未配置或不正确")
            print("\n请编辑 config.py 文件，填入你的 API Key：")
            print('  API_KEY = "你的API_KEY"')
            return False
    except ImportError:
        print("❌ 找不到 config.py 文件")
        return False

def test_connection():
    """测试 API 连接"""
    print("\n📌 步骤 2: 测试 API 连接")
    print("-" * 70)

    try:
        from config import API_KEY, ACCOUNT_URL, CURRENCY
        import requests

        url = f"{ACCOUNT_URL}/{CURRENCY}/balance"
        headers = {"X-API-Key": API_KEY}

        print("正在连接 Cloudbet API...")
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()
            balance = float(data.get("amount", 0))
            print(f"✅ 连接成功！")
            print(f"✅ 当前余额: {balance} {CURRENCY}")
            return True, balance
        else:
            print(f"❌ 连接失败: {response.status_code}")
            print(f"   错误信息: {response.text}")
            return False, 0

    except Exception as e:
        print(f"❌ 连接异常: {e}")
        return False, 0

def show_config():
    """显示当前配置"""
    print("\n📌 步骤 3: 当前配置")
    print("-" * 70)

    try:
        from config import (
            TARGET_HANDICAP, TARGET_ODDS_LOW, TARGET_ODDS_HIGH,
            SAFE_ODDS_LOW, SAFE_ODDS_HIGH, MAX_HOME_WIN_ODDS,
            R, Q, N, MAX_ONE, DAILY_LOSS_LIMIT, DAILY_PROFIT_TARGET,
            ENABLE_ODDS_TRACKING, DRY_RUN
        )

        print("\n【投注筛选】")
        print(f"  让球盘: {TARGET_HANDICAP}")
        print(f"  赔率范围: {TARGET_ODDS_LOW} - {TARGET_ODDS_HIGH}")
        print(f"  安全区: {SAFE_ODDS_LOW} - {SAFE_ODDS_HIGH}")
        print(f"  独赢上限: {MAX_HOME_WIN_ODDS}")

        print("\n【资金管理】")
        print(f"  单序列占比 R: {R*100}%")
        print(f"  倍率 Q: {Q}")
        print(f"  最大连败 N: {N}")
        print(f"  单场最大: {MAX_ONE*100}%")

        print("\n【风险控制】")
        print(f"  单日亏损限制: {DAILY_LOSS_LIMIT*100}%")
        print(f"  单日盈利目标: {DAILY_PROFIT_TARGET*100}%")

        print("\n【高级选项】")
        print(f"  赔率走势监控: {'启用' if ENABLE_ODDS_TRACKING else '禁用'}")
        print(f"  模拟运行模式: {'开启' if DRY_RUN else '关闭'}")

        return True
    except ImportError as e:
        print(f"❌ 配置文件错误: {e}")
        return False

def risk_assessment(balance):
    """风险评估"""
    print("\n📌 步骤 4: 风险评估")
    print("-" * 70)

    try:
        from config import R, Q, N, MAX_ONE
        from bankroll import calc_base_stake

        if balance < 50:
            print(f"⚠️  当前余额 {balance} USDT 较低")
            print("   建议至少 100 USDT 以获得更好的风险控制")

        # 计算首注
        s0 = calc_base_stake(balance, r=R, q=Q, n=N)
        print(f"\n基础投注: {s0:.4f} USDT ({s0/balance*100:.2f}%)")

        # 计算最坏情况
        total_risk = 0
        print(f"\n连败序列（最坏情况）:")
        print(f"{'轮次':<6} {'投注':<10} {'累计':<10} {'占余额':<10}")
        print("-" * 40)

        for k in range(N+1):
            stake = s0 * (Q ** k)
            stake = min(stake, balance * MAX_ONE)
            total_risk += stake
            risk_pct = total_risk / balance * 100
            print(f"{k+1:<6} {stake:<10.2f} {total_risk:<10.2f} {risk_pct:<10.1f}%")

        print(f"\n最坏情况: 连败 {N+1} 次，损失 {total_risk:.2f} USDT ({total_risk/balance*100:.1f}%)")

        if total_risk / balance > 0.5:
            print(f"\n⚠️  风险较高，建议:")
            print(f"   - 增加本金至 {total_risk * 2.5:.0f} USDT")
            print(f"   - 或降低 R 参数（如 0.2）")
            print(f"   - 或降低 Q 参数（如 2.0）")
        else:
            print(f"\n✅ 资金充足，风险可控")

        return True

    except Exception as e:
        print(f"❌ 评估失败: {e}")
        return False

def suggest_mode(balance):
    """建议运行模式"""
    print("\n📌 步骤 5: 运行建议")
    print("-" * 70)

    if balance < 100:
        print("💡 建议:")
        print("  1. 先开启模拟模式测试（DRY_RUN = True）")
        print("  2. 观察系统筛选的比赛质量")
        print("  3. 积累至少 100 USDT 后再实际运行")

    elif balance < 200:
        print("💡 建议:")
        print("  1. 使用保守配置（R=0.2, Q=2.0, N=5）")
        print("  2. 先运行 50-100 场测试")
        print("  3. 根据实际胜率调整参数")

    else:
        print("✅ 余额充足，可以使用当前配置")
        print("💡 建议:")
        print("  1. 严格执行止损策略")
        print("  2. 每天记录胜率和盈亏")
        print("  3. 达到盈利目标及时提现")

def final_check():
    """最终确认"""
    print("\n📌 最终确认")
    print("-" * 70)
    print("⚠️  重要提示:")
    print("  1. 投注有风险，可能导致资金损失")
    print("  2. 马丁格尔策略在连败时风险极大")
    print("  3. 需要至少 60% 胜率才能长期盈利")
    print("  4. 只用承受得起的闲钱")
    print("  5. 严格执行止损，不要情绪化")
    print()
    print("确认启动系统? (输入 yes 继续): ", end='')

    response = input().strip().lower()
    return response == 'yes'

def main():
    """主流程"""
    print_banner()

    # 1. 检查 API
    if not check_api_key():
        return

    # 2. 测试连接
    connected, balance = test_connection()
    if not connected:
        return

    # 3. 显示配置
    if not show_config():
        return

    # 4. 风险评估
    risk_assessment(balance)

    # 5. 运行建议
    suggest_mode(balance)

    # 6. 最终确认
    if not final_check():
        print("\n取消启动")
        return

    # 7. 启动主程序
    print("\n" + "="*70)
    print("🚀 启动投注机器人...")
    print("="*70)
    print()
    print("提示:")
    print("  - 按 Ctrl+C 可以安全退出")
    print("  - 日志保存在 bet_log.csv 和 bot.log")
    print()
    print("系统启动中...\n")

    try:
        import bot
        bot.main()
    except KeyboardInterrupt:
        print("\n\n用户中断，系统已停止")
    except Exception as e:
        print(f"\n\n❌ 系统异常: {e}")

if __name__ == '__main__':
    main()
