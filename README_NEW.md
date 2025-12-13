# Cloudbet 自动投注系统

> 基于 2.5倍马丁格尔策略 + 13层智能筛选的足球让球盘自动投注系统

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## ⚠️ 风险提示

**投资有风险，使用需谨慎！**

- 本系统仅供学习和研究使用
- 任何自动化投注都存在亏损风险
- 建议使用小额资金测试
- 不对任何亏损负责

## ✨ 核心特性

### 📊 13层智能筛选
```
API比赛 → 虚拟过滤 → 联赛过滤 → 时间窗口 → 让球盘 →
赔率范围 → 安全区 → 独赢赔率 → 走势分析 → 评分 →
排序 → 30分控制 → 历史检查 → ✅ 下注
```

### 💰 2.5倍马丁格尔资金管理
- **科学倒推公式**：`s0 = r * B * (q-1) / (q^(N+1) - 1)`
- **参数可调**：倍率、连败上限、风险比例
- **示例（100U）**：
  ```
  第1注: 1.38U  | 第2注: 3.46U  | 第3注: 8.64U
  第4注: 21.60U | 第5注: 50.00U (MAX_ONE限制)
  ```

### 🎯 4维度评分系统
1. **联赛评分**（0-10分）- 五大联赛10分
2. **让球盘评分**（0-3分）- 最优点1.70
3. **独赢评分**（0-2分）- 越低越好
4. **走势评分**（0-2分）- 尾盘下降加分

### 🌍 184个全球联赛
- **TIER_1**：38个核心联赛（五大联赛+主流）
- **TIER_2**：115个扩展联赛（次级+地区）
- **TIER_3**：31个国际赛事

### 🛡️ 多重风控机制
- **单场限制**：最大5%资金
- **连败保护**：4次后自动停止
- **场次控制**：30分钟最多1场
- **日内风控**：单日亏损20%停止

## 🚀 快速开始

### 1. 环境要求
```bash
Python 3.7+
pip install -r requirements.txt
```

### 2. 配置API密钥
编辑 `config.py`：
```python
API_KEY = "your_cloudbet_api_key_here"
```

### 3. 测试筛选
```bash
python debug_matcher.py
```

### 4. 模拟运行
```python
# config.py
DRY_RUN = True  # 模拟模式
LEAGUE_MODE = 'TIER_1'  # 只投注核心联赛
MATCH_TIME_BEFORE = 5  # 开赛前5分钟
```

```bash
python bot.py
```

### 5. 真实下注
```python
# 确认配置无误后
DRY_RUN = False  # ⚠️ 真实下单
```

## 📁 项目结构

```
cloudbet/
│
├── bot.py                # 主程序
├── matcher.py            # 比赛筛选模块
├── bankroll.py           # 资金管理模块
├── config.py             # 配置文件
├── core_leagues.py       # 联赛数据库
├── stats.py              # 统计分析工具
│
├── debug_matcher.py      # 调试工具
├── monitor.py            # 实时监控
│
├── requirements.txt      # 依赖包
├── README.md             # 本文档
├── ARCHITECTURE.md       # 架构文档
│
├── bet_log.csv           # 投注日志（自动生成）
└── bot.log               # 运行日志（自动生成）
```

## ⚙️ 配置参数详解

### 核心参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LEAGUE_MODE` | `'ALL'` | 联赛模式：TIER_1/EXPANDED/ALL |
| `MATCH_TIME_BEFORE` | `1` | 开赛前N分钟下注 |
| `TARGET_ODDS_LOW` | `1.2` | 让球盘最低赔率 |
| `TARGET_ODDS_HIGH` | `1.95` | 让球盘最高赔率 |
| `MAX_HOME_WIN_ODDS` | `1.8` | 独赢最高赔率 |
| `DRY_RUN` | `False` | 模拟模式开关 |

### 资金管理

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `R` | `0.30` | 单序列占资金30% |
| `Q` | `2.5` | 马丁格尔倍率 |
| `N` | `4` | 最大连输次数 |
| `MAX_ONE` | `0.05` | 单场最大5%资金 |
| `MIN_BET` | `1.0` | 最小投注额 |

### 风控参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DAILY_LOSS_LIMIT` | `0.20` | 单日最大亏损20% |
| `DAILY_PROFIT_TARGET` | `0.05` | 单日盈利目标5% |
| `MAX_BETS_PER_30MIN` | `1` | 30分钟最多1场 |
| `MIN_MATCH_SCORE` | `0` | 最低评分（0=不限） |

## 🔧 使用指南

### 调试筛选逻辑
```bash
python debug_matcher.py
```

输出示例：
```
================================================================================
📊 筛选统计
================================================================================
总比赛数: 25
  ❌ 联赛筛选: -5
  ❌ 时间筛选: -10
  ❌ 无-1盘口: -3
  ❌ 赔率范围: -2
  ✅ 通过所有: 5
```

### 查看统计数据
```bash
python stats.py
```

