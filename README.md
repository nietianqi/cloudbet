# Cloudbet 直播投注系统

> NBA 直播总分 + 足球直播总进球 | 贝叶斯定价 + 正期望值(+EV) | CLV 追踪

## 项目结构

```
cloudbet/
├── cloudbet_client.py   # Cloudbet API 封装（Feed v2 / Trading v3 / Account v1）
│
├── nba_bot.py           # NBA 直播总分机器人（主入口）
├── nba_model.py         # NBA 贝叶斯定价模型（正态分布近似）
├── nba_strategy.py      # NBA 信号生成模块
│
├── soccer_bot.py        # 足球直播总进球机器人（主入口）
├── soccer_model.py      # 足球 Poisson/Dixon-Coles 定价模型
├── soccer_strategy.py   # 足球信号生成模块
├── xg_client.py         # xG 数据客户端（API-Football 免费版）
│
├── live_db.py           # SQLite 数据库（WAL 模式，4张表）
├── clv_report.py        # CLV 分析报告工具
├── config.py            # 全局配置
└── requirements.txt     # 依赖
```

## 快速开始

### 1. 设置 API Key

```bash
export CLOUDBET_API_KEY=your_trading_api_key
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 运行（模拟模式）

```bash
# NBA 直播总分机器人（默认模拟模式，不下真钱）
python nba_bot.py --dry-run

# 足球直播总进球机器人
python soccer_bot.py --dry-run

# 使用 PLAY_EUR 测试资金真实测试
python nba_bot.py --play
python soccer_bot.py --play
```

### 4. 查看 CLV 报告

```bash
# 结算待处理订单
python clv_report.py --settle --api-key $CLOUDBET_API_KEY

# 查看 CLV 分析
python clv_report.py
```

## 策略说明

### NBA 直播总分

- **模型**：贝叶斯定价 — 赛前先验线 + 实时得分率后验更新
- **入场条件**：edge ≥ 5%，盘口稳定 20 秒，剩余 ≥ 6 分钟
- **仓位**：1/4 Kelly，单注上限 0.5% 资金

### 足球直播总进球

- **模型**：Poisson + Dixon-Coles 修正 + xG 贝叶斯混合
- **场态调整**：红牌 -25%，比分差≥2 -12%，最后15分钟落后 +18%
- **入场条件**：edge ≥ 5%，盘口稳定 30 秒，剩余 ≥ 10 分钟

## 核心 KPI

```
avg_CLV% > 0  →  策略有真实 edge，盈利只是时间问题
avg_CLV% ≤ 0  →  无 edge，需要重新审视信号质量
```

## 风控熔断

| 触发条件 | 阈值 |
|---------|------|
| 日内亏损 | ≥ 10% 资金 |
| 连续拒单 | 5 次 |
| 近100笔拒单率 | > 70% |
| 连续亏损 | 5 次 |

> Cloudbet 条款：拒单率 > 75% 在 100 笔内触发，账户冻结 7 天。

## 环境变量

| 变量 | 说明 |
|------|------|
| `CLOUDBET_API_KEY` | Trading API Key（必填） |
| `APIFOOTBALL_KEY` | API-Football Key（可选，提升 xG 精度） |
