from creds import get_db_info
from creds import get_proxy_info
from creds import json
from bs4 import BeautifulSoup
import io

# import PyPDF2
import pandas as pd
# import img2pdf
import random
import os

from tempfile import mkdtemp
from seleniumwire import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import boto3
from botocore.exceptions import ClientError
import pymysql
import requests
from bs4 import BeautifulSoup

import re
from datetime import datetime, timedelta
import base64
import sqlalchemy
from sqlalchemy import create_engine, text
import pymysql

from urllib.parse import urljoin
import time


def lambda_handler(event,context):

    host, user, password = get_db_info()
    port = 3306

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = {
        'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
        'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
    }

    def initialise_driver():
        # Retrieve proxy information
        proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
        proxy = {
            'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
            'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
        }

        # Configure Selenium Wire options for proxy
        seleniumwire_options = {
            'proxy': {
                'http': proxy['http'],
                'https': proxy['https']
            }
        }

        # Chrome options
        options = ChromeOptions()
        # options.add_argument("--headless=new")
        options.add_argument("--headless")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--incognito")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--no-zygote")
        options.add_argument("--single-process")
        options.add_argument(f"--user-data-dir={mkdtemp()}")
        options.add_argument(f"--data-path={mkdtemp()}")
        options.add_argument(f"--disk-cache-dir={mkdtemp()}")
        options.add_argument("--remote-debugging-pipe")
        options.add_argument("--verbose")
        options.add_argument("--log-path=/tmp")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36")

        options.binary_location = "/opt/chrome/chrome-linux64/chrome"

        # ChromeDriver service
        service = Service(
            executable_path="/opt/chrome-driver/chromedriver-linux64/chromedriver",
            service_log_path="/tmp/chromedriver.log"
        )

        # Initialize Selenium Wire WebDriver with proxy support
        driver = webdriver.Chrome(
            service=service,
            options=options,
            seleniumwire_options=seleniumwire_options
        )

        # Set initial window size
        initial_width = 1280
        initial_height = 1024
        driver.set_window_size(initial_width, initial_height)

        return driver
    
    temp_dir = mkdtemp(dir="/tmp")


    # Connect to MySQL
    conn = pymysql.connect(
        host= host,
        user=user,
        password=password,
        database='admitone',
        autocommit=True
    )

    ticket_url = event["ticket_url"]
    eventId = event["eventId"]
    if eventId == '-2852852648632716080':
        return
    event_name = event["event_name"]
    venue_name = event["venue_name"]
    event_datetime = event["event_datetime"]
    scraper_type = None
    pricing_list = None
    

    print(
        f'Attempting EVENT: {event_name} @ {venue_name} - {event_datetime}'
    )

    if 'cancelled' in event_name:
        return "Event is cancelled"

    print("=== DRIVER SETUP ===")
    driver = initialise_driver()

    try:
        print(f"\nNavigating to event:\n{ticket_url}")
        driver.get(ticket_url)

        print("Sleeping 5s for initial JS load...")
        time.sleep(5)

        print("Current URL:", driver.current_url)

        promo_page_html = driver.page_source
        print("Captured Promo HTML")

        print("\n=== CHECKING SOLD OUT / WAITLIST STATE ===")
        sold_out = False

        elements = driver.find_elements(By.XPATH, "//*[contains(text(),'Sold Out') or contains(text(),'Waitlist')]")

        for el in elements:
            txt = el.text.lower().strip()
            if "sold out" in txt or "waitlist" in txt:
                print("🚨 SOLD OUT / WAITLIST DETECTED")
                print("Element:", el.tag_name)
                print("Text:", txt)
                sold_out = True
                break

        if sold_out:
            scenario = "sold_out"
            print("\n=== FINAL RESULT ===")
            print("Scenario:", scenario)

            return {
                'status':'event is sold out'
            }
            raise ValueError("Scenario: sold_out")


        soup = BeautifulSoup(promo_page_html, "html.parser")

        event_info = {}

        # --------------------------------------------------
        # 1️⃣ JSON-LD (BEST SOURCE)
        # --------------------------------------------------
        json_ld = soup.find("script", {"type": "application/ld+json"})
        if json_ld:
            data = json.loads(json_ld.string)

            event_info["event_name"] = data.get("name")
            event_info["event_type"] = data.get("@type")
            event_info["event_date"] = data.get("startDate")

            # Venue
            location = data.get("location", {})
            address = location.get("address", {})

            event_info["venue_name"] = location.get("name")
            # event_info["venue_street"] = address.get("streetAddress")
            event_info["venue_city"] = address.get("addressLocality")
            event_info["venue_state"] = address.get("addressRegion")
            # event_info["venue_zip"] = address.get("postalCode")

            # Pricing
            offers = data.get("offers", {})
            event_info["low_price"] = offers.get("lowPrice")
            event_info["high_price"] = offers.get("highPrice")
            event_info["currency"] = offers.get("priceCurrency")
            event_info["availability"] = offers.get("availability")
            validFrom = offers.get("validFrom")
            if validFrom:
                validFrom_utc = (
                    pd.to_datetime(validFrom, utc=True)
                    .tz_convert("UTC")
                    .tz_localize(None)
                    .isoformat()
                )
                event_info["valid_from_utc"] = validFrom_utc

        print(f'event info: {event_info}')
        event_name_ld = event_info.get("event_name")

        if event_name_ld and (
            "cancelled" in event_name_ld.lower() or
            "canceled" in event_name_ld.lower()
        ):
            return "Event is cancelled"
        
        if driver.title == 'Not Available':

            soup = BeautifulSoup(promo_page_html, "html.parser")

            meta_desc = soup.find("meta", {"name": "description"})

            if meta_desc:
                message = meta_desc.get("content", "").lower()
                print("Meta message:", message)

                if "sales are currently not available" in message:
                    print("status", "sales_not_available")

                    return "Event is not available"


        # --------------------------------------------------
        # Find checkout ID
        # --------------------------------------------------
        find_tickets_id= None
        match = re.search(r'checkout/id/(-?\d+)', promo_page_html)

        if match:
            find_tickets_id = match.group(1) 
        else:
            raise ValueError("find_tickets_id not found")

        print(find_tickets_id)

        if not find_tickets_id:
            raise ValueError("find_tickets_id not found")


        # --------------------------------------------------
        # Click Buy / Get Tickets
        # --------------------------------------------------
        print("\n=== ATTEMPTING BUY / GET TICKETS CLICK ===")
        approved_button_text = ["buy tickets", "get tickets","tickets | sell out warning!"]

        wait = WebDriverWait(driver, 30)

        # Wait until at least one buybutton is present
        wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[contains(@class,'buybutton')]")
            )
        )

        buttons = driver.find_elements(By.XPATH, "//a[contains(@class,'buybutton')]")

        clicked = False

        for b in buttons:
            text = b.text.strip().lower()
            print("Found button:", text)

            if text in approved_button_text:
                print("Approved — clicking")
                b.click()
                clicked = True
                break

        if not clicked:
            print("No approved_button_text button found")
            raise ValueError("No approved_button_text button found")
        print("Current URL:", driver.current_url)

        print("Sleeping 4s after buy click...")
        time.sleep(4)


        # --------------------------------------------------
        # Handle new tab if queue
        # --------------------------------------------------
        timeout = 30
        start = time.time()

        while True:
            url = driver.current_url

            # If still in queue, keep waiting
            if "q.prekindle" in url:
                print("Still in queue:", url)
                time.sleep(2)
                continue

            # If correct ticket page → proceed
            if "find-tickets" in url or "checkout" in url:
                print("Reached ticket page:", url)
                break

            # If timeout exceeded → raise error
            if time.time() - start > timeout:
                raise Exception(f"Did not reach ticket page. Current URL: {url}")

            time.sleep(1)

        time.sleep(5)  # allow dynamic content to load

        # --------------------------------------------------
        # Look for seatsio iframe
        # --------------------------------------------------
        print("\n=== SEARCHING FOR SEATSIO IFRAME ===")

        # wait for seatsio_iframe
        has_seatsio = None
        has_seatsio = driver.find_elements(By.XPATH, "//iframe[contains(@src,'seatsio.net')]")
        if has_seatsio:
            print("Seats.io iframe found")
            time.sleep(5)
        else:
            print("No Seats.io iframe found")
        ticket_page_html = driver.page_source
        if '' == ticket_page_html:
            print("Sleeping again to capture html")
            time.sleep(5)


        # --------------------------------------------------
        # Scenario detection
        # --------------------------------------------------
        print("\n=== DETECTING SCENARIO ===")


        # 1️⃣ seats.io via network
        has_seatsio_response = any(
            request.response and "seatsio.net" in request.url
            for request in driver.requests
        )

        # also check iframe fallback
        has_seatsio_iframe = driver.find_elements(By.XPATH, "//iframe[contains(@src,'seatsio.net')]")

        if has_seatsio_response or has_seatsio_iframe:
            scenario = "seatsio"
            print("Scenario:", scenario)

            pricing = {}

            soup = BeautifulSoup(ticket_page_html, "html.parser")

            scripts = soup.find_all("script")

            pricing_block = None

            for script in scripts:
                if script.string and "pricing:" in script.string:
                    pricing_block = script.string
                    break

            if not pricing_block:
                raise ValueError("No pricing found")
            else:
                # Extract pricing array
                print("Pricing found")
                match = re.search(r"pricing\s*:\s*(\[[\s\S]*?\])\s*,\s*objectWithoutPricingSelectable", pricing_block)

                if not match:
                    print("Pricing pattern not matched")
                else:
                    pricing_str = match.group(1)

                    # Convert JS to valid JSON
                    pricing_str = pricing_str.replace("'", '"')
                    pricing_str = re.sub(r",\s*([}\]])", r"\1", pricing_str)

                    pricing_list = json.loads(pricing_str)

                    # remove descriptions
                    for item in pricing_list:
                        for tt in item.get("ticketTypes", []):
                            tt.pop("description", None)
                print("pricing_list:", pricing_list)
                if len(pricing_list)==0:
                    raise ValueError(f'no pricing list found!')





        # 2️⃣ SVG seatmap
        elif "<svg" in ticket_page_html.lower() and "wicketpath" in ticket_page_html.lower():
            scenario = "svg_seatmap"
            print("Scenario:", scenario)

        # 3️⃣ General admission
        elif (
            driver.find_elements(By.TAG_NAME, "select") or
            "total price" in ticket_page_html.lower() or
            "howmanypanel" in ticket_page_html.lower()
        ):
            scenario = "general_admission"
            print("Scenario:", scenario)

        else:
            scenario = "unknown"
            print("Scenario:", scenario)



        print("\n=== FINAL RESULT ===")
        print("Scenario:", scenario)

        if scenario == "unknown":
            raise ValueError(f'UNKNOWN SCENARIO')
        
        # --------------------------------------------------
        # Get CONFIG
        # --------------------------------------------------

        workspace_config = {}

        soup = BeautifulSoup(ticket_page_html, "html.parser")

        scripts = soup.find_all("script")

        seatsio_script = None

        for script in scripts:
            if script.string and "new seatsio.SeatingChart" in script.string:
                seatsio_script = script.string
                break

        if seatsio_script:

            # workspaceKey (or publicKey fallback)
            wk_match = re.search(
                r"(?:workspaceKey|publicKey)\s*:\s*['\"]([^'\"]+)['\"]",
                seatsio_script
            )
            if wk_match:
                workspace_config["workspace_key"] = wk_match.group(1)

            # event key
            ek_match = re.search(
                r"event\s*:\s*['\"]([^'\"]+)['\"]",
                seatsio_script
            )
            if ek_match:
                workspace_config["event_key"] = ek_match.group(1)

        print("workspace_config: ",workspace_config)
        try:
            workspace_key = workspace_config['workspace_key']
            event_key = workspace_config['event_key']
        except:
            if scenario == "seatsio":
                raise ValueError(f'no workspace key or event_key found: {workspace_config}')
            else:
                workspace_key = None
                event_key = None

        if scenario == "seatsio":

            for r in driver.requests:
                if r.response and (
                    "charts/published" in r.url or
                    "/charts/" in r.url
                ):
                    charts_request = r
                    break


            charturl = charts_request.url
            charts_workspace_key, chart_key, published_key = None, None, None

            match = re.search(
                r"/public/(?P<workspace_key>[^/]+)/charts/(?P<chart_key>[^/]+)/published/(?P<published_key>[^/]+)",
                charturl
            )

            if match:
                charts_workspace_key = match.group("workspace_key")
                chart_key = match.group("chart_key")
                published_key = match.group("published_key")

            #throw error 
            if not charts_workspace_key:
                raise ValueError("No charts_workspace_key found")
            if charts_workspace_key != workspace_config["workspace_key"]:
                raise ValueError("Workspace keys do not match")
            if not chart_key:
                raise ValueError("No chart_key found")
            if not published_key:
                raise ValueError("No published_key found")
        else:
            charts_workspace_key, chart_key, published_key = None, None, None

    finally:
        print("\n=== DRIVER CLEANUP ===")
        driver.quit()


    # --------------------------------------------------
    # Update MySQL with detected scenario
    # --------------------------------------------------
    print("\n=== UPDATING MYSQL SCENARIO ===")

    conn = pymysql.connect(
        host=host,
        user=user,
        password=password,
        database="prekindle",
        autocommit=True,
    )

    try:
        with conn.cursor() as cur:
            sql = """
                UPDATE event_discovery
                SET scraper_type = %s,
                    find_tickets_id = %s,
                    workspace_key = %s,
                    event_key = %s,
                    chart_key  = %s,
                    published_key = %s,
                    pricing_list = %s,
                    modified_at = UTC_TIMESTAMP()
                WHERE eventId = %s
            """
            cur.execute(
                sql,
                (
                    scenario,
                    find_tickets_id,
                    workspace_key,
                    event_key,
                    chart_key,
                    published_key,
                    json.dumps(pricing_list),
                    eventId
                ),
            )
            print(f"scraper_type updated → {scenario}")
    finally:
        conn.close()

    return {
        "statusCode": 200,
        "eventId": eventId,
        "find_tickets_id":find_tickets_id,
        "event_name": event_name,
        "venue_name": venue_name,
        "event_datetime": event_datetime,
        "scraper_type": scenario,
        "workspace_key":workspace_key,
        "pricing_list":pricing_list,
        "event_key": event_key
    }