"""
投注系统配置文件 - 精简可执行版
"""

# ========================================
# API 配置
# ========================================
API_KEY = "eyJhbGciOiJSUzI1NiIsImtpZCI6IkhKcDkyNnF3ZXBjNnF3LU9rMk4zV05pXzBrRFd6cEdwTzAxNlRJUjdRWDAiLCJ0eXAiOiJKV1QifQ.eyJhY2Nlc3NfdGllciI6InRyYWRpbmciLCJleHAiOjE5OTYyMzk5ODIsImlhdCI6MTY4MDg3OTk4MiwianRpIjoiNDM2Yzc1NjgtMTM0Ny00MDJhLTg4ZDMtZDlhZmU3OGQ1MDdiIiwic3ViIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIiwidGVuYW50IjoiY2xvdWRiZXQiLCJ1dWlkIjoiNDM4MzY1YTUtMzQ0Yi00NTRmLWE5NmQtM2YyMWUzMDc1YmYwIn0.4eI0AK7z17EyutBgx_0FLUc9r5nWR_oUuiurGPyNlcGSz3853wkipm1ul_-oIlijPbaIha1UoD_2v3u-X48cJsmQglLNyst-2UPie9qQ3t8bzQUlhnHjcye7Kc-msGHNi-ML5twdRI-42sESiAECTccsB6NVebHgCqZfAh9-PVT-Hmao4c9AJiyJ2NA5QOTcBz7BJR06MTC0ZMW5Yklm001eEaDYxpBAorDmvRg5GDldlCBuQfVcvip8Zkp0uPHuAu2TJTJrw7tMYXSn7CUWWlQ_oQ7Alb-AchSOLkk7y-eUfUtu7plYJnj50wBLs-NLBzjnV3ifUhDk0etB9HNebA"

BASE_URL = "https://sports-api.cloudbet.com/pub/v2/odds/events"
BET_URL = "https://sports-api.cloudbet.com/pub/v3/bets/place"
ACCOUNT_URL = "https://sports-api.cloudbet.com/pub/v1/account/currencies"
CURRENCY = "USDT"

# ========================================
# 联赛选择模式
# ========================================
# 选项: 'TIER_1' (38个核心联赛) | 'EXPANDED' (153个扩展) | 'ALL' (184个全部)
# 推荐：先用 TIER_1 测试，胜率 > 60% 后再扩展
LEAGUE_MODE = 'ALL'  # 启用全部184个联赛

# ========================================
# 比赛筛选
# ========================================
TARGET_HANDICAP = "-0.5"        # 主队让0.5球（-0.5盘口最常见，通过率高）
TARGET_ODDS_LOW = 1.5           # 最低赔率
TARGET_ODDS_HIGH = 2.0          # 最高赔率

# ⚠️ 重要：时间窗口设置
# 当前值 2 表示：离开赛 1.5-2.5 分钟的比赛（很严格）
# 如果找不到比赛，建议改为 5 或 10
# 值越大，找到的比赛越多，但赔率可能波动更大
MATCH_TIME_BEFORE = 5           # 修改为5分钟（查找4.5-5.5分钟窗口的比赛）

# 赔率安全区间（二次筛选）
# 如果找不到比赛，可以禁用（设为与 TARGET_ODDS 相同）
SAFE_ODDS_LOW = 1.5             # 与 TARGET_ODDS_LOW 保持一致
SAFE_ODDS_HIGH = 2.0            # 与 TARGET_ODDS_HIGH 保持一致

# 主胜1X2赔率要求（表示真·强队）
# 值越小越严格，如果找不到比赛可以放宽到 1.70
MAX_HOME_WIN_ODDS = 1.8

# ========================================
# 赔率走势监控
# ========================================
# 如果找不到比赛，建议先禁用（改为 False）
ENABLE_ODDS_TRACKING = False    # 推荐先禁用，积累数据后再启用
TRACKING_START_MINUTES = 10     # 从开赛前10分钟开始监控
TRACKING_INTERVAL = 30          # 每30秒检查一次
ODDS_DROP_THRESHOLD = -0.03     # 要求尾盘下降至少0.03（走热）
MAX_ODDS_VOLATILITY = 0.15      # 最大波动幅度

