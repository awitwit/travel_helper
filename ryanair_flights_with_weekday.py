from ryanair import Ryanair

# Create an API instance
api = Ryanair(currency="EUR")

# Get cheapest flights for the next coming days
from datetime import datetime, timedelta

# Configuration
origin_airports = [
    ("CGN", "Cologne Bonn"),
    # ("CRL", "Brussels"),
    ("NRN", "Weeze")
]
days_ahead = 14  # Search for the next 30 days
return_days_min = 2  # Minimum days for return
return_days_max = 3  # Maximum days for return
max_price = 80

print(f"Searching for flights from {len(origin_airports)} airports over the next {days_ahead} days...")
print("=" * 80)

# Collect all flights and trips
all_flights = []
all_trips = []

# Search for flights from each airport
for airport_code, airport_name in origin_airports:
    print(f"📍 Collecting flights from {airport_name} ({airport_code})...")

    # Search for flights day by day
    for day_offset in range(0, days_ahead):
        search_date = datetime.today().date() + timedelta(days=day_offset)

        # Get cheapest one-way flights for this day
        flights = api.get_cheapest_flights(airport_code, search_date, search_date + timedelta(days=1))

        if flights:
            # Add airport info to each flight for later display
            for flight in flights:
                flight._origin_airport = airport_name
                flight._origin_code = airport_code
            all_flights.extend(flights)

        # Get cheapest return flights for this day (return 2-4 days later)
        return_date_from = search_date + timedelta(days=return_days_min)
        return_date_to = search_date + timedelta(days=return_days_max)
        
        trips = api.get_cheapest_return_flights(
            airport_code, 
            search_date, search_date + timedelta(days=1),
            return_date_from, return_date_to
        )

        if trips:
            # Add airport info to each trip for later display
            for trip in trips:
                trip._origin_airport = airport_name
                trip._origin_code = airport_code
            all_trips.extend(trips)

print(f"\n✈️  Found {len(all_flights)} one-way flights and {len(all_trips)} return trips from all airports")
print("=" * 80)

# Separate outbound and inbound flights
outbound_flights = []
inbound_flights = []

# Add one-way flights to outbound (they are outbound from our airports)
for flight in all_flights:
    if flight.price <= max_price:
        outbound_flights.append(('one-way', flight, flight.price))

# Add outbound legs of return trips
for trip in all_trips:
    flight = trip.outbound
    flight._origin_code = trip._origin_code
    if flight.price <= max_price:
        outbound_flights.append(('return-outbound', flight, flight.price))

# Add inbound legs of return trips
for trip in all_trips:
    flight = trip.inbound
    flight._origin_code = trip._origin_code  # This will be the return destination (searched airport)
    # Limit return-in flights to 2026-02-05
    if flight.departureTime.date() <= datetime(2026, 3, 5).date() and flight.price <= max_price:
        inbound_flights.append(('return-inbound', flight, flight.price))

# Sort outbound flights by date, destination, price
outbound_flights.sort(key=lambda x: (x[1].departureTime.date(), x[1].destination, x[2]))

# Sort inbound flights by date, destination, price
inbound_flights.sort(key=lambda x: (x[1].departureTime.date(), x[1].destination, x[2]))

# Display outbound flights
print("🛫 OUTBOUND FLIGHTS (departing from searched airports):")
print("-" * 80)

for i, (flight_type, flight, price) in enumerate(outbound_flights, 1):
    # Include the weekday name in the formatted datetime
    departure_datetime = flight.departureTime.strftime('%Y-%m-%d %A %H:%M')
    origin_city = flight.originFull.split(',')[0] if ',' in flight.originFull else flight.originFull
    dest_city = flight.destinationFull.split(',')[0] if ',' in flight.destinationFull else flight.destinationFull
    type_label = "ONE-WAY" if flight_type == 'one-way' else "RETURN-OUT"
    print(f"{i:3d}. {departure_datetime} {price}€ {origin_city} ({flight._origin_code}) → {dest_city} ({flight.destination})")

print("\n" + "=" * 80)

# Display inbound flights
print("🛬 INBOUND FLIGHTS (returning to searched airports):")
print("-" * 80)

for i, (flight_type, flight, price) in enumerate(inbound_flights, 1):
    # Include the weekday name in the formatted datetime
    departure_datetime = flight.departureTime.strftime('%Y-%m-%d %A %H:%M')
    origin_city = flight.originFull.split(',')[0] if ',' in flight.originFull else flight.originFull
    dest_city = flight.destinationFull.split(',')[0] if ',' in flight.destinationFull else flight.destinationFull
    print(f"{i:3d}. {departure_datetime} {price}€ {origin_city} ({flight.origin}) → {dest_city} ({flight._origin_code})")

print("\n" + "=" * 80)
print("📊 SUMMARY:")
print(f"Airports searched: {len(origin_airports)}")
print(f"Days per airport: {days_ahead}")
print(f"Total one-way flights found: {len(all_flights)}")
print(f"Total return trips found: {len(all_trips)}")
print(f"Total outbound flights: {len(outbound_flights)}")
print(f"Total inbound flights: {len(inbound_flights)}")
print(f"Average flights per airport: {len(all_flights) / len(origin_airports):.1f}")
print(f"Average trips per airport: {len(all_trips) / len(origin_airports):.1f}")
print(f"Average flights per day (all airports): {len(all_flights) / (len(origin_airports) * days_ahead):.1f}")