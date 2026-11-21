// Ride scroll reveal and counters (THM.14, spec 14 §14.3). The CSS hides a `[data-reveal]`
// element only under `html.reveal-ready`, a class this module adds once it is running (so a
// blocked or failed module hides nothing), and never under `prefers-reduced-motion: reduce` or
// print; the module adds `.is-visible` once the element enters the viewport or the page end. Counters keep their final
// number in the HTML and count up once, only when motion is allowed. No library, no inline code.
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const language = document.documentElement.lang || undefined;

function countUp(box) {
  const text = box.querySelector("[data-counter-text]");
  const end = Number.parseInt(box.dataset.counter, 10);
  if (still || !text || !Number.isFinite(end)) return;
  // The server renders the localised final value; the animation only counts toward it and
  // ends on that exact text, so the format never changes when the animation stops.
  const final = text.textContent;
  const format = new Intl.NumberFormat(language);
  const start = performance.now();
  const step = (now) => {
    const t = Math.min((now - start) / 1200, 1);
    text.textContent = t < 1 ? format.format(Math.round(end * (1 - (1 - t) ** 3))) : final;
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const targets = [...document.querySelectorAll("[data-reveal], [data-counter]")];
const show = (el, animate) => {
  el.classList.add("is-visible");
  if (animate && el.hasAttribute("data-counter")) countUp(el);
};
if (still || !("IntersectionObserver" in window)) {
  for (const el of targets) show(el, false);
} else {
  let first = true;
  const pending = new Set(targets);
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const el = entry.target;
        observer.unobserve(el);
        pending.delete(el);
        // A counter that was already on screen (and not hidden by reveal) shows its final value.
        show(el, !first || el.hasAttribute("data-reveal"));
      }
      first = false;
    },
    { threshold: 0.01 },
  );
  // Nothing can scroll further: whatever is left (a block at the page end) is shown.
  const atEnd = () => {
    const root = document.documentElement;
    if (window.innerHeight + window.scrollY < root.scrollHeight - 2) return;
    for (const el of pending) {
      observer.unobserve(el);
      show(el, true);
    }
    pending.clear();
  };
  for (const el of targets) observer.observe(el);
  document.documentElement.classList.add("reveal-ready");
  addEventListener("scroll", atEnd, { passive: true });
  atEnd();
}
