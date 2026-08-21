"""
CLI Export Mode -- fetch the NSE option chain once (or continuously) and
write the processed data to CSV or JSON.

Usage:
    python -m backend.cli_export --symbol NIFTY --format csv --output nifty_chain.csv
    python -m backend.cli_export --symbol BANKNIFTY --format json --continuous --interval 60
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from backend.nse_fetcher import NSEFetcher, parse_nse_response
from backend.coa_processor import process_option_chain
from backend.oi_tracker import OITracker


def snapshot_to_rows(snapshot) -> list[dict]:
    """Flatten an OptionChainSnapshot into a list of row dicts for CSV."""
    rows = []
    for s in snapshot.strikes:
        rows.append({
            "timestamp": snapshot.timestamp,
            "symbol": snapshot.symbol,
            "expiry": snapshot.expiry,
            "spot_price": snapshot.spot_price,
            "future_price": snapshot.future_price,
            "strike_price": s.strike_price,
            "is_atm": s.is_atm,
            "ce_ltp": s.ce_ltp,
            "ce_oi": s.ce_oi,
            "ce_oi_change": s.ce_oi_change,
            "ce_oi_change_diff": s.ce_oi_change_diff,
            "ce_volume": s.ce_volume,
            "ce_iv": s.ce_iv,
            "pe_ltp": s.pe_ltp,
            "pe_oi": s.pe_oi,
            "pe_oi_change": s.pe_oi_change,
            "pe_oi_change_diff": s.pe_oi_change_diff,
            "pe_volume": s.pe_volume,
            "pe_iv": s.pe_iv,
            "s_reversal_ce": s.s_reversal_ce or "",
            "s_reversal_pe": s.s_reversal_pe or "",
            "wtt_wtb_pct": s.wtt_wtb_pct,
            "max_pain": snapshot.max_pain,
            "max_gain": snapshot.max_gain,
            "pcr": snapshot.pcr,
        })
    return rows


def write_csv(rows: list[dict], output_path: str, append: bool = False) -> None:
    mode = "a" if append else "w"
    write_header = not append or not Path(output_path).exists()

    with open(output_path, mode, newline="", encoding="utf-8") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    print(f"[CSV] Wrote {len(rows)} rows to {output_path}")


def write_json(snapshot, output_path: str, append: bool = False) -> None:
    data = json.loads(snapshot.model_dump_json())

    if append and Path(output_path).exists():
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if isinstance(existing, list):
            existing.append(data)
        else:
            existing = [existing, data]
        data = existing

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data if append else data, f, indent=2, ensure_ascii=False)

    print(f"[JSON] Wrote snapshot to {output_path}")


async def fetch_once(symbol: str) -> object:
    fetcher = NSEFetcher()
    try:
        raw = await fetcher.fetch_option_chain(symbol)
        if raw is None:
            print("ERROR: Failed to fetch data from NSE. Market may be closed.", file=sys.stderr)
            return None
        parsed = parse_nse_response(raw, symbol)
        snapshot = process_option_chain(parsed)
        return snapshot
    finally:
        await fetcher.close()


async def run_continuous(symbol: str, fmt: str, output: str, interval: int) -> None:
    fetcher = NSEFetcher()
    oi_tracker = OITracker()
    iteration = 0

    print(f"Continuous mode: fetching {symbol} every {interval}s -> {output}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            raw = await fetcher.fetch_option_chain(symbol)
            if raw is not None:
                parsed = parse_nse_response(raw, symbol)
                snapshot = process_option_chain(parsed)
                snapshot = oi_tracker.record_and_enrich(snapshot)

                append = iteration > 0
                if fmt == "csv":
                    rows = snapshot_to_rows(snapshot)
                    write_csv(rows, output, append=append)
                else:
                    write_json(snapshot, output, append=append)

                iteration += 1
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetch failed, retrying...")

            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {iteration} iterations.")
    finally:
        await fetcher.close()


def main():
    parser = argparse.ArgumentParser(
        description="NSE Option Chain CLI Exporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m backend.cli_export --symbol NIFTY --format csv --output nifty.csv
  python -m backend.cli_export --symbol BANKNIFTY --format json --output banknifty.json
  python -m backend.cli_export --symbol NIFTY --format csv --output nifty.csv --continuous --interval 60
        """,
    )
    parser.add_argument("--symbol", default="NIFTY", help="Index symbol (default: NIFTY)")
    parser.add_argument("--format", choices=["csv", "json"], default="csv", help="Output format")
    parser.add_argument("--output", default=None, help="Output file path")
    parser.add_argument("--continuous", action="store_true", help="Keep fetching at intervals")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between fetches in continuous mode")

    args = parser.parse_args()

    if args.output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"{args.symbol.lower()}_chain_{ts}.{args.format}"

    if args.continuous:
        asyncio.run(run_continuous(args.symbol, args.format, args.output, args.interval))
    else:
        snapshot = asyncio.run(fetch_once(args.symbol))
        if snapshot is None:
            sys.exit(1)
        if args.format == "csv":
            rows = snapshot_to_rows(snapshot)
            write_csv(rows, args.output)
        else:
            write_json(snapshot, args.output)

        print(f"\nDone! Output saved to: {args.output}")


if __name__ == "__main__":
    main()
