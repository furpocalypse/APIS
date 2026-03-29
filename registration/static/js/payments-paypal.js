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
                displayPaymentResults(
                    `Could not initiate PayPal Checkout...<br><br>${error}`, true
                );
            }
        },
        async onApprove(data, actions) {
            hidePaymentResults();
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
                    throw new Error(`
                        Sorry, your payment failed for a mysterious reason 
                        (${errorDetail.description} [${orderData.debug_id}]).
                        If the problem persists, please contact
                        <a href="mailto:${EVENT_REGISTRATION_EMAIL}">${EVENT_REGISTRATION_EMAIL}</a>
                        for assistance.</p>
                    `);
                } else if (!orderData.purchase_units) {
                    console.log('Capture result', orderData, JSON.stringify(orderData, null, 2));
                    throw new Error(`
                        Sorry, your payment failed for a mysterious reason 
                        If the problem persists, please contact
                        <a href="mailto:${EVENT_REGISTRATION_EMAIL}">${EVENT_REGISTRATION_EMAIL}</a>
                        for assistance.</p>
                    `);
                } else {
                    // (3) Successful transaction -> Show confirmation or thank you message
                    // Or go to another URL:  actions.redirect('thank_you.html');
                    const transaction =
                        orderData?.purchase_units?.[0]?.payments?.captures?.[0] ||
                        orderData?.purchase_units?.[0]?.payments
                            ?.authorizations?.[0];
                    console.log(
                        "Capture result",
                        orderData,
                        JSON.stringify(orderData, null, 2)
                    );
                    displayPaymentResults('');
                    window.location = URL_REGISTRATION_DONE;
                }
            } catch (error) {
                displayPaymentResults(error, true);
                console.error(error);
            }
        },
    });
    paypalButtons.render("#card-container");
}


// Helper method for displaying the Payment Status on the screen.
function displayPaymentResults(message, isError) {
    const statusContainer = document.getElementById('payment-status-container');
    statusContainer.innerHTML = message;

    if (!isError) {
        statusContainer.classList.remove('is-failure');
        statusContainer.classList.add('is-success');
    } else {
        statusContainer.classList.remove('is-success');
        statusContainer.classList.add('is-failure');
    }

    statusContainer.style.visibility = 'visible';
}

function hidePaymentResults() {
    const statusContainer = document.getElementById('payment-status-container');
    statusContainer.style.visibility = 'hidden';
}
