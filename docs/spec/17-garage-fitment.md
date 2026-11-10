# 17 — The garage: a personalised catalogue by what the customer owns

> **Status: decided in part (D-38, owner answers of 2026-11-10).** Written by lane-b as an independent
> design from the owner's idea (issue #86). Decided: the **whole garage is P1, before opening**, built
> in slices that merge one by one (§17.13); the seller wording is "Compatibility declared by the
> seller" (Q-G8); the fitment-relevant categories are an operator setting seeded with none (Q-G4).
> Open and with counsel, not blockers: Q-G1 (anonymous cookie) and Q-G2 (filter, Omnibus, DSA art. 27);
> the design takes the prudent option meanwhile. The text is sector-neutral on purpose: the wording
> is "item", "make", "model", "variant" and "year range". A vertical theme or the operator's
> configuration supplies the real nouns.

## 17.1 The idea and the principles

The owner's idea: ask the customer which item they own or prefer (the make and model of a
vehicle, device or piece of equipment) and show only the products, articles and other content
that fit it. The customer keeps the choice in a **garage**: a small set of named profiles, like
the print presets of a printer. One profile is active at a time. Resetting goes back to the
generic catalogue; choosing a profile again from the menu makes it active again. The person is
always aware of it.

Owner input, recorded as requirements:

1. SEO pages per make and model are **editorial articles written by the team**, linked to the
   compatibility data. They are not generated landing pages.
2. The filter is called the **garage**: profiles the user decides, reset gives the generic
   catalogue, re-select from the menu makes a profile usable again, the person is always aware of it.

Design principles (each one is argued in the sections that follow):

- **A filter, never a hidden ranking.** The garage narrows a list by a criterion the customer
  chose and can see. It never reorders results, and it never hides an item without saying so.
- **Always visible state.** On every page where the garage changes what is shown, a banner says
  what is active and offers *change* and *show all*.
- **No URL for a profile.** The garage never appears in a URL, so it cannot create crawlable
  variants, leak in shared links or fragment the shared page cache.
- **The customer's data stays small and theirs.** A profile is a catalogue node, an optional year
  and a name. Used only to filter and to answer "does it fit". No profiling.
- **Sector-neutral and operator-configured.** The levels (make, model, variant, year range) are
  data, not code.
- **Vendors own the fitment claims**, the platform checks their shape and shows them as "declared
  by the seller".

Summary of what this proposal asks lane-a to decide, in one place (full list in §17.12):
a new app `fitment`, a new prefix `FIT`, P1 in slices (owner, Q-G3), with the legacy `compatibility`
terms converted by FIT.15 before cutover, filter over ranking, anonymous garage in one
first-party cookie that asks consent until counsel confirms the user-interface-customisation exemption, no per-node
generated pages.

## 17.2 Data model

### 17.2.1 Where it lives

A new app `apps/fitment/` (taxonomy, product declarations, matching engine, garage profiles).
Why not extend the existing configurable `Taxonomy`/`Term` of the catalog
([03](03-catalog.md), the `compatibility` example "brand → model")?

- Fitment is **relational**, not a tag: a row says "fits node N, for years A to B" or "does not fit",
  has exclusions, a universal flag and a tri-state answer. A `Term` M2M cannot carry years or
  exclusions.
- The volume is of another order (hundreds of thousands of nodes and millions of rows in a
  large vertical), so the table needs its own indexes and bulk path.
- The customer's profile must point at a stable node id, and a node can be merged or renamed
  with an audited move of references, as `Brand` does (CAT.20).

The existing `compatibility` terms are **not thrown away**: they stay as terms with their legacy
URLs through cutover (CAT.2, MIG.3, MIG.11), and FIT.15 later converts them into fitment nodes.

App graph (spec 01 §1.3), no cycle: `accounts --> fitment`, `catalog --> fitment`,
`fitment --> cms`, `fitment --> events`, `fitment --> seo`, `ingest --> fitment`. The catalog does not
import `fitment`: CAT.8 and CAT.9 expose a small **listing predicate registry** (the same pattern as
`catalog/publishing.py` for compliance), and `fitment` registers its predicate at start-up. `tax`,
`shipping` and `orders` never import it (an order never stores the garage, §17.4.6).

### 17.2.2 The scheme: levels and nodes

```python
class FitmentLevel(Model):  # the operator's scheme: ordered levels, 1 to 4 node levels
    code = SlugField(unique=True)  # "make", "model", "variant"  (operator-defined, any sector)
    position = PositiveSmallIntegerField(unique=True)
    kind = CharField(
        choices=["node"]
    )  # a node level is a tree level; the year range is not a level
    translate_names = BooleanField(
        default=False
    )  # make and model names are usually the same in every language
    # + FitmentLevelTranslation(label, plural_label, placeholder)   # "Make", "Marke", "Marca"


class FitmentScheme(Model):  # singleton per installation
    garage_noun_key = (
        CharField()
    )  # gettext key: "Garage", "My devices", ... (theme or operator text)
    year_enabled = BooleanField()  # whether a year or year range exists in this sector
    year_min = SmallIntegerField()
    year_max_offset = SmallIntegerField()  # e.g. 1900 and +2 years
    contains_special_category = BooleanField(default=False)  # §17.4.5: forces session-only storage
    max_profiles = PositiveSmallIntegerField(default=10)


class FitmentNode(MP_Node):  # treebeard tree: depth = level position
    level = FK(FitmentLevel, PROTECT)
    name = CharField(120)  # canonical, NFC (I18N.10), language-neutral
    sort_key = CharField()  # natural sort ("Model 9" before "Model 10")
    status = CharField(choices=["proposed", "active", "retired"])
    source = FK(FitmentSource, null=True)
    external_ref = CharField(null=True)  # §17.3.5
    editorial_page = FK("wagtailcore.Page", null=True, on_delete=SET_NULL)  # hub article, §17.5.4
    # + FitmentNodeTranslation(language, name) only used when level.translate_names is true


class FitmentNodeAlias(
    Model
):  # typed variants: "Modelx", "Model-X", old names; matched after normalisation
    node = FK(FitmentNode)
    alias
    language = null
```

- The **levels are data.** One operator defines make → model → year
  range; another, brand → series → model; the code is identical. At most four node
  levels (a hard limit that keeps the cascading picker and the index shape simple).
- The **year range is not a level.** It is two integers on the declaration row and one optional
  integer on the profile. A year as a node level would multiply nodes by 30 for no gain.
- Changing the scheme is rare and audited (Configuration and tax role, MFA step-up, dry run): adding
  a level at the end and renaming labels are allowed; reordering or removing a level with nodes
  under it is refused (the operator merges or retires nodes first).
- Nodes are edited by the Catalog and moderation role in the Manager platform space: CRUD, merge
  (references moved in one transaction, the old node id answering as an alias for profiles), retire
  (kept in old profiles, hidden from pickers), and the review queue of proposals (§17.3.2).

