#!/usr/bin/env python3
"""
Travel helper: cheap round-trip flights from Düsseldorf Weeze / Köln, then hotels.

1. Collects return trips from Weeze (NRN) and Köln (CGN). Only the departure (outbound) must
   match the schedule: Thursday after 5 pm or Friday after 11 pm. Return is 3–4 nights later
   (any time); no schedule restriction on the return flight.
2. Picks the 10 cheapest such trips by outbound price.
3. For each, fetches hotels for 3–4 nights from the Trivago MCP server.

Callable by OpenClaw:
  - Run: python travel_helper.py [--json] [--no-hotels]
  - Use --json for machine-readable output (OpenClaw-friendly).
"""

from __future__ import annotations

import argparse
import asyncio
import html
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

# --------------- Config: Weeze + Köln, Thu eve / Fri late outbound, 3–4 nights ---------------
ORIGIN_AIRPORTS = [
    ("CGN", "Köln"),
    ("NRN", "Düsseldorf Weeze"),
]
DAYS_AHEAD = 120  # search ahead for Thu/Fri departures
RETURN_DAYS_MIN = 2  # 2 nights at destination
RETURN_DAYS_MAX = 4  # 4 nights at destination
HOTEL_NIGHTS = 4  # legacy; hotel stay now matches return flight (arrival = outbound date, departure = return date)
TRIVAGO_MCP_URL = "https://mcp.trivago.com/mcp"

# Only the departure (outbound) is restricted: Thursday >= 17:00, or Friday >= 23:00 (11 pm).
# Return flight is 3–4 nights later with no time-of-day restriction. Monday=0 in weekday().
THURSDAY = 3
FRIDAY = 4
OUTBOUND_THURSDAY_AFTER_HOUR = 17
OUTBOUND_FRIDAY_AFTER_HOUR = 23  # 11 pm

# Display: separator between the two legs on one line
LEG_SEP = "  |  "    # between outbound and inbound on one line

RYANAIR_BOOKING_BASE = "https://www.ryanair.com/de/de/trip/flights/select"


def _ryanair_booking_url(
    origin_iata: str,
    destination_iata: str,
    date_out: str,
    date_in: str,
    adults: int = 2,
) -> str:
    """Build Ryanair round-trip flight select URL (German site)."""
    params = (
        f"adults={adults}&teens=0&children=0&infants=0"
        f"&dateOut={date_out}&dateIn={date_in}"
        "&isConnectedFlight=false&discount=0&promoCode=&isReturn=true"
        f"&originIata={origin_iata}&destinationIata={destination_iata}"
        "&tpAdults=1&tpTeens=0&tpChildren=0&tpInfants=0"
        f"&tpStartDate={date_out}&tpEndDate={date_in}"
        "&tpDiscount=0&tpPromoCode="
        f"&tpOriginIata={origin_iata}&tpDestinationIata={destination_iata}"
    )
    return f"{RYANAIR_BOOKING_BASE}?{params}"


def _outbound_departure_allowed(dt: datetime) -> bool:
    """True if outbound departure is Thursday after 5 pm or Friday after 11 am (only departure is restricted)."""
    wd = dt.weekday()
    hour = dt.hour
    if wd == THURSDAY:
        return hour >= OUTBOUND_THURSDAY_AFTER_HOUR
    if wd == FRIDAY:
        return hour >= OUTBOUND_FRIDAY_AFTER_HOUR
    return False


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
    cheapest_flights: list[tuple[object, object, float]],
    hotels_per_flight: int = 3,
    adults: int = 2,
    rooms: int = 1,
) -> list[dict]:
    """
    For each (outbound, return_flight, price) entry, fetch hotels_per_flight hotels
    for that destination. Hotel stay = arrival (outbound date) to departure (return flight date).
    Returns list of { "destination", "arrival", "departure", "flight", "return_flight", "price", "hotels": [...] }.
    """
    if not TRIVAGO_AVAILABLE or not cheapest_flights:
        return []
    tasks = []
    for outbound, return_flight, price in cheapest_flights:
        dest_city = (
            outbound.destinationFull.split(",")[0].strip()
            if "," in outbound.destinationFull
            else outbound.destinationFull
        )
        arrival = outbound.departureTime.date().isoformat()
        departure = return_flight.departureTime.date().isoformat()
        tasks.append((dest_city, arrival, departure, outbound, return_flight, price))

    results = []
    async with streamable_http_client(TRIVAGO_MCP_URL) as streams:
        read_stream, write_stream = streams[0], streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            for dest_city, arrival, departure, outbound, return_flight, price in tasks:
                hotels = await _top_hotels_for_destination(
                    session, dest_city, arrival, departure,
                    n=hotels_per_flight, adults=adults, rooms=rooms,
                )
                results.append({
                    "destination": dest_city,
                    "arrival": arrival,
                    "departure": departure,
                    "flight": outbound,
                    "return_flight": return_flight,
                    "price": price,
                    "hotels": hotels,
                })
    return results


