"""
比赛筛选模块 - 精简可执行版
包含赔率走势监控和多维度筛选
"""

import requests
import time
import logging
from datetime import datetime
from collections import defaultdict
from config import (
    API_KEY, BASE_URL, TARGET_HANDICAP, TARGET_ODDS_LOW, TARGET_ODDS_HIGH,
    SAFE_ODDS_LOW, SAFE_ODDS_HIGH, MAX_HOME_WIN_ODDS, MATCH_TIME_BEFORE,
    ENABLE_ODDS_TRACKING, TRACKING_START_MINUTES, TRACKING_INTERVAL,
    ODDS_DROP_THRESHOLD, MAX_ODDS_VOLATILITY, MAX_BETS_PER_30MIN, MIN_MATCH_SCORE,
    LEAGUE_MODE
)
from core_leagues import (
    TIER_1_LEAGUES, EXPANDED_LEAGUES, ALL_LEAGUES,
    get_league_score
)

# 根据配置选择联赛集合
if LEAGUE_MODE == 'TIER_1':
    ACTIVE_LEAGUES = TIER_1_LEAGUES
elif LEAGUE_MODE == 'EXPANDED':
    ACTIVE_LEAGUES = EXPANDED_LEAGUES
elif LEAGUE_MODE == 'ALL':
    ACTIVE_LEAGUES = ALL_LEAGUES
else:
    ACTIVE_LEAGUES = TIER_1_LEAGUES  # 默认


# 全局：存储赔率历史
odds_history = defaultdict(list)  # {event_id: [(timestamp, odds), ...]}

# 全局：存储最近30分钟的投注
recent_bets = []  # [(timestamp, event_id), ...]


def fetch_events(minutes_ahead=15):
    """
    从 Cloudbet API 获取比赛数据

    参数:
        minutes_ahead: 查询未来多少分钟的比赛

    返回:
        events: 原始比赛数据列表
    """
    now = int(time.time())
    future = now + minutes_ahead * 60

    headers = {"X-API-Key": API_KEY}
    params = {
        "sport": "soccer",
        "from": now,
        "to": future,
        "markets": "soccer.asian_handicap,soccer.match_winner",  # 同时获取让球盘和独赢盘
        "limit": 1000
    }

    try:
        response = requests.get(BASE_URL, headers=headers, params=params, timeout=15)
        if response.status_code != 200:
            logging.error(f"API请求失败: {response.status_code}")
            return []

        data = response.json()
        return data.get("competitions", [])

    except Exception as e:
        logging.error(f"API请求异常: {e}")
        return []


def extract_match_info(event, competition):
    """
    从事件中提取比赛信息

    返回:
        match_info 字典或 None
    """
    event_id = event.get("id")
    home_name = event.get("home", {}).get("name", "N/A")
    away_name = event.get("away", {}).get("name", "N/A")
    cutoff_time = event.get("cutoffTime")

    if not cutoff_time:
        return None

    try:
        event_dt = datetime.fromisoformat(cutoff_time.replace("Z", "+00:00"))
        event_timestamp = int(event_dt.timestamp())
    except:
        return None

    match_info = {
        "event_id": event_id,
        "home": home_name,
        "away": away_name,
        "league": competition.get("name", "Unknown"),
        "league_key": competition.get("key", ""),
        "start_time": cutoff_time,
        "start_timestamp": event_timestamp,
        "markets": event.get("markets", {}),
    }

    return match_info


def get_asian_handicap_odds(markets):
    """
    从市场数据中提取亚洲让球盘 -1 主队赔率

    返回:
        odds: 赔率（float）或 None
    """
    for market_key, market in markets.items():
        if "asian_handicap" not in market_key.lower():
            continue

        for submarket in market.get("submarkets", {}).values():
            for selection in submarket.get("selections", []):
                if (selection.get("outcome") == "home" and
                    selection.get("params") == f"handicap={TARGET_HANDICAP}"):
                    return selection.get("price")

    return None


def get_home_win_odds(markets):
    """
    从市场数据中提取主队独赢（1X2）赔率

    返回:
        odds: 赔率（float）或 None
    """
    for market_key, market in markets.items():
        if "match_winner" not in market_key.lower():
            continue

        for submarket in market.get("submarkets", {}).values():
            for selection in submarket.get("selections", []):
                if selection.get("outcome") == "home":
                    return selection.get("price")

    return None


