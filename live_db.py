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
from datetime import datetime
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
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 支持并发读
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


def get_accepted_orders(db_file: str = DB_FILE) -> List[Dict]:
    """返回所有已成交但尚未结算的订单"""
    conn = get_connection(db_file)
    rows = conn.execute(
        """
        SELECT o.* FROM orders o
        LEFT JOIN results r ON o.reference_id = r.reference_id
        WHERE o.status = 'ACCEPTED' AND r.id IS NULL
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rejection_rate(window: int = 100, db_file: str = DB_FILE) -> float:
    """
    计算最近 N 笔下注的拒单率（用于风险监控）

    Cloudbet 条款：拒单率 > 80% 可能被标记
    """
    conn = get_connection(db_file)
    row = conn.execute(
        """
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) as rejected
        FROM (
            SELECT status FROM orders ORDER BY id DESC LIMIT ?
        )
        """,
        (window,),
    ).fetchone()
    conn.close()
    if row and row["total"] > 0:
        return row["rejected"] / row["total"]
    return 0.0


# ── results ───────────────────────────────────────────────────

def insert_result(result_data: Dict, db_file: str = DB_FILE) -> None:
    """记录结算结果（含 CLV）"""
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection(db_file)
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
    conn = get_connection(db_file)
    cutoff = datetime.utcnow().isoformat()
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM orders
        WHERE status = 'ACCEPTED'
          AND timestamp >= datetime(?, '-' || ? || ' minutes')
        """,
        (cutoff, minutes),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


# ── 独立测试 ──────────────────────────────────────────────────
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
