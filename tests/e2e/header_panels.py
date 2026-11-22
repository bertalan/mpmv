"""E2E helper (THM.20 follow-up): a light panel opened from the dark `ride` header stays readable.

The header's light link colour must not reach the links of a panel that paints its own light
background (owner report, 22 Nov: white links on a white panel). `assert_panel_readable` opens the
panel, checks that every link and button differs from the panel background, and runs axe inside
the open panel, also with the pointer on its first link. Each light panel has its own test file.
"""

from playwright.sync_api import Page, expect

from tests.e2e.test_ride_overlays import clean

# Each link and visible button of an open panel: its text colour and the panel's background.
COLOURS = """(panel) => {
  const list = document.querySelector(panel + ' > ul');
  const bg = getComputedStyle(list).backgroundColor;
  return [...list.querySelectorAll('a, button:not([hidden])')].map(
    (el) => [el.textContent.trim(), getComputedStyle(el).color, bg]);
}"""


def assert_panel_readable(page: Page, panel: str) -> None:
    """Open the `<details>` panel `panel` of the header and check its links."""
    page.locator(f"header {panel} > summary").click()
    links = page.locator(f"header {panel} > ul a")
    expect(links.first).to_be_visible()
    for label, colour, background in page.evaluate(COLOURS, f"header {panel}"):
        assert colour != background, f"{label!r} has the panel's own colour {colour}"
    clean(page, include=f"header {panel} > ul")
    links.first.hover()
    clean(page, include=f"header {panel} > ul")
