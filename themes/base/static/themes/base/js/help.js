// Contextual help (ARC.32, components/user_tools.html).
//
// The help button and the user menu's "Help" item open the native modal <dialog id="help-dialog">.
// This module reveals them (`hidden` without a script), fetches the screen's entry (GET, same
// origin, never cached) into the dialog, lists the page's form fields under it from the labels
// and help texts already in the page, returns the focus to the button on close and closes the
// menu on Escape, a click outside or focus leaving it. No keyboard shortcut; the server's HTML is
// inserted as parsed nodes, the page's text as text. No inline script or style (strict CSP).

const root = document.querySelector(".user-tools[data-help-url]");
const dialog = document.getElementById("help-dialog");

if (root && dialog) {
  const button = root.querySelector(".help-button");
  const menu = root.querySelector("details.user-menu");
  const body = dialog.querySelector(".dialog__body");
  const loadingText = body.textContent;

  for (const opener of root.querySelectorAll("[data-help-open]")) {
    opener.hidden = false;
  }

  function text(node) {
    return node ? node.textContent.replace(/\s+/g, " ").trim() : "";
  }

  // The labelled controls of the main content's forms, with their help texts: the field
  // component's own markup (storefront, account and Manager forms) and, for a hand-written form
  // (the second factor pages), a paragraph's label with the text its control points to with
  // `aria-describedby`.
  function pageFields() {
    const fields = [];
    const names =
      ".field > label, .field > fieldset > legend, .field__check > label, p > label[for]";
    for (const name of document.querySelectorAll(`main form :is(${names})`)) {
      const field = name.closest(".field");
      let help = field ? field.querySelector(".helptext") : null;
      if (!field && name.htmlFor) {
        const control = document.getElementById(name.htmlFor);
        const ids = control ? (control.getAttribute("aria-describedby") || "").split(/\s+/) : [];
        help = ids.map((id) => document.getElementById(id)).find((node) => node) || null;
      }
      fields.push({ name: text(name), help: text(help) });
    }
    return fields;
  }

  function fieldsSection(fields) {
    const section = document.createElement("section");
    section.className = "help-fields";
    const title = document.createElement("h3");
    title.className = "help-fields__title";
    title.textContent = root.dataset.helpFieldsTitle;
    const list = document.createElement("ul");
    list.className = "help-fields__list";
    for (const field of fields) {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.className = "help-fields__name";
      name.textContent = field.name;
      item.append(name);
      if (field.help) {
        const help = document.createElement("span");
        help.className = "help-fields__help";
        help.textContent = field.help;
        item.append(help);
      }
      list.append(item);
    }
    section.append(title, list);
    return section;
  }

  // Only the latest request may write to the dialog: an earlier, slower one is aborted.
  let pending = null;

  async function load() {
    if (pending) {
      pending.abort();
    }
    const mine = new AbortController();
    pending = mine;
    body.setAttribute("aria-busy", "true");
    body.textContent = loadingText;
    const query = new URLSearchParams({
      route: root.dataset.helpRoute,
      page_type: root.dataset.helpPageType,
      lang: root.dataset.helpLang,
    });
    try {
      const response = await fetch(`${root.dataset.helpUrl}?${query}`, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" },
        signal: mine.signal,
      });
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      if (pending !== mine) {
        return;
      }
      body.replaceChildren(...parsed.body.childNodes);
      const fields = pageFields();
      if (fields.length > 0) {
        body.append(fieldsSection(fields));
      }
    } catch {
      if (pending === mine) {
        body.textContent = root.dataset.helpError;
      }
    } finally {
      if (pending === mine) {
        pending = null;
        body.removeAttribute("aria-busy");
      }
    }
  }

  root.addEventListener("click", (event) => {
    if (!event.target.closest("[data-help-open]")) {
      return;
    }
    if (menu) {
      menu.open = false;
    }
    dialog.showModal();
    load();
  });

  dialog.addEventListener("close", () => button.focus());

  if (menu) {
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });
    // Keyboard users leaving the menu with Tab close it; focus moving inside it does not.
    menu.addEventListener("focusout", (event) => {
      if (menu.open && event.relatedTarget && !menu.contains(event.relatedTarget)) {
        menu.open = false;
      }
    });
    document.addEventListener("click", (event) => {
      if (menu.open && !menu.contains(event.target)) {
        menu.open = false;
      }
    });
  }
}
