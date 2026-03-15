from pathlib import Path
from datetime import datetime, timedelta

import live_db


def test_open_exposure_and_recent_returns(tmp_path: Path):
    db_file = str(tmp_path / "risk_test.db")
    live_db.init_db(db_file)

    # 两笔已成交订单，其中 REF-2 已结算，REF-1 未结算
    live_db.insert_order(
        {
            "reference_id": "REF-1",
            "event_id": "EVT-1",
            "stake": 5.0,
            "status": "ACCEPTED",
            "currency": "USDT",
        },
        db_file=db_file,
    )
    live_db.insert_order(
        {
            "reference_id": "REF-2",
            "event_id": "EVT-2",
            "stake": 2.0,
            "status": "ACCEPTED",
            "currency": "USDT",
        },
        db_file=db_file,
    )

    live_db.insert_result(
        {
            "reference_id": "REF-2",
            "event_id": "EVT-2",
            "stake": 2.0,
            "pnl": 1.0,
            "outcome": "WIN",
        },
        db_file=db_file,
    )

    exposure = live_db.get_open_exposure(db_file=db_file)
    assert exposure == 5.0

    returns = live_db.get_recent_result_returns(window=10, db_file=db_file)
    assert returns == [0.5]


def test_auto_close_zero_stake_and_pending_count(tmp_path: Path):
    db_file = str(tmp_path / "risk_cleanup.db")
    live_db.init_db(db_file)

    live_db.insert_order(
        {
            "reference_id": "ZERO-1",
            "event_id": "EVT-ZERO",
            "stake": 0.0,
            "status": "ACCEPTED",
            "currency": "USDT",
        },
        db_file=db_file,
    )
    live_db.insert_order(
        {
            "reference_id": "POS-1",
            "event_id": "EVT-POS",
            "stake": 3.0,
            "status": "ACCEPTED",
            "currency": "USDT",
        },
        db_file=db_file,
    )

    assert live_db.count_unsettled_accepted_orders(db_file=db_file, min_stake=0.01) == 1

    closed = live_db.auto_close_zero_stake_accepted_orders(db_file=db_file)
    assert closed == 1

    pending = live_db.get_accepted_orders(db_file=db_file, min_stake=0.01)
    assert len(pending) == 1
    assert pending[0]["reference_id"] == "POS-1"


def test_sport_scoped_pending_and_exposure(tmp_path: Path):
    db_file = str(tmp_path / "sport_scope.db")
    live_db.init_db(db_file)

    live_db.insert_order(
        {
            "reference_id": "SOC-1",
            "event_id": "SOC-EVT",
            "sport": "soccer",
            "stake": 4.0,
            "status": "PENDING",
            "currency": "USDT",
        },
        db_file=db_file,
    )
    live_db.insert_order(
        {
            "reference_id": "NBA-1",
            "event_id": "NBA-EVT",
            "sport": "basketball",
            "stake": 3.0,
            "status": "ACCEPTED",
            "currency": "USDT",
        },
        db_file=db_file,
    )

    cnt_soc = live_db.count_unsettled_accepted_orders(
        db_file=db_file,
        statuses=["ACCEPTED", "PENDING"],
        sport="soccer",
    )
    cnt_nba = live_db.count_unsettled_accepted_orders(
        db_file=db_file,
        statuses=["ACCEPTED", "PENDING"],
        sport="basketball",
    )

    assert cnt_soc == 1
    assert cnt_nba == 1

    exp_soc = live_db.get_open_exposure(db_file=db_file, sport="soccer")
    exp_all = live_db.get_open_exposure(db_file=db_file)
    assert exp_soc == 4.0
    assert exp_all == 7.0


