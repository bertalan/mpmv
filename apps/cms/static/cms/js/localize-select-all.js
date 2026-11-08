// "Select all" on wagtail-localize's translate form (I18N.3), as a static file for the CSP (SEC.1).
document.addEventListener("DOMContentLoaded", function () {
  "use strict";
  var selectAll = document.querySelector('[name="select_all"]');
  var locales = document.querySelectorAll('[name="locales"]');
  if (!selectAll) {
    return;
  }
  selectAll.addEventListener("change", function () {
    for (var i = 0; i < locales.length; i++) {
      locales[i].checked = selectAll.checked;
    }
  });
});
