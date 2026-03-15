"""
xG 数据客户端 — API-Football 集成
=====================================
数据源层级（从免费到付费）:

  【免费】API-Football v3 (api-sports.io)
    - 免费额度: 100 请求/天
    - 提供: 射门、射正、控球、危险进攻 (每 15 秒更新)
    - 无原生 xG → 用射门统计近似估算
    - 端点: GET v3.football.api-sports.io/fixtures/statistics?fixture={id}

  【免费，仅赛后】Understat / StatsBomb
    - 用于训练更好的 xG 模型（离线）
    - 不适合直播

  【付费】Sportmonks
    - ~€50/月 + Advanced xG 插件
    - 真实 live xG，每 2-5 分钟更新

xG 近似算法（API-Football 免费层）:
    xG ≈ shots_on_target × 0.30 + (shots_total - shots_on_target) × 0.05

    这是保守估算，实际准确度受限于射门位置未知。
    建议用于"方向性判断"（高/低 xG 节奏），而非精确概率。
"""

import logging
import os
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# API-Football 基础 URL
_AF_BASE = "https://v3.football.api-sports.io"

# xG 近似系数（基于 StatsBomb 公开数据的均值）
_XG_PER_SHOT_ON_TARGET = 0.30
_XG_PER_SHOT_BLOCKED = 0.05     # 射门但未射正
_XG_MIN_FLOOR = 0.001           # 防止除零

# 本地缓存：{fixture_id: (timestamp, stats_dict)}
_stats_cache: Dict[str, Tuple[float, dict]] = {}
_CACHE_TTL_SECS = 15            # 15 秒缓存（API 更新频率约 15 秒）

# Betfair odds 作为市场隐含概率基准（可选）
_BETFAIR_BASE = "https://api.betfair.com/exchange/betting/json/rpc/v1"


