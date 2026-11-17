// Ride consent-gated video modal (THM.14, spec 14 §14.3, SEO.5, SEO.9). The link in
// components/video_modal.html opens a Bootstrap modal. The embedded frame is created only while
// the modal is open and only when the visitor has consented to `marketing` (`window.mpmvConsent`,
// set by the consent module); otherwise the placeholder with the cookie-settings link stays, and
// no request goes to the video site. Closing removes the frame, which stops the video.
const allowed = () => window.mpmvConsent?.allows("marketing") === true;

function sync(box, open) {
  const frame = box.querySelector("[data-video-frame]");
  const gate = box.querySelector("[data-video-gate]");
  let src = null;
  try {
    const url = new URL(box.dataset.embed || "", location.href);
    if (url.protocol === "https:") src = url.href;
  } catch {}
  const play = open && allowed() && src;
  gate.hidden = Boolean(play);
  frame.hidden = !play;
  if (!play) {
    frame.replaceChildren();
  } else if (!frame.firstChild) {
    const iframe = document.createElement("iframe");
    iframe.src = src;
    iframe.title = box.dataset.title;
    iframe.allow = "fullscreen";
    iframe.referrerPolicy = "no-referrer";
    frame.append(iframe);
  }
}

for (const box of document.querySelectorAll("[data-video]")) {
  const modal = box.querySelector(".modal");
  modal.addEventListener("show.bs.modal", () => sync(box, true));
  modal.addEventListener("hidden.bs.modal", () => sync(box, false));
  document.addEventListener("mpmv:consent", () => sync(box, modal.classList.contains("show")));
}
