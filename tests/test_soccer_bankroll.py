import soccer_bot


def _cfg():
    cfg = dict(soccer_bot.SOCCER_CONFIG)
    cfg["db_file"] = "test.db"
    return cfg


def test_compute_bankroll_profile_scales_down_on_drawdown_and_vol(monkeypatch):
    cfg = _cfg()
    cfg["_peak_bankroll"] = 1000.0

    # 较高波动收益率序列
    returns = [0.06, -0.05, 0.04, -0.04] * 10
    monkeypatch.setattr(
        soccer_bot.live_db,
        "get_recent_result_returns",
        lambda window, db_file, sport=None: returns,
    )
    monkeypatch.setattr(
        soccer_bot.live_db,
        "get_open_exposure",
        lambda db_file, include_statuses=None, sport=None: 8.0,
    )

    profile = soccer_bot.compute_bankroll_profile(cfg, bankroll=800.0)

    assert round(profile["drawdown"], 3) == 0.2
    assert profile["kelly_fraction"] <= cfg["kelly_fraction"]
    assert profile["available_round_budget"] >= 0
    assert profile["available_round_budget"] <= profile["round_budget"]


def test_size_stake_scientific_respects_budget_and_min_stake():
    cfg = _cfg()
    profile = {
        "base_kelly": 0.20,
        "kelly_fraction": 0.10,
        "bankroll": 1000.0,
        "available_round_budget": 3.0,
    }
    signal = {
        "stake": 6.0,
        "edge": 0.12,
        "max_stake": 9999,
        "min_stake": 1.0,
    }

    stake, _ = soccer_bot.size_stake_scientific(cfg, signal, profile, used_round_budget=0.0)
    assert 1.0 <= stake <= 3.0

    # 剩余预算不足最小下注额时应跳过
    stake2, meta2 = soccer_bot.size_stake_scientific(cfg, signal, profile, used_round_budget=2.6)
    assert stake2 == 0.0
    assert meta2["reason"] == "below_min_stake_or_budget"

