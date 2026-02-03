"""
City enumeration for AdmitONE events
"""

# Known cities based on website exploration (IMPLEMENTATION_PLAN.md)
KNOWN_CITIES = [
    "toronto",
    "calgary",
    "hamilton",
    "montreal",
    "vancouver",
    "edmonton",
    "ottawa",
    "winnipeg",
    "south-brisbane",
    "norwood",
    "st-catharines",
]

# Slug (URL) -> EventCity enum value (title case). Events query requires EventCity enum.
CITY_SLUG_TO_EVENT_CITY: dict[str, str] = {
    "toronto": "Toronto",
    "calgary": "Calgary",
    "hamilton": "Hamilton",
    "montreal": "Montreal",
    "vancouver": "Vancouver",
    "edmonton": "Edmonton",
    "ottawa": "Ottawa",
    "winnipeg": "Winnipeg",
    "south-brisbane": "South Brisbane",
    "norwood": "Norwood",
    "st-catharines": "St Catharines",
}

# Canada geographic center (for Canada-wide radius queries)
CANADA_CENTER_LAT = 56.13
CANADA_CENTER_LON = -106.35
CANADA_RADIUS_KM = 5000

# Page size for GraphQL pagination
PAGE_SIZE = 100
