"""
足球直播进球总数 Poisson 定价模型
=====================================
参考文献: Dixon & Coles (1997) "Modelling Association Football Scores and
Inefficiencies in the Football Betting Market"

核心思路：
  1. 赛前用 Dixon-Coles 先验（home_xG / away_xG 预期进球）
  2. 比赛进行中用 live xG（API-Football 统计近似）动态更新
  3. Bayesian 混合权重随比赛进行从先验向实时倾斜
  4. 用 Poisson CDF 计算剩余时间内 P(总进球 > line)
  5. 加入比赛状态调整：比分悬殊、红牌、最后 15 分钟压迫进攻

用法:
    model = InPlayGoalsModel(pre_xg_home=1.5, pre_xg_away=1.2)
    result = model.over_under_prob(
        goals_home=1, goals_away=0,
        live_xg_home=1.2, live_xg_away=0.8,
        minute=60, line=2.5,
        game_state={"red_cards_home": 0, "red_cards_away": 1, "minute": 60}
    )
    # result: {"over": 0.264, "under": 0.736, "fair_over": 3.79, ...}
"""

import math
from typing import Dict, Optional

# ── 泊松分布（不依赖 scipy）─────────────────────────────────


def _poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) 其中 X ~ Poisson(lam)"""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_cdf(k: int, lam: float) -> float:
    """P(X ≤ k) 其中 X ~ Poisson(lam)"""
    if k < 0:
        return 0.0
    return sum(_poisson_pmf(i, lam) for i in range(k + 1))


def poisson_over_prob(goals_needed: float, lam: float) -> float:
    """
    P(剩余进球 > goals_needed)

    goals_needed: line - current_total（需要超过的进球数，可带小数如 0.5）
    lam         : 期望剩余进球数

    原理:
        对于半球盘（0.5, 1.5, 2.5 ...）：
          P(X > 0.5) = P(X >= 1) = 1 - CDF(0)
          P(X > 1.5) = P(X >= 2) = 1 - CDF(1)
          P(X > 2.5) = P(X >= 3) = 1 - CDF(2)
        通用公式: 1 - CDF(ceil(goals_needed) - 1)
    """
    if goals_needed <= 0:
        return 1.0
    # ceil(goals_needed) 得到"至少需要几球"，减 1 得 CDF 上界
    k_threshold = math.ceil(goals_needed) - 1
    return 1.0 - poisson_cdf(k_threshold, lam)


# ── Dixon-Coles ρ 修正（低分比赛）────────────────────────────

_DC_RHO = -0.13   # 典型校正参数，取值约 -0.1 ~ -0.2


def _dc_tau(home_goals: int, away_goals: int, lam_h: float, lam_a: float, rho: float) -> float:
    """
    Dixon-Coles τ 修正因子（仅对 0-0, 1-0, 0-1, 1-1 有效）

    对其他比分 τ=1（不修正）。
    """
    if home_goals == 0 and away_goals == 0:
        return 1.0 - lam_h * lam_a * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + lam_a * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + lam_h * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


# ── 比赛状态调整系数 ─────────────────────────────────────────

def _game_state_adjustment(
    score_diff: int,         # home - away (正值=主队领先)
    minute: int,
    red_cards_home: int = 0,
    red_cards_away: int = 0,
) -> tuple:
    """
    根据比赛状态调整主客队剩余 λ 的乘数

    返回:
        (home_multiplier, away_multiplier)

    调整规则（源自论文 + 实证）:
      1. 红牌: -25% 进攻效率（有红牌一方）
      2. 比分悬殊(|diff| ≥ 2): 领先队 -12% 攻势（收缩打法）
      3. 最后 15 分钟 + 落后: 落后队 +18% 进攻强度（压迫进攻）
    """
    home_mult = away_mult = 1.0

    # 1. 红牌惩罚
    if red_cards_home > 0:
        home_mult *= max(0.5, 1.0 - 0.25 * red_cards_home)
    if red_cards_away > 0:
        away_mult *= max(0.5, 1.0 - 0.25 * red_cards_away)

    # 2. 大比分领先方收缩 (|diff| >= 2)
    if abs(score_diff) >= 2:
        if score_diff > 0:
            home_mult *= 0.88       # 主队领先，减少攻势
        else:
            away_mult *= 0.88       # 客队领先

    # 3. 最后 15 分钟压迫进攻（落后方）
    if minute >= 75:
        if score_diff < 0:          # 主队落后
            home_mult *= 1.18
        elif score_diff > 0:        # 客队落后
            away_mult *= 1.18

    return home_mult, away_mult


# ── 核心模型 ──────────────────────────────────────────────────

class InPlayGoalsModel:
    """
    足球直播进球总数动态定价模型

    参数:
        pre_xg_home: 赛前主队预期进球（Dixon-Coles 或其他先验）
        pre_xg_away: 赛前客队预期进球
        live_weight: 最大 live xG 权重（0-1；60分钟时接近此值）
        dc_rho     : Dixon-Coles ρ 修正参数（低分比赛修正）
    """

    def __init__(
        self,
        pre_xg_home: float,
        pre_xg_away: float,
        live_weight: float = 0.65,
        dc_rho: float = _DC_RHO,
    ):
        if pre_xg_home <= 0 or pre_xg_away <= 0:
            raise ValueError("预期进球必须 > 0")
        self.pre_home = pre_xg_home
        self.pre_away = pre_xg_away
        self.live_weight = live_weight
        self.dc_rho = dc_rho

    def project_remaining(
        self,
        live_xg_home: float,
        live_xg_away: float,
        minute: int,
        extra_time: int = 0,
    ) -> tuple:
        """
        贝叶斯混合：预测剩余时间内主/客队期望进球

        参数:
            live_xg_home: 截至当前分钟的累计 xG（主队）
            live_xg_away: 截至当前分钟的累计 xG（客队）
            minute      : 当前比赛分钟（1-90，加时可超过 90）
            extra_time  : 补时分钟数

        返回:
            (rem_home_xg, rem_away_xg)：剩余时间内期望进球
        """
        total_minutes = 90 + extra_time
        elapsed_frac = max(minute, 1) / total_minutes
        remaining_frac = 1.0 - elapsed_frac

        if remaining_frac <= 0:
            return 0.0, 0.0

        # 先验：剩余时间按比例缩减
        pre_rem_h = self.pre_home * remaining_frac
        pre_rem_a = self.pre_away * remaining_frac

        # 实时推断：用当前 xG 速率外推剩余时间
        if elapsed_frac > 0:
            live_rate_h = live_xg_home / elapsed_frac   # 全场等效 xG
            live_rate_a = live_xg_away / elapsed_frac
        else:
            live_rate_h = self.pre_home
            live_rate_a = self.pre_away

        live_rem_h = live_rate_h * remaining_frac
        live_rem_a = live_rate_a * remaining_frac

        # 动态权重：比赛越靠后越相信实时数据
        # 前 10 分钟：几乎全用先验（样本太少）
        if minute < 10:
            w = 0.08
        else:
            w = self.live_weight * elapsed_frac + (1 - self.live_weight) * 0.25

        w = max(0.0, min(w, 0.95))   # 限制在 [0, 0.95]

        rem_h = (1 - w) * pre_rem_h + w * live_rem_h
        rem_a = (1 - w) * pre_rem_a + w * live_rem_a

        return max(rem_h, 0.001), max(rem_a, 0.001)

    def over_under_prob(
        self,
        goals_home: int,
        goals_away: int,
        live_xg_home: float,
        live_xg_away: float,
        minute: int,
        line: float = 2.5,
        game_state: Optional[Dict] = None,
        extra_time: int = 0,
    ) -> Dict:
        """
        计算 Over/Under 概率

        参数:
            goals_home    : 当前主队进球
            goals_away    : 当前客队进球
            live_xg_home  : 当前主队累计 xG
            live_xg_away  : 当前客队累计 xG
            minute        : 当前分钟
            line          : 总进球线 (e.g. 2.5)
            game_state    : 可选状态调整
                {
                  "red_cards_home": int,
                  "red_cards_away": int,
                }
            extra_time    : 补时（分钟）

        返回 dict:
            over          : P(over)
            under         : P(under)
            fair_over     : 公平赔率（over）
            fair_under    : 公平赔率（under）
            remaining_lambda: 剩余期望进球
            rem_home_lambda : 主队剩余期望
            rem_away_lambda : 客队剩余期望
            goals_needed  : 还需多少进球才 over
            blend_weight  : 当前 live 权重
        """
        current_total = goals_home + goals_away
        goals_needed = line - current_total

        # 已确定结果
        if goals_needed < 0:
            return {
                "over": 1.0, "under": 0.0,
                "fair_over": 1.0, "fair_under": 99.0,
                "remaining_lambda": 0.0, "rem_home_lambda": 0.0,
                "rem_away_lambda": 0.0, "goals_needed": goals_needed,
                "blend_weight": 1.0,
            }
        if goals_needed > 0 and minute >= (90 + extra_time):
            return {
                "over": 0.0, "under": 1.0,
                "fair_over": 99.0, "fair_under": 1.0,
                "remaining_lambda": 0.0, "rem_home_lambda": 0.0,
                "rem_away_lambda": 0.0, "goals_needed": goals_needed,
                "blend_weight": 1.0,
            }

        rem_h, rem_a = self.project_remaining(live_xg_home, live_xg_away, minute, extra_time)

        # 比赛状态调整
        red_home = red_away = 0
        if game_state:
            red_home = game_state.get("red_cards_home", 0)
            red_away = game_state.get("red_cards_away", 0)

        score_diff = goals_home - goals_away
        h_mult, a_mult = _game_state_adjustment(score_diff, minute, red_home, red_away)

        rem_h = max(rem_h * h_mult, 0.0)  # guard against negative after mult
        rem_a = max(rem_a * a_mult, 0.0)
        lam = rem_h + rem_a   # 总剩余期望进球

        # Poisson 计算
        p_over = poisson_over_prob(goals_needed, lam)
        p_under = 1.0 - p_over

        # 公平赔率（去水前）；极端情况限制赔率最大 999
        fair_over = round(1.0 / max(p_over, 0.001), 3)
        fair_under = round(1.0 / max(p_under, 0.001), 3)

        # blend_weight 仅用于日志透明度，单点计算避免重复
        total_mins = max(90 + extra_time, 1)
        elapsed_frac = max(minute, 1) / total_mins
        if minute < 10:
            blend_w = 0.08
        else:
            blend_w = min(self.live_weight * elapsed_frac + (1 - self.live_weight) * 0.25, 0.95)

        return {
            "over": round(p_over, 4),
            "under": round(p_under, 4),
            "fair_over": fair_over,
            "fair_under": fair_under,
            "remaining_lambda": round(lam, 3),
            "rem_home_lambda": round(rem_h, 3),
            "rem_away_lambda": round(rem_a, 3),
            "goals_needed": round(goals_needed, 1),
            "blend_weight": round(blend_w, 3),
        }

    def compute_edge(
        self,
        market: Dict,           # extract_total_goals_market 的返回值
        goals_home: int,
        goals_away: int,
        live_xg_home: float,
        live_xg_away: float,
        minute: int,
        game_state: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        计算赔率 edge，返回最优下注方向

        参数:
            market  : CloudbetClient.extract_total_goals_market() 的返回值
                      需含: line, over_price, under_price
            其余参数: 同 over_under_prob()

        返回:
            {side, line, edge, model_prob, mkt_prob, market_price,
             market_url, fair_price, model_result}
            或 None（无 edge）
        """
        line = market.get("line")
        over_price = market.get("over_price", 0)
        under_price = market.get("under_price", 0)

        if not line or over_price <= 1.0 or under_price <= 1.0:
            return None

        model = self.over_under_prob(
            goals_home, goals_away, live_xg_home, live_xg_away, minute, line, game_state
        )

        # 市场隐含概率（双边去水）
        p_raw_over = 1.0 / over_price
        p_raw_under = 1.0 / under_price
        overround = p_raw_over + p_raw_under

        if overround <= 0:
            return None

        p_mkt_over = p_raw_over / overround
        p_mkt_under = p_raw_under / overround

        edge_over = model["over"] - p_mkt_over
        edge_under = model["under"] - p_mkt_under

        # 选最优方向
        if edge_over >= edge_under and edge_over > 0:
            return {
                "side": "over",
                "line": line,
                "edge": round(edge_over, 4),
                "model_prob": round(model["over"], 4),
                "mkt_prob": round(p_mkt_over, 4),
                "market_price": over_price,
                "market_url": market.get("over_url", f"soccer.total_goals/over?total={line}"),
                "fair_price": model["fair_over"],
                "max_stake": market.get("over_max_stake", 9999),
                "min_stake": market.get("over_min_stake", 1),
                "model_result": model,
            }
        elif edge_under > edge_over and edge_under > 0:
            return {
                "side": "under",
                "line": line,
                "edge": round(edge_under, 4),
                "model_prob": round(model["under"], 4),
                "mkt_prob": round(p_mkt_under, 4),
                "market_price": under_price,
                "market_url": market.get("under_url", f"soccer.total_goals/under?total={line}"),
                "fair_price": model["fair_under"],
                "max_stake": market.get("under_max_stake", 9999),
                "min_stake": market.get("under_min_stake", 1),
                "model_result": model,
            }
        return None


