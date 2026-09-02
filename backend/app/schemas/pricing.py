"""Dashboard, price history and alert request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

AlertScope = Literal["owned", "card"]
AlertDirection = Literal["above", "below", "pct_up", "pct_down"]


class AlertRequest(BaseModel):
    """Body of ``POST /api/alerts`` and ``PATCH /api/alerts/{id}``.

    A rule needs a threshold that matches its direction: an absolute price for
    ``above``/``below``, a percentage for ``pct_up``/``pct_down``. Accepting a rule with
    the wrong one would create an alert that silently never fires.
    """

    scope: AlertScope = "card"
    card_id: int | None = None
    direction: AlertDirection
    threshold_cents: int | None = Field(default=None, ge=0)
    threshold_pct: float | None = Field(default=None, gt=0, le=1000)
    cooldown_days: int = Field(default=7, ge=0, le=365)
    active: bool = True

    @model_validator(mode="after")
    def _threshold_matches_direction(self) -> AlertRequest:
        """Reject rules that could never fire."""
        absolute = self.direction in ("above", "below")
        if absolute and self.threshold_cents is None:
            raise ValueError(f"{self.direction} alerts need threshold_cents")
        if not absolute and self.threshold_pct is None:
            raise ValueError(f"{self.direction} alerts need threshold_pct")
        if self.scope == "card" and self.card_id is None:
            raise ValueError("card-scoped alerts need a card_id")
        return self


class AlertPatch(BaseModel):
    """Body of ``PATCH /api/alerts/{id}``. Omitted fields are unchanged."""

    active: bool | None = None
    threshold_cents: int | None = Field(default=None, ge=0)
    threshold_pct: float | None = Field(default=None, gt=0, le=1000)
    cooldown_days: int | None = Field(default=None, ge=0, le=365)
