from unittest.mock import MagicMock

from cloudbet_client import CloudbetClient


def test_get_events_forwards_status_and_markets():
    client = CloudbetClient("dummy")
    client._get = MagicMock(return_value={"events": []})

    client.get_events(
        "soccer-england-premier-league",
        markets=["soccer.total_goals"],
        status="TRADING_LIVE",
    )

    client._get.assert_called_once_with(
        "/pub/v2/odds/competitions/soccer-england-premier-league",
        params={"markets": ["soccer.total_goals"], "status": "TRADING_LIVE"},
    )


def test_get_live_events_requests_live_status_and_filters():
    client = CloudbetClient("dummy")
    client.get_events = MagicMock(
        return_value={
            "events": [
                {"status": "TRADING_LIVE", "id": "1"},
                {"status": "TRADING", "id": "2"},
            ]
        }
    )

    result = client.get_live_events("soccer-england-premier-league", ["soccer.total_goals"])

    client.get_events.assert_called_once_with(
        "soccer-england-premier-league", ["soccer.total_goals"], status="TRADING_LIVE"
    )
    assert result == [{"status": "TRADING_LIVE", "id": "1"}]



def test_extract_competition_keys_supports_categories_shape():
    payload = {
        "categories": [
            {
                "name": "England",
                "competitions": [
                    {"key": "soccer-england-premier-league", "name": "Premier League"},
                    {"key": "soccer-virtual-123", "name": "Virtual Soccer"},
                ],
            },
            {
                "name": "Spain",
                "competitions": [
                    {"key": "soccer-spain-la-liga", "name": "La Liga"},
                ],
            },
        ]
    }

    keys = CloudbetClient.extract_competition_keys(payload)

    assert keys == ["soccer-england-premier-league", "soccer-spain-la-liga"]


def test_get_events_by_time_forwards_window_and_markets():
    client = CloudbetClient("dummy")
    client._get = MagicMock(return_value={"competitions": []})

    client.get_events_by_time(
        sport_key="soccer",
        from_ts=100,
        to_ts=200,
        markets=["soccer.total_goals"],
    )

    client._get.assert_called_once_with(
        "/pub/v2/odds/events",
        params={
            "sport": "soccer",
            "from": 100,
            "to": 200,
            "markets": ["soccer.total_goals"],
        },
    )


def test_get_all_live_soccer_bulk_api_accepts_trading_and_live():
    client = CloudbetClient("dummy")
    client.get_events_by_time = MagicMock(
        return_value={
            "competitions": [
                {
                    "key": "soccer-test-league",
                    "name": "Test League",
                    "events": [
                        {"id": "1", "status": "TRADING_LIVE"},
                        {"id": "2", "status": "TRADING"},
                        {"id": "3", "status": "PRE_TRADING"},
                    ],
                }
            ]
        }
    )

    events = client.get_all_live_soccer(
        markets=["soccer.total_goals"],
        hydrate_live_events=False,
    )

    client.get_events_by_time.assert_called_once()
    assert [e["id"] for e in events] == ["1", "2"]
    assert events[0]["_competition_key"] == "soccer-test-league"
    assert events[0]["_competition_name"] == "Test League"


def test_get_all_live_soccer_legacy_league_scan_accepts_trading_and_live():
    client = CloudbetClient("dummy")
    client.get_competitions = MagicMock(return_value={"categories": []})
    client.extract_competition_keys = MagicMock(return_value=["soccer-test-league"])
    client.get_events = MagicMock(
        return_value={
            "name": "Test League",
            "events": [
                {"id": "1", "status": "TRADING_LIVE"},
                {"id": "2", "status": "TRADING"},
                {"id": "3", "status": "PRE_TRADING"},
            ],
        }
    )

    events = client.get_all_live_soccer(
        markets=["soccer.total_goals"],
        prefer_bulk_events_api=False,
    )

    client.get_events.assert_called_once_with(
        "soccer-test-league", ["soccer.total_goals"], status=None
    )
    assert [e["id"] for e in events] == ["1", "2"]
    assert events[0]["_competition_key"] == "soccer-test-league"
    assert events[0]["_competition_name"] == "Test League"

def test_get_all_live_soccer_bulk_hydrates_trading_live_event():
    client = CloudbetClient("dummy")
    client.get_events_by_time = MagicMock(
        return_value={
            "competitions": [
                {
                    "key": "soccer-test-league",
                    "name": "Test League",
                    "events": [
                        {"id": "1", "status": "TRADING_LIVE", "markets": []},
                        {"id": "2", "status": "TRADING", "markets": []},
                    ],
                }
            ]
        }
    )
    client.get_event = MagicMock(
        return_value={"id": "1", "status": "TRADING_LIVE", "markets": [{"key": "soccer.total_goals"}]}
    )

    events = client.get_all_live_soccer(markets=["soccer.total_goals"])

    client.get_event.assert_called_once_with("1")
    assert [e["id"] for e in events] == ["1", "2"]
    assert events[0]["_competition_key"] == "soccer-test-league"

