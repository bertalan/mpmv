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
