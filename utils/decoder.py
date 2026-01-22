"""
Decoding utilities for obfuscated API responses
"""

import json
from typing import Optional, Any
import structlog

logger = structlog.get_logger()


def decode_seatsio_response(response_text: str) -> Optional[str]:
    """
    Decode seats.io object-statuses response.
    
    The response may be:
    1. Base64-encoded binary data that contains hex-encoded JSON with offset
    2. Direct hex-encoded JSON with offset
    3. Plain JSON
    
    Args:
        response_text: Raw response text
    
    Returns:
        Decoded JSON string or None if decoding fails
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
    
    # Try 2: Base64 decode first, then apply byte offset directly
    try:
        # Check if it looks like base64 (contains +, /, =)
        if any(c in response_text for c in '+/=') or len(response_text) > 1000:
            # Decode base64 to get binary data
            binary_data = base64.b64decode(response_text)
            # Apply offset directly to bytes (subtract 41 from each byte)
            decoded_bytes = bytes((b - 41) % 256 for b in binary_data)
            # Try to decode as UTF-8
            decoded = decoded_bytes.decode('utf-8')
            # Verify it's JSON
            json.loads(decoded)
            return decoded
    except Exception as e:
        pass
    
    # Try 3: Direct hex offset decode (if it's already hex)
    if all(c in '0123456789abcdefABCDEF' for c in response_text.strip()):
        try:
            decoded = decode_hex_offset(response_text.strip(), offset=41)
            if decoded:
                json.loads(decoded)  # Verify
                return decoded
        except:
            pass
    
    return None


def decode_charts_response(response_text: str) -> Optional[str]:
    """
    Decode seats.io charts/published response.
    
    The response is hex-encoded JSON with offset 0x29 (41).
    Uses the same decoding logic as object-statuses API.
    
    Args:
        response_text: Raw response text (hex-encoded)
    
    Returns:
        Decoded JSON string or None if decoding fails
    """
    if not response_text:
        return None
    
    # Try 1: Direct JSON parse (in case it's already decoded)
    try:
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError:
        pass
    
    # Try 2: Hex offset decode with offset 0x29 (41)
    cleaned = response_text.strip()
    if all(c in '0123456789abcdefABCDEF' for c in cleaned):
        try:
            decoded = decode_hex_offset(cleaned, offset=0x29)  # 0x29 = 41
            if decoded:
                # Verify it's valid JSON
                json.loads(decoded)
                return decoded
        except Exception as e:
            logger.debug("charts_hex_decode_failed", error=str(e))
            pass
    
    # Try 3: Use the same logic as seatsio_response (base64 + offset)
    try:
        import base64
        if any(c in response_text for c in '+/=') or len(response_text) > 1000:
            binary_data = base64.b64decode(response_text)
            decoded_bytes = bytes((b - 0x29) % 256 for b in binary_data)
            decoded = decoded_bytes.decode('utf-8')
            json.loads(decoded)  # Verify
            return decoded
    except Exception:
        pass
    
    return None


def decode_hex_offset(hex_string: str, offset: int = 41) -> str:
    """
    Decode hex-encoded string with byte offset subtraction.
    
    This is used by seats.io API responses which encode data as hex
    with each byte offset by a fixed value (typically 41).
    
    Args:
        hex_string: Hex-encoded string
        offset: Value to subtract from each byte (default 41)
    
    Returns:
        Decoded string
    """
    try:
        # Convert hex string to bytes
        encoded_bytes = bytes.fromhex(hex_string)
        
        # Subtract the offset from each byte and convert to character
        decoded_text = "".join(chr(b - offset) for b in encoded_bytes)
        
        return decoded_text
    except Exception as e:
        logger.error("hex_decode_failed", error=str(e), hex_length=len(hex_string) if hex_string else 0)
        return ""


def try_decode_response(response_text: str) -> tuple[str, str]:
    """
    Attempt to decode a potentially encoded response.
    
    Returns:
        Tuple of (decoded_text, encoding_type)
        encoding_type: 'plain', 'hex_offset', or 'unknown'
    """
    if not response_text:
        return "", "empty"
    
    # Check if it's already valid JSON
    try:
        json.loads(response_text)
        return response_text, "plain"
    except json.JSONDecodeError:
        pass
    
    # Check if it looks like hex (all hex characters)
    cleaned = response_text.strip()
    if all(c in '0123456789abcdefABCDEF' for c in cleaned):
        # Try hex offset decoding with common offsets
        for offset in [41, 0, 32, 64]:
            try:
                decoded = decode_hex_offset(cleaned, offset)
                # Validate it's JSON
                json.loads(decoded)
                logger.info("hex_decode_success", offset=offset)
                return decoded, f"hex_offset_{offset}"
            except (json.JSONDecodeError, ValueError):
                continue
    
    # Try direct byte interpretation
    try:
        decoded = bytes.fromhex(cleaned).decode('utf-8')
        json.loads(decoded)
        return decoded, "hex_utf8"
    except:
        pass
    
    return response_text, "unknown"


def parse_json_safe(text: str) -> Optional[Any]:
    """
    Safely parse JSON, attempting decoding if needed.
    
    Returns:
        Parsed JSON object or None if parsing fails
    """
    if not text:
        return None
    
    decoded, encoding_type = try_decode_response(text)
    
    if encoding_type == "unknown":
        logger.warning("could_not_decode_response", sample=text[:100] if text else "")
        return None
    
    try:
        return json.loads(decoded)
    except json.JSONDecodeError as e:
        logger.error("json_parse_failed", error=str(e), encoding_type=encoding_type)
        return None


def extract_event_key_from_url(url: str) -> Optional[str]:
    """
    Extract event key from seats.io URL.
    
    Example URL:
    https://cdn-eu.seatsio.net/system/public/{public_key}/events/object-statuses?event_key={event_key}
    """
    import re
    from urllib.parse import urlparse, parse_qs
    
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        
        if 'event_key' in params:
            return params['event_key'][0]
        
        # Try to extract from path
        match = re.search(r'/events/([^/\?]+)', url)
        if match:
            return match.group(1)
    except Exception as e:
        logger.error("event_key_extraction_failed", url=url, error=str(e))
    
    return None


def extract_event_id_from_url(url: str) -> Optional[str]:
    """
    Extract event ID from AdmitONE URLs.
    
    Examples:
    - https://admitone.com/events/.../681d11117d3a4df5fd2d2c24
    - https://tickets.admitone.com/events/144917
    - https://embed.admitonelive.com/api/events/seated/10031723
    """
    import re
    
    patterns = [
        r'/events/seated/(\d+)',  # embed.admitonelive.com
        r'/events/(\d+)',          # tickets.admitone.com
        r'/([a-f0-9]{24})$',       # MongoDB ObjectId at end of URL
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None
