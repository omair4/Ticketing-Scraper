import sys
sys.path.append('/mnt/access')

from creds import get_db_info

import pymysql
from datetime import date, timedelta


database = "mgmresorts"


def lambda_handler(event, context):
    """
    Reads upcoming events from event_schedule and returns
    inventory scrape payloads for event_inventory_main Lambda.

    Input event params:
      - days_ahead: how many days of events to scrape (default 30)
      - time_frame: integer label for this scrape run (default 0)
    """
    days_ahead = event.get("days_ahead", 30)
    time_frame = event.get("time_frame", 0)

    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host, user=user, password=password,
        database=database, port=3306, charset='utf8mb4'
    )

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT
                    s.event_id,
                    s.property_id,
                    s.event_date,
                    s.event_time,
                    d.show_name AS event_name,
                    d.venue_name,
                    d.booking_url AS ticket_url
                FROM event_schedule s
                JOIN event_discovery d ON s.show_id = d.show_id
                WHERE s.event_date BETWEEN %s AND %s
                  AND d.is_active = 1
                  AND d.scraper_type = 'mgm_internal'
                ORDER BY s.event_date, s.event_time
            """, (today.strftime("%Y-%m-%d"), cutoff.strftime("%Y-%m-%d")))
            rows = cursor.fetchall()
    finally:
        conn.close()

    params_list = [
        {
            "eventId":        row["event_id"],
            "property_id":    row["property_id"],
            "event_name":     row["event_name"],
            "venue_name":     row["venue_name"],
            "event_datetime": f"{row['event_date']} {row['event_time']}",
            "ticket_url":     row["ticket_url"],
            "time_frame":     time_frame,
        }
        for row in rows
    ]

    print(f"Returning {len(params_list)} events for inventory scrape")
    return params_list
