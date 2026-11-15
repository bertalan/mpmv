// PAY.1: confirms the payment in the browser with Stripe.js. The card is typed into Stripe's own
// iframe, so no card data reaches this server. No inline script: the page passes the public key
// and the client secret as data attributes.
(function () {
  var form = document.getElementById("payment-form");
  if (!form || typeof Stripe !== "function") {
    return;
  }
  var stripe = Stripe(form.dataset.publishableKey);
  // THM.8: the theme's colours, font and radius, as literal values (Stripe cannot read our CSS).
  var appearance;
  try {
    appearance = JSON.parse(form.dataset.appearance || "{}");
  } catch (error) {
    appearance = {};
  }
  var elements = stripe.elements({ clientSecret: form.dataset.clientSecret, appearance: appearance });
  elements.create("payment").mount("#payment-element");
  var message = document.getElementById("payment-message");
  var button = form.querySelector("button[type=submit]");
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    button.disabled = true;
    stripe
      .confirmPayment({ elements: elements, confirmParams: { return_url: form.dataset.returnUrl } })
      .then(function (result) {
        if (result.error) {
          message.textContent = result.error.message;
          button.disabled = false;
        }
      });
  });
})();