class APIFootballClient:
    """
    API-Football (api-sports.io) 封装

    用于获取直播赛事统计数据并推算 xG。

    参数:
        api_key: api-sports.io 的 API Key（免费注册获取）
                 https://dashboard.api-football.com/
    """

    def __init__(self, api_key: str, provider: str = "direct"):
        """
        参数:
            api_key  : API-Football key
            provider : "direct"   → x-apisports-key（来自 dashboard.api-football.com）
                       "rapidapi" → x-rapidapi-key（来自 RapidAPI 市场）
        """
        self.api_key = api_key
        self.session = requests.Session()
        if provider == "rapidapi":
            self.session.headers.update({
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": "v3.football.api-sports.io",
            })
        else:  # direct（默认）
            self.session.headers.update({
                "x-apisports-key": api_key,
            })
        self._request_count = 0
        self._request_day = datetime.utcnow().date()

    def _check_daily_limit(self, limit: int = 90) -> bool:
        """检查今日请求数是否接近上限（免费层 100/天，保留 10 个缓冲）"""
        today = datetime.utcnow().date()
        if today != self._request_day:
            self._request_count = 0
            self._request_day = today
        if self._request_count >= limit:
            logger.warning("API-Football 请求数已达上限 %d，今日不再请求", limit)
            return False
        return True

    def get_live_fixtures(self, league_id: int = None) -> list:
        """
        获取当前直播中的比赛列表

        参数:
            league_id: 过滤联赛 ID（可选）
                常用: 英超=39, 西甲=140, 德甲=78, 意甲=135, 法甲=61,
                      欧冠=2, NBA=12 (basketball)
        """
        if not self._check_daily_limit():
            return []

        params = {"live": "all"}
        if league_id:
            params["league"] = league_id

        try:
            resp = self.session.get(f"{_AF_BASE}/fixtures", params=params, timeout=10)
            self._request_count += 1
            if resp.status_code == 200:
                return resp.json().get("response", [])
            logger.warning("live fixtures: %d", resp.status_code)
        except requests.RequestException as exc:
            logger.error("get_live_fixtures: %s", exc)
        return []

    def get_fixture_statistics(self, fixture_id: int) -> Optional[dict]:
        """
        获取单场比赛的实时统计数据（含射门、射正、控球等）

        每次调用消耗 1 个每日配额，建议每 15 秒调用一次。

        返回:
            {
              "home": {shots_total, shots_on_target, xg_approx, ...},
              "away": {shots_total, shots_on_target, xg_approx, ...},
              "elapsed": int (分钟)
            }
        或 None
        """
        fixture_key = str(fixture_id)

        # 命中缓存
        cached = _stats_cache.get(fixture_key)
        if cached:
            ts, data = cached
            if time.time() - ts < _CACHE_TTL_SECS:
                return data

        if not self._check_daily_limit():
            return None

        try:
            resp = self.session.get(
                f"{_AF_BASE}/fixtures/statistics",
                params={"fixture": fixture_id},
                timeout=10,
            )
            self._request_count += 1
            if resp.status_code != 200:
                logger.debug("stats %d: %d", fixture_id, resp.status_code)
                return None

            raw = resp.json().get("response", [])
            parsed = self._parse_statistics(raw)
            if parsed:
                _stats_cache[fixture_key] = (time.time(), parsed)
            return parsed

        except requests.RequestException as exc:
            logger.error("get_fixture_statistics(%d): %s", fixture_id, exc)
            return None

    def _parse_statistics(self, raw: list) -> Optional[dict]:
        """将 API 原始统计格式转换为 {home: {...}, away: {...}}"""
        if not raw or len(raw) < 2:
            return None

        result = {}
        for idx, team_data in enumerate(raw):
            team_name = team_data.get("team", {}).get("name", "unknown")
            # API-Football 始终按 [主队, 客队] 顺序返回，无 type 字段
            side = "home" if idx == 0 else "away"

            stats_list = team_data.get("statistics", [])
            stats_map = {
                s.get("type", ""): s.get("value")
                for s in stats_list
                if s.get("value") is not None
            }

            # 解析射门数据
            shots_total = self._safe_int(stats_map.get("Total Shots"))
            shots_on = self._safe_int(stats_map.get("Shots on Goal"))
            shots_off = max(0, shots_total - shots_on)
            dangerous_attacks = self._safe_int(stats_map.get("Dangerous Attacks"))
            possession = self._safe_float(
                str(stats_map.get("Ball Possession", "50%")).replace("%", "")
            )

            # xG 近似：射正 × 0.30 + 射偏/封堵 × 0.05
            xg_approx = (shots_on * _XG_PER_SHOT_ON_TARGET +
                         shots_off * _XG_PER_SHOT_BLOCKED)
            xg_approx = max(xg_approx, _XG_MIN_FLOOR)

            result[side] = {
                "team": team_name,
                "shots_total": shots_total,
                "shots_on_target": shots_on,
                "shots_off_target": shots_off,
                "dangerous_attacks": dangerous_attacks,
                "possession": possession,
                "xg_approx": round(xg_approx, 3),
            }

        return result if len(result) == 2 else None

    @staticmethod
    def _safe_int(val) -> int:
        try:
            return int(val) if val is not None else 0
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val) if val is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    def get_events_info(self, fixture_id: int) -> Optional[dict]:
        """
        获取比赛事件（进球、红牌、换人等）

        返回:
            {goals_home, goals_away, red_cards_home, red_cards_away, elapsed}
        """
        if not self._check_daily_limit():
            return None
        try:
            resp = self.session.get(
                f"{_AF_BASE}/fixtures/events",
                params={"fixture": fixture_id},
                timeout=10,
            )
            self._request_count += 1
            if resp.status_code != 200:
                return None

            events = resp.json().get("response", [])
            return self._parse_events(events)

        except requests.RequestException as exc:
            logger.error("get_events_info(%d): %s", fixture_id, exc)
            return None

    @staticmethod
    def _parse_events(events: list) -> dict:
        info = {
            "goals_home": 0, "goals_away": 0,
            "red_cards_home": 0, "red_cards_away": 0,
            "elapsed": 0,
        }
        for ev in events:
            elapsed = ev.get("time", {}).get("elapsed", 0) or 0
            info["elapsed"] = max(info["elapsed"], elapsed)
            ev_type = ev.get("type", "")
            detail = ev.get("detail", "")
            team_type = ev.get("team", {}).get("type", "")

            if ev_type == "Goal":
                if team_type == "home":
                    info["goals_home"] += 1
                else:
                    info["goals_away"] += 1

            elif ev_type == "Card" and "Red" in detail:
                if team_type == "home":
                    info["red_cards_home"] += 1
                else:
                    info["red_cards_away"] += 1

        return info

    def get_today_request_count(self) -> int:
        return self._request_count


