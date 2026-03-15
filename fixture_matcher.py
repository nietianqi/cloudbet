"""
Cloudbet 事件 ↔ API-Football fixture_id 匹配层
================================================
职责：
  将 Cloudbet Feed 里的 home/away 队名，模糊匹配到
  API-Football 当日直播赛程的 fixture_id，然后注入
  event["_af_fixture_id"]，使 xg_client 能真正取数。

匹配算法（三级降级）：
  1. 精确匹配（忽略大小写）
  2. 包含匹配（一方队名是另一方子串）
  3. Token 交集比（去掉 FC/CF/SC/城市后缀后 Jaccard 相似度 ≥ 0.5）

缓存策略：
  - 每 REFRESH_INTERVAL 秒刷新一次 API-Football 直播赛程（默认 60 秒）
  - 同一赛季可跨轮询复用，避免消耗每日 100 次免费额度

使用：
    from fixture_matcher import FixtureMatcher
    matcher = FixtureMatcher(xg_client)

    # 批量注入 — 修改 events 列表（in-place）
    matched, total = matcher.inject_fixture_ids(events)
    logger.info("fixture 命中 ext:%d/%d", matched, total)
"""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 刷新间隔（秒）— 避免频繁消耗 API 配额
REFRESH_INTERVAL = 60

# 最小 token 交集 Jaccard 相似度（第三级匹配阈值）
_MIN_JACCARD = 0.50

# 需要忽略的通用词（队名清洗时去除）
_NOISE_TOKENS = {
    "fc", "cf", "sc", "ac", "rc", "bc", "fk", "sk", "bk", "nk",
    "united", "city", "town", "county", "athletic", "atletico",
    "real", "royal", "club", "sport", "sporting", "de", "the",
    "1", "2", "a", "b",
}


def _normalize(name: str) -> str:
    """小写、umlaut 转写、去标点、去噪声词，返回清洗后的字符串"""
    name = name.lower()
    # 德语/西班牙语常见变音符转写，保留 token 可比性
    for src, dst in [("ü", "u"), ("ö", "o"), ("ä", "a"), ("ß", "ss"),
                     ("é", "e"), ("è", "e"), ("ê", "e"), ("ñ", "n"),
                     ("ó", "o"), ("á", "a"), ("í", "i"), ("ú", "u")]:
        name = name.replace(src, dst)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    tokens = [t for t in name.split() if t not in _NOISE_TOKENS]
    return " ".join(tokens)


def _soft_token_match(t1: str, t2: str, prefix_len: int = 5) -> bool:
    """
    两个 token 的软匹配：精确相等，或共享 prefix_len 长度前缀
    用于处理 'munich' vs 'munchen'、'dortmund' vs 'dortmund' 等变体
    """
    if t1 == t2:
        return True
    if len(t1) >= prefix_len and len(t2) >= prefix_len:
        return t1[:prefix_len] == t2[:prefix_len]
    return False


def _jaccard(a: str, b: str) -> float:
    """
    两个字符串的软 token-level Jaccard 相似度
    使用 _soft_token_match 替代精确集合交集，处理跨语言变体
    """
    sa = a.split()
    sb = b.split()
    if not sa or not sb:
        return 0.0

    # 贪心匹配：每个 sa 中的 token 找 sb 中最佳匹配
    matched = 0
    used_b = set()
    for ta in sa:
        for j, tb in enumerate(sb):
            if j not in used_b and _soft_token_match(ta, tb):
                matched += 1
                used_b.add(j)
                break

    union = len(set(sa) | set(sb))
    # 用软匹配数 / union 作为分数
    return matched / max(len(sa), len(sb))


def _match_score(cb_name: str, af_name: str) -> float:
    """
    返回 [0, 1] 匹配分（越高越好）
      1.0  — 精确匹配
      0.8  — 包含匹配
      Jaccard — token 交集
      0.0  — 不匹配
    """
    a = _normalize(cb_name)
    b = _normalize(af_name)

    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.8
    j = _jaccard(a, b)
    return j if j >= _MIN_JACCARD else 0.0


