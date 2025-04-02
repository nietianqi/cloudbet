from flask import Flask, render_template, request, redirect
import csv
import os
import requests
import time
import uuid
import logging

app = Flask(__name__)

# ---------------------------
# 配置参数
# ---------------------------
INITIAL_BALANCE = 100  # 若账户 API 调用失败则使用初始余额
START_PERCENT = 0.005  # 基础下注比例：0.5%
MAX_RISK_PERCENT = 0.05  # 单次下注最高风险比例：5%
MAX_LOSS_STREAK = 5  # 最大连续亏损次数
LOG_FILE = "bet_log.csv"

API_KEY = "eyJhbGciOiJSUzI1NiIsImtpZCI6IkhKcDkyNnF3ZXBjNnF3LU9rMk4zV05pXzBrRFd6cEdwTzAxNlRJUjdRWDAiLCJ0eXAiOiJKV1QifQ.eyJhY2Nlc3NfdGllciI6InRyYWRpbmciLCJleHAiOjE5OTYyMzk5ODIsImlhdCI6MTY4MDg3OTk4MiwianRpIjoiNDM2Yzc1NjgtMTM0Ny00MDJhLTg4ZDMtZDlhZmU3OGQ1MDdiIiwic3ViIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIiwidGVuYW50IjoiY2xvdWRiZXQiLCJ1dWlkIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIn0.4eI0AK7z17EyutBgx_0FLUc9r5nWR_oUuiurGPyNlcGSz3853wkipm1ul_-oIlijPbaIha1UoD_2v3u-X48cJsmQglLNyst-2UPie9qQ3t8bzQUlhnHjcye7Kc-msGHNi-ML5twdRI-42sESiAECTccsB6NVebHgCqZfAh9-PVT-Hmao4c9AJiyJ2NA5QOTcBz7BJR06MTC0ZMW5Yklm001eEaDYxpBAorDmvRg5GDldlCBuQfVcvip8Zkp0uPHuAu2TJTJrw7tMYXSn7CUWWlQ_oQ7Alb-AchSOLkk7y-eUfUtu7plYJnj50wBLs-NLBzjnV3ifUhDk0etB9HNebA"
BASE_URL = "https://sports-api.cloudbet.com/pub/v2/odds/events"
BET_URL = "https://sports-api.cloudbet.com/pub/v3/bets/place"
ACCOUNT_URL = "https://sports-api.cloudbet.com/pub/v1/account/currencies"

# 顶级联赛 key
TOP_LEAGUES = {
    # 足球排名前20国家（一级和二级联赛）
    # England
    'soccer-england-premier-league',
    'soccer-england-championship',
    # Spain
    'soccer-spain-laliga',
    'soccer-spain-laliga-2',
    # Germany
    'soccer-germany-bundesliga',
    'soccer-germany-2nd-bundesliga',
    # Italy
    'soccer-italy-serie-a',
    'soccer-italy-serie-b',
    # France
    'soccer-france-ligue-1',
    'soccer-france-ligue-2',
    # Brazil
    'soccer-brazil-brasileiro-serie-a',
    'soccer-brazil-brasileiro-serie-b',
    # Argentina
    'soccer-argentina-superliga',
    'soccer-argentina-primera-nacional',
    # Netherlands
    'soccer-netherlands-eredivisie',
    'soccer-netherlands-eerste-divisie',
    # Belgium
    'soccer-belgium-first-division-a',
    'soccer-belgium-first-division-b',
    # Denmark
    'soccer-denmark-superligaen',
    'soccer-denmark-1st-division',
    # Switzerland
    'soccer-switzerland-super-league',
    'soccer-switzerland-challenge-league',
    # Uruguay
    'soccer-uruguay-primera-division',
    'soccer-uruguay-segunda-division',
    # Croatia
    'soccer-croatia-1-hnl',
    'soccer-croatia-2-hnl',
    # Portugal
    'soccer-portugal-primeira-liga',
    'soccer-portugal-segunda-liga',
    # Mexico
    'soccer-mexico-primera-division-clausura',
    'soccer-mexico-liga-de-ascenso-clausura',
    # USA
    'soccer-usa-major-league-soccer',
    'soccer-usa-usl-championship',
    # Chile
    'soccer-chile-primera-division',
    # Sweden
    'soccer-sweden-allsvenskan',
    'soccer-sweden-superettan',
    # Japan
    'soccer-japan-j-league',
    'soccer-japan-j-league-2',
    # Turkey
    'soccer-turkey-super-lig',
    'soccer-turkey-1-lig',

    # 其它国家（仅一级联赛）
    'soccer-australia-a-league',
    'soccer-austria-bundesliga',
    'soccer-bolivia-division-profesional',
    'soccer-bosnia-herzegovina-premijer-liga',
    'soccer-bulgaria-first-professional-league',
    'soccer-canada-canadian-premier-league',
    'soccer-costa-rica-primera-division-clausura',
    'soccer-cyprus-1st-division',
    'soccer-czech-republic-1-liga',
    'soccer-ecuador-ligapro-primera-a',
    'soccer-estonia-premium-liiga',
    'soccer-ethiopia-premier-league',
    'soccer-faroe-islands-1st-deild',
    'soccer-finland-veikkausliiga',
    'soccer-ghana-premier-league',
    'soccer-greece-super-league-1',
    'soccer-guatemala-liga-nacional-clausura',
    'soccer-honduras-liga-nacional-clausura',
    'soccer-hungary-nb-i',
    'soccer-iceland-t9038-besta-deild',
    'soccer-india-indian-super-league',
    'soccer-indonesia-liga-1',
    'soccer-iraqi-league',
    'soccer-ireland-premier-division',
    'soccer-israel-premier-league',
    'soccer-jamaica-premier-league',
    'soccer-latvia-virsliga',
    'soccer-luxembourg-promotion-d-honneur',
    'soccer-montenegro-1-cfl',
    'soccer-northern-ireland-premiership',
    'soccer-norway-eliteserien',
    'soccer-panama-liga-panamena-de-futbol-apertura',
    'soccer-paraguay-primera-division-apertura',
    'soccer-philippines-philippines-footb-league',
    'soccer-poland-ekstraklasa',
    'soccer-qatar-stars-league',
    'soccer-romania-liga-i',
    'soccer-rwanda-rwanda-premier-league',
    'soccer-saudi-arabia-saudi-prof-league',
    'soccer-scotland-premiership',
    'soccer-serbia-superliga',
    'soccer-singapore-premier-league',
    'soccer-slovakia-superliga',
    'soccer-slovenia-prvaliga',
    'soccer-south-africa-t903e-premiership',
    'soccer-south-korea-k-league-1',
    'soccer-uganda-premier-league',
    'soccer-ukraine-premier-league',
    'soccer-uzbekistan-pfl',
    'soccer-venezuela-primera-division',
    'soccer-zambia-super-league'
}

