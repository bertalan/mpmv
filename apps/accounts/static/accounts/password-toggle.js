// Show-password toggle (CMP.9, WCAG 2.2 SC 3.3.8, spec 14 §14.6). An ES module loaded as a
// static file under the strict CSP: no inline code. Every password field marked
// data-password-toggle="<button text>" gets a button right after it that shows and hides the
// password; aria-pressed tells assistive technology its state. The field is a password field
// again before its form is submitted, so the browser never stores the value as plain text.
// Without JavaScript nothing changes: the field still accepts paste and password managers.

const enhance = (input) => {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "password-toggle";
  button.textContent = input.dataset.passwordToggle || "Show password";
  button.setAttribute("aria-pressed", "false");
  if (input.id) button.setAttribute("aria-controls", input.id);

  const show = (visible) => {
    input.type = visible ? "text" : "password";
    button.setAttribute("aria-pressed", visible ? "true" : "false");
  };

  button.addEventListener("click", () => show(input.type === "password"));
  if (input.form) input.form.addEventListener("submit", () => show(false));
  input.after(button);
};

for (const input of document.querySelectorAll("input[type=password][data-password-toggle]")) {
  enhance(input);
}
