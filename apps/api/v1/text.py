"""Text fields of API v1 input schemas (SEC.5, hostile input).

`PlainText` is a `str` that PostgreSQL can store: a NUL character (`\\u0000` in JSON) or a lone
surrogate (`\\ud800`) is a 422 problem on the field, instead of a database error, and so a 500,
when the service looks it up or saves it. Django forms refuse NUL the same way
(`ProhibitNullCharactersValidator`).
"""

from typing import Annotated

from pydantic import AfterValidator

from apps.core.text import nfc


def storable_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Null characters are not allowed.")
    if any("\ud800" <= char <= "\udfff" for char in value):
        raise ValueError("Unpaired surrogate characters are not allowed.")
    return value


PlainText = Annotated[str, AfterValidator(storable_text)]


def storable_nfc_text(value: str) -> str:
    return str(nfc(storable_text(value)))


# Free text a client writes (a title, a description): storable and Unicode NFC (I18N.10). Not for
# codes and SKUs, which stay as sent.
NFCText = Annotated[str, AfterValidator(storable_nfc_text)]