# 定义目标盘口和水位范围（这里表示主队让1球，即 handicap=-1，
# 且主队水位在 TARGET_ODDS_LOW ~ TARGET_ODDS_HIGH 范围内）
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
        with open(LOG_FILE, mode='r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                logs.append(row)
    return logs


def next_bet_amount(current_balance, logs):
    base_bet = current_balance * START_PERCENT
    bet_amount = base_bet
    loss_streak = 0
    # 根据日志中的连续亏损翻倍下注（不超过最大连续亏损次数）
    for log in reversed(logs):
        if log['Result'] == 'Lose':
            bet_amount *= 2
            loss_streak += 1
        else:
            break
        if loss_streak >= MAX_LOSS_STREAK:
            break
    # 限制下注额不超过账户余额的最大风险比例
    max_bet = current_balance * MAX_RISK_PERCENT
    return round(min(bet_amount, max_bet), 2)


# ---------------------------
# API 请求和比赛筛选函数
# ---------------------------
def fetch_target_matches():
    """
    请求 Cloudbet API 获取未来72小时内的比赛数据，并筛选出：
    1. 参赛联赛为 TOP_LEAGUES（排除虚拟联赛）
    2. 在 asian_handicap 市场中，主队盘口为让1球 (handicap=-1)
       且主队水位在 TARGET_ODDS_LOW ~ TARGET_ODDS_HIGH 范围内
    """
    now = int(time.time())
    future = now + 72 * 3600
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
        # 排除虚拟联赛或不在顶级联赛列表中的联赛
        if "virtual" in comp_name.lower() or comp_key not in TOP_LEAGUES:
            continue

        for event in comp.get("events", []):
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
                                "market_url": f"{market_key}/home?handicap={TARGET_HANDICAP}"
                            })
    return matches


def place_bet(event_id, market_url, price, stake):
    """
    下单函数，向 Cloudbet API 发送下注请求
    """
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
        "currency": "USDT",
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


# ---------------------------
# Flask 路由
# ---------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    logs = load_logs()
    # 实时获取账户余额
    current_balance = get_current_balance("USDT")
    # 根据当前余额和历史日志计算下注额
    bet_amount = next_bet_amount(current_balance, logs)
    matches = fetch_target_matches()

    if request.method == 'POST':
        # 如果表单中含有 event_id，则为自动下注表单
        event_id = request.form.get('event_id')
        if event_id:
            match_name = request.form.get('match')
            market_url = request.form.get('market_url')
            price = request.form.get('price')
            amount = float(request.form.get('amount'))

            bet_response, status = place_bet(event_id, market_url, price, amount)
            result = "Pending"
            notes = ""
            if status == 200 and bet_response.get("status") == "ACCEPTED":
                notes = "Bet placed"
            else:
                notes = f"Failed: {bet_response.get('error', 'Unknown error')}"
                logging.error(f"下注失败: {notes}")

            round_no = len(logs) + 1
            # 写入下注记录日志，同时更新余额为当前余额
            with open(LOG_FILE, mode='a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(
                    [round_no, match_name, f"BACK @ {price}", amount, result, round(current_balance, 2), notes])
        else:
            # 手动日志录入
            match_name = request.form.get('match')
            direction = request.form.get('direction')
            result = request.form.get('result')
            amount = float(request.form.get('amount'))
            round_no = len(logs) + 1
            with open(LOG_FILE, mode='a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(
                    [round_no, match_name, direction, amount, result, round(current_balance, 2), "Manual entry"])
        return redirect('/')

    return render_template('index.html', logs=logs, balance=round(current_balance, 2), bet_amount=bet_amount,
                           matches=matches)


if __name__ == '__main__':
    app.run(debug=True)
