/**
 * Main application logic: WebSocket connection, data rendering, table updates.
 */

(function () {
    "use strict";

    const WS_URL = `ws://${location.host}/ws`;
    let ws = null;
    let reconnectTimer = null;
    let updateCount = 0;
    let _pendingData = null;
    let _rafScheduled = false;

    // ── DOM references ──────────────────────────────────────────────────
    const $id = (id) => document.getElementById(id);

    const dom = {
        symbolBadge:    $id("symbolBadge"),
        spotVal:        $id("spotVal"),
        futureVal:      $id("futureVal"),
        maxPainVal:     $id("maxPainVal"),
        maxGainVal:     $id("maxGainVal"),
        lotVal:         $id("lotVal"),
        pcrVal:         $id("pcrVal"),
        expiryVal:      $id("expiryVal"),
        riskyBadge:     $id("riskyBadge"),
        speedDot:       $id("speedDot"),
        speedText:      $id("speedText"),
        waitingOverlay: $id("waitingOverlay"),
        chainTable:     $id("chainTable"),
        chainBody:      $id("chainBody"),
        totalCeOi:      $id("totalCeOi"),
        totalPeOi:      $id("totalPeOi"),
        pcrCard:        $id("pcrCard"),
        atmCard:        $id("atmCard"),
        timestampStatus:$id("timestampStatus"),
        fetchLatency:   $id("fetchLatency"),
        processLatency: $id("processLatency"),
        strikeSelect1:  $id("strikeSelect1"),
        strikeSelect2:  $id("strikeSelect2"),
        strikeSelect3:  $id("strikeSelect3"),
        btnStart:       $id("btnStart"),
        btnStop:        $id("btnStop"),
        btnClear:       $id("btnClear"),
        strikeAnalysisBody: $id("strikeAnalysisBody"),
        strikeSummaryBody: $id("strikeSummaryBody"),
        symbolSelector: $id("symbolSelector"),
    };

    // ── Symbol Switcher ─────────────────────────────────────────────────
    if (dom.symbolSelector) {
        dom.symbolSelector.addEventListener("change", function () {
            const newSymbol = this.value;
            this.disabled = true;
            fetch("/api/switch-symbol", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ symbol: newSymbol }),
            })
            .then(r => r.json())
            .then(resp => {
                if (resp.status === "ok") {
                    _strikesPopulated = false;
                    _prevStrikeData = {};
                    dom.chainBody.innerHTML = "";
                    dom.waitingOverlay.style.display = "";
                    dom.chainTable.style.display = "none";
                    dom.waitingOverlay.querySelector("#waitingText").textContent = "Switching to " + newSymbol + "...";
                }
                dom.symbolSelector.disabled = false;
            })
            .catch(() => {
                dom.symbolSelector.disabled = false;
            });
        });
    }

    // ── Number formatting ───────────────────────────────────────────────
    const fmtNum = (n) => {
        if (n === undefined || n === null) return "--";
        return Number(n).toLocaleString("en-IN");
    };

    const fmtLakh = (n) => {
        if (!n) return "--";
        if (n >= 10000000) return (n / 10000000).toFixed(2) + " Cr";
        if (n >= 100000) return (n / 100000).toFixed(2) + " L";
        return fmtNum(n);
    };

    const fmtPrice = (n) => {
        if (!n) return "--";
        return Number(n).toFixed(2);
    };

    // ── Render header stats ─────────────────────────────────────────────
    function renderHeader(data) {
        if (dom.symbolSelector && data.symbol && dom.symbolSelector.value !== data.symbol) {
            dom.symbolSelector.value = data.symbol;
        }
        dom.spotVal.textContent = fmtPrice(data.spot_price);
        dom.futureVal.textContent = fmtPrice(data.future_price);
        dom.maxPainVal.textContent = fmtNum(data.max_pain);
        dom.maxGainVal.textContent = fmtNum(data.max_gain);
        dom.lotVal.textContent = data.lot_size || "--";
        dom.pcrVal.textContent = data.pcr ? data.pcr.toFixed(4) : "--";
        dom.expiryVal.textContent = data.expiry || "--";

        const risk = (data.risky || "moderate").toLowerCase();
        dom.riskyBadge.className = "risky-badge " + risk;
        dom.riskyBadge.textContent = data.risky || "MODERATE";

        if (data.pcr > 1.0) {
            dom.pcrVal.className = "stat-value green";
        } else if (data.pcr < 0.8) {
            dom.pcrVal.className = "stat-value red";
        } else {
            dom.pcrVal.className = "stat-value yellow";
        }
    }

    // ── Render option chain table (in-place update for tick-by-tick speed) ──
    // Column order (17 cols):
    //  0: CE OI Chg Diff  1: CE Vol Chg Diff  2: CE Vol Chg  3: CE Volume
    //  4: CE OI Chg  5: CE OI  6: CE LTP  7: CE S-Reversal
    //  8: Strike
    //  9: PE S-Reversal  10: PE LTP  11: PE OI  12: PE OI Chg
    // 13: PE Volume  14: PE Vol Chg  15: PE Vol Chg Diff  16: PE OI Chg Diff
    const COL_COUNT = 17;
    let _prevStrikeData = {};

    function renderTable(strikes, spotPrice) {
        if (!strikes || !strikes.length) return;

        dom.waitingOverlay.style.display = "none";
        dom.chainTable.style.display = "";

        const tbody = dom.chainBody;
        const existingRows = tbody.querySelectorAll("tr");
        const needsRebuild = existingRows.length !== strikes.length;

        if (needsRebuild) {
            const fragment = document.createDocumentFragment();
            for (const s of strikes) {
                const tr = _buildRow(s, spotPrice);
                tr.dataset.strike = s.strike_price;
                fragment.appendChild(tr);
            }
            tbody.innerHTML = "";
            tbody.appendChild(fragment);
        } else {
            for (let i = 0; i < strikes.length; i++) {
                const s = strikes[i];
                const tr = existingRows[i];
                const cells = tr.cells;
                if (!cells || cells.length < COL_COUNT) continue;

                const prev = _prevStrikeData[s.strike_price] || {};
                const isCallITM = s.strike_price < spotPrice;
                const isPutITM = s.strike_price > spotPrice;

                _updateCell(cells[0], fmtNum(s.ce_oi_change_diff), oiClass(s.ce_oi_change_diff), isCallITM);
                _updateCell(cells[1], fmtNum(s.ce_vol_change_diff), oiClass(s.ce_vol_change_diff), isCallITM);
                _updateCell(cells[2], fmtNum(s.ce_vol_change), oiClass(s.ce_vol_change), isCallITM);
                _updateCell(cells[3], fmtLakh(s.ce_volume), s.ce_volume > 50000 ? "vol-high" : "", isCallITM);
                _updateCell(cells[4], fmtNum(s.ce_oi_change), oiClass(s.ce_oi_change), isCallITM);
                _updateCell(cells[5], fmtLakh(s.ce_oi), "", isCallITM);
                _updateCellWithFlash(cells[6], fmtPrice(s.ce_ltp), prev.ce_ltp, s.ce_ltp, isCallITM);
                // Skip S-Reversal (7) and Strike (8) -- rarely change
                _updateCellWithFlash(cells[10], fmtPrice(s.pe_ltp), prev.pe_ltp, s.pe_ltp, isPutITM);
                _updateCell(cells[11], fmtLakh(s.pe_oi), "", isPutITM);
                _updateCell(cells[12], fmtNum(s.pe_oi_change), oiClass(s.pe_oi_change), isPutITM);
                _updateCell(cells[13], fmtLakh(s.pe_volume), s.pe_volume > 50000 ? "vol-high" : "", isPutITM);
                _updateCell(cells[14], fmtNum(s.pe_vol_change), oiClass(s.pe_vol_change), isPutITM);
                _updateCell(cells[15], fmtNum(s.pe_vol_change_diff), oiClass(s.pe_vol_change_diff), isPutITM);
                _updateCell(cells[16], fmtNum(s.pe_oi_change_diff), oiClass(s.pe_oi_change_diff), isPutITM);

                if (s.is_atm) tr.className = "atm-row";
            }
        }

        _prevStrikeData = {};
        for (const s of strikes) {
            _prevStrikeData[s.strike_price] = { ce_ltp: s.ce_ltp, pe_ltp: s.pe_ltp };
        }

        // Populate strike selectors if not yet populated
        _populateStrikeSelectors(strikes);
    }

    function _updateCell(td, text, extraClass, isITM) {
        if (td.textContent !== text) {
            td.textContent = text;
        }
        const cls = (extraClass ? extraClass + " " : "") + (isITM ? "itm-cell" : "");
        if (td.className !== cls) td.className = cls;
    }

    function _updateCellWithFlash(td, text, prevVal, newVal, isITM) {
        if (td.textContent !== text) {
            td.textContent = text;
            if (prevVal !== undefined && prevVal !== newVal && newVal > 0) {
                td.classList.remove("tick-up", "tick-down");
                void td.offsetWidth;
                td.classList.add(newVal > prevVal ? "tick-up" : "tick-down");
            }
        }
        if (isITM && !td.classList.contains("itm-cell")) td.classList.add("itm-cell");
    }

    function _buildRow(s, spotPrice) {
        const tr = document.createElement("tr");
        if (s.is_atm) tr.className = "atm-row";
        const isCallITM = s.strike_price < spotPrice;
        const isPutITM = s.strike_price > spotPrice;

        // CE side (8 cols)
        tr.appendChild(makeCell(fmtNum(s.ce_oi_change_diff), oiClass(s.ce_oi_change_diff), isCallITM));
        tr.appendChild(makeCell(fmtNum(s.ce_vol_change_diff || 0), oiClass(s.ce_vol_change_diff || 0), isCallITM));
        tr.appendChild(makeCell(fmtNum(s.ce_vol_change || 0), oiClass(s.ce_vol_change || 0), isCallITM));
        tr.appendChild(makeCell(fmtLakh(s.ce_volume), s.ce_volume > 50000 ? "vol-high" : "", isCallITM));
        tr.appendChild(makeCell(fmtNum(s.ce_oi_change), oiClass(s.ce_oi_change), isCallITM));
        tr.appendChild(makeCell(fmtLakh(s.ce_oi), "", isCallITM));
        tr.appendChild(makeCell(fmtPrice(s.ce_ltp), "", isCallITM));
        tr.appendChild(makeReversalCell(s.s_reversal_ce, s.s_reversal_ce_value));

        // Strike (1 col)
        tr.appendChild(makeStrikeCell(s));

        // PE side (8 cols)
        tr.appendChild(makeReversalCell(s.s_reversal_pe, s.s_reversal_pe_value));
        tr.appendChild(makeCell(fmtPrice(s.pe_ltp), "", isPutITM));
        tr.appendChild(makeCell(fmtLakh(s.pe_oi), "", isPutITM));
        tr.appendChild(makeCell(fmtNum(s.pe_oi_change), oiClass(s.pe_oi_change), isPutITM));
        tr.appendChild(makeCell(fmtLakh(s.pe_volume), s.pe_volume > 50000 ? "vol-high" : "", isPutITM));
        tr.appendChild(makeCell(fmtNum(s.pe_vol_change || 0), oiClass(s.pe_vol_change || 0), isPutITM));
        tr.appendChild(makeCell(fmtNum(s.pe_vol_change_diff || 0), oiClass(s.pe_vol_change_diff || 0), isPutITM));
        tr.appendChild(makeCell(fmtNum(s.pe_oi_change_diff), oiClass(s.pe_oi_change_diff), isPutITM));

        return tr;
    }

    function makeCell(text, extraClass, isITM) {
        const td = document.createElement("td");
        td.textContent = text;
        if (extraClass) td.className = extraClass;
        if (isITM) td.classList.add("itm-cell");
        return td;
    }

    function oiClass(val) {
        if (val > 0) return "oi-positive";
        if (val < 0) return "oi-negative";
        return "";
    }

    function makeReversalCell(label, value) {
        const td = document.createElement("td");
        td.className = "s-reversal";
        if (label === "Break Even") {
            td.classList.add("break-even");
            td.textContent = "Break Even";
        } else if (label === "Break Down") {
            td.classList.add("break-down");
            td.textContent = "Break Down";
        } else if (value) {
            td.textContent = fmtPrice(value);
        }
        return td;
    }

    function makeStrikeCell(s) {
        const td = document.createElement("td");
        td.className = "strike-col";

        const strikeSpan = document.createElement("div");
        strikeSpan.textContent = fmtNum(s.strike_price);
        strikeSpan.style.marginBottom = "2px";
        td.appendChild(strikeSpan);

        const container = document.createElement("div");
        container.className = "wtt-bar-container";

        const pct = s.wtt_wtb_pct || 50;
        const isBearish = pct > 50;

        const pctSpan = document.createElement("span");
        pctSpan.className = "wtt-pct " + (isBearish ? "bearish" : "bullish");
        pctSpan.textContent = pct.toFixed(1) + "%";

        const bar = document.createElement("div");
        bar.className = "wtt-bar";
        const fill = document.createElement("div");
        fill.className = "wtt-bar-fill " + (isBearish ? "bearish" : "bullish");
        fill.style.width = pct + "%";
        bar.appendChild(fill);

        container.appendChild(pctSpan);
        container.appendChild(bar);
        td.appendChild(container);

        return td;
    }

    // ── Strike Selector Logic ───────────────────────────────────────────
    let _strikesPopulated = false;

    function _populateStrikeSelectors(strikes) {
        if (_strikesPopulated) return;
        if (!strikes || !strikes.length) return;
        _strikesPopulated = true;

        const selects = [dom.strikeSelect1, dom.strikeSelect2, dom.strikeSelect3];
        const strikePrices = strikes.map(s => s.strike_price);
        const atmIdx = strikes.findIndex(s => s.is_atm);

        for (let si = 0; si < selects.length; si++) {
            const sel = selects[si];
            sel.innerHTML = "";
            for (const sp of strikePrices) {
                const opt = document.createElement("option");
                opt.value = sp;
                opt.textContent = fmtNum(sp);
                sel.appendChild(opt);
            }
            // Auto-select ATM-1, ATM, ATM+1
            const defaultIdx = Math.max(0, Math.min(strikePrices.length - 1, (atmIdx >= 0 ? atmIdx : Math.floor(strikePrices.length / 2)) + si - 1));
            sel.selectedIndex = defaultIdx;
        }
    }

    // ── Tracking State (START / STOP / CLEAR) ───────────────────────────
    let _tracking = false;
    let _trackingHistory = [];  // [{time, strike, ce_oi_chg, pe_oi_chg, diff, diff_of_diff, reversal}]
    let _prevTrackingDiffs = {}; // strike -> previous diff value for diff-of-diff

    function getSelectedStrikes() {
        return [
            parseFloat(dom.strikeSelect1.value),
            parseFloat(dom.strikeSelect2.value),
            parseFloat(dom.strikeSelect3.value),
        ].filter(v => !isNaN(v));
    }

    dom.btnStart.addEventListener("click", function () {
        _tracking = true;
        dom.btnStart.disabled = true;
        dom.btnStop.disabled = false;
        dom.strikeSelect1.disabled = true;
        dom.strikeSelect2.disabled = true;
        dom.strikeSelect3.disabled = true;
    });

    dom.btnStop.addEventListener("click", function () {
        _tracking = false;
        dom.btnStart.disabled = false;
        dom.btnStop.disabled = true;
        dom.strikeSelect1.disabled = false;
        dom.strikeSelect2.disabled = false;
        dom.strikeSelect3.disabled = false;
    });

    dom.btnClear.addEventListener("click", function () {
        _tracking = false;
        _trackingHistory = [];
        _prevTrackingDiffs = {};
        _prevHistoryTotalDiff = undefined;
        dom.btnStart.disabled = false;
        dom.btnStop.disabled = true;
        dom.strikeSelect1.disabled = false;
        dom.strikeSelect2.disabled = false;
        dom.strikeSelect3.disabled = false;
        dom.strikeSummaryBody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#555;">Select strikes &amp; press START</td></tr>';
        dom.strikeAnalysisBody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#555;">Tracking will start on START</td></tr>';
    });

    let _prevHistoryTotalDiff = undefined;

    function recordStrikeAnalysis(data) {
        if (!_tracking) return;
        if (!data.strikes || !data.strikes.length) return;

        const selectedStrikes = getSelectedStrikes();
        if (!selectedStrikes.length) return;

        const timeStr = data.timestamp ? data.timestamp.split(" ").pop() || data.timestamp : new Date().toLocaleTimeString("en-IN");

        // --- 1. Live Summary Table (like client's Excel) ---
        renderStrikeSummary(data, selectedStrikes, timeStr);

        // --- 2. Time-based history (totals across selected strikes) ---
        let totalCe = 0, totalPe = 0;
        for (const sp of selectedStrikes) {
            const strike = data.strikes.find(s => s.strike_price === sp);
            if (!strike) continue;
            totalCe += strike.ce_oi_change || 0;
            totalPe += strike.pe_oi_change || 0;
        }
        const totalDiff = totalCe - totalPe;
        const diffOfDiff = _prevHistoryTotalDiff !== undefined ? totalDiff - _prevHistoryTotalDiff : 0;
        _prevHistoryTotalDiff = totalDiff;

        _trackingHistory.push({
            time: timeStr,
            total_ce: totalCe,
            total_pe: totalPe,
            diff: totalDiff,
            diff_of_diff: diffOfDiff,
        });

        if (_trackingHistory.length > 200) {
            _trackingHistory = _trackingHistory.slice(-200);
        }

        renderHistoryLog();
    }

    function renderStrikeSummary(data, selectedStrikes, timeStr) {
        const tbody = dom.strikeSummaryBody;
        const fragment = document.createDocumentFragment();
        let totalCe = 0, totalPe = 0;

        for (const sp of selectedStrikes) {
            const strike = data.strikes.find(s => s.strike_price === sp);
            if (!strike) continue;

            const ceChg = strike.ce_oi_change || 0;
            const peChg = strike.pe_oi_change || 0;
            const diff = ceChg - peChg;
            totalCe += ceChg;
            totalPe += peChg;

            const tr = document.createElement("tr");

            const tdStrike = document.createElement("td");
            tdStrike.textContent = fmtNum(sp);
            tdStrike.className = "strike-col-mini";
            tr.appendChild(tdStrike);

            const tdCe = document.createElement("td");
            tdCe.textContent = fmtNum(ceChg);
            tdCe.className = ceChg < 0 ? "oi-negative" : "oi-positive";
            tr.appendChild(tdCe);

            const tdPe = document.createElement("td");
            tdPe.textContent = fmtNum(peChg);
            tdPe.className = peChg < 0 ? "oi-negative" : "oi-positive";
            tr.appendChild(tdPe);

            const tdDiff = document.createElement("td");
            tdDiff.textContent = fmtNum(diff);
            tr.appendChild(tdDiff);

            const tdSignal = document.createElement("td");
            if (peChg > ceChg) {
                tdSignal.textContent = "Support";
                tdSignal.className = "signal-support";
            } else if (ceChg > peChg) {
                tdSignal.textContent = "Resistance";
                tdSignal.className = "signal-resistance";
            } else {
                tdSignal.textContent = "Neutral";
                tdSignal.className = "signal-neutral";
            }
            tr.appendChild(tdSignal);

            fragment.appendChild(tr);
        }

        // Totals row
        const totalDiff = totalCe - totalPe;
        const trTotal = document.createElement("tr");
        trTotal.className = "summary-total-row";

        const tdTime = document.createElement("td");
        tdTime.textContent = timeStr;
        tdTime.className = "strike-col-mini";
        trTotal.appendChild(tdTime);

        const tdTotalCe = document.createElement("td");
        tdTotalCe.textContent = fmtNum(totalCe);
        tdTotalCe.className = totalCe < 0 ? "oi-negative" : "oi-positive";
        trTotal.appendChild(tdTotalCe);

        const tdTotalPe = document.createElement("td");
        tdTotalPe.textContent = fmtNum(totalPe);
        tdTotalPe.className = totalPe < 0 ? "oi-negative" : "oi-positive";
        trTotal.appendChild(tdTotalPe);

        const tdTotalDiff = document.createElement("td");
        tdTotalDiff.textContent = fmtNum(totalDiff);
        trTotal.appendChild(tdTotalDiff);

        const tdTotalSignal = document.createElement("td");
        tdTotalSignal.textContent = fmtNum(totalDiff);
        trTotal.appendChild(tdTotalSignal);

        fragment.appendChild(trTotal);

        tbody.innerHTML = "";
        tbody.appendChild(fragment);
    }

    function renderHistoryLog() {
        const tbody = dom.strikeAnalysisBody;
        if (!_trackingHistory.length) return;

        const fragment = document.createDocumentFragment();
        const recent = _trackingHistory.slice(-30).reverse();

        for (const entry of recent) {
            const tr = document.createElement("tr");

            const tdTime = document.createElement("td");
            tdTime.textContent = entry.time;
            tdTime.style.color = "var(--text-secondary)";
            tr.appendChild(tdTime);

            const tdCe = document.createElement("td");
            tdCe.textContent = fmtNum(entry.total_ce);
            tdCe.className = oiClass(entry.total_ce);
            tr.appendChild(tdCe);

            const tdPe = document.createElement("td");
            tdPe.textContent = fmtNum(entry.total_pe);
            tdPe.className = oiClass(entry.total_pe);
            tr.appendChild(tdPe);

            const tdDiff = document.createElement("td");
            tdDiff.textContent = fmtNum(entry.diff);
            tdDiff.className = oiClass(entry.diff);
            tr.appendChild(tdDiff);

            const tdDiffDiff = document.createElement("td");
            tdDiffDiff.textContent = fmtNum(entry.diff_of_diff);
            tdDiffDiff.className = oiClass(entry.diff_of_diff);
            tr.appendChild(tdDiffDiff);

            fragment.appendChild(tr);
        }

        tbody.innerHTML = "";
        tbody.appendChild(fragment);
    }

    // ── Render OI Summary ───────────────────────────────────────────────
    function renderOISummary(data) {
        dom.totalCeOi.textContent = fmtLakh(data.total_ce_oi);
        dom.totalPeOi.textContent = fmtLakh(data.total_pe_oi);
        dom.pcrCard.textContent = data.pcr ? data.pcr.toFixed(4) : "--";
        dom.atmCard.textContent = fmtNum(data.atm_strike);
    }

    // ── Render status bar ───────────────────────────────────────────────
    function renderStatus(data) {
        dom.timestampStatus.textContent = data.timestamp || "--";
        dom.fetchLatency.textContent = data.fetch_latency_ms || "--";
        dom.processLatency.textContent = data.process_latency_ms || "--";

        const srcEl = document.getElementById("dataSourceStatus");
        if (srcEl && _dataSource) {
            const label = _dataSource === "upstox" ? "UPSTOX REAL-TIME" :
                          _dataSource === "browser" ? "NSE (Chrome)" : "NSE (HTTP)";
            const badge = _dataSource === "upstox" ? "upstox" : "nse";
            srcEl.innerHTML = `Option Chain Dashboard &middot; COA 1.0 <span class="data-source-badge ${badge}">${label}</span>`;
        }
    }

    // ── Data source tracking ────────────────────────────────────────────
    let _dataSource = "unknown";

    // ── Main data handler ───────────────────────────────────────────────
    let _latestData = null;

    function onData(data) {
        updateCount++;
        _latestData = data;

        if (_dataSource === "upstox") {
            const banner = document.getElementById("upstoxBanner");
            if (banner) banner.style.display = "none";
        }

        renderHeader(data);
        renderTable(data.strikes, data.spot_price);
        renderOISummary(data);
        recordStrikeAnalysis(data);
        renderStatus(data);
        
        // Update def of def table if modal is open
        if ($id("defOfDefModal").style.display === "flex") {
            renderDefOfDefTable(data);
        }

        const totalLatency = (data.fetch_latency_ms || 0) + (data.process_latency_ms || 0);
        SpeedMeter.update(totalLatency, updateCount);

        if (data.spot_price && data.timestamp) {
            SpotChart.addPoint(data.spot_price, data.timestamp, {
                maxPain: data.max_pain,
                maxGain: data.max_gain,
                risky: data.risky,
                spot: data.spot_price,
            });
        }
    }

    // ── WebSocket connection ────────────────────────────────────────────
    function connect() {
        if (ws && ws.readyState <= 1) return;

        SpeedMeter.setStatus("connecting");
        ws = new WebSocket(WS_URL);

        ws.onopen = () => {
            SpeedMeter.setStatus("connected");
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                _pendingData = data;
                if (!_rafScheduled) {
                    _rafScheduled = true;
                    requestAnimationFrame(() => {
                        _rafScheduled = false;
                        if (_pendingData) {
                            onData(_pendingData);
                        }
                    });
                }
            } catch (e) {
                console.error("Parse error:", e);
            }
        };

        ws.onclose = () => {
            SpeedMeter.setStatus("disconnected");
            scheduleReconnect();
        };

        ws.onerror = () => {
            SpeedMeter.setStatus("disconnected");
        };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, 3000);
    }

    // ── Sidebar navigation ─────────────────────────────────────────────
    function setActiveNav(el) {
        document.querySelectorAll(".sidebar-icon").forEach(i => i.classList.remove("active"));
        el.classList.add("active");
    }

    $id("navDashboard").addEventListener("click", function () {
        setActiveNav(this);
        window.scrollTo({ top: 0, behavior: "smooth" });
    });

    $id("navExport").addEventListener("click", function () {
        setActiveNav(this);
        $id("exportModal").style.display = "flex";
    });

    $id("exportClose").addEventListener("click", function () {
        $id("exportModal").style.display = "none";
        setActiveNav($id("navDashboard"));
    });

    $id("exportModal").addEventListener("click", function (e) {
        if (e.target === this) {
            this.style.display = "none";
            setActiveNav($id("navDashboard"));
        }
    });

    function downloadFile(content, filename, mime) {
        const blob = new Blob([content], { type: mime });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    }

    $id("downloadCSV").addEventListener("click", function () {
        if (!_latestData || !_latestData.strikes || !_latestData.strikes.length) {
            alert("No data available yet. Wait for the first data update.");
            return;
        }
        const d = _latestData;
        const header = [
            "Strike", "CE_LTP", "CE_OI", "CE_OI_Change", "CE_Volume", "CE_IV",
            "CE_S_Reversal", "WTT_WTB_Pct", "CE_Vol_Change", "CE_Vol_Change_Diff",
            "PE_LTP", "PE_OI", "PE_OI_Change", "PE_Volume", "PE_IV",
            "PE_S_Reversal", "CE_OI_Change_Diff", "PE_OI_Change_Diff",
            "PE_Vol_Change", "PE_Vol_Change_Diff", "Is_ATM"
        ];
        const rows = [header.join(",")];
        for (const s of d.strikes) {
            rows.push([
                s.strike_price, s.ce_ltp, s.ce_oi, s.ce_oi_change, s.ce_volume, s.ce_iv,
                '"' + (s.s_reversal_ce || "") + '"', s.wtt_wtb_pct,
                s.ce_vol_change || 0, s.ce_vol_change_diff || 0,
                s.pe_ltp, s.pe_oi, s.pe_oi_change, s.pe_volume, s.pe_iv,
                '"' + (s.s_reversal_pe || "") + '"', s.ce_oi_change_diff, s.pe_oi_change_diff,
                s.pe_vol_change || 0, s.pe_vol_change_diff || 0, s.is_atm
            ].join(","));
        }
        const meta = [
            "", "# Metadata",
            "# Symbol," + d.symbol,
            "# Spot," + d.spot_price,
            "# Future," + d.future_price,
            "# Max Pain," + d.max_pain,
            "# Max Gain," + d.max_gain,
            "# PCR," + d.pcr,
            "# ATM Strike," + d.atm_strike,
            "# Expiry," + d.expiry,
            "# Timestamp," + d.timestamp,
        ];
        const ts = new Date().toISOString().slice(0, 19).replace(/[:-]/g, "");
        downloadFile(rows.join("\n") + "\n" + meta.join("\n"), d.symbol + "_option_chain_" + ts + ".csv", "text/csv");
        $id("exportModal").style.display = "none";
        setActiveNav($id("navDashboard"));
    });

    $id("downloadJSON").addEventListener("click", function () {
        if (!_latestData) {
            alert("No data available yet. Wait for the first data update.");
            return;
        }
        const ts = new Date().toISOString().slice(0, 19).replace(/[:-]/g, "");
        downloadFile(JSON.stringify(_latestData, null, 2), _latestData.symbol + "_option_chain_" + ts + ".json", "application/json");
        $id("exportModal").style.display = "none";
        setActiveNav($id("navDashboard"));
    });

    $id("navFullscreen").addEventListener("click", function () {
        setActiveNav(this);
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(() => {});
        } else {
            document.exitFullscreen().catch(() => {});
        }
    });

    $id("navSettings").addEventListener("click", function () {
        setActiveNav(this);
        $id("settingsModal").style.display = "flex";
        fetch("/api/config")
            .then(r => r.json())
            .then(cfg => {
                $id("settingSymbol").textContent = cfg.symbol || "--";
                $id("settingInterval").textContent = cfg.poll_interval + "s";
                $id("settingStrikes").textContent = cfg.strikes_shown + " strikes";
                $id("settingServer").textContent = cfg.server || location.host;

                const fetcherNames = {
                    upstox: "Upstox API (Real-time, Zero Delay)",
                    browser: "NSE India (Headless Chrome, ~30s delay)",
                    http: "NSE India (HTTP, may need cookies)",
                };
                $id("settingFetcher").textContent = fetcherNames[cfg.fetcher] || cfg.fetcher;

                const upstoxRow = $id("upstoxStatusRow");
                const upstoxVal = $id("settingUpstox");
                if (cfg.upstox_configured) {
                    upstoxRow.style.display = "";
                    upstoxVal.textContent = cfg.upstox_authenticated ? "Connected" : "Login Required";
                    upstoxVal.style.color = cfg.upstox_authenticated ? "var(--green)" : "var(--yellow)";
                }
            })
            .catch(() => {
                $id("settingServer").textContent = location.host;
            });
    });

    $id("settingsClose").addEventListener("click", function () {
        $id("settingsModal").style.display = "none";
        setActiveNav($id("navDashboard"));
    });

    $id("settingsModal").addEventListener("click", function (e) {
        if (e.target === this) {
            this.style.display = "none";
            setActiveNav($id("navDashboard"));
        }
    });

    // ── Def of Def Modal ─────────────────────────────────────────────
    $id("openDefOfDef").addEventListener("click", function () {
        $id("defOfDefModal").style.display = "flex";
        renderDefOfDefTable(_latestData);
    });

    $id("defOfDefClose").addEventListener("click", function () {
        $id("defOfDefModal").style.display = "none";
    });

    $id("defOfDefModal").addEventListener("click", function (e) {
        if (e.target === this) {
            this.style.display = "none";
        }
    });

    function renderDefOfDefTable(data) {
        if (!data || !data.strike_diff_log || !data.strike_diff_log.length) {
            $id("defOfDefBody").innerHTML = '<tr><td colspan="100" style="text-align:center;color:#555;">No data available yet. Wait for updates.</td></tr>';
            return;
        }

        const log = data.strike_diff_log;
        if (!log.length) return;

        // Get all unique strikes from the latest entry
        const latestEntry = log[log.length - 1];
        const strikes = latestEntry.strikes || [];
        const strikePrices = strikes.map(s => s.strike_price).sort((a, b) => a - b);

        // Build header with strike columns
        const thead = $id("defOfDefThead");
        thead.innerHTML = "";
        const headerRow = document.createElement("tr");
        headerRow.appendChild(document.createElement("th")).textContent = "Log Time";
        
        // Add columns for each strike: Call Strike, Put Strike, Call Diff, Put Diff, Difference, Diff of Diff, Reversal, Strike Price
        for (const sp of strikePrices) {
            const strikeHeader = document.createElement("th");
            strikeHeader.colSpan = 8;
            strikeHeader.textContent = fmtNum(sp);
            strikeHeader.style.textAlign = "center";
            strikeHeader.style.borderLeft = "2px solid var(--border)";
            headerRow.appendChild(strikeHeader);
        }
        thead.appendChild(headerRow);

        // Add sub-header row
        const subHeaderRow = document.createElement("tr");
        subHeaderRow.appendChild(document.createElement("th")).textContent = "";
        for (const sp of strikePrices) {
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Call OI Chg";
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Put OI Chg";
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Call Diff";
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Put Diff";
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Difference";
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Diff of Diff";
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Reversal";
            subHeaderRow.appendChild(document.createElement("th")).textContent = "Strike";
        }
        thead.appendChild(subHeaderRow);

        // Build body rows
        const tbody = $id("defOfDefBody");
        tbody.innerHTML = "";
        const recentLog = log.slice(-50).reverse(); // Show last 50 entries, newest first

        for (const entry of recentLog) {
            const tr = document.createElement("tr");
            
            // Time column
            const tdTime = document.createElement("td");
            tdTime.textContent = entry.time;
            tdTime.style.fontWeight = "bold";
            tr.appendChild(tdTime);

            // For each strike, add columns
            for (const sp of strikePrices) {
                const strikeData = entry.strikes.find(s => s.strike_price === sp);
                if (!strikeData) {
                    // Empty cells if strike not found
                    for (let i = 0; i < 8; i++) {
                        tr.appendChild(document.createElement("td"));
                    }
                    continue;
                }

                // Call Strike
                const tdCallStrike = document.createElement("td");
                tdCallStrike.textContent = fmtNum(strikeData.call_oi_change);
                tdCallStrike.className = oiClass(strikeData.call_oi_change);
                tr.appendChild(tdCallStrike);

                // Put Strike
                const tdPutStrike = document.createElement("td");
                tdPutStrike.textContent = fmtNum(strikeData.put_oi_change);
                tdPutStrike.className = oiClass(strikeData.put_oi_change);
                tr.appendChild(tdPutStrike);

                // Call Diff
                const tdCallDiff = document.createElement("td");
                tdCallDiff.textContent = fmtNum(strikeData.call_diff);
                tdCallDiff.className = oiClass(strikeData.call_diff);
                tr.appendChild(tdCallDiff);

                // Put Diff
                const tdPutDiff = document.createElement("td");
                tdPutDiff.textContent = fmtNum(strikeData.put_diff);
                tdPutDiff.className = oiClass(strikeData.put_diff);
                tr.appendChild(tdPutDiff);

                // Difference
                const tdDiff = document.createElement("td");
                tdDiff.textContent = fmtNum(strikeData.difference);
                tdDiff.className = oiClass(strikeData.difference);
                tr.appendChild(tdDiff);

                // Diff of Diff
                const tdDiffDiff = document.createElement("td");
                tdDiffDiff.textContent = fmtNum(strikeData.diff_of_diff);
                tdDiffDiff.className = oiClass(strikeData.diff_of_diff);
                tr.appendChild(tdDiffDiff);

                // Reversal
                const tdReversal = document.createElement("td");
                tdReversal.textContent = strikeData.reversal || "--";
                tr.appendChild(tdReversal);

                // Strike Price
                const tdStrike = document.createElement("td");
                tdStrike.textContent = fmtNum(strikeData.strike_price);
                tdStrike.style.fontWeight = "bold";
                tr.appendChild(tdStrike);
            }

            tbody.appendChild(tr);
        }
    }

    // ── Boot ────────────────────────────────────────────────────────────

    fetch("/api/upstox/status")
        .then(r => r.json())
        .then(status => {
            _dataSource = status.fetcher_active;
            if (status.configured && !status.authenticated) {
                const banner = document.getElementById("upstoxBanner");
                if (banner) banner.style.display = "flex";
                const prompt = document.getElementById("upstoxLoginPrompt");
                if (prompt) prompt.style.display = "block";
                const waitText = document.getElementById("waitingText");
                if (waitText) waitText.textContent = "Upstox login required for real-time data";
                const waitSub = document.getElementById("waitingSubtext");
                if (waitSub) waitSub.textContent = "Click below to login (one-time daily). NSE fallback active meanwhile.";
            } else if (status.fetcher_active === "upstox") {
                _dataSource = "upstox";
            }
        })
        .catch(() => {});

    connect();
})();
