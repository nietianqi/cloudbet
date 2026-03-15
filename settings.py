"""
全局配置文件 — 所有 API Key 和运行参数在这里统一设置
================================================================
改完这一个文件就能跑起来，不需要改其他任何文件。

快速开始：
    1. 填入 CLOUDBET_API_KEY（必须）
    2. API_FOOTBALL_KEY 已配置好（可选，提升足球 xG 精度）
    3. 先用 DRY_RUN = True 模拟观察，确认有信号后再真实下单
"""

import os

# ================================================================
# ① Cloudbet Trading API Key（必须）
# ================================================================
# 获取方式：登录 Cloudbet → 账户 → API Key → Trading Key
# 格式：JWT Token（eyJ 开头的长字符串）
#
# 方式一：直接填写（推荐，最简单）
CLOUDBET_API_KEY = os.environ.get("CLOUDBET_API_KEY", "")
#
# 方式二：环境变量（安全但需要每次设置）
#   export CLOUDBET_API_KEY=your_trading_jwt_token

# ================================================================
# ② API-Football Key（可选，提升足球 xG 数据精度）
# ================================================================
# 来源：https://dashboard.api-football.com/
# 免费层：100 次/天（够用于直播监控）
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "f5168d66f1b016876c537240592e4fc6")

# ================================================================
# ③ API-Football 接入方式（直连 vs RapidAPI）
# ================================================================
# "direct"   → 直连 v3.football.api-sports.io，使用 x-apisports-key 头
#              ✅ 推荐（你的 key 来自 dashboard.api-football.com）
# "rapidapi" → 通过 RapidAPI 代理，使用 x-rapidapi-key 头
#              适合从 RapidAPI 市场订阅的 key
APISPORTS_PROVIDER = "direct"

# ================================================================
# ④ 运行模式
# ================================================================
# True  = 模拟模式：扫描信号、记录日志，不下真钱 ✅ 推荐先用这个
# False = 真实下单：⚠️ 确认策略有正 CLV 后再切换
DRY_RUN = True

# ================================================================
# ⑤ 货币
# ================================================================
# "PLAY_EUR" = Cloudbet 测试资金（不花真钱，推荐先用）
# "USDT"     = 真实资金（谨慎！）
CURRENCY = "PLAY_EUR"

# ================================================================
# ⑥ 数据库文件路径
# ================================================================
DB_FILE = "live_betting.db"

# ================================================================
# ⑦ 足球策略参数
# ================================================================
SOCCER_EDGE_THRESHOLD = 0.05        # 最小 edge 5%（越高越保守）
SOCCER_MIN_REMAINING = 10.0         # 距结束至少剩 10 分钟
SOCCER_KELLY_FRACTION = 0.25        # 1/4 Kelly（保守）
SOCCER_MAX_STAKE_PCT = 0.005        # 单注最大 0.5% 资金
SOCCER_MIN_STAKE = 1.0              # 最小注额（USDT）
SOCCER_SLEEP_INTERVAL = 30          # 轮询间隔（秒）
SOCCER_DAILY_LOSS_LIMIT = 0.10      # 日内亏损 > 10% 熔断
SOCCER_MAX_CONSEC_LOSSES = 5        # 连续亏损 5 次熔断
SOCCER_MAX_CONSEC_REJECTS = 5       # 连续拒单 5 次熔断
SOCCER_MAX_REJECTION_RATE = 0.70    # 近 100 笔拒单率 > 70% 熔断
SOCCER_ACCEPT_PRICE_CHANGE = "NONE" # NONE=拒绝赔率变差 BETTER=接受更好

# ================================================================
# ⑧ NBA 策略参数
# ================================================================
NBA_EDGE_THRESHOLD = 0.05           # 最小 edge 5%
NBA_MIN_REMAINING = 6.0             # 距结束至少剩 6 分钟
NBA_STABLE_WINDOW = 20              # 盘口稳定窗口（秒）
NBA_PRIOR_WEIGHT = 0.45             # 贝叶斯先验权重
NBA_KELLY_FRACTION = 0.25           # 1/4 Kelly
NBA_MAX_STAKE_PCT = 0.005           # 单注最大 0.5% 资金
NBA_MIN_STAKE = 1.0                 # 最小注额
NBA_SLEEP_INTERVAL = 15             # 轮询间隔（秒）
NBA_DAILY_LOSS_LIMIT = 0.10         # 日内亏损 > 10% 熔断
NBA_MAX_CONSEC_LOSSES = 5           # 连续亏损 5 次熔断
NBA_MAX_CONSEC_REJECTS = 5          # 连续拒单 5 次熔断
NBA_MAX_REJECTION_RATE = 0.70       # 拒单率 > 70% 熔断
NBA_ACCEPT_PRICE_CHANGE = "NONE"    # NONE=拒绝赔率变差
