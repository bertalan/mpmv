// Ride: removes the `no-js` class the server puts on <html>. The offcanvas menu below 64rem shows
// as a plain list only under html.no-js, so without JavaScript the main navigation stays reachable.
// It is a classic, blocking script in <head> (one line, cached): it runs before the body is
// painted, so with JavaScript on the plain list never flashes on phones.
document.documentElement.classList.remove("no-js");
// Back-to-top (THM.15): the footer link stays visible without JavaScript (html.no-js); with it, the
// link shows only after the page is scrolled `is-scrolled` (CSS). No smooth scroll is set here.
(function () {
  var root = document.documentElement;
  // `is-away` (THM.21) turns the transparent header over the hero solid as soon as the page moves.
  function update() {
    root.classList.toggle("is-scrolled", window.scrollY > 400);
    root.classList.toggle("is-away", window.scrollY > 8);
  }
  window.addEventListener("scroll", update, { passive: true });
  update();
})();
