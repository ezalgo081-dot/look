"""
Decode Upstox WebSocket V3 protobuf messages using the compiled proto classes.

The MarketDataFeed_pb2 module is generated from Upstox's official proto file:
  https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from backend.MarketDataFeed_pb2 import FeedResponse  # type: ignore
    HAS_PROTO = True
except ImportError:
    HAS_PROTO = False
    logger.warning("MarketDataFeed_pb2 not found — protobuf decoding disabled")


def decode_feed_response(data: bytes) -> dict[str, dict]:
    """
    Decode a binary protobuf WebSocket message from Upstox V3.

    Returns a dict mapping instrument_key → tick data, e.g.:
      {
        "NSE_FO|NIFTY25FEB25400CE": {
            "ltp": 123.45,
            "cp": 120.0,
            "oi": 5000000,
            "volume": 12345,
            "bid_price": 123.0,
            "bid_qty": 100,
            "ask_price": 124.0,
            "ask_qty": 200,
            "iv": 18.5,
            ...
        },
        ...
      }
    """
    if not HAS_PROTO:
        return {}

    try:
        resp = FeedResponse()
        resp.ParseFromString(data)
    except Exception as exc:
        logger.debug("Protobuf parse error: %s", exc)
        return {}

    result: dict[str, dict] = {}

    for inst_key, feed in resp.feeds.items():
        tick: dict = {}

        # ── FullFeed mode (full_d5, full_d30) ────────────────────────
        if feed.HasField("fullFeed"):
            full = feed.fullFeed

            # MarketFullFeed — options / equities
            if full.HasField("marketFF"):
                mff = full.marketFF
                _extract_ltpc(mff.ltpc, tick)

                tick["oi"] = mff.oi
                tick["volume"] = mff.vtt
                tick["iv"] = mff.iv
                tick["atp"] = mff.atp
                tick["tbq"] = mff.tbq
                tick["tsq"] = mff.tsq

                # Best bid/ask from market depth
                if mff.marketLevel and mff.marketLevel.bidAskQuote:
                    baq = mff.marketLevel.bidAskQuote[0]
                    tick["bid_price"] = baq.bidP
                    tick["bid_qty"] = baq.bidQ
                    tick["ask_price"] = baq.askP
                    tick["ask_qty"] = baq.askQ

                # Option greeks
                if mff.HasField("optionGreeks"):
                    og = mff.optionGreeks
                    tick["delta"] = og.delta
                    tick["theta"] = og.theta
                    tick["gamma"] = og.gamma
                    tick["vega"] = og.vega
                    tick["rho"] = og.rho

            # IndexFullFeed — index instruments
            elif full.HasField("indexFF"):
                _extract_ltpc(full.indexFF.ltpc, tick)

        # ── LTPC mode ────────────────────────────────────────────────
        elif feed.HasField("ltpc"):
            _extract_ltpc(feed.ltpc, tick)

        # ── FirstLevelWithGreeks mode (option_greeks) ────────────────
        elif feed.HasField("firstLevelWithGreeks"):
            flg = feed.firstLevelWithGreeks
            _extract_ltpc(flg.ltpc, tick)

            tick["oi"] = flg.oi
            tick["volume"] = flg.vtt
            tick["iv"] = flg.iv

            if flg.HasField("firstDepth"):
                tick["bid_price"] = flg.firstDepth.bidP
                tick["bid_qty"] = flg.firstDepth.bidQ
                tick["ask_price"] = flg.firstDepth.askP
                tick["ask_qty"] = flg.firstDepth.askQ

            if flg.HasField("optionGreeks"):
                og = flg.optionGreeks
                tick["delta"] = og.delta
                tick["theta"] = og.theta
                tick["gamma"] = og.gamma
                tick["vega"] = og.vega
                tick["rho"] = og.rho

        if tick:
            result[inst_key] = tick

    return result


def _extract_ltpc(ltpc, tick: dict) -> None:
    """Pull LTP / close-price / trade-qty from an LTPC sub-message."""
    if ltpc.ltp:
        tick["ltp"] = ltpc.ltp
    if ltpc.cp:
        tick["cp"] = ltpc.cp
    if ltpc.ltq:
        tick["ltq"] = ltpc.ltq
    if ltpc.ltt:
        tick["ltt"] = ltpc.ltt
