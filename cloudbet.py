from flask import Flask, render_template, request, redirect
import csv
import os
import requests
import time
import uuid
import logging
from datetime import datetime  # 用于时间解析
from leagues import TOP_LEAGUES

app = Flask(__name__)

# ---------------------------
# 配置参数
# ---------------------------
INITIAL_BALANCE = 100          # 若账户 API 调用失败则使用初始余额
START_PERCENT = 0.005          # 基础下注比例：0.5%
MAX_RISK_PERCENT = 0.05        # 单次下注最高风险比例：5%
MAX_LOSS_STREAK = 5            # 最大连续亏损次数
LOG_FILE = "bet_log.csv"

API_KEY = "eyJhbGciOiJSUzI1NiIsImtpZCI6IkhKcDkyNnF3ZXBjNnF3LU9rMk4zV05pXzBrRFd6cEdwTzAxNlRJUjdRWDAiLCJ0eXAiOiJKV1QifQ.eyJhY2Nlc3NfdGllciI6InRyYWRpbmciLCJleHAiOjE5OTYyMzk5ODIsImlhdCI6MTY4MDg3OTk4MiwianRpIjoiNDM2Yzc1NjgtMTM0Ny00MDJhLTg4ZDMtZDlhZmU3OGQ1MDdiIiwic3ViIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIiwidGVuYW50IjoiY2xvdWRiZXQiLCJ1dWlkIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIn0.4eI0AK7z17EyutBgx_0FLUc9r5nWR_oUuiurGPyNlcGSz3853wkipm1ul_-oIlijPbaIha1UoD_2v3u-X48cJsmQglLNyst-2UPie9qQ3t8bzQUlhnHjcye7Kc-msGHNi-ML5twdRI-42sESiAECTccsB6NVebHgCqZfAh9-PVT-Hmao4c9AJiyJ2NA5QOTcBz7BJR06MTC0ZMW5Yklm001eEaDYxpBAorDmvRg5GDldlCBuQfVcvip8Zkp0uPHuAu2TJTJrw7tMYXSn7CUWWlQ_oQ7Alb-AchSOLkk7y-eUfUtu7plYJnj50wBLs-NLBzjnV3ifUhDk0etB9HNebA"
BASE_URL = "https://sports-api.cloudbet.com/pub/v2/odds/events"
BET_URL = "https://sports-api.cloudbet.com/pub/v3/bets/place"
ACCOUNT_URL = "https://sports-api.cloudbet.com/pub/v1/account/currencies"

# 定义目标盘口和水位范围（主队让1球，即 handicap=-1，且水位在 TARGET_ODDS_LOW ~ TARGET_ODDS_HIGH 范围内）
TARGET_HANDICAP = "-1"
TARGET_ODDS_LOW = 1.5
TARGET_ODDS_HIGH = 2.0

# 配置日志
logging.basicConfig(level=logging.INFO)

# ---------------------------
# 辅助函数：获取账户余额（实时）
# ---------------------------
def get_current_balance(currency="USDT"):
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

# ---------------------------
# 日志相关函数
# ---------------------------
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Round", "Match", "Bet Direction", "Bet Amount", "Result", "Balance", "Notes"])

def load_logs():
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            fieldnames = [f.strip() for f in reader.fieldnames]  # 清洗字段名
            for row in reader:
                cleaned_row = {fieldnames[i]: value.strip() for i, value in enumerate(row.values())}
                logs.append(cleaned_row)
    return logs

def next_bet_amount(current_balance, logs):
    base_bet = current_balance * START_PERCENT
    bet_amount = base_bet
    loss_streak = 0
    for log in reversed(logs):
        if log.get('Result', '').strip() == 'Lose':
            bet_amount *= 2
            loss_streak += 1
        else:
            break
        if loss_streak >= MAX_LOSS_STREAK:
            break
    max_bet = current_balance * MAX_RISK_PERCENT
    return round(min(bet_amount, max_bet), 2)

