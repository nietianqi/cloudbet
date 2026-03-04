"""
NBA 直播总分贝叶斯定价模型
================================
思路：
  1. 赛前总分线作为先验 (prior) — 市场对全场得分的最佳预估
  2. 比赛进行中用实时比分 + 时间做贝叶斯后验更新
  3. 输出: p_over, p_under, fair_price, edge (vs Cloudbet 盘口去水后概率)

模型假设：
  - 篮球每分钟得分近似泊松过程
  - 后验得分率 = prior_rate * (1-w) + observed_rate * w
    (w 随比赛进行线性增大，越到后期越相信实时节奏)
  - 剩余得分用正态分布近似 (样本大时比泊松更稳定)
"""

import math
from typing import Dict


# ── NBA 常规时间参数 ──────────────────────────────────────────
NBA_REGULATION_MINUTES = 48.0   # 4节 × 12分钟
NBA_OT_MINUTES = 5.0            # 每个加时
SIGMA_SCALE = 0.90              # 缩放因子: 实际比赛方差略小于泊松


def _normal_cdf(x: float) -> float:
    """标准正态 CDF（不依赖 scipy，仅用 math.erf）"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_over_prob(mu: float, sigma: float, threshold: float) -> float:
    """
    计算 P(X > threshold)，X ~ Normal(mu, sigma)

    参数:
        mu       : 期望剩余得分
        sigma    : 标准差
        threshold: 需要超过的分数（= 总分线 - 当前总分）

    返回:
        P(over) ∈ [0, 1]
    """
    if threshold <= 0:
        return 1.0           # 已经超过线，必定 over
    if sigma <= 0:
        return 0.0 if mu <= threshold else 1.0
    z = (threshold - mu) / sigma
    return 1.0 - _normal_cdf(z)


def estimate_scoring_rate(
    pregame_line: float,
    current_score: int,
    elapsed_minutes: float,
    prior_weight_base: float = 0.5,
) -> float:
    """
    贝叶斯后验得分率估算（每分钟）

    参数:
        pregame_line      : 赛前总分线 (e.g., 228.5)
        current_score     : 双方当前总得分
        elapsed_minutes   : 已过比赛分钟数
        prior_weight_base : 初始先验权重 (0-1)

    返回:
        posterior_rate: 每分钟期望得分
    """
    prior_rate = pregame_line / NBA_REGULATION_MINUTES

    if elapsed_minutes < 0.5:
        return prior_rate

    observed_rate = current_score / elapsed_minutes

    # 随比赛进行降低先验权重（线性退火）
    time_progress = min(elapsed_minutes / NBA_REGULATION_MINUTES, 1.0)
    prior_weight = prior_weight_base * (1.0 - time_progress)
    obs_weight = 1.0 - prior_weight

    return prior_weight * prior_rate + obs_weight * observed_rate


def compute_live_total_edge(
    pregame_line: float,
    current_score: int,
    elapsed_minutes: float,
    cloudbet_over_price: float,
    cloudbet_under_price: float,
    prior_weight: float = 0.45,
    overtime: bool = False,
) -> Dict:
    """
    计算当前时刻的 edge（相对于 Cloudbet 去水后概率）

    参数:
        pregame_line        : 赛前总分线 (e.g., 228.5)
        current_score       : 当前双方总得分（主 + 客）
        elapsed_minutes     : 已进行分钟数
        cloudbet_over_price : Cloudbet Over 欧洲赔率
        cloudbet_under_price: Cloudbet Under 欧洲赔率
        prior_weight        : 先验权重 (0-1)
        overtime            : 是否加时

    返回:
        dict 包含模型概率、市场概率、edge、公平赔率等

    边际(edge)定义:
        edge_over  = p_model_over  - p_mkt_over  (去水后)
        edge_under = p_model_under - p_mkt_under
        正值表示存在优势
    """
    total_minutes = NBA_REGULATION_MINUTES
    if overtime:
        total_minutes += NBA_OT_MINUTES

    remaining_minutes = max(total_minutes - elapsed_minutes, 0.1)

    # ── 后验得分率 & 期望剩余得分 ─────────────────────────────
    scoring_rate = estimate_scoring_rate(
        pregame_line, current_score, elapsed_minutes, prior_weight
    )
    mu_remaining = scoring_rate * remaining_minutes

    # 标准差：泊松 sigma = sqrt(lambda)，乘以缩放因子
    sigma_remaining = math.sqrt(mu_remaining) * SIGMA_SCALE if mu_remaining > 0 else 1.0

    # ── 模型概率 ──────────────────────────────────────────────
    threshold = pregame_line - current_score   # 还需要多少分才 over
    p_model_over = normal_over_prob(mu_remaining, sigma_remaining, threshold)
    p_model_under = 1.0 - p_model_over

    # ── 市场隐含概率（双边去水，维格去除法）────────────────────
    p_raw_over = 1.0 / cloudbet_over_price if cloudbet_over_price > 1.0 else 0.0
    p_raw_under = 1.0 / cloudbet_under_price if cloudbet_under_price > 1.0 else 0.0
    total_overround = p_raw_over + p_raw_under

    if total_overround > 0:
        p_mkt_over = p_raw_over / total_overround
        p_mkt_under = p_raw_under / total_overround
    else:
        p_mkt_over = p_mkt_under = 0.5

    # ── Edge & 公平赔率 ───────────────────────────────────────
    edge_over = p_model_over - p_mkt_over
    edge_under = p_model_under - p_mkt_under

    fair_over_price = round(1.0 / p_model_over, 3) if p_model_over > 0.01 else 99.0
    fair_under_price = round(1.0 / p_model_under, 3) if p_model_under > 0.01 else 99.0

    return {
        "p_model_over": round(p_model_over, 4),
        "p_model_under": round(p_model_under, 4),
        "p_mkt_over": round(p_mkt_over, 4),
        "p_mkt_under": round(p_mkt_under, 4),
        "edge_over": round(edge_over, 4),
        "edge_under": round(edge_under, 4),
        "fair_over_price": fair_over_price,
        "fair_under_price": fair_under_price,
        "remaining_minutes": round(remaining_minutes, 1),
        "expected_remaining_score": round(mu_remaining, 1),
        "scoring_rate_per_min": round(scoring_rate, 3),
        "threshold": round(threshold, 1),
        "sigma": round(sigma_remaining, 2),
    }


def pick_best_side(model_result: Dict, min_edge: float) -> Dict:
    """
    从模型结果中挑出最优下注方向

    参数:
        model_result: compute_live_total_edge 的返回值
        min_edge    : 最小 edge 阈值（e.g., 0.05 = 5%）

    返回:
        dict: {side, edge, model_prob, mkt_prob, fair_price, market_price}
        或 None（无信号）
    """
    over_edge = model_result["edge_over"]
    under_edge = model_result["edge_under"]

    # 选出 edge 最大且超过阈值的方向
    best_side = None
    best_edge = min_edge  # 必须超过阈值才有信号

    if over_edge >= best_edge:
        best_side = "over"
        best_edge = over_edge

    if under_edge >= best_edge:
        best_side = "under"
        best_edge = under_edge

    if best_side is None:
        return None

    if best_side == "over":
        return {
            "side": "over",
            "edge": over_edge,
            "model_prob": model_result["p_model_over"],
            "mkt_prob": model_result["p_mkt_over"],
            "fair_price": model_result["fair_over_price"],
        }
    else:
        return {
            "side": "under",
            "edge": under_edge,
            "model_prob": model_result["p_model_under"],
            "mkt_prob": model_result["p_mkt_under"],
            "fair_price": model_result["fair_under_price"],
        }


def kelly_stake(edge: float, odds: float, bankroll: float,
                fraction: float = 0.25, max_pct: float = 0.005) -> float:
    """
    分数 Kelly 仓位计算

    Kelly 公式: f = (p*b - q) / b
        p = 胜率（模型概率）
        q = 1 - p
        b = 净赔率（odds - 1）

    参数:
        edge     : edge 值（= model_prob - mkt_prob）
        odds     : 欧洲赔率
        bankroll : 当前资金
        fraction : Kelly 分数 (推荐 0.25 = 1/4 Kelly)
        max_pct  : 单注最大资金占比

    返回:
        stake: 建议注额（USDT）
    """
    if odds <= 1.0 or edge <= 0:
        return 0.0

    b = odds - 1.0
    p = 0.5 + edge / 2.0   # 近似胜率（从 edge 反推）
    q = 1.0 - p

    full_kelly = (p * b - q) / b
    if full_kelly <= 0:
        return 0.0

    stake = fraction * full_kelly * bankroll
    max_stake = bankroll * max_pct
    return round(min(stake, max_stake), 2)


# ── 独立测试 ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("NBA Live Totals 贝叶斯模型 — 单元测试")
    print("=" * 60)

    test_cases = [
        # (pregame_line, current_score, elapsed_min, over_price, under_price, desc)
        (228.5, 58, 12.0, 1.91, 1.95, "第1节末 / 得分略低"),
        (228.5, 72, 12.0, 1.85, 2.05, "第1节末 / 高得分节奏"),
        (228.5, 115, 24.0, 1.88, 1.98, "半场 / 平均节奏"),
        (228.5, 152, 36.0, 1.80, 2.10, "第3节末 / 低分节奏已进入"),
        (228.5, 195, 42.0, 1.75, 2.15, "第4节中 / 已接近线"),
    ]

    for line, score, elapsed, op, up, desc in test_cases:
        result = compute_live_total_edge(line, score, elapsed, op, up)
        signal = pick_best_side(result, min_edge=0.05)

        print(f"\n情景: {desc}")
        print(f"  赛前线={line} | 当前={score} | 已过={elapsed:.0f}分钟")
        print(f"  期望剩余={result['expected_remaining_score']} | "
              f"sigma={result['sigma']} | 阈值={result['threshold']}")
        print(f"  模型: Over={result['p_model_over']:.3f} / Under={result['p_model_under']:.3f}")
        print(f"  市场: Over={result['p_mkt_over']:.3f} / Under={result['p_mkt_under']:.3f}")
        print(f"  Edge: Over={result['edge_over']:+.3f} / Under={result['edge_under']:+.3f}")

        if signal:
            stake = kelly_stake(signal["edge"], op if signal["side"] == "over" else up, 1000)
            print(f"  ✅ 信号: {signal['side'].upper()} | edge={signal['edge']:+.3f} | "
                  f"1000U本金建议注={stake:.2f}U")
        else:
            print("  — 无信号（edge < 5%）")

    print("\n" + "=" * 60)
