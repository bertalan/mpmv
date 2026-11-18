// Variant galleries on the product page (CAT.23, CAT.26): choosing a version that has images of
// its own swaps the product gallery for that version's gallery (its own images first, then the
// product's); a version without any brings the product gallery back.
//
// Progressive enhancement. Without JavaScript the server's page shows the product gallery and,
// below it, each version's gallery under a heading naming the version, so every picture is
// reachable. Here the headings and the galleries of the other versions are hidden; the
// renditions are the ones that already exist, nothing is requested or built.
(() => {
  const root = document.querySelector("[data-variant-galleries]");
  if (!root) {
    return;
  }
  const productGallery = [...document.querySelectorAll(".gallery")].find((gallery) => !root.contains(gallery));
  const sections = [...root.querySelectorAll("[data-variant-gallery-for]")];
  for (const title of root.querySelectorAll("[data-variant-gallery-title]")) {
    title.hidden = true;
  }

  const show = (sku) => {
    const own = sections.find((section) => section.dataset.variantGalleryFor === sku);
    for (const section of sections) {
      section.hidden = section !== own;
    }
    if (productGallery) {
      productGallery.hidden = Boolean(own);
    }
  };

  for (const radio of document.querySelectorAll('input[type="radio"][name="variant"]')) {
    radio.addEventListener("change", () => {
      if (radio.checked) {
        show(radio.value);
      }
    });
  }
  const chosen = document.querySelector('input[type="radio"][name="variant"]:checked');
  show(chosen ? chosen.value : "");
})();