class FixtureMatcher:
    """
    维护一份 API-Football 直播赛程快照，为 Cloudbet 事件注入 fixture_id。

    参数:
        xg_client : APIFootballClient 实例（有 key 时）或 NullXGClient（无 key 时）
        league_ids: 要拉取的联赛 ID 列表（None = 拉全量直播，消耗更多配额）
                    常用: 英超=39, 西甲=140, 德甲=78, 意甲=135, 法甲=61,
                          欧冠=2, 欧联=3, 荷甲=88, 葡超=94
    """

    def __init__(self, xg_client, league_ids: Optional[List[int]] = None):
        self._client = xg_client
        self._league_ids = league_ids or [39, 140, 78, 135, 61, 2, 3, 88, 94]
        self._cache: List[Dict] = []          # [{fixture_id, home, away, league_id}, ...]
        self._last_refresh: float = 0.0
        self._refresh_count: int = 0
        self._hit_count: int = 0
        self._miss_count: int = 0

    # ── 内部：拉取 & 刷新 ────────────────────────────────────────

    def _refresh_if_needed(self) -> None:
        """距上次刷新超过 REFRESH_INTERVAL 秒则重新拉取"""
        now = time.time()
        if now - self._last_refresh < REFRESH_INTERVAL:
            return

        # NullXGClient 没有 get_live_fixtures，直接跳过
        if not hasattr(self._client, "get_live_fixtures"):
            return

        new_cache: List[Dict] = []
        for lid in self._league_ids:
            try:
                fixtures = self._client.get_live_fixtures(league_id=lid)
                for fx in fixtures:
                    fid = fx.get("fixture", {}).get("id")
                    teams = fx.get("teams", {})
                    home = teams.get("home", {}).get("name", "")
                    away = teams.get("away", {}).get("name", "")
                    if fid and home and away:
                        new_cache.append({
                            "fixture_id": fid,
                            "home": home,
                            "away": away,
                            "league_id": lid,
                        })
            except Exception as exc:
                logger.debug("拉取联赛 %d 直播赛程失败: %s", lid, exc)

        self._cache = new_cache
        self._last_refresh = now
        self._refresh_count += 1
        logger.debug(
            "fixture 缓存刷新 #%d: %d 场直播（%d 联赛）",
            self._refresh_count, len(self._cache), len(self._league_ids),
        )

    # ── 内部：单事件匹配 ─────────────────────────────────────────

    def _find_fixture_id(
        self, cb_home: str, cb_away: str
    ) -> Tuple[Optional[int], float]:
        """
        在缓存中寻找最佳匹配。

        返回:
            (fixture_id, score) — 若无匹配则 (None, 0.0)
        """
        best_id: Optional[int] = None
        best_score: float = 0.0

        for entry in self._cache:
            # 主客队必须同向匹配（不交叉）
            score_h = _match_score(cb_home, entry["home"])
            score_a = _match_score(cb_away, entry["away"])
            combined = (score_h + score_a) / 2.0

            if combined > best_score:
                best_score = combined
                best_id = entry["fixture_id"]

        # 两边都要有一定相似度才算命中（防止只有一边凑巧相似）
        if best_score < _MIN_JACCARD:
            return None, 0.0

        return best_id, best_score

    # ── 公开接口 ─────────────────────────────────────────────────

    def inject_fixture_ids(self, events: List[Dict]) -> Tuple[int, int]:
        """
        批量为 Cloudbet 事件列表注入 _af_fixture_id（in-place 修改）。

        返回:
            (matched_count, total_count)
        """
        self._refresh_if_needed()

        if not self._cache:
            logger.debug("fixture 缓存为空，跳过注入")
            return 0, len(events)

        matched = 0
        for event in events:
            # 已注入过则跳过（避免重复覆盖）
            if event.get("_af_fixture_id"):
                matched += 1
                continue

            home = event.get("home", {}).get("name", "") if isinstance(event.get("home"), dict) \
                   else str(event.get("home", ""))
            away = event.get("away", {}).get("name", "") if isinstance(event.get("away"), dict) \
                   else str(event.get("away", ""))

            if not home or not away:
                continue

            fid, score = self._find_fixture_id(home, away)
            if fid:
                event["_af_fixture_id"] = fid
                matched += 1
                self._hit_count += 1
                logger.debug(
                    "fixture 命中: %s vs %s → id=%d (score=%.2f)",
                    home, away, fid, score,
                )
            else:
                self._miss_count += 1
                logger.debug("fixture 未命中: %s vs %s", home, away)

        return matched, len(events)

    def get_stats(self) -> Dict:
        """返回累计命中/未命中统计"""
        return {
            "refresh_count": self._refresh_count,
            "cache_size": len(self._cache),
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "hit_rate": (
                self._hit_count / (self._hit_count + self._miss_count)
                if (self._hit_count + self._miss_count) > 0 else 0.0
            ),
        }


# ── 独立测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    print("── 队名匹配单元测试 ─────────────────────")
    cases = [
        # (cb_home, af_home, 预期分 ≥ 0.5?)
        ("Manchester City", "Manchester City",    True),
        ("Man City",        "Manchester City",    True),
        ("Real Madrid",     "Real Madrid CF",     True),
        ("Bayern Munich",   "FC Bayern München",  True),  # 跨语言，可能 miss
        ("Liverpool",       "Liverpool FC",       True),
        ("Arsenal",         "Arsenal FC",         True),
        ("Chelsea",         "Chelmsford City",    False), # 应不命中
        ("Juventus",        "Juventus FC",        True),
    ]
    for cb, af, expected in cases:
        score = _match_score(cb, af)
        hit = score >= _MIN_JACCARD
        status = "✅" if hit == expected else "❌"
        print(f"  {status} '{cb}' vs '{af}' → score={score:.2f} hit={hit} (expected={expected})")

    print("\n── inject_fixture_ids 接口测试（模拟缓存）────")

    class _MockClient:
        def get_live_fixtures(self, league_id=None):
            return [
                {"fixture": {"id": 1001}, "teams": {
                    "home": {"name": "Arsenal FC"}, "away": {"name": "Chelsea FC"}}},
                {"fixture": {"id": 1002}, "teams": {
                    "home": {"name": "Real Madrid CF"}, "away": {"name": "FC Barcelona"}}},
            ]

    matcher = FixtureMatcher(_MockClient(), league_ids=[39])
    fake_events = [
        {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}},
        {"home": {"name": "Real Madrid"}, "away": {"name": "Barcelona"}},
        {"home": {"name": "Fake FC"}, "away": {"name": "Nobody SC"}},
    ]
    n_matched, n_total = matcher.inject_fixture_ids(fake_events)
    print(f"  命中 {n_matched}/{n_total}")
    for ev in fake_events:
        home = ev["home"]["name"]
        fid = ev.get("_af_fixture_id", "MISS")
        print(f"  {home}: fixture_id={fid}")

    print(f"\n  统计: {matcher.get_stats()}")
