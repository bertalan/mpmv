"""Feature flags and kill switches (ARC.8): the registry, the resolution rules and a small TTL
cache. Pure Python, no ORM; storage and `flag_enabled` live in `apps.core.services`.

Every flag is declared in code with `register(FlagSpec(...))`, so a typo in a name fails loudly
instead of reading as "off". Its value is resolved in this order, each step overriding the
previous one when it says something:

1. the code default (`FlagSpec.default`);
2. the environment default, `settings.FEATURE_FLAGS` (from `MPMV_FEATURE_FLAGS`, e.g.
   `checkout=off`);
3. the staff switch, the `FeatureFlag` row edited in the admin: `default` (keep the value above),
   `on` or `off`;
4. a vendor override (`VendorFeatureFlag`, apps.vendors): on or off for one vendor.

A kill switch is a flag that is on by default and is turned off to stop something in an
emergency. A vendor override can turn it off for that vendor, but never back on when the
platform value is off: pulling a kill switch stops the feature for everyone.
"""

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

DEFAULT = "default"
ON = "on"
OFF = "off"
STATES = (DEFAULT, ON, OFF)

NAME_MAX_LENGTH = 64
_NAME = re.compile(r"[a-z][a-z0-9_]*")


class UnknownFlag(KeyError):
    """No flag of that name is registered."""


@dataclass(frozen=True)
class FlagSpec:
    name: str
    description: str
    default: bool = False
    kill_switch: bool = False

    def __post_init__(self) -> None:
        if len(self.name) > NAME_MAX_LENGTH or not _NAME.fullmatch(self.name):
            raise ValueError(
                f"Flag name {self.name!r} must be a lowercase identifier of at most "
                f"{NAME_MAX_LENGTH} characters"
            )


_REGISTRY: dict[str, FlagSpec] = {}


def register(spec: FlagSpec) -> FlagSpec:
    """Declare a flag. Apps call this at import time of their own module (or `ready()`)."""
    if spec.name in _REGISTRY:
        raise ValueError(f"Flag {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec
    return spec


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def spec_of(name: str) -> FlagSpec:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownFlag(name) from None


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def registered_flags() -> tuple[FlagSpec, ...]:
    return tuple(sorted(_REGISTRY.values(), key=lambda spec: spec.name))


# --- The kill switches (spec 01 ARC.8). Later code checks them with
# `apps.core.services.flag_enabled(CHECKOUT, vendor=...)`.

CHECKOUT = register(
    FlagSpec(
        "checkout",
        "Kill switch: customers can check out and place orders.",
        default=True,
        kill_switch=True,
    )
).name
IMPORT = register(
    FlagSpec(
        "import",
        "Kill switch: supplier catalog imports run and write to the catalog.",
        default=True,
        kill_switch=True,
    )
).name
PAYMENTS = register(
    FlagSpec(
        "payments",
        "Kill switch: payments are taken and refunds and vendor transfers are sent.",
        default=True,
        kill_switch=True,
    )
).name


def platform_value(spec: FlagSpec, *, environment: bool | None, state: str) -> bool:
    """The value for the whole platform: code default, environment default, staff switch."""
    if state not in STATES:
        raise ValueError(f"Unknown flag state {state!r}")
    if state == ON:
        return True
    if state == OFF:
        return False
    return spec.default if environment is None else environment


def resolve(spec: FlagSpec, *, environment: bool | None, state: str, vendor: bool | None) -> bool:
    """The value of `spec` for one vendor (`vendor` is its override, None when it has none)."""
    value = platform_value(spec, environment=environment, state=state)
    if vendor is None:
        return value
    if spec.kill_switch:
        return value and vendor  # an override may stop it for one vendor, never revive it
    return vendor


@dataclass(frozen=True)
class FlagSnapshot:
    """The stored switches and vendor overrides at one moment, by flag name (and vendor pk)."""

    states: Mapping[str, str] = field(default_factory=dict)
    vendor_overrides: Mapping[tuple[str, int], bool] = field(default_factory=dict)

    def enabled(self, spec: FlagSpec, *, environment: bool | None, vendor_id: int | None) -> bool:
        override = None if vendor_id is None else self.vendor_overrides.get((spec.name, vendor_id))
        return resolve(
            spec,
            environment=environment,
            state=self.states.get(spec.name, DEFAULT),
            vendor=override,
        )


class TtlCache[T]:
    """One value, reloaded when older than `ttl` seconds (0: never cached). Per process."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entry: tuple[float, T] | None = None

    def get(self, load: Callable[[], T], *, ttl: float) -> T:
        now = self._clock()
        entry = self._entry
        if ttl > 0 and entry is not None and now - entry[0] < ttl:
            return entry[1]
        value = load()
        self._entry = (now, value) if ttl > 0 else None
        return value

    def clear(self) -> None:
        self._entry = None
