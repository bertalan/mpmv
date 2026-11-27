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
from typing import Any, Final, Protocol

from apps.ingest import engine

DEFAULT_MAX_BYTES: Final = 5 * 1024 * 1024
DEFAULT_MAX_ROWS: Final = 20_000

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


def build_row(row_no: int, values: Mapping[str, Any]) -> ParsedRow | RowError:
    """Validate one mapped row (field name -> raw text/value) into an observation and product."""
    from decimal import Decimal, InvalidOperation

    def text(name: str) -> str:
        value = values.get(name)
        return "" if value is None else str(value).strip()

    for name in REQUIRED_FIELDS:
        if not text(name):
            return RowError(row_no, f"missing_{name}", f"The {name} is empty.")
    price: str | None = None
    raw_price = text("price")
    if raw_price:
        cleaned = "".join(raw_price.split())
        if "," in cleaned and "." in cleaned:
            dot_last = cleaned.rfind(".") > cleaned.rfind(",")
            thousands, decimal = (",", ".") if dot_last else (".", ",")
            cleaned = cleaned.replace(thousands, "").replace(decimal, ".")
        else:
            cleaned = cleaned.replace(",", ".")
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            return RowError(row_no, "bad_price", "The price is not a number.")
        if not amount.is_finite() or amount < 0:
            return RowError(row_no, "bad_price", "The price is not a non-negative number.")
        price = f"{amount:.2f}"
    stock: int | None = None
    raw_stock = text("stock_quantity")
    if raw_stock:
        try:
            stock = int(Decimal(raw_stock.replace(",", ".")))
        except (InvalidOperation, ValueError):
            return RowError(row_no, "bad_stock", "The stock is not a whole number.")
        if stock < 0:
            return RowError(row_no, "bad_stock", "The stock is negative.")
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
