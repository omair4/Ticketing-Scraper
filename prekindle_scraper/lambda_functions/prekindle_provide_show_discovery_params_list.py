import sys
sys.path.append('/mnt/access')
# ^^^^^^^^ MUST IMPORT

from customrequests import make_request
from customrequests import sleep
from creds import get_proxy_info
import pymysql
import json
import requests
import json
import pandas as pd
# from bs4 import BeautifulSoup
# import random
import time
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


database = "prekindle"

def lambda_handler(event, context):

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = {
        'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
        'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
    }

    SEARCH_API_URL = "https://web.prekindle.com/api/v1/event/search"
    TIMEZONE = "America/New_York"
    LIMIT = 12

    now_utc = datetime.now(timezone.utc)
    start_time = now_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')

    params = {
        "page": 0,
        "limit": LIMIT,
        "timeZone": TIMEZONE,
        "startTime": start_time,
    }

    response = requests.get(SEARCH_API_URL, params=params, proxies=proxy)
    response.raise_for_status()
    data = response.json()

    total_pages = data.get("totalPages", 0)

    if total_pages == 0:
        raise ValueError("No pages found")

    pages = []

    for page in range(total_pages):
        pages.append({
            "page": page,
            "limit": LIMIT,
            "timeZone": TIMEZONE,
            "startTime": start_time
        })

    return {
        "pages": pages,
        "total_pages": total_pages
    }