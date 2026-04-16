"""
Lambda 3: Build list of shows that need schedule refresh.

Input:  {"time_frame": int}
Output: [{"show_slug": str, "scraper_type": str}, ...]
"""
import sys
sys.path.append('/mnt/access')

import pymysql
from creds import get_db_info

DATABASE = "tix4"


def lambda_handler(event, context):
    host, user, password = get_db_info()
    conn = pymysql.connect(host=host, user=user, password=password,
                           database=DATABASE, charset="utf8mb4")
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT show_slug, scraper_type
                FROM event_discovery
                WHERE is_active = 1
                  AND scraper_type IS NOT NULL
                ORDER BY show_slug
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    print(f"[schedule_params] {len(rows)} active shows with scraper_type")
    return list(rows)