输出示例：
```
📊 投注统计报告
================================================================================
【总体统计】
  总投注数: 50
  胜: 32 | 负: 15 | 平: 3
  胜率: 64.00%
  总盈亏: +15.50 USDT
  ROI: +12.34%
  最大连胜: 7 | 最大连败: 3

【联赛统计】（前10）
联赛                                     投注   胜     胜率    盈亏
----------------------------------------------------------------------
English Premier League                   15     10     66.7%    +8.50
Spanish LaLiga                           12     8      66.7%    +6.20
...
```

### 实时监控
```bash
python monitor.py
```

## 📈 优化建议

### 初期测试（建议配置）
```python
LEAGUE_MODE = 'TIER_1'           # 只38个核心联赛
MATCH_TIME_BEFORE = 5            # 5分钟窗口
SAFE_ODDS_LOW = 1.60             # 严格赔率
SAFE_ODDS_HIGH = 1.75
MAX_HOME_WIN_ODDS = 1.60         # 只要大热门
ENABLE_ODDS_TRACKING = False     # 禁用走势
MIN_MATCH_SCORE = 8.0            # 高评分
DRY_RUN = True                   # 模拟模式
```

### 稳定运行（胜率>60%后）
```python
LEAGUE_MODE = 'EXPANDED'         # 扩展到153个
MATCH_TIME_BEFORE = 3            # 缩小窗口
ENABLE_ODDS_TRACKING = True      # 启用走势
MIN_MATCH_SCORE = 6.0            # 适当放宽
DRY_RUN = False                  # 真实下注
```

### 激进模式（不推荐）
```python
LEAGUE_MODE = 'ALL'              # 全部184个
MATCH_TIME_BEFORE = 10           # 大窗口
MIN_MATCH_SCORE = 0              # 不限评分
```

## 🐛 常见问题

### Q1: 找不到符合条件的比赛？
**A**:
1. 检查时间窗口：`debug_matcher.py` 查看"时间筛选"过滤数量
2. 扩大联赛范围：改为 `LEAGUE_MODE = 'EXPANDED'`
3. 放宽赔率限制：调整 `SAFE_ODDS_LOW/HIGH`
4. 查看当前比赛时间：欧洲主流联赛通常在北京时间晚上

### Q2: 如何提高胜率？
**A**:
1. 收紧筛选条件：提高 `MIN_MATCH_SCORE` 到 8.0
2. 只投核心联赛：使用 `LEAGUE_MODE = 'TIER_1'`
3. 启用走势监控：`ENABLE_ODDS_TRACKING = True`
4. 缩小赔率范围：`TARGET_ODDS_LOW = 1.60`, `TARGET_ODDS_HIGH = 1.80`

### Q3: 连败后怎么办？
**A**:
系统会自动处理：
- 连败4次后自动停止（`N = 4`）
- 单日亏损20%停止（`DAILY_LOSS_LIMIT = 0.20`）
- 建议检查 `bet_log.csv` 分析原因
- 考虑降低倍率 `Q` 从 2.5 降到 2.0

### Q4: API密钥安全问题？
**A**:
- 不要将 `config.py` 上传到公共仓库
- 建议使用环境变量存储API密钥
- 设置严格的API权限（只允许交易和查询）

## 📊 架构文档

详细的系统架构、流程图、数据结构请查看：
- [ARCHITECTURE.md](./ARCHITECTURE.md) - 系统架构详解

## 🔐 安全建议

1. **小额测试**
   - 初期使用10-50 USDT测试
   - 累计50场以上数据后再增加资金

2. **严格风控**
   - 不要修改 `DAILY_LOSS_LIMIT`
   - 单日亏损达标立即停止
   - 定期查看 `stats.py` 统计

3. **监控运行**
   - 每天查看 `bot.log`
   - 分析 `bet_log.csv`
   - 关注异常波动

4. **API安全**
   - 定期更换API密钥
   - 设置IP白名单
   - 监控账户异常登录

## 📝 更新日志

### v1.0.0 (2025-12-04)
- ✅ 完整的13层筛选系统
- ✅ 2.5倍马丁格尔资金管理
- ✅ 184个全球联赛支持
- ✅ 4维度评分系统
- ✅ 赔率走势监控
- ✅ 多重风控机制
- ✅ 统计分析工具
- ✅ 完整文档支持

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发计划
- [ ] 支持更多盘口类型（大小球、欧赔等）
- [ ] 机器学习模型优化评分系统
- [ ] Web界面和实时监控
- [ ] 多账户管理
- [ ] 电报机器人通知

## 📄 许可证

[MIT License](LICENSE)

## ⚡ 快速链接

- [Cloudbet API 文档](https://www.cloudbet.com/api/)
- [问题反馈](https://github.com/nietianqi/cloudbet/issues)

---

**免责声明**: 本项目仅供学习研究使用，使用本系统进行实际投注的一切后果由使用者自行承担。
