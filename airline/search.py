from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
import csv


from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import CATALOG_DIR, pool

router = APIRouter()
airports = set()
first_flights = defaultdict(list)
connections = defaultdict(list)
connection_times = {}


def load_search_catalog():
    with (CATALOG_DIR / 'airports.csv').open(newline='') as source:
        airports.update(row['code'] for row in csv.DictReader(source))
    with (CATALOG_DIR / 'flights.csv').open(newline='') as source:
        for row in csv.DictReader(source):
            row['departure_time'] = datetime.fromisoformat(row['scheduled_departure'].replace('Z', '+00:00'))
            row['arrival_time'] = datetime.fromisoformat(row['scheduled_arrival'].replace('Z', '+00:00'))
            row['base_fare_cents'] = int(row['base_fare_cents'])
            first_flights[(row['origin'], row['origin_local_date'])].append(row)
            connections[(row['origin'], row['destination'])].append(row)
    for key, flights in connections.items():
        flights.sort(key=lambda flight: flight['departure_time'])
        connection_times[key] = [flight['departure_time'] for flight in flights]


@router.get('/search')
def search(request: Request):
    params = request.query_params
    origin, destination, travel_date = (params.get(key, '') for key in ('origin', 'destination', 'date'))
    try:
        if origin not in airports or destination not in airports or date.fromisoformat(travel_date).isoformat() != travel_date:
            raise ValueError
        passengers = int(params.get('passengers', ''))
        if passengers < 1 or passengers > 9:
            raise ValueError
    except ValueError:
        return JSONResponse({'error': 'invalid_request', 'detail': 'Invalid search parameters'}, status_code=422)

    candidates = []
    for first in first_flights.get((origin, travel_date), ()):
        if first['destination'] == destination:
            candidates.append((first,))
        elif first['destination'] != origin and destination != origin:
            key = (first['destination'], destination)
            times = connection_times.get(key, ())
            start = bisect_left(times, first['arrival_time'] + timedelta(minutes=45))
            end = bisect_right(times, first['arrival_time'] + timedelta(minutes=240))
            candidates.extend((first, second) for second in connections.get(key, ())[start:end])
    ids = list({flight['flight_id'] for itinerary in candidates for flight in itinerary})
    if not ids:
        return {'itineraries': []}
    with pool.connection() as connection:
        rows = connection.execute('''
            SELECT f.flight_id, f.capacity - f.seats_sold - COALESCE(SUM(hl.seats) FILTER
                (WHERE h.expires_at > now() AND NOT h.consumed), 0) AS available
            FROM flights f LEFT JOIN hold_legs hl ON hl.flight_id = f.flight_id
            LEFT JOIN holds h ON h.hold_id = hl.hold_id
            WHERE f.flight_id = ANY(%s) GROUP BY f.flight_id
        ''', (ids,)).fetchall()
    available = {row["flight_id"]: row["available"] for row in rows}
    results = []
    for itinerary in candidates:
        seats = min(available[flight['flight_id']] for flight in itinerary)
        if seats < passengers:
            continue
        flight_ids = [flight['flight_id'] for flight in itinerary]
        results.append({'itinerary_id': '+'.join(flight_ids), 'flight_ids': flight_ids,
                        'departure': itinerary[0]['scheduled_departure'], 'arrival': itinerary[-1]['scheduled_arrival'],
                        'stops': len(itinerary) - 1, 'price_cents': sum(f['base_fare_cents'] for f in itinerary),
                        'seats_available': seats})
    results.sort(key=lambda item: (item['stops'], item['price_cents'], item['departure']))
    return {'itineraries': results[:20]}
