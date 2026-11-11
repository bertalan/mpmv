"""Security review probes for the redaction filter (OPS.2, PR 219)."""

import copy
import hashlib
import json
import logging

import pytest

from apps.core.observability import JsonFormatter, RedactSensitiveData, redact_text

KEY_SHAPE = "abcdefghijklmnopqrstuvwxyz0123456789"


def _fake(label: str, length: int) -> str:
    """A stable, credential-free-looking test value built at run time (no literal in the source)."""
    return hashlib.sha256(label.encode()).hexdigest()[:length]


_PW = _fake("pw", 12)  # derived at run time, stable across xdist workers
_TOK = _fake("tok", 8)


def _emit(record: logging.LogRecord) -> str:
    RedactSensitiveData().filter(record)
    return JsonFormatter().format(record)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("IBAN IT60X0542811101000000123456 paid", "0542811101000000123456"),
        ("iban it60x0542811101000000123456", "0542811101000000123456"),
        ("pay DE89 3704 0044 0532 0130 00 now", "0532 0130 00"),
        ("DE89370400440532013000", "370400440532013000"),
        ("GB29 NWBK 6016 1331 9268 19", "9268 19"),
        ("FR1420041010050500013M02606", "0500013M02606"),
        ("NO9386011117947", "86011117947"),
        ("MT84MALT011000012345MTLCAST001S", "MTLCAST001S"),
        ("VAT number IT12345678901 on file", "12345678901"),
        ("P.IVA 12345678901", "12345678901"),
        ("USt-IdNr. DE123456789", "123456789"),
        ("customer FR12345678901 and NL123456789B01", "12345678901"),
        ("ATU12345678", "12345678"),
    ],
)
def test_iban_and_vat_numbers_are_redacted(text: str, secret: str) -> None:
    assert secret not in redact_text(text)


@pytest.mark.parametrize("scheme", ["Basic", "Bearer", "Token", "Digest", "Negotiate", "ApiKey"])
def test_authorization_header_of_any_scheme_is_redacted(scheme: str) -> None:
    out = redact_text(f"Authorization: {scheme} dXNlcjpwYXNzd29yZA== more")
    assert "dXNlcjpwYXNzd29yZA" not in out
    out = redact_text(f"headers={{'Authorization': '{scheme} dXNlcjpwYXNz'}} x")
    assert "dXNlcjpwYXNz" not in out
    assert "x" in out.split("}")[-1]


@pytest.mark.parametrize(
    "text",
    [
        f"{{'password': '{_PW}'}}",
        f'{{"password": "{_PW}"}}',
        f'password = "{_PW}"',
        f"password='{_PW}'",
        repr({"user": "ada", "password": _PW}),
        json.dumps({"nested": {"password": _PW}}),
        f'password": "{_PW} with spaces"',
    ],
)
def test_quoted_keys_and_dict_reprs_are_redacted(text: str) -> None:
    assert _PW not in redact_text(text)


@pytest.mark.parametrize(
    "key",
    [
        "secret_key", "client_secret", "api_secret", "my_passwd", "pwd", "pw", "access_token",
        "refresh-token", "apikey", "api_key", "API_KEY", "private_key", "session_id",
        "sessionid", "cookie", "csrftoken", "csrf_token", "signature", "x_signature",
    ],
)  # fmt: skip
def test_broad_key_names_are_redacted(key: str) -> None:
    assert "v4lue-xyz" not in redact_text(f"{key}=v4lue-xyz")
    assert "v4lue-xyz" not in redact_text(f"{key}: v4lue-xyz")
    assert "v4lue-xyz" not in redact_text(f"{{'{key}': 'v4lue-xyz'}}")


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("dsn postgres://appuser:p4ssw0rd@db.internal/mpmv", "p4ssw0rd"),
        ("https://user:pa55@example.org/x", "pa55"),
        ("https://tokenonly123@example.org/x", "tokenonly123"),
        ("key sk_live_" + KEY_SHAPE, KEY_SHAPE),
        ("key sk_test_" + KEY_SHAPE, KEY_SHAPE),
        ("key rk_live_" + KEY_SHAPE, KEY_SHAPE),
        ("key pk_live_" + KEY_SHAPE, KEY_SHAPE),
        ("hook whsec_" + KEY_SHAPE, KEY_SHAPE),
        ("value=" + "Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm", "Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm"),
    ],
)
def test_url_credentials_provider_keys_and_long_tokens_are_redacted(text: str, secret: str) -> None:
    assert secret not in redact_text(text)


def test_ordinary_text_is_not_mangled() -> None:
    text = "shipping to Milano via mapping step, delivered 3 items in 12 ms: ok"
    assert redact_text(text) == text


def test_nested_extra_values_are_redacted_by_key_and_by_value() -> None:
    nested = {
        "data": {"password": _PW, "n": [{"token": _TOK}, "ada@example.org"]},
        "tuple": ("Bearer abc.def.ghi", 4111111111111111),
        "ok": "fine",
    }
    out = _emit(_record("hello", payload=nested))
    for leaked in (_PW, _TOK, "ada@example.org", "abc.def.ghi", "4111111111111111"):
        assert leaked not in out
    assert "fine" in out


def test_extra_objects_are_redacted_through_their_text() -> None:
    class Thing:
        def __str__(self) -> str:
            return f"password={_PW} owner=ada@example.org"

    out = _emit(_record("hello", thing=Thing()))
    assert _PW not in out
    assert "ada@example.org" not in out


def test_the_callers_object_is_never_mutated() -> None:
    payload = {"password": _PW, "items": ["ada@example.org"]}
    before = copy.deepcopy(payload)
    _emit(_record("hello", payload=payload))
    assert payload == before


def test_depth_and_size_are_limited() -> None:
    deep: object = f"password={_PW}"
    for _ in range(50):
        deep = {"a": deep}
    huge = list(range(100_000))
    out = _emit(_record("hello", deep=deep, huge=huge))
    assert _PW not in out
    assert len(out) < 20_000


def test_unrepresentable_objects_do_not_break_logging() -> None:
    class Bad:
        def __str__(self) -> str:
            raise RuntimeError("no")

    assert "hello" in _emit(_record("hello", bad=Bad()))


def test_stack_info_is_redacted() -> None:
    record = _record("hello")
    record.stack_info = f"frame pw={_PW} jane@example.com"
    out = _emit(record)
    assert _PW not in out
    assert "jane@example.com" not in out
    assert record.stack_info is not None
    assert _PW not in record.stack_info


def test_percent_encoded_email_is_redacted() -> None:
    assert "user%40example.com" not in redact_text("mail=user%40example.com&x=1")


def test_a_canonical_uuid_stays_intact_but_a_uuid_secret_does_not() -> None:
    ident = "73d91555-6306-464b-b514-bc350456c401"
    assert redact_text(f"case {ident} overdue") == f"case {ident} overdue"
    assert redact_text(f"{ident}, {ident}") == f"{ident}, {ident}"
    assert ident not in redact_text(f"token={ident}")
    assert ident not in redact_text(f"password: {ident}")
    assert "ada@example.org" not in redact_text(f"{ident} ada@example.org")
