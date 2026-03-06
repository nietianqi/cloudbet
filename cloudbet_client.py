"""
Cloudbet API 客户端 — 完整 REST 封装
======================================
覆盖官方三个模块：
  Feed API v2   — /pub/v2/odds/*  (赔率/赛事数据)
  Trading API v3 — /pub/v3/bets/* (下注/状态查询)
  Account API v1 — /pub/v1/account/* (账户/余额)

关键运营约束（务必遵守）：
  - 下注频率: 硬限制 1 次/秒
  - 拒单率: 最近 100 笔若 >75% 被拒 → 账户可能被封 7 天
  - Feed 轮询: 建议 3-5 秒间隔（使用 Trading key 获取实时数据）
  - Affiliate key 的 Feed 最多缓存 1 分钟，不适合直播

使用方式:
    client = CloudbetClient(api_key="your_jwt_token")
    live = client.get_live_events("soccer-england-premier-league",
                                  markets=["soccer.total_goals", "soccer.match_odds"])
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from collections import Counter
from typing import Dict, List, Optional
from urllib.parse import urlencode, parse_qs

import requests

logger = logging.getLogger(__name__)

BASE = "https://sports-api.cloudbet.com"

_SETTLEMENT_TIMEOUT = 300
_SETTLEMENT_INTERVAL = 5

# 下单速率限制：1 次/秒（全局，非线程安全，单线程使用）
_LAST_BET_TIME: float = 0.0

# 已知拒单错误代码及建议动作
REJECTION_ACTIONS: Dict[str, str] = {
    "PRICE_CHANGED": "retry_fresh_price",
    "STAKE_ABOVE_MAX": "use_corrected_stake",
    "STAKE_BELOW_MIN": "use_corrected_stake",
    "LIABILITY_LIMIT_EXCEEDED": "reduce_stake",
    "MARKET_SUSPENDED": "wait_retry",
    "MARKET_NOT_FOUND": "refetch_event",
    "INSUFFICIENT_BALANCE": "stop",
    "RESTRICTED": "stop",
}

# 结算终止状态集合
TERMINAL_STATUSES = frozenset(
    {"ACCEPTED", "WIN", "LOSS", "VOID", "PARTIAL_WON", "PARTIAL_LOST", "REJECTED"}
)


class CloudbetAPIError(Exception):
    """Cloudbet API 级别的错误"""
    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class CloudbetClient:
    """
    Cloudbet REST API 完整封装

    参数:
        api_key : Trading API Key（JWT Token）
        timeout : 请求超时（秒）
        protobuf: 是否请求 Protobuf 编码（节省带宽，高频轮询推荐）
    """

    def __init__(self, api_key: str, timeout: int = 15, protobuf: bool = False):
        self.api_key = api_key
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-Key": api_key,
                "Accept": "application/x-protobuf" if protobuf else "application/json",
                "Content-Type": "application/json",
            }
        )

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{BASE}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise CloudbetAPIError(0, f"网络请求失败: {exc}") from exc
        if resp.status_code == 401:
            raise CloudbetAPIError(401, "API Key 无效或已过期")
        if resp.status_code == 404:
            raise CloudbetAPIError(404, f"资源不存在: {path}")
        if resp.status_code == 429:
            raise CloudbetAPIError(429, "请求频率超限（Rate limit exceeded）")
        if resp.status_code >= 500:
            raise CloudbetAPIError(resp.status_code, f"服务器错误: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise CloudbetAPIError(resp.status_code, f"JSON 解析失败: {resp.text[:100]}") from exc

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{BASE}{path}"
        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise CloudbetAPIError(0, f"网络请求失败: {exc}") from exc
        if resp.status_code == 401:
            raise CloudbetAPIError(401, "API Key 无效或已过期")
        if resp.status_code == 400:
            raise CloudbetAPIError(400, f"请求格式错误: {resp.text[:300]}")
        if resp.status_code == 404:
            raise CloudbetAPIError(404, f"端点不存在: {path}")
        if resp.status_code >= 500:
            raise CloudbetAPIError(resp.status_code, f"服务器错误: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise CloudbetAPIError(resp.status_code, f"JSON 解析失败: {resp.text[:100]}") from exc

    # ── Feed API v2 ───────────────────────────────────────────

    def get_sports(self) -> dict:
        """列出所有运动及赛事数量"""
        return self._get("/pub/v2/odds/sports")

    def get_competitions(self, sport_key: str = "soccer") -> dict:
        """获取某运动下的所有联赛（按分类分组）"""
        return self._get(f"/pub/v2/odds/sports/{sport_key}")


    @staticmethod
    def extract_competition_keys(sport_payload: dict) -> List[str]:
        """
        Extract competition keys from /pub/v2/odds/sports/{sport} response.

        Supports both shapes:
          1) categories[].competitions[].key
          2) competitions[].key
        """
        keys: List[str] = []

        for category in sport_payload.get("categories", []) or []:
            for comp in category.get("competitions", []) or []:
                key = comp.get("key")
                name = (comp.get("name") or "").lower()
                if key and not name.startswith("virtual"):
                    keys.append(key)

        if not keys:
            for comp in sport_payload.get("competitions", []) or []:
                key = comp.get("key")
                name = (comp.get("name") or "").lower()
                if key and not name.startswith("virtual"):
                    keys.append(key)

        dedup: List[str] = []
        seen = set()
        for k in keys:
            if k in seen:
                continue
            seen.add(k)
            dedup.append(k)
        return dedup

    def get_events(
        self,
        competition_key: str,
        markets: List[str] = None,
        status: str = None,
    ) -> dict:
        """
        获取联赛下的所有赛事 + 赔率

        参数:
            competition_key: e.g. "soccer-england-premier-league"
            markets       : 过滤市场列表，e.g. ["soccer.total_goals", "soccer.match_odds"]
            status        : 过滤状态，e.g. "TRADING_LIVE"

        联赛键格式: soccer-{country}-{league-name}
          常用键:
            soccer-england-premier-league
            soccer-spain-la-liga
            soccer-germany-bundesliga
            soccer-italy-serie-a
            soccer-france-ligue-1
            soccer-uefa-champions-league
            soccer-international-world-cup
        """
        params: dict = {}
        if markets:
            params["markets"] = markets
        if status:
            params["status"] = status
        return self._get(f"/pub/v2/odds/competitions/{competition_key}", params=params)

    def get_event(self, event_id: str) -> dict:
        """获取单个赛事的完整市场数据"""
        return self._get(f"/pub/v2/odds/events/{event_id}")

    def get_events_by_time(
        self,
        sport_key: str = "soccer",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        markets: List[str] = None,
    ) -> dict:
        """
        Batch fetch events by unix timestamp window.

        Notes:
            - Cloudbet expects unix seconds for from/to.
            - Useful to avoid N-competition sequential scan in live polling.
        """
        now_ts = int(time.time())
        start_ts = int(from_ts) if from_ts is not None else now_ts - 4 * 3600
        end_ts = int(to_ts) if to_ts is not None else now_ts + 2 * 3600

        params: dict = {"sport": sport_key, "from": start_ts, "to": end_ts}
        if markets:
            params["markets"] = markets
        return self._get("/pub/v2/odds/events", params=params)

    def get_live_events(
        self, competition_key: str, markets: List[str] = None
    ) -> List[dict]:
        """
        获取联赛中所有 TRADING_LIVE 赛事

        事件状态说明:
          TRADING_LIVE  — 直播中，市场活跃更新
          TRADING       — 赛前，市场开放
          PRE_TRADING   — 已列出但市场未开
          RESULTED      — 已结束，市场清空
          CANCELLED     — 已取消
        """
        data = self.get_events(competition_key, markets, status="TRADING_LIVE")
        return [e for e in data.get("events", []) if e.get("status") == "TRADING_LIVE"]

    def get_all_live_soccer(
        self,
        markets: List[str] = None,
        priority_leagues: List[str] = None,
        live_statuses: Optional[List[str]] = None,
        progress_every: int = 25,
        prefer_bulk_events_api: bool = True,
        bulk_from_hours: int = 4,
        bulk_to_hours: int = 2,
        hydrate_live_events: bool = True,
        fallback_to_league_scan_on_bulk_failure: bool = False,
    ) -> List[dict]:
        """
        Scan soccer competitions and return events matching requested statuses.

        Args:
            markets: Market keys to request.
            priority_leagues: Competition keys to scan. None means full soccer scan.
            live_statuses: Allowed statuses. Default ["TRADING_LIVE", "TRADING"].

        Returns:
            List of event dicts with _competition_key/_competition_name fields.
        """
        if live_statuses is None:
            live_statuses = ["TRADING_LIVE", "TRADING"]
        allowed_statuses = {str(s).upper() for s in live_statuses if s}
        api_status = None

        if priority_leagues is None and prefer_bulk_events_api:
            scan_start = time.time()
            try:
                now_ts = int(time.time())
                from_ts = now_ts - int(bulk_from_hours * 3600)
                to_ts = now_ts + int(bulk_to_hours * 3600)
                # Bulk list payload can be large; when hydrating live events anyway,
                # skip markets here to reduce timeout risk.
                bulk_markets = None if hydrate_live_events else markets
                payload = self.get_events_by_time(
                    sport_key="soccer",
                    from_ts=from_ts,
                    to_ts=to_ts,
                    markets=bulk_markets,
                )
                competitions = payload.get("competitions", []) or []
                total_comps = len(competitions)
                logger.info(
                    "Soccer bulk scan started: competitions=%d window=-%dh/+%dh allowed=%s",
                    total_comps,
                    bulk_from_hours,
                    bulk_to_hours,
                    sorted(allowed_statuses) if allowed_statuses else "ALL",
                )

                live_events: List[dict] = []
                status_counter: Counter = Counter()
                scanned_events = 0
                hydrated_events = 0

                for idx, comp in enumerate(competitions, start=1):
                    comp_key = comp.get("key") or ""
                    comp_name = comp.get("name") or comp_key
                    for event in comp.get("events", []) or []:
                        status = str(event.get("status", "")).upper()
                        scanned_events += 1
                        if status:
                            status_counter[status] += 1
                        if allowed_statuses and status not in allowed_statuses:
                            continue

                        event_payload = event
                        if hydrate_live_events and status == "TRADING_LIVE":
                            event_id = event.get("id")
                            if event_id is not None:
                                try:
                                    detail = self.get_event(str(event_id))
                                    if isinstance(detail, dict) and detail:
                                        event_payload = detail
                                        hydrated_events += 1
                                except CloudbetAPIError as exc:
                                    logger.debug("Hydrate event %s failed: %s", event_id, exc)

                        event_payload["_competition_key"] = comp_key
                        event_payload["_competition_name"] = comp_name
                        live_events.append(event_payload)

                    if progress_every and idx % progress_every == 0:
                        logger.info(
                            "Soccer bulk progress: %d/%d competitions events=%d matched=%d hydrated=%d elapsed=%.1fs",
                            idx,
                            total_comps,
                            scanned_events,
                            len(live_events),
                            hydrated_events,
                            time.time() - scan_start,
                        )

                logger.info(
                    "Soccer bulk scan: competitions=%d events=%d matched=%d hydrated=%d allowed=%s seen=%s elapsed=%.1fs",
                    total_comps,
                    scanned_events,
                    len(live_events),
                    hydrated_events,
                    sorted(allowed_statuses) if allowed_statuses else "ALL",
                    dict(status_counter),
                    time.time() - scan_start,
                )
                return live_events
            except CloudbetAPIError as exc:
                if not fallback_to_league_scan_on_bulk_failure:
                    logger.warning("Soccer bulk scan failed, skip this round: %s", exc)
                    return []
                logger.warning("Soccer bulk scan failed, fallback to league scan: %s", exc)

        if priority_leagues is None:
            comps = self.get_competitions("soccer")
            priority_leagues = self.extract_competition_keys(comps)

        total_leagues = len(priority_leagues)
        scan_start = time.time()
        logger.info(
            "Soccer scan started: leagues=%d allowed=%s",
            total_leagues,
            sorted(allowed_statuses) if allowed_statuses else "ALL",
        )

        live_events = []
        status_counter: Counter = Counter()
        scanned_events = 0
        for idx, comp_key in enumerate(priority_leagues, start=1):
            if not comp_key:
                continue
            try:
                data = self.get_events(comp_key, markets, status=api_status)
                comp_name = data.get("name", comp_key)
                for event in data.get("events", []):
                    status = str(event.get("status", "")).upper()
                    scanned_events += 1
                    if status:
                        status_counter[status] += 1
                    if allowed_statuses and status not in allowed_statuses:
                        continue
                    event["_competition_key"] = comp_key
                    event["_competition_name"] = comp_name
                    live_events.append(event)
            except CloudbetAPIError as exc:
                logger.debug("Scan %s failed: %s", comp_key, exc)

            if progress_every and idx % progress_every == 0:
                logger.info(
                    "Soccer scan progress: %d/%d leagues events=%d matched=%d elapsed=%.1fs",
                    idx,
                    total_leagues,
                    scanned_events,
                    len(live_events),
                    time.time() - scan_start,
                )
        logger.info(
            "Soccer scan: leagues=%d events=%d matched=%d allowed=%s seen=%s elapsed=%.1fs",
            total_leagues,
            scanned_events,
            len(live_events),
            sorted(allowed_statuses) if allowed_statuses else "ALL",
            dict(status_counter),
            time.time() - scan_start,
        )
        return live_events

    # ── Trading API v3 ────────────────────────────────────────

    def place_bet(
        self,
        event_id: str,
        market_url: str,
        price: float,
        stake: float,
        currency: str = "PLAY_EUR",
        accept_price_change: str = "BETTER",
        reference_id: Optional[str] = None,
    ) -> dict:
        """
        提交下注请求

        参数:
            event_id          : 数字型赛事 ID（来自 Feed API）
            market_url        : 市场 URL，格式 {sport}.{market}/{outcome}?{params}
                                例: "soccer.total_goals/over?total=2.5"
                                例: "soccer.asian_handicap/home?handicap=0.5"
            price             : 欧洲赔率（必须等于 Feed API 当前价格）
            stake             : 注额（必须在 minStake ~ maxStake 之间）
            currency          : 货币（PLAY_EUR=测试, USDT=真实）
            accept_price_change:
              "NONE"   — 拒绝任何赔率变化（最保守）
              "BETTER" — 只接受更优赔率（推荐）
              "ALL"    — 接受任何变化（慎用）
            reference_id      : 可选，调用方传入已存入 DB 的 UUID，确保 DB 与
                                API 使用同一个 referenceId，便于状态追踪和结算。
                                若不传则自动生成新 UUID。

        返回:
            response dict，包含 status: ACCEPTED / REJECTED / PENDING

        重要:
            - 每次下注必须使用新的 referenceId（UUID v4）
            - 被拒后必须重新生成 referenceId 再重试
            - 速率限制：1 次/秒，超过则在此方法内自动等待
        """
        global _LAST_BET_TIME
        # 速率限制：严格 1 次/秒（use max to guard against negative sleep）
        now = time.time()
        wait = 1.0 - (now - _LAST_BET_TIME)
        if wait > 0:
            time.sleep(wait)

        ref_id = reference_id if reference_id else str(uuid.uuid4())
        payload = {
            "referenceId": ref_id,
            "eventId": str(event_id),
            "marketUrl": market_url,
            "price": str(round(price, 3)),
            "stake": str(round(stake, 2)),
            "currency": currency,
            "acceptPriceChange": accept_price_change,
        }

        _LAST_BET_TIME = time.time()
        result = self._post("/pub/v3/bets/place", payload)
        result["_referenceId"] = ref_id  # 保证调用方能拿到
        return result

    def place_bet_with_retry(
        self,
        event_id: str,
        market_url: str,
        price_fn,       # callable -> float: 调用时重新拉取最新赔率
        stake: float,
        currency: str = "PLAY_EUR",
        max_retries: int = 2,
    ) -> dict:
        """
        带自动重试的下注（仅针对 PRICE_CHANGED 错误重试一次）

        price_fn: 无参数 callable，每次调用返回最新价格（用于刷新赔率后重试）
        """
        for attempt in range(max_retries + 1):
            price = price_fn()
            if price <= 1.0:
                return {"status": "SKIPPED", "error": "赔率无效"}

            result = self.place_bet(event_id, market_url, price, stake, currency)
            status = result.get("status", "")
            error_code = result.get("error", result.get("errorCode", ""))

            if status == "ACCEPTED":
                return result

            action = REJECTION_ACTIONS.get(error_code, "stop")

            if action == "retry_fresh_price" and attempt < max_retries:
                logger.info("赔率变化，刷新后重试 (第 %d 次)...", attempt + 1)
                time.sleep(0.5)
                continue

            if action == "wait_retry" and attempt < max_retries:
                logger.info("市场暂停（危险时刻），等待 5 秒后重试...")
                time.sleep(5)
                continue

            # 停止条件
            logger.warning("下注被拒: %s (action=%s)", error_code, action)
            return result

        return {"status": "REJECTED", "error": "超过最大重试次数"}

    def get_bet_status(self, reference_id: str) -> dict:
        """
        查询单笔下注状态

        状态生命周期:
          PENDING → (最多 10 秒，危险时刻最多 5 分钟) → ACCEPTED / REJECTED
          ACCEPTED → WIN / LOSS / VOID / PARTIAL_WON / PARTIAL_LOST
        """
        return self._get(f"/pub/v3/bets/{reference_id}/status")

    def get_bet_history(self, limit: int = 50, offset: int = 0) -> dict:
        """
        获取投注历史（分页）

        返回字段包含: referenceId, status, stake, price, marketUrl,
                      eventName, returnAmount, sportsKey
        """
        return self._get("/pub/v4/bets/history", params={"limit": limit, "offset": offset})

    def wait_for_settlement(
        self,
        reference_id: str,
        timeout: int = _SETTLEMENT_TIMEOUT,
        interval: int = _SETTLEMENT_INTERVAL,
    ) -> dict:
        """
        轮询直到下注结算或超时

        终止状态: ACCEPTED, WIN, LOSS, VOID, PARTIAL_WON, PARTIAL_LOST, REJECTED
        """
        terminal = TERMINAL_STATUSES
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                status = self.get_bet_status(reference_id)
                if status.get("status") in terminal:
                    return status
            except CloudbetAPIError as exc:
                logger.debug("查询 %s 状态失败: %s", reference_id, exc)
            time.sleep(interval)

        logger.warning("等待结算超时: %s", reference_id)
        return {"status": "TIMEOUT", "referenceId": reference_id}

    # ── Account API v1 ────────────────────────────────────────

    def get_currencies(self) -> List[str]:
        """返回账户持有的货币列表"""
        return self._get("/pub/v1/account/currencies").get("currencies", [])

    def get_balance(self, currency: str = "PLAY_EUR") -> float:
        """
        获取指定货币余额

        测试货币: PLAY_EUR
        真实货币: BTC, ETH, USDT, USDC, EUR, USD, CAD, BCH 等
        """
        raw = self._get(f"/pub/v1/account/currencies/{currency}/balance")
        try:
            return float(raw.get("amount") or 0)
        except (ValueError, TypeError):
            logger.warning("无法解析余额响应: %s", raw)
            return 0.0

    # ── Market URL 构建工具 ───────────────────────────────────

    @staticmethod
    def build_total_goals_url(outcome: str, total: float) -> str:
        """
        构建 total_goals 市场 URL

        outcome: "over" 或 "under"
        total  : 进球数线 (e.g. 2.5)

        返回: "soccer.total_goals/over?total=2.5"
        """
        return f"soccer.total_goals/{outcome}?total={total}"

    @staticmethod
    def build_asian_handicap_url(outcome: str, handicap: float) -> str:
        """
        构建 asian_handicap 市场 URL

        outcome : "home" 或 "away"
        handicap: 让球数（从主队视角，正值=受让）
                  e.g. home -0.5 → handicap=0.5
                       home -1   → handicap=1
                       home +0.5 → handicap=-0.5

        返回: "soccer.asian_handicap/home?handicap=0.5"
        """
        return f"soccer.asian_handicap/{outcome}?handicap={abs(handicap)}"

    @staticmethod
    def build_match_odds_url(outcome: str) -> str:
        """
        构建 match_odds 市场 URL

        outcome: "home", "draw", "away"
        返回: "soccer.match_odds/home"
        """
        return f"soccer.match_odds/{outcome}"

    # ── 从 Feed 响应中提取市场数据 ────────────────────────────

    @staticmethod
    def extract_total_goals_market(event: dict, period: str = "ft") -> Optional[dict]:
        """
        从事件数据中提取 total_goals 市场选项

        返回:
            {line, over_price, under_price, over_url, under_url,
             over_min_stake, over_max_stake, under_min_stake, under_max_stake}
        或 None
        """
        markets = event.get("markets", {})
        market = markets.get("soccer.total_goals", {})
        if not market:
            return None

        sub_key = f"period={period}"
        submarket = market.get("submarkets", {}).get(sub_key, {})
        selections = submarket.get("selections", [])

        result: dict = {}
        for sel in selections:
            sel_params = sel.get("params", "")
            outcome = sel.get("outcome", "")
            price = sel.get("price")
            status = sel.get("status", "")

            if status != "SELECTION_ENABLED" or not price or not outcome:
                continue

            # 用 parse_qs 稳健解析参数（如 "total=2.5" 或 "total=2.5&foo=bar"）
            if "total=" in sel_params and "line" not in result:
                try:
                    qs = parse_qs(sel_params)
                    result["line"] = float(qs["total"][0])
                except (KeyError, IndexError, ValueError):
                    continue

            try:
                price_f = float(price)
            except (ValueError, TypeError):
                continue

            if outcome == "over":
                result["over_price"] = price_f
                result["over_min_stake"] = float(sel.get("minStake") or 0)
                result["over_max_stake"] = float(sel.get("maxStake") or 9999)
            elif outcome == "under":
                result["under_price"] = price_f
                result["under_min_stake"] = float(sel.get("minStake") or 0)
                result["under_max_stake"] = float(sel.get("maxStake") or 9999)

        # 构建 URL 仅在 line 确认后（避免空字符串 URL）
        if "over_price" in result and "under_price" in result and "line" in result:
            line = result["line"]
            result["over_url"] = f"soccer.total_goals/over?total={line}"
            result["under_url"] = f"soccer.total_goals/under?total={line}"
            return result
        return None

    @staticmethod
    def extract_match_score(event: dict) -> tuple:
        """
        ????????????

        ??: (home_goals, away_goals, elapsed_minute)
        """
        home_goals = away_goals = 0
        elapsed = 0

        # ?? 1: scores ??
        scores = event.get("scores") or {}
        if isinstance(scores, dict) and scores:
            try:
                home_goals = int(scores.get("home") or scores.get("1") or 0)
                away_goals = int(scores.get("away") or scores.get("2") or 0)
            except (ValueError, TypeError):
                pass

        # ?? 2: home/away score ???
        if home_goals == 0 and away_goals == 0:
            try:
                home_obj = event.get("home") or {}
                home_goals = int(home_obj.get("score") or 0)
                away_obj = event.get("away") or {}
                away_goals = int(away_obj.get("score") or 0)
            except (ValueError, TypeError):
                pass

        # ?? 3: periods ??
        periods = event.get("periods") or []
        if periods and home_goals == 0 and away_goals == 0:
            try:
                for p in periods:
                    home_goals += int(p.get("homeScore") or 0)
                    away_goals += int(p.get("awayScore") or 0)
            except (ValueError, TypeError):
                pass

        # ????????? clock??????? kickoff ??
        clock = event.get("clock") or {}
        elapsed_candidates = (
            clock.get("elapsedSeconds"),
            clock.get("elapsed"),
            event.get("elapsedSeconds"),
            event.get("elapsed"),
        )
        for raw_elapsed in elapsed_candidates:
            if raw_elapsed in (None, ""):
                continue
            try:
                elapsed_secs = float(raw_elapsed)
            except (TypeError, ValueError):
                continue
            if elapsed_secs > 0:
                elapsed = int(elapsed_secs // 60)
                break

        if elapsed <= 0:
            kickoff = event.get("cutoffTime") or event.get("startTime")
            if kickoff:
                try:
                    kickoff_dt = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
                    if kickoff_dt.tzinfo is None:
                        kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
                    now_dt = datetime.now(timezone.utc)
                    elapsed = max(0, int((now_dt - kickoff_dt).total_seconds() // 60))
                except (TypeError, ValueError):
                    pass

        # ?????/????????????
        elapsed = max(0, min(elapsed, 130))

        return home_goals, away_goals, elapsed


# ── 独立使用示例 ──────────────────────────────────────────────
if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    api_key = os.environ.get("CLOUDBET_API_KEY", "")
    if not api_key:
        print("请设置环境变量 CLOUDBET_API_KEY")
        exit(1)

    client = CloudbetClient(api_key)

    # 余额查询
    balance = client.get_balance("PLAY_EUR")
    print(f"PLAY_EUR 余额: {balance}")

    # 直播足球赛事（英超）
    live = client.get_live_events(
        "soccer-england-premier-league",
        markets=["soccer.total_goals", "soccer.match_odds"],
    )
    for event in live[:3]:
        home = event.get("home", {}).get("name", "?")
        away = event.get("away", {}).get("name", "?")
        tg = CloudbetClient.extract_total_goals_market(event)
        score = CloudbetClient.extract_match_score(event)
        print(f"{home} {score[0]}-{score[1]} {away}  |  "
              f"O/U {tg['line'] if tg else '?'}: "
              f"Over={tg['over_price'] if tg else '?'} / Under={tg['under_price'] if tg else '?'}")
