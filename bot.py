"""
Cloudbet 投注机器人 - 主程序
精简可执行版 - 2.5倍马丁格尔 + 智能筛选
"""

import csv
import os
import sys
import time
import uuid
import requests
import logging
from datetime import datetime

from config import (
    API_KEY, BET_URL, ACCOUNT_URL, CURRENCY, LOG_FILE,
    SLEEP_INTERVAL, INITIAL_BALANCE, DRY_RUN, VERBOSE, LEAGUE_MODE
)
from bankroll import next_bet_amount, format_bet_stats, calc_today_pnl
from matcher import filter_matches, mark_bet_placed, build_market_url, ACTIVE_LEAGUES

# ========================================
# 日志配置
# ========================================
log_level = logging.DEBUG if VERBOSE else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# ========================================
# 日志文件管理
# ========================================

def init_log_file():
    """初始化CSV日志文件"""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp", "EventID", "Match", "League",
                "AHC_Odds", "HomeWin_Odds", "Stake", "LossStreak",
                "Result", "PnL", "Balance", "Score", "Notes"
            ])
        logging.info(f"创建日志文件: {LOG_FILE}")


def load_logs():
    """加载投注日志"""
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                logs.append(row)
    return logs


def save_log(log_entry):
    """保存一条投注记录"""
    with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            log_entry['Timestamp'],
            log_entry['EventID'],
            log_entry['Match'],
            log_entry['League'],
            log_entry['AHC_Odds'],
            log_entry['HomeWin_Odds'],
            log_entry['Stake'],
            log_entry['LossStreak'],
            log_entry['Result'],
            log_entry.get('PnL', ''),
            log_entry['Balance'],
            log_entry.get('Score', ''),
            log_entry['Notes']
        ])


def has_bet_on_event(event_id, logs):
    """检查是否已对该比赛下注"""
    for log in logs:
        if str(log.get('EventID', '')) == str(event_id):
            return True
    return False


# ========================================
# 账户和下注
# ========================================

def get_balance():
    """获取账户余额"""
    url = f"{ACCOUNT_URL}/{CURRENCY}/balance"
    headers = {"X-API-Key": API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data.get("amount", INITIAL_BALANCE))
        else:
            logging.error(f"获取余额失败: {response.status_code}")
            return INITIAL_BALANCE
    except Exception as e:
        logging.error(f"获取余额异常: {e}")
        return INITIAL_BALANCE


def place_bet(event_id, market_url, price, stake):
    """
    执行投注

    返回:
        (success, response_data)
    """
    if DRY_RUN:
        logging.info(f"[模拟模式] 投注: {stake} USDT @ {price}")
        return True, {"status": "ACCEPTED", "referenceId": "DRY_RUN"}

    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }

    ref_id = str(uuid.uuid4())
    payload = {
        "referenceId": ref_id,
        "customerReference": ref_id,
        "stake": str(stake),
        "price": str(price),
        "eventId": str(event_id),
        "marketUrl": market_url,
        "currency": CURRENCY,
        "side": "BACK",
        "acceptPriceChange": "BETTER"
    }

    try:
        response = requests.post(BET_URL, headers=headers, json=payload, timeout=15)
        result = response.json()

        if response.status_code == 200 and result.get("status") == "ACCEPTED":
            return True, result
        else:
            logging.error(f"投注失败: {response.status_code} - {result}")
            return False, result

    except Exception as e:
        logging.error(f"投注异常: {e}")
        return False, {"error": str(e)}


# ========================================
# 主循环
# ========================================

def print_banner():
    """打印启动横幅"""
    print("\n" + "="*70)
    print("  Cloudbet 投注机器人 - 精简可执行版")
    print("  策略: 2.5倍马丁格尔 + 智能筛选")
    print("="*70)
    print(f"  联赛模式: {LEAGUE_MODE}")
    print(f"  激活联赛: {len(ACTIVE_LEAGUES)} 个")
    print(f"  轮询间隔: {SLEEP_INTERVAL}秒")
    print(f"  模拟模式: {'开启' if DRY_RUN else '关闭'}")
    print(f"  日志文件: {LOG_FILE}")
    print("="*70)
    print()


