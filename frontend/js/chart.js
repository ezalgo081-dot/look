/**
 * Spot price chart with 6 Magical SPOT Lines + key levels (Max Pain, Max Gain, S-Reversal).
 * Stock-market-style rendering with labeled horizontal price lines.
 */

const SpotChart = (function () {
    "use strict";

    const MAX_POINTS = 600;
    const points = [];
    let levels = {};       // { maxPain, maxGain, risky, magicalLines, sReversalCe, sReversalPe, spot }
    let canvas = null;
    let ctx = null;
    let containerEl = null;
    let dpr = 1;

    function init() {
        containerEl = document.getElementById("spotChart");
        if (!containerEl) return;

        canvas = document.createElement("canvas");
        canvas.style.width = "100%";
        canvas.style.height = "100%";
        containerEl.appendChild(canvas);

        window.addEventListener("resize", resize);
        resize();
    }

    function resize() {
        if (!canvas || !containerEl) return;
        const rect = containerEl.getBoundingClientRect();
        dpr = window.devicePixelRatio || 1;
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        canvas.style.width = rect.width + "px";
        canvas.style.height = rect.height + "px";
        ctx = canvas.getContext("2d");
        ctx.scale(dpr, dpr);
        draw();
    }

    function addPoint(price, label, newLevels) {
        points.push({ price, label, time: Date.now() });
        if (points.length > MAX_POINTS) points.shift();
        if (newLevels) levels = newLevels;
        draw();
    }

    function draw() {
        if (!ctx || !canvas || !containerEl) return;

        const rect = containerEl.getBoundingClientRect();
        const W = rect.width;
        const H = rect.height;
        const PAD = { top: 12, right: 80, bottom: 24, left: 8 };

        ctx.clearRect(0, 0, W, H);

        // Background
        ctx.fillStyle = "#0d0d1a";
        ctx.fillRect(0, 0, W, H);

        if (points.length < 2) {
            ctx.fillStyle = "#555";
            ctx.font = "12px Inter, sans-serif";
            ctx.textAlign = "center";
            ctx.fillText("Waiting for price data...", W / 2, H / 2);
            return;
        }

        // Collect all price levels for Y-axis range
        const prices = points.map(p => p.price);
        const allLevels = [...prices];
        const ml = levels.magicalLines || [];
        allLevels.push(...ml);
        if (levels.maxPain) allLevels.push(levels.maxPain);
        if (levels.maxGain) allLevels.push(levels.maxGain);

        let minP = Math.min(...allLevels);
        let maxP = Math.max(...allLevels);
        const range = maxP - minP || 100;
        minP -= range * 0.06;
        maxP += range * 0.06;

        const chartW = W - PAD.left - PAD.right;
        const chartH = H - PAD.top - PAD.bottom;

        const toX = (i) => PAD.left + (i / (points.length - 1)) * chartW;
        const toY = (p) => PAD.top + (1 - (p - minP) / (maxP - minP)) * chartH;

        // Y-axis grid
        ctx.strokeStyle = "rgba(42,42,74,0.3)";
        ctx.lineWidth = 0.5;
        const gridSteps = 6;
        for (let i = 0; i <= gridSteps; i++) {
            const y = PAD.top + (i / gridSteps) * chartH;
            ctx.beginPath();
            ctx.moveTo(PAD.left, y);
            ctx.lineTo(W - PAD.right, y);
            ctx.stroke();

            const val = maxP - (i / gridSteps) * (maxP - minP);
            ctx.fillStyle = "#555";
            ctx.font = "9px JetBrains Mono, monospace";
            ctx.textAlign = "left";
            ctx.fillText(val.toFixed(0), W - PAD.right + 4, y + 3);
        }

        // Time axis labels
        ctx.fillStyle = "#444";
        ctx.font = "9px JetBrains Mono, monospace";
        ctx.textAlign = "center";
        const timeSteps = Math.min(6, points.length - 1);
        for (let i = 0; i <= timeSteps; i++) {
            const idx = Math.floor((i / timeSteps) * (points.length - 1));
            const x = toX(idx);
            const d = new Date(points[idx].time);
            const timeStr = d.getHours().toString().padStart(2, "0") + ":" + d.getMinutes().toString().padStart(2, "0");
            ctx.fillText(timeStr, x, H - 4);
        }

        // --- Draw horizontal level lines (the key feature from the screenshot) ---
        const currentSpot = points[points.length - 1].price;

        function drawLevelLine(price, label, color, dashPattern) {
            if (!price || price === 0) return;
            const y = toY(price);
            if (y < PAD.top - 5 || y > H - PAD.bottom + 5) return;

            // Dashed line
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.2;
            ctx.setLineDash(dashPattern || [6, 4]);
            ctx.beginPath();
            ctx.moveTo(PAD.left, y);
            ctx.lineTo(W - PAD.right, y);
            ctx.stroke();
            ctx.setLineDash([]);

            // Label tag on the right
            const tagW = PAD.right - 6;
            const tagH = 16;
            const tagX = W - PAD.right + 2;
            const tagY = y - tagH / 2;

            // Tag background
            ctx.fillStyle = color;
            ctx.beginPath();
            roundRect(ctx, tagX, tagY, tagW, tagH, 3);
            ctx.fill();

            // Tag text
            ctx.fillStyle = "#fff";
            ctx.font = "bold 9px JetBrains Mono, monospace";
            ctx.textAlign = "left";
            ctx.fillText(label, tagX + 3, y + 3);

            // Price below the label
            ctx.fillStyle = color;
            ctx.font = "8px JetBrains Mono, monospace";
            ctx.fillText(price.toFixed(2), tagX + 3, y + tagH / 2 + 10);
        }

        function roundRect(ctx, x, y, w, h, r) {
            ctx.moveTo(x + r, y);
            ctx.lineTo(x + w - r, y);
            ctx.arcTo(x + w, y, x + w, y + r, r);
            ctx.lineTo(x + w, y + h - r);
            ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
            ctx.lineTo(x + r, y + h);
            ctx.arcTo(x, y + h, x, y + h - r, r);
            ctx.lineTo(x, y + r);
            ctx.arcTo(x, y, x + r, y, r);
        }

        // Max Pain (red dashed)
        drawLevelLine(levels.maxPain, "Max Pain", "#e53935", [8, 4]);

        // Max Gain (green dashed)
        drawLevelLine(levels.maxGain, "S Max Gain", "#43a047", [8, 4]);

        // Risky level (solid red-orange)
        if (levels.risky && levels.risky !== "Safe") {
            // Use max pain + offset as "R Risky" line
            const riskyLevel = levels.maxPain ? levels.maxPain + (levels.maxPain - currentSpot) * 0.3 : 0;
            if (riskyLevel > 0) {
                drawLevelLine(riskyLevel, "R Risky", "#ff7043", []);
            }
        }

        // Magical SPOT Lines
        const sortedML = [...ml].sort((a, b) => b - a);
        for (let i = 0; i < sortedML.length; i++) {
            const level = sortedML[i];
            const isSupport = level < currentSpot;
            const color = isSupport ? "#66bb6a" : "#ef5350";
            const label = isSupport ? "S " + (i + 1) : "R " + (i + 1);
            drawLevelLine(level, label, color, [4, 3]);
        }

        // --- Price line (smooth) ---
        ctx.beginPath();
        ctx.strokeStyle = "#26c6da";
        ctx.lineWidth = 2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        for (let i = 0; i < points.length; i++) {
            const x = toX(i);
            const y = toY(points[i].price);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Gradient fill
        const gradient = ctx.createLinearGradient(0, PAD.top, 0, H - PAD.bottom);
        gradient.addColorStop(0, "rgba(38,198,218,0.18)");
        gradient.addColorStop(1, "rgba(38,198,218,0)");

        ctx.beginPath();
        for (let i = 0; i < points.length; i++) {
            const x = toX(i);
            const y = toY(points[i].price);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.lineTo(toX(points.length - 1), H - PAD.bottom);
        ctx.lineTo(toX(0), H - PAD.bottom);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Current price marker + horizontal line
        const lastX = toX(points.length - 1);
        const lastY = toY(currentSpot);

        // Horizontal dotted line from current price to right edge
        ctx.strokeStyle = "rgba(38,198,218,0.5)";
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 2]);
        ctx.beginPath();
        ctx.moveTo(lastX, lastY);
        ctx.lineTo(W - PAD.right, lastY);
        ctx.stroke();
        ctx.setLineDash([]);

        // Current price dot
        ctx.beginPath();
        ctx.arc(lastX, lastY, 5, 0, Math.PI * 2);
        ctx.fillStyle = "#26c6da";
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Current price tag
        const cpTagW = PAD.right - 6;
        const cpTagH = 18;
        const cpTagX = W - PAD.right + 2;
        const cpTagY = lastY - cpTagH / 2;
        ctx.fillStyle = "#26c6da";
        ctx.beginPath();
        roundRect(ctx, cpTagX, cpTagY, cpTagW, cpTagH, 3);
        ctx.fill();
        ctx.fillStyle = "#000";
        ctx.font = "bold 10px JetBrains Mono, monospace";
        ctx.textAlign = "left";
        ctx.fillText(currentSpot.toFixed(2), cpTagX + 3, lastY + 4);
    }

    document.addEventListener("DOMContentLoaded", init);

    return { addPoint };
})();
