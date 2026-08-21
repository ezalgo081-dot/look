"""
NSE India Option Chain Data Fetcher.

NSE uses aggressive Akamai bot protection that requires JavaScript execution
to generate valid session cookies. This module provides two strategies:

  1. BROWSER COOKIE MODE (recommended): Uses cookies exported from a real
     browser session. The user visits nseindia.com once, and the script
     extracts cookies automatically using the 'browser_cookie3' library,
     or the user can paste cookies manually into cookies.json.

  2. DIRECT MODE (fallback): Attempts direct HTTP requests with full browser
     headers. Works intermittently depending on NSE's bot protection mood.

  3. SAMPLE DATA MODE: If all else fails, loads a bundled sample response
     so the dashboard is always functional for demo/development.
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

from backend.config import (
    NSE_BASE_URL,
    NSE_OPTION_CHAIN_URL,
    NSE_HEADERS,
    POLL_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

COOKIES_FILE = Path(__file__).resolve().parent.parent / "cookies.json"
SAMPLE_DATA_FILE = Path(__file__).resolve().parent / "sample_data.json"

# Headers mimicking a real Chrome browser XHR request
_API_HEADERS = {
    **NSE_HEADERS,
    "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="131", "Chromium";v="131"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
}

_PAGE_HEADERS = {
    **NSE_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


class NSEFetcher:
    """Async fetcher that maintains an NSE session and polls option chain data."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._session_ready = False
        self._last_session_time: float = 0
        self._session_ttl: float = 120.0
        self._consecutive_empty: int = 0
        self._use_sample: bool = False

    async def _build_client(self) -> httpx.AsyncClient:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

        # Load cookies from file if available
        cookies = self._load_cookies_from_file()

        self._client = httpx.AsyncClient(
            cookies=cookies,
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            http2=False,
        )
        self._session_ready = bool(cookies)
        if cookies:
            logger.info("Loaded %d cookies from %s", len(cookies), COOKIES_FILE)
            self._last_session_time = time.time()
        return self._client

    def _load_cookies_from_file(self) -> dict:
        """
        Load cookies from cookies.json.
        Format: {"cookie_name": "cookie_value", ...}
        or: [{"name": "...", "value": "..."}, ...]
        """
        if not COOKIES_FILE.exists():
            return {}
        try:
            raw = json.loads(COOKIES_FILE.read_text())
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, list):
                return {c["name"]: c["value"] for c in raw if "name" in c and "value" in c}
        except Exception as exc:
            logger.warning("Failed to load %s: %s", COOKIES_FILE, exc)
        return {}

    async def _ensure_session(self) -> httpx.AsyncClient:
        now = time.time()
        need_refresh = (
            self._client is None
            or self._client.is_closed
            or (now - self._last_session_time) > self._session_ttl
        )

        if not need_refresh:
            return self._client

        client = await self._build_client()

        if not self._session_ready:
            # Try direct warmup
            for url in [NSE_BASE_URL + "/option-chain", NSE_BASE_URL]:
                try:
                    resp = await client.get(url, headers=_PAGE_HEADERS)
                    logger.info("Warmup %s -> %d (%d cookies)", url, resp.status_code, len(client.cookies))
                    await asyncio.sleep(0.5)
                except httpx.HTTPError as exc:
                    logger.warning("Warmup %s failed: %s", url, exc)

            if len(client.cookies) > 0:
                self._session_ready = True
                self._last_session_time = now

        return client

    def _validate_response(self, data: dict) -> bool:
        """Check if the NSE response actually contains option chain records."""
        records = data.get("records", {})
        return bool(records.get("data")) and bool(records.get("expiryDates"))

    async def fetch_option_chain(self, symbol: str) -> Optional[dict]:
        """
        Fetch the full option chain JSON for the given index symbol.
        Falls back to sample data if NSE is unreachable.
        """
        if self._use_sample:
            return self._load_sample_data()

        client = await self._ensure_session()
        url = NSE_OPTION_CHAIN_URL.format(symbol=symbol)

        start = time.perf_counter()
        try:
            resp = await client.get(url, headers=_API_HEADERS)
            elapsed_ms = (time.perf_counter() - start) * 1000

            if resp.status_code in (401, 403):
                logger.warning("NSE returned %d -- forcing session refresh", resp.status_code)
                self._session_ready = False
                self._consecutive_empty += 1
                return self._fallback_or_none()

            resp.raise_for_status()
            data = resp.json()

            if not self._validate_response(data):
                self._consecutive_empty += 1
                if self._consecutive_empty <= 1:
                    logger.info("NSE returned empty data -- retrying with fresh session")
                    self._session_ready = False
                elif self._consecutive_empty == 5:
                    logger.warning(
                        "5 consecutive empty responses. NSE bot protection is active. "
                        "To fix: open https://www.nseindia.com/option-chain in your browser, "
                        "then export cookies to cookies.json (see README for instructions)."
                    )
                elif self._consecutive_empty >= 10:
                    logger.info("Falling back to sample data for demo mode")
                    self._use_sample = True
                return self._fallback_or_none()

            self._consecutive_empty = 0
            data["_fetch_latency_ms"] = elapsed_ms
            return data

        except httpx.HTTPError as exc:
            logger.error("Fetch failed: %s", exc)
            self._consecutive_empty += 1
            return self._fallback_or_none()
        except ValueError as exc:
            logger.error("JSON parse failed: %s", exc)
            return self._fallback_or_none()

    def _fallback_or_none(self) -> Optional[dict]:
        """If we've failed enough times, fall back to sample data."""
        if self._consecutive_empty >= 10 or self._use_sample:
            self._use_sample = True
            return self._load_sample_data()
        return None

    def _load_sample_data(self) -> Optional[dict]:
        """Load the bundled sample NSE response for demo/development."""
        if not SAMPLE_DATA_FILE.exists():
            return None
        try:
            data = json.loads(SAMPLE_DATA_FILE.read_text())
            data["_fetch_latency_ms"] = 0.0
            data["_is_sample"] = True
            return data
        except Exception:
            return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def parse_nse_response(raw: dict, symbol: str) -> dict:
    """
    Transform the raw NSE JSON into a cleaner intermediate dict.
    """
    records_section = raw.get("records", {})
    filtered = raw.get("filtered", {})

    expiry_dates = records_section.get("expiryDates", [])
    timestamp = records_section.get("timestamp", "")
    spot = records_section.get("underlyingValue", 0.0)
    is_sample = raw.get("_is_sample", False)

    selected_expiry = expiry_dates[0] if expiry_dates else ""

    # Use filtered data (nearest expiry) for the chain
    chain_data = filtered.get("data", [])

    records = []
    for row in chain_data:
        strike = row.get("strikePrice", 0)
        ce_raw = row.get("CE")
        pe_raw = row.get("PE")

        def _extract(opt: Optional[dict]) -> Optional[dict]:
            if opt is None:
                return None
            return {
                "ltp": opt.get("lastPrice", 0.0),
                "oi": opt.get("openInterest", 0),
                "changeinOpenInterest": opt.get("changeinOpenInterest", 0),
                "totalTradedVolume": opt.get("totalTradedVolume", 0),
                "impliedVolatility": opt.get("impliedVolatility", 0.0),
                "bidprice": opt.get("bidprice", 0.0),
                "askPrice": opt.get("askPrice", 0.0),
                "underlyingValue": opt.get("underlyingValue", spot),
            }

        records.append({
            "strike_price": strike,
            "ce": _extract(ce_raw),
            "pe": _extract(pe_raw),
        })

    # Estimate future price from ATM synthetic forward
    future_price = spot
    if records and spot > 0:
        atm_strike = min(records, key=lambda r: abs(r["strike_price"] - spot))
        ce = atm_strike.get("ce")
        pe = atm_strike.get("pe")
        if ce and pe:
            future_price = atm_strike["strike_price"] + ce["ltp"] - pe["ltp"]

    return {
        "symbol": symbol,
        "spot_price": spot,
        "future_price": round(future_price, 2),
        "timestamp": timestamp,
        "expiry_dates": expiry_dates,
        "selected_expiry": selected_expiry,
        "fetch_latency_ms": raw.get("_fetch_latency_ms", 0.0),
        "records": records,
        "is_sample": is_sample,
    }
