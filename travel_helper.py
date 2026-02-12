#!/usr/bin/env python3
"""
Travel helper: collect all cheap Ryanair flights, then fetch 3 hotels for the 3 cheapest flights.

1. Collects all outbound flights (under max price) from configured origins.
2. Picks the 3 cheapest flights by price.
3. For each of those 3, asks the Trivago MCP server for 3 hotels (2 nights from flight date).

Callable by OpenClaw:
  - Run: python travel_helper.py [--json] [--no-hotels]
  - Use --json for machine-readable output (OpenClaw-friendly).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Project root on path for trivago package
if __name__ == "__main__" and __package__ is None:
    _root = Path(__file__).resolve().parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from ryanair import Ryanair

# Trivago MCP (optional: only if mcp is installed)
try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from trivago.fetch_hotels_mcp import get_location_suggestion, search_accommodations

    TRIVAGO_AVAILABLE = True
except ImportError:
    TRIVAGO_AVAILABLE = False

# --------------- Config (aligned with ryanair_flights_with_weekday.py) ---------------
ORIGIN_AIRPORTS = [
    ("CGN", "Cologne Bonn"),
    ("NRN", "Weeze"),
]
DAYS_AHEAD = 14
RETURN_DAYS_MIN = 2
RETURN_DAYS_MAX = 3
MAX_FLIGHT_PRICE = 80
HOTEL_NIGHTS = 2
TRIVAGO_MCP_URL = "https://mcp.trivago.com/mcp"


def _parse_price_night(h: dict) -> float:
    """Parse 'Price Per Night' e.g. '€77' to float. Return inf if missing/invalid."""
    raw = h.get("Price Per Night") or h.get("price_per_night") or ""
    if not raw:
        return float("inf")
    m = re.search(r"[\d.,]+", raw.replace(",", "."))
    if not m:
        return float("inf")
    try:
        return float(m.group(0))
    except ValueError:
        return float("inf")


def _city_name_for_trivago(destination: str) -> str:
    """Extract city name only for Trivago: remove airport code in parentheses, e.g. 'Nador (NDR)' -> 'Nador'."""
    s = destination.strip()
    if " (" in s and s.endswith(")"):
        s = s[: s.rindex(" (")].strip()
    return s


def _trivago_query_for_destination(destination_city: str) -> list[str]:
    """Build search queries for Trivago: city only (no airport code), then try part before ' - ' if present."""
    city_only = _city_name_for_trivago(destination_city)
    queries = [city_only]
    if " - " in city_only:
        queries.append(city_only.split(" - ")[0].strip())
    return queries


async def _top_hotels_for_destination(
    session: ClientSession,
    destination_city: str,
    arrival_date: str,
    departure_date: str,
    n: int = 3,
    adults: int = 2,
    rooms: int = 1,
) -> list[dict]:
    """Return up to n cheapest hotels (by price per night) for one destination/dates."""
    suggestion = None
    for query in _trivago_query_for_destination(destination_city):
        suggestion = await get_location_suggestion(session, query)
        if suggestion:
            break
    if not suggestion:
        return []
    location_id, location_ns = suggestion
    hotels = await search_accommodations(
        session,
        location_id,
        location_ns,
        arrival_date,
        departure_date,
        adults=adults,
        rooms=rooms,
    )
    if not hotels:
        return []
    hotels_sorted = sorted(hotels, key=_parse_price_night)
    return hotels_sorted[:n]


async def fetch_hotels_for_cheapest_flights(
    cheapest_flights: list[tuple[str, object, float]],
    hotels_per_flight: int = 3,
    adults: int = 2,
    rooms: int = 1,
) -> list[dict]:
    """
    For each of the given (flight_type, flight, price) entries, fetch hotels_per_flight hotels
    for that flight's destination and dates (2 nights from flight date).
    Returns list of { "destination", "arrival", "departure", "flight", "price", "hotels": [...] }.
    """
    if not TRIVAGO_AVAILABLE or not cheapest_flights:
        return []
    # Build (dest_city, arrival, departure) for each flight
    tasks = []
    for _ft, flight, price in cheapest_flights:
        dest_city = (
            flight.destinationFull.split(",")[0].strip()
            if "," in flight.destinationFull
            else flight.destinationFull
        )
        flight_date = flight.departureTime.date()
        arrival = flight_date.isoformat()
        departure = (flight_date + timedelta(days=HOTEL_NIGHTS)).isoformat()
        tasks.append((dest_city, arrival, departure, flight, price))

    results = []
    async with streamable_http_client(TRIVAGO_MCP_URL) as streams:
        read_stream, write_stream = streams[0], streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            for dest_city, arrival, departure, flight, price in tasks:
                hotels = await _top_hotels_for_destination(
                    session, dest_city, arrival, departure,
                    n=hotels_per_flight, adults=adults, rooms=rooms,
                )
                results.append({
                    "destination": dest_city,
                    "arrival": arrival,
                    "departure": departure,
                    "flight": flight,
                    "price": price,
                    "hotels": hotels,
                })
    return results


def collect_outbound_flights() -> list[tuple[str, object, float]]:
    """Same logic as ryanair_flights_with_weekday: collect outbound flights under max price."""
    api = Ryanair(currency="EUR")
    all_flights = []
    all_trips = []
    for airport_code, airport_name in ORIGIN_AIRPORTS:
        for day_offset in range(0, DAYS_AHEAD):
            search_date = datetime.today().date() + timedelta(days=day_offset)
            flights = api.get_cheapest_flights(
                airport_code, search_date, search_date + timedelta(days=1)
            )
            if flights:
                for f in flights:
                    f._origin_airport = airport_name
                    f._origin_code = airport_code
                all_flights.extend(flights)
            return_date_from = search_date + timedelta(days=RETURN_DAYS_MIN)
            return_date_to = search_date + timedelta(days=RETURN_DAYS_MAX)
            trips = api.get_cheapest_return_flights(
                airport_code,
                search_date, search_date + timedelta(days=1),
                return_date_from, return_date_to,
            )
            if trips:
                for t in trips:
                    t._origin_airport = airport_name
                    t._origin_code = airport_code
                all_trips.extend(trips)
    outbound = []
    for flight in all_flights:
        if flight.price <= MAX_FLIGHT_PRICE:
            outbound.append(("one-way", flight, flight.price))
    for trip in all_trips:
        f = trip.outbound
        f._origin_code = trip._origin_code
        if f.price <= MAX_FLIGHT_PRICE:
            outbound.append(("return-outbound", f, f.price))
    outbound.sort(key=lambda x: (x[1].departureTime.date(), x[1].destination, x[2]))
    return outbound


def run(
    output_json: bool = False,
    fetch_hotels: bool = True,
    adults: int = 2,
    rooms: int = 1,
    num_cheapest_flights: int = 3,
    hotels_per_flight: int = 3,
) -> None:
    # 1. Collect all outbound flights
    outbound_flights = collect_outbound_flights()
    # 2. Sort by price and take the N cheapest
    by_price = sorted(outbound_flights, key=lambda x: (x[2], x[1].departureTime.date(), x[1].destination))
    cheapest_flights = by_price[:num_cheapest_flights]

    # 3. Fetch hotels for those flights only (3 hotels per flight)
    hotel_results = []
    if fetch_hotels and TRIVAGO_AVAILABLE and cheapest_flights:
        if not output_json:
            print(f"Fetching {hotels_per_flight} hotels (2 nights) for the {num_cheapest_flights} cheapest flights...", file=sys.stderr)
        hotel_results = asyncio.run(
            fetch_hotels_for_cheapest_flights(
                cheapest_flights,
                hotels_per_flight=hotels_per_flight,
                adults=adults,
                rooms=rooms,
            )
        )

    if output_json:
        out = {
            "cheapest_flights_with_hotels": [
                {
                    "departure": r["flight"].departureTime.isoformat(),
                    "origin": r["flight"].origin,
                    "origin_full": r["flight"].originFull,
                    "destination": r["destination"],
                    "destination_code": r["flight"].destination,
                    "price_eur": r["price"],
                    "hotel_arrival": r["arrival"],
                    "hotel_departure": r["departure"],
                    "hotels": r["hotels"],
                }
                for r in hotel_results
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return

    # Human-readable output
    print("Travel helper: 3 cheapest flights + 3 hotels each (2 nights from flight date)")
    print("=" * 80)
    print("CHEAPEST FLIGHTS + HOTELS")
    print("-" * 80)
    for i, r in enumerate(hotel_results, 1):
        flight = r["flight"]
        price = r["price"]
        dest_city = r["destination"]
        arrival, departure = r["arrival"], r["departure"]
        dep_weekday = flight.departureTime.strftime("%Y-%m-%d %A %H:%M")
        origin_city = flight.originFull.split(",")[0] if "," in flight.originFull else flight.originFull
        print(f"{i}. {dep_weekday}  {price}€  {origin_city} ({flight._origin_code}) → {dest_city} ({flight.destination})")
        print(f"   Hotels ({arrival} → {departure}):")
        for j, hotel in enumerate(r["hotels"], 1):
            name = hotel.get("Accommodation Name") or hotel.get("accommodation_name") or "—"
            price_night = hotel.get("Price Per Night") or hotel.get("price_per_night") or "—"
            price_stay = hotel.get("Price Per Stay") or hotel.get("price_per_stay") or "—"
            url = hotel.get("Accommodation URL") or hotel.get("accommodation_url") or ""
            print(f"     {j}. {name}  |  {price_night} (total {price_stay})")
            if url:
                print(f"        {url}")
        if not r["hotels"]:
            print("     (none found)")
        print()
    if not hotel_results and fetch_hotels and cheapest_flights:
        print("(No hotel results from Trivago.)", file=sys.stderr)
    elif not TRIVAGO_AVAILABLE and fetch_hotels:
        print("(Trivago MCP not installed: pip install 'mcp[cli]' for hotels.)", file=sys.stderr)
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cheap Ryanair flights: show N cheapest flights + M hotels each (2 nights from flight date). OpenClaw-callable.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON for OpenClaw/machine use",
    )
    parser.add_argument(
        "--no-hotels",
        action="store_true",
        dest="no_hotels",
        help="Skip Trivago hotel fetch (flights only)",
    )
    parser.add_argument("--adults", type=int, default=2, help="Adults for hotel search")
    parser.add_argument("--rooms", type=int, default=1, help="Rooms for hotel search")
    parser.add_argument(
        "--num-cheapest",
        type=int,
        default=3,
        metavar="N",
        help="Number of cheapest flights to show and fetch hotels for (default: 3)",
    )
    parser.add_argument(
        "--hotels-per-flight",
        type=int,
        default=3,
        metavar="M",
        help="Number of hotels to fetch per flight (default: 3)",
    )
    args = parser.parse_args()
    run(
        output_json=args.json,
        fetch_hotels=not args.no_hotels,
        adults=args.adults,
        rooms=args.rooms,
        num_cheapest_flights=args.num_cheapest,
        hotels_per_flight=args.hotels_per_flight,
    )


if __name__ == "__main__":
    main()
