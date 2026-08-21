"""
Selenium-based NSE data fetcher.

Uses headless Chrome to load the NSE option chain page, then scrapes
the rendered DOM table. This bypasses Akamai bot protection because
the real Chrome browser handles all JavaScript challenges natively.

DOM table column layout (23 columns per row):
  CALLS:   [0:checkbox, 1:OI, 2:ChgOI, 3:Vol, 4:IV, 5:LTP, 6:Chg, 7:BidQty, 8:Bid, 9:Ask, 10:AskQty]
  STRIKE:  [11]
  PUTS:    [12:BidQty, 13:Bid, 14:Ask, 15:AskQty, 16:Chg, 17:LTP, 18:IV, 19:Vol, 20:ChgOI, 21:OI, 22:checkbox]
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)

# Top-level returns for Selenium's execute_script (it wraps code in a function body)
_JS_SCRAPE = """
var table = document.querySelector('#optionChainTable-indices');
if (!table) return JSON.stringify({error: 'no table'});

var rows = table.querySelectorAll('tbody tr');
if (rows.length === 0) return JSON.stringify({error: 'no rows'});

var spotText = '';
var allSpans = document.querySelectorAll('span');
for (var si = 0; si < allSpans.length; si++) {
    var t = allSpans[si].textContent.trim();
    if (t.indexOf('NIFTY') === 0 && t.length < 20 && t.indexOf('.') > 0) {
        spotText = t;
    }
}
var spotPrice = parseFloat(spotText.replace('NIFTY', '').replace(/,/g, '').trim()) || 0;

function parseNum(s) {
    if (!s || s === '-' || s === '') return 0;
    return parseFloat(s.replace(/,/g, '')) || 0;
}

var expirySelect = document.querySelector('#expirySelect');
var expiry = expirySelect ? expirySelect.value : '';
var expiryDates = [];
if (expirySelect) {
    var opts = expirySelect.querySelectorAll('option');
    for (var oi = 0; oi < opts.length; oi++) {
        var v = opts[oi].textContent.trim();
        if (v && v !== 'Select') expiryDates.push(v);
    }
}

var data = [];
for (var i = 0; i < rows.length; i++) {
    var cells = rows[i].querySelectorAll('td');
    if (cells.length < 23) continue;
    var c = [];
    for (var j = 0; j < cells.length; j++) c.push(cells[j].textContent.trim());

    var strike = parseNum(c[11]);
    if (strike === 0) continue;

    data.push({
        strikePrice: strike,
        expiryDate: expiry,
        CE: {strikePrice: strike, expiryDate: expiry, underlying: 'NIFTY',
             identifier: 'CE-' + strike,
             openInterest: parseNum(c[1]), changeinOpenInterest: parseNum(c[2]),
             totalTradedVolume: parseNum(c[3]), impliedVolatility: parseNum(c[4]),
             lastPrice: parseNum(c[5]), change: parseNum(c[6]),
             bidQty: parseNum(c[7]), bidprice: parseNum(c[8]),
             askPrice: parseNum(c[9]), askQty: parseNum(c[10]),
             underlyingValue: spotPrice},
        PE: {strikePrice: strike, expiryDate: expiry, underlying: 'NIFTY',
             identifier: 'PE-' + strike,
             openInterest: parseNum(c[21]), changeinOpenInterest: parseNum(c[20]),
             totalTradedVolume: parseNum(c[19]), impliedVolatility: parseNum(c[18]),
             lastPrice: parseNum(c[17]), change: parseNum(c[16]),
             bidQty: parseNum(c[12]), bidprice: parseNum(c[13]),
             askPrice: parseNum(c[14]), askQty: parseNum(c[15]),
             underlyingValue: spotPrice}
    });
}

var totalCeOI = 0, totalPeOI = 0;
for (var di = 0; di < data.length; di++) {
    totalCeOI += data[di].CE.openInterest;
    totalPeOI += data[di].PE.openInterest;
}

