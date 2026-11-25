# AI literacy briefing and AI labels (CMP.8)

Short briefing for staff who review AI drafts (AI Act article 4, AI literacy) and the rules the
code enforces for article 50 (transparency). Owner wording review is still open: the label texts
are not legal wording yet.

## What the tool can and cannot do

- A machine draft can be fluent and still wrong: invented facts, wrong sizes, wrong materials,
  a translation that changes the meaning. It does not know the product.
- It cannot judge legal or tax wording, safety claims or "green" claims. Generic environmental
  claims ("eco", "green", "climate friendly") are banned (directive (EU) 2024/825); never publish
  one, whoever wrote it.
- It can carry over a mistake from the source text into every language.
- Read the whole text against the product data. Only the person who read it confirms it.

## Rules in the system

| Content | Rule | Where |
| --- | --- | --- |
| Product text written by a machine | `origin = machine`. It stays a draft until a person confirms it: in the admin tick "I have read and checked this text" and set the status to reviewed or published. The system records who and when. A database constraint also refuses a machine text that is reviewed or published without that record. | `ProductTranslation.origin`, `services.review_translation` |
| Text that changed at the source | Goes back to draft, and needs a new confirmation. | `TranslationModel.mark_stale_translations` |
| Product image made or changed by AI | Set "AI origin" to generated or edited. The product page shows "AI-generated image" or "AI-edited image" next to the image and in the zoom view, and the product data (JSON-LD `ImageObject`) carries the IPTC digital source type. | `ProductImage.ai_origin`, `media.set_ai_origin` |

Text reviewed by a person under their editorial responsibility needs no label on the page.
Images do: a realistic AI image can look like a real photo (deep fake rule, article 50(4)).

## Not done yet

- The IPTC/XMP tag is not written into the image file: renditions are re-encoded and stripped of
  metadata (SEC.2), so the flag travels in the page markup and the JSON-LD instead.
- The enrichment tool (ING.5) sets `origin = machine` when it is built.
