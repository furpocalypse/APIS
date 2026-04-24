/*
 * Replacement for https://www.paypal.com/sdk/js loaded by the checkout
 * templates. Only implements the fields that registration/static/js/
 * payments-paypal.js reaches for: window.paypal.Buttons({...}).render(sel).
 *
 * The stub renders a single native button into the host element. On click it:
 *   1. awaits the callback's createOrder() — which hits the site's
 *      /cart/paypalcreate/ endpoint and returns a real server-side order id.
 *   2. immediately invokes onApprove({ orderID }) to simulate a buyer
 *      approval, which the site's code then captures server-side (and the
 *      in-process Python stub resolves deterministically).
 *
 * Errors surface by invoking onError if present, then re-throwing so the
 * Playwright test fails loudly instead of silently hanging.
 */
(function () {
  function Buttons(options) {
    options = options || {};
    return {
      render: function (selector) {
        var host =
          typeof selector === "string"
            ? document.querySelector(selector)
            : selector;
        if (!host) {
          throw new Error("PayPal stub: render target not found: " + selector);
        }
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = "Pay with PayPal (mock)";
        button.setAttribute("data-testid", "paypal-mock-button");
        button.style.padding = "12px 24px";
        button.style.background = "#ffc439";
        button.style.border = "1px solid #c88a00";
        button.style.borderRadius = "4px";
        button.style.cursor = "pointer";
        button.style.fontWeight = "bold";
        button.addEventListener("click", async function () {
          button.disabled = true;
          try {
            var orderID = await options.createOrder();
            if (!orderID) {
              throw new Error("PayPal stub: createOrder returned falsy");
            }
            if (typeof options.onApprove === "function") {
              await options.onApprove({ orderID: orderID });
            }
          } catch (err) {
            if (typeof options.onError === "function") {
              try {
                options.onError(err);
              } catch (_ignored) {
                /* noop */
              }
            }
            console.error("[paypal-stub] click failed", err);
            button.disabled = false;
            throw err;
          }
        });
        host.appendChild(button);
        return Promise.resolve();
      },
      isEligible: function () {
        return true;
      },
      close: function () {
        return Promise.resolve();
      },
    };
  }

  function FUNDING() {
    return {};
  }

  window.paypal = {
    Buttons: Buttons,
    FUNDING: {
      PAYPAL: "paypal",
      CARD: "card",
      VENMO: "venmo",
    },
    getFundingSources: function () {
      return ["paypal"];
    },
    rememberFunding: function () {},
    version: "e2e-stub",
  };

  window.dispatchEvent(new Event("paypal-stub-loaded"));
})();
