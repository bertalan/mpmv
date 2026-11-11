// D-34: name each Draftail editor after the visible label of its field (WCAG 4.1.2). Wagtail's
// editor has no label option; the label's `for` points at the hidden input it replaces.
(function () {
  function name() {
    document.querySelectorAll("[data-draftail-input]").forEach(function (input) {
      var label = document.querySelector('label[for="' + input.id + '"]');
      var field = input.closest(".field");
      if (!label || !field) return;
      if (!label.id) label.id = input.id + "_label";
      var editor = field.querySelector(".public-DraftEditor-content");
      if (editor && !editor.hasAttribute("aria-labelledby")) {
        editor.setAttribute("aria-labelledby", label.id);
      }
    });
  }
  new MutationObserver(name).observe(document.body, { childList: true, subtree: true });
  name();
})();
