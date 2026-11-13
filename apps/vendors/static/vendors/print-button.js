// Print button of the public sales-terms page (VEN.35). A static file under the strict CSP:
// no inline handler. Every element marked data-print opens the browser's print dialog.
for (const button of document.querySelectorAll("[data-print]")) {
  button.addEventListener("click", () => window.print());
}
