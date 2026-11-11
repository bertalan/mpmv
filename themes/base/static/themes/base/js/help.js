// Contextual help (ARC.32, components/user_tools.html).
//
// For a signed-in user the header holds a help button and a user menu with a "Help" item. Both
// open the native modal <dialog id="help-dialog">: the browser keeps the focus inside, makes the
// page behind inert and closes it on Escape. This module
// - reveals the two openers (they are `hidden` without a script);
// - fetches the entry for the screen from the help endpoint (GET, same origin, never cached) and
//   puts it in the dialog body;
// - lists the form fields of the page under it ("Fields on this page"), read from the labels
//   and help texts already in the page, so the help text is the form's own, nothing is repeated
//   on the server;
// - returns the focus to the help button when the dialog closes, also when it was opened from
//   the menu, and closes the menu on Escape or a click outside it.
// There is no keyboard shortcut. The server's HTML is inserted as parsed nodes and the page's
// text only as text content. No inline script or style: the strict CSP holds.

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

  async function load() {
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
      });
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      body.replaceChildren(...parsed.body.childNodes);
      const fields = pageFields();
      if (fields.length > 0) {
        body.append(fieldsSection(fields));
      }
    } catch {
      body.textContent = root.dataset.helpError;
    } finally {
      body.removeAttribute("aria-busy");
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
    document.addEventListener("click", (event) => {
      if (menu.open && !menu.contains(event.target)) {
        menu.open = false;
      }
    });
  }
}
