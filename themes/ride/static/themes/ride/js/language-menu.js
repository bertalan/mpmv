// Ride language menu (THM.35, spec 14 §14.6): the disclosure behind `[data-language-menu]`.
// The toggle button flips `aria-expanded` and the `is-open` class of the menu. Escape closes it and
// returns focus to the toggle (it stops there, so an open menu drawer stays open); Tab out of the
// menu and a click outside close it. Until this script has run (and added `language-menu--enhanced`) the list is shown expanded (CSS).
// No inline script, no eval.

const close = (menu, focus) => {
  const toggle = menu.querySelector(".language-menu__toggle");
  menu.classList.remove("is-open");
  toggle.setAttribute("aria-expanded", "false");
  if (focus) toggle.focus();
};

document.querySelectorAll("[data-language-menu]").forEach((menu) => {
  // The collapsed look is CSS under this class, so a script that never runs leaves the list open.
  menu.classList.add("language-menu--enhanced");
  const toggle = menu.querySelector(".language-menu__toggle");
  toggle.addEventListener("click", () => {
    const open = !menu.classList.contains("is-open");
    menu.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", String(open));
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !menu.classList.contains("is-open")) return;
    event.stopPropagation();
    close(menu, true);
  });
  menu.addEventListener("focusout", (event) => {
    if (event.relatedTarget && !menu.contains(event.relatedTarget)) close(menu, false);
  });
  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) close(menu, false);
  });
});
