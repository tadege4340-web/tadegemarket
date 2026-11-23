
(function () {
    "use strict";

    // Prevent loading the same payment handlers more than once.
    if (window.shopNovaPaymentEventsLoaded) return;
    window.shopNovaPaymentEventsLoaded = true;

    function getElement(id) {
        return document.getElementById(id);
    }

    function setText(id, value) {
        const element = getElement(id);

        if (element) {
            element.textContent =
                value !== undefined && value !== null && value !== ""
                    ? value
                    : "-";
        }
    }

    function openModal(id) {
        const modal = getElement(id);

        if (modal) {
            modal.classList.add("active");
        }
    }

    function closeModal(modal) {
        if (modal) {
            modal.classList.remove("active");
        }
    }

    function getPaymentId(button) {
        if (!button) return null;

        // First check the button itself.
        if (button.dataset.paymentId || button.dataset.id) {
            return button.dataset.paymentId || button.dataset.id;
        }

        // Then check the payment row.
        const row = button.closest(".payment-row");

        if (row) {
            if (row.dataset.paymentId || row.dataset.id) {
                return row.dataset.paymentId || row.dataset.id;
            }

            const detailsButton = row.querySelector(".payment-view");

            if (detailsButton) {
                return detailsButton.dataset.id;
            }
        }

        return null;
    }

    // -----------------------------------------------------
    // CLOSE MODAL
    // -----------------------------------------------------

    window.closePaymentModal = function (modalId) {
        closeModal(getElement(modalId));
    };

    // -----------------------------------------------------
    // PAYMENT TABLE FILTER
    // -----------------------------------------------------

    window.filterPaymentsTable = function () {
        const table = getElement("paymentsTable");

        if (!table) return;

        const searchInput = getElement("paymentSearch");
        const statusInput = getElement("paymentStatusFilter");
        const methodInput = getElement("paymentMethodFilter");
        const resultCount = getElement("paymentResultCount");

        const search = searchInput
            ? searchInput.value.toLowerCase().trim()
            : "";

        const status = statusInput ? statusInput.value : "";
        const method = methodInput ? methodInput.value : "";

        const rows = table.querySelectorAll(".payment-row");

        let visible = 0;

        rows.forEach(function (row) {
            const rowSearch = (row.dataset.search || "").toLowerCase();
            const rowStatus = row.dataset.status || "";
            const rowMethod = row.dataset.method || "";

            const matchesSearch = rowSearch.includes(search);
            const matchesStatus = !status || rowStatus === status;
            const matchesMethod = !method || rowMethod === method;

            const show =
                matchesSearch &&
                matchesStatus &&
                matchesMethod;

            row.style.display = show ? "" : "none";

            if (show) visible++;
        });

        if (resultCount) {
            resultCount.textContent = visible + " payment(s)";
        }
    };

    // -----------------------------------------------------
    // PAYMENT DETAILS
    // -----------------------------------------------------

    window.showPaymentDetails = function (button) {
        if (!button) return;

        const data = button.dataset;

        setText("detailPaymentId", "#" + (data.id || "-"));
        setText("detailOrderId", "#" + (data.order || "-"));
        setText("detailCustomer", data.name);
        setText("detailEmail", data.email);

        setText(
            "detailMethod",
            (data.method || "-").replace(/_/g, " ")
        );

        setText("detailStatus", data.status);

        setText(
            "detailAmount",
            (data.amount || "0") + " " + (data.currency || "ETB")
        );

        setText(
            "detailReference",
            data.reference || data.txref
        );

        setText("detailRecipient", data.recipient);
        setText("detailPhone", data.phone);
        setText("detailCity", data.city);
        setText("detailAddress", data.address);
        setText("detailNotes", data.notes || "No notes");

        const receiptContainer = getElement(
            "paymentReceiptContainer"
        );

        if (receiptContainer) {
            receiptContainer.replaceChildren();

            if (data.receipt) {
                const link = document.createElement("a");

                link.href = data.receipt;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                link.className = "payment-receipt-link";
                link.textContent = "Open uploaded payment receipt";

                receiptContainer.appendChild(link);
            } else if (data.method !== "chapa") {
                const message = document.createElement("p");

                message.style.color = "#b45309";
                message.style.fontSize = "12px";
                message.textContent =
                    "No receipt is attached to this payment.";

                receiptContainer.appendChild(message);
            }
        }

        openModal("paymentDetailsModal");
    };

    // -----------------------------------------------------
    // APPROVE / REJECT PAYMENT
    // -----------------------------------------------------

    window.openPaymentDecision = function (paymentId, action) {
        if (!paymentId) {
            alert("Payment ID could not be found.");
            return;
        }

        if (action !== "approve" && action !== "reject") {
            alert("Invalid payment action.");
            return;
        }

        const isApprove = action === "approve";

        const idInput = getElement("decisionPaymentId");
        const title = getElement("decisionTitle");
        const description = getElement("decisionDescription");
        const warning = getElement("decisionWarning");
        const submitButton = getElement("decisionSubmitButton");
        const form = getElement("paymentDecisionForm");
        const note = getElement("paymentAdminNote");

        if (
            !idInput ||
            !title ||
            !description ||
            !warning ||
            !submitButton ||
            !form
        ) {
            alert("Payment decision form could not be found.");
            return;
        }

        idInput.value = paymentId;

        title.textContent = isApprove
            ? "Verify Manual Payment"
            : "Reject Manual Payment";

        description.textContent = isApprove
            ? "Confirm that you independently verified the transfer in the official merchant account."
            : "Reject this payment if the transfer cannot be verified or the submitted information is incorrect.";

        warning.textContent = isApprove
            ? "Only approve after independently confirming the transaction. Approval may trigger order processing and stock deduction."
            : "The payment will be marked as failed. Explain the reason in the admin note.";

        submitButton.textContent = isApprove
            ? "Verify Payment"
            : "Reject Payment";

        submitButton.style.backgroundColor = isApprove
            ? "#16a34a"
            : "#dc2626";

        submitButton.disabled = false;

        if (note) {
            note.value = "";
        }

        form.action = isApprove
            ? "/admin/payments/" +
                encodeURIComponent(paymentId) +
                "/approve"
            : "/admin/payments/" +
                encodeURIComponent(paymentId) +
                "/reject";

        openModal("paymentDecisionModal");
    };

    // -----------------------------------------------------
    // REFRESH PAYMENT PAGE
    // -----------------------------------------------------

    window.loadPaymentsPage = function () {
        const link = document.querySelector(
            '[data-admin-page][href*="payments"]'
        );

        if (link) {
            link.click();
        } else {
            window.location.reload();
        }
    };

    // -----------------------------------------------------
    // EVENT DELEGATION
    // Supports payment pages loaded dynamically through AJAX.
    // -----------------------------------------------------

    document.addEventListener("click", function (event) {
        const target = event.target;

        if (!(target instanceof Element)) return;

        // PAYMENT DETAILS
        const detailsButton = target.closest(".payment-view");

        if (detailsButton) {
            event.preventDefault();

            window.showPaymentDetails(detailsButton);
            return;
        }

        // APPROVE PAYMENT
        const approveButton = target.closest(".payment-approve");

        if (approveButton) {
            event.preventDefault();

            const paymentId = getPaymentId(approveButton);

            window.openPaymentDecision(paymentId, "approve");
            return;
        }

        // REJECT PAYMENT
        const rejectButton = target.closest(".payment-reject");

        if (rejectButton) {
            event.preventDefault();

            const paymentId = getPaymentId(rejectButton);

            window.openPaymentDecision(paymentId, "reject");
            return;
        }

        // REFRESH
        const refreshButton = target.closest(".payments-refresh");

        if (refreshButton) {
            event.preventDefault();

            window.loadPaymentsPage();
            return;
        }

        // CLOSE BUTTONS
        const closeButton = target.closest(
            ".payment-modal-close, .payment-cancel"
        );

        if (closeButton) {
            event.preventDefault();

            closeModal(closeButton.closest(".payment-modal"));
            return;
        }

        // CLICK MODAL BACKGROUND
        if (target.classList.contains("payment-modal")) {
            closeModal(target);
        }
    });

    // -----------------------------------------------------
    // SEARCH FILTER
    // -----------------------------------------------------

    document.addEventListener("input", function (event) {
        if (event.target.id === "paymentSearch") {
            window.filterPaymentsTable();
        }
    });

    // -----------------------------------------------------
    // DROPDOWN FILTERS
    // -----------------------------------------------------

    document.addEventListener("change", function (event) {
        if (
            event.target.id === "paymentStatusFilter" ||
            event.target.id === "paymentMethodFilter"
        ) {
            window.filterPaymentsTable();
        }
    });

    // -----------------------------------------------------
    // APPROVE / REJECT FORM CONFIRMATION
    // -----------------------------------------------------

    document.addEventListener("submit", function (event) {
        if (event.target.id !== "paymentDecisionForm") {
            return;
        }

        const form = event.target;
        const paymentId = getElement("decisionPaymentId");

        const action = form.action || "";

        if (
            !paymentId ||
            !paymentId.value ||
            !action
        ) {
            event.preventDefault();

            alert("Payment information is missing.");
            return;
        }

        const isApprove = action.includes("/approve");

        const message = isApprove
            ? "Have you independently verified this transaction in the official merchant account?"
            : "Are you sure you want to reject this payment?";

        if (!window.confirm(message)) {
            event.preventDefault();
            return;
        }

        const submitButton = getElement("decisionSubmitButton");

        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = "Processing...";
        }
    });

    // -----------------------------------------------------
    // INITIALIZE FILTERS
    // -----------------------------------------------------

    window.initializePaymentPage = function () {
        window.filterPaymentsTable();
    };

    // Observe AJAX page insertion so the initial payment count
    // is updated when the payment table appears.
    let lastPaymentTable = null;

    const observer = new MutationObserver(function () {
        const currentTable = getElement("paymentsTable");

        if (currentTable && currentTable !== lastPaymentTable) {
            lastPaymentTable = currentTable;
            window.initializePaymentPage();
        }

        if (!currentTable) {
            lastPaymentTable = null;
        }
    });

    if (document.body) {
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    // Initialize if the payment page is already visible.
    window.initializePaymentPage();

})();
