"""Text fields of API v1 input schemas (SEC.5, hostile input).

`PlainText` is a `str` that PostgreSQL can store: a NUL character (`\\u0000` in JSON) or a lone
surrogate (`\\ud800`) is a 422 problem on the field, instead of a database error, and so a 500,
when the service looks it up or saves it. Django forms refuse NUL the same way
(`ProhibitNullCharactersValidator`).
"""

from typing import Annotated

from pydantic import AfterValidator


def storable_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Null characters are not allowed.")
    if any("\ud800" <= char <= "\udfff" for char in value):
        raise ValueError("Unpaired surrogate characters are not allowed.")
    return value


PlainText = Annotated[str, AfterValidator(storable_text)]