# ========================================
# 场次控制
# ========================================
MAX_BETS_PER_30MIN = 1          # 每30分钟最多下注1场
MIN_MATCH_SCORE = 0             # 最低匹配评分（0=不限制）

# ========================================
# 资金管理（2.5倍马丁格尔）
# ========================================
# 核心参数
R = 0.30                        # 单序列占总资金的30%
Q = 2.5                         # 倍率2.5
N = 4                           # 最大连输4次
MAX_ONE = 0.05                  # 单场最大下注5%
MIN_BET = 1.0                   # 最小投注1 USDT

# ========================================
# 日内风控
# ========================================
DAILY_LOSS_LIMIT = 0.20         # 单日最大亏损20%
DAILY_PROFIT_TARGET = 0.05      # 单日盈利目标5%（达到后收工）
ENABLE_DAILY_PROFIT_LOCK = True # 启用每日盈利锁定

# ========================================
# 系统运行
# ========================================
SLEEP_INTERVAL = 30             # 轮询间隔30秒
LOG_FILE = "bet_log.csv"
INITIAL_BALANCE = 100           # 备用初始余额

# ========================================
# 高级选项
# ========================================
DRY_RUN = False                # 真实下单模式（实际下注）⚠️
VERBOSE = False                 # 详细日志（改为 False 减少日志）

# ========================================
# 💡 快速配置建议
# ========================================
"""
【找不到比赛？试试这个配置】

LEAGUE_MODE = 'EXPANDED'        # 扩展到153个联赛
MATCH_TIME_BEFORE = 10          # 10分钟窗口
SAFE_ODDS_LOW = 1.50            # 不限制安全区
SAFE_ODDS_HIGH = 1.95
MAX_HOME_WIN_ODDS = 1.70        # 放宽独赢
ENABLE_ODDS_TRACKING = False    # 禁用走势监控
MIN_MATCH_SCORE = 0             # 不限制评分

---

【严格质量配置】

LEAGUE_MODE = 'TIER_1'          # 只38个核心联赛
MATCH_TIME_BEFORE = 5           # 5分钟窗口
SAFE_ODDS_LOW = 1.60            # 严格安全区
SAFE_ODDS_HIGH = 1.75
MAX_HOME_WIN_ODDS = 1.50        # 只要大热门
ENABLE_ODDS_TRACKING = True     # 启用走势
MIN_MATCH_SCORE = 8.0           # 高评分

---

查看更多帮助：常见问题解决.md
"""

# ========================================
# NBA 直播总分策略配置（nba_bot.py 使用）
# ========================================
# 快速开始：
#   python nba_bot.py --dry-run           # 先用模拟模式观察信号
#   python nba_bot.py --play --dry-run    # 用测试资金，模拟不下单
#   python nba_bot.py --play              # 用 PLAY_EUR 测试资金真实下单
#   python nba_bot.py --real              # 用 USDT 真实资金（谨慎）

# 信号阈值（越高越保守，但信号越少）
NBA_EDGE_THRESHOLD = 0.05       # 最小 edge：5%
NBA_MIN_REMAINING = 6.0         # 距结束至少剩 6 分钟入场
NBA_STABLE_WINDOW = 20          # 盘口稳定性检测窗口（秒）
NBA_PRIOR_WEIGHT = 0.45         # 贝叶斯先验权重（0=完全信实时，1=完全信先验）

# 仓位管理
NBA_KELLY_FRACTION = 0.25       # 1/4 Kelly（推荐保守值）
NBA_MAX_STAKE_PCT = 0.005       # 单注最大 0.5% 资金
NBA_MIN_STAKE = 1.0             # 最小注额（USDT）

# 风控熔断
NBA_DAILY_LOSS_LIMIT = 0.10     # 日内亏损 > 10% 停机
NBA_MAX_CONSEC_LOSSES = 5       # 连续亏损 5 次停机复盘
NBA_MAX_REJECTION_RATE = 0.70   # 拒单率 > 70% 停机

# 执行参数
NBA_SLEEP_INTERVAL = 15         # 轮询间隔（秒）；直播建议 10-20
NBA_DRY_RUN = True              # 默认模拟模式；改为 False 才真实下单
NBA_CURRENCY = "PLAY_EUR"       # 测试货币；真实下注改为 "USDT"
NBA_DB_FILE = "live_betting.db" # SQLite 数据库文件
