import time
import requests

# ---------------------------
# 配置参数
# ---------------------------
INITIAL_BALANCE = 100
START_PERCENT = 0.01
MAX_LOSS_STREAK = 5
LOG_FILE = "bet_log.csv"

# 替换为你自己的 Cloudbet API Key
API_KEY = "eyJhbGciOiJSUzI1NiIsImtpZCI6IkhKcDkyNnF3ZXBjNnF3LU9rMk4zV05pXzBrRFd6cEdwTzAxNlRJUjdRWDAiLCJ0eXAiOiJKV1QifQ.eyJhY2Nlc3NfdGllciI6InRyYWRpbmciLCJleHAiOjE5OTYyMzk5ODIsImlhdCI6MTY4MDg3OTk4MiwianRpIjoiNDM2Yzc1NjgtMTM0Ny00MDJhLTg4ZDMtZDlhZmU3OGQ1MDdiIiwic3ViIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIiwidGVuYW50IjoiY2xvdWRiZXQiLCJ1dWlkIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIn0.4eI0AK7z17EyutBgx_0FLUc9r5nWR_oUuiurGPyNlcGSz3853wkipm1ul_-oIlijPbaIha1UoD_2v3u-X48cJsmQglLNyst-2UPie9qQ3t8bzQUlhnHjcye7Kc-msGHNi-ML5twdRI-42sESiAECTccsB6NVebHgCqZfAh9-PVT-Hmao4c9AJiyJ2NA5QOTcBz7BJR06MTC0ZMW5Yklm001eEaDYxpBAorDmvRg5GDldlCBuQfVcvip8Zkp0uPHuAu2TJTJrw7tMYXSn7CUWWlQ_oQ7Alb-AchSOLkk7y-eUfUtu7plYJnj50wBLs-NLBzjnV3ifUhDk0etB9HNebA"
BASE_URL = "https://sports-api.cloudbet.com/pub/v2/odds/events"

# 顶级联赛列表
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
    'soccer-iraq-iraqi-league',
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


# ---------------------------
# 函数定义
# ---------------------------
def fetch_matches():
    """
    请求 Cloudbet API 获取未来72小时内的比赛数据，
    并筛选出符合以下条件的比赛：
      1. 所属联赛在 TOP_LEAGUES 内且不是虚拟联赛
      2. 市场为亚洲让球盘口（asian_handicap），且主队盘口为让1球 (handicap=-1)
    返回符合条件的比赛列表，每个元素包含比赛 ID、对阵信息、开赛时间、赔率和市场 URL。
    """
    # 获取当前时间和未来72小时的时间戳
    now = int(time.time())
    future = now + 72 * 3600

    headers = {"X-API-Key": API_KEY}
    params = {
        "sport": "soccer",
        "from": now,
        "to": future,
        "markets": "soccer.asian_handicap",  # 根据 API 规格
        "players": False,
        "limit": 1000
    }

    try:
        response = requests.get(BASE_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()  # 若状态码不为200则抛出异常
    except requests.RequestException as e:
        print(f"请求 API 时出错: {e}")
        return []

    data = response.json()
    matches = []

    # 遍历每个联赛
    for comp in data.get("competitions", []):
        comp_name = comp.get("name", "")
        comp_key = comp.get("key", "")
        # 排除虚拟联赛或不在顶级联赛列表中的联赛
        if "virtual" in comp_name.lower() or comp_key not in TOP_LEAGUES:
            continue

        # 遍历每个比赛事件
        for event in comp.get("events", []):
            # 遍历每个市场
            for market_key, market in event.get("markets", {}).items():
                # 判断市场键名中是否包含 "asian_handicap"（忽略大小写）
                if "asian_handicap" not in market_key.lower():
                    continue
                # 遍历子市场
                for sub in market.get("submarkets", {}).values():
                    # 调试打印子市场信息
                    # print(sub)
                    # 遍历每个选项

                    for sel in sub.get("selections", []):
                        # 筛选条件：选项 outcome 为 "home" 且盘口参数为 "handicap=-1"

                        if sel.get("outcome") == "home" and sel.get("params") == "handicap=-1" and 1.5 <=sel.get("price") <= 2:
                            matches.append({
                                "event_id": event.get("id"),
                                "match": f"{event.get('home', {}).get('name', 'N/A')} vs {event.get('away', {}).get('name', 'N/A')}",
                                "start": event.get("cutoffTime"),
                                "price": sel.get("price"),
                                "market_url": f"{market_key}/home?handicap=-1"
                            })
    return matches


def main():
    matches = fetch_matches()
    if matches:
        print("符合条件的比赛：")
        for m in matches:
            print(f"比赛: {m['match']}")
            print(f"  开赛时间: {m['start']}")
            print(f"  赔率: {m['price']}")
            print(f"  事件 ID: {m['event_id']}")
            print(f"  市场 URL: {m['market_url']}")
            print("-" * 40)
    else:
        print("没有找到符合条件的比赛。")


# ---------------------------
# 主程序入口
# ---------------------------
if __name__ == '__main__':
    main()
