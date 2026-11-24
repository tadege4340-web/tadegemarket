
(function () {
    "use strict";

    // Prevent duplicate heartbeat timers.
    if (window.shopNovaPresenceInitialized) {
        return;
    }

    window.shopNovaPresenceInitialized = true;

    const HEARTBEAT_URL = "/api/heartbeat";
    const HEARTBEAT_INTERVAL = 30000;

    let heartbeatTimer = null;
    let requestInProgress = false;

    async function sendHeartbeat() {
        if (requestInProgress || document.visibilityState === "hidden") {
            return;
        }

        requestInProgress = true;

        try {
            const response = await fetch(HEARTBEAT_URL, {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest"
                }
            });

            if (response.status === 401 || response.status === 403) {
                stopHeartbeat();
                return;
            }

            if (!response.ok) {
                console.warn("ShopNova heartbeat failed:", response.status);
                return;
            }

            const data = await response.json();

            if (data.success) {
                window.dispatchEvent(
                    new CustomEvent("shopnova:presence-updated", {
                        detail: data
                    })
                );
            }

        } catch (error) {
            console.warn("ShopNova heartbeat error:", error);
        } finally {
            requestInProgress = false;
        }
    }

    function startHeartbeat() {
        if (heartbeatTimer !== null) {
            return;
        }

        sendHeartbeat();

        heartbeatTimer = window.setInterval(
            sendHeartbeat,
            HEARTBEAT_INTERVAL
        );
    }

    function stopHeartbeat() {
        if (heartbeatTimer !== null) {
            window.clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible") {
            sendHeartbeat();
        }
    });

    window.addEventListener("online", sendHeartbeat);

    window.addEventListener("pagehide", function () {
        stopHeartbeat();
    });

    startHeartbeat();

})();
