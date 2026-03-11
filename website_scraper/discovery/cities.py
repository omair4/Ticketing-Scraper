"""
City enumeration for AdmitONE events
"""

# Known cities based on website exploration (IMPLEMENTATION_PLAN.md)
# Updated from actual events found in GraphQL queries
KNOWN_CITIES = [
    "adelaide",
    "auckland",
    "austin",
    "belgrave",
    "brisbane",
    "brookville",
    "calgary",
    "caloundra",
    "canberra",
    "canberra-city",
    "canoe",
    "chicago",
    "chilliwack",
    "cumberland",
    "dallas",
    "edmonton",
    "fort-mcmurray",
    "geelong",
    "gibsons",
    "halifax",
    "hamilton",
    "kelowna",
    "london",
    "los-angeles",
    "markham",
    "melbourne",
    "montreal",
    "nanaimo",
    "new-farm",
    "new-york",
    "newcastle",
    "newtown",
    "niagara-on-the-lake",
    "north-bay",
    "northcote",
    "norwood",
    "orillia",
    "ottawa",
    "perth",
    "peterborough",
    "qualicum-beach",
    "quebec",
    "richmond",
    "san-francisco",
    "saskatoon",
    "seattle",
    "shawnigan-lake",
    "south-brisbane",
    "southbank",
    "st-kilda",
    "subiaco",
    "sudbury",
    "surfers-paradise",
    "surrey",
    "sutherland",
    "sydney",
    "toronto",
    "torrensville",
    "vancouver",
    "victoria",
    "whistler",
    "white-rock",
    "windsor",
    "winnipeg",
]

# Slug (URL) -> EventCity enum value (title case). Events query requires EventCity enum.
# Only includes cities that are valid EventCity enum values (verified via GraphQL API)
CITY_SLUG_TO_EVENT_CITY: dict[str, str] = {
    "toronto": "Toronto",
    "calgary": "Calgary",
    "hamilton": "Hamilton",
    "montreal": "Montreal",
    "vancouver": "Vancouver",
    "edmonton": "Edmonton",
    "ottawa": "Ottawa",
    "winnipeg": "Winnipeg",
    # Removed: "south-brisbane" (not a valid EventCity enum - causes 422)
    # Removed: "norwood" (not a valid EventCity enum - causes 422)
    # Note: "st-catharines" removed as it's not verified - add back if needed
}

# Canada geographic center (for Canada-wide radius queries)
CANADA_CENTER_LAT = 0.0
CANADA_CENTER_LON = 0.0
CANADA_RADIUS_KM = 20050

# Page size for GraphQL pagination
PAGE_SIZE = 100
