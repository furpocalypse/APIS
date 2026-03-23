if (window.paypal) {
    const paypalButtons = window.paypal.Buttons({
        style: {
            shape: "rect",
            layout: "vertical",
            color: "gold",
            label: "paypal",
        },
        message: {
            amount: 100,
        },
        async createOrder() {
            try {
                const response = await postJSON(
                    URL_REGISTRATION_CREATEORDER,
                    JSON.stringify({
                        charityDonation: $("#donateCharity").val(),
                        orgDonation: $("#donateOrg").val()
                    })
                );

                const orderData = await response.json();

                if (orderData.id) {
                    return orderData.id;
                }
                const errorDetail = orderData?.details?.[0];
                const errorMessage = errorDetail
                    ? `${errorDetail.issue} ${errorDetail.description} (${orderData.debug_id})`
                    : JSON.stringify(orderData);

                throw new Error(errorMessage);
            } catch (error) {
                console.error(error);
                resultMessage(`Could not initiate PayPal Checkout...<br><br>${error}`);
            }
        },
        async onApprove(data, actions) {
            try {
                const response = await postJSON(
                    URL_REGISTRATION_CHECKOUT,
                    JSON.stringify({
                        orderID: data.orderID,
                        onsite: false,
                        billingData: {
                            cc_firstname: $("#fname").val(),
                            cc_lastname: $("#lname").val(),
                            email: $("#email").val(),
                            address1: $("#add1").val(),
                            address2: $("#add2").val(),
                            city: $("#city").val(),
                            state: $("#state").val(),
                            country: $("#country").val(),
                            postal: $("#postal").val(),
                        },
                        charityDonation: $("#donateCharity").val(),
                        orgDonation: $("#donateOrg").val()
                    })
                );

                const orderData = await response.json();
                // Three cases to handle:
                //   (1) Recoverable INSTRUMENT_DECLINED -> call actions.restart()
                //   (2) Other non-recoverable errors -> Show a failure message
                //   (3) Successful transaction -> Show confirmation or thank you message

                const errorDetail = orderData?.details?.[0];

                if (errorDetail?.issue === "INSTRUMENT_DECLINED") {
                    // (1) Recoverable INSTRUMENT_DECLINED -> call actions.restart()
                    // recoverable state, per
                    // https://developer.paypal.com/docs/checkout/standard/customize/handle-funding-failures/
                    return actions.restart();
                } else if (errorDetail) {
                    // (2) Other non-recoverable errors -> Show a failure message
                    throw new Error(
                        `${errorDetail.description} (${orderData.debug_id})`
                    );
                } else if (!orderData.purchase_units) {
                    throw new Error(JSON.stringify(orderData));
                } else {
                    // (3) Successful transaction -> Show confirmation or thank you message
                    // Or go to another URL:  actions.redirect('thank_you.html');
                    const transaction =
                        orderData?.purchase_units?.[0]?.payments?.captures?.[0] ||
                        orderData?.purchase_units?.[0]?.payments
                            ?.authorizations?.[0];
                    resultMessage(
                        `Transaction ${transaction.status}: ${transaction.id}<br>
            <br>See console for all available details`
                    );
                    console.log(
                        "Capture result",
                        orderData,
                        JSON.stringify(orderData, null, 2)
                    );
                }
            } catch (error) {
                console.error(error);
                resultMessage(
                    `Sorry, your transaction could not be processed...<br><br>${error}`
                );
            }
        },
    });
    paypalButtons.render("#card-container");
}


// Example function to show a result to the user. Your site's UI library can be used instead.
function resultMessage(message) {
    const container = document.querySelector("#payment-status-container");
    container.innerHTML = message;
}