def test_auto_expire_stale_pending_orders(tmp_path: Path):
    db_file = str(tmp_path / "stale_pending.db")
    live_db.init_db(db_file)

    old_ts = (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z"
    live_db.insert_order(
        {
            "timestamp": old_ts,
            "reference_id": "SOC-OLD-PEND",
            "event_id": "SOC-EVT-OLD",
            "sport": "soccer",
            "stake": 2.0,
            "status": "PENDING",
            "currency": "USDT",
        },
        db_file=db_file,
    )
    live_db.insert_order(
        {
            "reference_id": "SOC-NEW-PEND",
            "event_id": "SOC-EVT-NEW",
            "sport": "soccer",
            "stake": 1.0,
            "status": "PENDING",
            "currency": "USDT",
        },
        db_file=db_file,
    )

    expired = live_db.auto_expire_stale_pending_orders(
        db_file=db_file,
        stale_minutes=30,
        sport="soccer",
    )
    assert expired == 1

    pending_left = live_db.count_unsettled_accepted_orders(
        db_file=db_file,
        statuses=["PENDING"],
        sport="soccer",
    )
    stale_count = live_db.count_unsettled_accepted_orders(
        db_file=db_file,
        statuses=["STALE_PENDING"],
        sport="soccer",
    )

    assert pending_left == 1
    assert stale_count == 1


def test_recent_competition_performance(tmp_path: Path):
    db_file = str(tmp_path / "competition_perf.db")
    live_db.init_db(db_file)

    # Event A (good)
    live_db.insert_odds_snapshot(
        {
            "event_id": "EVT-A",
            "sport": "soccer",
            "competition": "CompA",
            "status": "TRADING_LIVE",
            "line": 2.5,
            "over_price": 1.9,
            "under_price": 1.9,
        },
        db_file=db_file,
    )
    live_db.insert_order(
        {
            "reference_id": "A-1",
            "event_id": "EVT-A",
            "sport": "soccer",
            "stake": 2.0,
            "status": "ACCEPTED",
        },
        db_file=db_file,
    )
    live_db.insert_result(
        {
            "reference_id": "A-1",
            "event_id": "EVT-A",
            "stake": 2.0,
            "pnl": 1.0,
            "outcome": "WIN",
        },
        db_file=db_file,
    )

    # Event B (bad)
    live_db.insert_odds_snapshot(
        {
            "event_id": "EVT-B",
            "sport": "soccer",
            "competition": "CompB",
            "status": "TRADING_LIVE",
            "line": 2.5,
            "over_price": 1.9,
            "under_price": 1.9,
        },
        db_file=db_file,
    )
    live_db.insert_order(
        {
            "reference_id": "B-1",
            "event_id": "EVT-B",
            "sport": "soccer",
            "stake": 2.0,
            "status": "ACCEPTED",
        },
        db_file=db_file,
    )
    live_db.insert_result(
        {
            "reference_id": "B-1",
            "event_id": "EVT-B",
            "stake": 2.0,
            "pnl": -2.0,
            "outcome": "LOSE",
        },
        db_file=db_file,
    )

    perf = live_db.get_recent_competition_performance(
        sport="soccer",
        window=20,
        min_samples=1,
        db_file=db_file,
    )

    assert "CompA" in perf
    assert "CompB" in perf
    assert perf["CompA"]["samples"] == 1
    assert perf["CompA"]["roi"] > 0
    assert perf["CompA"]["win_rate"] == 1.0
    assert perf["CompB"]["samples"] == 1
    assert perf["CompB"]["roi"] < 0


def test_event_level_open_order_dedup_and_accept_clears_reason(tmp_path: Path):
    db_file = str(tmp_path / "event_dedup.db")
    live_db.init_db(db_file)

    live_db.insert_order(
        {
            "reference_id": "EVT-OPEN-1",
            "event_id": "EVT-OPEN",
            "sport": "soccer",
            "stake": 1.0,
            "status": "PENDING",
            "reject_reason": "status=PENDING_ACCEPTANCE",
        },
        db_file=db_file,
    )
    live_db.insert_order(
        {
            "reference_id": "EVT-OPEN-2",
            "event_id": "EVT-OPEN",
            "sport": "soccer",
            "stake": 1.0,
            "status": "ACCEPTED",
        },
        db_file=db_file,
    )

    # EVT-OPEN-2 已结算后，不应继续计入“未结算订单”
    live_db.insert_result(
        {
            "reference_id": "EVT-OPEN-2",
            "event_id": "EVT-OPEN",
            "stake": 1.0,
            "pnl": 0.2,
            "outcome": "WIN",
        },
        db_file=db_file,
    )

    open_cnt = live_db.count_unsettled_orders_for_event(
        event_id="EVT-OPEN",
        sport="soccer",
        statuses=["PENDING", "ACCEPTED"],
        db_file=db_file,
    )
    assert open_cnt == 1
    assert live_db.has_open_order_for_event(
        event_id="EVT-OPEN",
        sport="soccer",
        statuses=["PENDING", "ACCEPTED"],
        db_file=db_file,
    )

    # ACCEPTED 更新应清除历史 reject_reason（避免“已接单但原因仍是 pending”污染诊断）
    live_db.update_order_status(
        "EVT-OPEN-1",
        "ACCEPTED",
        executed_price=1.95,
        db_file=db_file,
    )
    conn = live_db.get_connection(db_file)
    row = conn.execute(
        "SELECT status, COALESCE(reject_reason, '') AS reject_reason FROM orders WHERE reference_id=?",
        ("EVT-OPEN-1",),
    ).fetchone()
    conn.close()

    assert row["status"] == "ACCEPTED"
    assert row["reject_reason"] == ""
