# 16 — Trust badges, customer reviews and community

Design for decision D-37 (owner idea of 2026-11-10): vendor trust badges, a customer review
system, and a possible social or blog section. Status: **approved by the owner on 2026-11-13**,
with Q-T1 to Q-T13 answered the same day; counsel answered Q-T7 and Q-T8 (legal basis of review
invitations and of reviews) on 2026-11-13 and the answers are built in below. The rows in §16.12 are
P2 and P3 and none blocks go-live. The design was written independently of any other notes, so it
also says what it would not build.

## 16.1 What exists today

- **Reviews**: the legacy shop has none on its pages, and the new spec only has a stub. [10](10-content-seo-ai.md)
  "Reviews" says verified purchases, supplier reviews with declared origin and date, staff moderation,
  vendor reply, and Omnibus transparency; CMS.6 is one P2 row. The model, the rules and the
  display were never designed. This document designs them and CMS.6 keeps its ID.
- **Supplier reviews**: ING.11 imports them. They are not purchases on MPMV (§16.3).
- **Authors**: CMS.3 defines article authors as translatable snippets with a bio, `sameAs` links and a
  `Person` JSON-LD node. They are editorial staff or vendors, not customers.
- **Order data** that badges can use already exists in [07](07-cart-orders-payments.md):
  `VendorOrder` (`state`, `shipped_at`, `delivered_at`, shipping snapshot with the destination
  country) and `ReturnRequest` (`kind`, `state`). VEN.20 gives the dispatch date shown at checkout,
  VEN.5 the vendor activation date, CMP.11 the trader verification data.
- **Rules that already bind us**: no generic environmental claims (CMP.10, `GREEN`), no
  Google script before consent, CSP without inline script ([14](14-frontend-themes.md)), a rich-text
  sanitiser for anything not typed by an editor (CMS.12), retention per D-29 and D-30.

## 16.2 Principles

1. **A badge is a fact computed from our own data, never a purchase and never a vendor claim.**
   A plan, a fee or a commission level never buys or hides a badge.
2. **Every badge says what it counts, over which period, and as of when.** "184 orders delivered
   through MPMV, as of 2027-03-02", not "trusted seller".
3. **Buyers are anonymous by default.** Showing a nickname or any detail is an explicit,
   revocable opt-in, and withdrawal takes effect everywhere at once.
4. **Reviews cannot be bought, filtered by the vendor or deleted by the vendor.**
5. **Thresholds are data** (versioned `TrustRules`, staff-editable with audit, like ARC.24), not code.
6. **No new third-party scripts and no social network.** §16.8 lists what is out.

## 16.3 The customer review

### What a review is

There are two kinds, both written by a buyer about something they bought, both tied to a delivered
order. Keeping them apart avoids a product review that is really a complaint about shipping.

| | `ProductReview` | `VendorReview` |
|---|---|---|
| About | one `OrderLine`'s product (the product, not the variant) | one `VendorOrder` as a whole: dispatch, packaging, communication, as described |
| Rating | 1 to 5 whole stars | 1 to 5 whole stars |
| Text | optional, 20 to 2,000 characters, plain text | optional, same limits |
| Limit | one per order line | one per vendor order |
| Shown on | product page, product JSON-LD | vendor storefront, vendor trust panel, never in product JSON-LD |
| Drives badges | no | yes (the reviews badge, §16.4) |

Fields shared by both (`trust.models`): `vendor_order` (PROTECT; the order record outlives the
account under D-29), `reviewer` (nullable FK to the user, empty for guest checkout buyers),
`rating`, `body`, `language` (detected, editable), `state` (`pending_check`, `published`, `held`,
`removed`, `withdrawn`), `published_at`, `edited_at`, `origin` (`mpmv_verified`, `supplier_import`),
`order_refunded` (internal flag), and an append-only `ReviewRevision` table (previous text and
rating, the time). A vendor reply is a separate `ReviewReply` row (one per review, 1,000 characters,
its own revision history, same `state` values).

### Verified purchase only

- A review can be submitted only against a `VendorOrder` in state `delivered` (carrier delivery or
  vendor mark, ORD.6), by the buyer of that order. There is no other way to create one: no form with
  a free product picker, and no import of `mpmv_verified` rows.
- **Window**: opens at `delivered_at`, closes 90 days later (`TrustRules.review_window_days`).
- **How the buyer proves it**: through the account (the order is in "my orders") or, for guest
  checkout, through a single-use signed link in the invitation email. The link follows the
  QuoteLink pattern ([06](06-shipping-and-quotes.md)): only the token hash is stored, a GET shows the
  form and changes nothing, a POST acts once and a second POST is idempotent.
