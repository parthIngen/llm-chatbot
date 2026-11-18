# Vehicle domain prompt: vehicle numbers, unique vehicle counts, active vehicles on field

VEHICLE_PROMPT = """
VEHICLE DOMAIN RULES:
1. SPECIFIC VEHICLE NUMBER (field: "vehicleNo"):
   - "vehicle <vehicleNo>", "trips for vehicle <vehicleNo>" (e.g. "DL01HU5859", "UP32AB1234") ->
     filter: vehicleNo eq "<vehicleNo>"

2. UNIQUE / DISTINCT VEHICLES COUNT:
   - "unique vehicles", "distinct vehicles", "total distinct vehicles", "List all distinct vehicles used":
     * groupBy: ["vehicleNo"]
     * metrics: [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
   - Default tripStatus to "valid" unless specific status is requested.

3. ACTIVE VEHICLES OPERATING ON THE FIELD:
   - "active vehicles operating on the field", "operating on the field":
     * groupBy: ["vehicleNo"]
     * metrics: [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
     * tripStatus eq "valid" (or "active")

4. VEHICLES COMBINED WITH TRANSPORTER:
   - "count total unique vehicles and distinct transporters" ->
     * groupBy: ["Transporter"]
     * metrics: [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]

VEHICLE EXAMPLES:
- "Show trips for vehicle UP32AB1234" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "vehicleNo", "operator": "eq", "value": "UP32AB1234"}], "select": []}
- "List all distinct vehicles used in Aug 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "groupId", "operator": "eq", "value": "0041"}], "groupBy": ["vehicleNo"], "metrics": [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]}
- "Count all active vehicles operating on the field during Aug 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "groupBy": ["vehicleNo"], "metrics": [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]}
"""

