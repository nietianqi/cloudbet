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