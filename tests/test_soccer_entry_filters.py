import time

from soccer_strategy import (
    _odds_cache,
    _recent_best_price,
    _is_price_chasing_recent_best,
    _robust_edge_after_price_drop,
)


def test_recent_best_price_uses_side_and_window():
    event_id = "evt-test-price"
    _odds_cache.pop(event_id, None)
    now = time.time()
    _odds_cache[event_id] = [
        (now - 5, 1.90, 1.95),
        (now - 3, 2.02, 1.80),
        (now - 1, 1.88, 2.10),
    ]

    best_over = _recent_best_price(event_id, "over", window_secs=10)
    best_under = _recent_best_price(event_id, "under", window_secs=10)

    assert best_over == 2.02
    assert best_under == 2.10



def test_is_price_chasing_recent_best_threshold():
    assert _is_price_chasing_recent_best(
        current_price=1.80,
        recent_best_price=2.00,
        worse_tolerance=0.02,
    ) is True

    assert _is_price_chasing_recent_best(
        current_price=1.97,
        recent_best_price=2.00,
        worse_tolerance=0.02,
    ) is False



def test_robust_edge_after_price_drop_is_more_conservative():
    base_edge = 0.58 - (1.0 / 2.10)
    robust_edge = _robust_edge_after_price_drop(
        model_prob=0.58,
        market_price=2.10,
        adverse_price_delta=0.10,
    )
    assert robust_edge < base_edge
    assert round(robust_edge, 4) == round(0.58 - (1.0 / 2.00), 4)
