from pathlib import Path

import requests
import yaml
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from market_info_layer.db.models import MacroObservation
from market_info_layer.settings import ROOT_DIR, get_settings
from market_info_layer.utils.time import utc_now_iso

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def read_series(path: Path | None = None) -> list[str]:
    data = yaml.safe_load((path or ROOT_DIR / "config" / "macro_series.yaml").read_text()) or {}
    return data.get("series", [])


def collect_macro_observations(session: Session, series_path: Path | None = None) -> int:
    inserted = 0
    settings = get_settings()
    for series_id in read_series(series_path):
        params = {"series_id": series_id, "file_type": "json"}
        if settings.fred_api_key:
            params["api_key"] = settings.fred_api_key
        response = requests.get(FRED_URL, params=params, timeout=30)
        response.raise_for_status()
        for obs in response.json().get("observations", []):
            value = None if obs.get("value") in {None, "."} else float(obs["value"])
            session.add(
                MacroObservation(
                    series_id=series_id,
                    observation_date=obs["date"],
                    value=value,
                    realtime_start=obs.get("realtime_start"),
                    realtime_end=obs.get("realtime_end"),
                    source="FRED",
                    collected_at=utc_now_iso(),
                )
            )
            try:
                session.commit()
                inserted += 1
            except IntegrityError:
                session.rollback()
    return inserted