def track_odds(event_id, odds):
    """
    记录赔率历史（用于走势分析）

    参数:
        event_id: 比赛ID
        odds: 当前赔率
    """
    if not ENABLE_ODDS_TRACKING:
        return

    now = time.time()
    odds_history[event_id].append((now, odds))

    # 只保留最近15分钟的数据
    cutoff = now - 15 * 60
    odds_history[event_id] = [(t, o) for t, o in odds_history[event_id] if t >= cutoff]


def analyze_odds_trend(event_id, current_odds):
    """
    分析赔率走势

    检查:
        1. 尾盘是否下降（走热）
        2. 波动是否过大

    返回:
        (is_good, reason)
    """
    if not ENABLE_ODDS_TRACKING:
        return True, "未启用赔率监控"

    history = odds_history.get(event_id, [])
    if len(history) < 3:
        return True, "数据不足"  # 数据不足时默认通过

    # 计算历史平均值
    historical_odds = [o for _, o in history[:-1]]  # 排除最新的
    avg_odds = sum(historical_odds) / len(historical_odds)

    # 检查1: 尾盘下降（市场看好主队）
    odds_change = current_odds - avg_odds
    if odds_change > 0:
        return False, f"赔率上升 {odds_change:+.3f}，市场不看好"

    if odds_change > ODDS_DROP_THRESHOLD:
        return False, f"下降不足 {odds_change:+.3f}（需<{ODDS_DROP_THRESHOLD}）"

    # 检查2: 波动不能太大
    max_odds = max(historical_odds)
    min_odds = min(historical_odds)
    volatility = max_odds - min_odds

    if volatility > MAX_ODDS_VOLATILITY:
        return False, f"波动过大 {volatility:.3f}（最大{MAX_ODDS_VOLATILITY}）"

    return True, f"走势良好，下降 {abs(odds_change):.3f}"


def calculate_match_score(match_info, ahc_odds, home_win_odds):
    """
    计算比赛评分（用于多场候选时排序）

    评分因素:
        1. 联赛等级分（0-10）
        2. 让球盘赔率（越接近1.7越好，权重3）
        3. 独赢赔率（越低越好，权重2）
        4. 赔率下降幅度（权重1）

    返回:
        score: 综合评分
    """
    score = 0.0

    # 1. 联赛评分
    league_score = get_league_score(match_info["league_key"])
    score += league_score

    # 2. 让球盘赔率评分（最优点1.7，偏离扣分）
    optimal_ahc = 1.70
    ahc_diff = abs(ahc_odds - optimal_ahc)
    ahc_score = max(0, 3 - ahc_diff * 10)
    score += ahc_score

    # 3. 独赢赔率评分（越低越好）
    if home_win_odds:
        if home_win_odds < 1.30:
            home_score = 2.0
        elif home_win_odds < 1.50:
            home_score = 1.5
        elif home_win_odds < 1.60:
            home_score = 1.0
        else:
            home_score = 0.0
        score += home_score

    # 4. 赔率走势评分
    history = odds_history.get(match_info["event_id"], [])
    if len(history) >= 3:
        historical_odds = [o for _, o in history[:-1]]
        avg_odds = sum(historical_odds) / len(historical_odds)
        odds_change = ahc_odds - avg_odds
        if odds_change < 0:
            trend_score = min(abs(odds_change) * 10, 2.0)
            score += trend_score

    return round(score, 2)


