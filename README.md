# Real-Time Option Chain Dashboard (COA 1.0)

## यह क्या है? (What is this?)

यह एक **real-time NIFTY / BANKNIFTY / SENSEX option chain dashboard** है जो browser में चलता है। इसमें COA (Chart of Accuracy) algorithm, Max Pain, Max Gain, OI Change Tracking, Strike Range OI Analysis, और बहुत कुछ built-in है।

### Data Source:

| Mode | Speed | Setup |
|------|-------|-------|
| **Upstox API (Recommended)** | **Zero delay, real-time, tick-by-tick** | Free Upstox account + API key |
| **NSE Scraping (Fallback)** | ~30 second delay | बस Python + Chrome चाहिए |

**Upstox API FREE है** -- कोई monthly charge नहीं।

---

## Features (क्या-क्या मिलेगा)

| Feature | Description |
|---------|-------------|
| **Real-time Option Chain** | Upstox: tick-by-tick zero delay / NSE: हर ~3 second |
| **Symbol Switcher** | Dashboard में dropdown से NIFTY, BANKNIFTY, FINNIFTY, SENSEX, BANKEX switch करें -- restart नहीं करना |
| **COA Algorithm** | S-Reversal (Break Even / Break Down), WTT/WTB indicators |
| **Max Pain & Max Gain** | Header में दिखता है |
| **Volume Change Tracking** | Volume Change + Volume Change Diff columns |
| **3 Strike Range OI Analysis** | 3 strike select करें, START दबाएं -- Call OI Change, Put OI Change, Difference, Support/Resistance signal |
| **OI Change History** | Time-wise OI totals + Diff of Diff log |
| **START / STOP / CLEAR** | Data tracking control buttons (हरा START, नारंगी STOP, लाल CLEAR) |
| **Spot Chart** | Max Pain, Max Gain lines chart पर labeled tags के साथ |
| **Speed Meter** | Header में green dot - data कितनी fast आ रहा है |
| **Dark Theme** | LTP Calculator जैसा dark theme |
| **CSV/JSON Export** | Dashboard से download button या command line से export |
| **11 Default Strikes** | Clean view -- 5 ऊपर + ATM + 5 नीचे |

---

## Windows पर कैसे चलाएं (4 Steps)

### Step 1: Python Install करें (अगर नहीं है)

1. जाएं: https://www.python.org/downloads/
2. Download करें और install करें
3. **IMPORTANT:** Install करते समय **"Add Python to PATH"** checkbox जरूर tick करें

Check करें: Command Prompt खोलें और लिखें:
```
python --version
```
`Python 3.9` या उससे ऊपर दिखना चाहिए।

### Step 2: ZIP खोलें और Dependencies Install करें

1. ZIP file extract करें (Right click → "Extract All")
2. `option-chain-dashboard` folder में जाएं
3. Address bar में `cmd` type करके Enter दबाएं (Command Prompt खुलेगा)
4. यह command चलाएं (सिर्फ पहली बार):
```
pip install -r requirements.txt
```

### Step 3: Server चालू करें

**सबसे आसान:** `run.bat` file पर double-click करें

**या Command Prompt से:**
```
python -m backend.main
```

एक black window खुलेगी -- **इसे बंद मत करें!**

### Step 4: Browser में Dashboard खोलें

Browser में यह URL खोलें:

**http://127.0.0.1:8765**

Dashboard खुल जाएगा! अगर Upstox API set है तो **"Login with Upstox"** button दिखेगा -- उसपर click करें।

---

## Mac पर कैसे चलाएं (4 Steps)

### Step 1: Python Check करें
```
python3 --version
```
अगर Python 3.9+ है तो ठीक है। अगर नहीं: https://www.python.org/downloads/

### Step 2: Dependencies Install करें
```
cd ~/Desktop/option-chain-dashboard
pip3 install -r requirements.txt
```

### Step 3: Server चालू करें
```
python3 -m backend.main
```

### Step 4: Browser में खोलें

**http://127.0.0.1:8765**

---

## Upstox API Setup (Zero Delay Data के लिए)

### Step 1: Upstox Account खोलें (10 minute)
1. जाएं: https://upstox.com/open-demat-account
2. Aadhaar + PAN card से account खोलें
3. Account approve होने का wait करें

### Step 2: API App बनाएं (2 minute)
1. Login करें: https://account.upstox.com/developer/apps
2. **"New App"** click करें
3. Details भरें:
   - **App Name:** कुछ भी (जैसे `algotrader`)
   - **Redirect URL:** `http://127.0.0.1:8765/callback` **(exactly यही लिखें!)**
4. App बनने के बाद **API Key** और **API Secret** मिलेगा

### Step 3: Config में API Key डालें
`backend/config.py` file Notepad में खोलें और बदलें:
```python
UPSTOX_API_KEY = "your-api-key-here"
UPSTOX_API_SECRET = "your-api-secret-here"
```

### Step 4: Login करें
1. Server start करें
2. Browser में http://127.0.0.1:8765 खोलें
3. **"Login with Upstox"** button click करें
4. Upstox ID/password डालें
5. **Data ab real-time, zero delay आएगा!**

**Note:** Login दिन में एक बार करना है।

---

## Dashboard कैसे समझें

### Header Bar (सबसे ऊपर)

