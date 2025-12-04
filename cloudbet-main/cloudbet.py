import csv
import os
import requests
import time
import uuid
import logging
from datetime import datetime

# ---------------------------
# 配置参数
# ---------------------------
INITIAL_BALANCE = 100
LOG_FILE = "bet_log.csv"

API_KEY = "eyJhbGciOiJSUzI1NiIsImtpZCI6IkhKcDkyNnF3ZXBjNnF3LU9rMk4zV05pXzBrRFd6cEdwTzAxNlRJUjdRWDAiLCJ0eXAiOiJKV1QifQ.eyJhY2Nlc3NfdGllciI6InRyYWRpbmciLCJleHAiOjE5OTYyMzk5ODIsImlhdCI6MTY4MDg3OTk4MiwianRpIjoiNDM2Yzc1NjgtMTM0Ny00MDJhLTg4ZDMtZDlhZmU3OGQ1MDdiIiwic3ViIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIiwidGVuYW50IjoiY2xvdWRiZXQiLCJ1dWlkIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIn0.4eI0AK7z17EyutBgx_0FLUc9r5nWR_oUuiurGPyNlcGSz3853wkipm1ul_-oIlijPbaIha1UoD_2v3u-X48cJsmQglLNyst-2UPie9qQ3t8bzQUlhnHjcye7Kc-msGHNi-ML5twdRI-42sESiAECTccsB6NVebHgCqZfAh9-PVT-Hmao4c9AJiyJ2NA5QOTcBz7BJR06MTC0ZMW5Yklm001eEaDYxpBAorDmvRg5GDldlCBuQfVcvip8Zkp0uPHuAu2TJTJrw7tMYXSn7CUWWlQ_oQ7Alb-AchSOLkk7y-eUfUtu7plYJnj50wBLs-NLBzjnV3ifUhDk0etB9HNebA"
BASE_URL = "https://sports-api.cloudbet.com/pub/v2/odds/events"
BET_URL = "https://sports-api.cloudbet.com/pub/v3/bets/place"
ACCOUNT_URL = "https://sports-api.cloudbet.com/pub/v1/account/currencies"

TARGET_HANDICAP = "-1"
TARGET_ODDS_LOW = 1.5
TARGET_ODDS_HIGH = 2.0

FIB_SEQUENCE = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55]  # 支持10连挂，可自己加长
MIN_BET = 1
CURRENCY = "USDT"
SLEEP_SECONDS = 60

# 配置日志
logging.basicConfig(level=logging.INFO)

# ---------------------------
# 辅助函数
# ---------------------------
def get_current_balance(currency=CURRENCY):
    url = f"{ACCOUNT_URL}/{currency}/balance"
    headers = {"X-API-Key": API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data.get("amount", INITIAL_BALANCE))
        else:
            logging.error(f"获取余额失败，状态码: {response.status_code}")
            return INITIAL_BALANCE
    except Exception as e:
        logging.error(f"获取余额异常: {e}")
        return INITIAL_BALANCE

if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Round", "Match", "Bet Direction", "Bet Amount", "Fibonacci Index", "Result", "Balance", "Notes"])

def load_logs():
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            fieldnames = [f.strip() for f in reader.fieldnames]
            for row in reader:
                cleaned_row = {fieldnames[i]: value.strip() for i, value in enumerate(row.values())}
                logs.append(cleaned_row)
    return logs

def calc_fib_index(logs):
    """
    通过日志自动推算当前应该用哪个斐波那契投注序号。
    """
    index = 0
    # 从最后一笔开始往前找，遇到赢退两格，遇到输进一格
    for log in reversed(logs):
        if log.get("Result", "") == "Accepted":
            break  # 尚未出结果
        if log.get("Result", "") == "Lose":
            index += 1
        elif log.get("Result", "") == "Win":
            index = max(index - 2, 0)
        # 其它情况（Pending/空/手动补录等）无视
    return min(index, len(FIB_SEQUENCE) - 1)

def next_bet_amount_fib(logs):
    index = calc_fib_index(logs)
    amount = max(FIB_SEQUENCE[index], MIN_BET)
    return amount, index

def has_been_bet(event_id, logs):
    for log in logs:
        if log.get("Notes", "").find(f"event_id {event_id}") != -1:
            return True
    return False

