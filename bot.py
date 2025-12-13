"""
Cloudbet 投注机器人 - 主程序
精简可执行版 - 2.5倍马丁格尔 + 智能筛选
"""

import sys
import time
import uuid
from typing import Dict, Iterable, List

import requests
import logging
from datetime import datetime

from config import (
    API_KEY, BET_URL, ACCOUNT_URL, CURRENCY, LOG_FILE,
    SLEEP_INTERVAL, INITIAL_BALANCE, DRY_RUN, VERBOSE, LEAGUE_MODE
)
from bankroll import next_bet_amount, format_bet_stats, calc_today_pnl
from matcher import filter_matches, mark_bet_placed, build_market_url, ACTIVE_LEAGUES
from log_utils import BetLogEntry, BetLogManager

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

def print_banner(log_manager: BetLogManager) -> None:
    """打印启动横幅"""
    print("\n" + "="*70)
    print("  Cloudbet 投注机器人 - 精简可执行版")
    print("  策略: 2.5倍马丁格尔 + 智能筛选")
    print("="*70)
    print(f"  联赛模式: {LEAGUE_MODE}")
    print(f"  激活联赛: {len(ACTIVE_LEAGUES)} 个")
    print(f"  轮询间隔: {SLEEP_INTERVAL}秒")
    print(f"  模拟模式: {'开启' if DRY_RUN else '关闭'}")
    print(f"  日志文件: {log_manager.log_file}")
    print("="*70)
    print()


def log_match_summary(match: Dict[str, str]) -> None:
    logging.info("\n🎯 发现目标比赛:")
    logging.info("  %s vs %s", match['home'], match['away'])
    logging.info("  联赛: %s", match['league'])
    logging.info("  让球盘: %s", match['ahc_odds'])
    logging.info("  独赢: %s", match['home_win_odds'])
    logging.info("  评分: %s", match['score'])
    logging.info("  走势: %s", match['trend_reason'])
    logging.info("  开赛: %.1f分钟后", match['time_to_match'])


def record_bet(
    match: Dict[str, str],
    stake: float,
    loss_streak: int,
    balance: float,
    success: bool,
    response: Dict[str, str],
    log_manager: BetLogManager,
) -> None:
    result = "ACCEPTED" if success else "FAILED"
    notes = (
        f"Score:{match['score']} | {match['trend_reason']} | "
        f"{response.get('referenceId', 'N/A')}"
    )

    entry = BetLogEntry(
        timestamp=datetime.utcnow().isoformat() + 'Z',
        event_id=str(match['event_id']),
        match=f"{match['home']} vs {match['away']}",
        league=match['league'],
        ahc_odds=float(match['ahc_odds']),
        home_win_odds=float(match['home_win_odds']),
        stake=stake,
        loss_streak=loss_streak,
        result=result,
        balance=balance,
        score=float(match['score']),
        notes=notes,
    )

    log_manager.append(entry)

    if success:
        logging.info("✅ 投注成功！")
        mark_bet_placed(match['event_id'])
    else:
        error_msg = response.get('error', response.get('message', '未知错误'))
        logging.error("❌ 投注失败: %s", error_msg)


def process_matches(
    matches: Iterable[Dict[str, str]],
    stake: float,
    loss_streak: int,
    balance: float,
    log_manager: BetLogManager,
) -> List[Dict[str, str]]:
    """Iterate over matches and place bets when eligible."""

    logs = log_manager.load()
    for match in matches:
        event_id = match['event_id']

        if BetLogManager.has_bet_on_event(event_id, logs):
            logging.info("已投注过: %s vs %s", match['home'], match['away'])
            continue

        log_match_summary(match)
        market_url = build_market_url(match['ahc_odds'])
        logging.info("\n💰 投注: %s %s @ %s", stake, CURRENCY, match['ahc_odds'])

        success, response = place_bet(
            event_id,
            market_url,
            match['ahc_odds'],
            stake
        )

        record_bet(
            match,
            stake,
            loss_streak,
            balance,
            success,
            response,
            log_manager,
        )

        # refresh logs to prevent duplicate bets in same round
        logs = log_manager.load()

    return logs


def run_loop(log_manager: BetLogManager) -> None:
    round_count = 0
    while True:
        round_count += 1
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        logging.info(f"\n{'='*70}")
        logging.info(f"第 {round_count} 轮扫描 - {now_str}")
        logging.info(f"{'='*70}")

        balance = get_balance()
        logs = log_manager.load()
        stake, loss_streak, stop_reason = next_bet_amount(logs, balance)

        if stop_reason:
            logging.warning(f"⚠️  停止下注: {stop_reason}")
            logging.info(f"当前余额: {balance:.2f} {CURRENCY}")

            if "盈利目标" in stop_reason:
                logging.info("🎉 达到今日盈利目标，建议收工！")

            time.sleep(SLEEP_INTERVAL * 2)
            continue

        day_pnl, _ = calc_today_pnl(logs, balance)
        stats = format_bet_stats(balance, stake, loss_streak, day_pnl)
        logging.info(f"📊 {stats}")

        matches = filter_matches()
        if not matches:
            logging.info("暂无符合条件的比赛")
            time.sleep(SLEEP_INTERVAL)
            continue

        process_matches(matches, stake, loss_streak, balance, log_manager)
        logging.info(f"\n等待 {SLEEP_INTERVAL} 秒...\n")
        time.sleep(SLEEP_INTERVAL)


def main() -> None:
    log_manager = BetLogManager(LOG_FILE)
    print_banner(log_manager)
    log_manager.init_file()

    try:
        run_loop(log_manager)
    except KeyboardInterrupt:
        logging.info("\n\n用户中断，程序退出")
        sys.exit(0)
    except Exception as e:
        logging.error(f"\n\n系统异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