def _print_html(
    cheapest_flights: list[tuple[object, object, float]],
    hotel_results: list[dict],
    adults: int = 2,
) -> None:
    """Output results as HTML with short 'Book' links for Ryanair."""
    title = "Travel helper: round trips Weeze/Köln"
    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "  <meta charset=\"utf-8\">",
        f"  <title>{html.escape(title)}</title>",
        "  <style>",
        "    body { font-family: system-ui, sans-serif; margin: 1rem 2rem; max-width: 900px; }",
        "    h1 { font-size: 1.25rem; }",
        "    .trip { margin: 1rem 0; padding: 0.75rem; border: 1px solid #ccc; border-radius: 6px; }",
        "    .trip-header { font-weight: bold; margin-bottom: 0.25rem; }",
        "    .trip-details { color: #444; font-size: 0.95rem; }",
        "    a.trip-link { color: #073590; text-decoration: none; }",
        "    a.trip-link:hover { text-decoration: underline; }",
        "    .hotels { margin-top: 0.5rem; font-size: 0.9rem; }",
        "    .hotel { margin: 0.2rem 0; }",
        "    .hotel a { color: #073590; }",
        "  </style>",
        "</head>",
        "<body>",
        f"  <h1>{html.escape(title)}</h1>",
        "  <p>Departure Thu after 5pm or Fri after 11pm; return 2–4 nights later.</p>",
    ]
    if hotel_results:
        for i, r in enumerate(hotel_results, 1):
            outbound = r["flight"]
            ret = r["return_flight"]
            dest_city = r["destination"]
            total = r["price"] + ret.price
            out_weekday = outbound.departureTime.strftime("%Y-%m-%d %A %H:%M")
            ret_weekday = ret.departureTime.strftime("%Y-%m-%d %A %H:%M")
            out_leg = f"{out_weekday}  {outbound.price}€  {outbound.origin}→{outbound.destination}"
            ret_leg = f"{ret_weekday}  {ret.price}€  {ret.origin}→{ret.destination}"
            ryanair_url = _ryanair_booking_url(
                outbound.origin, outbound.destination,
                outbound.departureTime.date().isoformat(),
                ret.departureTime.date().isoformat(),
                adults=adults,
            )
            out_date = outbound.departureTime.date()
            ret_date = ret.departureTime.date()
            nights = (ret_date - out_date).days
            days = nights + 1
            lines.append("  <div class=\"trip\">")
            lines.append(f"    <div class=\"trip-header\">{html.escape(dest_city)} ({total:.2f}€) — {days} days, {nights} nights</div>")
            lines.append(f"    <a class=\"trip-details trip-link\" href=\"{html.escape(ryanair_url)}\" target=\"_blank\" rel=\"noopener\">{html.escape(out_leg)}  |  {html.escape(ret_leg)}</a>")
            lines.append("    <div class=\"hotels\">")
            for hotel in r["hotels"]:
                name = hotel.get("Accommodation Name") or hotel.get("accommodation_name") or "—"
                url = hotel.get("Accommodation URL") or hotel.get("accommodation_url") or ""
                price_stay = hotel.get("Price Per Stay") or hotel.get("price_per_stay") or ""
                if url:
                    lines.append(f"      <div class=\"hotel\"><a href=\"{html.escape(url)}\" target=\"_blank\" rel=\"noopener\">{html.escape(name)}</a> {html.escape(price_stay)}</div>")
                else:
                    lines.append(f"      <div class=\"hotel\">{html.escape(name)} {html.escape(price_stay)}</div>")
            lines.append("    </div>")
            lines.append("  </div>")
    else:
        for i, (ob, ib, price) in enumerate(cheapest_flights, 1):
            dest_city = ob.destinationFull.split(",")[0] if "," in ob.destinationFull else ob.destinationFull
            total = price + ib.price
            out_weekday = ob.departureTime.strftime("%Y-%m-%d %A %H:%M")
            ret_weekday = ib.departureTime.strftime("%Y-%m-%d %A %H:%M")
            out_leg = f"{out_weekday}  {price}€  {ob.origin}→{ob.destination}"
            ret_leg = f"{ret_weekday}  {ib.price}€  {ib.origin}→{ib.destination}"
            ryanair_url = _ryanair_booking_url(
                ob.origin, ob.destination,
                ob.departureTime.date().isoformat(),
                ib.departureTime.date().isoformat(),
                adults=adults,
            )
            out_date = ob.departureTime.date()
            ret_date = ib.departureTime.date()
            nights = (ret_date - out_date).days
            days = nights + 1
            lines.append("  <div class=\"trip\">")
            lines.append(f"    <div class=\"trip-header\">{html.escape(dest_city)} ({total:.2f}€) — {days} days, {nights} nights</div>")
            lines.append(f"    <a class=\"trip-details trip-link\" href=\"{html.escape(ryanair_url)}\" target=\"_blank\" rel=\"noopener\">{html.escape(out_leg)}  |  {html.escape(ret_leg)}</a>")
            lines.append("  </div>")
    if not cheapest_flights:
        lines.append("  <p>(No round trips found.)</p>")
    lines.append("</body>")
    lines.append("</html>")
    now = datetime.now()
    filename = f"travel_helper_{now.strftime('%Y-%m-%d')}.html"
    path = Path(filename).resolve()
    path.write_text("\n".join(lines), encoding="utf-8")
    print(path, file=sys.stderr)


