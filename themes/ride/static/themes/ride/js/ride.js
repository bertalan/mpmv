// Ride: removes the `no-js` class the server puts on <html>. The offcanvas menu below 64rem shows
// as a plain list only under html.no-js, so without JavaScript the main navigation stays reachable.
// It is a classic, blocking script in <head> (one line, cached): it runs before the body is
// painted, so with JavaScript on the plain list never flashes on phones.
document.documentElement.classList.remove("no-js");