return JSON.stringify({
    records: {
        expiryDates: expiryDates,
        underlyingValue: spotPrice,
        strikePrices: data.map(function(d){return d.strikePrice;}),
        timestamp: new Date().toLocaleString('en-IN'),
        data: data
    },
    filtered: {
        CE: {totOI: totalCeOI, totVol: 0},
        PE: {totOI: totalPeOI, totVol: 0},
        data: data
    },
    _source: 'dom_scrape',
    _rows: data.length
});
"""


class BrowserFetcher:
    """Uses headless Chrome + DOM scraping to get live NSE option chain data."""

    def __init__(self) -> None:
        self._driver = None
        self._ready = False
        self._fetch_count = 0
        self._last_refresh = 0.0
        self._chrome_binary = self._find_chrome()

    @staticmethod
    def _find_chrome() -> str:
        import platform, os
        candidates = {
            "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
            "Windows": [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            ],
            "Linux": ["/usr/bin/google-chrome", "/usr/bin/chromium-browser"],
        }
        for path in candidates.get(platform.system(), []):
            if os.path.exists(path):
                return path
        return ""

    def _init_browser(self) -> None:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        if self._chrome_binary:
            opts.binary_location = self._chrome_binary
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )

        try:
            self._driver = webdriver.Chrome(options=opts)
            self._driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
            )
            logger.info("Headless Chrome started")
        except Exception as exc:
            logger.error("Failed to start Chrome: %s", exc)
            self._driver = None
            return

        self._load_page()

    def _load_page(self) -> None:
        """Navigate to NSE option chain and wait for table to render."""
        try:
            logger.info("Loading NSE main page (session warmup)...")
            self._driver.get("https://www.nseindia.com")
            time.sleep(4)

            logger.info("Loading NSE option chain page...")
            self._driver.get("https://www.nseindia.com/option-chain")
            time.sleep(10)

            has_table = self._driver.execute_script(
                "return document.querySelector('#optionChainTable-indices') !== null"
            )
            if has_table:
                self._ready = True
                self._last_refresh = time.time()
                logger.info("NSE option chain page loaded -- table found")
            else:
                logger.warning("NSE page loaded but option chain table not found")
                self._ready = False
        except Exception as exc:
            logger.error("Page load failed: %s", exc)
            self._ready = False

    def _refresh_if_needed(self) -> None:
        """Refresh the page periodically to keep session alive and get fresh data."""
        if time.time() - self._last_refresh > 300:
            logger.info("Refreshing NSE page to keep session alive...")
            try:
                self._driver.refresh()
                time.sleep(8)
                self._last_refresh = time.time()
            except Exception:
                self._ready = False

    def _fetch_sync(self, symbol: str) -> Optional[dict]:
        """Scrape the option chain from the rendered DOM."""
        if self._driver is None or not self._ready:
            self._init_browser()

        if self._driver is None or not self._ready:
            return None

        self._refresh_if_needed()

        try:
            start = time.time()
            result = self._driver.execute_script(_JS_SCRAPE)
            elapsed_ms = int((time.time() - start) * 1000)

            if not result:
                logger.warning("DOM scrape returned None")
                return None

            data = json.loads(result)
            if "error" in data:
                logger.warning("DOM scrape error: %s", data["error"])
                return None

            num_rows = data.get("_rows", 0)
            if num_rows == 0:
                logger.warning("DOM scrape returned 0 rows -- refreshing")
                self._load_page()
                return None

            data["_fetch_latency_ms"] = elapsed_ms
            self._fetch_count += 1

            if self._fetch_count % 30 == 1:
                spot = data.get("records", {}).get("underlyingValue", 0)
                logger.info(
                    "DOM scrape #%d: spot=%.2f, strikes=%d, latency=%dms",
                    self._fetch_count, spot, num_rows, elapsed_ms,
                )

            return data
        except Exception as exc:
            logger.error("DOM scrape failed: %s", exc)
            self._ready = False
            return None

    async def fetch_option_chain(self, symbol: str) -> Optional[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self._fetch_sync, symbol)

    async def close(self) -> None:
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
