"""The contract clauses of CMP.5 (adviser opinion 2026-11-11), as versioned texts. Pure: no ORM.

Customer terms carry clauses (a) to (c), vendor terms (d) to (h). Each clause states only what the
spec and the adviser opinion say (D-05, D-08, D-15, L-10, Q-G2, Q-G8; VEN.33), in the wording of
the owner's legal review of 2026-11-13. Counsel still reviews and approves them (CMP.24) before
go-live. Only English and Italian exist so far; the other site languages fall back to English
until they are translated. The vendor may not pass the refund fee of clause (f) on to consumers
(Codice del consumo, artt. 56-57), so the clause has no pass-on sentence.

The Italian texts follow the existing legal texts of the repository: formal and impersonal (no
informal "tu"), with "il Cliente" and "il Venditore" as the parties.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

CUSTOMER: Final = "customer"
VENDOR: Final = "vendor"

#: Starts the clause block inside a vendor contract body; a new version replaces everything from
#: here on and keeps the text before it.
BLOCK_MARKER: Final = "--- Platform clauses (CMP.5) ---"

#: Languages with a drafted wording.
LANGUAGES: Final = ("en", "it")


@dataclass(frozen=True)
class Clause:
    letter: str
    kind: str
    key: str
    headings: dict[str, str]
    texts: dict[str, str]


CLAUSES: Final[tuple[Clause, ...]] = (
    Clause(
        "a",
        CUSTOMER,
        "payment_discharge",
        {"en": "Payment through the platform", "it": "Pagamento tramite la piattaforma"},
        {
            "en": (
                "Payment of the price through the platform, made by Stripe, discharges the "
                "customer's debt towards the vendor (Italian civil code, art. 1188)."
            ),
            "it": (
                "Il pagamento del prezzo tramite la piattaforma, effettuato con Stripe, estingue "
                "il debito del Cliente verso il Venditore (art. 1188 del codice civile)."
            ),
        },
    ),
    Clause(
        "b",
        CUSTOMER,
        "garage_compatibility",
        {
            "en": "Compatibility declared by the seller",
            "it": "Compatibilità dichiarata dal venditore",
        },
        {
            "en": (
                "The compatibility data of the Garage is declared by the vendor. The platform "
                "does not guarantee it. For a consumer customer, an incompatibility with what was "
                "declared is a conformity defect (Codice del consumo, art. 129 and following) "
                "borne by the vendor."
            ),
            "it": (
                "I dati di compatibilità del Garage sono dichiarati dal Venditore. La piattaforma "
                "non li garantisce. Per il Cliente consumatore, l'incompatibilità rispetto a "
                "quanto dichiarato è un difetto di conformità (Codice del consumo, art. 129 e "
                "seguenti) a carico del Venditore."
            ),
        },
    ),
    Clause(
        "c",
        CUSTOMER,
        "garage_filter",
        {"en": "The Garage filter", "it": "Il filtro Garage"},
        {
            "en": (
                "The Garage filter is a filter that the customer chooses and can switch off. It "
                "does not change the order of the results. While a vehicle is active, an item "
                "without declared fitment is hidden; declared fitment and universal compatibility "
                "are parameters of what an active Garage shows."
            ),
            "it": (
                "Il filtro Garage è un filtro che il Cliente sceglie e può disattivare. Non "
                "modifica l'ordine dei risultati. Finché un veicolo è attivo, un articolo senza "
                "compatibilità dichiarata è nascosto; la compatibilità dichiarata e la "
                "compatibilità universale sono parametri di ciò che mostra un Garage attivo."
            ),
        },
    ),
    Clause(
        "d",
        VENDOR,
        "stripe_role",
        {"en": "Payment service", "it": "Servizio di pagamento"},
        {
            "en": (
                "The platform does not provide payment services. Stripe provides the payment "
                "service under its Connected Account Agreement, which the vendor accepts by "
                "authorising collection through the platform."
            ),
            "it": (
                "La piattaforma non presta servizi di pagamento. Stripe presta il servizio di "
                "pagamento secondo il proprio Connected Account Agreement, che il Venditore "
                "accetta autorizzando l'incasso tramite la piattaforma."
            ),
        },
    ),
    Clause(
        "e",
        VENDOR,
        "set_off",
        {"en": "Set-off and SEPA debit", "it": "Compensazione e addebito SEPA"},
        {
            "en": (
                "The vendor agrees to contractual set-off (Italian civil code, art. 1252) of "
                "commissions, collection costs, amounts refunded to customers and refund fees "
                "against transfers. For a negative balance, the platform may debit the vendor by "
                "SEPA under the mandate the vendor has given."
            ),
            "it": (
                "Il Venditore accetta la compensazione convenzionale (art. 1252 del codice "
                "civile) di commissioni, costi di incasso, importi rimborsati ai Clienti e costi "
                "di rimborso con i trasferimenti. In caso di saldo negativo, la piattaforma può "
                "addebitare il Venditore tramite SEPA in base al mandato da esso conferito."
            ),
        },
    ),
    Clause(
        "f",
        VENDOR,
        "refund_fee",
        {"en": "Refund fee", "it": "Costo di rimborso"},
        {
            "en": (
                "A refund or chargeback gives back the commission and the collection cost on the "
                "refunded share. A refund fee applies to the vendor on every refund event, "
                "withdrawal included, whatever the cause, except a refund caused by a platform "
                "malfunction or error. The refund fee is Stripe's actual cost of the refund plus "
                "the fixed part of EUR 2.00, rounded up to the whole euro. A change of the fixed "
                "part is notified on a durable medium at least 15 days before it takes effect and "
                "applies only to orders created from its effective date."
            ),
            "it": (
                "Un rimborso o uno storno (chargeback) restituisce la commissione e il costo di "
                "incasso sulla quota rimborsata. Si applica al Venditore un costo di rimborso per "
                "ogni evento di rimborso, recesso compreso, qualunque ne sia la causa, salvo il "
                "rimborso causato da un malfunzionamento o da un errore della piattaforma. Il "
                "costo di rimborso è il costo vivo di Stripe per il rimborso più la parte fissa "
                "di EUR 2,00, arrotondato per eccesso all'euro intero. La modifica della parte "
                "fissa è comunicata su supporto durevole almeno 15 giorni prima della sua "
                "efficacia e si applica solo agli ordini creati dalla data di efficacia."
            ),
        },
    ),
    Clause(
        "g",
        VENDOR,
        "accessibility",
        {"en": "Accessible content", "it": "Contenuti accessibili"},
        {
            "en": (
                "The vendor provides descriptions and attachments that are accessible within the "
                "meaning of the accessibility legislation (Legislative Decree 82/2022)."
            ),
            "it": (
                "Il Venditore fornisce descrizioni e allegati accessibili ai sensi della "
                "normativa sull'accessibilità (D.Lgs. 82/2022)."
            ),
        },
    ),
    Clause(
        "h",
        VENDOR,
        "declared_compatibility",
        {
            "en": "Compatibility declared by the seller",
            "it": "Compatibilità dichiarata dal venditore",
        },
        {
            "en": (
                "The vendor answers for the compatibility it declares and indemnifies the "
                "platform against third-party claims."
            ),
            "it": (
                "Il Venditore risponde della compatibilità che dichiara e manleva la piattaforma "
                "da reclami di terzi."
            ),
        },
    ),
    Clause(
        "i",
        VENDOR,
        "dac7_outside_refunds",
        {
            "en": "DAC7 reporting and refunds outside the platform",
            "it": "Comunicazione DAC7 e rimborsi esterni",
        },
        {
            "en": (
                "For the annual DAC7 report (Legislative Decree 32/2023) the platform reports only "
                "the consideration and refunds that pass through its own payment system. A refund "
                "the vendor makes outside the platform does not reduce the amount the platform "
                "reports to the tax authority; the vendor documents it in its own accounts."
            ),
            "it": (
                "Ai fini della comunicazione annuale DAC7 (D.Lgs. 32/2023), la piattaforma "
                "rendiconta i soli corrispettivi e rimborsi transitati tramite il proprio sistema "
                "di pagamento. Eventuali rimborsi effettuati dal Venditore fuori dalla piattaforma "
                "non riducono l'importo comunicato dalla piattaforma all'autorità fiscale; resta a "
                "carico del Venditore documentarli nella propria contabilità."
            ),
        },
    ),
)


def clauses_for(kind: str) -> tuple[Clause, ...]:
    return tuple(clause for clause in CLAUSES if clause.kind == kind)


def clause_block(kind: str, language: str) -> str:
    """The clauses of `kind` in `language` (English when it has no wording), numbered (a), (b)..."""
    code = language if language in LANGUAGES else "en"
    lines = [BLOCK_MARKER]
    for clause in clauses_for(kind):
        lines.append(f"({clause.letter}) {clause.headings[code]}\n{clause.texts[code]}")
    return "\n\n".join(lines)


def with_clauses(kind: str, language: str, previous_body: str = "") -> str:
    """`previous_body` without an older clause block, then the current block."""
    head = previous_body.split(BLOCK_MARKER, 1)[0].rstrip()
    block = clause_block(kind, language)
    return f"{head}\n\n{block}" if head else block


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def missing_clauses(kind: str, text: str, language: str, *, only: Iterable[str] = ()) -> list[str]:
    """The letters of the clauses of `kind` whose wording in `language` is not in `text`.

    Whitespace and line breaks do not matter; the words do (a clause with changed words is
    missing). A language without a drafted wording is checked against the English one.
    """
    code = language if language in LANGUAGES else "en"
    wanted = set(only)
    haystack = _squash(text)
    return [
        clause.letter
        for clause in clauses_for(kind)
        if (not wanted or clause.letter in wanted) and _squash(clause.texts[code]) not in haystack
    ]
