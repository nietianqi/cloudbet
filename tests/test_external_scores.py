from datetime import datetime, timedelta, timezone

from external_scores import match_external_score_for_event


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_match_external_score_for_event_matches_same_teams():
    kickoff = datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc)
    snapshots = [
        {
            "home_name": "Barcelona",
            "away_name": "Real Madrid",
            "home_score": 1,
            "away_score": 0,
            "elapsed_minutes": 37,
            "kickoff": kickoff,
            "competition": "LaLiga",
            "source": "api_football",
        }
    ]

    matched = match_external_score_for_event(
        event_home="FC Barcelona",
        event_away="Real Madrid CF",
        event_kickoff=_iso_utc(kickoff),
        event_competition="Spain La Liga",
        snapshots=snapshots,
        min_confidence=0.70,
        kickoff_tolerance_mins=120,
    )

    assert matched is not None
    assert matched["snapshot"]["home_score"] == 1
    assert matched["snapshot"]["away_score"] == 0
    assert matched["confidence"] >= 0.70


def test_match_external_score_for_event_rejects_swapped_teams():
    kickoff = datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc)
    snapshots = [
        {
            "home_name": "Chelsea",
            "away_name": "Arsenal",
            "home_score": 1,
            "away_score": 2,
            "elapsed_minutes": 55,
            "kickoff": kickoff,
            "competition": "Premier League",
            "source": "api_football",
        }
    ]

    matched = match_external_score_for_event(
        event_home="Arsenal",
        event_away="Chelsea",
        event_kickoff=_iso_utc(kickoff),
        event_competition="Premier League",
        snapshots=snapshots,
        min_confidence=0.60,
        kickoff_tolerance_mins=120,
    )

    assert matched is None


def test_match_external_score_for_event_respects_kickoff_tolerance():
    kickoff = datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc)
    snapshots = [
        {
            "home_name": "Lakers",
            "away_name": "Warriors",
            "home_score": 68,
            "away_score": 64,
            "elapsed_minutes": 26,
            "kickoff": kickoff + timedelta(hours=7),
            "competition": "NBA",
            "source": "api_basketball",
        }
    ]

    matched = match_external_score_for_event(
        event_home="Los Angeles Lakers",
        event_away="Golden State Warriors",
        event_kickoff=_iso_utc(kickoff),
        event_competition="NBA",
        snapshots=snapshots,
        min_confidence=0.60,
        kickoff_tolerance_mins=120,
    )

    assert matched is None
