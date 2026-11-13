"""The anonymous garage cookie (D-38 Q-G1, FIT.6): pure rules, no ORM, no request.

The cookie `mpmv_garage` is technical: written only when the visitor presses *Save*, it holds up
to three vehicles as make, model and year labels (no identifier, no name, no free text beyond
those labels), it lives at most 12 months and the visitor can remove a vehicle at any time. The
value is plain data: it is never trusted (`parse` treats anything odd as an empty garage) and
never signed, since it holds nothing a visitor could gain by forging.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

COOKIE_NAME = "mpmv_garage"
VERSION = 1
MAX_VEHICLES = 3
MAX_LABEL = 60
MAX_VALUE_LENGTH = 1024  # a larger value is not ours
DEFAULT_DAYS = 180
MAX_DAYS = 365  # 12 months (Q-G1)

#: The label next to the *Save* control (spec 17, FIT.6): wording fixed by the owner decision.
SAVE_LABEL = (
    "Save this vehicle on this browser for your next visits (you can remove it at any time)"
)

_TOP_KEYS = {"v", "vehicles", "active"}
_VEHICLE_KEYS = {"make", "model", "year"}
_FORBIDDEN = set("<>\"'`\\&{}")


class GarageFull(ValueError):
    """The garage already holds the maximum number of vehicles."""


@dataclass(frozen=True)
class Vehicle:
    make: str
    model: str
    year: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"make": self.make, "model": self.model, "year": self.year}


@dataclass(frozen=True)
class Garage:
    vehicles: tuple[Vehicle, ...]
    active: int | None

    def active_snapshot(self) -> dict[str, str] | None:
        """The active vehicle as the labels an order keeps (FIT.17); None without one."""
        if self.active is None or not self.vehicles:
            return None
        return self.vehicles[self.active].as_dict()


EMPTY = Garage((), None)


def clean_label(value: object, *, required: bool = True) -> str | None:
    """A label as stored, or None when it is not acceptable: text, one line, a sane length, no
    markup or quoting characters."""
    if not isinstance(value, str):
        return None
    if any(ord(c) < 32 or ord(c) == 127 or c in _FORBIDDEN for c in value):
        return None
    text = " ".join(value.split())
    if len(text) > MAX_LABEL or (required and not text):
        return None
    return text


def make_vehicle(make: object, model: object, year: object = "") -> Vehicle | None:
    clean_make, clean_model = clean_label(make), clean_label(model)
    clean_year = clean_label(year if year is not None else "", required=False)
    if clean_make is None or clean_model is None or clean_year is None:
        return None
    return Vehicle(clean_make, clean_model, clean_year)


def parse(raw: str | None) -> Garage:
    """The garage a cookie value stands for; anything unexpected is an empty garage."""
    if not raw or len(raw) > MAX_VALUE_LENGTH:
        return EMPTY
    try:
        payload: Any = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeError):
        return EMPTY
    if not isinstance(payload, dict) or payload.get("v") != VERSION:
        return EMPTY
    if not set(payload) <= _TOP_KEYS:
        return EMPTY  # any other field makes the cookie invalid (spec 17 section 17.4.2)
    items = payload.get("vehicles")
    if not isinstance(items, list):
        return EMPTY
    vehicles: list[Vehicle] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != _VEHICLE_KEYS:
            return EMPTY
        vehicle = make_vehicle(item.get("make"), item.get("model"), item["year"])
        if vehicle is None:
            return EMPTY
        if vehicle not in vehicles:
            vehicles.append(vehicle)
    vehicles = vehicles[:MAX_VEHICLES]
    if not vehicles:
        return EMPTY
    active = payload.get("active")
    if not isinstance(active, int) or isinstance(active, bool) or not 0 <= active < len(vehicles):
        active = 0
    return Garage(tuple(vehicles), active)


def dumps(value: Garage) -> str:
    """The cookie value: make, model and year of each vehicle, nothing else."""
    payload = {
        "v": VERSION,
        "vehicles": [vehicle.as_dict() for vehicle in value.vehicles],
        "active": value.active,
    }
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(text.encode("ascii")).decode("ascii")


def save(value: Garage, vehicle: Vehicle) -> Garage:
    """Add the vehicle and make it the active one; one already there becomes the active one."""
    if vehicle in value.vehicles:
        return Garage(value.vehicles, value.vehicles.index(vehicle))
    if len(value.vehicles) >= MAX_VEHICLES:
        raise GarageFull(f"the garage holds at most {MAX_VEHICLES} vehicles")
    return Garage((*value.vehicles, vehicle), len(value.vehicles))


def remove(value: Garage, index: int) -> Garage:
    """Drop one vehicle at once; an unknown index changes nothing."""
    if not 0 <= index < len(value.vehicles):
        return value
    vehicles = value.vehicles[:index] + value.vehicles[index + 1 :]
    if not vehicles:
        return EMPTY
    active = value.active if value.active is not None else 0
    if active > index:
        active -= 1
    elif active == index:
        active = 0
    return Garage(vehicles, min(active, len(vehicles) - 1))


def cookie_max_age(days: int) -> int:
    """The cookie lifetime in seconds; more than 12 months (or none) is refused (Q-G1)."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"the garage cookie lives from 1 day to 12 months ({MAX_DAYS} days)")
    return days * 86400
