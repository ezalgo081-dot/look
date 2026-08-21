/**
 * Speed Meter widget -- shows data refresh latency and connection status.
 * Displayed in the header bar.
 */

const SpeedMeter = (function () {
    "use strict";

    let dotEl = null;
    let textEl = null;
    let status = "disconnected";

    function init() {
        dotEl = document.getElementById("speedDot");
        textEl = document.getElementById("speedText");
    }

    function setStatus(s) {
        status = s;
        if (!dotEl || !textEl) init();
        if (!dotEl) return;

        dotEl.className = "dot";
        switch (s) {
            case "connected":
                textEl.textContent = "Connected";
                break;
            case "connecting":
                dotEl.classList.add("slow");
                textEl.textContent = "Connecting...";
                break;
            case "disconnected":
                dotEl.classList.add("offline");
                textEl.textContent = "Disconnected";
                break;
        }
    }

    function update(latencyMs, updateCount) {
        if (!dotEl || !textEl) init();
        if (!dotEl) return;

        dotEl.className = "dot";
        if (latencyMs > 2000) {
            dotEl.classList.add("slow");
        } else if (latencyMs > 5000) {
            dotEl.classList.add("offline");
        }

        textEl.textContent =
            "Data Age: " + Math.round(latencyMs) + "ms | Updates: " + updateCount;
    }

    document.addEventListener("DOMContentLoaded", init);

    return { setStatus, update };
})();
