"""
Lambda 5: Build list of events that need inventory scraping.

Input:  {"time_frame": int, "days_ahead": int}
Output: [{"eventId": str, "show_slug": str, "scraper_type": str, ...}, ...]
"""
import sys
sys.path.append('/mnt/access')

import pymysql
from creds import get_db_info

DATABASE = "tix4"


def lambda_handler(event, context):
    days_ahead = event.get("days_ahead", 15)
    time_frame = event.get("time_frame", 0)

    host, user, password = get_db_info()
    conn = pymysql.connect(host=host, user=user, password=password,
                           database=DATABASE, charset="utf8mb4")
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT
                    es.event_id_token AS eventId,
                    es.show_slug,
                    COALESCE(es.scraper_type, ed.scraper_type) AS scraper_type,
                    es.buy_url_path,
                    ed.title AS event_name,
                    'tix4.com' AS venue_name,
                    es.event_datetime
                FROM event_schedule es
                JOIN event_discovery ed ON ed.show_slug = es.show_slug
                WHERE es.event_datetime BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL %s DAY)
                  AND ed.is_active = 1
                  AND COALESCE(es.scraper_type, ed.scraper_type) IS NOT NULL
                ORDER BY es.event_datetime
            """, (days_ahead,))
            rows = cur.fetchall()
    finally:
        conn.close()

    # Attach time_frame and stringify datetime
    for r in rows:
        r["time_frame"] = time_frame
        if r["event_datetime"]:
            r["event_datetime"] = r["event_datetime"].strftime("%Y-%m-%d %H:%M:%S")

    print(f"[inventory_params] {len(rows)} events in next {days_ahead} days")
    return list(rows)
