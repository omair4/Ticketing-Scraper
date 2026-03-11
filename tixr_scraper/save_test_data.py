"""One-off script to save captured Tixr event raw API data for testing."""
import json
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

dynamo2_data = {"id":173397,"venue":{"id":10083,"name":"Saber Cats Stadium ","address":{"streetAddress":"2055 Mowery Rd","city":"Houston","state":"TX","postalCode":"77051","countryCodeAlpha2":"US"},"seatingType":"OPEN","venueType":"STADIUM","capacity":4000},"status":"PUBLISHED","name":"Houston Dynamo 2 vs. Timbers2","startDate":1773615600000,"endDate":1773626400000,"eventCategoryName":"SportsEvent","allInPricing":False,"group":{"id":2686,"name":"Houston Dynamo","subdomain":"houstondynamofc"},"sales":[{"id":1931545,"eventId":173397,"state":"OPEN","category":"GA","type":"FIXED","seatingType":"OPEN","tiers":[{"id":2189550,"name":"General Admission","price":0,"minPrice":0,"maxPrice":0,"active":True}],"resaleState":"UNAVAILABLE"}],"seatsIoConfiguration":None,"url":"https://www.tixr.com/groups/houstondynamofc/events/houston-dynamo-2-vs-timbers2-173397","formattedStartDate":"March 15, 2026 at 06:00 PM CDT"}

wisp_data = {"id":175000,"venue":{"id":6086,"name":"Great American Music Hall","address":{"streetAddress":"859 O'Farrell St","city":"San Francisco","state":"CA","postalCode":"94109","countryCodeAlpha2":"US"},"seatingType":"OPEN","venueType":"CONCERT_HALL","capacity":1000},"status":"PUBLISHED","name":"Wisp","startDate":1771819200000,"endDate":1771833600000,"eventCategoryName":"Festival","allInPricing":False,"group":{"id":1854,"name":"Noise Pop Fest","subdomain":"noisepopfest"},"sales":[{"id":1953874,"eventId":175000,"state":"SOLD_OUT","category":"GA","type":"FIXED","seatingType":"OPEN","tiers":[{"id":2213005,"name":"GA Advance","price":57.78,"minPrice":57.78,"maxPrice":57.78,"active":False}],"resaleState":"UNAVAILABLE","optionalSaleRedirectUrl":"https://wl.eventim.us/event/wisp/679465?afflky=GreatAmericanMusicHall"}],"seatsIoConfiguration":None,"url":"https://www.tixr.com/groups/noisepopfest/events/wisp-175000","formattedStartDate":"February 22, 2026 at 08:00 PM PST"}

fisher_data = {"id":168656,"venue":{"id":9891,"name":"Festival Hall at Navy Pier","address":{"streetAddress":"600 E Grand Ave","city":"Chicago","state":"IL","postalCode":"60611","countryCodeAlpha2":"US"},"seatingType":"OPEN","venueType":"CONCERT_HALL","capacity":12500},"status":"PUBLISHED","name":"FISHER","startDate":1774047600000,"endDate":1774065600000,"eventCategoryName":"MusicEvent","ageRestriction":18,"allInPricing":True,"group":{"id":2764,"name":"Navy Pier","subdomain":"navypiereventscenter"},"sales":[{"id":1845794,"eventId":168656,"state":"SOLD_OUT","category":"VIP","type":"FIXED","seatingType":"OPEN","tiers":[{"id":2103781,"name":"VIP","info":"Includes expedited entry, a unique viewing area, private bars, and private restrooms.","price":171.13,"minPrice":171.13,"maxPrice":171.13,"active":True}],"resaleState":"UNAVAILABLE"},{"id":1845792,"eventId":168656,"state":"SOLD_OUT","category":"GA","type":"FIXED","seatingType":"OPEN","tiers":[{"id":2103779,"name":"GA","price":132.2,"minPrice":132.2,"maxPrice":132.2,"active":True}],"resaleState":"UNAVAILABLE"}],"seatsIoConfiguration":None,"url":"https://www.tixr.com/groups/navypiereventscenter/events/fisher-168656","formattedStartDate":"March 20, 2026 at 06:00 PM CDT"}

files = {
    "raw_api_173397_dynamo2_simple_ga_free.json": dynamo2_data,
    "raw_api_175000_wisp_soldout.json": wisp_data,
    "raw_api_168656_fisher_soldout.json": fisher_data,
}

for fname, data in files.items():
    path = os.path.join(OUTPUT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {fname} ({os.path.getsize(path)} bytes)")

print("\nAll test data saved.")
