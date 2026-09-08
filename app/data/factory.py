from __future__ import annotations

from .base import MarketDataProvider
from .dukascopy import DukascopyClient, DukascopyProvider
from ..config import Settings
from ..data_twelvedata import TwelveDataProvider
from ..dxy import DXY_COMPONENTS


def create_provider(settings: Settings) -> MarketDataProvider:
    if settings.data_provider == "dukascopy":
        client = DukascopyClient(base_url=settings.dukascopy_base_url)
        return DukascopyProvider(
            settings.data_dir,
            validation_mode=settings.data_validation_mode,
            anchor_timezone=settings.htf_anchor_timezone,
            client=client,
            workers=settings.dukascopy_workers,
        )
    if settings.data_provider == "twelvedata":
        return TwelveDataProvider(settings)
    raise ValueError(f"Unsupported DATA_PROVIDER: {settings.data_provider}")


def required_symbols(settings: Settings) -> list[str]:
    symbols = ["EUR_USD", "GBP_USD"]
    if settings.dxy_source == "dukascopy_direct" and settings.data_provider == "dukascopy":
        symbols.append("DXY")
    else:
        symbols.extend(DXY_COMPONENTS)
    return list(dict.fromkeys(symbols))
