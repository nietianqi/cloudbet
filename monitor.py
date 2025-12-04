"""
实时监控工具 - 查看即将开始的比赛
显示何时会进入投注窗口
"""

import requests
import time
from datetime import datetime, timedelta
from config import API_KEY, BASE_URL, MATCH_TIME_BEFORE, TARGET_HANDICAP
from matcher import ACTIVE_LEAGUES

def monitor_upcoming_matches():
    """监控即将开始的比赛"""

    print("\n" + "="*80)
    print("⏰ 实时监控 - 即将开始的比赛")
    print("="*80)
    print(f"投注窗口: 开赛前 {MATCH_TIME_BEFORE}±0.5 分钟")
    print(f"激活联赛: {len(ACTIVE_LEAGUES)} 个")
    print("="*80)

    now = int(time.time())
    future = now + 120 * 60  # 未来2小时

    headers = {"X-API-Key": API_KEY}
    params = {
        "sport": "soccer",
        "from": now,
        "to": future,
        "markets": "soccer.asian_handicap",
        "limit": 1000
    }

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

        matches = []

        for comp in competitions:
            comp_name = comp.get("name", "")
            comp_key = comp.get("key", "")

            if "virtual" in comp_name.lower():
                continue

            if comp_key not in ACTIVE_LEAGUES:
                continue

            for event in comp.get("events", []):
                cutoff = event.get("cutoffTime")
                if not cutoff:
                    continue

                try:
                    event_dt = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
                    event_ts = int(event_dt.timestamp())
                    time_to_match = (event_ts - now) / 60
                except:
                    continue

                # 检查是否有-1盘口
                has_handicap = False
                ahc_odds = None
                markets = event.get("markets", {})
                for market_key, market in markets.items():
                    if "asian_handicap" not in market_key.lower():
                        continue
                    for sub in market.get("submarkets", {}).values():
                        for sel in sub.get("selections", []):
                            if (sel.get("outcome") == "home" and
                                sel.get("params") == f"handicap={TARGET_HANDICAP}"):
                                has_handicap = True
                                ahc_odds = sel.get("price")
                                break

                if not has_handicap:
                    continue

                home = event.get("home", {}).get("name", "N/A")
                away = event.get("away", {}).get("name", "N/A")

                # 计算何时进入投注窗口
                target_time = MATCH_TIME_BEFORE
                time_window_start = event_ts - (target_time + 0.5) * 60
                time_window_end = event_ts - (target_time - 0.5) * 60

                time_until_window = (time_window_start - now) / 60

                matches.append({
                    "league": comp_name,
                    "home": home,
                    "away": away,
                    "time_to_match": time_to_match,
                    "time_until_window": time_until_window,
                    "window_start": datetime.fromtimestamp(time_window_start),
                    "window_end": datetime.fromtimestamp(time_window_end),
                    "kickoff": event_dt,
                    "ahc_odds": ahc_odds,
                })

        if not matches:
            print("\n❌ 未找到有-1盘口的比赛")
            return

        # 按距离投注窗口的时间排序
        matches.sort(key=lambda x: abs(x['time_until_window']))

        print(f"\n找到 {len(matches)} 场有-1盘口的比赛:\n")

        now_dt = datetime.now()

        for i, match in enumerate(matches[:20], 1):  # 只显示前20场
            time_to_match = match['time_to_match']
            time_until_window = match['time_until_window']

            # 判断状态
            if abs(time_until_window) < 0.5:
                status = "🎯 投注窗口中"
                status_color = "\033[92m"  # 绿色
            elif time_until_window < 0:
                status = "❌ 已错过"
                status_color = "\033[91m"  # 红色
            elif time_until_window < 5:
                status = f"⏰ {time_until_window:.1f}分钟后进入"
                status_color = "\033[93m"  # 黄色
            else:
                status = f"⏳ {time_until_window:.1f}分钟后进入"
                status_color = "\033[0m"   # 默认

            print(f"{i:2}. {status_color}{status}\033[0m")
            print(f"    {match['home']} vs {match['away']}")
            print(f"    联赛: {match['league']}")
            print(f"    让球盘: {match['ahc_odds']}")
            print(f"    开赛时间: {match['kickoff'].strftime('%H:%M:%S')}")
            print(f"    距开赛: {time_to_match:.1f}分钟")
            print(f"    投注窗口: {match['window_start'].strftime('%H:%M:%S')} - {match['window_end'].strftime('%H:%M:%S')}")
            print()

        # 统计
        in_window = sum(1 for m in matches if abs(m['time_until_window']) < 0.5)
        within_5min = sum(1 for m in matches if 0 <= m['time_until_window'] < 5)
        within_15min = sum(1 for m in matches if 0 <= m['time_until_window'] < 15)

        print("="*80)
        print("📊 统计")
        print("="*80)
        print(f"总比赛数: {len(matches)}")
        print(f"  🎯 在投注窗口内: {in_window}")
        print(f"  ⏰ 5分钟内进入: {within_5min}")
        print(f"  ⏳ 15分钟内进入: {within_15min}")
        print("="*80)

        # 建议
        if in_window == 0:
            if within_5min > 0:
                print("\n💡 有比赛即将进入投注窗口，请稍候...")
            elif within_15min > 0:
                next_match = min([m for m in matches if m['time_until_window'] > 0],
                               key=lambda x: x['time_until_window'])
                wait_time = max(0, next_match['time_until_window'])
                print(f"\n💡 下一场比赛将在 {wait_time:.1f} 分钟后进入投注窗口")
                print(f"   {next_match['home']} vs {next_match['away']}")
            else:
                print("\n💡 当前时段比赛较少，建议:")
                print("   - 在比赛高峰时段运行（欧洲时间晚上）")
                print("   - 或扩大联赛范围: LEAGUE_MODE = 'EXPANDED'")

    except Exception as e:
        print(f"\n❌ 异常: {e}")


if __name__ == '__main__':
    while True:
        monitor_upcoming_matches()
        print("\n⏰ 30秒后刷新...\n")
        time.sleep(30)