def collect_outbound_flights() -> list[tuple[object, object, float]]:
    """Collect return trips from Weeze/Köln. Only the departure must match: Thu after 5pm or Fri after 11pm.
    Return is 3–4 nights later (any time of day). Uses API time windows so we get trips in those slots.
    Returns list of (outbound, return_flight, outbound_price).
    """
    api = Ryanair(currency="EUR")
    outbound = []
    for airport_code, airport_name in ORIGIN_AIRPORTS:
        for day_offset in range(0, DAYS_AHEAD):
            search_date = datetime.today().date() + timedelta(days=day_offset)
            wd = search_date.weekday()
            if wd == THURSDAY:
                outbound_time_from, outbound_time_to = "17:00", "23:59"
            elif wd == FRIDAY:
                outbound_time_from, outbound_time_to = "11:00", "23:59"
            else:
                continue
            return_date_from = search_date + timedelta(days=RETURN_DAYS_MIN)
            return_date_to = search_date + timedelta(days=RETURN_DAYS_MAX)
            trips = api.get_cheapest_return_flights(
                airport_code,
                search_date, search_date,
                return_date_from, return_date_to,
                outbound_departure_time_from=outbound_time_from,
                outbound_departure_time_to=outbound_time_to,
            )
            if trips:
                for t in trips:
                    t._origin_airport = airport_name
                    t._origin_code = airport_code
                    ob = t.outbound
                    ob._origin_airport = t._origin_airport
                    ob._origin_code = t._origin_code
                    outbound.append((ob, t.inbound, ob.price))
    outbound.sort(key=lambda x: (x[2] + x[1].price, x[0].departureTime.date(), x[0].destination))
    return outbound


