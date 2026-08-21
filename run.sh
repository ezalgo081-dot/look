#!/usr/bin/env bash
set -e

echo "============================================"
echo "  NSE Option Chain Dashboard - COA 1.0"
echo "============================================"
echo ""
echo "Starting server on http://127.0.0.1:8765"
echo "Press Ctrl+C to stop."
echo ""

cd "$(dirname "$0")"
python3 -m backend.main
