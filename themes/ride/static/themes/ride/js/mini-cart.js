// Ride header overlays (THM.15 part 2, spec 14 §14.6): the glue Bootstrap and htmx leave open.
//
// - The cart count: the mini-cart body (the `orders:cart_mini` fragment, loaded by htmx) carries the number of
//   items and the same number in words (`data-cart-count`, `data-cart-label`, set by the server in
//   the visitor's language). The visual badge shows the number; the polite live region
//   (#cart-count) says it in words. The first value, written when a page loads, is not announced.
// - After a product is added (htmx POST to the cart's add route) the mini-cart body is loaded
//   again, so the count and the list change without a reload.
// - The search modal puts focus on its field when it has opened.
// No inline script, no eval: htmx and Bootstrap read their own data-* attributes.

let announced = false;

const sync = () => {
  const source = document.querySelector("[data-cart-count]");
  const badge = document.getElementById("cart-badge");
  const live = document.getElementById("cart-count");
  if (!source || !badge || !live) return;
  const count = Number(source.dataset.cartCount) || 0;
  badge.textContent = String(count);
  badge.hidden = count === 0;
  const label = source.dataset.cartLabel || "";
  if (live.textContent === label) return;
  if (announced) {
    live.textContent = label;
    return;
  }
  announced = true;
  live.setAttribute("aria-live", "off");
  live.textContent = label;
  window.setTimeout(() => live.setAttribute("aria-live", "polite"), 500);
};

document.addEventListener("htmx:afterSwap", sync);

document.addEventListener("htmx:afterRequest", (event) => {
  const { successful, pathInfo, requestConfig } = event.detail;
  const path = (pathInfo && pathInfo.requestPath) || "";
  const verb = requestConfig && requestConfig.verb;
  if (successful && verb === "post" && path.endsWith("/cart/add/")) {
    document.body.dispatchEvent(new CustomEvent("cart-changed"));
  }
});

document.addEventListener("shown.bs.modal", (event) => {
  if (!(event.target instanceof Element) || event.target.id !== "search-modal") return;
  const field = event.target.querySelector("input[type='search']");
  if (field instanceof HTMLElement) field.focus();
});

sync();
