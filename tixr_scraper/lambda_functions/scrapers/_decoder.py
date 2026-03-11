"""
Seats.io decoder utilities for Tixr.

This is a direct adaptation of the helper functions at the bottom of
`prekindle_scraper/lambda_functions/event_inventory.seatsio.py`. It handles
the various encoding schemes Seats.io uses (plain JSON, hex+offset,
base64+offset, gzip).
"""

from __future__ import annotations

import gzip
import json
from typing import Any, Optional, Tuple


def _bruteforce_offset_decode(raw_bytes: bytes, source: str = "") -> Optional[Tuple[str, int]]:
    """
    Try all 256 byte offsets to decode raw bytes into valid JSON.

    Uses smart prediction first: computes which offsets would make the
    first byte decode to '{' (0x7B) or '[' (0x5B), then falls back to
    iterating all 256 offsets.
    """
    if not raw_bytes:
        return None

    first_byte = raw_bytes[0]

    # Smart prediction: try offsets that would make the first byte
    # decode to '{' (0x7B) or '[' (0x5B)
    priority_offsets = [
        (first_byte - 0x7B) % 256,
        (first_byte - 0x5B) % 256,
    ]

    seen = set()
    candidates = []
    for o in priority_offsets:
        if o not in seen:
            seen.add(o)
            candidates.append(o)

    for o in range(256):
        if o not in seen:
            candidates.append(o)

    for offset in candidates:
        try:
            decoded_bytes = bytes((b - offset) % 256 for b in raw_bytes)
            decoded_str = decoded_bytes.decode("utf-8")
            json.loads(decoded_str)
            return decoded_str, offset
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue

    return None


def decode_seatsio_response(response_text: str) -> Optional[str]:
    """
    Decode seats.io object-statuses response.

    The response may be:
    1. Base64-encoded binary data that contains hex-encoded JSON with offset
    2. Direct hex-encoded JSON with offset
    3. Plain JSON
    """
    import base64

    if not response_text:
        return None

    # Try 1: Direct JSON parse
    try:
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError:
        pass

    # Try 2: Base64 decode first, then bruteforce byte offset
    try:
        if any(c in response_text for c in "+/=") or len(response_text) > 1000:
            binary_data = base64.b64decode(response_text)
            result = _bruteforce_offset_decode(binary_data, source="base64")
            if result:
                return result[0]
    except Exception:
        pass

    # Try 3: Direct hex offset decode
    cleaned = response_text.strip()
    if cleaned and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        try:
            encoded_bytes = bytes.fromhex(cleaned)
            result = _bruteforce_offset_decode(encoded_bytes, source="hex")
            if result:
                return result[0]
        except Exception:
            pass

    # Try 4: Raw binary (e.g. when fetched with .content.decode('latin-1'))
    try:
        if response_text and all(ord(c) < 256 for c in response_text):
            raw_bytes = response_text.encode("latin-1")
            result = _bruteforce_offset_decode(raw_bytes, source="binary")
            if result:
                return result[0]
    except Exception:
        pass

    return None


def decode_charts_response(response_data: Any) -> Optional[str]:
    """
    Decode seats.io charts/published response.
    Supports:
    - gzip compressed
    - plain JSON
    - hex offset
    - base64 offset
    """
    import base64

    if not response_data:
        return None

    # If it's bytes (e.g. from requests), handle directly
    if isinstance(response_data, bytes):
        # 1) GZIP
        if response_data[:2] == b"\x1f\x8b":
            try:
                decompressed = gzip.decompress(response_data)
                return decompressed.decode("utf-8")
            except Exception:
                pass

        # fallback: decode as latin-1 string
        response_text = response_data.decode("latin-1", errors="ignore")
    else:
        response_text = response_data

    # 2) Direct JSON
    try:
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError:
        pass

    # 3) Hex offset
    cleaned = response_text.strip()
    if cleaned and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        try:
            encoded_bytes = bytes.fromhex(cleaned)
            decoded = _bruteforce_offset_decode(encoded_bytes)
            if decoded:
                return decoded[0]
        except Exception:
            pass

    # 4) Base64 + offset
    try:
        binary_data = base64.b64decode(response_text)
        decoded = _bruteforce_offset_decode(binary_data)
        if decoded:
            return decoded[0]
    except Exception:
        pass

    # 5) Raw binary offset
    try:
        raw_bytes = response_text.encode("latin-1")
        decoded = _bruteforce_offset_decode(raw_bytes)
        if decoded:
            return decoded[0]
    except Exception:
        pass

    return None


def parse_json_safe(text: str) -> Optional[Any]:
    """Safely parse JSON, attempting decoding if needed."""
    if not text:
        return None

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try decode then parse
    decoded = decode_seatsio_response(text)
    if decoded:
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            pass

    return None

