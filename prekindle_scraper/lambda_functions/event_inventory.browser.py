# scrapers/_seatsio_browser.py

from tempfile import mkdtemp
from seleniumwire import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions
# from selenium.webdriver.common.by import By

# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC

from creds import get_proxy_info

def driversetup():
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

# temp_dir = mkdtemp(dir="/tmp")