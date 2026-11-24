"""Flags for the language menu of the `ride` theme (THM.35, spec 14 §14.6). Pure data.

One default country per language code (`en` is the United Kingdom: the content is British
English). The flags are plain civil flags drawn as our own SVGs (themes/ride/static/themes/ride/
flags); a language whose country flag carries a coat of arms (Portuguese, Slovak, Slovene,
Croatian) has none and shows its endonym alone.
"""

# Language code -> the flag file stem (the ISO 3166 country code, lower case).
LANGUAGE_FLAGS: dict[str, str] = {
    "en": "gb",
    "de": "de",
    "it": "it",
    "fr": "fr",
    "es": "es",
    "nl": "nl",
    "pl": "pl",
    "ro": "ro",
    "sv": "se",
    "da": "dk",
    "fi": "fi",
    "el": "gr",
    "cs": "cz",
    "hu": "hu",
    "bg": "bg",
}


def flag_path(code: str) -> str:
    """The static path of the flag of language `code`, or "" when it has none."""
    stem = LANGUAGE_FLAGS.get(code.lower().split("-")[0])
    return f"themes/ride/flags/{stem}.svg" if stem else ""
