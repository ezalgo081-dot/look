"""
FastAPI application entry point.

Serves the web dashboard and provides a WebSocket endpoint that pushes
real-time option chain data to connected clients.

Data source priority:
  1. Upstox API (real-time, zero delay) -- if API key configured
  2. BrowserFetcher (headless Chrome) -- NSE scraping fallback
  3. NSEFetcher (HTTP) -- basic HTTP fallback
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.config import (
    SYMBOL as _DEFAULT_SYMBOL, POLL_INTERVAL_SECONDS, HOST, PORT,
    STRIKES_ABOVE_BELOW_ATM, SUPPORTED_SYMBOLS,
    UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI,
    UPSTOX_ACCESS_TOKEN,
)

# Mutable runtime symbol (can be changed via API)
active_symbol: str = _DEFAULT_SYMBOL
from backend.nse_fetcher import parse_nse_response
from backend.coa_processor import process_option_chain
from backend.oi_tracker import OITracker
from backend.models import OptionChainSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Choose fetcher (priority: Upstox > Browser > HTTP) ───────────────────────
fetcher = None
fetcher_type = "none"
upstox_fetcher = None

if UPSTOX_API_KEY and UPSTOX_API_SECRET:
    try:
        from backend.upstox_fetcher import UpstoxFetcher
        upstox_fetcher = UpstoxFetcher(
            api_key=UPSTOX_API_KEY,
            api_secret=UPSTOX_API_SECRET,
            redirect_uri=UPSTOX_REDIRECT_URI,
            access_token=UPSTOX_ACCESS_TOKEN or None,
        )
        if upstox_fetcher.is_authenticated:
            fetcher = upstox_fetcher
            fetcher_type = "upstox"
            logger.info("✓ Using Upstox API (REAL-TIME, zero delay)")
        else:
            logger.info("Upstox API key found but not authenticated -- login via /upstox/login")
    except Exception as exc:
        logger.warning("Upstox fetcher init failed: %s -- falling back", exc)

if fetcher is None:
    try:
        from backend.browser_fetcher import BrowserFetcher
        fetcher = BrowserFetcher()
        if fetcher_type == "none":
            fetcher_type = "browser"
        logger.info("Using BrowserFetcher (headless Chrome) for NSE data (~30s delay)")
    except ImportError:
        logger.info("Selenium not installed -- falling back to HTTP fetcher")

if fetcher is None:
    from backend.nse_fetcher import NSEFetcher
    fetcher = NSEFetcher()
    if fetcher_type == "none":
        fetcher_type = "http"
    logger.info("Using NSEFetcher (HTTP) -- may need cookies for NSE bot protection")

# ── Shared state ─────────────────────────────────────────────────────────────
oi_tracker = OITracker()
connected_clients: set[WebSocket] = set()
latest_snapshot: Optional[OptionChainSnapshot] = None
_poll_task: Optional[asyncio.Task] = None


async def _broadcast(snapshot: OptionChainSnapshot) -> None:
    """Push snapshot to all connected WebSocket clients."""
    payload = snapshot.model_dump_json()
    dead: list[WebSocket] = []
    for ws in connected_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.discard(ws)


_tick_broadcast_count = 0
_rt_broadcast_task: Optional[asyncio.Task] = None


async def _realtime_broadcast_loop() -> None:
    """
    High-frequency broadcast loop (decoupled from WS receive loop).

    Polls upstox_fetcher._chain_version every ~50 ms.  When a new version
    is detected the chain snapshot is parsed, processed, and broadcast to
    all connected dashboard clients.  This keeps the WS receive loop
    completely non-blocking so ticks are never missed.
    """
    global latest_snapshot, _tick_broadcast_count
    last_version = 0

    while True:
        try:
            # Only run when Upstox WS is actively streaming
            if (
                not upstox_fetcher
                or not upstox_fetcher._ws_connected
                or not upstox_fetcher._latest_chain
            ):
                await asyncio.sleep(0.5)
                continue

            current_version = getattr(upstox_fetcher, "_chain_version", 0)
            if current_version == last_version:
                # No new tick → yield and check again in ~30 ms
                await asyncio.sleep(0.03)
                continue

            last_version = current_version

            raw = upstox_fetcher._latest_chain
            parsed = parse_nse_response(raw, active_symbol)
            if parsed["records"]:
                snapshot = process_option_chain(parsed)
                _tick_broadcast_count += 1
                # OI enrichment every 600 broadcasts (~30 s at 20 fps)
                if _tick_broadcast_count % 600 == 0:
                    snapshot = oi_tracker.record_and_enrich(snapshot)
                latest_snapshot = snapshot
                await _broadcast(snapshot)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Realtime broadcast error: %s", exc)
            await asyncio.sleep(0.1)

        # ~50 ms pause → 20 broadcasts/sec (human-eye limit ≈ 60 fps)
        await asyncio.sleep(0.05)


async def _poll_loop() -> None:
    """Background loop: fetch -> process -> broadcast to all WS clients."""
    global latest_snapshot, fetcher, fetcher_type
    cycle = 0

    while True:
        try:
            # If Upstox just got authenticated, switch to it
            if (
                upstox_fetcher is not None
                and upstox_fetcher.is_authenticated
                and fetcher_type != "upstox"
            ):
                old_fetcher = fetcher
                fetcher = upstox_fetcher
                fetcher_type = "upstox"
                logger.info("Switched to Upstox real-time fetcher!")
                # _realtime_broadcast_loop handles fast broadcasts now
                if old_fetcher and old_fetcher is not upstox_fetcher and hasattr(old_fetcher, "close"):
                    try:
                        await old_fetcher.close()
                    except Exception:
                        pass

            raw = await fetcher.fetch_option_chain(active_symbol)

            if raw is not None:
                parsed = parse_nse_response(raw, active_symbol)

                if not parsed["records"]:
                    if cycle % 20 == 0:
                        logger.warning("Fetched OK but 0 records -- session may be stale")
                else:
                    snapshot = process_option_chain(parsed)
                    snapshot = oi_tracker.record_and_enrich(snapshot)

                    if parsed.get("is_sample"):
                        snapshot.timestamp = snapshot.timestamp + " [SAMPLE DATA]"

                    latest_snapshot = snapshot

                    if cycle % 30 == 0:
                        src = raw.get("_source", fetcher_type).upper()
                        logger.info(
                            "[%s] Spot=%.2f | Strikes=%d | CE_OI=%s | PE_OI=%s | Clients=%d | Ticks=%d",
                            src,
                            snapshot.spot_price,
                            len(snapshot.strikes),
                            snapshot.total_ce_oi,
                            snapshot.total_pe_oi,
                            len(connected_clients),
                            _tick_broadcast_count,
                        )

                    await _broadcast(snapshot)
            else:
                if cycle % 10 == 0:
                    logger.warning("Fetch returned None -- waiting for valid session")
                # After 10 consecutive None results, load sample data so UI can be tested
                if cycle > 0 and cycle % 10 == 0 and latest_snapshot is None:
                    try:
                        from backend.nse_fetcher import NSEFetcher
                        sample_raw = NSEFetcher()._load_sample_data()
                        if sample_raw:
                            parsed = parse_nse_response(sample_raw, active_symbol)
                            if parsed["records"]:
                                snapshot = process_option_chain(parsed)
                                snapshot = oi_tracker.record_and_enrich(snapshot)
                                snapshot.timestamp = snapshot.timestamp + " [SAMPLE - Market Closed]"
                                latest_snapshot = snapshot
                                await _broadcast(snapshot)
                                logger.info("Loaded sample data for UI testing (market closed)")
                    except Exception as sample_exc:
                        logger.debug("Sample data fallback failed: %s", sample_exc)

        except Exception as exc:
            logger.exception("Poll loop error: %s", exc)

        cycle += 1
        # Determine poll interval based on fetcher & WebSocket status
        if fetcher_type == "upstox":
            ws_active = getattr(upstox_fetcher, '_ws_connected', False)
            # WS streaming → REST just refreshes every 30s for missed data
            # WS down → REST polls every 1s for near real-time updates
            interval = 30.0 if ws_active else 1.0
        else:
            interval = POLL_INTERVAL_SECONDS
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _poll_task, _rt_broadcast_task
    _poll_task = asyncio.create_task(_poll_loop())
    # Start the high-frequency broadcast loop (runs alongside poll loop)
    _rt_broadcast_task = asyncio.create_task(_realtime_broadcast_loop())
    logger.info("Poll loop started (fetcher=%s, interval=%.1fs)", fetcher_type, POLL_INTERVAL_SECONDS)
    logger.info("Realtime broadcast loop started (50 ms cadence)")
    yield
    _poll_task.cancel()
    if _rt_broadcast_task:
        _rt_broadcast_task.cancel()
    if fetcher and hasattr(fetcher, "close"):
        try:
            await fetcher.close()
        except Exception:
            pass
    if upstox_fetcher and upstox_fetcher is not fetcher:
        try:
            await upstox_fetcher.close()
        except Exception:
            pass
    logger.info("Shutdown complete")


app = FastAPI(title="NSE Option Chain Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.nseindia.com", "https://nseindia.com"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# ── Static files (frontend) ─────────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    logger.info("Client connected (%d total)", len(connected_clients))

    if latest_snapshot is not None:
        try:
            await ws.send_text(latest_snapshot.model_dump_json())
        except Exception:
            pass

    try:
        while True:
            msg = await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)
        logger.info("Client disconnected (%d remaining)", len(connected_clients))


@app.get("/api/snapshot")
async def api_snapshot():
    if latest_snapshot is None:
        return {"status": "waiting", "message": "No data yet. Market may be closed."}
    return json.loads(latest_snapshot.model_dump_json())


@app.get("/api/config")
async def api_config():
    return {
        "symbol": active_symbol,
        "supported_symbols": SUPPORTED_SYMBOLS,
        "poll_interval": 1.0 if fetcher_type == "upstox" else POLL_INTERVAL_SECONDS,
        "strikes_shown": STRIKES_ABOVE_BELOW_ATM * 2 + 1,
        "server": f"{HOST}:{PORT}",
        "fetcher": fetcher_type,
        "upstox_configured": bool(UPSTOX_API_KEY),
        "upstox_authenticated": upstox_fetcher.is_authenticated if upstox_fetcher else False,
    }


# ── Symbol Switch ───────────────────────────────────────────────────────────

@app.post("/api/switch-symbol")
async def switch_symbol(request: Request):
    """Switch the active trading symbol at runtime."""
    global active_symbol, latest_snapshot
    body = await request.json()
    new_symbol = (body.get("symbol") or "").upper().strip()
    if new_symbol not in SUPPORTED_SYMBOLS:
        return {"status": "error", "message": f"Unsupported symbol: {new_symbol}. Supported: {SUPPORTED_SYMBOLS}"}

    if new_symbol == active_symbol:
        return {"status": "ok", "symbol": active_symbol, "message": "Already active"}

    active_symbol = new_symbol
    latest_snapshot = None  # Clear stale data from previous symbol
    oi_tracker.__init__()   # Reset OI tracking for new symbol
    # Force immediate fetch for new symbol
    if upstox_fetcher:
        upstox_fetcher._latest_chain = None
        upstox_fetcher._last_rest_fetch = 0.0
    logger.info("Symbol switched to %s", active_symbol)
    return {"status": "ok", "symbol": active_symbol}


# ── Upstox OAuth endpoints ──────────────────────────────────────────────────

@app.get("/upstox/login")
async def upstox_login():
    """Redirect user to Upstox login page for OAuth."""
    if not upstox_fetcher:
        return HTMLResponse(
            "<h2>Upstox not configured</h2>"
            "<p>Set UPSTOX_API_KEY and UPSTOX_API_SECRET in backend/config.py</p>",
            status_code=400,
        )
    login_url = upstox_fetcher.get_login_url()
    return RedirectResponse(url=login_url)


@app.get("/callback")
async def upstox_callback(code: str = ""):
    """Handle Upstox OAuth callback after user logs in."""
    if not upstox_fetcher:
        return HTMLResponse("<h2>Error: Upstox not configured</h2>", status_code=400)

    if not code:
        return HTMLResponse("<h2>Error: No authorization code received</h2>", status_code=400)

    success = await upstox_fetcher.exchange_code_for_token(code)
    if success:
        return HTMLResponse(
            """<!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>Upstox Connected!</title>
            <style>
                body { background: #0a0e17; color: #00ff88; font-family: sans-serif;
                       display: flex; justify-content: center; align-items: center;
                       height: 100vh; margin: 0; }
                .box { text-align: center; padding: 40px; border: 2px solid #00ff88;
                       border-radius: 12px; }
                h1 { font-size: 2em; }
                p { color: #aaa; margin-top: 10px; }
                a { color: #00ff88; text-decoration: none; font-size: 1.2em;
                    border: 1px solid #00ff88; padding: 10px 30px; border-radius: 8px;
                    display: inline-block; margin-top: 20px; }
                a:hover { background: #00ff88; color: #0a0e17; }
            </style></head>
            <body><div class="box">
                <h1>Upstox Connected!</h1>
                <p>Real-time data ab active hai. Dashboard pe jaao.</p>
                <a href="/">Open Dashboard</a>
            </div></body></html>""",
        )
    else:
        return HTMLResponse(
            """<!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>Login Failed</title>
            <style>
                body { background: #0a0e17; color: #ff4444; font-family: sans-serif;
                       display: flex; justify-content: center; align-items: center;
                       height: 100vh; margin: 0; }
                .box { text-align: center; padding: 40px; border: 2px solid #ff4444;
                       border-radius: 12px; }
                a { color: #00ff88; text-decoration: none; border: 1px solid #00ff88;
                    padding: 10px 30px; border-radius: 8px; display: inline-block;
                    margin-top: 20px; }
            </style></head>
            <body><div class="box">
                <h1>Login Failed</h1>
                <p>Token exchange failed. Please try again.</p>
                <a href="/upstox/login">Retry Login</a>
            </div></body></html>""",
            status_code=400,
        )


@app.get("/api/upstox/status")
async def upstox_status():
    """Check Upstox authentication status."""
    return {
        "configured": bool(UPSTOX_API_KEY),
        "authenticated": upstox_fetcher.is_authenticated if upstox_fetcher else False,
        "fetcher_active": fetcher_type,
        "login_url": f"http://{HOST}:{PORT}/upstox/login" if UPSTOX_API_KEY else None,
    }


@app.post("/api/upstox/token")
async def upstox_set_token(request: Request):
    """Accept a pasted Upstox access token directly."""
    global fetcher, fetcher_type
    body = await request.json()
    token = (body.get("access_token") or body.get("token") or "").strip()
    if not token:
        return {"status": "error", "message": "No access_token provided"}

    if not upstox_fetcher:
        return {"status": "error", "message": "Upstox not configured (set API keys in config.py)"}

    upstox_fetcher.set_access_token(token)

    if upstox_fetcher.is_authenticated:
        fetcher = upstox_fetcher
        fetcher_type = "upstox"
        logger.info("✓ Token pasted — switched to Upstox real-time")
        return {"status": "ok", "message": "Token set, using Upstox real-time data"}
    else:
        return {"status": "error", "message": "Token set but authentication check failed"}


@app.post("/api/ingest")
async def ingest_nse_data(request: Request):
    """Receive raw NSE data posted externally (bridge script or extension)."""
    global latest_snapshot
    try:
        raw = await request.json()
        raw["_fetch_latency_ms"] = raw.get("_fetch_latency_ms", 0)
        parsed = parse_nse_response(raw, active_symbol)

        if not parsed["records"]:
            return {"status": "error", "message": "Empty data received"}

        snapshot = process_option_chain(parsed)
        snapshot = oi_tracker.record_and_enrich(snapshot)
        snapshot.fetch_latency_ms = raw.get("_fetch_latency_ms", 0)
        latest_snapshot = snapshot

        await _broadcast(snapshot)

        logger.info(
            "[INGEST] Spot=%.2f | Strikes=%d | Clients=%d",
            snapshot.spot_price, len(snapshot.strikes), len(connected_clients),
        )
        return {"status": "ok", "strikes": len(snapshot.strikes)}
    except Exception as exc:
        logger.error("Ingest error: %s", exc)
        return {"status": "error", "message": str(exc)}


# ── Run directly ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
