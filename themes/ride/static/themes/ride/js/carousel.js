// Ride carousels (THM.14, spec 14 §14.3): the glue Bootstrap's carousel leaves open.
//
// Bootstrap moves the slides and handles the buttons, the indicators and the arrow keys. This
// module reveals the controls (without JavaScript every slide is shown and the buttons stay
// hidden) and owns what Bootstrap does not do accessibly: rotation is off unless the editor set
// `data-autoplay` (the interval in ms), it pauses while the pointer or the focus is inside, it
// never runs under `prefers-reduced-motion`, and the Pause/Play button can always stop or start
// it. While nothing rotates the slides are a polite live region, so a change is announced.
// No inline script, no eval, no library.

const reduced = matchMedia("(prefers-reduced-motion: reduce)");

for (const root of document.querySelectorAll("[data-ride-carousel]")) {
  const { Carousel } = window.bootstrap || {};
  if (!Carousel) continue;
  const interval = Number.parseInt(root.dataset.autoplay, 10) || 6000;
  const carousel = Carousel.getOrCreateInstance(root, { ride: false, interval, pause: false });
  const live = root.querySelector(".carousel-inner");
  const toggle = root.querySelector("[data-ride-toggle]");
  let wanted = root.hasAttribute("data-autoplay") && !reduced.matches;
  let hovered = false;
  let focused = false;

  const sync = () => {
    if (wanted && !hovered && !focused && !reduced.matches) carousel.cycle();
    else carousel.pause();
    live.setAttribute("aria-live", wanted ? "off" : "polite");
    if (toggle) {
      toggle.textContent = wanted ? toggle.dataset.labelPause : toggle.dataset.labelPlay;
      toggle.hidden = reduced.matches;
    }
  };

  for (const control of root.querySelectorAll("[hidden]")) {
    if (control.matches("[data-bs-slide], [data-bs-slide-to], [data-ride-toggle]")) {
      control.hidden = false;
    }
  }
  toggle?.addEventListener("click", () => {
    wanted = !wanted;
    if (wanted) focused = false; // an explicit Play wins over the focus hold
    sync();
  });
  root.addEventListener("mouseenter", () => {
    hovered = true;
    sync();
  });
  root.addEventListener("mouseleave", () => {
    hovered = false;
    sync();
  });
  root.addEventListener("focusin", () => {
    focused = true;
    sync();
  });
  root.addEventListener("focusout", (event) => {
    focused = root.contains(event.relatedTarget);
    sync();
  });
  reduced.addEventListener("change", () => {
    wanted = wanted && !reduced.matches;
    sync();
  });
  sync();
}
