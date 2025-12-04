"""
调试工具 - 查看筛选过程的详细信息
用于诊断为什么找不到符合条件的比赛
"""

import requests
import time
import logging
from datetime import datetime
from config import API_KEY, BASE_URL, TARGET_HANDICAP, TARGET_ODDS_LOW, TARGET_ODDS_HIGH
from config import SAFE_ODDS_LOW, SAFE_ODDS_HIGH, MAX_HOME_WIN_ODDS, MATCH_TIME_BEFORE
from matcher import ACTIVE_LEAGUES

logging.basicConfig(level=logging.INFO, format='%(message)s')


def debug_fetch_matches():
    """调试：获取所有即将开始的比赛并显示筛选过程"""

    now = int(time.time())
    future = now + 60 * 60  # 未来1小时的比赛

    headers = {"X-API-Key": API_KEY}
    params = {
        "sport": "soccer",
        "from": now,
        "to": future,
        "markets": "soccer.asian_handicap,soccer.match_winner",
        "limit": 1000
    }

    print("="*80)
    print("🔍 调试模式 - 查看筛选过程")
    print("="*80)
    print(f"\n查询时间范围: 现在 到 未来60分钟")
    print(f"激活联赛数: {len(ACTIVE_LEAGUES)}")
    print(f"\n筛选条件:")
    print(f"  让球盘: {TARGET_HANDICAP}")
    print(f"  赔率范围: {TARGET_ODDS_LOW} - {TARGET_ODDS_HIGH}")
    print(f"  安全区: {SAFE_ODDS_LOW} - {SAFE_ODDS_HIGH}")
    print(f"  独赢上限: {MAX_HOME_WIN_ODDS}")
    print(f"  开赛时间: {MATCH_TIME_BEFORE} 分钟 (±30秒)")
    print("\n" + "="*80)

    try:
        response = requests.get(BASE_URL, headers=headers, params=params, timeout=15)
        if response.status_code != 200:
            print(f"❌ API错误: {response.status_code}")
            return

        data = response.json()
        competitions = data.get("competitions", [])

        if not competitions:
            print("\n❌ 未找到任何比赛")
            return

        print(f"\n✅ API返回 {len(competitions)} 个联赛")

        total_events = 0
        filtered_by_league = 0
        filtered_by_time = 0
        filtered_by_handicap = 0
        filtered_by_odds = 0
        filtered_by_safe_odds = 0
        filtered_by_home_win = 0
        passed_all = 0

        for comp in competitions:
            comp_name = comp.get("name", "")
            comp_key = comp.get("key", "")

            # 跳过虚拟比赛
            if "virtual" in comp_name.lower():
                continue

            events = comp.get("events", [])
            if not events:
                continue

            total_events += len(events)

            # 检查是否在激活联赛中
            in_active = comp_key in ACTIVE_LEAGUES
            if not in_active:
                filtered_by_league += len(events)
                print(f"\n❌ {comp_name} ({comp_key})")
                print(f"   不在激活联赛列表中 (共{len(events)}场比赛)")
                continue

            print(f"\n📌 {comp_name} ({comp_key})")
            print(f"   在激活联赛: ✅")

            for event in events:
                home = event.get("home", {}).get("name", "N/A")
                away = event.get("away", {}).get("name", "N/A")
                match_name = f"{home} vs {away}"

                # 解析时间
                cutoff = event.get("cutoffTime")
                if not cutoff:
                    continue

                try:
                    event_dt = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
                    event_ts = int(event_dt.timestamp())
                    time_to_match = (event_ts - now) / 60  # 分钟
                except:
                    continue

                # 时间筛选
                time_min = MATCH_TIME_BEFORE - 0.5
                time_max = MATCH_TIME_BEFORE + 0.5

                if not (time_min <= time_to_match <= time_max):
                    filtered_by_time += 1
                    print(f"   ⏰ {match_name}")
                    print(f"      开赛: {time_to_match:.1f}分钟后 (需要{time_min:.1f}-{time_max:.1f}分钟)")
                    continue

                print(f"   ✅ {match_name}")
                print(f"      开赛: {time_to_match:.1f}分钟后 ✅")

                # 解析市场
                markets = event.get("markets", {})

                # 找亚洲让球盘
                ahc_odds = None
                for market_key, market in markets.items():
                    if "asian_handicap" not in market_key.lower():
                        continue
                    for sub in market.get("submarkets", {}).values():
                        for sel in sub.get("selections", []):
                            if (sel.get("outcome") == "home" and
                                sel.get("params") == f"handicap={TARGET_HANDICAP}"):
                                ahc_odds = sel.get("price")
                                break

                if not ahc_odds:
                    filtered_by_handicap += 1
                    print(f"      让球盘: ❌ 无 {TARGET_HANDICAP} 盘口")
                    continue

                print(f"      让球盘: {ahc_odds} ✅")

                # 赔率范围
                if not (TARGET_ODDS_LOW <= ahc_odds <= TARGET_ODDS_HIGH):
                    filtered_by_odds += 1
                    print(f"      赔率范围: ❌ {ahc_odds} 不在 {TARGET_ODDS_LOW}-{TARGET_ODDS_HIGH}")
                    continue

                print(f"      赔率范围: ✅")

                # 安全区
                if not (SAFE_ODDS_LOW <= ahc_odds <= SAFE_ODDS_HIGH):
                    filtered_by_safe_odds += 1
                    print(f"      安全区: ❌ {ahc_odds} 不在 {SAFE_ODDS_LOW}-{SAFE_ODDS_HIGH}")
                    continue

                print(f"      安全区: ✅")

                # 找独赢赔率
                home_win_odds = None
                for market_key, market in markets.items():
                    if "match_winner" not in market_key.lower():
                        continue
                    for sub in market.get("submarkets", {}).values():
                        for sel in sub.get("selections", []):
                            if sel.get("outcome") == "home":
                                home_win_odds = sel.get("price")
                                break

                if not home_win_odds:
                    filtered_by_home_win += 1
                    print(f"      独赢赔率: ❌ 无数据")
                    continue

                print(f"      独赢赔率: {home_win_odds}")

                if home_win_odds > MAX_HOME_WIN_ODDS:
                    filtered_by_home_win += 1
                    print(f"      独赢限制: ❌ {home_win_odds} > {MAX_HOME_WIN_ODDS}")
                    continue

                print(f"      独赢限制: ✅")
                print(f"      🎯 通过所有筛选！")
                passed_all += 1

        print("\n" + "="*80)
        print("📊 筛选统计")
        print("="*80)
        print(f"总比赛数: {total_events}")
        print(f"  ❌ 联赛筛选: -{filtered_by_league}")
        time_min = MATCH_TIME_BEFORE - 0.5
        time_max = MATCH_TIME_BEFORE + 0.5
        print(f"  ❌ 时间筛选: -{filtered_by_time} (需要离开赛{time_min:.1f}-{time_max:.1f}分钟)")
        print(f"  ❌ 无-1盘口: -{filtered_by_handicap}")
        print(f"  ❌ 赔率范围: -{filtered_by_odds} (需要{TARGET_ODDS_LOW}-{TARGET_ODDS_HIGH})")
        print(f"  ❌ 安全区外: -{filtered_by_safe_odds} (需要{SAFE_ODDS_LOW}-{SAFE_ODDS_HIGH})")
        print(f"  ❌ 独赢过高: -{filtered_by_home_win} (需要<{MAX_HOME_WIN_ODDS})")
        print(f"  ✅ 通过所有: {passed_all}")
        print("="*80)

        # 建议
        if passed_all == 0:
            print("\n💡 优化建议:")
            if filtered_by_time > total_events * 0.5:
                print("  1. 放宽时间窗口: MATCH_TIME_BEFORE = 5")
            if filtered_by_safe_odds > 0:
                print("  2. 禁用安全区或放宽: SAFE_ODDS_HIGH = 1.95")
            if filtered_by_home_win > 0:
                print("  3. 放宽独赢限制: MAX_HOME_WIN_ODDS = 1.70")
            if filtered_by_handicap > 0:
                print("  4. 当前时段可能-1盘口较少")

    except Exception as e:
        print(f"\n❌ 异常: {e}")


if __name__ == '__main__':
    debug_fetch_matches()
