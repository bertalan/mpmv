// Palette builder, live part (THM.34 2/2, spec 14 §14.12). A static file under the strict CSP:
// no inline code. It only enhances the plain form: on each change it asks the server for the pair
// list (the same `check_brand_palette` numbers that guard the save), swaps the preview
// stylesheet (a generated same-origin file) and turns "Use this" into an in-page action.
// Without JavaScript the form still validates on submit.

const form = document.getElementById("palette-builder");
if (form && form.dataset.checkUrl) {
  const roles = ["primary", "secondary", "band"];
  const hex = /^#[0-9A-Fa-f]{6}$/;
  const inputs = Object.fromEntries(roles.map((r) => [r, document.getElementById(`id_${r}`)]));
  const summary = document.getElementById("palette-failing");
  const body = document.querySelector("#palette-pairs tbody");
  const save = form.querySelector('button[value="save"]');
  const check = form.querySelector('button[value="check"]');
  const preview = document.querySelector(".palette-preview");
  const link = document.getElementById("palette-preview-css");
  const start = form.querySelector('input[name="start"]').value;
  let reason = document.getElementById("palette-save-reason");
  let timer = 0;
  let serial = 0;

  const query = () => {
    const params = new URLSearchParams({ start });
    for (const role of roles) params.set(role, inputs[role].value.trim());
    return params;
  };

  const cell = (tag, text) => {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  };

  const showReason = (text) => {
    if (!reason) {
      reason = document.createElement("p");
      reason.id = "palette-save-reason";
      save.parentElement.append(reason);
    }
    reason.textContent = text;
    save.setAttribute("aria-describedby", "palette-save-reason");
  };

  const render = (data) => {
    body.replaceChildren(
      ...data.rows.map((row) => {
        const tr = document.createElement("tr");
        tr.dataset.pairResult = row.passes ? "pass" : "fail";
        const th = cell("th", row.name);
        th.scope = "row";
        const fix = document.createElement("td");
        if (row.suggestion) {
          const button = cell("button", row.suggestion.label);
          button.type = "button";
          button.className = "button button-small button-secondary";
          button.dataset.field = row.suggestion.field;
          button.dataset.colour = row.suggestion.colour;
          fix.append(button);
        } else {
          fix.textContent = row.no_fix;
        }
        tr.append(th, cell("td", `${row.ratio}:1`), cell("td", `${row.minimum}:1`), cell("td", row.label), fix);
        return tr;
      }),
    );
    summary.textContent = data.summary;
    save.disabled = !data.valid;
    if (data.valid) {
      save.removeAttribute("aria-describedby");
      if (reason) reason.textContent = "";
    } else {
      showReason(data.reason);
    }
  };

  // The check is out of reach: keep the last good pair list, say so in the live region and keep
  // Save off, because the colours in the fields are no longer the ones that were checked.
  const fail = () => {
    summary.textContent = form.dataset.checkError;
    save.disabled = true;
    showReason(form.dataset.checkError);
  };

  const refresh = async () => {
    const mine = ++serial;
    const params = query();
    if (roles.some((r) => params.get(r) && !hex.test(params.get(r)))) return; // still typing
    try {
      const response = await fetch(`${form.dataset.checkUrl}?${params}`, { credentials: "same-origin" });
      if (mine !== serial) return; // a newer change has already been asked
      if (!response.ok) throw new Error(String(response.status));
      render(await response.json());
      link.href = `${form.dataset.previewUrl}?${params}`;
    } catch (error) {
      if (mine === serial) fail();
    }
  };

  const schedule = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, 150);
  };

  // A colour picker beside each hex field; the hex field stays the field that is submitted.
  for (const role of roles) {
    const text = inputs[role];
    const picker = document.createElement("input");
    picker.type = "color";
    picker.className = "palette-picker";
    picker.setAttribute("aria-label", `${document.querySelector(`label[for="id_${role}"]`).textContent.trim()}, ${form.dataset.pickerLabel}`);
    picker.value = hex.test(text.value) ? text.value.toLowerCase() : "#000000";
    text.after(picker);
    picker.addEventListener("input", () => {
      text.value = picker.value.toUpperCase();
      schedule();
    });
    text.addEventListener("input", () => {
      if (hex.test(text.value)) picker.value = text.value.toLowerCase();
      schedule();
    });
  }

  body.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-field]");
    if (!button) return;
    const text = inputs[button.dataset.field];
    text.value = button.dataset.colour;
    text.dispatchEvent(new Event("input"));
    text.focus();
  });

  check.hidden = true; // the page checks as you type
  preview.hidden = false;
  refresh();
}
