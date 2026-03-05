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
