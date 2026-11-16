// Ride scroll reveal and counters (THM.14, spec 14 §14.3). The CSS hides a `[data-reveal]`
// element only with JavaScript on (no `no-js` class) and without `prefers-reduced-motion:
// reduce`; this module adds `.is-visible` once it enters the viewport. Counters keep their final
// number in the HTML and count up once, only when motion is allowed. No library, no inline code.
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const language = document.documentElement.lang || undefined;

function countUp(box) {
  const text = box.querySelector("[data-counter-text]");
  const end = Number.parseInt(box.dataset.counter, 10);
  if (still || !text || !Number.isFinite(end)) return;
  const format = new Intl.NumberFormat(language);
  const start = performance.now();
  const step = (now) => {
    const t = Math.min((now - start) / 1200, 1);
    text.textContent = format.format(Math.round(end * (1 - (1 - t) ** 3)));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const targets = document.querySelectorAll("[data-reveal], [data-counter]");
const seen = (el) => {
  el.classList.add("is-visible");
  if (el.hasAttribute("data-counter")) countUp(el);
};
if (still || !("IntersectionObserver" in window)) {
  for (const el of targets) el.classList.add("is-visible");
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        seen(entry.target);
      }
    },
    { rootMargin: "0px 0px -10% 0px" },
  );
  for (const el of targets) observer.observe(el);
}
