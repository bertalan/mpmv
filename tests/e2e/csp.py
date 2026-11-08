"""Record Content-Security-Policy violations in the browser (shared by e2e tests)."""

from playwright.sync_api import Page

RECORD_CSP_VIOLATIONS = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__cspViolations.push(
    `${location.pathname}: ${event.effectiveDirective} blocked ${event.blockedURI} `
    + `(${event.sourceFile}:${event.lineNumber} ${event.sample})`
  );
});
"""


def csp_violations(page: Page) -> list[str]:
    """Every violation recorded on the current page since it loaded."""
    violations: list[str] = page.evaluate("window.__cspViolations")
    return violations
