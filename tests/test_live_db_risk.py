from pathlib import Path

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
