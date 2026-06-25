from pathlib import Path

from sqlalchemy.orm import Session

from market_info_layer.collectors.fred_macro import collect_macro_observations, latest_macro_values
from market_info_layer.db.database import get_engine, init_db


class _Response:
    def json(self):
        return {
            "observations": [
                {
                    "date": "2024-01-01",
                    "value": "1.5",
                    "realtime_start": "2024-01-02",
                    "realtime_end": "2024-01-03",
                },
                {"date": "2024-02-01", "value": "."},
            ]
        }

    def raise_for_status(self):
        return None


def test_collect_macro_observations_prevents_duplicates_and_uses_api_key(tmp_path, monkeypatch):
    config = tmp_path / "macro_series.yaml"
    config.write_text("series:\n  - series_id: TEST\n    name: Test series\n")
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return _Response()

    monkeypatch.setenv("FRED_API_KEY", "fred-key")
    from market_info_layer.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("market_info_layer.collectors.fred_macro.requests.get", fake_get)
    db_url = f"sqlite:///{tmp_path / 'macro.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        assert collect_macro_observations(session, Path(config)) == 2
        assert collect_macro_observations(session, Path(config)) == 0
        latest = latest_macro_values(session, Path(config))

    assert calls[0][1]["api_key"] == "fred-key"
    assert latest == [
        {
            "series_id": "TEST",
            "name": "Test series",
            "observation_date": "2024-02-01",
            "value": None,
            "collected_at": latest[0]["collected_at"],
        }
    ]
