from pathlib import Path
from typing import Any

import requests
import yaml
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from market_info_layer.db.models import MacroObservation
from market_info_layer.settings import ROOT_DIR, get_settings
from market_info_layer.utils.time import utc_now_iso

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def read_series_config(path: Path | None = None) -> list[dict[str, str | None]]:
    data = yaml.safe_load((path or ROOT_DIR / "config" / "macro_series.yaml").read_text()) or {}
    configured = data.get("series", [])
    series: list[dict[str, str | None]] = []
    for item in configured:
        if isinstance(item, str):
            series.append({"series_id": item, "name": None})
        elif isinstance(item, dict):
            series_id = item.get("series_id") or item.get("id")
            if series_id:
                series.append({"series_id": str(series_id), "name": item.get("name")})
    return series


def read_series(path: Path | None = None) -> list[str]:
    return [item["series_id"] for item in read_series_config(path) if item["series_id"]]


def _value(raw: Any) -> float | None:
    if raw in {None, ".", ""}:
        return None
    return float(raw)


def _observation_exists(session: Session, series_id: str, observation_date: str) -> bool:
    return (
        session.scalar(
            select(MacroObservation.id).where(
                MacroObservation.series_id == series_id,
                MacroObservation.observation_date == observation_date,
            )
        )
        is not None
    )


def collect_macro_observations(session: Session, series_path: Path | None = None) -> int:
    inserted = 0
    settings = get_settings()
    collected_at = utc_now_iso()
    for series_id in read_series(series_path):
        params = {"series_id": series_id, "file_type": "json"}
        if settings.fred_api_key:
            params["api_key"] = settings.fred_api_key
        response = requests.get(FRED_URL, params=params, timeout=30)
        response.raise_for_status()
        for obs in response.json().get("observations", []):
            observation_date = obs.get("date")
            if not observation_date or _observation_exists(session, series_id, observation_date):
                continue
            session.add(
                MacroObservation(
                    series_id=series_id,
                    observation_date=observation_date,
                    value=_value(obs.get("value")),
                    realtime_start=obs.get("realtime_start"),
                    realtime_end=obs.get("realtime_end"),
                    source="FRED",
                    collected_at=collected_at,
                )
            )
            try:
                session.commit()
                inserted += 1
            except IntegrityError:
                session.rollback()
    return inserted


def latest_macro_values(session: Session, series_path: Path | None = None) -> list[dict[str, Any]]:
    latest: list[dict[str, Any]] = []
    for configured in read_series_config(series_path):
        series_id = configured["series_id"]
        if not series_id:
            continue
        observation = session.scalars(
            select(MacroObservation)
            .where(MacroObservation.series_id == series_id)
            .order_by(
                MacroObservation.observation_date.desc(), MacroObservation.collected_at.desc()
            )
            .limit(1)
        ).first()
        latest.append(
            {
                "series_id": series_id,
                "name": configured.get("name"),
                "observation_date": observation.observation_date if observation else None,
                "value": observation.value if observation else None,
                "collected_at": observation.collected_at if observation else None,
            }
        )
    return latest
