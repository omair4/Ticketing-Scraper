import sys
sys.path.append('/mnt/access')

from creds import get_db_info
from creds import get_proxy_info

import pymysql
from datetime import date


database = "mgmresorts"


def lambda_handler(event, context):
    """
    Reads all active internal shows from event_discovery and
    returns a list of input payloads for mgmresorts_event_schedule Lambda.

    Used as a Step Function Map state input generator.
    """
    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host, user=user, password=password,
        database=database, port=3306, charset='utf8mb4'
    )

    start_date = event.get("start_date", date.today().strftime("%Y-%m-%d"))

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT show_id, property_id, show_name, venue_name, booking_url
                FROM event_discovery
                WHERE is_active = 1
                  AND scraper_type = 'mgm_internal'
                ORDER BY show_id
            """)
            rows = cursor.fetchall()
    finally:
        conn.close()

    params_list = [
        {
            "show_id":     row["show_id"],
            "property_id": row["property_id"],
            "show_name":   row["show_name"],
            "venue_name":  row["venue_name"],
            "booking_url": row["booking_url"],
            "start_date":  start_date,
        }
        for row in rows
    ]

    print(f"Returning {len(params_list)} shows for schedule refresh")
    return params_list