# ── Kelly 仓位 ────────────────────────────────────────────────

def kelly_stake(
    edge: float,
    model_prob: float,
    odds: float,
    bankroll: float,
    fraction: float = 0.25,
    max_pct: float = 0.005,
    min_stake: float = 1.0,
) -> float:
    """
    分数 Kelly 仓位计算

    Kelly 公式: f* = (p·b - q) / b
        p = model_prob（模型胜率）
        q = 1 - p
        b = odds - 1（净赔率）
    """
    if odds <= 1.0 or model_prob <= 0 or model_prob >= 1.0:
        return 0.0

    b = odds - 1.0
    q = 1.0 - model_prob
    full_kelly = (model_prob * b - q) / b

    if full_kelly <= 0:
        return 0.0

    stake = fraction * full_kelly * bankroll
    stake = min(stake, bankroll * max_pct)     # 硬上限（0.5%）
    # 满足平台最小注额，但不超过硬上限（避免小资金时 min_stake 突破 max_pct）
    hard_cap = bankroll * max_pct
    if min_stake <= hard_cap:
        stake = max(stake, min_stake)
    return round(stake, 2)


# ── 独立测试 ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 65)
    print("足球直播进球模型 — 单元测试")
    print("=" * 65)

    model = InPlayGoalsModel(pre_xg_home=1.5, pre_xg_away=1.2)

    cases = [
        # (goals_h, goals_a, live_xg_h, live_xg_a, minute, line, desc)
        (0, 0, 0.3, 0.2, 20, 2.5, "上半场早段，0-0"),
        (1, 0, 1.2, 0.8, 60, 2.5, "60分钟，1-0，较高xG"),
        (1, 0, 0.5, 0.3, 60, 2.5, "60分钟，1-0，低xG节奏"),
        (2, 0, 1.8, 0.4, 75, 2.5, "75分钟，2-0，主队大胜"),
        (1, 1, 1.1, 1.0, 70, 2.5, "70分钟，1-1，平局压迫"),
    ]

    for gh, ga, lh, la, min_, line, desc in cases:
        result = model.over_under_prob(gh, ga, lh, la, min_, line,
                                       game_state={"red_cards_home": 0, "red_cards_away": 0})
        print(f"\n{desc}")
        print(f"  比分={gh}-{ga} | xG={lh:.1f}/{la:.1f} | {min_}分 | 线={line}")
        print(f"  剩余λ={result['remaining_lambda']:.3f} "
              f"(H={result['rem_home_lambda']:.3f}/A={result['rem_away_lambda']:.3f})")
        print(f"  P(Over)={result['over']:.4f} | P(Under)={result['under']:.4f}")
        print(f"  公平赔率: Over={result['fair_over']} / Under={result['fair_under']}")

    # 红牌测试
    print("\n\n── 红牌影响 ─────────────────────")
    no_rc = model.over_under_prob(0, 0, 0.6, 0.4, 30, 2.5)
    with_rc = model.over_under_prob(0, 0, 0.6, 0.4, 30, 2.5,
                                    game_state={"red_cards_home": 1, "red_cards_away": 0})
    print(f"无红牌:   P(Over)={no_rc['over']:.4f} | λ={no_rc['remaining_lambda']:.3f}")
    print(f"主队红牌: P(Over)={with_rc['over']:.4f} | λ={with_rc['remaining_lambda']:.3f}")

    print("\n" + "=" * 65)
