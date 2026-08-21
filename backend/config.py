"""
Configuration for the NSE Option Chain Dashboard.
Edit these values to change the symbol, refresh rate, or display settings.

=== UPSTOX API (Real-time data, zero delay) ===
Upstox API FREE hai. Setup karne ke liye:
  1. Upstox account banao: https://upstox.com/open-demat-account
  2. API app banao: https://account.upstox.com/developer/apps
  3. Neeche API_KEY aur API_SECRET dalo
  4. REDIRECT_URI wahi dalo jo app banate waqt diya tha

Agar Upstox API configure nahi hai, toh NSE scraping se data aayega (~30 sec delay).
"""

# ── Upstox API (Real-time, zero delay) ──────────────────────────────────────
UPSTOX_API_KEY = "01cdf43d-dae4-414e-bd8c-ebf621eaf3ab"
UPSTOX_API_SECRET = "8te76q8el1"
UPSTOX_REDIRECT_URI = "http://127.0.0.1:8765/callback"   # App mein yahi set karo

# Access token -- auto-loaded from upstox_token.json after first OAuth login.
# You can also paste it here directly from the Upstox developer dashboard.
UPSTOX_ACCESS_TOKEN = ""  # Leave empty to use token file / OAuth flow

# ── Symbol & Expiry ──────────────────────────────────────────────────────────
SYMBOL = "NIFTY"                       # Default symbol at startup
EXPIRY = None                          # None = auto-select nearest expiry

SUPPORTED_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX"]

LOT_SIZE = {
    "NIFTY": 75,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "SENSEX": 10,
    "BANKEX": 15,
}

# Upstox instrument keys for index symbols
UPSTOX_INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "SENSEX": "BSE_INDEX|SENSEX",
    "BANKEX": "BSE_INDEX|BANKEX",
}

# ── NSE Endpoints ────────────────────────────────────────────────────────────
NSE_BASE_URL = "https://www.nseindia.com"
NSE_OPTION_CHAIN_URL = NSE_BASE_URL + "/api/option-chain-indices?symbol={symbol}"
NSE_EQUITY_CHAIN_URL = NSE_BASE_URL + "/api/option-chain-equities?symbol={symbol}"
NSE_INDEX_QUOTE_URL = NSE_BASE_URL + "/api/quote-equity-derivative?symbol={symbol}"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/option-chain",
}

# ── Refresh & Performance ────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 3.0            # Seconds between scrapes (page reloads every ~25s for fresh data)
STRIKES_ABOVE_BELOW_ATM = 5            # Number of strikes to show each side of ATM (5+5+1 = 11 total)

# ── OI Tracker ───────────────────────────────────────────────────────────────
OI_SNAPSHOT_INTERVAL_SECONDS = 60      # How often to take an OI snapshot
MAX_OI_SNAPSHOTS = 480                 # ~8 hours of 1-min snapshots

# ── Server ───────────────────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 8765
