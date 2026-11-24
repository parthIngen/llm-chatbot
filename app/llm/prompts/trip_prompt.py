# Trip domain prompt: status, shipment methods, routes, locations, regions, and trip counts

TRIP_PROMPT = """
TRIP DOMAIN RULES:
1. TRIP STATUS (field: "tripStatus"):
   - "running" (active / live / in-transit / scheduled / ongoing / moving / open) -> tripStatus eq "running"
   - "closed" (completed / finished / inactive) -> tripStatus eq "InActive"
   - "cancelled" -> tripStatus eq "cancelled"
   - "valid" -> default for all queries unless a specific tripStatus is requested or cancelled trips are included.
   - If query explicitly asks to include cancelled trips ("including cancelled trips") -> omit tripStatus eq "valid".

2. SHIPMENT METHODS (field: "shipmentMethod"):
   - "air" / "AIR_Network" -> shipmentMethod eq "air"
   - "sfc" / "SFC_Network" -> shipmentMethod eq "sfc"
   - "speed" / "Speed_Truck" -> shipmentMethod eq "speed"
   - "feeder" / "Feeder" -> shipmentMethod eq "feeder"
   - "empty" / "Empty" -> shipmentMethod eq "empty"
   - "delivery" / "Customer Delivery" -> shipmentMethod eq "delivery"
   - "Pick Up and Delivery" -> shipmentMethod in ["Delivery", "Pick Up and Delivery"]
   - "Pick Up" / "pickup" -> shipmentMethod eq "Pick Up"
   - Defaults to tripStatus eq "running" unless closed or cancelled is requested.

3. ROUTE CATEGORIES & ROUTE (fields: "routeCategory", "Route"):
   - "intercity" / "network" -> routeCategory eq "intercity"
   - "intracity" / "local" -> routeCategory eq "intracity"
   - "route <Name>" -> Route eq "<Name>"

4. ORIGIN / DESTINATION:
   - "from / source / origin <Loc>" -> source eq "<Loc>"
   - "to / destination / reaching <Loc>" -> destination eq "<Loc>"

5. REGION & STATE (fields: "region", "state"):
   - "<Region> region" -> region eq "<Region>" | multiple regions -> region in ["East", "West"]
   - "state <State>" -> state eq "<State>" | multiple states -> state in ["Maharashtra", "Tamil Nadu"], groupBy: ["state"]

6. CLOSE REMARKS (field: "closeremark"):
   - "forcefully" -> closeremark eq "forcefully" | "geofence" -> closeremark eq "geofence"
   - Allowed values: "manual", "forcefully", "geofence", "polygon", "api", "backend", "web"

7. MASTER VALIDATION (fields: "vehicleMaster", "routeMaster", "fleetMaster"):
   - "vehicle inside / outside master" -> vehicleMaster eq "inside" / "outside"
   - "route inside / outside master" -> routeMaster eq "inside" / "outside" (if outside: add shipmentMethod nin ["Pick Up", "Delivery", "Pick Up and Delivery"])
   - "fleet inside / outside master" -> fleetMaster eq "inside" / "outside" (if outside: add shipmentMethod nin ["Pick Up", "Delivery", "Pick Up and Delivery"])

8. TRIP DISTANCE (field: "tripDistance"):
   - "distance between X and Y km" -> tripDistance gte "X", tripDistance lte "Y"
   - "distance exceeding X km" -> tripDistance gte "X"

9. BREAKDOWN & GROUPING:
   - Daywise: groupBy: ["runDate"], groupByFormat: {"runDate": "day"}, metrics: [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}], sort: [{"field": "runDate", "order": "asc"}]
   - Monthwise: groupBy: ["runDate"], groupByFormat: {"runDate": "month"}, metrics: [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}], sort: [{"field": "runDate", "order": "asc"}]
   - Unique Dimensions: "unique routes" -> groupBy: ["route"] | "unique destinations" -> groupBy: ["destination"] | "unique areas" -> groupBy: ["area"] | "unique branches" -> groupBy: ["branch"]

TRIP EXAMPLES:
- "Show all running trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "running"}], "select": []}
- "Show closed trips in September 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "InActive"}, {"field": "runDate", "operator": "gte", "value": "2026-09-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-09-30 23:59:59"}], "select": []}
- "Show cancelled trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "cancelled"}], "select": []}
- "How many trips ran in August 2026?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "Show all trips for North region including cancelled trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "region", "operator": "eq", "value": "North"}], "select": []}
- "Show active Air Intercity trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "tripStatus", "operator": "eq", "value": "active"}, {"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "shipmentMethod", "operator": "eq", "value": "air"}, {"field": "routeCategory", "operator": "eq", "value": "intercity"}], "select": []}
- "Trip count across major feeder types" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}], "select": [], "groupBy": ["shipmentMethod"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]}
- "How many trips were Speed_Truck or Empty?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "shipmentMethod", "operator": "in", "value": ["speed", "empty"]}], "select": [], "groupBy": ["shipmentMethod"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]}
- "Delivery trips originated from location BOB" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "source", "operator": "eq", "value": "BOB"}, {"field": "shipmentMethod", "operator": "eq", "value": "Delivery"}], "select": []}
- "Trips generated by Maharashtra and Tamil Nadu combined" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "state", "operator": "in", "value": ["Maharashtra", "Tamil Nadu"]}], "select": [], "groupBy": ["state"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]}
- "Total trips with vehicle outside of master" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "vehicleMaster", "operator": "eq", "value": "outside"}], "select": []}
- "Total trips with route outside of master" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "routeMaster", "operator": "eq", "value": "outside"}, {"field": "shipmentMethod", "operator": "nin", "value": ["Pick Up", "Delivery", "Pick Up and Delivery"]}], "select": []}
- "Total trips with fleet inside of master" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "fleetMaster", "operator": "eq", "value": "inside"}], "select": []}
- "monthwise breakdown of trips between August 2026 and October 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-10-31 23:59:59"}], "select": [], "groupBy": ["runDate"], "groupByFormat": {"runDate": "month"}, "metrics": [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}], "sort": [{"field": "runDate", "order": "asc"}]}
- "daywise breakdown of trips in August 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": [], "groupBy": ["runDate"], "groupByFormat": {"runDate": "day"}, "metrics": [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}], "sort": [{"field": "runDate", "order": "asc"}]}
- "Trips with distance between 1 km and 50 km" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "tripDistance", "operator": "gte", "value": "1"}, {"field": "tripDistance", "operator": "lte", "value": "50"}], "select": []}
- "Trips recorded under Area DEL" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "area", "operator": "eq", "value": "DEL"}]}
- "Unique routes utilized across trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}], "select": [], "groupBy": ["route"]}
"""
