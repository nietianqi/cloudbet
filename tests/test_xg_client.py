from unittest.mock import patch

from xg_client import APIFootballClient, NullXGClient, create_xg_client


def test_create_xg_client_prefers_new_env_var():
    with patch.dict(
        "os.environ",
        {"API_FOOTBALL_KEY": "new-key", "APIFOOTBALL_KEY": "legacy-key"},
        clear=True,
    ):
        client = create_xg_client()
        assert isinstance(client, APIFootballClient)
        assert client.api_key == "new-key"


def test_create_xg_client_falls_back_to_legacy_env_var():
    with patch.dict("os.environ", {"APIFOOTBALL_KEY": "legacy-key"}, clear=True):
        client = create_xg_client()
        assert isinstance(client, APIFootballClient)
        assert client.api_key == "legacy-key"


def test_create_xg_client_uses_null_client_without_keys():
    with patch.dict("os.environ", {}, clear=True):
        client = create_xg_client()
        assert isinstance(client, NullXGClient)