### 17.2.3 How a product declares what it fits

```python
class ProductFitmentState(Model):  # one row per product that has any declaration
    product = OneToOne(catalog.Product, related_name="fitment_state")
    mode = CharField(choices=["undeclared", "specific", "universal"], default="undeclared")
    always_visible = (
        BooleanField()
    )  # denormalised: universal, or the category is not fitment-relevant (below)
    rows_version = PositiveIntegerField()  # bumped on every change, used by caches


class ProductFitment(Model):  # the fitment table: product x node
    product = FK(catalog.Product, on_delete=CASCADE)
    node = FK(FitmentNode, on_delete=PROTECT)  # any depth: a make row covers every model below it
    node_path = CharField()  # denormalised treebeard path, for the "profile is less specific" query
    years = IntegerRangeField(null=True)  # [year_from, year_to]; null = every year
    is_exclusion = BooleanField(default=False)  # "all models of the make, except this one"
    note = FK(
        FitmentNote, null=True
    )  # operator-defined short reasons, translated: "with adapter", "rear only"
    source = CharField(choices=["manual", "csv", "feed", "standard"])
    # constraints: unique (product, node, years, is_exclusion); years lower <= upper; max rows per product (setting, 500)
```

Category rule: `Category.fitment_relevant` (default **false**). Only items in a relevant category
are expected to declare fitment. An item in a not-relevant category (gift cards, books, a service) is
`always_visible`, and the garage never hides it. This avoids a whole class of "the filter hid the
gift card" bugs, and means the operator turns the feature on category by category.

**Matching (pure function `fitment/engine.py`, no ORM).** Input: a profile `(node, year or None)` and a
product's rows. The answer is one of three:

| Answer | When | Shown as |
|---|---|---|
| `fits` | `mode=universal`, or an include row whose node is the profile's node or an ancestor, with the year in the row's range (or no range), and no exclusion that covers the profile | "Fits <profile>" (or "Universal") |
| `check` | the profile is less specific than the row (the row names a variant, the profile only a model) or the profile has no year and the row has a range, and no exclusion fully covers the profile | "Check the fit: <variants/years>" |
| `no` | `mode=specific` and nothing above, or an exclusion fully covers the profile | "Does not fit <profile>" |
| `unknown` | `mode=undeclared` in a fitment-relevant category | "The seller has not declared fitment" |

Reading the rules:

- An exclusion beats an include at the same or an ancestor depth (it is the whole point of "all but").
- A profile at make level only (the customer does not know the model) matches rows below it as
  `check`, never `fits`: the garage must not claim certainty it does not have.
- `universal` items answer `fits` for every profile, and they are **never hidden** by the filter.
- `undeclared` items in a relevant category are hidden by an active garage, but disclosed
  ("N more items have no fitment information", §17.5.1), so the vendor's gap is visible to the
  customer and to the vendor's data-quality report, never silent.

### 17.2.4 Validation rules (service level, shared by every entry path)

- the node exists and is `active` (a row on a `proposed` node is stored inactive until approved);
- `year_from <= year_to`, both inside the scheme's allowed range;
- an exclusion needs a covering include row or a `universal` item, otherwise it is a warning
  ("an exclusion without anything to exclude from");
- the same node with overlapping year ranges and opposite exclusion flags is an error;
- at most the configured rows per product; a bigger set must use higher-level nodes;
- `mode=specific` needs at least one include row; `universal` needs none and rejects include rows;
- publish gate (setting per category, default off): a `buy` product in a fitment-relevant category
  cannot be published with `mode=undeclared`.

## 17.3 How vendors fill it

### 17.3.1 Manager shop space (CORE.9, CAT.19)

A "Fits" tab on the product form in `/manager/shop/products/`:

- a radio group *Specific items / Fits everything (universal) / Not declared*;
- a table of rows with a cascading picker (a typed search over node names and aliases with the
  hierarchy shown, native selects as the no-script fallback), year from and to, an *Excludes* checkbox and an optional
  note from the operator's list;
- *Copy from another product* and a bulk action in the product list: *Add fitment to the selected
  products* (with a ChangeSet preview, nothing applies before the vendor confirms);
- a per-vendor **data-quality report**: items in relevant categories still undeclared, rows pending on
  proposed nodes, rows that look suspicious (an exclusion without include).

Vendors only see and edit their own products (CORE.3 tenant isolation). The service
`set_product_fitment(product, mode, rows, source, dry_run=False)` returns a `ChangeSet`, writes an
`AuditLog` entry for a replace of more than one row, bumps `rows_version` and refreshes
`always_visible`.

### 17.3.2 A node the vendor cannot find

Same pattern as brands (CAT.20): the vendor *proposes* a node (parent, name), it stays `proposed`
and its rows inactive until Catalog and moderation approves, merges it into an existing node
(duplicate detection on normalised name plus alias) or refuses with a reason. An anti-flooding limit
on pending proposals per vendor (setting, default 20) applies; proposals arriving with an import run
are grouped into one batch per run, reviewed together, outside that limit. Vendors are never free to
create nodes directly: a node is shared by every vendor and by the customers' profiles.

### 17.3.3 Bulk CSV

Headers use the **level codes of the scheme**, so the format is the same in every sector:

```
sku;mode;make;model;variant;year_from;year_to;exclude
A-100;specific;Make One;Model A;;2016;2021;0
A-100;specific;Make One;Model A;Variant 2;;;1
A-200;universal;;;;;;
```

- `sku` is the CAT.13 match key (variants resolve to their product), per vendor.
- Load mode `replace` (default: the file is the full truth for the SKUs it names) or `add`.
- Names are matched after NFC, case and punctuation normalisation against node names and aliases;
  unmatched names do not create nodes: they become review items (and, for a trusted source, a
  batch proposal as in §17.3.2).
- Always a dry run first: a report of rows read, matched, unmatched, rejected (with the line and the
  reason), and the ChangeSet. Set-based upsert through a staging table, limits of 500 000 rows and
  50 MB per file, processed as a background task (ARC.9) with progress.
- The same module exports a vendor's fitment as the same CSV (round trip test, like SHP.14).

### 17.3.4 The import pipeline (ING)

- `MappingTable` gets a kind `fitment`: the supplier's own make/model strings (and codes) map to
  nodes, per vendor, with aliases (ING.1).
- Adapters emit `StagedFitment(row, raw_text, parsed_nodes[], parsed_years)` from supplier fields
  (a "compatible with" column, or text). Deterministic parsing first (ING.4 style).
- Because a wrong fitment claim makes customers buy parts that do not fit, text written or parsed by
  the AI `TextEnricher` (ING.5) is **never published automatically**: it lands in the review queue as
  a proposal. This is a deliberate exception to the "enrich then publish by policy" flow.
