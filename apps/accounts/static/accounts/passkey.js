// Passkey registration and authentication (CORE.4). Loaded as a static file under the strict
// CSP: no inline code. Forms marked data-passkey="register|authenticate" carry the endpoint URLs
// and the CSRF token in data attributes; the server returns WebAuthn options as JSON with
// base64url byte fields. The forms have no method and their buttons are type="button", so
// nothing posts natively; once bound, a form gets data-passkey-ready.
(() => {
  "use strict";

  const toBytes = (value) => {
    let text = value.replace(/-/g, "+").replace(/_/g, "/");
    while (text.length % 4) text += "=";
    return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
  };

  const toBase64url = (buffer) => {
    let binary = "";
    for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  };

  const postJson = async (url, form, body) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": form.dataset.csrfToken,
      },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || form.dataset.textRequestFailed || "The request failed.");
    return data;
  };

  const descriptors = (list) => (list || []).map((item) => ({ ...item, id: toBytes(item.id) }));

  const showCodes = (codes, redirect) => {
    const section = document.getElementById("recovery-codes");
    const list = section.querySelector("ol.recovery-codes");
    list.replaceChildren(
      ...codes.map((code) => {
        const item = document.createElement("li");
        const text = document.createElement("code");
        text.textContent = code;
        item.append(text);
        return item;
      }),
    );
    section.querySelector("[data-continue]").href = redirect;
    section.hidden = false;
  };

  const register = async (form) => {
    const { publicKey } = await postJson(form.dataset.optionsUrl, form);
    publicKey.challenge = toBytes(publicKey.challenge);
    publicKey.user.id = toBytes(publicKey.user.id);
    publicKey.excludeCredentials = descriptors(publicKey.excludeCredentials);
    const credential = await navigator.credentials.create({ publicKey });
    const response = credential.response;
    const result = await postJson(form.dataset.finishUrl, form, {
      name: (form.querySelector("input[name=name]") || {}).value || "Passkey",
      credential: {
        id: credential.id,
        rawId: toBase64url(credential.rawId),
        type: credential.type,
        response: {
          clientDataJSON: toBase64url(response.clientDataJSON),
          attestationObject: toBase64url(response.attestationObject),
          transports: response.getTransports ? response.getTransports() : [],
        },
        clientExtensionResults: credential.getClientExtensionResults(),
        authenticatorAttachment: credential.authenticatorAttachment,
      },
    });
    if (result.recovery_codes && result.recovery_codes.length) {
      form.hidden = true;
      showCodes(result.recovery_codes, result.redirect);
    } else {
      window.location.assign(result.redirect);
    }
  };

  const authenticate = async (form) => {
    const { publicKey } = await postJson(form.dataset.optionsUrl, form);
    publicKey.challenge = toBytes(publicKey.challenge);
    publicKey.allowCredentials = descriptors(publicKey.allowCredentials);
    const credential = await navigator.credentials.get({ publicKey });
    const response = credential.response;
    const result = await postJson(form.dataset.finishUrl, form, {
      credential: {
        id: credential.id,
        rawId: toBase64url(credential.rawId),
        type: credential.type,
        response: {
          clientDataJSON: toBase64url(response.clientDataJSON),
          authenticatorData: toBase64url(response.authenticatorData),
          signature: toBase64url(response.signature),
          userHandle: response.userHandle ? toBase64url(response.userHandle) : null,
        },
        clientExtensionResults: credential.getClientExtensionResults(),
        authenticatorAttachment: credential.authenticatorAttachment,
      },
    });
    window.location.assign(result.redirect);
  };

  const ceremonies = { register, authenticate };

  for (const form of document.querySelectorAll("form[data-passkey]")) {
    const error = form.querySelector("[data-passkey-error]");
    if (!window.PublicKeyCredential) {
      error.textContent =
        form.dataset.textUnsupported || "This browser does not support passkeys.";
      error.hidden = false;
      continue;
    }
    let running = false;
    const start = async (event) => {
      event.preventDefault();
      if (running) return;
      running = true;
      error.hidden = true;
      try {
        await ceremonies[form.dataset.passkey](form);
      } catch (err) {
        error.textContent = err && err.message
          ? err.message
          : form.dataset.textFailed || "The passkey did not work.";
        error.hidden = false;
      } finally {
        running = false;
      }
    };
    form.addEventListener("submit", start); // Enter in the name field
    for (const button of form.querySelectorAll("[data-passkey-start]")) {
      button.addEventListener("click", start);
    }
    form.dataset.passkeyReady = "1";
  }
})();