def run(
    output_json: bool = False,
    output_html: bool = False,
    fetch_hotels: bool = True,
    adults: int = 2,
    rooms: int = 1,
    num_cheapest_flights: int = 10,
    hotels_per_flight: int = 3,
) -> None:
    # 1. Collect return trips (only departure restricted: Thu after 5pm / Fri after 11pm; return 3–4 nights later, any time)
    outbound_flights = collect_outbound_flights()
    # 2. Already sorted by price; take the N cheapest
    cheapest_flights = outbound_flights[:num_cheapest_flights]

    # 3. Fetch hotels for those flights (stay = outbound date to return date)
    hotel_results = []
    if fetch_hotels and TRIVAGO_AVAILABLE and cheapest_flights:
        if not output_json:
            print(f"Fetching {hotels_per_flight} hotels per trip (stay = outbound date → return date) for the {num_cheapest_flights} cheapest round trips...", file=sys.stderr)
        hotel_results = asyncio.run(
            fetch_hotels_for_cheapest_flights(
                cheapest_flights,
                hotels_per_flight=hotels_per_flight,
                adults=adults,
                rooms=rooms,
            )
        )

    if output_json:
        # Prefer hotel_results when present; otherwise output flight-only from cheapest_flights
        if hotel_results:
            out = {
                "cheapest_flights_with_hotels": [
                    {
                        "outbound": {
                            "departure": r["flight"].departureTime.isoformat(),
                            "origin": r["flight"].origin,
                            "origin_full": r["flight"].originFull,
                            "destination": r["flight"].destination,
                            "destination_full": r["flight"].destinationFull,
                            "price_eur": r["price"],
                        },
                        "return": {
                            "departure": r["return_flight"].departureTime.isoformat(),
                            "origin": r["return_flight"].origin,
                            "origin_full": r["return_flight"].originFull,
                            "destination": r["return_flight"].destination,
                            "destination_full": r["return_flight"].destinationFull,
                            "price_eur": r["return_flight"].price,
                        },
                        "hotel_arrival": r["arrival"],
                        "hotel_departure": r["departure"],
                        "hotels": r["hotels"],
                    }
                    for r in hotel_results
                ],
            }
        else:
            out = {
                "cheapest_flights": [
                    {
                        "outbound": {
                            "departure": ob.departureTime.isoformat(),
                            "origin": ob.origin,
                            "origin_full": ob.originFull,
                            "destination": ob.destination,
                            "destination_full": ob.destinationFull,
                            "price_eur": ob.price,
                        },
                        "return": {
                            "departure": ib.departureTime.isoformat(),
                            "origin": ib.origin,
                            "origin_full": ib.originFull,
                            "destination": ib.destination,
                            "destination_full": ib.destinationFull,
                            "price_eur": ib.price,
                        },
                    }
                    for ob, ib, price in cheapest_flights
                ],
            }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return

    if output_html:
        _print_html(
            cheapest_flights=cheapest_flights,
            hotel_results=hotel_results,
            adults=adults,
        )
        return

    # Human-readable output
    print("Travel helper: round trips Weeze/Köln → destination (departure: Thu after 5pm or Fri after 11pm only), 3–4 nights, return to Weeze/Köln (return time unrestricted)")
    print("=" * 80)
    print("CHEAPEST ROUND TRIPS" + (" + HOTELS" if hotel_results else " (flights only)"))
    print("-" * 80)
    if hotel_results:
        for i, r in enumerate(hotel_results, 1):
            outbound = r["flight"]
            ret = r["return_flight"]
            price = r["price"]
            dest_city = r["destination"]
            arrival, departure = r["arrival"], r["departure"]
            out_weekday = outbound.departureTime.strftime("%Y-%m-%d %A %H:%M")
            ret_weekday = ret.departureTime.strftime("%Y-%m-%d %A %H:%M")
            origin_city = outbound.originFull.split(",")[0] if "," in outbound.originFull else outbound.originFull
            ret_origin_city = ret.originFull.split(",")[0] if "," in ret.originFull else ret.originFull
            ret_dest_city = ret.destinationFull.split(",")[0].strip() if "," in ret.destinationFull else ret.destination
            total = price + ret.price
            nights = (ret.departureTime.date() - outbound.departureTime.date()).days
            days = nights + 1
            out_leg = f"{out_weekday}  {price}€  {origin_city} ({outbound._origin_code})→{dest_city} ({outbound.destination})"
            ret_leg = f"{ret_weekday}  {ret.price}€  {ret_origin_city} ({ret.origin})→{ret_dest_city} ({ret.destination})"
            print(f"{i}. {dest_city} ({total:.2f}€) — {days} days, {nights} nights: {out_leg}{LEG_SEP}{ret_leg}")
            ryanair_url = _ryanair_booking_url(
                outbound.origin, outbound.destination,
                outbound.departureTime.date().isoformat(),
                ret.departureTime.date().isoformat(),
                adults=adults,
            )
            print(f"   {ryanair_url}")
            nights = (datetime.fromisoformat(departure).date() - datetime.fromisoformat(arrival).date()).days
            print(f"   Hotels ({nights} nights, {arrival} → {departure}):")
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
    else:
        for i, (ob, ib, price) in enumerate(cheapest_flights, 1):
            out_weekday = ob.departureTime.strftime("%Y-%m-%d %A %H:%M")
            ret_weekday = ib.departureTime.strftime("%Y-%m-%d %A %H:%M")
            origin_city = ob.originFull.split(",")[0] if "," in ob.originFull else ob.originFull
            dest_city = ob.destinationFull.split(",")[0] if "," in ob.destinationFull else ob.destinationFull
            ret_origin_city = ib.originFull.split(",")[0] if "," in ib.originFull else ib.originFull
            ret_dest_city = ib.destinationFull.split(",")[0] if "," in ib.destinationFull else ib.destination
            total = price + ib.price
            nights = (ib.departureTime.date() - ob.departureTime.date()).days
            days = nights + 1
            out_leg = f"{out_weekday}  {price}€  {origin_city} ({ob._origin_code})→{dest_city} ({ob.destination})"
            ret_leg = f"{ret_weekday}  {ib.price}€  {ret_origin_city} ({ib.origin})→{ret_dest_city} ({ib.destination})"
            print(f"{i}. {dest_city} ({total:.2f}€) — {days} days, {nights} nights: {out_leg}{LEG_SEP}{ret_leg}")
            ryanair_url = _ryanair_booking_url(
                ob.origin, ob.destination,
                ob.departureTime.date().isoformat(),
                ib.departureTime.date().isoformat(),
                adults=adults,
            )
            print(f"   {ryanair_url}")
        if not cheapest_flights:
            print("(No round trips found for Thu after 5pm / Fri after 11pm from Weeze or Köln.)")
    if not TRIVAGO_AVAILABLE and fetch_hotels:
        print("(Trivago MCP not installed: pip install 'mcp[cli]' for hotels.)", file=sys.stderr)
    elif not hotel_results and fetch_hotels and cheapest_flights:
        print("(No hotel results from Trivago. Check network and that Python/SSL support HTTPS.)", file=sys.stderr)
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Round trips from Weeze/Köln (Thu after 5pm or Fri after 11pm outbound, 3–4 nights, return). N cheapest + M hotels each.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON for OpenClaw/machine use",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Write results to travel_helper_YYYY-MM-DD.html (full path printed to stderr)",
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
        default=10,
        metavar="N",
        help="Number of cheapest round trips to show and fetch hotels for (default: 10)",
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
        output_html=args.html,
        fetch_hotels=not args.no_hotels,
        adults=args.adults,
        rooms=args.rooms,
        num_cheapest_flights=args.num_cheapest,
        hotels_per_flight=args.hotels_per_flight,
    )


if __name__ == "__main__":
    main()
