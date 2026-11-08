// Launch countdown (CMS.9, spec 10 §10.2, components/launch_countdown.html).
//
// The server renders the absolute date in the operator's time zone, the call to action, and the
// expired text `hidden`. This module, for each [data-countdown] block:
// - rewrites the <time> text in the visitor's time zone, in the page language (Intl);
// - shows the remaining days, hours and minutes, updated once a minute (no seconds); minutes
//   round up, as `apps.cms.engine.countdown_state` does, so the counter never reads 0 before
//   the target;
// - reveals the pause button: paused, the counter stops updating (WCAG 2.2.2);
// - at the target, hides the countdown, reveals the expired text and link, and announces the
//   expired text once through the page's polite live region (#live-region, WCAG 4.1.3). The
//   counter itself is never a live region.
// No inline script or style attribute, so the strict CSP holds; no animation at all.

const MINUTE = 60_000;
// Browsers keep a timer's delay as a signed 32-bit integer: a longer one (about 24.8 days)
// overflows and fires at once. Longer waits are cut into steps of at most this.
const MAX_DELAY = 2 ** 31 - 1;
const language = document.documentElement.lang || undefined;

function formatDate(date) {
  return new Intl.DateTimeFormat(language, {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

function formatRemaining(left) {
  const total = Math.ceil(left / MINUTE);
  const parts = [
    ["day", Math.floor(total / 1440)],
    ["hour", Math.floor(total / 60) % 24],
    ["minute", total % 60],
  ];
  // Leading zero units are left out; the minutes always show.
  const first = parts.findIndex(([, value]) => value > 0);
  const shown = parts.slice(first < 0 ? 2 : Math.min(first, 2));
  const words = shown.map(([unit, value]) =>
    new Intl.NumberFormat(language, { style: "unit", unit, unitDisplay: "long" }).format(value),
  );
  return new Intl.ListFormat(language, { style: "long", type: "unit" }).format(words);
}

function announce(text) {
  const region = document.getElementById("live-region");
  if (region && text) {
    region.textContent = text;
  }
}

function startCountdown(block) {
  const target = Date.parse(block.dataset.target);
  if (Number.isNaN(target)) {
    return;
  }
  const time = block.querySelector("time");
  const upcoming = block.querySelector("[data-countdown-upcoming]");
  const counter = block.querySelector("[data-countdown-counter]");
  const remaining = block.querySelector("[data-countdown-remaining]");
  const expired = block.querySelector("[data-countdown-expired]");
  const pause = block.querySelector("[data-countdown-pause]");
  const pauseLabel = pause?.textContent.trim();
  let timer = null;
  let paused = false;
  let done = false;

  if (time) {
    time.textContent = formatDate(new Date(target));
  }

  function expire() {
    if (done) {
      return;
    }
    done = true;
    clearTimeout(timer);
    if (upcoming) {
      upcoming.hidden = true;
    }
    block.classList.add("launch-countdown--expired");
    if (expired) {
      expired.hidden = false;
      announce(expired.querySelector(".launch-countdown__expired")?.textContent.trim());
    }
  }

  // Shows the time left and waits for the next whole minute of it (or the target).
  function tick() {
    clearTimeout(timer);
    const left = target - Date.now();
    if (left <= 0) {
      expire();
      return;
    }
    if (paused) {
      // Paused, the counter stands still; the launch itself still switches the block.
      timer = setTimeout(tick, Math.min(left, MAX_DELAY));
      return;
    }
    if (remaining) {
      remaining.textContent = formatRemaining(left);
    }
    timer = setTimeout(tick, left % MINUTE || MINUTE);
  }

  if (counter) {
    counter.hidden = false;
  }
  if (pause) {
    pause.hidden = false;
    pause.addEventListener("click", () => {
      paused = !paused;
      pause.textContent = paused ? pause.dataset.resumeLabel : pauseLabel;
      tick();
    });
  }
  tick();
}

for (const block of document.querySelectorAll("[data-countdown][data-target]")) {
  startCountdown(block);
}
