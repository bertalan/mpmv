// Consent banner (SEO.5, SEO.9; docs/dev/consent-providers.md). Shows the hidden banner when the
// mpmv_consent cookie holds no valid choice, saves a choice with fetch (plain form post as the
// fallback), and starts the `<script type="text/plain" data-consent data-src>` tags of the
// granted categories. The cookie format is parsed as apps/seo/engine.py does.
const PATTERN = /^v1\.([A-Za-z0-9_]{1,20})\.[0-9a-f]{32}\.([a-z-]{0,64})$/;
const GATED = ["preferences", "statistics", "marketing"];
const banner = document.querySelector("[data-consent-banner]");

if (banner) {
  const { cookie, version, signals: map, saved } = { ...banner.dataset };
  const signalMap = JSON.parse(map || "{}");
  const root = document.documentElement;
  const read = () => {
    const raw = document.cookie.split("; ").find((c) => c.startsWith(`${cookie}=`));
    const m = PATTERN.exec(raw ? raw.slice(cookie.length + 1) : "");
    const ok = m && m[1] === version;
    return { decided: !!ok, granted: new Set(ok ? m[2].split("-").filter((c) => GATED.includes(c)) : []) };
  };
  const started = new WeakSet();
  const publish = (state) => {
    const signals = Object.fromEntries(
      Object.entries(signalMap).map(([s, c]) => [s, c === "necessary" || state.granted.has(c) ? "granted" : "denied"]),
    );
    window.mpmvConsent = {
      decided: state.decided,
      allows: (c) => c === "necessary" || state.granted.has(c),
      signals: () => ({ ...signals }),
    };
    for (const held of document.querySelectorAll('script[type="text/plain"][data-consent]')) {
      if (!started.has(held) && state.granted.has(held.dataset.consent)) {
        started.add(held);
        const script = document.createElement("script");
        script.src = held.dataset.src;
        script.async = true;
        held.after(script);
      }
    }
    document.dispatchEvent(new CustomEvent("mpmv:consent", { detail: { ...state, signals } }));
  };
  // Focus not obscured (WCAG 2.4.11): scroll padding and bottom space of the fixed banner's height.
  const watcher = new ResizeObserver(() =>
    root.style.setProperty("--consent-offset", `${Math.ceil(banner.getBoundingClientRect().height)}px`),
  );
  const show = (visible) => {
    banner.hidden = !visible;
    if (visible) {
      watcher.observe(banner);
    } else {
      watcher.disconnect();
      root.style.removeProperty("--consent-offset");
    }
  };

  let state = read();
  publish(state);
  show(!state.decided && !document.querySelector("[data-consent-preferences]"));

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest?.("[data-consent-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    const action = event.submitter?.value || "custom";
    const body = new FormData(form);
    body.set("action", action);
    let result;
    try {
      const response = await fetch(form.getAttribute("action"), { method: "POST", body, headers: { Accept: "application/json" } });
      if (!response.ok) {
        throw new Error(response.status);
      }
      result = await response.json();
    } catch {
      form.append(Object.assign(document.createElement("input"), { type: "hidden", name: "action", value: action }));
      form.submit();
      return;
    }
    const before = state;
    state = { decided: true, granted: new Set(result.granted) };
    const page = document.querySelector("[data-consent-preferences]");
    if ([...before.granted].some((c) => !state.granted.has(c))) {
      // a script that already runs cannot be taken back; the settings page shows its notice after the reload
      if (page) {
        const url = new URL(location.href);
        url.searchParams.set("saved", "1");
        location.assign(url);
      } else {
        location.reload();
      }
      return;
    }
    if (page) {
      const labels = [];
      for (const box of page.querySelectorAll('input[name="categories"]:not(:disabled)')) {
        box.checked = state.granted.has(box.value);
        if (box.checked) {
          labels.push(box.labels[0]?.textContent.trim());
        }
      }
      const notice = page.querySelector("[data-consent-notice]");
      const head = document.createElement("p");
      head.append(Object.assign(document.createElement("strong"), { textContent: page.dataset.saved }));
      const note = document.createElement("p");
      note.textContent = labels.length ? `${page.dataset.some} ${labels.join(", ")}.` : page.dataset.none;
      notice.replaceChildren(head, note);
      notice.hidden = false;
    }
    publish(state);
    const inBanner = banner.contains(document.activeElement);
    show(false);
    const live = document.getElementById("live-region");
    if (live) {
      live.textContent = saved;
    }
    if (inBanner) {
      document.getElementById("main")?.focus();
    }
  });
}
