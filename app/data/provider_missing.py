"""Evidence about absent provider bars. Never supplies candles to a strategy."""
from datetime import timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ConfirmationPolicy(EvidenceModel):
    confirm_provider_missing: bool = False
    recheck_provider_missing: bool = False

    @model_validator(mode="after")
    def require_explicit_confirmation(self):
        if self.recheck_provider_missing and not self.confirm_provider_missing:
            raise ValueError("Rechecking provider absence requires explicit confirmation")
        return self


class PayloadEvidence(EvidenceModel):
    endpoint: str
    http_status: Literal[200]
    fetched_at: AwareDatetime
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MissingCheck(EvidenceModel):
    bid: PayloadEvidence
    ask: PayloadEvidence
    bid_present: Literal[False] = False
    ask_present: Literal[False] = False


class BoundaryObservation(EvidenceModel):
    timestamp: AwareDatetime
    available_at: AwareDatetime
    field: Literal["open", "close"]
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)

    @model_validator(mode="after")
    def causal_availability(self):
        # Conservative OHLC policy: even an open is exposed only once its M1 closes.
        if self.available_at < self.timestamp + timedelta(minutes=1):
            raise ValueError("Boundary metadata cannot precede the source candle close")
        return self


class ProviderMissingConfirmation(EvidenceModel):
    version: Literal["DUKASCOPY_MISSING_V1"] = "DUKASCOPY_MISSING_V1"
    classification: Literal["PROVIDER_CONFIRMED_MISSING"] = "PROVIDER_CONFIRMED_MISSING"
    symbol: str
    provider_symbol: str
    timestamp: AwareDatetime
    confirmed_at: AwareDatetime
    checks: list[MissingCheck] = Field(min_length=2, max_length=2)
    before: BoundaryObservation | None = None
    after: BoundaryObservation | None = None

    @model_validator(mode="after")
    def validate_evidence(self):
        t = self.timestamp
        if t.utcoffset() != timedelta(0) or t.second or t.microsecond:
            raise ValueError("Confirmation timestamp must be a UTC minute")
        for check in self.checks:
            for side in ("bid", "ask"):
                evidence = getattr(check, side)
                expected = f"/candles/minute/{self.provider_symbol}/{side.upper()}/{t.year}/{t.month}/{t.day}"
                if evidence.endpoint != expected or not t + timedelta(minutes=1) <= evidence.fetched_at <= self.confirmed_at:
                    raise ValueError("Confirmation evidence must concern the closed UTC day/minute")
        if min(self.checks[1].bid.fetched_at, self.checks[1].ask.fetched_at) <= max(self.checks[0].bid.fetched_at, self.checks[0].ask.fetched_at):
            raise ValueError("Confirmation requires two successive independent fetches")
        if self.before and (self.before.timestamp >= t or self.before.field != "close"):
            raise ValueError("Before boundary must be a preceding real close")
        if self.after and (self.after.timestamp <= t or self.after.field != "open"):
            raise ValueError("After boundary must be a following real open")
        return self

    def boundaries_at(self, at):
        """Expose only boundary observations available at the requested market time."""
        return {
            side: observation.model_dump(mode="json") if observation and observation.available_at <= at else None
            for side in ("before", "after")
            for observation in (getattr(self, side),)
        }