class NullXGClient:
    """
    无 xG 数据时的回退方案

    当没有 API-Football key 时，使用基于赛前先验 + 比分推断的保守估算。
    """

    def get_fixture_statistics(self, fixture_id: int) -> Optional[dict]:
        return None

    def get_events_info(self, fixture_id: int) -> Optional[dict]:
        return None


def create_xg_client(api_key: str = None, provider: str = None) -> "APIFootballClient | NullXGClient":
    """
    工厂函数：有 key 则用真实客户端，否则用 NullXGClient

    参数优先级:
      1. 传入参数 api_key
      2. 环境变量 API_FOOTBALL_KEY
      3. 兼容旧变量 APIFOOTBALL_KEY
    """
    key = (
        api_key
        or os.environ.get("API_FOOTBALL_KEY", "")
        or os.environ.get("APIFOOTBALL_KEY", "")
    )
    if key:
        logger.info("使用 API-Football 真实 xG 数据 (provider=%s)", prov)
        return APIFootballClient(key, provider=prov)
    logger.warning("未配置 API_FOOTBALL_KEY，使用先验估算（xG 近似精度较低）")
    return NullXGClient()


def estimate_xg_from_score_and_time(
    goals_home: int, goals_away: int, minute: int,
    pre_xg_home: float, pre_xg_away: float
) -> Tuple[float, float]:
    """
    无实时 xG 数据时的回退估算

    逻辑：若进球>先验，说明节奏偏高；反之偏低。
    用实际进球数 + 泊松逆推估算 xG（非常粗糙，仅作兜底）

    返回:
        (estimated_xg_home, estimated_xg_away)
    """
    if minute < 1:
        return pre_xg_home, pre_xg_away

    elapsed_frac = min(minute / 90.0, 1.0)

    # 先验期望（按时间比例）
    expected_h = pre_xg_home * elapsed_frac
    expected_a = pre_xg_away * elapsed_frac

    # 观测值（进球数）
    # 对 xG 进行贝叶斯混合：实际进球 0.5 + 先验比例 0.5
    est_h = 0.5 * goals_home + 0.5 * expected_h
    est_a = 0.5 * goals_away + 0.5 * expected_a

    return max(est_h, _XG_MIN_FLOOR), max(est_a, _XG_MIN_FLOOR)


# ── 独立测试 ──────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 测试回退估算
    print("── xG 回退估算 ─────────────────────")
    cases = [
        (1, 0, 60, 1.5, 1.2),
        (0, 0, 30, 1.5, 1.2),
        (3, 1, 70, 1.5, 1.2),
    ]
    for gh, ga, min_, ph, pa in cases:
        eh, ea = estimate_xg_from_score_and_time(gh, ga, min_, ph, pa)
        print(f"  比分={gh}-{ga} | {min_}分 | est_xG={eh:.3f}/{ea:.3f}")

    # 如果有 key，测试真实 API
    key = os.environ.get("API_FOOTBALL_KEY", "") or os.environ.get("APIFOOTBALL_KEY", "")
    if key:
        client = APIFootballClient(key)
        print("\n── API-Football 直播赛事 ────────")
        fixtures = client.get_live_fixtures()
        print(f"  当前直播: {len(fixtures)} 场")
        if fixtures:
            fid = fixtures[0]["fixture"]["id"]
            stats = client.get_fixture_statistics(fid)
            print(f"  统计数据({fid}): {stats}")
    else:
        print("\n未设置 API_FOOTBALL_KEY，跳过真实 API 测试")
        print("设置方式: export API_FOOTBALL_KEY=your_key (或旧变量 APIFOOTBALL_KEY)")
