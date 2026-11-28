"""Supplier adapter contracts (ING.9, spec 08 "Contracts").

Pure types, no ORM. Two shapes exist:

* `SupplierAdapter`: network adapters that discover and fetch items one by one (HTML, Store
  API; ING.3). Fetching goes through the SSRF-guarded client (MIG.12), never through this module.
* `FileAdapter`: adapters that read one uploaded or watched file given as bytes (CSV, XLSX,
  feed, YAML). Fetching the file is out of scope here: the caller hands over the bytes.

Files are untrusted: every file adapter enforces `max_bytes` and `max_rows` before and while
parsing, and reports a bad row instead of raising, so one bad row never hides the others.
Parsing is a read: a dry-run is the default and writes nothing; the caller stores the result.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final, Protocol

from apps.ingest import engine

DEFAULT_MAX_BYTES: Final = 5 * 1024 * 1024
DEFAULT_MAX_ROWS: Final = 20_000
DEFAULT_MAX_COLUMNS: Final = 200

# The generic observation fields every file adapter can fill. `sku` and `title` are required.
FIELDS: Final = ("sku", "title", "description", "price", "stock_quantity", "url", "ean")
REQUIRED_FIELDS: Final = ("sku", "title")


class AdapterError(ValueError):
    """The whole file is refused (too large, unreadable, no usable header): nothing is read."""


@dataclass(frozen=True)
class SourceRef:
    """Where one item lives in its source (identity of the item, spec 08 `source_url`)."""

    identity: str
    locator: str = ""


@dataclass(frozen=True)
class RawObservation:
    """One item as the supplier gave it, JSON-safe: the future `SourceObservation.payload`."""

    ref: SourceRef
    payload: Mapping[str, Any]

    @property
    def payload_hash(self) -> str:
        return engine.payload_hash(self.payload)


@dataclass(frozen=True)
class ExtractedProduct:
    """The validated product data of one observation: the future `ProductDraft.extracted`."""

    sku: str
    title: str
    description: str = ""
    price: str | None = None  # decimal text, never a float
    stock_quantity: int | None = None
    url: str = ""
    ean: str = ""

    def as_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}


@dataclass(frozen=True)
class RowError:
    row: int  # 1-based position of the data row in the file (the header is row 0)
    code: str
    message: str


@dataclass(frozen=True)
class ParsedRow:
    observation: RawObservation
    product: ExtractedProduct


@dataclass
class AdapterResult:
    """What one file yielded: good rows, row errors and the file-level notes."""

    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    encoding: str = ""
    dry_run: bool = True

    def counters(self) -> dict[str, int]:
        """The ImportRun counters (`engine.RUN_COUNTERS`) this parse stands for."""
        return {
            "seen": len(self.rows) + len(self.errors),
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": len(self.errors),
            "errors": len(self.errors),
        }


class SupplierAdapter(Protocol):
    code: str

    def discover(self, source: Any) -> Iterable[SourceRef]: ...

    def fetch(self, ref: SourceRef) -> RawObservation: ...

    def extract(self, raw: RawObservation) -> ExtractedProduct: ...


class FileAdapter(Protocol):
    code: str

    def parse(self, data: bytes) -> AdapterResult: ...


class AmbiguousNumber(ValueError):
    """A number whose separators could mean thousands or decimals (e.g. "1,234")."""


MAX_AMOUNT: Final = Decimal(10) ** 9
FIELD_MAX_CHARS: Final = 65_536


def _is_grouped(text: str, sep: str) -> bool:
    """True for `1,234,567`: 1-3 leading digits (optional sign), then groups of exactly three.
    Plain string checks, no regex, so a hostile cell cannot cost more than linear time."""
    first, *groups = text.split(sep)
    digits = first[1:] if first[:1] in {"+", "-"} else first
    return (
        1 <= len(digits) <= 3
        and digits.isascii()
        and digits.isdigit()
        and bool(groups)
        and all(len(g) == 3 and g.isascii() and g.isdigit() for g in groups)
    )


def parse_decimal(raw: str, decimal: str | None = None) -> Decimal:
    """A finite decimal with |value| <= 10**9, or ValueError.

    `decimal` ("," or ".") says which separator is the decimal one; the other is a thousands
    separator. Without it: when both appear the last one is the decimal; a single separator
    repeated, in groups of three, is thousands; a single one followed by exactly three digits
    (not after a leading 0) is ambiguous and refused, any other single separator is decimal.
    """
    cleaned = "".join(raw.split())
    if decimal in {",", "."}:
        other = "." if decimal == "," else ","
        cleaned = cleaned.replace(other, "").replace(decimal, ".")
    else:
        found = [sep for sep in ",." if sep in cleaned]
        if len(found) == 2:
            dot_last = cleaned.rfind(".") > cleaned.rfind(",")
            thousands, dec = (",", ".") if dot_last else (".", ",")
            cleaned = cleaned.replace(thousands, "").replace(dec, ".")
        elif found:
            sep = found[0]
            head, _, tail = cleaned.partition(sep)
            if cleaned.count(sep) > 1:
                if not _is_grouped(cleaned, sep):
                    raise ValueError("bad grouping")
                cleaned = cleaned.replace(sep, "")
            elif len(tail) == 3 and tail.isdigit() and head.lstrip("+-") not in {"", "0"}:
                raise AmbiguousNumber(raw)
            else:
                cleaned = cleaned.replace(sep, ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("not a number") from exc
    if not amount.is_finite() or not -MAX_AMOUNT <= amount <= MAX_AMOUNT:
        raise ValueError("out of range")
    return amount


def parse_number(raw: object, decimal: str | None = None) -> Decimal:
    """`parse_decimal` for cell values: real numbers (int, float, Decimal) need no separator
    guess; a float goes through its shortest repr, never its binary expansion."""
    if isinstance(raw, bool):
        raise ValueError("not a number")
    if isinstance(raw, float):
        raw = Decimal(repr(raw))
    if isinstance(raw, int | Decimal):
        amount = Decimal(raw)
        if not amount.is_finite() or not -MAX_AMOUNT <= amount <= MAX_AMOUNT:
            raise ValueError("out of range")
        return amount
    return parse_decimal(str(raw).strip(), decimal)


def build_row(
    row_no: int, values: Mapping[str, Any], *, decimal: str | None = None
) -> ParsedRow | RowError:
    """Validate one mapped row (field name -> raw text/value) into an observation and product."""

    def text(name: str) -> str:
        value = values.get(name)
        return "" if value is None else str(value).strip()

    for name in REQUIRED_FIELDS:
        if not text(name):
            return RowError(row_no, f"missing_{name}", f"The {name} is empty.")
    price: str | None = None
    raw_price = values.get("price")
    if text("price"):
        try:
            amount = parse_number(raw_price, decimal)
        except AmbiguousNumber:
            return RowError(row_no, "ambiguous_number", "The price separator is ambiguous.")
        except ValueError:
            return RowError(row_no, "bad_price", "The price is not a usable number.")
        if amount < 0:
            return RowError(row_no, "bad_price", "The price is negative.")
        price = str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    stock: int | None = None
    raw_stock = values.get("stock_quantity")
    if text("stock_quantity"):
        try:
            quantity = parse_number(raw_stock, decimal)
        except AmbiguousNumber:
            return RowError(row_no, "ambiguous_number", "The stock separator is ambiguous.")
        except ValueError:
            return RowError(row_no, "bad_stock", "The stock is not a usable number.")
        if quantity != quantity.to_integral_value() or quantity < 0:
            return RowError(row_no, "bad_stock", "The stock is not a whole, non-negative number.")
        stock = int(quantity)
    product = ExtractedProduct(
        sku=text("sku"),
        title=text("title"),
        description=text("description"),
        price=price,
        stock_quantity=stock,
        url=text("url"),
        ean=text("ean"),
    )
    payload = product.as_json()
    ref = SourceRef(identity=product.sku, locator=f"row:{row_no}")
    return ParsedRow(RawObservation(ref, payload), product)