- **Invitations** (counsel's answer to Q-T7, 2026-11-13): the platform sends them, never the vendor.
  A review invitation is treated like marketing under the Italian rules (art. 130 of the privacy
  code), so it rests on legitimate interest and the "soft opt-in" of art. 130(4), not on consent,
  and the opt-out comes first:
  - **Opt-out at checkout**: the checkout and the order confirmation carry a line with a link or a
    toggle "do not invite me to review this order", and every invitation email carries a "No,
    thanks" link that works without login. The same for guests and for business buyers; the signed
    single-use link for guests is fine.
  - **Best route, a service message**: the "Delivered" email (a pure service message, GDPR article
    6(1)(b)) carries two buttons, "Report a problem or return" and "Rate products and shipping".
    No promotion ever goes into that email or into any invitation.
  - **One invitation only**, 7 days after delivery (`TrustRules.invite_delay_days`, counsel's range
    7 to 10), with neutral wording. A reminder 14 days later goes only to registered users who
    turned review notifications on in their account; nobody else gets a second email.
  - No invitation is skipped because the order had a return, a complaint or a low expected rating
    ("review gating" is forbidden, §16.8). The email has no tracking pixel (CMP.4). A buyer who
    opted out at checkout gets neither the invitation nor the reminder and can still write a review
    from the account or the delivered email.
- **No incentive**: no discount, points or gift for a review, and the vendor terms forbid vendors
  from offering one. If that ever changes, the incentive must be shown next to every review it
  produced.
- **Supplier reviews** (ING.11) are `origin = supplier_import`: shown only on the product page, in
  their own block labelled "imported from the supplier, not a purchase on MPMV" with the original
  date, never counted in a badge, never in `AggregateRating`.

### Edit, withdraw, delete

- The author can edit text and rating for 30 days after publication. Each edit stores a
  `ReviewRevision`, shows "edited on <date>" and goes through the automatic checks again.
  An edit after a vendor reply keeps the reply and shows the reply's date, so a reader can see
  the order of events.
- The author can **delete the review at any time**, with no reason: the review disappears from the
  page, the counts and the JSON-LD at once and the text is erased. The rating survives only as a
  number in the aggregate history table, which holds no text and no link to a person.
- A **vendor cannot edit, hide or delete a review**. It can reply once, and it can report a
  review through the notice form (CMP.5, notice target `review`).
- Account erasure (CMP.4): the nickname and the link to the user are removed (nickname set to NULL),
  and the review stays under the label "Verified customer" (or "Business purchase", §16.6), never
  "Former customer" (counsel, Q-T8). The deletion dialog has an optional box "Also delete my
  reviews", honoured at once.

### Moderation

Post-moderation: a review goes live after automatic checks; staff act on what the checks hold or a
notice reports.

- **Automatic checks** (`trust.engine.check_review_text`, pure): links and email addresses (held),
  telephone numbers and postal addresses (held, buyers pasting their own data), a small word list per
  language, text identical or near-identical (shingle hash) to another review by a different
  account (held, §16.5), and a text in a language other than the one declared (flag only). A held
  review is not shown and its author is told it is waiting.
- **Staff queue** in the Manager platform space for the Catalog and moderation role (BO-2): held
  reviews, reviews reported through CMP.5, anti-gaming flags (§16.5). Every decision is an
  `AuditLog` entry and, for a removal, a statement of reasons (DSA article 17) emailed to the author
  with the reason code and the appeal route (article 20, internal complaint).
- **Allowed removal reasons** (closed list, data): illegal content; personal data of a third party;
  abuse or hate; advertising or links; not about this order (a different product or a different
  trader); conflict of interest proven (vendor's own account, relative, paid); duplicate. **A
  negative review is never a reason.** The vendor may dispute a review as false (wrong product,
  not a customer); staff check it against the order and may ask the buyer to confirm, but a review
  that describes a real experience stays even when the vendor disagrees.
- **Vendor replies** are moderated with the same list and labelled "Reply from the vendor".
- Repeat authors of removed reviews and abusive repeat reporters are handled under DSA article 23
  measures once the exemption of CMP.5 ends, and by the terms before that.

### Display and ranking rules (published on the "How reviews work" page)

- Default order: newest first. Filters: rating, language. No other ordering, and no "most helpful"
  vote (it invites vote manipulation and is a second ranking we would have to explain).
- The average is the arithmetic mean of all published `mpmv_verified` reviews, with the count next to
  it. Below 3 reviews the page shows the reviews and "not enough reviews for an average".
- Reviews appear in their original language with a language label (Q-T9). A machine translation may
  be shown, labelled "Machine translated from [language] - Show original" (CMP.8), with a link to the
  original text, which stays the reference; the reader can always switch to it. The translation is
  made at display time or cached per review language, is replaced when the author edits, is never
  put in JSON-LD (§16.9), and is dropped with the review. It needs no consent from the author
  (counsel, Q-T8): the terms give the platform a free non-exclusive licence to publish and translate
  the review. The translation provider is a GDPR processor of the review text (sub-processor
  register, CMP.4; spec 11) and its choice follows ING.14 and CMP.8.
- Vendor plan, commission, advertising or payment never changes the display or the average.
- **Listing cards render no rating or reviews badge** (Q-T1): the average and count appear only on the
  product page and the vendor page.
- The "How verified reviews work" page, linked next to every star rating (Q-T8, counsel), says: who
  may review (buyers of delivered orders), how that is checked (the order record and the signed
  link), no gating, no incentives, that the average weighs business and consumer purchases equally,
  the formulas and windows of the badges, how reviews are checked and removed (the list above),
  that vendors cannot remove reviews, how a vendor reports a review with a specific reason
  (DSA article 16: a low rating or a criticism is not a reason), that a removal comes with a
  statement of reasons to the author (article 17) and a chance to correct the text, and that a
  product review is about the quality of the goods while packaging and delivery belong in the
  vendor review.
- **Business purchase label** (Q-T11): when the order's buyer bought as a business (the checkout
  business-buyer flag of [07](07-cart-orders-payments.md): the buyer chose to buy as a business and
  gave a VAT number, whatever the VIES result), the review shows "Business purchase" in place of
  "Verified customer". It is read from the order snapshot when the review is displayed, never from
  the reviewer's text, and the vendor cannot change it.

## 16.4 Badges

Eight badges. Each is computed by `trust.engine.compute_vendor_trust(inputs, rules, as_of)`, a pure
function over a `VendorTrustInputs` dataclass built by `trust.services`; the engine imports no ORM
(ARC.18). The result is stored as an append-only `VendorTrustSnapshot` (vendor, `as_of`,
`rules_version`, metrics JSON, earned badges, the reason a badge is missing), and the public pages
read only the latest snapshot.

| # | Badge | Computed from | Counts only | Threshold and tiers (default `TrustRules`) |
|---|---|---|---|---|
| 1 | **Orders delivered** | `VendorOrder` in `delivered` 14 days or more ago (the withdrawal window is closed), not wholly refunded | net goods above `min_order_value` (default 5.00), after the §16.5 exclusions, at most 3 orders per buyer per rolling 12 months | tiers 10, 50, 100, 500, 1,000, shown as "100+ orders delivered through MPMV" |
| 2 | **Reviews received** | published `mpmv_verified` `VendorReview` | rating mean and count over all time; the latest review shown (stars, excerpt of 200 characters, date, label per §16.6) | stars shown from 5 reviews; the count from 1. **The latest review, never the best**, and the vendor cannot pick |
| 3 | **Countries delivered to** | destination country of the order's shipping snapshot, delivered orders | countries with 2 or more delivered orders from 2 or more distinct buyers (no single buyer is identifiable) | count and the list of country names in the page language, a real list; no flags |
| 4 | **Repeat customers** | distinct buyers with 2 or more delivered orders at least 7 days apart, over 24 months | a count only, shown from 3 buyers; a buyer's nickname only through the §16.6 opt-in | "12 customers ordered more than once" |
| 5 | **Dispatch on time** | `shipped_at` against the dispatch date promised at checkout (VEN.20, EVT.8), clock paused during a declared closure | orders of the last 90 days, minimum 10 orders | earned at 90% on time, lost under 85% (hysteresis) |
| 6 | **Low dispute rate** | `ReturnRequest` of kind `warranty` or "not as described" that staff upheld, upheld payment disputes, upheld CMP.5 notices against the vendor, over 12 months | minimum 20 delivered orders | earned under 3% of orders, lost over 4%. **Never shown as a number, only as the badge**, and a **withdrawal (`kind=withdrawal`) is never a dispute**: penalising a legal right would discourage it |
| 7 | **On MPMV since** | `Vendor` first activation date (VEN.5) | calendar year | no threshold; shows "since 2026" |
| 8 | **Business verified** | CMP.11 data: VAT number valid in VIES at the last check (TAX.5 log), trade register entry matched where the country has one, payout account onboarding complete (VEN.6), DAC7 data complete, GPSR economic operator named where the product needs one (CMP.3) | all the checks that apply to the vendor must pass; re-run monthly | earned or not. The badge links to a plain list of what was checked and says in so many words that it is not a quality guarantee. It never exposes the data itself |

Notes on the table:

- **Response time to quotes and questions** is folded into badge 5 for orders and is not a separate
  badge: quote turnaround (QuoteLink, [06](06-shipping-and-quotes.md)) is an internal vendor metric
  in the Manager dashboard and a staff alert, because a public "answers in 2 hours" badge rewards
  automated replies. It can become a badge later with the same engine.
- **Migrated orders** (MIG.4) count towards badges 1 and 7 (answer to Q-T4, §16.10) as "including N orders on our previous
  shop", because the operator is the same and the data is verified at import. Their buyers are not
  invited to review (no basis, §16.9). Q-T4.
- **No sustainability, quality, "premium" or "top seller" badge** is built. They would be claims we
  cannot verify from data (CMP.10 `GREEN`, UCPD).
- Badges are not marked up in JSON-LD (§16.9) and do not change search or listing ranking. If the
  owner wants ranking to use them, the ranking parameters page of CMP.5 and the vendor terms must
  say so first (P2B article 5). Q-T5: never at launch; a later change needs a new task after the
  CMP.5 ranking page and the vendor-terms clause exist.
- Badges 3 and 4 use buyers' data only in aggregate; a vendor never gets a buyer list from them.

### Refresh

- **Full recompute nightly** for every vendor with `status = active`, as a task on the ADR-0004
  queue. It writes a snapshot only when a metric or a badge changed, and purges the cached pages
  that show the vendor (SEO.6): the snapshot is the only thing that invalidates them.
- **Event-driven for the fast ones**: a review published, removed or withdrawn, an order reaching
  `delivered`, a `ReturnRequest` changing state, a vendor suspension: the vendor is marked dirty and
  recomputed within 15 minutes (debounced), so a removed review does not stay in an average for a day.
- **Hysteresis** on the rate badges (5 and 6) so a vendor does not flip daily; a badge gained or
  lost writes an `AuditLog` entry and a line in the vendor's Manager dashboard with the reason and the
  figure behind it. A suspended or closed vendor shows no trust panel.
- The vendor sees every metric, including those never shown publicly, and what is missing for each
  badge. It **cannot hide an earned badge** (Q-T6): hiding the unflattering ones and keeping the
  flattering ones would make the panel misleading.

## 16.5 Anti-gaming and anti-fraud

The point is to make fake volume and fake reviews cost more than they are worth, without punishing
honest vendors.

1. **Self-dealing exclusion** in the badge inputs: orders whose buyer is a member of the vendor, whose
   shipping address matches the vendor's registered or declared addresses, or whose billing email,
   phone or payment-method fingerprint (where the gateway exposes it) matches a vendor member.
   Excluded orders keep working as orders; they only do not count.
2. **Order floor and per-buyer cap** (badge 1), so a 1.00 item bought 500 times does not make a tier.
3. **Review rules**: delivered orders only, one review per order line and per vendor order, a window,
   no vendor invitation, no incentive.
4. **Anomaly flags** (`TrustFlag`, computed nightly, shown to staff, never an automatic penalty): reviews
   per day far above the vendor's own baseline; many 5-star reviews from accounts under 7 days old;
   reviews published within minutes of delivery; near-duplicate texts across accounts; one reviewer
   with several vendors who share an address or a payout account; a surge in orders from one buyer or
   one address; reviews on orders that are refunded in full soon afterwards.
5. **Outcomes**: staff can exclude orders from the counts (reason code, `AuditLog`), remove reviews
   (statement of reasons), and suspend one badge or all of them for a vendor. Confirmed
   manipulation is a breach of the vendor terms and follows the P2B notice rules (30 days' notice, or
   none for serious breaches).
6. **Attacks on a vendor** (review bombing): a review needs a delivered order, which costs money;
   one review per order; flags for bursts of 1-star reviews; the vendor can report through the
   notice form.
7. **Explainability**: the `TrustRules` version is in every snapshot so staff can explain any
   result later; exact exclusion heuristics are not published, the tiers are.

## 16.6 Buyer identity and consent

### Default

A buyer is shown as **"Verified customer"** (or **"Business purchase"** for a business buyer, §16.3) with the month of the order. Nothing else. This is the
default for every review, for the "latest review" in the badge and for the structured data. The
label is not derived from the real name: a masked real name such as `M***o` still identifies the
person to anyone who knows them, so masking a real name is not used.

### Opt-in reveal (`CommunityProfile`, one row per user, created on first opt-in only)

| Level | Shown | Chosen by |
|---|---|---|
| 0 (default, no row) | "Verified customer" or "Business purchase", month | nothing to do |
| 1 | a nickname chosen by the buyer | a checkbox and a nickname field |
| 2 | nickname plus country and "customer since <year>" | a second, separate checkbox |
| per vendor | the nickname in that vendor's "regular customers" (badge 4) | a third checkbox, asked once after the second delivered order from that vendor |

- Never shown at any level: surname, city, email, avatar or photo, the list of what the person
  bought, the orders at other vendors.
- **Why per vendor**: a purchase history with one vendor can reveal sensitive facts (health,
  family, hobbies). The default is off, asked once, "not now" is remembered for 12 months, both
  buttons have the same weight, nothing is pre-ticked (DSA article 25 and the CMP.9 form rules).
- **Nickname rules**: 3 to 24 characters, unique ignoring case, no reserved words (`mpmv`, `admin`,
  `verified`, vendor names, staff names), no email or URL, checked at the form and by the moderators
  for impersonation. A nickname is not an identifier and is not a link: **there is no public buyer
  profile page.**
- **Legal basis and records** (counsel, Q-T8): the pseudonymous display is personal data and its
  basis is the buyer's separate consent (GDPR articles 6(1)(a), 7), revocable in one click, recorded
  on the profile row with the text version, the time and, on withdrawal, the time; the default
  stays "Verified customer". The consent text is versioned like the CMP.24 legal wording. The
  review itself does not rest on consent: its basis is legitimate interest and the function of the
  service (6(1)(b)), plus the legal duty of the Omnibus rules (6(1)(c)) for the link between review
  and order. The review form carries a notice: write no personal data.
- **Withdrawal** (article 7(3)): one control in the account and one link in each review email
  switch back to level 0 at once. Reviews reference the profile and do not copy the nickname,
  so every page, the vendor panel and the JSON-LD change with the cache purge done in the same
  request. Withdrawing never deletes the review (that is a separate, equally easy control).
- **No profiling**: no buyer "reputation score", no helpfulness rank, no data on reviewers given to
  vendors beyond the review itself and the order it was written on (GDPR article 22 and
  proportionality).

### Authors and buyers: kept separate

CMS.3 authors are a different thing and the two should not be merged.

| | `Author` (CMS.3) | `CommunityProfile` (this spec) |
|---|---|---|
| Who | editors, vendor staff writing articles | a customer |
| Identity | real name, photo, job title, bio, `sameAs` | a nickname or nothing |
| Basis | the person's role and contract | consent |
| Public page | yes, with `ProfilePage` and `Person` | no |
| JSON-LD | `Person` with a stable `@id` | none of its own; a review's `author` is a `Person` named "Verified buyer" or the nickname |
| Lifecycle | staff-managed | erasure and withdrawal by the person |

Reasons: different legal basis and retention; `Person` markup with editorial credentials would be
diluted by customer nicknames; and a merged model would put a "public profile" on customers by
default. The one bridge worth having: an `Author` can point to a `Vendor` (nullable), so a vendor's
articles and "vendor updates" (§16.8) appear on the storefront and the author page says who it
writes for.

## 16.7 Layout

New app `apps/trust`, one layer above `orders`, `vendors` and `cms` (`orders → trust`,
`vendors → trust`, `cms → trust`, `trust → seo`, `trust → compliance`); `tax`, `shipping` and
`orders` never import it. CMS.6's model lives there. The import-linter contracts of ARC.18 are
extended to it in TRU.1. The tasks are in §16.12.

## 16.8 Social and blog section: what I would and would not build

### Would build

- **Reviews and the badges** (above): the trust layer, P2.
- **Vendor updates (P3)**: short articles written by vendor members through a Manager form, as
  `BlogPostPage` with a limited block set (paragraph, image under the CMS.8 rules, product chooser,
  no raw HTML and no embeds), signed by an `Author` linked to the vendor, with post-moderation (staff
  pre-approve a vendor's first 3 posts, then moderate on notice). They use the events moderation
  pattern ([15](15-events.md)) and the CMS.12 sanitiser.
- **A "Community" index page (P3)**: editor-curated, not algorithmic: latest verified reviews across
  the shop (§16.3 rules, level 0 or opted-in labels only), vendor updates, articles and events. No
  engagement ranking.
- **Customer stories**: an editor-written article about a customer, only with that customer's written
  consent recorded as in §16.6. It needs no new model.

### Would not build

- Comment threads under articles, product Q&A, forums, live chat: spam, abuse and illegal content
  at scale, a moderation team we do not have, and a vendor's public answer to a product question is
  a commitment (warranty, compatibility) that needs the care of a contract. The vendor contact form
  already exists.
- Public buyer profiles, follower graphs, "likes", direct messages between users, avatars and photo
  uploads, "buyer of the month" or reviewer leaderboards.
- **Points, badges or rewards for writing reviews** (incentives, Omnibus), and reviewer reputation
  scores.
- **Review gating** (inviting only likely-happy buyers), vendor-written or vendor-selected reviews,
  vendor opt-out of unflattering badges, paid badges, "featured reviews" chosen by a vendor.
- **AI-written or AI-summarised reviews.** An AI summary of reviews is P3 at best, with the CMP.8
  label, the original reviews linked, and counsel's opinion first.
- Social login, social share buttons that load a third-party script, embedded social feeds. Sharing
  is a plain link (SEC, CSP).
- Review photos or videos at launch: a moderation and image-licence burden (EXIF, faces, other
  people's data, CMP.8). A separate decision later (Q-T10).

## 16.9 Law, SEO and accessibility

### EU rules (⚖️ each point to be confirmed by counsel before the rows are built)

- **Omnibus** (Directive (EU) 2019/2161) amending the Unfair Commercial Practices Directive
  2005/29/EC: article 7(6): when a trader gives access to consumer reviews it must say whether and
  how it ensures the reviews come from consumers who used or bought the product (material
  information); Annex I point 23b: claiming that reviews come from real users without reasonable
  and proportionate steps to check is always unfair; point 23c: submitting or commissioning false
  reviews, or misrepresenting reviews to promote products, is always unfair. Our answer: the
  verified-purchase rule, the "How reviews work" page (§16.3), no incentive, no gating, and the
  rule that the vendor cannot touch reviews. The Italian transposition is in the Codice del Consumo
  (D.Lgs. 26/2023); counsel checks the articles and the sanctions (CMP.24).
- **UCPD, general**: a badge is a commercial claim to a consumer. It must be true, specific and
  dated (§16.2); "verified" means exactly what the verified-business list says, and no claim of
  endorsement goes beyond that.
- **DSA** (regulation (EU) 2022/2065): a review is content of a recipient of the service. Notice and
  action (article 16) covers it through CMP.5 with the target type `review`; statement of reasons
  for removal (17); internal complaints (20, built from the start although the micro and small
  exemption of CMP.5 may cover it); misuse measures (23); no dark patterns in consent and opt-in
  flows (25); transparency of the main parameters of any recommender (27: the display rules above
  are our statement); traders (30 to 32) through CMP.11, which feeds badge 8.
- **P2B** (regulation (EU) 2019/1150): removing a badge or a review against a vendor is a
  restriction needing a statement of reasons; ranking parameters if badges ever influence ranking.
- **GDPR**: consent for any reveal (6(1)(a), 7), withdrawal as easy as giving it (7(3)),
  minimisation (5(1)(c)), notice at collection (13), erasure (17), no profiling (22). Review text
  is not an order record: it has its own retention (kept while the vendor is active and 24 months
  after, then anonymised in full) and is not covered by the D-29 fiscal exception.
- **ePrivacy and emails** (counsel, Q-T7, 2026-11-13): a review invitation is treated like marketing
  (art. 130 of the Italian privacy code; the German BGH ruling VI ZR 225/17 points the same way).
  It is sent without consent only on legitimate interest and the art. 130(4) soft opt-in, with
  the opt-out at checkout and in every email, one invitation only, and the review links inside the
  "Delivered" service email (§16.3). TRU.3 and TRU.5 are no longer gated: the open point is
  answered. The customer terms carry two articles for it (CMP.5, customer kind).
- **Legal basis of reviews** (counsel, Q-T8): legitimate interest, 6(1)(b) and 6(1)(c) as in §16.6;
  not consent. Nickname: separate consent.
- **Business buyers**: a review from a company with a VAT number is not a consumer review in the UCPD
  sense. They are accepted, labelled "Business purchase" (§16.3), and the "How verified reviews work"
  page says reviews may come from business buyers and that they weigh the same in the average
  (Q-T11).

### SEO

- `AggregateRating` and up to five `Review` nodes only on `Product` nodes of SEO.7's `@graph`, only
  from published `mpmv_verified` `ProductReview` rows, only what the page visibly shows (count,
  mean and the same texts), none below the visible-average threshold of 3, none for supplier
  reviews, none for a vendor reply.
- **No self-serving markup**: no `AggregateRating` on the operator `Organization`, on `WebSite` or on
  the vendor `Organization` node (search engines ignore or penalise an entity rating itself on its
  own pages); `VendorReview` data stays in the HTML and the trust panel only (Q-T12; a vendor-level rating in markup is a later experiment, not at launch).
- `Review` nodes carry only the original text, never a machine translation (Q-T9), and the
  "Business purchase" label is page text, not markup.
- `Review.author` is a `Person` with the name "Verified customer" (or "Business purchase") or the opted-in nickname, never more.
- Reviews have no URL of their own beyond an `#review-<id>` anchor; review list pages 2 and up are
  `noindex, follow`; links in review text are not allowed (sanitiser, CMS.12), and any that remain
  carry `rel="ugc nofollow"`.
- Badges are text and an icon and get no schema.org markup.
- A page with reviews is cacheable (SEO.6) only if its HTML has no per-request value; the
  consent-dependent and logged-in parts (the "write a review" button, the buyer's own review) load
  after the page, not inside the cached HTML.

### Accessibility (WCAG 2.2 AA, CMP.7)

- A rating is always text ("4 out of 5, 38 reviews"); star graphics are `aria-hidden`; there is no
  hover-only star widget. The input is a `fieldset` with a `legend` and radio buttons whose labels
  are visible.
- Badges are text plus an icon, with contrast from the theme tokens ([14](14-frontend-themes.md)),
  never colour alone; their explanation opens in `<details>` or a `<dialog>`, not a hover tooltip.
- The "latest review" and the regular customers list are static lists, not a rotating carousel
  (WCAG 2.2.2).
- The countries list is a real list of country names in the page language (no flags or emoji as the
  only content).
- The review form: errors tied to fields (3.3.1, 3.3.3), no puzzle (3.3.8, CMP.9), a prefilled
  nickname (3.3.7 redundant entry), a character counter as a polite live region, targets of at least
  24 px (2.5.8). The pages are added to the axe audit (CMP.7).

## 16.10 Owner questions (all answered 2026-11-13; Q-T7 and Q-T8 by counsel)

1. **Q-T1**: is the vendor "reviews" badge (average and count) wanted on listing cards, or only on
   the vendor page and product page? (Cards are visible everywhere, so most exposed to disputes.)
   **Answered (owner, 2026-11-13): vendor page and product page only, not on listing cards.**
2. **Q-T2**: product review and vendor review as two kinds (this proposal), or one review per
   order? One is simpler; two keep complaints about shipping off product pages.
   **Answered (owner, 2026-11-13): two kinds, product review and vendor review.**
3. **Q-T3**: the review window (90 days after delivery) and the edit window (30 days).
   **Answered (owner, 2026-11-13): 90 days after delivery to review, 30 days to edit.**
4. **Q-T4**: do orders from the legacy shop count for the "orders delivered" and "on MPMV since" badges
   ("including N orders on our previous shop"), and are legacy buyers ever invited?
   **Answered (owner, 2026-11-13): legacy orders count, with the "including N orders on our previous shop" wording; legacy buyers are never invited.**
5. **Q-T5**: may badges ever influence search or listing order? Default: never (it needs the P2B
   ranking page and the vendor terms first).
   **Answered (owner, 2026-11-13): never at launch; a later change needs a new task after the CMP.5 ranking page and the vendor-terms clause exist.**
6. **Q-T6**: may a vendor hide an earned badge? Default: no (§16.4).
   **Answered (owner, 2026-11-13): no.**
7. **Q-T7**: counsel: are the review invitation and reminder emails service messages or marketing
   under current Italian and EU practice, and do they need consent? **Answered (counsel, 2026-11-13): see §16.3 (invitations) and §16.9.**
8. **Q-T8**: counsel: the legal basis for the review itself and for the nickname (consent as written
   here, or legitimate interest), and the default on erasure (the review stays as "Former customer",
   or is deleted; counsel: the label is "Verified customer", with an optional "also delete my reviews" box). **Answered (counsel, 2026-11-13): see §16.3, §16.6 and §16.9.**
9. **Q-T9**: show reviews in their original language only (this proposal), or add machine
   translation (needs the CMP.8 label)?
   **Answered (owner, 2026-11-13): add machine translation, labelled "machine translated" (CMP.8) with a link to the original text, which stays the reference.**
10. **Q-T10**: review photos: later, or never?
   **Answered (owner, 2026-11-13): later, not at launch (image moderation and personal data in photos first).**
11. **Q-T11**: accept reviews from business buyers, and say so on the page?
   **Answered (owner, 2026-11-13): yes, labelled "business purchase" on the review.**
12. **Q-T12**: a vendor-level rating in the vendor `Organization` markup: a later experiment, or
    never?
   **Answered (owner, 2026-11-13): a later, measured experiment, not at launch.**
13. **Q-T13**: vendor updates and a "Community" page: P3 as written here, or dropped?
   **Answered (owner, 2026-11-13): kept at P3, after launch.**

## 16.11 Regression cases

1. A review cannot be created against an undelivered, cancelled or other buyer's order.
2. A second submission on the same signed link changes nothing; a GET does not consume the link.
3. A vendor, with any role including a support user acting as the vendor, cannot delete or edit a
   review.
4. A withdrawal (`kind=withdrawal`) never counts as a dispute; an upheld `warranty` return does.
5. An order from a vendor member, a matching address or a matching payment fingerprint does not count
   towards badges 1 to 4.
6. The "latest review" is the most recent published one, whatever its rating.
7. Hysteresis: 89% on-time dispatch after a gain at 90% keeps the badge; 84% loses it.
8. A buyer who withdraws the nickname consent disappears from every page and JSON-LD node in the same
   request, and the cached pages are purged.
9. A cached public product page shows the same review count and mean as its JSON-LD.
10. No `AggregateRating` on `Organization` or `WebSite`; none for supplier reviews.
11. The "How verified reviews work" page exists in each go-live language before any review is published.
12. Listing cards render no rating or reviews badge; the same vendor and product show them on their own pages.
13. A review of an order from a business buyer shows "Business purchase"; a consumer order shows "Verified customer"; the vendor and the reviewer cannot change either.
14. A machine translation of a review shows its "Machine translated" label and a link to the original, and no JSON-LD node carries it.
15. A buyer who opted out at checkout (or by the email link, with no login) gets no invitation or reminder; a buyer who did not gets exactly one invitation, and a reminder only if registered with review notifications on.
16. After account erasure the review keeps "Verified customer" with no nickname; with the "also delete my reviews" box ticked it disappears at once.
17. Orders migrated from the previous shop count in badges 1 and 7 with the "including N orders on our previous shop" wording, and their buyers get no invitation.

## 16.12 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| TRU.1 | `trust` app and rules: `TrustRules` (versioned, staff-editable with audit, dry-run preview), `VendorTrustInputs`, pure `compute_vendor_trust` for badges 1 to 7 with tiers and hysteresis, append-only `VendorTrustSnapshot`; import contracts extended | P2 | 10 | ORD.2, VEN.5, ARC.18 | The §16.11 cases 4, 6 and 7 pass on the engine with no database; a new rules version changes a vendor's result and the old snapshot keeps its `rules_version` |
| TRU.2 | Snapshot service and refresh: inputs built from orders, returns and vendors with the §16.5 exclusions, nightly task for active vendors, dirty marking and 15-minute recompute on order, return, review and suspension events, cache purge, `AuditLog` on gain and loss | P2 | 8 | TRU.1, ORD.6, SEO.6, MIG.4 | A review removal updates the vendor's snapshot within 15 minutes; an excluded order does not move any count; a suspended vendor has no public panel; orders imported by MIG.4 count in badges 1 and 7 with the "including N orders on our previous shop" wording and are never invited to review (Q-T4) |
| TRU.3 | Review submission on CMS.6's models: eligibility and window; single-use signed link for guest buyers; one platform invitation after delivery with the opt-out at checkout and in every email, review links in the "Delivered" service email, a reminder only for registered users with review notifications on (counsel, Q-T7); edit, withdraw, delete; vendor reply; supplier-imported reviews in their own block (ING.11) | P2 | 10 | CMS.6, ARC.16, ORD.6, TRU.1 | §16.11 cases 1 to 3 pass; a vendor invitation or incentive field does not exist; an imported review never counts in a snapshot or in markup; §16.11 case 15; the order confirmation carries the opt-out line; the form carries the "no personal data" notice; the review stores the order's business-buyer flag for the "Business purchase" label |
| TRU.4 | Review moderation: automatic checks, staff queue in the Manager for Catalog and moderation, `review` as a CMP.5 notice target, closed removal reasons, statement of reasons (article 17) to the author with a chance to correct the text, and appeal route to the author and the vendor, vendor reports need a specific reason (article 16), vendor reply moderation | P2 | 8 | TRU.3, CMP.5 | A notice against a review gets a tracked outcome; a removal emails a statement of reasons; a negative review with no listed reason cannot be removed; the Catalog and moderation role sees the queue and Finance does not |
| TRU.5 | Community profile and consent: `CommunityProfile` with levels 0 to 2, per-vendor regular-customers opt-in asked once after the second delivered order, nickname rules, versioned consent text and withdrawal, erasure per CMP.4 with the optional "also delete my reviews" box | P2 | 8 | TRU.3, CMP.4, CMP.24 | §16.11 case 8; nothing is pre-ticked and both buttons weigh the same; §16.11 case 16; an erased account's reviews show "Verified customer", never "Former customer"; a nickname that is a reserved word is refused |
| TRU.6 | Anti-gaming: nightly `TrustFlag` rules (§16.5 point 4), staff queue, exclude-orders and suspend-badge actions with reasons and audit | P2 | 8 | TRU.2, TRU.4 | Seeded burst, duplicate-text and young-account cases raise a flag and change nothing by themselves; a staff exclusion lowers the count and is audited |
| TRU.7 | Public display: vendor trust panel, product chip, reviews list with the §16.3 display rules, "How verified reviews work" page in every go-live language and linked next to the stars, "Business purchase" label (Q-T11), machine-translated reviews with their label and link to the original (Q-T9, CMP.8), no rating or reviews badge on listing cards (Q-T1) (legal wording approved under CMP.24), badge 8 explanation list; WCAG 2.2 AA per §16.9; pages added to the CMP.7 axe audit | P2 | 10 | TRU.2, TRU.3, THM.3, I18N.9, CMP.8 | §16.11 case 11; zero axe violations on the new pages; the rating is available as text; the panel works without script; §16.11 cases 12 to 14: a listing card renders no rating or badge, a business-buyer order shows "Business purchase", a machine translation shows its label and original link and is absent from JSON-LD |
| TRU.8 | Review structured data in `seo.page_graph`: `AggregateRating` and up to five `Review` nodes on `Product` only, per §16.9 | P2 | 4 | SEO.7, TRU.3 | §16.11 cases 9 and 10; the Rich Results Test shows no errors on a product with reviews |
| TRU.9 | Business-verified badge (badge 8) from CMP.11, VEN.6 and CMP.3 data with a monthly re-check and a public list of what was checked | P2 | 4 | TRU.1, CMP.11 | A vendor failing a VIES re-check loses the badge and the list shows only what was checked |
| TRU.10 | Vendor updates: vendor-authored `BlogPostPage` through a Manager form, limited blocks, `Author` linked to a vendor, first-3-posts pre-approval then post-moderation | P3 | 10 | CMS.3, CMS.12, TRU.4, CORE.9 | A vendor member writes a post that goes to moderation; raw HTML and embeds are refused; the vendor's storefront lists the approved posts |
| TRU.11 | Community index page: editor-curated, shows latest verified reviews (levels per §16.6), vendor updates, articles and events, with no engagement ranking | P3 | 6 | TRU.7, TRU.10, EVT.1 | The page lists items by date only; a withdrawn nickname never appears |
