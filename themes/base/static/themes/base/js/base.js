// Alpine components of the base theme (THM.3, spec 14 §14.5).
//
// A module loaded before the vendored Alpine CSP build: components register on `alpine:init`.
// The CSP build binds only property and method names.

{
  const { cookie, version, script } = document.querySelector("[data-consent-banner]")?.dataset ?? {};
  if (script && (!document.cookie.includes(`${cookie}=v1.${version}.`) || document.querySelector("[data-consent-preferences],[data-consent]"))) {
    import(script);
  }
}

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

  // Tabs (THM.4, components/tabs.html): the server renders every panel under a heading and the
  // tab list hidden, so the content works without JavaScript. Here the list shows, the panels
  // become tab panels and only the selected one stays visible. The CSP build allows no
  // per-element expressions, so the state lives in the DOM (aria-selected, tabindex, hidden),
  // set imperatively; the directives only name the handlers. `$root` (the x-data element), not
  // `$el`: in a handler `$el` is the element carrying the directive, the tab list.
  window.Alpine.data("tabs", () => ({
    init() {
      const list = this.$root.querySelector(".tabs__list");
      list.hidden = false;
      this.$root.classList.add("is-enhanced");
      for (const panel of this.panels()) {
        panel.setAttribute("role", "tabpanel");
        panel.tabIndex = 0;
      }
      const tabs = this.tabs();
      const current = tabs.find((tab) => tab.getAttribute("aria-selected") === "true");
      this.select(current ?? tabs[0], false);
    },
    tabs() {
      return [...this.$root.querySelectorAll(':scope > .tabs__list > [role="tab"]')];
    },
    panels() {
      return [...this.$root.querySelectorAll(":scope > .tabs__panel")];
    },
    select(chosen, focus) {
      for (const tab of this.tabs()) {
        const selected = tab === chosen;
        tab.setAttribute("aria-selected", selected ? "true" : "false");
        tab.tabIndex = selected ? 0 : -1;
        document.getElementById(tab.getAttribute("aria-controls")).hidden = !selected;
      }
      if (focus) {
        chosen.focus();
      }
    },
    // @click on the list: the tab that was clicked, if any.
    choose(event) {
      const tab = event.target.closest('[role="tab"]');
      if (tab) {
        this.select(tab, true);
      }
    },
    // @keydown on the list: arrows wrap around, Home and End jump; selection follows focus.
    move(event) {
      const tabs = this.tabs();
      const index = tabs.indexOf(event.target.closest('[role="tab"]'));
      if (index < 0) {
        return;
      }
      const last = tabs.length - 1;
      const next = {
        ArrowRight: index === last ? 0 : index + 1,
        ArrowLeft: index === 0 ? last : index - 1,
        Home: 0,
        End: last,
      }[event.key];
      if (next === undefined) {
        return;
      }
      event.preventDefault();
      this.select(tabs[next], true);
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

// Dismissible alerts and toasts (THM.4, components/alert.html, components/toast.html): the
// "Dismiss" button is rendered `hidden`, so it shows only once this script runs; a click removes
// its alert and moves the focus to the main content, never leaving it on a removed element. A
// toast keeps the focus near the toasts instead (see dismissToast). Delegated: alerts swapped in by htmx work
// too. htmx 2 fires htmx:load on every element it adds, in-band or out of band (an out-of-band
// swap fires no htmx:afterSwap), so the buttons inside the new element are revealed there. No
// inline handler, so the strict CSP holds.
function revealDismissButtons(root) {
  if (root.matches?.("[data-dismiss]")) {
    root.hidden = false;
  }
  for (const button of root.querySelectorAll?.("[data-dismiss]") ?? []) {
    button.hidden = false;
  }
}
revealDismissButtons(document);
document.addEventListener("htmx:load", (event) => revealDismissButtons(event.target));
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-dismiss]");
  const alert = button && button.closest(".alert, .toast");
  if (!alert) {
    return;
  }
  if (alert.matches(".toast")) {
    dismissToast(alert);
    return;
  }
  alert.remove();
  document.getElementById("main")?.focus();
});

// The control that raised each toast (a button with data-toast), to give the focus back to.
const toastTriggers = new WeakMap();

// A toast sits at the end of the main content: sending the focus to the top of it would lose
// the visitor's place. The focus goes to the next toast's Dismiss button, else the previous
// one's, else back to the control that raised the toast, else to the main content.
function dismissToast(toast) {
  const neighbour = [toast.nextElementSibling, toast.previousElementSibling].find((element) =>
    element?.matches(".toast"),
  );
  const trigger = toastTriggers.get(toast);
  toast.remove();
  const target =
    neighbour?.querySelector(".toast__dismiss") ?? (trigger?.isConnected ? trigger : null);
  (target ?? document.getElementById("main"))?.focus();
}

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

// Script-only controls of the component library (THM.4): the dialog openers
// (components/dialog.html, components/gallery.html), the carousel buttons
// (components/carousel.html), the quantity buttons (components/quantity_stepper.html) and the
// toast buttons of the styleguide (components/toast.html) are
// rendered `hidden`, so without JavaScript no button does nothing. Revealed here, and in every
// element htmx adds.
const ENHANCED = "[data-dialog], [data-carousel], [data-step], [data-toast]";

// Quantity stepper: the buttons step the field within its min and max and fire the events a
// typed change fires. At a limit a button is marked aria-disabled, not disabled: a focused
// button that turns disabled drops the focus to <body> (WCAG 2.4.3, 2.4.7), while the capture
// guard above stops its clicks. Only a disabled field disables its buttons.
function syncStepper(input) {
  const value = Number(input.value);
  for (const button of input.closest(".quantity").querySelectorAll("[data-step]")) {
    const down = button.dataset.step === "-1";
    const limit = down ? input.min : input.max;
    const atLimit = limit !== "" && (down ? value <= Number(limit) : value >= Number(limit));
    button.disabled = input.disabled;
    setAriaDisabled(button, atLimit && !input.disabled);
  }
}

function setAriaDisabled(button, disabled) {
  if (disabled) {
    button.setAttribute("aria-disabled", "true");
  } else {
    button.removeAttribute("aria-disabled");
  }
}

// Carousel: the Previous button is marked aria-disabled at the start of the track and the Next
// button at its end, so a click that cannot scroll is announced as unavailable. `scrollLeft` is
// negative in a right-to-left page, hence the absolute value; one pixel absorbs rounding.
function syncCarousel(track) {
  const offset = Math.abs(track.scrollLeft);
  const atStart = offset <= 1;
  const atEnd = offset + track.clientWidth >= track.scrollWidth - 1;
  for (const button of document.querySelectorAll(`[data-carousel][aria-controls="${track.id}"]`)) {
    setAriaDisabled(button, button.dataset.carousel === "next" ? atEnd : atStart);
  }
}

function revealEnhanced(root) {
  const buttons = [...(root.querySelectorAll?.(ENHANCED) ?? [])];
  if (root.matches?.(ENHANCED)) {
    buttons.push(root);
  }
  for (const button of buttons) {
    button.hidden = false;
  }
  for (const input of root.querySelectorAll?.(".quantity input") ?? []) {
    syncStepper(input);
  }
  for (const track of root.querySelectorAll?.(".carousel__track") ?? []) {
    syncCarousel(track);
  }
}

revealEnhanced(document);
document.addEventListener("htmx:load", (event) => revealEnhanced(event.target));
document.addEventListener("input", (event) => {
  if (event.target.matches?.(".quantity input")) {
    syncStepper(event.target);
  }
});
// Scroll events do not bubble: listen in the capture phase for every carousel track.
document.addEventListener(
  "scroll",
  (event) => {
    if (event.target.matches?.(".carousel__track")) {
      syncCarousel(event.target);
    }
  },
  { capture: true, passive: true },
);
window.addEventListener("resize", () => {
  for (const track of document.querySelectorAll(".carousel__track")) {
    syncCarousel(track);
  }
});

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

document.addEventListener("click", (event) => {
  // Dialog: a native modal; the browser traps the focus, closes on Escape and returns the focus.
  const opener = event.target.closest("[data-dialog]");
  if (opener) {
    document.getElementById(opener.dataset.dialog)?.showModal();
    return;
  }
  // Carousel: scroll by one view of the track, without smooth motion when reduced is asked.
  const control = event.target.closest("[data-carousel]");
  if (control) {
    const track = document.getElementById(control.getAttribute("aria-controls"));
    const direction = control.dataset.carousel === "next" ? 1 : -1;
    track?.scrollBy({
      left: direction * track.clientWidth,
      behavior: reducedMotion.matches ? "auto" : "smooth",
    });
    return;
  }
  // Quantity stepper.
  const step = event.target.closest("[data-step]");
  if (step) {
    const input = document.getElementById(step.getAttribute("aria-controls"));
    if (!input || input.disabled) {
      return;
    }
    if (step.dataset.step === "-1") {
      input.stepDown();
    } else {
      input.stepUp();
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return;
  }
  // Toast: clone the region's template with the message; the polite region announces it.
  const trigger = event.target.closest("[data-toast]");
  if (trigger) {
    const region = document.getElementById(trigger.dataset.toastRegion);
    const template = region?.querySelector("template");
    if (!template) {
      return;
    }
    const toast = template.content.firstElementChild.cloneNode(true);
    toast.querySelector(".toast__message").textContent = trigger.dataset.toast;
    toastTriggers.set(toast, trigger);
    region.insertBefore(toast, template);
  }
});
