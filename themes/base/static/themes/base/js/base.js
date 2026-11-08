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
