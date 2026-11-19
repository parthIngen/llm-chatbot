# Trip domain prompt: status, shipment methods, routes, locations, regions, and trip counts

TRIP_PROMPT = """
TRIP DOMAIN RULES:
1. TRIP STATUS (field: "tripStatus"):
   - "running" (= active / live / in-transit / scheduled / ongoing / moving / open) -> tripStatus eq "running" (or "active")
   - "closed" (= completed / finished / inactive) -> tripStatus eq "InActive" (or "closed")
   - "cancelled" -> tripStatus eq "cancelled" (or "cancel")
   - "valid" (= default for all queries unless a specific tripStatus is requested or cancelled trips are included) -> tripStatus eq "valid"
   - If query explicitly asks to include cancelled trips ("including cancelled trips", "include cancel trip") -> do NOT include tripStatus eq "valid".

2. SHIPMENT METHODS (field: "shipmentMethod"):
   - "AIR_Network", "air network", "air truck", "air trips", "air" -> shipmentMethod eq "air"
   - "SFC_Network", "sfc network", "sfc truck", "sfc schedule", "sfc trips", "sfc" -> shipmentMethod eq "sfc"
   - "Speed_Truck", "speed truck", "speed network", "speed trips", "speed" -> shipmentMethod eq "speed"
   - "Feeder", "feeder truck", "feeder network", "feeder trips", "feeder" -> shipmentMethod eq "feeder"
   - "Empty", "empty truck", "empty network", "empty trips", "empty" -> shipmentMethod eq "empty"
   - "Customer Delivery", "delivery truck", "delivery trips", "delivery" -> shipmentMethod eq "delivery"
   - "Pick Up and Delivery", "pick and delivery", "delivery with pick & delivery" -> shipmentMethod in ["Delivery", "Pick Up and Delivery"]
   - "Pick Up", "pickup" -> shipmentMethod eq "Pick Up"
   - All trip queries for these shipment methods default to tripStatus eq "running" unless closed or cancelled is specifically requested.

3. ROUTE CATEGORIES (field: "routeCategory" / "tripType"):
   - "intercity", "network" -> routeCategory eq "intercity"
   - "intracity", "local" -> routeCategory eq "intracity"

4. ROUTE (field: "Route" / "RouteName"):
   - "route <Name>" -> Route eq "<Name>"

5. ORIGIN / DESTINATION:
   - "from <Location>", "source <Location>", "origin <Location>", "start from <Location>" -> field: "source", operator: "eq", value: "<Location>"
   - "to <Location>", "destination <Location>", "reaching <Location>", "end on <Location>" -> field: "destination", operator: "eq", value: "<Location>"

6. REGION (field: "region"):
   - "<Region> region" -> region eq "<Region>" (e.g. "East region" -> region eq "East")
   - "West and South region" -> region in ["West", "South"]

7. CLOSE REMARKS (field: "closeremark"):
   - "closed automatically via forcefull" -> closeremark eq "forcefully"
   - "closed automatically via geofence" -> closeremark eq "geofence"
   - Allowed values: "manual", "forcefully", "geofence", "polygon", "api", "backend", "web"

TRIP EXAMPLES:
- "Show all running trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "running"}], "select": []}
- "Show closed trips in September 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "InActive"}, {"field": "runDate", "operator": "gte", "value": "2026-09-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-09-30 23:59:59"}], "select": []}
- "Show cancelled trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "cancelled"}], "select": []}
- "How many trips ran in August 2026?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "Show all trips for the North region including cancelled trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "region", "operator": "eq", "value": "North"}], "select": []}
- "how the count of sfc network trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "tripStatus", "operator": "eq", "value": "running"}, {"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "shipmentMethod", "operator": "eq", "value": "sfc"}], "select": []}
- "Show active Air Intercity trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "tripStatus", "operator": "eq", "value": "active"}, {"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "shipmentMethod", "operator": "eq", "value": "air"}, {"field": "routeCategory", "operator": "eq", "value": "intercity"}], "select": []}
- "What is the trip count across major feeder types in August 2026?" / "What is the trip count across major route types in August 2026?" / "What is the trip count across major shipment method in August 2026?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": [], "groupBy": ["shipmentMethod"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]}
- "How many trips were Speed_Truck, Empty, Comm/BZ-Out, or Comm/BZ-In in August 2026?" / "breakdown of trips that were Speed_Truck, Empty, Comm/BZ-Out, or Comm/BZ-In in August 2026?" / "trips that were Speed_Truck v/s Empty v/s Comm/BZ-Out v/sComm/BZ-In in August 2026?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "shipmentMethod", "operator": "in", "value": ["speed", "empty", "Comm/BZ-Out", "Comm/BZ-In"]}], "select": [], "groupBy": ["shipmentMethod"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]}
- "How many trips used Secutrak_TP Fixed GPS in August 2026?" / "How many trips used Fleetx (fleetx_bluedart) Fixed GPS in August 2026?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "gps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "vendorgps", "operator": "contains", "value": "Secutrak_TP"}], "select": []}
- "How many trips used Kiasaint Fixed E-Lock in August 2026?" / "How many trips used G-Trac Fixed E-Lock in August 2026?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "fixedlock"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "vendorfixedelock", "operator": "contains", "value": "Kiasaint"}], "select": []}
- "How many trips used Kiasaint Portable E-Lock in August 2026?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "portablelock"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "vendorportableelock", "operator": "contains", "value": "Kiasaint"}], "select": []}
- "Lis of the vehicle was on trip this month for the transporter - DIDAR SINGH" / "how many vehicle was on trip this month for the transporter - DIDAR SINGH" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "THIS_MONTH_START"}, {"field": "runDate", "operator": "lte", "value": "THIS_MONTH_END"}, {"field": "transporter", "operator": "eq", "value": "DIDAR SINGH"}], "select": [], "groupBy": ["vehicleNo"], "metrics": [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]}
"""

