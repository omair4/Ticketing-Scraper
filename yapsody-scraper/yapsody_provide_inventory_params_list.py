"""
Lambda helper: Build list of { event_id } payloads for Step Function Map state.

Reads event_discovery WHERE scraper_type = 'yapsody_seatsio' AND event_status = 'active'.

Input:  {} (no params)
Output: { events: [{event_id, time_frame}], total: N }
"""
import sys
sys.path.append('/mnt/access')

from creds import get_db_info
import pymysql

DATABASE = "yapsody"


def lambda_handler(event, context):
    time_frame = event.get("time_frame", 0)

    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=DATABASE,
        autocommit=True,
    )

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT event_id
                FROM event_discovery
                WHERE scraper_type = 'yapsody_seatsio'
                  AND event_status = 'active'
                  AND workspace_key IS NOT NULL
                  AND event_key IS NOT NULL
                  AND chart_key IS NOT NULL
                  AND pricing_list IS NOT NULL
                ORDER BY event_datetime ASC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    events = [{"event_id": r["event_id"], "time_frame": time_frame} for r in rows]
    print(f"Found {len(events)} active yapsody_seatsio events")

    return {
        "events": events,
        "total":  len(events),
    }
