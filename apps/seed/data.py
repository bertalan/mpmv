"""The synthetic demo data set (ARC.10): pure data, no ORM.

Everything here is invented for development, the test suites and the STAGE demo: vendors are the
pseudonyms "Vendor A" to "Vendor E", addresses use example streets, emails use the reserved
`example.org` domain and legal identifiers are synthetic (checksum-valid where the platform
checks a checksum). Nothing comes from the legacy system.

The stable keys below (slugs, SKUs, codes, emails) are what makes a row seed-owned: the seed finds
its rows again by these keys, and `--reset` removes only rows that carry them.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

# Synthetic vendors and events located in Italy, one of the EU member states the demo uses.
ITALY: Final = "IT"  # country-literal: synthetic demo data, not the platform country

# Text in several languages: language code -> text. EN is the source language (spec 09).
type Texts = dict[str, str]

SEED_EPOCH: Final = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)  # start of every seeded validity
EMAIL_DOMAIN: Final = "example.org"  # reserved for documentation (RFC 2606)
OPERATOR_EMAIL: Final = f"seed-operator@{EMAIL_DOMAIN}"
ACTOR_LABEL: Final = "command:seed"
REOPEN_REASON: Final = "Synthetic demo data seeded again after a reset (ARC.10)."

# --- Platform

OPERATOR_NAME: Final = "Demo Marketplace Operator S.r.l."
CONTRACT_NOTE: Final = "Synthetic demo contract (ARC.10)."
CONTRACT_TEXTS: Final[dict[str, tuple[str, str]]] = {
    "en": (
        "Vendor agreement (demo)",
        "DEMO TEXT, NOT A CONTRACT. This placeholder stands in for the vendor agreement on "
        "development and demo sites. It has no legal value.",
    ),
    "it": (
        "Contratto venditore (demo)",
        "TESTO DIMOSTRATIVO, NON È UN CONTRATTO. Questo segnaposto sostituisce il contratto "
        "venditore sui siti di sviluppo e demo. Non ha valore legale.",
    ),
}

# VAT rates as published by the member states (spec 05): public facts, not legacy data. Seeded
# only for a country and category that has no rate yet.
TAX_RATES: Final[dict[str, dict[str, Decimal]]] = {
    "AT": {"standard": Decimal("20"), "reduced": Decimal("10")},
    "DE": {"standard": Decimal("19"), "reduced": Decimal("7")},
    "ES": {"standard": Decimal("21"), "reduced": Decimal("10")},
    "FR": {"standard": Decimal("20"), "reduced": Decimal("5.5")},
    # Italy: books at 4% (art. 74 publishing scheme).
    ITALY: {
        "standard": Decimal("22"),
        "reduced": Decimal("10"),
        "books": Decimal("4"),
    },
}

# --- Catalog structure

ATTRIBUTES: Final[dict[str, tuple[Texts, list[tuple[str, Texts]]]]] = {
    "demo_size": (
        {"en": "Size", "it": "Taglia", "de": "Größe", "fr": "Taille", "es": "Talla"},
        [
            ("s", {"en": "S"}),
            ("m", {"en": "M"}),
            ("l", {"en": "L"}),
            ("xl", {"en": "XL"}),
        ],
    ),
    "demo_colour": (
        {"en": "Colour", "it": "Colore", "de": "Farbe", "fr": "Couleur", "es": "Color"},
        [
            ("natural", {"en": "Natural", "it": "Naturale", "de": "Natur", "fr": "Naturel"}),
            ("charcoal", {"en": "Charcoal", "it": "Antracite", "de": "Anthrazit"}),
            ("blue", {"en": "Blue", "it": "Blu", "de": "Blau", "fr": "Bleu", "es": "Azul"}),
            ("green", {"en": "Green", "it": "Verde", "de": "Grün", "fr": "Vert", "es": "Verde"}),
            ("red", {"en": "Red", "it": "Rosso", "de": "Rot", "fr": "Rouge", "es": "Rojo"}),
        ],
    ),
}


@dataclass(frozen=True)
class Node:
    """A category or a taxonomy term: names and slugs by language, children below it."""

    key: str  # the EN slug: the stable key
    names: Texts
    slugs: Texts
    children: tuple["Node", ...] = ()


def _node(key: str, names: Texts, slugs: Texts, *children: Node) -> Node:
    return Node(key=key, names=names, slugs={"en": key, **slugs}, children=children)


CATEGORIES: Final = (
    _node(
        "home-kitchen",
        {
            "en": "Home & kitchen",
            "it": "Casa e cucina",
            "de": "Haus & Küche",
            "fr": "Maison & cuisine",
            "es": "Hogar y cocina",
        },
        {"it": "casa-e-cucina", "de": "haus-kueche", "fr": "maison-cuisine", "es": "hogar-cocina"},
        _node(
            "tableware",
            {
                "en": "Tableware",
                "it": "Tavola",
                "de": "Geschirr",
                "fr": "Arts de la table",
                "es": "Vajilla",
            },
            {"it": "tavola", "de": "geschirr", "fr": "arts-de-la-table", "es": "vajilla"},
        ),
        _node(
            "cookware",
            {
                "en": "Cookware",
                "it": "Pentole e utensili",
                "de": "Kochgeschirr",
                "fr": "Ustensiles",
                "es": "Menaje",
            },
            {"it": "pentole-utensili", "de": "kochgeschirr", "fr": "ustensiles", "es": "menaje"},
        ),
    ),
    _node(
        "clothing",
        {
            "en": "Clothing",
            "it": "Abbigliamento",
            "de": "Kleidung",
            "fr": "Vêtements",
            "es": "Ropa",
        },
        {"it": "abbigliamento", "de": "kleidung", "fr": "vetements", "es": "ropa"},
        _node(
            "knitwear",
            {
                "en": "Knitwear",
                "it": "Maglieria",
                "de": "Strickwaren",
                "fr": "Tricots",
                "es": "Punto",
            },
            {"it": "maglieria", "de": "strickwaren", "fr": "tricots", "es": "punto"},
        ),
        _node(
            "accessories",
            {
                "en": "Accessories",
                "it": "Accessori",
                "de": "Accessoires",
                "fr": "Accessoires",
                "es": "Accesorios",
            },
            {"it": "accessori", "de": "accessoires", "fr": "accessoires", "es": "accesorios"},
        ),
    ),
    _node(
        "books-stationery",
        {
            "en": "Books & stationery",
            "it": "Libri e cartoleria",
            "de": "Bücher & Papeterie",
            "fr": "Livres & papeterie",
            "es": "Libros y papelería",
        },
        {
            "it": "libri-cartoleria",
            "de": "buecher-papeterie",
            "fr": "livres-papeterie",
            "es": "libros-papeleria",
        },
        _node(
            "books",
            {"en": "Books", "it": "Libri", "de": "Bücher", "fr": "Livres", "es": "Libros"},
            {"it": "libri", "de": "buecher", "fr": "livres", "es": "libros"},
        ),
        _node(
            "notebooks",
            {
                "en": "Notebooks",
                "it": "Quaderni",
                "de": "Notizbücher",
                "fr": "Carnets",
                "es": "Cuadernos",
            },
            {"it": "quaderni", "de": "notizbuecher", "fr": "carnets", "es": "cuadernos"},
        ),
    ),
    _node(
        "outdoor-garden",
        {
            "en": "Outdoor & garden",
            "it": "Outdoor e giardino",
            "de": "Outdoor & Garten",
            "fr": "Plein air & jardin",
            "es": "Exterior y jardín",
        },
        {
            "it": "outdoor-giardino",
            "de": "outdoor-garten",
            "fr": "plein-air-jardin",
            "es": "exterior-jardin",
        },
        _node(
            "camping",
            {
                "en": "Camping",
                "it": "Campeggio",
                "de": "Camping",
                "fr": "Camping",
                "es": "Acampada",
            },
            {"it": "campeggio", "de": "camping", "fr": "camping", "es": "acampada"},
        ),
        _node(
            "garden",
            {"en": "Garden", "it": "Giardino", "de": "Garten", "fr": "Jardin", "es": "Jardín"},
            {"it": "giardino", "de": "garten", "fr": "jardin", "es": "jardin"},
        ),
    ),
    _node(
        "home-decor",
        {
            "en": "Home decor",
            "it": "Arredo e decorazione",
            "de": "Wohndeko",
            "fr": "Décoration",
            "es": "Decoración",
        },
        {"it": "arredo-decorazione", "de": "wohndeko", "fr": "decoration", "es": "decoracion"},
        _node(
            "lighting",
            {
                "en": "Lighting",
                "it": "Illuminazione",
                "de": "Leuchten",
                "fr": "Luminaires",
                "es": "Iluminación",
            },
            {"it": "illuminazione", "de": "leuchten", "fr": "luminaires", "es": "iluminacion"},
        ),
        _node(
            "wall-art",
            {
                "en": "Wall art",
                "it": "Quadri e stampe",
                "de": "Wandbilder",
                "fr": "Affiches",
                "es": "Láminas",
            },
            {"it": "quadri-stampe", "de": "wandbilder", "fr": "affiches", "es": "laminas"},
        ),
    ),
)

# A flat taxonomy with a page per term (CAT.8): `/material/wood/`, `/it/materiale/legno/`.
TAXONOMY_CODE: Final = "demo_material"
TAXONOMY_NAMES: Final[Texts] = {
    "en": "Material",
    "it": "Materiale",
    "de": "Material",
    "fr": "Matière",
    "es": "Material",
}
TAXONOMY_SLUGS: Final[Texts] = {
    "en": "material",
    "it": "materiale",
    "de": "werkstoff",
    "fr": "matiere",
    "es": "materia",
}
TERMS: Final = (
    _node(
        "wood",
        {"en": "Wood", "it": "Legno", "de": "Holz", "fr": "Bois", "es": "Madera"},
        {"it": "legno", "de": "holz", "fr": "bois", "es": "madera"},
    ),
    _node(
        "ceramic",
        {"en": "Ceramic", "it": "Ceramica", "de": "Keramik", "fr": "Céramique", "es": "Cerámica"},
        {"it": "ceramica", "de": "keramik", "fr": "ceramique", "es": "ceramica"},
    ),
    _node(
        "linen",
        {"en": "Linen", "it": "Lino", "de": "Leinen", "fr": "Lin", "es": "Lino"},
        {"it": "lino", "de": "leinen", "fr": "lin", "es": "lino"},
    ),
    _node(
        "wool",
        {"en": "Wool", "it": "Lana", "de": "Wolle", "fr": "Laine", "es": "Lana"},
        {"it": "lana", "de": "wolle", "fr": "laine", "es": "lana"},
    ),
    _node(
        "paper",
        {"en": "Paper", "it": "Carta", "de": "Papier", "fr": "Papier", "es": "Papel"},
        {"it": "carta", "de": "papier", "fr": "papier", "es": "papel"},
    ),
    _node(
        "metal",
        {"en": "Metal", "it": "Metallo", "de": "Metall", "fr": "Métal", "es": "Metal"},
        {"it": "metallo", "de": "metall", "fr": "metal", "es": "metal"},
    ),
)

BRANDS: Final[dict[str, Texts]] = {
    "Demo Brand Alpha": {
        "en": "Everyday pieces made in small batches.",
        "it": "Oggetti quotidiani prodotti in piccoli lotti.",
    },
    "Demo Brand Beta": {
        "en": "Garments knitted to order.",
        "it": "Capi lavorati a maglia su ordinazione.",
    },
    "Demo Brand Gamma": {
        "en": "Independent books and paper goods.",
        "it": "Libri indipendenti e articoli di carta.",
    },
    "Demo Brand Delta": {
        "en": "Gear for the outdoors, built to last.",
        "it": "Attrezzatura per l'aria aperta, fatta per durare.",
    },
    "Demo Brand Epsilon": {
        "en": "Lamps and prints from a design studio.",
        "it": "Lampade e stampe da uno studio di design.",
    },
}

# --- Vendors


@dataclass(frozen=True)
class VendorSeed:
    """One vendor, its legal identity (VEN.12), its VAT regime (TAX.1) and its storefront."""

    letter: str
    display_name: str
    legal_name: str
    country: str
    legal_form: str
    fiscal_regime: str
    vat_regime: str  # tax engine: oss, domestic_only, exempt
    exempt_reason: str
    identity: dict[str, str]  # further Vendor fields: address, identifiers, bank
    tagline: Texts
    description: Texts
    brand: str
    shipping_country: str  # the country of the vendor's home shipping zone
    island_areas: tuple[str, ...]  # special areas (SHP.14) with a surcharge
    quote_areas: tuple[str, ...]  # special areas quoted on request
    closed_areas: tuple[str, ...]  # special areas the vendor does not ship to
    oss_since: dt.date | None = None
    share_capital: Decimal | None = None
    enquiries: bool = False

    @property
    def slug(self) -> str:
        return self.display_name.lower().replace(" ", "-")

    @property
    def owner_email(self) -> str:
        return f"owner.vendor-{self.letter.lower()}@{EMAIL_DOMAIN}"

    @property
    def contact_email(self) -> str:
        return f"shop.vendor-{self.letter.lower()}@{EMAIL_DOMAIN}"

    @property
    def sku_prefix(self) -> str:
        return f"DEMO-{self.letter}"


def _policies() -> dict[str, Texts]:
    return {
        "shipping_policy": {
            "en": "Orders ship within two working days from our workshop.",
            "it": "Gli ordini partono entro due giorni lavorativi dal nostro laboratorio.",
        },
        "refund_policy": {
            "en": "Unused items can be returned within 14 days for a full refund.",
            "it": "Gli articoli non usati si possono restituire entro 14 giorni, con rimborso.",
        },
        "cancellation_policy": {
            "en": "You can cancel an order until it ships.",
            "it": "Puoi annullare un ordine finché non è stato spedito.",
        },
    }


POLICIES: Final = _policies()

# Identity fields filled at seed time from the vendor's sequence number: checksum-valid VAT
# numbers, codici fiscali and IBANs (`services._identity`).
VENDORS: Final = (
    VendorSeed(
        letter="A",
        display_name="Vendor A Workshop",
        legal_name="Vendor A Workshop S.r.l.",
        country=ITALY,
        legal_form="srl",
        fiscal_regime="RF01",
        vat_regime="domestic_only",
        exempt_reason="",
        share_capital=Decimal("10000.00"),
        identity={
            "registered_street": "Via Esempio",
            "registered_street_number": "1",
            "registered_postal_code": "50100",
            "registered_city": "Firenze",
            "registered_province": "FI",
            "rea_office": "FI",
            "rea_number": "100001",
            "ateco_code": "23.41",
            "bank_name": "Example Bank",
            "bic": "EXMPITMMXXX",
        },
        tagline={
            "en": "Stoneware and kitchen tools, thrown and turned by hand.",
            "it": "Gres e utensili da cucina, torniti a mano.",
        },
        description={
            "en": "A small ceramics workshop: plates, bowls and kitchen tools made in short runs.",
            "it": "Un piccolo laboratorio di ceramica: piatti, ciotole e utensili in brevi serie.",
        },
        brand="Demo Brand Alpha",
        shipping_country=ITALY,
        island_areas=("it-sicily", "it-sardinia"),
        quote_areas=("it-minor-islands", "it-venice-lagoon"),
        closed_areas=("it-livigno", "it-campione"),
    ),
    VendorSeed(
        letter="B",
        display_name="Vendor B Atelier",
        legal_name="",  # a sole proprietor: first and last name (services._identity)
        country=ITALY,
        legal_form="sole_proprietorship",
        fiscal_regime="RF19",
        vat_regime="exempt",
        exempt_reason="flat_rate_forfettario",
        identity={
            "first_name": "Bianca",
            "last_name": "Esempio",
            "registered_street": "Via Esempio",
            "registered_street_number": "2",
            "registered_postal_code": "20100",
            "registered_city": "Milano",
            "registered_province": "MI",
            "ateco_code": "14.39",
        },
        tagline={
            "en": "Knitwear and accessories in natural yarns.",
            "it": "Maglieria e accessori in filati naturali.",
        },
        description={
            "en": "A one-person atelier knitting jumpers, scarves and hats to order.",
            "it": "Un atelier di una sola persona che lavora maglioni, sciarpe e cappelli.",
        },
        brand="Demo Brand Beta",
        shipping_country=ITALY,
        island_areas=("it-sicily", "it-sardinia", "it-minor-islands"),
        quote_areas=(),
        closed_areas=("it-livigno", "it-campione"),
        enquiries=True,
    ),
    VendorSeed(
        letter="C",
        display_name="Vendor C Press",
        legal_name="Vendor C Press S.r.l.",
        country=ITALY,
        legal_form="srl",
        fiscal_regime="RF01",
        vat_regime="exempt",
        exempt_reason="art74_publishing",
        share_capital=Decimal("20000.00"),
        identity={
            "registered_street": "Via Esempio",
            "registered_street_number": "3",
            "registered_postal_code": "40100",
            "registered_city": "Bologna",
            "registered_province": "BO",
            "rea_office": "BO",
            "rea_number": "100003",
            "ateco_code": "58.11",
            "bank_name": "Example Bank",
            "bic": "EXMPITMMXXX",
        },
        tagline={
            "en": "An independent publisher of essays, notebooks and paper goods.",
            "it": "Un editore indipendente di saggi, quaderni e articoli di carta.",
        },
        description={
            "en": "Books printed in small runs, with notebooks bound in the same bindery.",
            "it": "Libri stampati in piccole tirature e quaderni rilegati nella stessa legatoria.",
        },
        brand="Demo Brand Gamma",
        shipping_country=ITALY,
        island_areas=("it-sicily", "it-sardinia"),
        quote_areas=("it-minor-islands",),
        closed_areas=(),
    ),
    VendorSeed(
        letter="D",
        display_name="Vendor D Outdoor",
        legal_name="Vendor D Outdoor GmbH",
        country="DE",
        legal_form="other",
        fiscal_regime="RF18",
        vat_regime="oss",
        exempt_reason="",
        oss_since=dt.date(2025, 7, 1),
        identity={
            "registered_street": "Beispielstrasse",
            "registered_street_number": "4",
            "registered_postal_code": "10115",
            "registered_city": "Berlin",
            "registered_country": "DE",
            "tax_residence_country": "DE",
            "sdi_recipient_code": "XXXXXXX",
            "bank_name": "Example Bank",
            "bic": "EXMPDEFFXXX",
        },
        tagline={
            "en": "Camping and garden gear from a family workshop.",
            "it": "Attrezzatura da campeggio e giardino da un laboratorio di famiglia.",
        },
        description={
            "en": "Tools, lanterns and camp furniture in steel and oiled wood.",
            "it": "Attrezzi, lanterne e arredi da campo in acciaio e legno oliato.",
        },
        brand="Demo Brand Delta",
        shipping_country="DE",
        island_areas=(),
        quote_areas=(),
        closed_areas=("de-heligoland", "de-busingen"),
    ),
    VendorSeed(
        letter="E",
        display_name="Vendor E Studio",
        legal_name="Vendor E Studio SARL",
        country="FR",
        legal_form="other",
        fiscal_regime="RF20",
        vat_regime="exempt",
        exempt_reason="other",
        identity={
            "registered_street": "Rue Exemple",
            "registered_street_number": "5",
            "registered_postal_code": "69001",
            "registered_city": "Lyon",
            "registered_country": "FR",
            "tax_residence_country": "FR",
            "sdi_recipient_code": "XXXXXXX",
            "bank_name": "Example Bank",
            "bic": "EXMPFRPPXXX",
        },
        tagline={
            "en": "Lamps and art prints from a small design studio.",
            "it": "Lampade e stampe d'arte da un piccolo studio di design.",
        },
        description={
            "en": "A two-person studio that designs table lamps and prints posters in-house.",
            "it": "Uno studio di due persone che disegna lampade e stampa poster in proprio.",
        },
        brand="Demo Brand Epsilon",
        shipping_country="FR",
        island_areas=("fr-corsica",),
        quote_areas=(),
        closed_areas=("fr-guadeloupe", "fr-martinique", "fr-guiana", "fr-reunion", "fr-mayotte"),
    ),
)

# --- Products


@dataclass(frozen=True)
class ProductSeed:
    """One product: its SKU suffix, category, material term, titles and price (net, EUR)."""

    code: str
    category: str  # a subcategory key
    term: str
    title: Texts
    price: Decimal
    weight: Decimal
    sale_price: Decimal | None = None
    axes: tuple[str, ...] = ()  # attribute codes of a variable product
    options: tuple[tuple[str, ...], ...] = ()  # one value per axis, per variant
    fiscal_category: str = "standard"
    sales_mode: str = "buy"
    extra: dict[str, str] = field(default_factory=dict)


def _simple(
    code: str,
    category: str,
    term: str,
    en: str,
    it: str,
    price: str,
    weight: str,
    sale: str | None = None,
    **kwargs: str,
) -> ProductSeed:
    return ProductSeed(
        code=code,
        category=category,
        term=term,
        title={"en": en, "it": it},
        price=Decimal(price),
        weight=Decimal(weight),
        sale_price=Decimal(sale) if sale else None,
        fiscal_category=kwargs.get("fiscal_category", "standard"),
        sales_mode=kwargs.get("sales_mode", "buy"),
    )


def _variable(
    code: str,
    category: str,
    term: str,
    en: str,
    it: str,
    price: str,
    weight: str,
    axes: tuple[str, ...],
    options: tuple[tuple[str, ...], ...],
) -> ProductSeed:
    return ProductSeed(
        code=code,
        category=category,
        term=term,
        title={"en": en, "it": it},
        price=Decimal(price),
        weight=Decimal(weight),
        axes=axes,
        options=options,
    )


SIZES: Final = (("s",), ("m",), ("l",), ("xl",))
COLOURS: Final = (("natural",), ("charcoal",), ("blue",))
SIZE_COLOURS: Final = tuple((s, c) for s in ("s", "m", "l") for c in ("natural", "charcoal"))

PRODUCTS: Final[dict[str, tuple[ProductSeed, ...]]] = {
    "A": (
        _simple(
            "01",
            "tableware",
            "ceramic",
            "Stoneware dinner plate",
            "Piatto piano in gres",
            "18.50",
            "0.650",
        ),
        _simple(
            "02", "tableware", "ceramic", "Stoneware soup bowl", "Fondina in gres", "15.00", "0.480"
        ),
        _variable(
            "03",
            "tableware",
            "ceramic",
            "Glazed mug",
            "Tazza smaltata",
            "12.90",
            "0.350",
            ("demo_colour",),
            COLOURS,
        ),
        _simple(
            "04",
            "tableware",
            "ceramic",
            "Serving platter",
            "Piatto da portata",
            "39.00",
            "1.400",
            sale="32.00",
        ),
        _simple(
            "05",
            "tableware",
            "wood",
            "Olive wood salad servers",
            "Posate da insalata in legno d'ulivo",
            "24.00",
            "0.200",
        ),
        _simple(
            "06",
            "cookware",
            "wood",
            "Beech wood spoon set",
            "Set di cucchiai in faggio",
            "16.50",
            "0.250",
        ),
        _simple(
            "07",
            "cookware",
            "ceramic",
            "Ceramic baking dish",
            "Pirofila in ceramica",
            "34.00",
            "1.800",
        ),
        _simple(
            "08",
            "cookware",
            "metal",
            "Hand-forged kitchen knife",
            "Coltello da cucina forgiato a mano",
            "79.00",
            "0.300",
        ),
        _simple(
            "09",
            "cookware",
            "wood",
            "End-grain cutting board",
            "Tagliere in legno di testa",
            "49.00",
            "2.100",
        ),
        _simple(
            "10", "cookware", "ceramic", "Ceramic oil cruet", "Oliera in ceramica", "21.00", "0.400"
        ),
    ),
    "B": (
        _variable(
            "01",
            "knitwear",
            "wool",
            "Merino crew-neck jumper",
            "Maglione girocollo in merino",
            "89.00",
            "0.450",
            ("demo_size", "demo_colour"),
            SIZE_COLOURS,
        ),
        _variable(
            "02",
            "knitwear",
            "wool",
            "Chunky cardigan",
            "Cardigan a maglia grossa",
            "119.00",
            "0.700",
            ("demo_size",),
            SIZES,
        ),
        _variable(
            "03",
            "knitwear",
            "linen",
            "Linen summer top",
            "Top estivo in lino",
            "54.00",
            "0.200",
            ("demo_size",),
            SIZES,
        ),
        _variable(
            "04",
            "knitwear",
            "wool",
            "Ribbed vest",
            "Gilet a coste",
            "65.00",
            "0.350",
            ("demo_size",),
            SIZES[:3],
        ),
        _simple("05", "accessories", "wool", "Wool beanie", "Berretto in lana", "29.00", "0.100"),
        _simple(
            "06",
            "accessories",
            "wool",
            "Long knitted scarf",
            "Sciarpa lunga a maglia",
            "45.00",
            "0.250",
            sale="39.00",
        ),
        _simple(
            "07",
            "accessories",
            "linen",
            "Linen tote bag",
            "Borsa shopper in lino",
            "32.00",
            "0.200",
        ),
        _simple(
            "08", "accessories", "wool", "Fingerless gloves", "Guanti senza dita", "25.00", "0.080"
        ),
        _simple("09", "accessories", "linen", "Linen bandana", "Bandana in lino", "14.00", "0.050"),
        _simple(
            "10",
            "accessories",
            "wool",
            "Made-to-measure jumper",
            "Maglione su misura",
            "160.00",
            "0.500",
            sales_mode="on_request",
        ),
    ),
    "C": (
        _simple(
            "01",
            "books",
            "paper",
            "Essays on slow making",
            "Saggi sul fare lento",
            "18.00",
            "0.400",
            fiscal_category="book_art74",
        ),
        _simple(
            "02",
            "books",
            "paper",
            "A field guide to city trees",
            "Guida agli alberi di città",
            "22.00",
            "0.550",
            fiscal_category="book_art74",
        ),
        _simple(
            "03",
            "books",
            "paper",
            "Letters from the bindery",
            "Lettere dalla legatoria",
            "16.00",
            "0.300",
            fiscal_category="book_art74",
        ),
        _simple(
            "04",
            "books",
            "paper",
            "Small press cookbook",
            "Ricettario di piccola editoria",
            "28.00",
            "0.800",
            fiscal_category="book_art74",
        ),
        _simple(
            "05",
            "books",
            "paper",
            "Poems for long evenings",
            "Poesie per sere lunghe",
            "14.00",
            "0.250",
            fiscal_category="book_art74",
        ),
        _variable(
            "06",
            "notebooks",
            "paper",
            "Stitched A5 notebook",
            "Quaderno A5 cucito",
            "9.50",
            "0.180",
            ("demo_colour",),
            COLOURS,
        ),
        _simple(
            "07", "notebooks", "paper", "Dot-grid journal", "Diario a puntini", "19.00", "0.350"
        ),
        _simple(
            "08",
            "notebooks",
            "paper",
            "Pocket sketchbook",
            "Taccuino tascabile",
            "8.00",
            "0.120",
            sale="6.50",
        ),
        _simple(
            "09",
            "notebooks",
            "paper",
            "Weekly desk planner",
            "Agenda settimanale da scrivania",
            "24.00",
            "0.450",
        ),
        _simple(
            "10",
            "notebooks",
            "linen",
            "Linen-bound guest book",
            "Libro ospiti rilegato in lino",
            "42.00",
            "0.900",
        ),
    ),
    "D": (
        _simple(
            "01",
            "camping",
            "metal",
            "Steel camp lantern",
            "Lanterna da campo in acciaio",
            "48.00",
            "0.900",
        ),
        _simple(
            "02",
            "camping",
            "wood",
            "Folding camp stool",
            "Sgabello pieghevole da campo",
            "59.00",
            "2.300",
        ),
        _simple(
            "03", "camping", "metal", "Enamel camp mug", "Tazza smaltata da campo", "11.00", "0.200"
        ),
        _variable(
            "04",
            "camping",
            "wool",
            "Wool camp blanket",
            "Coperta da campo in lana",
            "95.00",
            "1.600",
            ("demo_colour",),
            (("charcoal",), ("green",), ("red",)),
        ),
        _simple(
            "05",
            "camping",
            "metal",
            "Cast iron skillet",
            "Padella in ghisa",
            "54.00",
            "3.200",
            sale="45.00",
        ),
        _simple(
            "06",
            "garden",
            "metal",
            "Forged garden trowel",
            "Trapiantatoio forgiato",
            "27.00",
            "0.400",
        ),
        _simple("07", "garden", "wood", "Cedar planter box", "Fioriera in cedro", "79.00", "6.500"),
        _simple("08", "garden", "metal", "Pruning shears", "Forbici da potatura", "38.00", "0.350"),
        _simple(
            "09",
            "garden",
            "linen",
            "Canvas garden apron",
            "Grembiule da giardino in tela",
            "34.00",
            "0.400",
        ),
        _simple(
            "10",
            "garden",
            "wood",
            "Wooden bird house",
            "Casetta per uccelli in legno",
            "29.00",
            "1.100",
        ),
    ),
    "E": (
        _variable(
            "01",
            "lighting",
            "ceramic",
            "Ceramic table lamp",
            "Lampada da tavolo in ceramica",
            "129.00",
            "2.400",
            ("demo_colour",),
            COLOURS,
        ),
        _simple(
            "02",
            "lighting",
            "metal",
            "Brass reading lamp",
            "Lampada da lettura in ottone",
            "189.00",
            "2.900",
        ),
        _simple(
            "03",
            "lighting",
            "paper",
            "Paper pendant shade",
            "Paralume a sospensione in carta",
            "64.00",
            "0.600",
        ),
        _simple(
            "04",
            "lighting",
            "wood",
            "Oak wall sconce",
            "Applique in rovere",
            "98.00",
            "1.300",
            sale="84.00",
        ),
        _simple(
            "05",
            "lighting",
            "metal",
            "Custom lighting design",
            "Progetto luce su misura",
            "450.00",
            "0.100",
            sales_mode="on_request",
        ),
        _variable(
            "06",
            "wall-art",
            "paper",
            "Botanical print",
            "Stampa botanica",
            "35.00",
            "0.300",
            ("demo_size",),
            (("s",), ("m",), ("l",)),
        ),
        _simple(
            "07",
            "wall-art",
            "paper",
            "City map poster",
            "Poster mappa della città",
            "29.00",
            "0.250",
        ),
        _simple(
            "08",
            "wall-art",
            "paper",
            "Abstract risograph print",
            "Stampa risograph astratta",
            "42.00",
            "0.250",
        ),
        _simple(
            "09", "wall-art", "wood", "Oak picture frame", "Cornice in rovere", "39.00", "0.800"
        ),
        _simple(
            "10", "wall-art", "linen", "Linen wall hanging", "Arazzo in lino", "72.00", "0.500"
        ),
    ),
}


def product_description(title: Texts, vendor: VendorSeed) -> tuple[Texts, Texts]:
    """Short description and description (HTML) of a product, in EN and IT."""
    short = {
        "en": f"{title['en']} by {vendor.display_name}. Synthetic demo product.",
        "it": f"{title['it']} di {vendor.display_name}. Prodotto dimostrativo.",
    }
    long = {
        "en": (
            f"<p>{title['en']}, made in small batches by {vendor.display_name}.</p>"
            "<p>This is demo content for a test marketplace: the product, its price and its "
            "stock are invented.</p>"
        ),
        "it": (
            f"<p>{title['it']}, prodotto in piccoli lotti da {vendor.display_name}.</p>"
            "<p>Questo è un contenuto dimostrativo di un marketplace di prova: prodotto, prezzo "
            "e disponibilità sono inventati.</p>"
        ),
    }
    return short, long


# --- Shipping

CARRIERS: Final[dict[str, Texts]] = {
    "demo-post": {"en": "Demo Post", "it": "Posta Demo"},
    "demo-express": {"en": "Demo Express Courier", "it": "Corriere Espresso Demo"},
}

# Weight bands of the standard method: (from kg, to kg or None, net cost in EUR).
STANDARD_BANDS: Final = (
    ("0", "2", "6.90"),
    ("2", "10", "9.90"),
    ("10", None, "19.90"),
)
EXPRESS_BANDS: Final = (("0", "5", "14.90"), ("5", None, "24.90"))
EU_BANDS: Final = (("0", "2", "12.90"), ("2", "10", "19.90"), ("10", None, "34.90"))
FREE_SHIPPING_THRESHOLD: Final = "99.00"
ISLAND_SURCHARGE: Final = "8.00"

# --- CMS


@dataclass(frozen=True)
class PageSeed:
    slug: Texts
    title: Texts
    body: Texts  # rich text HTML


DEMO_NOTICE: Final[Texts] = {
    "en": "<p><strong>Demo content.</strong> This site is a test marketplace with invented "
    "vendors and products. Nothing here can be bought.</p>",
    "it": "<p><strong>Contenuto dimostrativo.</strong> Questo sito è un marketplace di prova con "
    "venditori e prodotti inventati. Qui non si può acquistare nulla.</p>",
}

HOME_HERO: Final[dict[str, tuple[str, str, str]]] = {
    # language: (heading, text, link label)
    "en": (
        "Made by independent makers",
        "Ceramics, knitwear, books, outdoor gear and lamps from five demo vendors.",
        "How it works",
    ),
    "it": (
        "Fatto da artigiani indipendenti",
        "Ceramiche, maglieria, libri, attrezzatura "
        "outdoor e lampade da cinque venditori dimostrativi.",
        "Come funziona",
    ),
    "de": (
        "Von unabhängigen Herstellern",
        "Keramik, Strickwaren, Bücher, Outdoor-Ausrüstung und Leuchten von fünf Demo-Händlern.",
        "So funktioniert es",
    ),
    "fr": (
        "Fait par des artisans indépendants",
        "Céramique, tricots, livres, matériel de plein "
        "air et luminaires de cinq vendeurs de démonstration.",
        "Comment ça marche",
    ),
    "es": (
        "Hecho por artesanos independientes",
        "Cerámica, punto, libros, equipo de exterior "
        "y lámparas de cinco vendedores de demostración.",
        "Cómo funciona",
    ),
}
HOME_FEATURED: Final[Texts] = {
    "en": "Browse by material",
    "it": "Scegli per materiale",
    "de": "Nach Material",
    "fr": "Par matière",
    "es": "Por material",
}
HOME_VENDORS: Final[Texts] = {
    "en": "Meet the vendors",
    "it": "I venditori",
    "de": "Die Händler",
    "fr": "Les vendeurs",
    "es": "Los vendedores",
}
HOME_HERO_ALT: Final[Texts] = {
    "en": "Abstract placeholder shapes in warm colours",
    "it": "Forme astratte segnaposto in colori caldi",
}

HOW_IT_WORKS: Final = PageSeed(
    slug={"en": "how-it-works", "it": "come-funziona"},
    title={"en": "How it works (demo)", "it": "Come funziona (demo)"},
    body={
        "en": "<p>Each vendor runs its own shop on the marketplace and ships its own orders. "
        "Prices are shown without VAT until the checkout knows where you are.</p>",
        "it": "<p>Ogni venditore gestisce il proprio negozio sul marketplace e spedisce i propri "
        "ordini. I prezzi sono mostrati senza IVA finché il checkout non sa dove ti trovi.</p>",
    },
)
HOW_IT_WORKS_FAQ: Final[dict[str, list[tuple[str, str]]]] = {
    "en": [
        ("Is this a real shop?", "<p>No. It is a demo with invented data.</p>"),
        ("Who ships my order?", "<p>Each vendor ships its own products.</p>"),
    ],
    "it": [
        ("È un negozio vero?", "<p>No. È una demo con dati inventati.</p>"),
        ("Chi spedisce il mio ordine?", "<p>Ogni venditore spedisce i propri prodotti.</p>"),
    ],
}

LEGAL_EFFECTIVE: Final = dt.date(2026, 1, 1)
LEGAL_PAGES: Final[dict[str, PageSeed]] = {
    kind: PageSeed(
        slug=slug,
        title=title,
        body={
            "en": "<p><strong>DEMO PLACEHOLDER, NOT A LEGAL TEXT.</strong> The real "
            f"{title['en'].lower().removesuffix(' (demo)')} will be published here before "
            "launch.</p>",
            "it": "<p><strong>SEGNAPOSTO DIMOSTRATIVO, NON È UN TESTO LEGALE.</strong> Il testo "
            "definitivo sarà pubblicato qui prima del lancio.</p>",
        },
    )
    for kind, slug, title in (
        (
            "terms",
            {"en": "demo-terms-of-sale", "it": "demo-condizioni-di-vendita"},
            {"en": "Terms of sale (demo)", "it": "Condizioni di vendita (demo)"},
        ),
        (
            "privacy",
            {"en": "demo-privacy-policy", "it": "demo-informativa-privacy"},
            {"en": "Privacy policy (demo)", "it": "Informativa privacy (demo)"},
        ),
        (
            "cookies",
            {"en": "demo-cookie-policy", "it": "demo-cookie-policy"},
            {"en": "Cookie policy (demo)", "it": "Cookie policy (demo)"},
        ),
        (
            "withdrawal",
            {"en": "demo-right-of-withdrawal", "it": "demo-diritto-di-recesso"},
            {"en": "Right of withdrawal (demo)", "it": "Diritto di recesso (demo)"},
        ),
        (
            "returns",
            {"en": "demo-shipping-and-returns", "it": "demo-spedizioni-e-resi"},
            {"en": "Shipping and returns (demo)", "it": "Spedizioni e resi (demo)"},
        ),
    )
}

# --- Events


@dataclass(frozen=True)
class EventSeed:
    slug: Texts
    title: Texts
    summary: Texts
    vendor: str | None  # vendor letter; None for a platform event
    event_type: str
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    days: tuple[dt.date, dt.date] | None = None
    online_url: str = ""
    venue: dict[str, str] = field(default_factory=dict)


EVENTS: Final = (
    EventSeed(
        slug={"en": "demo-makers-fair", "it": "demo-fiera-artigiani"},
        title={"en": "Makers fair (demo)", "it": "Fiera degli artigiani (demo)"},
        summary={
            "en": "Meet Vendor A and Vendor C at their stands.",
            "it": "Incontra Vendor A e Vendor C ai loro stand.",
        },
        vendor="A",
        event_type="fair",
        start=dt.datetime(2027, 3, 13, 9, 0, tzinfo=dt.UTC),
        end=dt.datetime(2027, 3, 13, 17, 0, tzinfo=dt.UTC),
        venue={
            "venue_name": "Example Exhibition Hall",
            "street": "Via Esempio 10",
            "postcode": "50100",
            "city": "Firenze",
            "region": "FI",
            "country": ITALY,
            "booth": "A12",
        },
    ),
    EventSeed(
        slug={"en": "demo-spring-sale", "it": "demo-saldi-di-primavera"},
        title={"en": "Spring sale (demo)", "it": "Saldi di primavera (demo)"},
        summary={
            "en": "Selected outdoor gear at reduced prices.",
            "it": "Attrezzatura outdoor selezionata a prezzi ridotti.",
        },
        vendor="D",
        event_type="promotion",
        days=(dt.date(2027, 4, 1), dt.date(2027, 4, 14)),
    ),
    EventSeed(
        slug={"en": "demo-online-workshop", "it": "demo-laboratorio-online"},
        title={
            "en": "Online bookbinding workshop (demo)",
            "it": "Laboratorio online di legatoria (demo)",
        },
        summary={
            "en": "A one-hour live session on binding a notebook.",
            "it": "Un'ora in diretta per rilegare un quaderno.",
        },
        vendor=None,
        event_type="other",
        start=dt.datetime(2027, 5, 6, 17, 0, tzinfo=dt.UTC),
        end=dt.datetime(2027, 5, 6, 18, 0, tzinfo=dt.UTC),
        online_url="https://meet.example.org/demo-workshop",
    ),
)

# --- Placeholder images (drawn with Pillow, `apps.seed.images`)

IMAGES: Final[dict[str, tuple[str, tuple[int, int, int]]]] = {
    # key: (title, base colour)
    "home-hero": ("Demo placeholder: home banner", (196, 112, 74)),
    "how-it-works": ("Demo placeholder: how it works", (74, 128, 160)),
    "event-fair": ("Demo placeholder: makers fair", (120, 150, 90)),
    "event-sale": ("Demo placeholder: spring sale", (210, 170, 60)),
    "event-workshop": ("Demo placeholder: online workshop", (130, 100, 160)),
}
IMAGE_AUTHOR: Final = "MPMV demo seed (generated)"
