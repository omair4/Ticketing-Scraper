#!/usr/bin/env python3
"""
Test script to verify scraper installation and basic functionality.

Run this to check if all dependencies are installed correctly.
"""

import sys
import json
from decimal import Decimal


def test_imports():
    """Test that all required modules can be imported"""
    print("Testing imports...")
    
    errors = []
    
    # Core dependencies
    try:
        import playwright
        print("  ✓ playwright")
    except ImportError as e:
        errors.append(f"playwright: {e}")
        print("  ✗ playwright")
    
    try:
        import httpx
        print("  ✓ httpx")
    except ImportError as e:
        errors.append(f"httpx: {e}")
        print("  ✗ httpx")
    
    try:
        import pydantic
        print("  ✓ pydantic")
    except ImportError as e:
        errors.append(f"pydantic: {e}")
        print("  ✗ pydantic")
    
    try:
        import structlog
        print("  ✓ structlog")
    except ImportError as e:
        errors.append(f"structlog: {e}")
        print("  ✗ structlog")
    
    # Local modules
    try:
        from models import Seat, ScrapeResult, EventInfo, SeatStatus
        print("  ✓ models")
    except ImportError as e:
        errors.append(f"models: {e}")
        print("  ✗ models")
    
    try:
        from config import ScraperConfig, DEFAULT_CONFIG
        print("  ✓ config")
    except ImportError as e:
        errors.append(f"config: {e}")
        print("  ✗ config")
    
    try:
        from utils.decoder import decode_hex_offset, try_decode_response
        print("  ✓ utils.decoder")
    except ImportError as e:
        errors.append(f"utils.decoder: {e}")
        print("  ✗ utils.decoder")
    
    try:
        from utils.exporters import export_to_json, export_to_csv
        print("  ✓ utils.exporters")
    except ImportError as e:
        errors.append(f"utils.exporters: {e}")
        print("  ✗ utils.exporters")
    
    try:
        from scrapers import SeatsIOScraper, DirectAPIScraper, get_scraper_for_scenario
        print("  ✓ scrapers")
    except ImportError as e:
        errors.append(f"scrapers: {e}")
        print("  ✗ scrapers")
    
    try:
        from browser import BrowserAutomation
        print("  ✓ browser")
    except ImportError as e:
        errors.append(f"browser: {e}")
        print("  ✗ browser")
    
    return errors


def test_hex_decoder():
    """Test the hex decoding functionality"""
    print("\nTesting hex decoder...")
    
    from utils.decoder import decode_hex_offset
    
    # Test with a simple encoded string
    # This simulates seats.io encoding where each byte is offset by 41
    original = '{"test": true}'
    
    # Encode it (add 41 to each byte and convert to hex)
    encoded = ''.join(f'{ord(c) + 41:02x}' for c in original)
    
    # Decode it back
    decoded = decode_hex_offset(encoded, offset=41)
    
    if decoded == original:
        print(f"  ✓ Hex decoding works correctly")
        return []
    else:
        print(f"  ✗ Hex decoding failed: got '{decoded}' expected '{original}'")
        return ["Hex decoder test failed"]


def test_models():
    """Test the Pydantic models"""
    print("\nTesting models...")
    
    from models import Seat, SeatStatus, EventInfo, ScrapeResult
    
    errors = []
    
    # Test Seat model
    try:
        seat = Seat(
            section="Orchestra 1",
            row="A",
            seat="1",
            price="59.50",
            service_fee="16.75",
            status=SeatStatus.AVAILABLE,
            price_level="Orchestra 1"
        )
        
        assert seat.price == Decimal("59.50")
        assert seat.total_price == Decimal("76.25")
        assert seat.status == SeatStatus.AVAILABLE
        print("  ✓ Seat model")
    except Exception as e:
        errors.append(f"Seat model: {e}")
        print(f"  ✗ Seat model: {e}")
    
    # Test EventInfo model
    try:
        event = EventInfo(
            event_id="123",
            event_name="Test Event",
            venue_name="Test Venue",
            event_url="https://example.com"
        )
        assert event.event_name == "Test Event"
        print("  ✓ EventInfo model")
    except Exception as e:
        errors.append(f"EventInfo model: {e}")
        print(f"  ✗ EventInfo model: {e}")
    
    # Test ScrapeResult model
    try:
        result = ScrapeResult(
            event=event,
            scenario_type="seatsio",
            seats=[seat]
        )
        assert result.total_seats_found == 1
        assert result.min_price == Decimal("59.50")
        print("  ✓ ScrapeResult model")
    except Exception as e:
        errors.append(f"ScrapeResult model: {e}")
        print(f"  ✗ ScrapeResult model: {e}")
    
    return errors


def test_scraper_factory():
    """Test the scraper factory"""
    print("\nTesting scraper factory...")
    
    from scrapers import get_scraper_for_scenario, SeatsIOScraper, DirectAPIScraper
    
    errors = []
    
    scraper1 = get_scraper_for_scenario('seatsio')
    if isinstance(scraper1, SeatsIOScraper):
        print("  ✓ SeatsIO scraper selection")
    else:
        errors.append("Wrong scraper for 'seatsio'")
        print("  ✗ SeatsIO scraper selection")
    
    scraper2 = get_scraper_for_scenario('direct_api')
    if isinstance(scraper2, DirectAPIScraper):
        print("  ✓ DirectAPI scraper selection")
    else:
        errors.append("Wrong scraper for 'direct_api'")
        print("  ✗ DirectAPI scraper selection")
    
    return errors


def test_section_normalization():
    """Test section name normalization"""
    print("\nTesting section normalization...")
    
    from scrapers.base import BaseScraper
    from scrapers import SeatsIOScraper
    
    scraper = SeatsIOScraper()
    
    test_cases = [
        ("MEZZL", "Mezzanine Left"),
        ("ORCHR", "Orchestra Right"),
        ("BALCL", "Balcony Left"),
        ("Orchestra 1", "Orchestra 1"),  # Should remain unchanged
    ]
    
    errors = []
    for raw, expected in test_cases:
        result = scraper.normalize_section_name(raw)
        if result == expected:
            print(f"  ✓ '{raw}' → '{result}'")
        else:
            errors.append(f"'{raw}' should be '{expected}', got '{result}'")
            print(f"  ✗ '{raw}' → '{result}' (expected '{expected}')")
    
    return errors


def main():
    """Run all tests"""
    print("=" * 60)
    print("AdmitONE Scraper - Installation Test")
    print("=" * 60)
    
    all_errors = []
    
    all_errors.extend(test_imports())
    all_errors.extend(test_hex_decoder())
    all_errors.extend(test_models())
    all_errors.extend(test_scraper_factory())
    all_errors.extend(test_section_normalization())
    
    print("\n" + "=" * 60)
    if all_errors:
        print(f"FAILED - {len(all_errors)} error(s) found:")
        for err in all_errors:
            print(f"  - {err}")
        print("\nPlease fix these issues before running the scraper.")
        return 1
    else:
        print("ALL TESTS PASSED ✓")
        print("\nYou can now run the scraper:")
        print('  python main.py "https://admitone.com/events/..."')
        return 0


if __name__ == '__main__':
    sys.exit(main())
