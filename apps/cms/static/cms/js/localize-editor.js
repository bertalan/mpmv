// Set-up of wagtail-localize's translation editor under the strict CSP (I18N.3, SEC.1).
//
// 1. The chooser URLs come from data attributes, so no inline script is needed.
// 2. The editor styles itself with styled-components, which adds <style> elements but cannot be
//    given the CSP nonce (its bundle fixes webpack's nonce to undefined). Style elements created
//    by scripts on this page get the request's nonce here; this runs before the editor bundle.
//    Only scripts the CSP already lets run can create elements, so markup injected into the page
//    gains nothing, and inline style attributes stay blocked.
(function () {
  "use strict";
  var settings = document.getElementById("localize-editor-settings");
  if (!settings) {
    return;
  }
  if (typeof window.chooserUrls === "undefined") {
    window.chooserUrls = {
      imageChooser: settings.dataset.imageChooser,
      documentChooser: settings.dataset.documentChooser,
      pageChooser: settings.dataset.pageChooser,
    };
  }
  var nonce = settings.dataset.styleNonce;
  if (nonce) {
    var createElement = document.createElement;
    document.createElement = function (tagName) {
      var element = createElement.apply(document, arguments);
      if (String(tagName).toLowerCase() === "style") {
        element.setAttribute("nonce", nonce);
      }
      return element;
    };
  }
})();
