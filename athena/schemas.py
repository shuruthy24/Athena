"""Data contracts for every table Athena reads (AGENTS.md section 6). pipeline/check.py validates each row."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Band = Literal["18", "22", "01"]
Mode = Literal["walk", "bike", "transit"]


class Street(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = Field(min_length=1)
    highway: str = Field(min_length=1)
    lit: Optional[Literal["yes", "no"]]          # None = unknown, never coerced to "no"
    tunnel: bool
    risk_18: Optional[float] = Field(ge=0, le=1)
    risk_22: Optional[float] = Field(ge=0, le=1)
    risk_01: Optional[float] = Field(ge=0, le=1)
    venues_18: Optional[int] = Field(ge=0)
    venues_22: Optional[int] = Field(ge=0)
    venues_01: Optional[int] = Field(ge=0)
    lights_per_100m: Optional[float] = Field(default=None, ge=0)


class RouteStep(BaseModel):
    model_config = ConfigDict(extra="allow")
    trip: str
    band: Band
    mode: Mode
    variant: Literal["fastest", "athena"]
    duration_min: float = Field(ge=0)
    leg: int = Field(ge=0)
    leg_mode: str
    step: int = Field(ge=0)
    street: Optional[str]
    distance_m: float = Field(ge=0)


class RouteLeg(BaseModel):
    model_config = ConfigDict(extra="allow")
    journey: str
    trip: str
    band: Band
    mode: Mode
    variant: str
    itinerary: int = Field(ge=0)
    total_min: float = Field(ge=0)
    leg: int = Field(ge=0)
    leg_mode: str
    distance_m: float = Field(ge=0)


class LegStreet(BaseModel):
    model_config = ConfigDict(extra="allow")
    journey: str
    trip: str
    band: Band
    mode: Mode
    itinerary: int = Field(ge=0)
    leg: int = Field(ge=0)
    step: int = Field(ge=0)
    street: Optional[str]
    distance_m: float = Field(ge=0)


class Place(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    lat: float = Field(ge=-34.2, le=-33.6)
    lon: float = Field(ge=150.9, le=151.4)


class Report(BaseModel):
    """A labelled street report (Step 3). Text has PII removed before it is stored."""
    id: str
    street: str
    band: Band
    text: str = Field(min_length=1)
    category: Literal["lighting", "isolation", "harassment", "infrastructure", "positive", "other"]
    action: Literal["approve", "review", "reject"]   # human label; redact is mechanical
    source: Literal["collected", "seed", "synthetic"]
    author_group: str = Field(min_length=1)          # pseudonymous group for the split, never a name
    created_at: datetime


TABLES = {"streets": Street, "route_steps": RouteStep, "route_legs": RouteLeg, "leg_streets": LegStreet,
          "places": Place}