| Item | मतलब |
|------|-------|
| **Dropdown (NIFTY/BANKNIFTY/...)** | Symbol बदलने के लिए -- click करके select करें, restart नहीं करना |
| **Spot** | Current price |
| **Max Pain** | Writers को सबसे कम loss वाली strike |
| **Max Gain** | सबसे ज्यादा OI वाली strike |
| **LOT** | Lot size (NIFTY=75, BANKNIFTY=15, SENSEX=10) |
| **PCR** | Put-Call Ratio (>1 = bullish, <1 = bearish) |
| **Green dot** | Speed Meter - data speed |

### Option Chain Table (बीच में)

| Column | मतलब |
|--------|-------|
| **OI Chg Diff** | OI Change का Change (second derivative) |
| **Vol Chg Diff** | Volume Change ka Change |
| **Vol Chg** | Volume Change (current - previous) |
| **Volume** | Total volume traded |
| **OI Chg** | Open Interest Change |
| **OI** | Total Open Interest |
| **LTP** | Last Traded Price (green flash = price up, red flash = price down) |
| **S-Reversal** | "Break Even" (support) / "Break Down" (resistance) |
| **Strike** | Strike price + WTT/WTB bar |

**Yellow row** = ATM (At The Money) strike

### Right Panel (दाईं तरफ)

**OI Summary (सबसे ऊपर):**
- Total CE OI, Total PE OI, PCR, ATM Strike

**Spot Chart:**
- Price line + Max Pain, Max Gain lines

**3 Strike Range OI Analysis:**
1. 3 dropdowns से strike select करें (default: ATM-1, ATM, ATM+1)
2. **START** button दबाएं (हरा)
3. Live Summary Table दिखेगा:

| Strike | Call OI Change | Put OI Change | Difference | Signal |
|--------|---------------|---------------|------------|--------|
| 25,550 | -1,92,525 | 15,80,700 | ... | **Support** (हरा) |
| 25,600 | 8,69,925 | 10,23,150 | ... | **Support** (हरा) |
| 25,650 | 30,48,325 | 14,22,075 | ... | **Resistance** (लाल) |
| 15:42 | **Total** | **Total** | **Total** | |

**Signal ka matlab:**
- **Support (हरा)** = Put OI > Call OI (buyers zyada, support level)
- **Resistance (लाल)** = Call OI > Put OI (sellers zyada, resistance level)

4. **STOP** button = tracking pause (नारंगी)
5. **CLEAR** button = sab data reset (लाल)

**OI Change History (नीचे):**
- Time-wise CE OI Total, PE OI Total, Difference, Diff of Diff log

### Sidebar (बाईं तरफ)

| Icon | क्या करता है |
|------|-------------|
| ■ | Dashboard - ऊपर scroll |
| ↓ | Download CSV / JSON |
| ★ | Fullscreen |
| ⚙ | Settings |

---

## Symbol बदलना

### तरीका 1: Dashboard से (आसान -- restart नहीं करना!)
- Dashboard में ऊपर left side में dropdown है
- NIFTY, BANKNIFTY, FINNIFTY, SENSEX, BANKEX select करें
- Data automatically switch हो जाएगा

### तरीका 2: Config से (permanent default बदलना)
`backend/config.py` में:
```python
SYMBOL = "BANKNIFTY"    # Default symbol at startup
```
Server restart करें।

---

## Lot Sizes

| Symbol | Lot Size |
|--------|----------|
| NIFTY | 75 |
| BANKNIFTY | 15 |
| FINNIFTY | 25 |
| SENSEX | 10 |
| BANKEX | 15 |

---

## Data Export (CSV/JSON Download)

### Dashboard से:
1. Sidebar में **↓** icon click करें
2. "Download CSV" या "Download JSON" button दबाएं

### Command Line से:
```
python -m backend.cli_export --symbol NIFTY --format csv --output nifty_chain.csv
```

---

## Market Hours

| Time | Status |
|------|--------|
| **9:15 AM - 3:30 PM IST (Mon-Fri)** | Live data |
| **3:30 PM के बाद** | Last data frozen |
| **Saturday-Sunday** | Market बंद - sample data दिखेगा |

---

## Problem आए तो (Troubleshooting)

| Problem | Solution |
|---------|----------|
| "python" command not found | Python install करें, "Add to PATH" tick करें |
| pip install error | `python -m pip install -r requirements.txt` try करें |
| Dashboard खुले लेकिन data न आए | Market बंद है, या 20-30 sec wait करें |
| "Port already in use" | `config.py` में `PORT = 9000` करें, restart करें |
| Upstox "Invalid Credentials" | API Key/Secret check करें, Redirect URL `http://127.0.0.1:8765/callback` exact match होना चाहिए |
| Table में सब 0 दिखे | Market बंद है -- Monday 9:15 AM के बाद live values आएंगे |

---

## Quick Reference Card

| काम | कैसे करें |
|-----|----------|
| **Dashboard चालू** | `run.bat` double-click या `python -m backend.main` |
| **Dashboard खोलें** | http://127.0.0.1:8765 |
| **Dashboard बंद** | `Ctrl + C` |
| **Symbol बदलें** | Dashboard में dropdown से select करें |
| **CSV download** | ↓ icon → "Download CSV" |
| **Fullscreen** | ★ icon click |
| **Settings** | ⚙ icon click |

---

## Support

कोई problem हो या कुछ समझ न आए तो contact करें।