def has_been_bet(event_id, logs):
    """检查日志中是否已存在针对该 event_id 的投注记录"""
    for log in logs:
        if log.get("Notes", "").find(f"event_id {event_id}") != -1:
            return True
    return False

# ---------------------------
# API 请求和比赛筛选函数
# ---------------------------
def fetch_target_matches():
    """
    请求 Cloudbet API 获取比赛数据，并筛选出：
      1. 参赛联赛为 TOP_LEAGUES（排除虚拟联赛）
      2. 在 asian_handicap 市场中，主队盘口为让1球 (handicap=-1)
         且主队水位在 TARGET_ODDS_LOW ~ TARGET_ODDS_HIGH 范围内
      3. 比赛开始时间在当前时间到未来 110 分钟内
    """
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
        comp_key = comp["key"]
        if "virtual" in comp_name.lower() or comp_key not in TOP_LEAGUES:
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
    """
    下单函数，向 Cloudbet API 发送下注请求
    """
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.93 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9"
    }
    ref_id = str(uuid.uuid4())
    payload = {
        "referenceId": ref_id,   # UUID v4，每次下注必须唯一
        "stake": str(stake),
        "price": str(price),
        "eventId": str(event_id),
        "marketUrl": market_url,
        "currency": "USDT",
        "acceptPriceChange": "BETTER"  # 接受更优赔率
    }
    try:
        response = requests.post(BET_URL, headers=headers, json=payload, timeout=10)
        # 调试：打印返回的原始响应内容
        logging.info(f"Response text: {response.text}")
        result_json = response.json()
    except Exception as e:
        logging.error(f"下注请求失败: {e}")
        return {"error": str(e)}, 500
    return result_json, response.status_code

# ---------------------------
# Flask 路由
# ---------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    logs = load_logs()
    current_balance = get_current_balance("USDT")
    bet_amount = next_bet_amount(current_balance, logs)
    matches = fetch_target_matches()

    # 自动投注（GET 请求时执行）
    if request.method == 'GET':
        for match in matches:
            if not has_been_bet(match['event_id'], logs):
                # 更新余额与下注金额（避免多次投注时金额不准确）
                current_balance = get_current_balance("USDT")
                bet_amount = next_bet_amount(current_balance, logs)
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
                    writer.writerow([round_no, match['match'], f"BACK @ {match['price']}", bet_amount, result, round(current_balance, 2), note])
                # 标记该比赛已自动投注
                match['bet_placed'] = True
                logs = load_logs()  # 重新加载日志

    # 处理手动投注或日志录入（POST 请求）
    if request.method == 'POST':
        event_id = request.form.get('event_id')
        if event_id:
            # 手动点击投注按钮
            match_name = request.form.get('match')
            market_url = request.form.get('market_url')
            price = request.form.get('price')
            amount = float(request.form.get('amount'))
            bet_response, status = place_bet(event_id, market_url, price, amount)
            result = "Pending"
            if status == 200 and bet_response.get("status") == "ACCEPTED":
                note = f"Manual bet placed: event_id {event_id}"
                result = "Accepted"
            else:
                note = f"Manual bet failed: event_id {event_id}, error: {bet_response.get('error', 'Unknown error')}"
            round_no = len(logs) + 1
            with open(LOG_FILE, mode='a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow([round_no, match_name, f"BACK @ {price}", amount, result, round(current_balance, 2), note])
        else:
            # 手动日志录入
            match_name = request.form.get('match')
            direction = request.form.get('direction')
            result_field = request.form.get('result')
            amount = float(request.form.get('amount'))
            round_no = len(logs) + 1
            with open(LOG_FILE, mode='a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow([round_no, match_name, direction, amount, result_field, round(current_balance, 2), "Manual entry"])
        return redirect('/')

    return render_template('index.html', logs=logs, balance=round(current_balance, 2), bet_amount=bet_amount, matches=matches)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
