from soccer_strategy import (
    _extract_country_from_comp_key,
    _infer_domestic_league_tier,
    _is_allowed_by_fifa_country_tier,
    _normalize_country_slug_for_fifa,
)


def test_country_slug_aliases_for_fifa_filter():
    assert _normalize_country_slug_for_fifa("Turkey") == "turkiye"
    assert _normalize_country_slug_for_fifa("Republic Of Korea") == "korea-republic"
    assert _normalize_country_slug_for_fifa("USA") == "usa"


def test_extract_country_prefers_longest_known_slug():
    known = {"bosnia", "bosnia-herzegovina", "england"}
    assert (
        _extract_country_from_comp_key(
            "soccer-bosnia-herzegovina-premijer-liga", known
        )
        == "bosnia-herzegovina"
    )


def test_fifa_rule_top40_can_include_second_tier():
    allowed, reason = _is_allowed_by_fifa_country_tier(
        country_slug="england",
        tier=2,
        allow_second_tier_for_top40=True,
    )
    assert allowed is True
    assert reason == "ok_top40"


def test_fifa_rule_top150_non_top40_second_tier_rejected():
    allowed, reason = _is_allowed_by_fifa_country_tier(
        country_slug="albania",
        tier=2,
        allow_second_tier_for_top40=True,
    )
    assert allowed is False
    assert reason == "tier_not_allowed"


def test_fifa_rule_country_outside_top150_rejected():
    allowed, reason = _is_allowed_by_fifa_country_tier(
        country_slug="san-marino",
        tier=1,
        allow_second_tier_for_top40=True,
    )
    assert allowed is False
    assert reason == "country_outside_top150"


def test_infer_league_tier_for_common_cases():
    assert _infer_domestic_league_tier(
        "soccer-england-premier-league", "Premier League", "england"
    ) == 1
    assert _infer_domestic_league_tier(
        "soccer-england-championship", "Championship", "england"
    ) == 2
    assert _infer_domestic_league_tier(
        "soccer-england-fa-cup", "FA Cup", "england"
    ) is None
    assert _infer_domestic_league_tier(
        "soccer-cyprus-1st-division", "1st Division", "cyprus"
    ) == 1
    assert _infer_domestic_league_tier(
        "soccer-denmark-1st-division", "1st Division", "denmark"
    ) == 2


def test_fifa_rule_domestic_cup_allowed_for_top150_country():
    allowed, reason = _is_allowed_by_fifa_country_tier(
        country_slug="england",
        tier=None,
        allow_second_tier_for_top40=True,
        comp_key="soccer-england-fa-cup",
        comp_name="FA Cup",
    )
    assert allowed is True
    assert reason == "ok_domestic_cup_top150"


def test_fifa_rule_international_cup_allowed():
    allowed, reason = _is_allowed_by_fifa_country_tier(
        country_slug="international-clubs",
        tier=None,
        allow_second_tier_for_top40=True,
        comp_key="soccer-international-clubs-uefa-champions-league",
        comp_name="UEFA Champions League",
    )
    assert allowed is True
    assert reason == "ok_international_cup"
