"""
Upstox API Fetcher — Real-Time Option Chain via WebSocket + REST.

Architecture:
  1. REST  → GET /v2/option/chain   (initial load + periodic 30s refresh)
  2. WS    → wss://...market-data-feed  (tick-by-tick streaming, protobuf)

On each WebSocket tick the in-memory chain is updated *in-place* and the
tick_callback fires → main.py broadcasts the snapshot to all dashboard
clients instantly.  REST refreshes happen every ~30 s to pick up new strikes
or missed data.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable, Awaitable, List
from urllib.parse import urlencode

import httpx

from backend.config import UPSTOX_INSTRUMENT_KEYS

logger = logging.getLogger(__name__)

# ── Upstox API endpoints ─────────────────────────────────────────────────────
UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
UPSTOX_BASE_URL = "https://api.upstox.com/v2"

TOKEN_FILE = Path(__file__).resolve().parent.parent / "upstox_token.json"

# Try importing websockets (already in requirements.txt)
try:
    import websockets          # type: ignore
    import websockets.asyncio  # type: ignore
    HAS_WS = True
except ImportError:
    HAS_WS = False
    logger.warning("websockets package not found — WebSocket streaming disabled")


class UpstoxFetcher:
    """
    Fetches real-time option chain data from the Upstox API.

    • REST polling  → full chain every ~30 s (or every 3 s if WS fails)
    • WebSocket     → tick-by-tick updates for subscribed instruments
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.redirect_uri = redirect_uri
        self.access_token: Optional[str] = access_token

        self._client: Optional[httpx.AsyncClient] = None
        self._latest_chain: Optional[dict] = None
        self._tick_callback: Optional[Callable[[], Awaitable[None]]] = None
        self._authenticated = False
        self._current_symbol: str = "NIFTY"

        # Caches
        self._expiry_cache: dict[str, tuple[List[str], float]] = {}
        self._last_rest_fetch: float = 0.0

        # WebSocket state
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_connected: bool = False
        self._instrument_map: dict[str, dict] = {}   # inst_key → {strike, type}
        self._spot_instrument_key: str = ""
        self._ws_reconnect_count: int = 0
        self._chain_version: int = 0               # Incremented on each WS tick update

        # Token init
        if self.access_token:
            self._authenticated = True
            self._save_token(self.access_token)
            logger.info("✓ Upstox: Using provided access token")
        else:
            self._load_token()

        if self.is_authenticated:
            logger.info("✓ Upstox: Authenticated — ready for live data")
        else:
            logger.info("Upstox: Not authenticated — login via /upstox/login")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated and self.access_token is not None

    # ── Token management ──────────────────────────────────────────────────────

    def _load_token(self) -> None:
        if not TOKEN_FILE.exists():
            return
        try:
            data = json.loads(TOKEN_FILE.read_text())
            token = data.get("access_token")
            expires_at = data.get("expires_at", 0)
            if token and time.time() < expires_at:
                self.access_token = token
                self._authenticated = True
                logger.info("✓ Loaded saved token (expires in %.0f min)",
                            (expires_at - time.time()) / 60)
            else:
                logger.info("Saved token expired — re-auth required")
        except Exception as exc:
            logger.debug("Token load failed: %s", exc)

    def _save_token(self, token: str, expires_in: int = 86400) -> None:
        try:
            TOKEN_FILE.write_text(json.dumps({
                "access_token": token,
                "expires_at": time.time() + expires_in - 300,
                "saved_at": time.time(),
            }, indent=2))
        except Exception as exc:
            logger.warning("Token save failed: %s", exc)

    # ── OAuth ─────────────────────────────────────────────────────────────────

    def get_login_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": self.api_key,
            "redirect_uri": self.redirect_uri,
        }
        return f"{UPSTOX_AUTH_URL}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as c:
                resp = await c.post(UPSTOX_TOKEN_URL, data={
                    "code": code,
                    "client_id": self.api_key,
                    "client_secret": self.api_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                })
                resp.raise_for_status()
                data = resp.json()
                token = data.get("access_token")
                if token:
                    self.access_token = token
                    self._authenticated = True
                    self._save_token(token, data.get("expires_in", 86400))
                    await self._reset_client()
                    logger.info("✓ OAuth successful!")
                    return True
                logger.error("No access_token in response: %s", data)
                return False
        except Exception as exc:
            logger.error("Token exchange failed: %s", exc)
            return False

    def set_access_token(self, token: str) -> bool:
        self.access_token = token
        self._authenticated = True
        self._save_token(token)
        if self._client and not self._client.is_closed:
            asyncio.get_event_loop().create_task(self._client.aclose())
        self._client = None
        self._expiry_cache.clear()
        logger.info("✓ Access token set directly")
        return True

    def set_tick_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._tick_callback = callback
        logger.info("Tick callback registered")

    # ── HTTP client ───────────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.access_token}",
                },
                timeout=httpx.Timeout(15.0),
            )
        return self._client

    async def _reset_client(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ── Expiry helpers ────────────────────────────────────────────────────────

    async def _get_expiry_dates(self, symbol: str) -> List[str]:
        cached = self._expiry_cache.get(symbol)
        if cached and time.time() - cached[1] < 300:
            return cached[0]

        instrument_key = UPSTOX_INSTRUMENT_KEYS.get(symbol)
        if not instrument_key:
            return []

        try:
            client = await self._get_client()
            resp = await client.get(
                f"{UPSTOX_BASE_URL}/option/contract",
                params={"instrument_key": instrument_key},
            )
            if resp.status_code == 401:
                self._handle_auth_failure()
                return []
            resp.raise_for_status()
            data = resp.json()
            expiry_set = set()
            for item in data.get("data", []):
                exp = item.get("expiry")
                if exp:
                    expiry_set.add(exp)
            dates = sorted(list(expiry_set))
            self._expiry_cache[symbol] = (dates, time.time())
            return dates
        except Exception as exc:
            logger.error("Expiry dates fetch failed: %s", exc)
            return []

    async def _get_nearest_expiry(self, symbol: str) -> Optional[str]:
        dates = await self._get_expiry_dates(symbol)
        today_str = datetime.now().strftime("%Y-%m-%d")
        if dates:
            for d in dates:
                if d >= today_str:
                    return d
            return dates[-1]
        # Fallback: nearest Thursday
        today = datetime.now()
        days_ahead = 3 - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0 and today.hour >= 16:
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # ══════════════════════════════════════════════════════════════════════════
    #  MAIN FETCH — called by poll loop in main.py
    # ══════════════════════════════════════════════════════════════════════════

    async def fetch_option_chain(self, symbol: str) -> Optional[dict]:
        """
        Return the latest option chain data.

        • If WebSocket is connected and data is fresh (<30 s) → return
          the cached chain immediately (WebSocket keeps it updated).
        • Otherwise → REST API call to refresh the full chain.
        """
        if not self.is_authenticated:
            return None

        symbol_changed = symbol != self._current_symbol
        
        # If symbol changed, reset WebSocket and clear cache to force fresh fetch
        if symbol_changed:
            logger.info("Symbol changed from %s to %s - resetting WebSocket and cache", 
                       self._current_symbol, symbol)
            if self._ws_task and not self._ws_task.done():
                self._ws_task.cancel()
            self._ws_connected = False
            self._latest_chain = None
            self._last_rest_fetch = 0.0
            self._instrument_map.clear()
            self._chain_version = 0
        
        self._current_symbol = symbol

        # Fast path: WebSocket is streaming, data is fresh
        rest_age = time.time() - self._last_rest_fetch
        if (
            self._ws_connected
            and self._latest_chain
            and not symbol_changed
            and rest_age < 30.0
        ):
            return self._latest_chain

        # ── REST API call ─────────────────────────────────────────────────
        instrument_key = UPSTOX_INSTRUMENT_KEYS.get(symbol)
        if not instrument_key:
            logger.warning("No instrument key for symbol: %s", symbol)
            return None

        try:
            client = await self._get_client()
            expiry_date = await self._get_nearest_expiry(symbol)
            if not expiry_date:
                return self._latest_chain

            start = time.perf_counter()
            resp = await client.get(
                f"{UPSTOX_BASE_URL}/option/chain",
                params={
                    "instrument_key": instrument_key,
                    "expiry_date": expiry_date,
                },
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            if resp.status_code == 401:
                self._handle_auth_failure()
                return self._latest_chain
            if resp.status_code == 429:
                logger.warning("Rate limited — using cached data")
                await asyncio.sleep(1.5)
                return self._latest_chain

            resp.raise_for_status()
            upstox_data = resp.json()

            if upstox_data.get("status") != "success":
                return self._latest_chain

            all_expiry = await self._get_expiry_dates(symbol)
            nse_data = await self._convert_to_nse_format(
                upstox_data, symbol, expiry_date, all_expiry, elapsed_ms,
            )

            if nse_data:
                self._latest_chain = nse_data
                self._last_rest_fetch = time.time()

            # Extract instrument keys & start WebSocket
            if upstox_data.get("data"):
                self._extract_instrument_keys(upstox_data, symbol)
                if HAS_WS and (not self._ws_connected or symbol_changed):
                    asyncio.create_task(self._ensure_websocket())

            return nse_data

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self._handle_auth_failure()
            logger.error("REST error %d: %s",
                         exc.response.status_code, exc.response.text[:200])
            return self._latest_chain
        except httpx.TimeoutException:
            logger.warning("REST timeout")
            return self._latest_chain
        except Exception as exc:
            logger.error("Upstox fetch: %s", exc)
            return self._latest_chain

    # ══════════════════════════════════════════════════════════════════════════
    #  WEBSOCKET — tick-by-tick streaming
    # ══════════════════════════════════════════════════════════════════════════

    def _extract_instrument_keys(self, upstox_data: dict, symbol: str) -> None:
        """Extract instrument keys from the REST response for WS subscription."""
        self._instrument_map.clear()
        self._spot_instrument_key = UPSTOX_INSTRUMENT_KEYS.get(symbol, "")

        items = upstox_data.get("data", [])

        # Find spot price for ATM-based filtering
        spot = 0.0
        for item in items:
            sp = item.get("underlying_spot_price", 0)
            if sp and sp > 0:
                spot = sp
                break

        # Keep only the closest 25 strikes to ATM (50 instruments max)
        if spot > 0:
            items = sorted(items,
                           key=lambda x: abs(x.get("strike_price", 0) - spot))
            items = items[:25]

        for item in items:
            strike = item.get("strike_price", 0)
            for opt_type, field in [("CE", "call_options"), ("PE", "put_options")]:
                opt = item.get(field) or {}
                inst_key = opt.get("instrument_key")
                if inst_key:
                    self._instrument_map[inst_key] = {
                        "strike": strike, "type": opt_type,
                    }

        logger.info("Extracted %d instrument keys for WebSocket", len(self._instrument_map))

    async def _ensure_websocket(self) -> None:
        """Start or restart the WebSocket connection."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass

        self._ws_connected = False
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def _ws_loop(self) -> None:
        """WebSocket connection loop with auto-reconnect (Upstox V3 API)."""
        tick_count = 0

        while self.is_authenticated:
            try:
                # 1. Get authorised WebSocket URL — try V3 first, fall back to V2
                client = await self._get_client()
                v3_url = UPSTOX_BASE_URL.replace("/v2", "/v3")

                resp = await client.get(
                    f"{v3_url}/feed/market-data-feed/authorize",
                )

                if resp.status_code in (401, 403):
                    self._handle_auth_failure()
                    return

                if resp.status_code != 200:
                    # V3 failed — try V2 as fallback
                    logger.warning("V3 auth returned %d, trying V2...", resp.status_code)
                    resp = await client.get(
                        f"{UPSTOX_BASE_URL}/feed/market-data-feed/authorize",
                    )

                if resp.status_code in (401, 403):
                    self._handle_auth_failure()
                    return
                resp.raise_for_status()

                ws_url = resp.json()["data"]["authorizedRedirectUri"]
                logger.info("WebSocket authorize OK → connecting...")

                # 2. Connect
                async with websockets.connect(
                    ws_url,
                    ping_interval=25,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2 ** 20,   # 1 MB
                ) as ws:
                    self._ws_connected = True
                    self._ws_reconnect_count = 0

                    # 3. Subscribe to all instruments in "full" mode
                    all_keys = list(self._instrument_map.keys())
                    if self._spot_instrument_key:
                        all_keys.append(self._spot_instrument_key)

                    sub_msg = json.dumps({
                        "guid": "optchain",
                        "method": "sub",
                        "data": {
                            "mode": "full",
                            "instrumentKeys": all_keys,
                        },
                    })
                    # IMPORTANT: send as BINARY frame — Upstox V3 requires binary
                    await ws.send(sub_msg.encode("utf-8"))
                    logger.info("✓ WebSocket connected — %d instruments subscribed (full mode)",
                                len(all_keys))

                    # 4. Receive ticks — process every binary message instantly
                    #    Do NOT await anything slow here; the broadcast loop
                    #    in main.py picks up _chain_version changes independently.
                    async for message in ws:
                        if isinstance(message, bytes):
                            if len(message) > 2:
                                self._process_tick(message)
                                tick_count += 1

                                if tick_count <= 5:
                                    logger.info("WS tick #%d received (%d bytes, chain_v=%d)",
                                                tick_count, len(message), self._chain_version)
                                elif tick_count % 500 == 0:
                                    logger.info("WS ticks processed: %d (chain_v=%d)",
                                                tick_count, self._chain_version)
                        elif isinstance(message, str):
                            # Text ACK / error / subscription confirmation
                            logger.info("WS text msg: %s", message[:300])

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._ws_connected = False
                self._ws_reconnect_count += 1
                wait = min(2 ** self._ws_reconnect_count, 30)
                logger.warning("WebSocket error: %s — reconnect in %ds", exc, wait)
                await asyncio.sleep(wait)

        self._ws_connected = False

    # ── Tick processing ───────────────────────────────────────────────────────

    _tick_log_count: int = 0

    def _process_tick(self, data: bytes) -> None:
        """Decode protobuf tick and update _latest_chain *in-place*."""
        from backend.proto_helper import decode_feed_response

        feeds = decode_feed_response(data)
        if not feeds:
            if self._tick_log_count < 5:
                logger.warning("Protobuf decode returned empty (msg %d bytes)", len(data))
                self._tick_log_count += 1
            return

        if not self._latest_chain:
            if self._tick_log_count < 5:
                logger.warning("Tick received but _latest_chain is None — waiting for REST data")
                self._tick_log_count += 1
            return

        chain_data = self._latest_chain.get("filtered", {}).get("data", [])
        if not chain_data:
            return

        # Log first few ticks for debugging
        self._tick_log_count += 1
        if self._tick_log_count <= 3:
            sample_keys = list(feeds.keys())[:3]
            sample_vals = {k: {kk: vv for kk, vv in feeds[k].items() if kk in ("ltp", "oi", "iv")}
                          for k in sample_keys}
            logger.info("Tick decoded: %d instruments — samples: %s", len(feeds), sample_vals)

        for instrument_key, tick in feeds.items():

            # ── Spot price (index instrument) ─────────────────────────
            if instrument_key == self._spot_instrument_key:
                ltp = tick.get("ltp", 0)
                if ltp > 0:
                    # Update spot price in the chain
                    if self._latest_chain:
                        self._latest_chain["records"]["underlyingValue"] = ltp
                        for row in chain_data:
                            for ot in ("CE", "PE"):
                                if ot in row:
                                    row[ot]["underlyingValue"] = ltp
                    logger.debug("Updated spot price from WebSocket: %.2f", ltp)
                continue

            # ── Option instrument ─────────────────────────────────────
            info = self._instrument_map.get(instrument_key)
            if not info:
                continue

            strike = info["strike"]
            opt_type = info["type"]

            for row in chain_data:
                if row.get("strikePrice") != strike or opt_type not in row:
                    continue

                opt = row[opt_type]
                ltp = tick.get("ltp", 0)
                if ltp > 0:
                    cp = tick.get("cp", 0) or opt.get("_cp", 0)
                    opt["lastPrice"] = ltp
                    if cp:
                        opt["change"] = round(ltp - cp, 2)
                        opt["pChange"] = round((ltp - cp) / cp * 100, 2)
                        opt["_cp"] = cp

                oi = tick.get("oi", 0)
                if oi > 0:
                    prev_oi = tick.get("prev_oi", 0) or opt.get("_prev_oi", oi)
                    opt["openInterest"] = int(oi)
                    opt["changeinOpenInterest"] = int(oi - prev_oi)
                    if prev_oi > 0:
                        opt["pchangeinOpenInterest"] = round(
                            (oi - prev_oi) / prev_oi * 100, 2,
                        )
                    opt["_prev_oi"] = prev_oi

                vol = tick.get("volume", 0)
                if vol > 0:
                    opt["totalTradedVolume"] = vol

                bp = tick.get("bid_price", 0)
                if bp > 0:
                    opt["bidprice"] = bp
                    opt["bidQty"] = tick.get("bid_qty", 0)

                ap = tick.get("ask_price", 0)
                if ap > 0:
                    opt["askPrice"] = ap
                    opt["askQty"] = tick.get("ask_qty", 0)

                iv = tick.get("iv", 0)
                if iv > 0:
                    opt["impliedVolatility"] = round(iv, 2)

                # Greeks
                for g in ("delta", "theta", "gamma", "vega", "rho"):
                    gv = tick.get(g)
                    if gv:
                        opt[g] = round(gv, 4)

                break

        # Refresh timestamp & bump version so broadcast loop picks it up
        self._latest_chain["records"]["timestamp"] = (
            datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        )
        self._chain_version += 1

    # ── Auth failure ──────────────────────────────────────────────────────────

    def _handle_auth_failure(self) -> None:
        logger.warning("⚠ Token expired/invalid — re-auth required")
        self._authenticated = False

    # ══════════════════════════════════════════════════════════════════════════
    #  NSE-COMPATIBLE FORMAT CONVERSION
    # ══════════════════════════════════════════════════════════════════════════

    async def _convert_to_nse_format(
        self,
        upstox_data: dict,
        symbol: str,
        selected_expiry: str,
        all_expiry_dates: List[str],
        elapsed_ms: float,
    ) -> Optional[dict]:
        items = upstox_data.get("data", [])
        if not items:
            return None

        spot_price = 0.0
        for item in items:
            sp = item.get("underlying_spot_price", 0.0)
            if sp and sp > 0:
                spot_price = sp
                break
        
        # If spot price not found in option chain, try fetching from market data API
        if spot_price == 0.0:
            try:
                client = await self._get_client()
                instrument_key = UPSTOX_INSTRUMENT_KEYS.get(symbol)
                if instrument_key:
                    # Try to get spot price from market quote API
                    quote_resp = await client.get(
                        f"{UPSTOX_BASE_URL}/market-quote/quotes",
                        params={"instrument_key": instrument_key},
                    )
                    if quote_resp.status_code == 200:
                        quote_data = quote_resp.json()
                        if quote_data.get("status") == "success":
                            quotes = quote_data.get("data", {})
                            if instrument_key in quotes:
                                ltp = quotes[instrument_key].get("last_price", 0.0)
                                if ltp and ltp > 0:
                                    spot_price = ltp
                                    logger.info("Fetched spot price from market quote API: %.2f", spot_price)
            except Exception as exc:
                logger.debug("Failed to fetch spot price from market quote API: %s", exc)

        nse_expiry_dates = [self._to_nse_date(e) for e in all_expiry_dates]
        nse_selected = self._to_nse_date(selected_expiry)
        if nse_selected not in nse_expiry_dates:
            nse_expiry_dates.insert(0, nse_selected)

        chain_data: list[dict] = []
        for item in items:
            strike = item.get("strike_price", 0)
            if not strike:
                continue

            row: dict = {"strikePrice": strike}

            for opt_type, field in [("CE", "call_options"), ("PE", "put_options")]:
                raw_opt = item.get(field)
                if not raw_opt:
                    continue
                md = raw_opt.get("market_data") or {}
                greeks = raw_opt.get("option_greeks") or {}
                oi = md.get("oi", 0) or 0
                prev_oi = md.get("prev_oi") or oi
                ltp = md.get("ltp", 0.0) or 0.0
                close = md.get("close_price", 0.0) or 0.0

                row[opt_type] = {
                    "strikePrice": strike,
                    "expiryDate": nse_selected,
                    "underlying": symbol,
                    "identifier": f"{symbol}{nse_selected}{int(strike)}{opt_type}",
                    "openInterest": oi,
                    "changeinOpenInterest": oi - prev_oi,
                    "pchangeinOpenInterest": (
                        round((oi - prev_oi) / prev_oi * 100, 2) if prev_oi else 0.0
                    ),
                    "totalTradedVolume": md.get("volume", 0) or 0,
                    "impliedVolatility": greeks.get("iv", 0.0) or 0.0,
                    "lastPrice": ltp,
                    "change": round(ltp - close, 2),
                    "pChange": round((ltp - close) / close * 100, 2) if close else 0.0,
                    "totalBuyQuantity": md.get("bid_qty", 0) or 0,
                    "totalSellQuantity": md.get("ask_qty", 0) or 0,
                    "bidQty": md.get("bid_qty", 0) or 0,
                    "bidprice": md.get("bid_price", 0.0) or 0.0,
                    "askQty": md.get("ask_qty", 0) or 0,
                    "askPrice": md.get("ask_price", 0.0) or 0.0,
                    "underlyingValue": spot_price,
                    "_cp": close,        # keep close price for WS tick updates
                    "_prev_oi": prev_oi, # keep prev-day OI for WS tick updates
                }

            chain_data.append(row)

        if not chain_data:
            return None

        timestamp = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        total_ce_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in chain_data)
        total_pe_oi = sum(r.get("PE", {}).get("openInterest", 0) for r in chain_data)
        total_ce_vol = sum(r.get("CE", {}).get("totalTradedVolume", 0) for r in chain_data)
        total_pe_vol = sum(r.get("PE", {}).get("totalTradedVolume", 0) for r in chain_data)

        return {
            "records": {
                "expiryDates": nse_expiry_dates,
                "timestamp": timestamp,
                "underlyingValue": spot_price,
                "strikePrices": [r["strikePrice"] for r in chain_data],
                "data": chain_data,
            },
            "filtered": {
                "data": chain_data,      # same list — in-place WS updates
                "CE": {"totOI": total_ce_oi, "totVol": total_ce_vol},
                "PE": {"totOI": total_pe_oi, "totVol": total_pe_vol},
            },
            "_fetch_latency_ms": round(elapsed_ms, 1),
            "_is_sample": False,
            "_source": "upstox",
        }

    @staticmethod
    def _to_nse_date(date_str: str) -> str:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            return date_str

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        logger.info("UpstoxFetcher closed")
