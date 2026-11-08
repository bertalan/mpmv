// Alpine components of the base theme (THM.3, spec 14 §14.5).
//
// Loaded with type="module" before the vendored Alpine CSP build, so the components are
// registered on `alpine:init`, before Alpine starts. The CSP build evaluates only property and
// method names, so every value a template binds is a method here.

document.addEventListener("alpine:init", () => {
  // Disclosure navigation: a button that shows and hides the main menu on narrow screens.
  // Without JavaScript the button stays hidden and the links are always shown.
  window.Alpine.data("disclosure", () => ({
    open: false,

    // :class: "is-enhanced" lets the CSS collapse the list, "is-open" shows it again.
    classes() {
      return { "is-enhanced": true, "is-open": this.open };
    },
    // :aria-expanded (a string, never removed).
    expanded() {
      return this.open ? "true" : "false";
    },
    // x-bind:hidden on the button: shown once the component runs.
    toggleHidden() {
      return false;
    },
    toggle() {
      this.open = !this.open;
    },
    // Escape closes the menu and returns focus to its button.
    close() {
      if (!this.open) {
        return;
      }
      this.open = false;
      this.$refs.toggle.focus();
    },
  }));
});

// Focus not obscured (WCAG 2.4.11): from 48rem up the header is sticky, and a long menu can wrap
// it to a second row, taller than the --header-height token. The scroll padding reads
// --header-offset, the header's real height, kept up to date here. Setting a custom property
// through the CSSOM is allowed by the CSP. The token itself is not touched: it is the header's
// minimum height, so writing the measure back would make the header grow on every resize.
const siteHeader = document.querySelector(".site-header");
if (siteHeader && "ResizeObserver" in window) {
  new ResizeObserver(() => {
    const height = Math.ceil(siteHeader.getBoundingClientRect().height);
    document.documentElement.style.setProperty("--header-offset", `${height}px`);
  }).observe(siteHeader);
}

// Dismissible alerts (THM.4, components/alert.html): the "Dismiss" button is rendered `hidden`,
// so it shows only once this script runs; a click removes its alert and moves the focus to the
// main content, never leaving it on a removed element. Delegated: alerts swapped in by htmx work
// too. No inline handler, so the strict CSP holds.
for (const button of document.querySelectorAll("[data-dismiss]")) {
  button.hidden = false;
}
document.addEventListener("htmx:afterSwap", (event) => {
  for (const button of event.target.querySelectorAll("[data-dismiss]")) {
    button.hidden = false;
  }
});
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-dismiss]");
  const alert = button && button.closest(".alert");
  if (!alert) {
    return;
  }
  alert.remove();
  document.getElementById("main")?.focus();
});

// Busy and disabled-but-focusable controls (THM.4, components/button.html): a loading button
// carries aria-disabled="true" and keeps the focus, but it must not act. Capture phase, so no
// other listener (htmx included) sees the click or the submit.
document.addEventListener(
  "click",
  (event) => {
    if (event.target.closest('[aria-disabled="true"]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  },
  true,
);
document.addEventListener(
  "submit",
  (event) => {
    if (event.submitter?.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  },
  true,
);

// Error summary (THM.4, components/error_summary.html): after a failed submit, the summary
// marked data-autofocus takes the focus, so the visitor starts on the list of errors (WCAG
// 3.3.1). A module runs once the document is parsed, so the box is already there.
document.querySelector(".error-summary[data-autofocus]")?.focus();
