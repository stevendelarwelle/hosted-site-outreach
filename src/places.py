"""
Google Places sourcing — Text Search per (business_type, location) target,
then Place Details for phone/website/rating/reviews. Same Places API surface
Card-Shout's scraper.ts uses, but generalized: location is a free-text string
("Green Bay WI") instead of a hardcoded "... Wisconsin" suffix, and the field
mask is wider (rating, user_ratings_total — Card-Shout's scraper never pulls
these, we need them for the qualify + email-draft steps).
"""

import os
import time
from pathlib import Path

import requests

PLACES_API_BASE = "https://maps.googleapis.com/maps/api/place"


class FatalApiError(Exception):
    pass


def search_places(api_key: str, business_type: str, location: str) -> list[dict]:
    query = f"{business_type} in {location}"
    results: list[dict] = []
    page_token = None
    page = 0

    while True:
        params = {"query": query, "key": api_key}
        if page_token:
            params["pagetoken"] = page_token
        resp = requests.get(f"{PLACES_API_BASE}/textsearch/json", params=params, timeout=15)
        data = resp.json()

        if data.get("status") in ("REQUEST_DENIED", "INVALID_REQUEST"):
            raise FatalApiError(data.get("error_message", data.get("status")))

        results.extend(data.get("results", []))
        page_token = data.get("next_page_token")
        page += 1

        if page_token and page < 3:
            time.sleep(2)  # Google requires a short delay before a page token is valid
        else:
            break

    return results


def get_place_details(api_key: str, place_id: str) -> dict:
    fields = "name,formatted_address,formatted_phone_number,website,rating,user_ratings_total,opening_hours,types"
    resp = requests.get(
        f"{PLACES_API_BASE}/details/json",
        params={"place_id": place_id, "fields": fields, "key": api_key},
        timeout=15,
    )
    return resp.json().get("result", {})


def source_leads(api_key: str, business_type: str, location: str, seen_ids: set[str]) -> list[dict]:
    """Returns a list of row dicts ready for db.upsert_lead — does not write
    to the DB itself, caller owns dedupe/persistence."""
    rows = []
    try:
        places = search_places(api_key, business_type, location)
    except FatalApiError:
        raise
    except Exception as e:
        print(f"  [WARN] search failed for {business_type} in {location}: {e}")
        return rows

    for place in places:
        place_id = place.get("place_id")
        if not place_id or place_id in seen_ids:
            continue
        seen_ids.add(place_id)

        try:
            details = get_place_details(api_key, place_id)
            time.sleep(0.3)
        except Exception:
            details = {}

        rows.append({
            "place_id": place_id,
            "business_type": business_type,
            "location_query": location,
            "name": details.get("name") or place.get("name") or "",
            "address": details.get("formatted_address") or place.get("formatted_address") or "",
            "phone": details.get("formatted_phone_number") or "",
            "website": details.get("website") or "",
            "rating": details.get("rating"),
            "review_count": details.get("user_ratings_total"),
            "status": "new",
        })

    return rows


def main():
    import yaml
    from src import db

    api_key = os.environ["GOOGLE_API_KEY"]
    config_path = Path(__file__).parent.parent / "config" / "targets.yml"
    config = yaml.safe_load(config_path.read_text())

    seen_ids: set[str] = set()
    total = 0

    for business_type in config["business_types"]:
        for location in config["locations"]:
            print(f"Searching: {business_type} in {location}")
            rows = source_leads(api_key, business_type, location, seen_ids)
            for row in rows:
                db.upsert_lead(row)
            total += len(rows)
            print(f"  +{len(rows)} new lead(s)")

    print(f"\nDone. {total} new lead(s) sourced this run.")


if __name__ == "__main__":
    main()
