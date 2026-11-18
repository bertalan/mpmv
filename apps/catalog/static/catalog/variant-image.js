// Variant image on the product page (CAT.23): choosing a version that has its own cover shows
// that picture in place of the gallery; a version without one brings the gallery back.
//
// Progressive enhancement. The server renders each variant's picture in the variant list (so it
// shows without JavaScript) and again, hidden, in the panel below the gallery; the renditions
// are the ones that already exist, nothing is requested or built here.
(() => {
  const panel = document.querySelector("[data-variant-image-panel]");
  const gallery = document.querySelector(".gallery");
  if (!panel) {
    return;
  }
  const items = [...panel.querySelectorAll("[data-variant-image-for]")];

  const show = (sku) => {
    const own = items.find((item) => item.dataset.variantImageFor === sku);
    for (const item of items) {
      item.hidden = item !== own;
    }
    panel.hidden = !own;
    if (gallery) {
      gallery.hidden = Boolean(own);
    }
  };

  for (const radio of document.querySelectorAll('input[type="radio"][name="variant"]')) {
    radio.addEventListener("change", () => {
      if (radio.checked) {
        show(radio.value);
      }
    });
  }
})();
