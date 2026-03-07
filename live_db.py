"""
SQLite 数据库管理 — 直播投注闭环日志
======================================
四张核心表：
  odds_snapshot  — 每次轮询的赔率快照（用于 CLV 计算）
  model_snapshot — 模型定价快照（用于事后回测）
  orders         — 每笔下注记录（含执行价 / 拒单原因）
  results        — 结算结果 + CLV

设计原则：
  - 只追加，不修改历史记录（orders 状态更新除外）
  - 支持多策略共存（sport 字段区分）
  - 可直接用 pandas / SQLite Browser 做离线分析
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

DB_FILE = "live_betting.db"

# ── Schema ────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_snapshot (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    event_id         TEXT    NOT NULL,
    sport            TEXT    NOT NULL DEFAULT 'basketball',
    competition      TEXT,
    home_team        TEXT,
    away_team        TEXT,
    status           TEXT,
    market_url       TEXT,
    line             REAL,
    over_price       REAL,
    under_price      REAL,
    elapsed_minutes  REAL,
    current_score    INTEGER
);

CREATE TABLE IF NOT EXISTS model_snapshot (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT    NOT NULL,
    event_id              TEXT    NOT NULL,
    pregame_line          REAL,
    current_score         INTEGER,
    elapsed_minutes       REAL,
    p_model_over          REAL,
    p_model_under         REAL,
    p_mkt_over            REAL,
    p_mkt_under           REAL,
    edge_over             REAL,
    edge_under            REAL,
    fair_over_price       REAL,
    fair_under_price      REAL,
    scoring_rate          REAL,
    expected_remaining    REAL
);

CREATE TABLE IF NOT EXISTS orders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    reference_id     TEXT    UNIQUE,
    event_id         TEXT    NOT NULL,
    sport            TEXT    NOT NULL DEFAULT 'basketball',
    match            TEXT,
    market_url       TEXT,
    side             TEXT,
    line             REAL,
    requested_price  REAL,
    executed_price   REAL,
    stake            REAL,
    currency         TEXT    DEFAULT 'USDT',
    status           TEXT    DEFAULT 'PENDING',
    reject_reason    TEXT,
    edge_at_bet      REAL,
    p_model_at_bet   REAL
);

CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    reference_id    TEXT,
    event_id        TEXT    NOT NULL,
    match           TEXT,
    side            TEXT,
    stake           REAL,
    bet_price       REAL,
    closing_price   REAL,
    clv             REAL,
    clv_percent     REAL,
    outcome         TEXT,
    pnl             REAL,
    final_score     INTEGER,
    final_total     REAL
);

CREATE INDEX IF NOT EXISTS idx_odds_event ON odds_snapshot (event_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_model_event ON model_snapshot (event_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_orders_ref ON orders (reference_id);
CREATE INDEX IF NOT EXISTS idx_results_ref ON results (reference_id);
"""


# ── 连接 / 初始化 ─────────────────────────────────────────────