def main():
    """主程序"""
    print_banner()

    # 初始化
    init_log_file()
    round_count = 0

    try:
        while True:
            round_count += 1
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            logging.info(f"\n{'='*70}")
            logging.info(f"第 {round_count} 轮扫描 - {now_str}")
            logging.info(f"{'='*70}")

            # 1. 获取账户信息
            balance = get_balance()
            logs = load_logs()

            # 2. 计算投注金额
            stake, loss_streak, stop_reason = next_bet_amount(logs, balance)

            if stop_reason:
                logging.warning(f"⚠️  停止下注: {stop_reason}")
                logging.info(f"当前余额: {balance:.2f} {CURRENCY}")

                # 如果是达到盈利目标，可以选择退出
                if "盈利目标" in stop_reason:
                    logging.info("🎉 达到今日盈利目标，建议收工！")
                    # 可以选择 break 退出，或继续监控
                    # break

                # 等待一段时间再检查
                time.sleep(SLEEP_INTERVAL * 2)
                continue

            # 3. 计算今日盈亏
            day_pnl, day_start_balance = calc_today_pnl(logs, balance)

            # 4. 显示统计
            stats = format_bet_stats(balance, stake, loss_streak, day_pnl)
            logging.info(f"📊 {stats}")

            # 5. 筛选比赛
            matches = filter_matches()

            if not matches:
                logging.info("暂无符合条件的比赛")
                time.sleep(SLEEP_INTERVAL)
                continue

            # 6. 处理候选比赛
            for match in matches:
                event_id = match['event_id']

                # 检查是否已投注
                if has_bet_on_event(event_id, logs):
                    logging.info(f"已投注过: {match['home']} vs {match['away']}")
                    continue

                # 显示比赛信息
                logging.info(f"\n🎯 发现目标比赛:")
                logging.info(f"  {match['home']} vs {match['away']}")
                logging.info(f"  联赛: {match['league']}")
                logging.info(f"  让球盘: {match['ahc_odds']}")
                logging.info(f"  独赢: {match['home_win_odds']}")
                logging.info(f"  评分: {match['score']}")
                logging.info(f"  走势: {match['trend_reason']}")
                logging.info(f"  开赛: {match['time_to_match']:.1f}分钟后")

                # 构建市场URL
                market_url = build_market_url(match['ahc_odds'])

                # 执行投注
                logging.info(f"\n💰 投注: {stake} {CURRENCY} @ {match['ahc_odds']}")

                success, response = place_bet(
                    event_id,
                    market_url,
                    match['ahc_odds'],
                    stake
                )

                # 记录日志
                result = "ACCEPTED" if success else "FAILED"
                notes = f"Score:{match['score']} | {match['trend_reason']} | {response.get('referenceId', 'N/A')}"

                log_entry = {
                    'Timestamp': datetime.utcnow().isoformat() + 'Z',
                    'EventID': event_id,
                    'Match': f"{match['home']} vs {match['away']}",
                    'League': match['league'],
                    'AHC_Odds': match['ahc_odds'],
                    'HomeWin_Odds': match['home_win_odds'],
                    'Stake': stake,
                    'LossStreak': loss_streak,
                    'Result': result,
                    'Balance': balance,
                    'Score': match['score'],
                    'Notes': notes
                }

                save_log(log_entry)

                if success:
                    logging.info(f"✅ 投注成功！")
                    mark_bet_placed(event_id)
                else:
                    error_msg = response.get('error', response.get('message', '未知错误'))
                    logging.error(f"❌ 投注失败: {error_msg}")

                # 重新加载日志
                logs = load_logs()

            # 7. 等待下一轮
            logging.info(f"\n等待 {SLEEP_INTERVAL} 秒...\n")
            time.sleep(SLEEP_INTERVAL)

    except KeyboardInterrupt:
        logging.info("\n\n用户中断，程序退出")
        sys.exit(0)

    except Exception as e:
        logging.error(f"\n\n系统异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
