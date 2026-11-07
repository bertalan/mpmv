"""The Wagtail admin under the strict Content-Security-Policy (CMS.1, SEC.1). Pure functions.

Wagtail 8.1's date, time and date-time widgets start their picker with a one-line inline
script, `<script>initDateChooser("<id>", <JSON config>);</script>`, which the CSP blocks (no
'unsafe-inline'). `nonce_picker_scripts` gives those scripts, and only those, the request's
nonce: the whole script must be one call of a picker initialiser with an HTML id and a JSON
object, and JSON cannot run code, so injected markup cannot borrow the nonce to run anything
else. Every other inline script stays blocked.

The image and document chooser widgets (I18N.3) start the same way,
`<script>new ImageChooser("<id>", <JSON config>);</script>`, and get the nonce on the same terms.
"""

import json
import re

PICKER_SCRIPT = re.compile(
    r"<script>(?P<call>(?:initDateChooser|initTimeChooser|initDateTimeChooser"
    r"|new ImageChooser|new DocumentChooser)"
    r'\("[A-Za-z0-9_-]+", (?P<config>\{[^<]*?\})\);)</script>'
)


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def nonce_picker_scripts(html: str, nonce: str) -> tuple[str, int]:
    """`html` with Wagtail's picker scripts carrying `nonce`, and how many were changed."""
    changed = 0

    def add_nonce(match: re.Match[str]) -> str:
        nonlocal changed
        if not _is_json_object(match["config"]):
            return match[0]
        changed += 1
        return f'<script nonce="{nonce}">{match["call"]}</script>'

    return PICKER_SCRIPT.sub(add_nonce, html), changed
