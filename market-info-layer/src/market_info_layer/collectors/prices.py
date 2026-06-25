from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    price_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    source: str


class PriceProvider(ABC):
    @abstractmethod
    def daily_prices(self, ticker: str) -> list[PriceBar]:
        raise NotImplementedError


class PlaceholderPriceProvider(PriceProvider):
    def daily_prices(self, ticker: str) -> list[PriceBar]:
        return []