def fetch_target_matches():
    now = int(time.time())
    future = now + 11 * 60
    headers = {"X-API-Key": API_KEY}
    params = {
        "sport": "soccer",
        "from": now,
        "to": future,
        "markets": "soccer.asian_handicap",
        "limit": 1000
    }
    try:
        response = requests.get(BASE_URL, headers=headers, params=params, timeout=10)
    except Exception as e:
        logging.error(f"API 请求失败: {e}")
        return []

    if response.status_code != 200:
        logging.error(f"API 返回错误状态码: {response.status_code}")
        return []

    matches = []
    data = response.json()
    for comp in data.get("competitions", []):
        comp_name = comp["name"]
        if "virtual" in comp_name.lower():
            continue

        for event in comp.get("events", []):
            event_start_str = event.get("cutoffTime")
            if event_start_str:
                try:
                    event_start_dt = datetime.fromisoformat(event_start_str.replace("Z", "+00:00"))
                    event_timestamp = int(event_start_dt.timestamp())
                except Exception as e:
                    logging.error(f"时间解析错误: {e}")
                    continue
            else:
                continue

            if event_timestamp < now or event_timestamp > future:
                continue

            for market_key, market in event.get("markets", {}).items():
                if "asian_handicap" not in market_key.lower():
                    continue
                for sub in market.get("submarkets", {}).values():
                    for sel in sub.get("selections", []):
                        if (sel.get("outcome") == "home" and
                                sel.get("params") == f"handicap={TARGET_HANDICAP}" and
                                TARGET_ODDS_LOW <= sel.get("price") <= TARGET_ODDS_HIGH):
                            matches.append({
                                "event_id": event.get("id"),
                                "match": f"{event.get('home', {}).get('name', 'N/A')} vs {event.get('away', {}).get('name', 'N/A')}",
                                "start": event.get("cutoffTime"),
                                "price": sel.get("price"),
                                "market_url": f"{market_key}/home?handicap={TARGET_HANDICAP}",
                                "bet_placed": False
                            })
    return matches

def place_bet(event_id, market_url, price, stake):
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
        response = requests.post(BET_URL, headers=headers, json=payload, timeout=10)
        result_json = response.json()
    except Exception as e:
        logging.error(f"下注请求失败: {e}")
        return {"error": str(e)}, 500
    return result_json, response.status_code

def print_bet_logs(logs, show_last_n=20):
    print("\n========= 最近投注记录（最多显示{}条）=========".format(show_last_n))
    print("{:>5} {:<25} {:<13} {:<8} {:<5} {:<8} {:<10} {}".format(
        "轮次", "比赛", "方向", "金额", "斐波", "结果", "余额", "备注"))
    for log in logs[-show_last_n:]:
        print("{:>5} {:<25} {:<13} {:<8} {:<5} {:<8} {:<10} {}".format(
            log.get("Round", ""),
            log.get("Match", ""),
            log.get("Bet Direction", ""),
            log.get("Bet Amount", ""),
            log.get("Fibonacci Index", ""),
            log.get("Result", ""),
            log.get("Balance", ""),
            log.get("Notes", "")
        ))
    print("=" * 70)

# ---------------------------
# 自动循环下注主流程
# ---------------------------
if __name__ == '__main__':
    print("斐波那契自动投注机器人启动，每60秒自动轮询。按Ctrl+C可中止。")
    while True:
        logs = load_logs()
        current_balance = get_current_balance(CURRENCY)
        bet_amount, fib_index = next_bet_amount_fib(logs)
        matches = fetch_target_matches()
        now = int(time.time())
        n_bet = 0

        for match in matches:
            event_start_ts = int(datetime.fromisoformat(match["start"].replace("Z", "+00:00")).timestamp())
            if not has_been_bet(match['event_id'], logs) and event_start_ts > now + 60:
                current_balance = get_current_balance(CURRENCY)
                bet_amount, fib_index = next_bet_amount_fib(logs)
                bet_response, status = place_bet(match['event_id'], match['market_url'], match['price'], bet_amount)
                result = "Pending"
                if status == 200 and bet_response.get("status") == "ACCEPTED":
                    note = f"Auto bet placed: event_id {match['event_id']}"
                    result = "Accepted"
                else:
                    note = f"Auto bet failed: event_id {match['event_id']}, error: {bet_response.get('error', 'Unknown error')}"
                round_no = len(logs) + 1
                with open(LOG_FILE, mode='a', newline='') as file:
                    writer = csv.writer(file)
                    writer.writerow([round_no, match['match'], f"BACK @ {match['price']}", bet_amount, fib_index, result, round(current_balance, 2), note])
                print(f"\n[下注] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {match['match']} | 金额 {bet_amount} | 赔率 {match['price']} | 斐波序号 {fib_index} | 状态: {result} | 备注: {note}")
                logs = load_logs()
                n_bet += 1

        if n_bet == 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 无新可下注比赛。")
        print_bet_logs(logs, show_last_n=20)
        print("sleep 60 seconds ...")
        time.sleep(SLEEP_SECONDS)
