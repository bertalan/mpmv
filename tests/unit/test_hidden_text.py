"""`strip_hidden`: tag block, Unicode-version robustness and private use (SEC.5)."""

import unicodedata

from apps.core.hidden_text import _KEPT, strip_hidden


def test_every_tag_block_code_point_is_stripped_assigned_or_not() -> None:
    for cp in range(0xE0000, 0xE0080):
        assert strip_hidden(f"a{chr(cp)}b") == "ab", hex(cp)


def test_every_cf_and_cc_character_is_stripped_per_running_unicodedata() -> None:
    # Asserted against the running unicodedata, so a Unicode upgrade cannot break it.
    for cp in (*range(0x20000), *range(0xE0000, 0xE1000)):
        ch = chr(cp)
        if unicodedata.category(ch) in ("Cf", "Cc") and ch not in _KEPT:
            assert strip_hidden(f"a{ch}b") == "ab", hex(cp)


def test_private_use_characters_survive() -> None:
    assert unicodedata.category("") == "Co"
    assert strip_hidden("ab") == "ab"
