# Option Chain Dashboard - Setup Instructions

## 📦 After Unzipping the Project

Follow these steps to get the dashboard running:

---

## Step 1: Install Python (if not already installed)

1. Go to: https://www.python.org/downloads/
2. Download and install Python 3.9 or higher
3. **IMPORTANT:** During installation, check **"Add Python to PATH"** checkbox
4. Verify installation by opening Command Prompt/Terminal and typing:
   ```
   python --version
   ```
   Should show: `Python 3.9.x` or higher

---

## Step 2: Install Required Python Packages

Open Command Prompt/Terminal in the project folder and run:

### For Windows:
```bash
pip install -r requirements.txt
```

### For Mac/Linux:
```bash
pip3 install -r requirements.txt
```

**Note:** If you get permission errors, use:
- Windows: `pip install --user -r requirements.txt`
- Mac/Linux: `pip3 install --user -r requirements.txt`

---

## Step 3: Configure Upstox API (Optional but Recommended for Real-Time Data)

### Why Upstox?
- **FREE** - No monthly charges
- **Zero delay** - Real-time tick-by-tick updates
- **Better than NSE scraping** - No 30-second delay

### Setup Steps:

1. **Create Upstox Account** (if you don't have one):
   - Go to: https://upstox.com/open-demat-account
   - Sign up for a free account

2. **Create API App**:
   - Go to: https://account.upstox.com/developer/apps
   - Click "Create New App"
   - Fill in:
     - **App Name:** Option Chain Dashboard (or any name)
     - **Redirect URI:** `http://127.0.0.1:8765/callback`
   - Click "Create"
   - **Copy your API Key and API Secret**

3. **Update Configuration**:
   - Open `backend/config.py` in a text editor
   - Find these lines:
     ```python
     UPSTOX_API_KEY = "your-api-key-here"
     UPSTOX_API_SECRET = "your-api-secret-here"
     ```
   - Replace with your actual API Key and Secret
   - Save the file

4. **Login to Upstox** (First Time):
   - Start the dashboard (see Step 4)
   - Open browser and go to: `http://127.0.0.1:8765/upstox/login`
   - Login with your Upstox credentials
   - You'll be redirected back - dashboard will now use real-time data!

**Note:** If you skip Upstox setup, the dashboard will use NSE scraping (30-second delay).

---

## Step 4: Start the Dashboard

### For Windows:
Double-click `run.bat` OR open Command Prompt in project folder and run:
```bash
run.bat
```

### For Mac/Linux:
Open Terminal in project folder and run:
```bash
chmod +x run.sh
./run.sh
```

**OR** manually run:
```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

---

## Step 5: Open Dashboard in Browser

Once the server starts, you'll see:
```
INFO:     Uvicorn running on http://127.0.0.1:8765
```

1. Open your web browser (Chrome, Firefox, Edge, etc.)
2. Go to: **http://127.0.0.1:8765**
3. The dashboard will load automatically!

---

## 🎯 Using the Dashboard

### Symbol Switching:
- Use the dropdown at the top to switch between:
  - NIFTY
  - BANKNIFTY
  - FINNIFTY
  - SENSEX
  - BANKEX
- No need to restart the server!

### Key Features:

1. **Option Chain Table:**
   - Shows OI Change, Volume, LTP for all strikes
   - Yellow row = ATM (At The Money) strike
   - Green flash = Price went up, Red flash = Price went down

2. **OI Change History:**
   - Shows time-wise OI totals
   - Click **"Def. of Def"** button to open detailed difference log

3. **3 Strike Range Analysis:**
   - Select 3 strikes from dropdowns
   - Click **START** (green button) to begin tracking
   - See Call/Put OI changes, differences, and support/resistance signals
   - Click **STOP** (orange) to pause
   - Click **CLEAR** (red) to reset

4. **Export Data:**
   - Click the download icon in left sidebar
   - Choose CSV or JSON format

---

## ⚠️ Troubleshooting

### Problem: "Module not found" error
**Solution:** Make sure you ran `pip install -r requirements.txt` (Step 2)

### Problem: Port 8765 already in use
**Solution:** 
- Close any other application using port 8765
- OR change port in `backend/config.py` (line 75): `PORT = 8766`

### Problem: Upstox login not working
**Solution:**
- Make sure Redirect URI in Upstox app settings is exactly: `http://127.0.0.1:8765/callback`
- Check API Key and Secret in `backend/config.py`

### Problem: No data showing
**Solution:**
- Market might be closed (dashboard works during market hours: 9:15 AM - 3:30 PM IST)
- Check if Upstox is logged in (banner at top will show login link)
- Wait 30-60 seconds for first data load

### Problem: "Python not found"
**Solution:**
- Reinstall Python with "Add to PATH" checked
- OR use full path: `C:\Python39\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8765`

---

## 📞 Support

If you face any issues:
1. Check the terminal/command prompt for error messages
2. Make sure all steps above are completed
3. Verify Python version: `python --version` (should be 3.9+)

---

## 🎉 You're All Set!

The dashboard should now be running. Enjoy real-time option chain analysis!

**Default URL:** http://127.0.0.1:8765

---

**Note:** This dashboard is for educational and analysis purposes. Always verify data with official sources before making trading decisions.