- Row provenance: `source` is kept per row. A feed refresh replaces only `feed` rows. A `manual` or `csv`
  row is never overwritten by a feed (consistent with D-14's "local fields").

### 17.3.5 Standard reference data (later, optional)

`FitmentSource(code, name, licence, version)` and `FitmentNode.external_ref` let the operator load a
reference catalogue of makes, models and generations (a standards body or a licensed data
provider) behind a `FitmentCatalogueProvider` interface, with a diff preview, no automatic
deletion of nodes in use and the licence terms stored with the data (databases may carry a
sui generis right, Directive 96/9/EC; the licence decides what may be stored and shown). The same
interface can later supply product-to-node compatibility lists keyed by MPN. It is a P3 row (FIT.16)
so that nothing depends on buying data; the manual and CSV paths work alone.

## 17.4 The garage

### 17.4.1 Profiles

```python
class GarageProfile(Model):
    public_id = UUIDField()
    user = FK(accounts.User, on_delete=CASCADE)
    name = CharField(40)  # chosen by the user; defaults to "<make> <model> <year>"
    node = FK(
        FitmentNode, on_delete=PROTECT
    )  # any depth; a profile may be only a make, or down to a variant
    year = SmallIntegerField(null=True)
    is_active = BooleanField(default=False)  # at most one per user (partial unique constraint)
    created_at
    last_used_at
```

- Several profiles per account (default maximum 10, a setting), **one active at a time, or none**.
  "None active" is the **generic** state, the default for everybody.
- Create, rename, duplicate, delete, *make active*, *reset (generic)*. **Reset keeps the profiles**:
  it only clears the active flag, exactly like switching a printer back from a preset to the
  defaults. Choosing a profile again from the menu makes it active again. Deleting a profile is the
  only way to lose it, and asks to confirm.
- A node that is later merged or retired keeps working in profiles (merged: the profile follows the
  merge; retired: the profile stays, shown with a note "this item is no longer in the catalogue",
  and matching treats it as before).

### 17.4.2 The anonymous garage

Anonymous visitors can use the garage without an account. The proposal is **one first-party
cookie, written only after an explicit user action** (pressing *Save* in the chooser), never at
first visit:

- cookie `mpmv_garage`: a versioned JSON of at most 2 KB, holding up to 3 profiles
  `{node id, year, name}` and which one is active. **No identifier, no session id, no counter.** It is
  `Secure`, `SameSite=Lax`, `Path=/`, not readable by third parties, 180 days from the last change
  (a setting);
- the server reads it on each request and **never stores it**: nothing about an anonymous
  garage is written to the database or to logs (the request log does not record the cookie value);
- every value is validated like hostile input (SEC.5): integers only, node must exist and be active,
  name NFC-normalised, 40 characters, HTML-escaped on output; an invalid cookie is ignored and cleared;
- a footer and a menu link *Forget my garage* removes it at once, with no confirmation text that
  has to be read.

Why a cookie and not `localStorage` or a server-side session: the server must know the active
profile on the first byte of a server-rendered page (no flash of the unfiltered list, works without
JavaScript, accessible); a server session would store a visitor-linked record for every anonymous
user, which is data we do not need.

**ePrivacy.** Directive 2002/58/EC article 5(3) (in Italy, article 122 of the privacy code and the
Garante's 2021 cookie guidelines) covers *any* storage on the terminal, cookie or `localStorage`
alike (EDPB Guidelines 2/2023). Consent is not needed for storage that is "strictly necessary to
provide a service explicitly requested by the user". The Article 29 Working Party's Opinion 04/2012
treats **user-interface customisation** cookies as exempt when they are set because the user chose a
setting (a language, a layout) and are limited to that purpose. A garage that the user created by
pressing a button, that holds no identifier and is used for nothing else, is argued to fit.
The condition list that keeps it inside the exemption: set only on the explicit action, only the
selection inside, no identifier, never read by analytics, advertising or the Consent Mode tags
(SEO.5), duration tied to the purpose, plain-language information next to the button
("This stays on this device so the shop remembers your choice. It is not used for anything else.").
A setting `GARAGE_ANON_STORAGE` has three values: `consent` (**the default until counsel answers**:
the *Save* button first asks for the CMP's functional/preferences category and, without it, the
anonymous garage works for the current visit only), `exempt` (the cookie is written on the explicit
action alone, switched on by the operator after counsel confirms the exemption) and `off` (current
visit only, in a session cookie). Counsel answers Q-G1 (⚖️, owner handles it); the design does not
wait for it and does not need a code change to move from `consent` to `exempt`.

### 17.4.3 Merge on login or sign-up

When a visitor with an anonymous garage signs in or creates an account:

1. the server compares: if the cookie holds profiles that the account does not have (same node and
   year counts as the same), the next page shows one non-modal notice: *"You have 2 profiles saved on
   this device. Add them to your account? [Add] [Not now]"*;
2. *Add* copies them (respecting the maximum; names kept, a numeric suffix on a clash), clears the
   cookie, and the account's own active profile stays active; if the account has none, the cookie's
   active one becomes active;
3. *Not now* leaves the cookie alone and asks again at the next sign-in, not on every page;
4. no silent merge: a shared computer must not copy a previous visitor's garage into a new account.

At sign-out the garage cookie is cleared if (and only if) it was created from the account's active
profile; a signed-in user's profiles live in the database, not in a cookie. The signed-in garage
needs no cookie at all.

### 17.4.4 The always-visible state

On every list that the garage filters (category, taxonomy, brand, vendor storefront, search, product
grids, blog index, event list) a region labelled "Garage" shows above the results:

> **Showing items for: Make One Model A 2019 ("Daily")** · 128 of 940 items · [Change] [Show all]

- *Change* opens the chooser (profiles to pick from and *Add a new one*).
- *Show all* turns the filter off **for this view of the session**, the banner then reads
  *"Garage Daily is on but not applied. [Apply]"*, so the person is never unaware: the
  state is a text banner in the same place, not a colour only.
- A header menu entry is always present: *Garage: Daily* when active and *Garage: generic* when not,
  opening the profile list with *Generic (show all)*, *Add*, *Manage*. It is a menu button with
  the standard disclosure pattern (keyboard, screen-reader names).
- The state changes only by an explicit action (a button or a form), which posts, redirects and
  announces the new state in a polite live region ("Garage reset: showing all items"). It
  never changes because of navigation, a link in an email, a shared URL or a crawler. There is **no
  URL parameter** for any of it (§17.6).
- No pop-up on arrival. A discreet, dismissible *Add your garage* line appears on listings and
  product pages for a visitor who has none; dismissing it hides it for the session. It never covers
  content, steals focus or repeats (dark patterns, DSA article 25).

### 17.4.5 GDPR

- **Is a garage personal data?** Linked to an account, yes: it is information relating to an
  identifiable person, and it can reveal something about them (what they own and use).
  Without any identifier (the anonymous cookie) it is, in our hands, not linked to a person and we
  do not store it; the user still holds it on their device. The proposal treats the account
  garage as personal data in every respect (export, erasure, notice) and the cookie as the user's own
  device data.
- **Special categories.** In some sectors the item may reveal health or other article 9 data. `FitmentScheme.contains_special_category`, set by the operator,
  turns the account garage off (anonymous session-only garage remains) unless the operator
  documents an explicit-consent flow. The default is off, because most schemes are not sensitive; the operator decides and records it in the processing register.
- **Legal basis.** Contract performance (art. 6(1)(b)) for a feature the user explicitly asked for
  and manages themself; legitimate interest (6(1)(f)) is not needed. No consent is required for
  the filter itself.
- **Purpose limitation (5(1)(b)).** The garage is used for three things only: filtering and the
  fit check, the editorial links and the event tags (§17.5). It is **not** used for: advertising or
  Merchant/Meta feeds, analytics events (the e-commerce events of SEO.5 carry no make, model or
  garage field), marketing emails, search ranking, price or shipping differences, vendor lead
  generation, or sharing with vendors. Vendors never see a customer's garage. A "new items for your
  garage" email, a personalised home page or an aggregated demand report would be new purposes and
  need their own decision and opt-in (outside this proposal, P3 at most).
- **Minimisation and storage (5(1)(c), (e)).** A node id, a year, a name, two dates. Retention for
  an account profile: until the user deletes it, or the account is erased (CMP.4), or **24 months
  without use** (a setting), after a service email 30 days before. The anonymous cookie: 180 days
  from the last change, and removable at any time.
- **Rights.** The garage is in the account export (access and portability, art. 15 and 20) and in the
  erasure (art. 17) of CMP.4, with a test. Notice (art. 13): the privacy page names the garage, its
  purposes and its retention (FIT.13).
- **No automated decision** with legal or similar effect (art. 22): a filter changes what is
  displayed, not rights, prices or terms. Fit answers are information, not a decision about the person.
- **Privacy by design (art. 25).** Generic is the default, the garage is opt-in, state is visible,
  deletion is one click, nothing is sent to third parties, logs and analytics do not carry it.
  A DPIA screening note is recorded (low risk, small-scale, no special category by default).

### 17.4.6 Orders do not store the garage

A fit check is shown at the point of choosing, not written to the order. Orders keep the existing
snapshots (price, tax, commission rule) and nothing about the garage, so the account export does not
grow and no personal "what they own" data reaches the ledger or the vendors. If counsel or a dispute
policy later wants the shown fit answer kept as evidence on the order line, that is a separate
decision with its own retention rule (open question Q-G6).

## 17.5 How the garage is used

### 17.5.1 Filter, not ranking (recommendation)

Two designs were compared:

| | Filter (recommended) | Ranking boost |
|---|---|---|
| Predictability | deterministic, the same query gives the same set | order changes opaquely per person |
| Customer control | clear: "N items fit", *show all* one click away | hard to see or undo |
| Legal exposure | not a recommender (§17.9), no ranking parameters to disclose for consumers | needs ranking transparency (Omnibus, P2B, DSA 27) |
| Fairness to vendors | a vendor is visible if the fitment is declared, an auditable rule | an invisible weight |
| Cost | one indexed predicate | scoring in every listing |
| Failure mode | an undeclared item is hidden: mitigated by the disclosure line and the vendor data-quality report | a fitting item can be buried below non-fitting ones |

A filter also matches the owner's words ("show only"). **Rule:** the active garage filters every list
that has a fitment meaning; it never reorders; sorting stays whatever the listing sorts by.

What is shown under an active garage in a fitment-relevant context:

- `fits` and `check` items, and `always_visible` items (universal, or not-fitment-relevant category);
- `check` items carry the badge "Check the fit";
- `no` and `unknown` items are hidden and counted: *"128 of 940 items fit. 31 more have no fitment
  information. [Show all]"*.

### 17.5.2 Listings and search

- CAT.8 listings (category, taxonomy, brand, vendor storefront) and CMS.10 product grids accept the
  registered predicate; the active garage is read from the request context (user or cookie), never
  from a parameter.
- CAT.9 search: the predicate filters the matches **after** the FTS and SKU ranking and does not
  change their order. An **exact SKU, GTIN or MPN match is never filtered out**: it is shown
  with the "does not fit <profile>" marker, so a customer who types a part number is never
  blocked by the garage.
- The result count and the "N of M" figures come from the same query shape (one extra count).
- Search suggestions (autocomplete) do not use the garage in v1.

### 17.5.3 Product page: "Does it fit?"

The product page is **always reachable** whatever the garage says: a link from a search engine, an
email or another customer must not become a 404 or a different page.

- With an active garage a box shows the answer in text and an icon (never colour alone):
  *Fits Daily (Make One Model A 2019)*, *Check the fit: it fits Variant 1 and Variant 2*,
  *Does not fit Daily*, or *The seller has not declared fitment*. It states that **the fitment is
  declared by the seller**, and links to the full list and to the garage chooser. The box is
  titled **"Compatibility declared by the seller"** and carries one line, linked to the vendor
  terms: the platform does not guarantee the declaration, the seller answers for it, and an item
  that does not fit as declared is a non-conformity (Directive (EU) 2019/771: the buyer's remedies
  and returns apply as for any non-conforming item; the right of withdrawal is unaffected).
- Without a garage the box invites: *Does it fit your <item>? [Choose in your garage]*; the chooser
  returns to the same page.
- The full fitment list is rendered for everybody as a summary grouped by make and model
  (`<details>` for long lists), so the page content is the same for every visitor and crawler.
- The price, stock and the buy button are never affected by the answer. Buying an item flagged
  *does not fit* is possible (the customer may know better), with one inline note and no extra step.

### 17.5.4 Editorial articles linked to fitment (owner input 1)

There are **no generated make or model pages.** A node has a public page only if an editor has
written one.

- BlogPostPage (CMS.3) and TermLandingPage (CMS.4) get an optional `fitment_nodes` chooser
  (nodes at any depth, several allowed). An article with no node is *general* and is always shown.
- `FitmentNode.editorial_page` marks the **hub article** of a node, at most one per node and per
  locale (wagtail-localize translates it like any page). Its URL is an ordinary blog URL and stays
  stable when the node is renamed.
- On such an article a *Compatible items* block lists products by the fitment query for that node
  (not hand-picked, so it never goes stale), using the CAT.8 listing component, with a
  *Make this my garage* button (a form post, never a link).
- The product page links to the hub article of any node it fits, the article to the matching
  listing filter, both server-side.
- A staff report in the Manager shows **nodes with many fitting products and no article**, the
  editors' work list, and articles whose nodes no longer exist or have no products.
- With an active garage the blog index offers the same *Showing articles for <profile>* banner;
  articles linked to the profile's node, its ancestors or its descendants are listed, plus every
  general article. Hub articles for the profile's node can be shown first as a "Guides for your
  <item>" strip (this is a labelled section, not a reorder).
- Migration: the existing articles per model of the legacy site are loaded as blog posts (MIG.3)
  and an editor-assisted mapping links each to a node (FIT.15), with the report of unmatched ones.

### 17.5.5 Events

Events (spec 15) are sparse and local. Hiding them by garage would often give an empty list, so:

- `EventPage` gets an optional `fitment_nodes` chooser set by the organiser;
- with an active garage the event list shows a **badge** "For <profile> owners" on matching events
  and a checkbox *Only events for my garage* (off by default, remembered like the garage itself);
- an event without nodes is general. Feeds (RSS, ICS, SEO.7) ignore the garage.

### 17.5.6 Empty and edge states

- **Filter returns 0:** *"No items in this category fit <profile>."* with buttons *Show all items in
  this category*, *See where items that fit are* (the other categories with a count) and *Change
  garage*, plus the contact link. No dead end, no blank list.
- **Profile node with no products anywhere** (a model nobody declared): *"We have no items that fit
  <profile> yet."* and the same options; no email sign-up (that would be a new purpose).
- **Profile too coarse** (make only): results are `check`; the banner offers *Add model and year*.
- **Stale cookie or deleted profile:** ignored and cleared, nothing breaks.
- **Language switch** keeps the garage (nodes are language-neutral) and the labels follow the language.
- **Pagination, sorting and other filters** compose with the garage as intersections.

## 17.6 SEO

- **What is indexed:** products, categories, terms, brand pages, vendor storefronts and the
  **editorial hub articles per make and model** (owner input 1). The fitment summary on each
  product page is plain server-rendered text, identical for every visitor and every crawler.
- **What is not indexed:** nothing is generated per node or per profile. There is no
  `/fits/<make>/<model>/` listing, no faceted garage URL, no parameter. The two garage actions are
  form posts with a redirect to the same URL, so **no crawlable per-profile URL can exist**.
- **Personalised views.** A personalised response differs from the generic one only when the request
  carries the garage cookie or a signed-in session; a crawler has neither, so it always receives the
  generic page. The server never varies by user agent (that would be cloaking). Every list
  keeps a self-referencing canonical to its generic URL; the personalised HTML
  carries the same canonical and `Cache-Control: private` (§17.7). The proposal deliberately does
  **not** add `noindex` on top of the canonical: the two signals together are contradictory, the
  canonical alone already points any stray copy at the generic page.
- **Sitemap:** hub articles appear like any blog post with their hreflang; there is no sitemap
  entry for a garage state.
- **Structured data (SEO.7).** Product: `isAccessoryOrSparePartFor` with up to 10 nodes as `Product`
  nodes (`name`, and `sameAs` to a reference id when FIT.16 supplied one), taken from the most
  specific rows; the full list is on the page, not in the graph. BlogPosting: `about` points to the same
  nodes. The graph **never** contains the visitor's garage.
- **Internal links:** the product page links to the hub article of its nodes, articles link to the
  listing filter results through the *Compatible items* block, so the editorial pages receive links
  from the commercial pages they describe (the owner's reason for writing them).
- **Legacy URLs** of model articles keep their redirects through CAT.6 and MIG.11.

## 17.7 Performance and caching (SEO.6)

**Recommendation: personalised responses are not shared-cacheable. Everything else keeps the
anonymous page cache.**

- A request with no garage (no cookie, no active profile) is **identical to today** and uses the
  shared page cache. This is the majority and every crawler.
- A request with the garage cookie, or from a signed-in user with an active profile, gets
  `Cache-Control: private` and `Vary: Cookie`. The edge rule
  bypasses the shared cache when the `mpmv_garage` cookie is present (cheap: the cache key
  ignores every other cookie). Signed-in pages are already private.
- **Why not a cache key per profile:** with an anonymous cookie there is no profile id to key on, only
  `(node, year)`, and the product of listings × pages × languages × nodes × years collapses the hit rate
  to near zero while publish-time invalidation (SEO.6) becomes far more expensive. A hash of a profile
  id would be unique per user and give no sharing at all. Keying by node only would still multiply
  the cache by the number of nodes with traffic.
- **Where the cost goes instead:** the database. The fitment predicate is a semi-join on an index:

```sql
-- ancestors = the profile node's path prefixes (<= 4 ids), computed in Python
WHERE ps.always_visible
   OR ( EXISTS (SELECT 1 FROM fitment_productfitment f
                WHERE f.product_id = p.id AND NOT f.is_exclusion
                  AND f.node_id = ANY(%(ancestors)s)
                  AND (f.years IS NULL OR f.years @> %(year)s::int))
        AND NOT EXISTS (SELECT 1 FROM fitment_productfitment x
                WHERE x.product_id = p.id AND x.is_exclusion
                  AND x.node_id = ANY(%(ancestors)s)
                  AND (x.years IS NULL OR x.years @> %(year)s::int)) )
```

  Indexes: `(node_id, product_id) WHERE NOT is_exclusion`, `(node_id, product_id) WHERE is_exclusion`,
  `(product_id)`, `(node_path text_pattern_ops)` for the `check` tier (prefix match below the
  profile node), and a partial index on `ProductFitmentState(product_id) WHERE always_visible`. The
  year test is a post-filter after the node join; a GiST index on `years` is only added if the
  EXPLAIN budget needs it.
- **Budget (FIT.14):** a generated dataset of 200 000 products and 3 million fitment rows; a
  category listing page with an active garage, p95 under 150 ms of database time and the page
  within the SEO.6 LCP budget; a measured EXPLAIN test fails the build when the plan stops using the
  indexes. The count query for "N of M" is limited to the listing's own filter set.
- **Optional second step (only if the budget fails):** an application-cache entry of the matching
  product id set per `(node, year)` while the set is below 50 000 ids, invalidated by a global
  `fitment_version` counter bumped on any fitment write. Not built first.
- **Write path:** bulk replace is set-based (a staging table, one `INSERT ... ON CONFLICT`), the
  search/listing denormalised columns refresh in the same transaction.
- A change of fitment does not purge pages (the generic pages show a summary that changes): it
  purges the product page and the hub article of the affected nodes through the existing
  publish-time invalidation (SEO.6).

## 17.8 Internationalisation

- Level labels, the garage noun, notes and the interface are translatable (gettext and
  `FitmentLevelTranslation`, `FitmentNote` translations through `TranslationModel`, I18N.2). The
  garage noun is an operator text per language ("Garage", "My items"), because other
  sectors need "My devices".
- **Make and model names are not translated** by default (`translate_names=False`): a trademark and a
  model name are the same everywhere. A level whose names are real words (a generation called
  "Third generation") can turn the flag on and use `FitmentNodeTranslation`.
- Matching is on node ids, so profiles, rows and links are language-neutral. Search over nodes uses
  normalised names plus aliases and, for translatable levels, the translated names; sorting uses a
  locale-aware collation and a natural sort key.
- Years and ranges are formatted by the locale ("2016–2021", "from 2016"). The fitment sentence uses
  gettext plural and placeholder rules, not string concatenation.
- Hub articles are wagtail-localize pages; a missing translation of an article never shows a
  fallback copy as another language's article (I18N.6 rules apply).

## 17.9 EU rules

| Rule | Does it apply | What the design does |
|---|---|---|
| **GDPR** (2016/679) | the account garage is personal data | §17.4.5: basis 6(1)(b), purposes limited, minimal data, retention, export and erasure, notice, no art. 22 decision, privacy by design, special-category switch |
| **ePrivacy** art. 5(3) | storage on the terminal (the anonymous cookie, also `localStorage`) | explicit user action, no identifier, UI-customisation exemption argued (WP29 04/2012, EDPB 2/2023) but not relied on at launch: `GARAGE_ANON_STORAGE` defaults to `consent`, `exempt` once counsel agrees (Q-G1) |
| **Omnibus** (2019/2161), Consumer Rights Directive art. 6(1)(ea) | main parameters of ranking of results presented after a search | Our reading: a filter the consumer chooses and can switch off is the consumer's own criterion, not a ranking the trader determines, and the order is unchanged. Nothing in this design boosts or demotes. To be safe the banner links to a "How the garage works" page, and the existing ranking page (CMP.5) states that fitment declaration affects what an active garage shows. **Counsel confirms (⚖️, Q-G2).** If a boost is ever added, art. 6(1)(ea) applies in full |
| **P2B** (2019/1150) art. 5 and 7 | the garage changes vendors' exposure | yes, in practice: an undeclared item is hidden under an active garage. The ranking/exposure page (CMP.5) lists "declared fitment and `universal` status" as a parameter and says how it works; no preferential treatment of the operator's own items (art. 7), tested by FIT.8 |
| **DSA** art. 27 (recommender systems), art. 3(s) | a system that *suggests or prioritises* information | a user-chosen filter suggests and prioritises nothing, so we read it as out of scope; the "where items that fit are" and "Guides for your <item>" strips are deterministic and explained. Art. 27 sits in the section that micro and small platforms are exempt from (art. 19), but the terms (CMP.5) describe it anyway. Art. 38 (a non-profiling option) is met by the generic state. Art. 25 (dark patterns): no nag, no pre-selection. Art. 26(3): no advertising by profiling, no special-category data |
| **UCPD** (2005/29/EC) | "fits your X" is a claim to the consumer | "Compatibility declared by the seller" (Q-G8), the `check` tier, `unknown` stated openly, no claim of certainty the data lacks; vendors answer for their declarations in the vendor terms, with the same notice-and-action path (CMP.5) for wrong fitment |
| **Trade mark use** (Directive 2015/2436 art. 14(1)(c)) | make and model names are often trade marks | allowed to indicate the intended purpose of an accessory or spare part when in line with honest practices: names only, no makers' logos or implied endorsement, wording "fits" not "official" |
| **Database right** (96/9/EC) | only for FIT.16 reference data | licence stored with the data, no copying beyond it |
| **Accessibility** (EAA, EN 301 549, WCAG 2.2 AA, CMP.7) | the whole UI | native `<select>` and `<button>` controls, visible labels, a polite live region for the state change (4.1.3), an explicit *Apply* instead of auto-submit on change (3.2.2), no drag, targets at least 24 px (2.5.8), the banner in text with an icon (1.4.1), focus returned to the banner after a change, consistent help link (3.2.6), the chooser state remembered so it is not asked twice (3.3.7), an axe and a keyboard e2e test of the whole flow |

## 17.10 Alternatives rejected

- **Ranking boost** (personalised sort): see §17.5.1.
- **Generated landing pages per node** (programmatic SEO): the owner asked for editorial articles;
  generated pages would also create thin duplicate content and a URL explosion.
- **Garage in the URL** (`?fits=...`): shareable but crawlable, cache-fragmenting, and it leaks
  the person's data in logs, referrers and screenshots.
- **Reusing `Term` trees for fitment**: no years, no exclusions, no bulk path (§17.2.1).
- **One server-side session record per anonymous visitor**: stores data we do not need.
- **Client-side filtering of a cached page**: flash of unfiltered content, breaks without
  JavaScript, cannot paginate by fit, fails accessibility expectations.

## 17.11 Regression and acceptance cases

1. A product that fits Make One Model A 2016–2021 is `fits` for 2019, `no` for 2022, `check` when
   the profile is Model A with no year, and `check` for a make-only profile.
2. An exclusion on Variant 2 under an include on Model A: `fits` for Variant 1, `no` for Variant 2.
3. A `universal` item and a gift card (not-relevant category) are visible under every profile; a
   relevant `undeclared` item is hidden and counted.
4. Search for an exact SKU of a non-fitting item shows it, marked "does not fit".
5. The product page of a non-fitting item answers 200 with the same canonical and the same
   structured data as without a garage.
6. No URL, link, sitemap entry or JSON-LD contains a garage, and a crawl with the cookie set gives
   the same crawlable URL set as one without.
7. Reset keeps the profiles; selecting one again makes it active; deleting asks to confirm.
8. Anonymous cookie: nothing stored server-side, hostile cookie ignored, at most 3 profiles,
   sign-in shows the merge notice once, *Not now* does not merge, a shared computer never
   merges silently.
9. A shared-cache test: two anonymous requests without the cookie share one cached page; a request
   with the cookie bypasses it and is `private`.
10. The account export contains the profiles, erasure removes them; the analytics events carry no
    garage field; vendors cannot read any profile (negative cross-tenant test).
11. A scheme change that would orphan nodes is refused; a node merge moves rows and profiles.
12. A vendor CSV: dry run lists unmatched names; `replace` removes only the named SKUs' rows; a
    feed refresh never overwrites `manual` rows; AI-parsed fitment lands in review, not published.
13. The EXPLAIN budget of §17.7 on the generated dataset.

## 17.12 Owner questions: decided and open

| ID | Question | Proposal |
|---|---|---|
| Q-G1 ⚖️ | Counsel: is the anonymous garage cookie exempt from consent under the user-interface-customisation exemption, as designed? | **With counsel (owner handles it), not a blocker.** Prudent design: `GARAGE_ANON_STORAGE=consent` by default; `exempt` is an operator switch once counsel agrees |
| Q-G2 ⚖️ | Counsel: confirm that a consumer-chosen filter is not a ranking under Omnibus art. 6(1)(ea) and not a recommender under DSA art. 27 | **With counsel, not a blocker.** The "How the garage works" page and the CMP.5 ranking and terms text are updated anyway (FIT.13) |
| Q-G3 ✅ | Priority and timing | **Decided (owner, 2026-11-10): P1, before opening, the whole garage** (not only the fit box), in the slices of §17.13 that merge one by one |
| Q-G4 ✅ | Which categories are fitment-relevant? | **Decided (owner):** a setting the operator fills (`Category.fitment_relevant`, bulk-settable in the platform space), **seeded with none**: until the operator marks categories, the garage filters nothing |
| Q-G5 | Undeclared items under an active garage: hidden and counted (proposal), or shown in a separate collapsed section? | Hidden and counted, with *Show all* |
| Q-G6 | Should the fit answer shown at purchase be kept on the order line as dispute evidence? | No: orders never store the garage (§17.4.6) |
| Q-G7 | Level limits: at most 4 node levels and 3 anonymous profiles, 10 per account, 500 rows per product, 24 months inactivity: acceptable defaults? | Yes, all settings |
| Q-G8 ✅ | Vendor liability wording and the return rule | **Decided (owner):** the wording is "Compatibility declared by the seller"; the platform does not guarantee it; the seller answers for its declaration; a wrong fit is a non-conformity return (§17.5.3, vendor terms through FIT.13) |
| Q-G9 | Is a reference catalogue (licensed standard data) in scope at all, and who pays the licence? | Open. FIT.16 stays P3 and optional: the garage works without it |
| Q-G10 | A "new items for your garage" email or a personalised home block: wanted later? | Not in this proposal; each needs its own opt-in and decision |

## 17.13 Tasks

The rows are no longer blocked by a proposal: D-38 is decided as P1 in slices, with only the counsel items (Q-G1, Q-G2) open and not blocking. IDs are checked against `main` and
every open pull request. Owner decision Q-G3: **P1, before opening, the whole garage**. FIT.16 (optional reference data) stays P3.

### Slices that merge one by one

Each slice is a coherent, testable set that can merge alone and leaves the system working with the
garage still invisible to customers until the slice that shows it.

| Slice | Rows | Hours | Contents | Needs first |
|---|---|---:|---|---|
| FIT-a | FIT.1, FIT.2 | 32 | scheme, nodes, product declaration, matching engine | CAT.2, CAT.14, CORE.17, CORE.18 |
| FIT-b | FIT.3, FIT.4 | 28 | vendor entry (Fits tab, node proposals) and CSV with ING | FIT-a, CAT.19, ING.1 |
| FIT-c | FIT.5, FIT.6, FIT.7 | 36 | garage profiles, anonymous cookie and merge, the garage UI | FIT-a, THM.4, SEO.5 |
| FIT-d | FIT.8, FIT.9, FIT.14 | 28 | listing and search filter, product page box, performance budget | FIT-a, FIT-c, CAT.8, CAT.9, CAT.7 |
| FIT-e | FIT.10, FIT.11, FIT.12 | 26 | editorial hubs, events, SEO and cache rules | FIT-d, CMS.3, CMS.4, EVT.1, SEO.7 |
| FIT-f | FIT.13, FIT.15 | 14 | legal and transparency text, legacy carry-over | FIT-c (FIT.13), FIT-e and MIG.3 (FIT.15) |

Total 164 h of P1. FIT-a and FIT-b can start as soon as CAT and the Manager shell exist and are the
data the others need; FIT-c can run in parallel with FIT-b; FIT-d after both; the garage opens to
customers when FIT-c, FIT-d and FIT.13 are merged. FIT.15 must land before the MIG.11 URL switch.

### Effect on the existing P1 plan

P1 goes from 1,781 h to 1,945 h (+164 h, about +9%; with the acceptance, review and coordination
margins of [91](91-estimates.md) §91.2 the go-live total grows by about 190 h). No existing P1 row
gets a dependency on FIT, so no existing row is blocked. The P1 critical path (the schedule lower bound
with full parallelism) grows by **6 h, 147 to 153 h**, because the longest FIT chain (CORE.18, FIT.1,
FIT.5, FIT.7, FIT.8, FIT.10, FIT.12) is now the longest path. What really moves is capacity and order. The garage touches the same pages and the same lane, so these P1 rows that are
still open are sequenced **after** the slice that gives them a garage slot (a proposal for lane-a to
confirm, not a new dependency edge):

- THM.7 (catalog pages of the `ride` theme, 6 h) and THM.10 (visual baselines, 4 h) after FIT-c, so
  that the baselines already contain the garage menu and banner (otherwise they are re-taken);
- SEO.7 (site-wide graph, 8 h) and SEO.1 (product JSON-LD, 10 h) are finished first and then
  extended by FIT.12: they are not delayed, FIT.12 is;
- EVT.6, EVT.7 and EVT.10 (event public pages and JSON-LD, 17 h) are finished first, FIT.11 extends them;
- the lane-b content rows CMS.5, CMS.11 and the open EVT rows (EVT.4, EVT.8, EVT.9, EVT.11, EVT.14,
  EVT.15, about 59 h) lose about one week of lane capacity to FIT-c to FIT-e (the lane clears about 500
  estimated hours a week, §91.4, so 164 h is roughly a third of a week of the whole budget);
- ING.4 and ING.5 (import extraction and enrichment, 18 h) keep their order but FIT.4 needs ING.1 early:
  ING.1 is pulled ahead of ING.2 and ING.3 if FIT-b is to start before the adapters;
- two rows lose a dependency they would otherwise have gained and stay where they are: SEO.6 (P2)
  and ORD.11 (P2) are **not** prerequisites (FIT.12 writes the cache rule into the SEO.6 contract; the
  "My garage" page is a minimal customer-area page that ORD.11 later embeds).

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| FIT.1 | Scheme and nodes (D-38, slice FIT-a): `FitmentScheme`, `FitmentLevel` (up to 4 node levels, translated labels), `FitmentNode` tree with status, aliases, optional translated names, `FitmentNote`; Manager platform pages for the scheme (Configuration and tax, step-up, dry run, no reorder or removal when nodes exist) and for nodes (Catalog and moderation: CRUD, merge with references moved, retire, proposals queue); `Category.fitment_relevant` as an operator setting seeded with none, bulk-settable | P1 | 16 | CAT.2, CORE.17, CORE.18, I18N.2 | A two-level and a four-level scheme are created in two sectors' vocabularies; a scheme change that would orphan nodes is refused; a node merge moves the references; a proposed node is invisible to pickers until approved |
| FIT.2 | Product declaration (D-38, slice FIT-a): `ProductFitmentState`, `ProductFitment` with year range, exclusion, note, source, constraints, validation rules of §17.2.4, pure `fitment/engine.py` (`fits`, `check`, `no`, `unknown`), `set_product_fitment` with dry run, ChangeSet and AuditLog, `always_visible` and `rows_version` upkeep | P1 | 16 | FIT.1, CAT.14 | The regression cases 1 to 3 of §17.11 pass as unit tests with no database; an overlapping contradictory row is refused; a replace of several rows writes one AuditLog entry |
| FIT.3 | Vendor Fits tab in the shop space (D-38, slice FIT-b): mode radio, cascading node picker with search and a no-script fallback, year fields, exclusion, notes, copy from another product, bulk action with ChangeSet preview, node proposals with an anti-flooding limit, per-vendor data-quality report | P1 | 14 | FIT.2, CAT.19, CORE.9 | A vendor sets fitment on a product and on 50 products in bulk; a proposal is pending and invisible until reviewed; the 21st pending proposal is refused; a cross-tenant negative test passes |
| FIT.4 | CSV import and export of fitment and the ING link (D-38, slice FIT-b): headers from the level codes, `replace` and `add`, dry run report, set-based upsert through staging, review items for unmatched names, `MappingTable` kind `fitment`, `StagedFitment`, provenance rules, AI-parsed fitment never auto-published | P1 | 14 | FIT.2, ING.1, ING.5 | A 100 000-row file loads in a background task with a report; round trip export equals import; a feed refresh leaves `manual` rows alone; an AI-parsed row lands in review |
| FIT.5 | Garage profiles for accounts (D-38, slice FIT-c): `GarageProfile` (one active at most, reset keeps profiles, rename, duplicate, delete with confirmation, merged and retired node handling), limits, the "My garage" page in the customer area, 24-month inactivity cleanup with notice, CMP.4 export and erasure hooks, `contains_special_category` switch | P1 | 12 | FIT.1, CORE.1, CMP.4 | Regression case 7 and 10 pass; export contains the profiles and erasure removes them; with the special-category switch the account garage is unavailable |
| FIT.6 | Anonymous garage and merge (D-38, slice FIT-c): the `mpmv_garage` cookie (explicit save, up to 3 profiles, no identifier, hostile-input validation, 180 days, *Forget my garage*), `GARAGE_ANON_STORAGE` modes `exempt`, `consent`, `off` wired to the SEO.5 consent state, merge notice at sign-in without silent merge, cookie cleared at sign-out when it came from the account | P1 | 10 | FIT.5, SEO.5, ORD.1 | Regression case 8 passes; no server write and no log of the cookie value; the three storage modes are tested and the default is `consent`; analytics tags never read the cookie |
| FIT.7 | Garage UI (D-38, slice FIT-c): header menu, banner with *Change* and *Show all*, chooser (explicit *Apply*), "Add your garage" line, polite live announcements, focus handling, empty states of §17.5.6, no URL parameter anywhere, the translatable garage noun | P1 | 14 | FIT.5, THM.4, I18N.1 | An e2e run with screenshots covers the flow with keyboard only; zero axe violations (`wcag22aa`); reload, language switch and back button keep the state; no garage in any URL |
| FIT.8 | Listings and search (D-38, slice FIT-d): predicate registry in CAT.8 and CAT.9, registered by `fitment`; filter semantics of §17.5.1, counts "N of M" and the unknown-count, exact SKU, GTIN or MPN always shown, CMS.10 grids, no reordering, `check` badge | P1 | 14 | FIT.2, FIT.7, CAT.8, CAT.9, CMS.10 | Regression cases 3 and 4 pass; an active garage never changes the sort order of a listing; the count equals the rows listed; the operator's own items get no preference |
| FIT.9 | Product page (D-38, slice FIT-d): "Does it fit?" box in four states, summary list of fitment grouped by make and model, links to hub articles, card badges, buy flow unaffected | P1 | 8 | FIT.8, CAT.7 | Regression case 5 passes; a non-fitting item is purchasable with one inline note; the summary is identical with and without a garage |
| FIT.10 | Editorial links (D-38, slice FIT-e): `fitment_nodes` chooser on blog posts and term landing pages, `editorial_page` hub article per node and locale, *Compatible items* block from the fitment query, *Make this my garage* form button, product-to-article links, the staff coverage report, the blog index garage banner | P1 | 12 | FIT.1, FIT.8, CMS.3, CMS.4 | An article linked to a node lists the matching items without hand-picking; a node with 50 items and no article appears in the report; a general article is always shown; a second hub article for the same node and locale is refused |
| FIT.11 | Events (D-38, slice FIT-e): `fitment_nodes` on `EventPage` (organiser chooser), the "For <profile> owners" badge and the *Only events for my garage* checkbox (off by default), feeds ignore the garage | P1 | 6 | FIT.1, FIT.7, EVT.1 | A tagged event gets the badge; the checkbox filters; the RSS and ICS feeds are the same with and without a garage |
| FIT.12 | SEO and cache rules (D-38, slice FIT-e): self-canonical to the generic URL, `Cache-Control: private` and `Vary: Cookie` on personalised responses (no `noindex`, canonical only), edge bypass on the garage cookie, JSON-LD `isAccessoryOrSparePartFor` (up to 10) and BlogPosting `about`, hub articles in the sitemap, cache purge on fitment change | P1 | 8 | FIT.8, FIT.10, SEO.7 | Regression cases 6 and 9 pass (the bypass rule is also written into the SEO.6 cache contract and tested there when SEO.6 lands); the generic and personalised crawl give the same URL set; Rich Results Test has no errors; no garage in any JSON-LD |
| FIT.13 | Legal and transparency text (D-38, slice FIT-f): privacy notice and processing register entry, DPIA screening note, the "How the garage works" page, the CMP.5 ranking and terms entries (declared fitment as an exposure parameter), vendor terms wording ("Compatibility declared by the seller": no platform guarantee, the seller answers for it, a wrong fit is a non-conformity return), counsel review of Q-G1 and Q-G2 | P1 | 6 | CMP.4, CMP.5, FIT.5 | The texts exist in the five languages; counsel's answers are recorded; the ranking page lists the fitment rule |
| FIT.14 | Performance (D-38, slice FIT-d): dataset generator (200 000 products, 3 million rows), EXPLAIN budget test (p95 database time under 150 ms with an active garage), the optional matched-set cache only if the budget fails, bulk write timing | P1 | 6 | FIT.8 | The budget test passes in CI and fails when an index is dropped |
| FIT.15 | Legacy carry-over (D-38, slice FIT-f): convert the migrated `compatibility` terms into fitment nodes (dry run, report, aliases from the legacy names), map the legacy model articles to nodes with an editor-assisted list, keep all legacy URLs | P1 | 8 | FIT.1, FIT.10, MIG.3, MIG.11 | Every legacy compatibility term maps to a node or is listed as unmatched; each legacy article is linked to a node or in the unmatched report; no legacy URL changes |
| FIT.16 | Standard reference data import (D-38, optional): `FitmentSource`, `FitmentCatalogueProvider` interface, a file provider, diff preview, licence stored, no automatic deletion of nodes in use, `external_ref` and `sameAs` | P3 | 14 | FIT.1, FIT.4 | A sample reference file loads with a diff preview; nodes in use are never deleted; the licence is stored with the source |