def get_connection(db_file: str = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(db_file, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 支持并发读
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db(db_file: str = DB_FILE) -> None:
    """初始化数据库（首次运行时建表）"""
    conn = get_connection(db_file)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    logging.info("数据库就绪: %s", db_file)


# ── odds_snapshot ─────────────────────────────────────────────

def insert_odds_snapshot(data: Dict, db_file: str = DB_FILE) -> None:
    """记录一次赔率快照"""
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection(db_file)
    conn.execute(
        """
        INSERT INTO odds_snapshot
            (timestamp, event_id, sport, competition, home_team, away_team,
             status, market_url, line, over_price, under_price,
             elapsed_minutes, current_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data.get("timestamp", now),
            data["event_id"],
            data.get("sport", "basketball"),
            data.get("competition", ""),
            data.get("home_team", ""),
            data.get("away_team", ""),
            data.get("status", ""),
            data.get("market_url", ""),
            data.get("line"),
            data.get("over_price"),
            data.get("under_price"),
            data.get("elapsed_minutes"),
            data.get("current_score"),
        ),
    )
    conn.commit()
    conn.close()


def get_first_odds_snapshot(event_id: str, db_file: str = DB_FILE) -> Optional[Dict]:
    """返回该赛事最早的赔率快照（用于计算 CLV 时的"下单时赔率"）"""
    conn = get_connection(db_file)
    row = conn.execute(
        "SELECT * FROM odds_snapshot WHERE event_id=? ORDER BY timestamp ASC LIMIT 1",
        (event_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── model_snapshot ────────────────────────────────────────────

def insert_model_snapshot(
    event_id: str,
    pregame_line: float,
    current_score: int,
    elapsed_minutes: float,
    model_result: Dict,
    db_file: str = DB_FILE,
) -> None:
    """记录一次模型定价快照"""
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection(db_file)
    conn.execute(
        """
        INSERT INTO model_snapshot
            (timestamp, event_id, pregame_line, current_score, elapsed_minutes,
             p_model_over, p_model_under, p_mkt_over, p_mkt_under,
             edge_over, edge_under, fair_over_price, fair_under_price,
             scoring_rate, expected_remaining)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            now,
            event_id,
            pregame_line,
            current_score,
            elapsed_minutes,
            model_result.get("p_model_over"),
            model_result.get("p_model_under"),
            model_result.get("p_mkt_over"),
            model_result.get("p_mkt_under"),
            model_result.get("edge_over"),
            model_result.get("edge_under"),
            model_result.get("fair_over_price"),
            model_result.get("fair_under_price"),
            model_result.get("scoring_rate_per_min"),
            model_result.get("expected_remaining_score"),
        ),
    )
    conn.commit()
    conn.close()


# ── orders ────────────────────────────────────────────────────

def insert_order(order_data: Dict, db_file: str = DB_FILE) -> None:
    """记录一笔下注（下单时立即写入，status=PENDING）"""
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection(db_file)
    conn.execute(
        """
        INSERT OR IGNORE INTO orders
            (timestamp, reference_id, event_id, sport, match, market_url,
             side, line, requested_price, executed_price, stake, currency,
             status, reject_reason, edge_at_bet, p_model_at_bet)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            order_data.get("timestamp", now),
            order_data.get("reference_id"),
            order_data["event_id"],
            order_data.get("sport", "basketball"),
            order_data.get("match", ""),
            order_data.get("market_url", ""),
            order_data.get("side", ""),
            order_data.get("line"),
            order_data.get("requested_price"),
            order_data.get("executed_price"),
            order_data.get("stake"),
            order_data.get("currency", "USDT"),
            order_data.get("status", "PENDING"),
            order_data.get("reject_reason", ""),
            order_data.get("edge_at_bet"),
            order_data.get("p_model_at_bet"),
        ),
    )
    conn.commit()
    conn.close()


def update_order_status(
    reference_id: str,
    status: str,
    executed_price: float = None,
    reject_reason: str = None,
    db_file: str = DB_FILE,
) -> None:
    """更新订单状态（ACCEPTED / REJECTED）"""
    conn = get_connection(db_file)
    if executed_price is not None:
        conn.execute(
            "UPDATE orders SET status=?, executed_price=? WHERE reference_id=?",
            (status, executed_price, reference_id),
        )
    elif reject_reason:
        conn.execute(
            "UPDATE orders SET status=?, reject_reason=? WHERE reference_id=?",
            (status, reject_reason, reference_id),
        )
    else:
        conn.execute(
            "UPDATE orders SET status=? WHERE reference_id=?",
            (status, reference_id),
        )
    conn.commit()
    conn.close()


def get_accepted_orders(
    db_file: str = DB_FILE,
    min_stake: float = 0.0,
    limit: Optional[int] = None,
    statuses: Optional[List[str]] = None,
    sport: Optional[str] = None,
) -> List[Dict]:
    """Return unsettled orders with requested statuses and positive stake."""
    status_list = [str(s).upper() for s in (statuses or ["ACCEPTED"]) if str(s).strip()]
    if not status_list:
        status_list = ["ACCEPTED"]

    placeholders = ",".join("?" for _ in status_list)
    conn = get_connection(db_file)
    sql = f"""
        SELECT o.*
        FROM orders o
        LEFT JOIN results r ON o.reference_id = r.reference_id
        WHERE o.status IN ({placeholders})
          AND r.id IS NULL
          AND COALESCE(o.stake, 0) > ?
    """
    params: List = status_list + [float(min_stake)]
    if sport:
        sql += " AND o.sport = ?"
        params.append(str(sport).lower())

    sql += " ORDER BY o.id ASC"
    if limit is not None and int(limit) > 0:
        sql += " LIMIT ?"
        params.append(int(limit))

    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_unsettled_accepted_orders(
    db_file: str = DB_FILE,
    min_stake: float = 0.0,
    statuses: Optional[List[str]] = None,
    sport: Optional[str] = None,
) -> int:
    """Count unsettled orders with requested statuses and positive stake."""
    status_list = [str(s).upper() for s in (statuses or ["ACCEPTED"]) if str(s).strip()]
    if not status_list:
        status_list = ["ACCEPTED"]

    placeholders = ",".join("?" for _ in status_list)
    conn = get_connection(db_file)
    sql = f"""
        SELECT COUNT(*) AS cnt
        FROM orders o
        LEFT JOIN results r ON o.reference_id = r.reference_id
        WHERE o.status IN ({placeholders})
          AND r.id IS NULL
          AND COALESCE(o.stake, 0) > ?
    """
    params: List = status_list + [float(min_stake)]
    if sport:
        sql += " AND o.sport = ?"
        params.append(str(sport).lower())

    row = conn.execute(sql, tuple(params)).fetchone()
    conn.close()
    return int(row["cnt"] or 0) if row else 0


def auto_close_zero_stake_accepted_orders(db_file: str = DB_FILE) -> int:
    """
    Auto-close accepted orders that have zero stake and no settlement yet.

    Returns:
        Number of rows updated.
    """
    conn = get_connection(db_file)
    cur = conn.execute(
        """
        UPDATE orders
        SET status = 'AUTO_VOID',
            reject_reason = COALESCE(reject_reason, 'AUTO_CLOSED_ZERO_STAKE')
        WHERE id IN (
            SELECT o.id
            FROM orders o
            LEFT JOIN results r ON o.reference_id = r.reference_id
            WHERE o.status = 'ACCEPTED'
              AND r.id IS NULL
              AND COALESCE(o.stake, 0) <= 0
        )
        """
    )
    conn.commit()
    affected = int(cur.rowcount or 0)
    conn.close()
    return affected



def auto_expire_stale_pending_orders(
    db_file: str = DB_FILE,
    stale_minutes: float = 20.0,
    sport: Optional[str] = None,
    reason: str = "AUTO_STALE_PENDING_TIMEOUT",
) -> int:
    """
    Auto-expire long-pending orders to avoid stale exposure blocking new entries.

    Orders are marked as STALE_PENDING only when:
      - status is PENDING
      - no settlement row exists
      - order timestamp is older than now - stale_minutes
    """
    try:
        stale_minutes_val = float(stale_minutes)
    except (TypeError, ValueError):
        stale_minutes_val = 20.0
    stale_minutes_val = max(stale_minutes_val, 0.0)

    cutoff = (datetime.utcnow() - timedelta(minutes=stale_minutes_val)).isoformat() + "Z"

    select_sql = """
        SELECT o.id
        FROM orders o
        LEFT JOIN results r ON o.reference_id = r.reference_id
        WHERE o.status = 'PENDING'
          AND r.id IS NULL
          AND o.timestamp <= ?
    """
    select_params: List = [cutoff]
    if sport:
        select_sql += " AND o.sport = ?"
        select_params.append(str(sport).lower())

    conn = get_connection(db_file)
    stale_ids = [row["id"] for row in conn.execute(select_sql, tuple(select_params)).fetchall()]
    if not stale_ids:
        conn.close()
        return 0

    placeholders = ",".join("?" for _ in stale_ids)
    update_sql = f"""
        UPDATE orders
        SET status = 'STALE_PENDING',
            reject_reason = CASE
                WHEN COALESCE(TRIM(reject_reason), '') = '' THEN ?
                ELSE reject_reason
            END
        WHERE id IN ({placeholders})
    """
    cur = conn.execute(update_sql, tuple([str(reason)] + stale_ids))
    conn.commit()
    affected = int(cur.rowcount or 0)
    conn.close()
    return affected
def repair_pending_acceptance_rejections(db_file: str = DB_FILE) -> int:
    """
    Recover historically misclassified orders:
    status=REJECTED but reason indicates PENDING_ACCEPTANCE.
    """
    conn = get_connection(db_file)
    cur = conn.execute(
        """
        UPDATE orders
        SET status = 'PENDING'
        WHERE id IN (
            SELECT o.id
            FROM orders o
            LEFT JOIN results r ON o.reference_id = r.reference_id
            WHERE o.status = 'REJECTED'
              AND r.id IS NULL
              AND UPPER(COALESCE(o.reject_reason, '')) LIKE '%PENDING_ACCEPTANCE%'
        )
        """
    )
    conn.commit()
    affected = int(cur.rowcount or 0)
    conn.close()
    return affected


def get_rejection_stats(
    window: int = 100,
    db_file: str = DB_FILE,
    sport: Optional[str] = None,
    include_statuses: Optional[List[str]] = None,
    rejected_statuses: Optional[List[str]] = None,
) -> Dict:
    """Return rejection stats for recent orders after filtering."""
    include = [str(s).upper() for s in (include_statuses or ["ACCEPTED", "REJECTED"]) if str(s).strip()]
    rejected = [str(s).upper() for s in (rejected_statuses or ["REJECTED"]) if str(s).strip()]
    if not include:
        include = ["ACCEPTED", "REJECTED"]
    if not rejected:
        rejected = ["REJECTED"]

    where_parts: List[str] = []
    params: List = []
    if sport:
        where_parts.append("sport = ?")
        params.append(str(sport).lower())

    inc_ph = ",".join("?" for _ in include)
    where_parts.append(f"status IN ({inc_ph})")
    params.extend(include)

    # Historical bug compatibility: do not treat pending-acceptance responses as true rejects.
    where_parts.append("NOT (status = 'REJECTED' AND UPPER(COALESCE(reject_reason, '')) LIKE '%PENDING_ACCEPTANCE%')")

    where_clause = " AND ".join(where_parts) if where_parts else "1=1"
    rej_ph = ",".join("?" for _ in rejected)

    conn = get_connection(db_file)
    sql = f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status IN ({rej_ph}) THEN 1 ELSE 0 END) as rejected
        FROM (
            SELECT status
            FROM orders
            WHERE {where_clause}
            ORDER BY id DESC
            LIMIT ?
        )
    """
    row = conn.execute(sql, tuple(rejected + params + [int(window)])).fetchone()
    conn.close()

    total = int(row["total"] or 0) if row else 0
    rejected_cnt = int(row["rejected"] or 0) if row else 0
    rate = (rejected_cnt / total) if total > 0 else 0.0
    return {"total": total, "rejected": rejected_cnt, "rate": rate}


def get_rejection_rate(
    window: int = 100,
    db_file: str = DB_FILE,
    sport: Optional[str] = None,
    include_statuses: Optional[List[str]] = None,
    rejected_statuses: Optional[List[str]] = None,
) -> float:
    """Backward-compatible rejection-rate helper."""
    stats = get_rejection_stats(
        window=window,
        db_file=db_file,
        sport=sport,
        include_statuses=include_statuses,
        rejected_statuses=rejected_statuses,
    )
    return float(stats.get("rate", 0.0))

# ── results ───────────────────────────────────────────────────

def insert_result(result_data: Dict, db_file: str = DB_FILE) -> None:
    """记录结算结果（含 CLV）"""
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection(db_file)
    ref_id = result_data.get("reference_id")
    if ref_id:
        existing = conn.execute(
            "SELECT 1 FROM results WHERE reference_id=? LIMIT 1",
            (ref_id,),
        ).fetchone()
        if existing:
            conn.close()
            return

    conn.execute(
        """
        INSERT INTO results
            (timestamp, reference_id, event_id, match, side, stake,
             bet_price, closing_price, clv, clv_percent,
             outcome, pnl, final_score, final_total)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            result_data.get("timestamp", now),
            result_data.get("reference_id"),
            result_data["event_id"],
            result_data.get("match", ""),
            result_data.get("side", ""),
            result_data.get("stake"),
            result_data.get("bet_price"),
            result_data.get("closing_price"),
            result_data.get("clv"),
            result_data.get("clv_percent"),
            result_data.get("outcome"),
            result_data.get("pnl"),
            result_data.get("final_score"),
            result_data.get("final_total"),
        ),
    )
    conn.commit()
    conn.close()


# ── 聚合查询（CLV / PnL 统计）────────────────────────────────

def get_clv_summary(db_file: str = DB_FILE) -> Dict:
    """
    返回结算记录的 CLV 和盈亏汇总
    CLV > 0 表示下单价优于收盘价 → 长期正期望的信号
    """
    conn = get_connection(db_file)
    row = conn.execute(
        """
        SELECT
            COUNT(*)                                        AS total_settled,
            ROUND(AVG(clv), 4)                             AS avg_clv,
            ROUND(AVG(clv_percent), 4)                     AS avg_clv_pct,
            SUM(CASE WHEN clv > 0 THEN 1 ELSE 0 END)      AS positive_clv_count,
            SUM(CASE WHEN outcome='WIN'  THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN outcome='LOSE' THEN 1 ELSE 0 END) AS losses,
            ROUND(SUM(pnl), 4)                             AS total_pnl,
            ROUND(SUM(stake), 4)                           AS total_stake,
            ROUND(SUM(pnl) / NULLIF(SUM(stake), 0), 4)    AS roi
        FROM results
        WHERE clv IS NOT NULL
        """
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_recent_orders_count(minutes: int = 30, db_file: str = DB_FILE) -> int:
    """返回最近 N 分钟内的已成交订单数（用于控频）"""
    from datetime import timedelta
    conn = get_connection(db_file)
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat() + "Z"
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM orders WHERE status = 'ACCEPTED' AND timestamp >= ?",
        (cutoff,),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


# -- Risk Helpers ------------------------------------------------------------
def get_recent_result_returns(
    window: int = 80,
    db_file: str = DB_FILE,
    sport: Optional[str] = None,
) -> List[float]:
    """
    Return recent settled bet returns as pnl / stake.
    """
    conn = get_connection(db_file)
    sql = """
        SELECT r.stake, r.pnl
        FROM results r
        LEFT JOIN orders o ON r.reference_id = o.reference_id
        WHERE r.stake IS NOT NULL AND r.stake > 0
          AND r.pnl IS NOT NULL
    """
    params: List = []
    if sport:
        sql += " AND o.sport = ?"
        params.append(str(sport).lower())

    sql += " ORDER BY r.id DESC LIMIT ?"
    params.append(int(window))

    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()

    returns: List[float] = []
    for row in rows:
        try:
            stake = float(row["stake"])
            pnl = float(row["pnl"])
        except (TypeError, ValueError):
            continue
        if stake > 0:
            returns.append(pnl / stake)
    return returns


def get_open_exposure(
    db_file: str = DB_FILE,
    include_statuses: Optional[List[str]] = None,
    sport: Optional[str] = None,
) -> float:
    """
    Return total stake of unsettled orders by statuses/sport.
    """
    status_list = [
        str(s).upper()
        for s in (include_statuses or ["ACCEPTED", "PENDING"])
        if str(s).strip()
    ]
    if not status_list:
        status_list = ["ACCEPTED", "PENDING"]

    status_ph = ",".join("?" for _ in status_list)
    where_parts = [f"o.status IN ({status_ph})", "r.id IS NULL"]
    params: List = list(status_list)
    if sport:
        where_parts.append("o.sport = ?")
        params.append(str(sport).lower())

    where_clause = " AND ".join(where_parts)

    conn = get_connection(db_file)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(o.stake), 0) AS exposure
        FROM orders o
        LEFT JOIN results r ON o.reference_id = r.reference_id
        WHERE {where_clause}
        """,
        tuple(params),
    ).fetchone()
    conn.close()
    try:
        return float(row["exposure"] or 0.0) if row else 0.0
    except (TypeError, ValueError):
        return 0.0

# -- Self Test ---------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    test_db = "test_live_betting.db"

    # 清理旧测试文件
    if os.path.exists(test_db):
        os.remove(test_db)

    init_db(test_db)
    print("数据库初始化成功")

    # 写入样本数据
    insert_odds_snapshot(
        {
            "event_id": "EVT001",
            "competition": "NBA",
            "home_team": "Lakers",
            "away_team": "Celtics",
            "status": "TRADING_LIVE",
            "market_url": "basketball.totals/over?points=228.5",
            "line": 228.5,
            "over_price": 1.91,
            "under_price": 1.95,
            "elapsed_minutes": 24.0,
            "current_score": 115,
        },
        db_file=test_db,
    )

    insert_order(
        {
            "reference_id": "REF-001",
            "event_id": "EVT001",
            "match": "Lakers vs Celtics",
            "market_url": "basketball.totals/over?points=228.5",
            "side": "over",
            "line": 228.5,
            "requested_price": 1.91,
            "stake": 5.0,
            "status": "ACCEPTED",
            "edge_at_bet": 0.062,
            "p_model_at_bet": 0.562,
        },
        db_file=test_db,
    )

    insert_result(
        {
            "reference_id": "REF-001",
            "event_id": "EVT001",
            "match": "Lakers vs Celtics",
            "side": "over",
            "stake": 5.0,
            "bet_price": 1.91,
            "closing_price": 1.85,
            "clv": 0.06,
            "clv_percent": 3.14,
            "outcome": "WIN",
            "pnl": 4.55,
            "final_score": 241,
            "final_total": 228.5,
        },
        db_file=test_db,
    )

    stats = get_clv_summary(test_db)
    print(f"CLV 统计: {dict(stats)}")
    rejection_rate = get_rejection_rate(db_file=test_db)
    print(f"拒单率: {rejection_rate:.1%}")

    os.remove(test_db)
    print("测试通过，测试文件已清理")







