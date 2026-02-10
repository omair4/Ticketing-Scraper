"""
Decoding utilities for obfuscated API responses (seats.io hex-encoded data)

Copied from event_scraper/utils/decoder.py — same encoding used by cdn-na.seatsio.net
"""

import json
from typing import Optional, Any
import structlog

logger = structlog.get_logger()


def _bruteforce_offset_decode(raw_bytes: bytes, source: str = "") -> Optional[tuple[str, int]]:
    """
    Try all 256 byte offsets to decode raw bytes into valid JSON.

    Uses smart prediction first: computes which offsets would make the
    first byte decode to '{' (0x7B) or '[' (0x5B), then falls back to
    iterating all 256 offsets.

    Args:
        raw_bytes: Raw bytes (already converted from hex or base64)
        source: Label for logging (e.g. "hex", "base64")

    Returns:
        Tuple of (decoded_json_str, offset) on success, None on failure
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
            decoded_str = decoded_bytes.decode('utf-8')
            json.loads(decoded_str)
            logger.info("bruteforce_decode_success", offset=offset, source=source)
            return (decoded_str, offset)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue

    logger.warning("bruteforce_decode_failed", source=source, data_length=len(raw_bytes))
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
        if any(c in response_text for c in '+/=') or len(response_text) > 1000:
            binary_data = base64.b64decode(response_text)
            result = _bruteforce_offset_decode(binary_data, source="base64")
            if result:
                return result[0]
    except Exception:
        pass

    # Try 3: Direct hex offset decode
    cleaned = response_text.strip()
    if all(c in '0123456789abcdefABCDEF' for c in cleaned):
        try:
            encoded_bytes = bytes.fromhex(cleaned)
            result = _bruteforce_offset_decode(encoded_bytes, source="hex")
            if result:
                return result[0]
        except Exception:
            pass

    return None


def decode_charts_response(response_text: str) -> Optional[str]:
    """
    Decode seats.io charts/published response.
    Uses the same decoding logic as object-statuses API.
    """
    if not response_text:
        return None

    # Try 1: Direct JSON parse
    try:
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError:
        pass

    # Try 2: Hex offset decode with bruteforce
    cleaned = response_text.strip()
    if all(c in '0123456789abcdefABCDEF' for c in cleaned):
        try:
            encoded_bytes = bytes.fromhex(cleaned)
            result = _bruteforce_offset_decode(encoded_bytes, source="hex")
            if result:
                return result[0]
        except Exception as e:
            logger.debug("charts_hex_decode_failed", error=str(e))

    # Try 3: Base64 decode + bruteforce byte offset
    try:
        import base64
        if any(c in response_text for c in '+/=') or len(response_text) > 1000:
            binary_data = base64.b64decode(response_text)
            result = _bruteforce_offset_decode(binary_data, source="base64")
            if result:
                return result[0]
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
