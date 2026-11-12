// Contextual help in both admins (ARC.32 parts 3 and 4): the Wagtail admin and the staff admin.
//
// An opener is any element with `data-admin-help-open`: the staff admin's header button, or the
// "Help for this page" item of the Wagtail sidebar's Help menu. It carries what the dialog needs
// (data-help-url, -route, -page-type, -lang and the already translated -title, -loading, -error,
// -fields-title, -close), so this file holds no text of its own. A click:
// - opens a native modal <dialog> (built here once): the browser keeps the focus inside, makes the
//   page behind inert and closes it on Escape;
// - fetches the entry for the screen from the help endpoint (GET, same origin, never cached) and
//   puts the server's HTML in as parsed nodes (never innerHTML);
// - lists the form fields of the screen under it, read from the labels and help texts already in
//   the page (Wagtail's `.w-field` and the staff admin's `.form-row`), as text content only;
// - returns the focus to the opener on close.
// The listener is delegated, so it also works for Wagtail's sidebar, which draws its menu itself.
// There is no keyboard shortcut. No inline script or style: the strict CSP holds.

(() => {
  const DIALOG_ID = "admin-help-dialog";
  let dialog = null;
  let body = null;
  let opener = null;
  let controller = null;

  function squash(text) {
    return text.replace(/\s+/g, " ").trim();
  }

  function labelText(label) {
    const clone = label.cloneNode(true);
    for (const mark of clone.querySelectorAll(".w-required-mark, .visually-hidden, .sr-only")) {
      mark.remove();
    }
    return squash(clone.textContent).replace(/[*:]+$/, "").trim();
  }

  // The labelled fields of the screen's forms, with their help texts.
  function pageFields() {
    const fields = [];
    const rows = document.querySelectorAll(
      "main .w-field, #content-main .form-row, #content-main .field-row",
    );
    for (const row of rows) {
      const label = row.querySelector(".w-field__label, label, legend");
      if (!label || row.closest("dialog")) {
        continue;
      }
      const name = labelText(label);
      if (!name) {
        continue;
      }
      const help = row.querySelector(".w-field__help, .help, .helptext");
      fields.push({ name, help: help ? squash(help.textContent) : "" });
    }
    return fields;
  }

  function fieldsSection(fields, title) {
    const section = document.createElement("section");
    section.className = "help-fields";
    const heading = document.createElement("h3");
    heading.className = "help-fields__title";
    heading.textContent = title;
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
    section.append(heading, list);
    return section;
  }

  function build(data) {
    const element = document.createElement("dialog");
    element.id = DIALOG_ID;
    element.className = "admin-help";
    element.setAttribute("aria-labelledby", `${DIALOG_ID}-title`);
    const header = document.createElement("div");
    header.className = "admin-help__header";
    const title = document.createElement("h2");
    title.id = `${DIALOG_ID}-title`;
    title.className = "admin-help__title";
    title.textContent = data.helpTitle;
    const form = document.createElement("form");
    form.method = "dialog";
    const close = document.createElement("button");
    close.type = "submit";
    close.className = "admin-help__close dialog__close";
    close.setAttribute("aria-label", data.helpClose);
    close.textContent = "×";
    form.append(close);
    header.append(title, form);
    body = document.createElement("div");
    body.className = "admin-help__body dialog__body";
    element.append(header, body);
    element.addEventListener("close", () => {
      // The close event is queued: if the dialog was reopened meanwhile, the load now running
      // belongs to the new opening and must not be cancelled.
      if (controller && !element.open) {
        controller.abort();
      }
      // Wagtail redraws its sidebar, so the opener may be gone: the Help item that is there now,
      // else the page itself.
      const back =
        opener && opener.isConnected ? opener : document.querySelector("[data-admin-help-open]");
      (back || document.body).focus();
    });
    document.body.append(element);
    return element;
  }

  async function load(data) {
    // A newer load cancels the one before it, so a slow stale answer never replaces a fresh one.
    if (controller) {
      controller.abort();
    }
    const mine = new AbortController();
    controller = mine;
    const target = body;
    target.setAttribute("aria-busy", "true");
    target.textContent = data.helpLoading;
    const query = new URLSearchParams({
      route: data.helpRoute || "",
      page_type: data.helpPageType || "",
      lang: data.helpLang || "",
    });
    try {
      const response = await fetch(`${data.helpUrl}?${query}`, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" },
        signal: mine.signal,
      });
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      const text = await response.text();
      if (mine.signal.aborted) {
        return;
      }
      const parsed = new DOMParser().parseFromString(text, "text/html");
      target.replaceChildren(...parsed.body.childNodes);
      const fields = pageFields();
      if (fields.length > 0) {
        target.append(fieldsSection(fields, data.helpFieldsTitle));
      }
    } catch {
      if (!mine.signal.aborted) {
        target.textContent = data.helpError;
      }
    } finally {
      if (controller === mine) {
        target.removeAttribute("aria-busy");
      }
    }
  }

  function open(target) {
    const data = target.dataset;
    opener = target;
    dialog = document.getElementById(DIALOG_ID) || build(data);
    body = dialog.querySelector(".admin-help__body");
    if (!dialog.open) {
      dialog.showModal();
    }
    load(data);
  }

  function opening(event) {
    return event.target instanceof Element ? event.target.closest("[data-admin-help-open]") : null;
  }

  document.addEventListener("click", (event) => {
    const target = opening(event);
    if (!target) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    open(target);
  });

  // Wagtail's item is a link with role="button": a button answers Space as well as Enter.
  document.addEventListener("keydown", (event) => {
    const target = opening(event);
    if (target && target.tagName === "A" && event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      open(target);
    }
  });
})();