def filter_matches():
    """
    筛选符合条件的比赛（主流程）

    流程:
        1. 获取API数据
        2. 基础筛选（时间、联赛、盘口、赔率）
        3. 高级筛选（独赢赔率、走势分析）
        4. 计算评分并排序
        5. 30分钟控制（只选最好的1场）

    返回:
        candidates: 候选比赛列表
    """
    now = time.time()
    candidates = []

    # 1. 获取比赛数据
    competitions = fetch_events(minutes_ahead=15)

    for comp in competitions:
        comp_name = comp.get("name", "")
        comp_key = comp.get("key", "")

        # 过滤虚拟联赛
        if "virtual" in comp_name.lower():
            continue

        # 过滤非激活联赛
        if comp_key not in ACTIVE_LEAGUES:
            continue

        for event in comp.get("events", []):
            match_info = extract_match_info(event, comp)
            if not match_info:
                continue

            event_id = match_info["event_id"]
            start_timestamp = match_info["start_timestamp"]

            # 时间筛选：开赛前 N 分钟 ± 30秒
            time_to_match = (start_timestamp - now) / 60
            time_min = MATCH_TIME_BEFORE - 0.5
            time_max = MATCH_TIME_BEFORE + 0.5

            if not (time_min <= time_to_match <= time_max):
                # 如果开赛前10分钟，记录赔率用于走势分析
                if ENABLE_ODDS_TRACKING and time_to_match <= TRACKING_START_MINUTES:
                    ahc_odds = get_asian_handicap_odds(match_info["markets"])
                    if ahc_odds:
                        track_odds(event_id, ahc_odds)
                continue

            # 提取让球盘赔率
            ahc_odds = get_asian_handicap_odds(match_info["markets"])
            if not ahc_odds:
                continue

            # 赔率范围筛选
            if not (TARGET_ODDS_LOW <= ahc_odds <= TARGET_ODDS_HIGH):
                continue

            # 更严格的赔率安全区（可选）
            if not (SAFE_ODDS_LOW <= ahc_odds <= SAFE_ODDS_HIGH):
                logging.debug(f"赔率超出安全区: {match_info['home']} vs {match_info['away']}, {ahc_odds}")
                continue

            # 提取独赢赔率
            home_win_odds = get_home_win_odds(match_info["markets"])
            if not home_win_odds:
                logging.debug(f"无独赢赔率: {match_info['home']} vs {match_info['away']}")
                continue

            # 独赢赔率筛选（确保是真强队）
            if home_win_odds > MAX_HOME_WIN_ODDS:
                logging.debug(f"独赢赔率过高: {match_info['home']} vs {match_info['away']}, {home_win_odds}")
                continue

            # 记录当前赔率
            track_odds(event_id, ahc_odds)

            # 赔率走势分析
            trend_ok, trend_reason = analyze_odds_trend(event_id, ahc_odds)
            if not trend_ok:
                logging.debug(f"走势不符: {match_info['home']} vs {match_info['away']}, {trend_reason}")
                continue

            # 计算评分
            score = calculate_match_score(match_info, ahc_odds, home_win_odds)

            if score < MIN_MATCH_SCORE:
                logging.debug(f"评分过低: {match_info['home']} vs {match_info['away']}, {score:.2f}")
                continue

            # 组装候选
            candidate = {
                **match_info,
                "ahc_odds": ahc_odds,
                "home_win_odds": home_win_odds,
                "time_to_match": round(time_to_match, 2),
                "trend_reason": trend_reason,
                "score": score,
            }

            candidates.append(candidate)

    # 按评分排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # 30分钟控制：只保留最高分的1场
    if len(candidates) > 0:
        # 清理30分钟前的投注记录
        cutoff_time = now - 30 * 60
        global recent_bets
        recent_bets = [(t, eid) for t, eid in recent_bets if t >= cutoff_time]

        # 检查30分钟内投注数量
        if len(recent_bets) >= MAX_BETS_PER_30MIN:
            logging.info(f"30分钟内已投注 {len(recent_bets)} 场，跳过")
            return []

        # 只返回评分最高的1场
        best_match = candidates[0]
        logging.info(f"最佳候选: {best_match['home']} vs {best_match['away']}, 评分: {best_match['score']}")
        return [best_match]

    return []


def mark_bet_placed(event_id):
    """
    标记已下注（用于30分钟控制）

    参数:
        event_id: 比赛ID
    """
    now = time.time()
    recent_bets.append((now, event_id))


def build_market_url(ahc_odds):
    """
    构建市场URL（用于下注）

    返回:
        market_url: 如 "soccer.asian_handicap/home?handicap=-1"
    """
    return f"soccer.asian_handicap/home?handicap={TARGET_HANDICAP}"


# ========================================
# 测试代码
# ========================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    print("="*60)
    print("比赛筛选模块测试")
    print("="*60)

    print(f"\n配置:")
    print(f"  让球盘: {TARGET_HANDICAP}")
    print(f"  赔率范围: {TARGET_ODDS_LOW} - {TARGET_ODDS_HIGH}")
    print(f"  安全区: {SAFE_ODDS_LOW} - {SAFE_ODDS_HIGH}")
    print(f"  独赢上限: {MAX_HOME_WIN_ODDS}")
    print(f"  开赛时间: {MATCH_TIME_BEFORE}分钟")
    print(f"  核心联赛数: {len(CORE_LEAGUES)}")

    print(f"\n开始筛选...")
    matches = filter_matches()

    if not matches:
        print("未找到符合条件的比赛")
    else:
        print(f"\n找到 {len(matches)} 场候选比赛:")
        for match in matches:
            print(f"\n{match['home']} vs {match['away']}")
            print(f"  联赛: {match['league']}")
            print(f"  让球盘: {match['ahc_odds']}")
            print(f"  独赢: {match['home_win_odds']}")
            print(f"  开赛: {match['time_to_match']:.1f}分钟后")
            print(f"  评分: {match['score']}")
            print(f"  走势: {match['trend_reason']}")
