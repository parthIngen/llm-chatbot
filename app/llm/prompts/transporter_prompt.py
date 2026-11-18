# Transporter domain prompt: transporter filters, unique transporter counts, and fleet rules

TRANSPORTER_PROMPT = """
TRANSPORTER DOMAIN RULES:
1. TRANSPORTER FILTER (field: "Transporter" / "transporter"):
   - "transporter <Name>" / "vehicles belonging to transporter <Name>" -> Transporter eq "<Name>" (or transporter eq "<Name>")
   - "fleet <Fleet>" -> Fleet eq "<Fleet>"

2. UNIQUE TRANSPORTERS COUNT:
   - "unique transporters", "distinct transporters", "total number of unique transporters used":
     * groupBy: ["Transporter"]
     * metrics: [{"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}]
   - Default tripStatus to "valid" unless specific status is requested.

3. UNIQUE VEHICLES BY TRANSPORTER:
   - "unique vehicles used by transporter <Name>" / "List all unique vehicles used by transporter <Name>":
     * filter: Transporter eq "<Name>"
     * groupBy: ["Transporter"] (or ["vehicleNo"])
     * metrics: [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]

TRANSPORTER EXAMPLES:
- "Show total number of unique transporters used in Aug 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "groupId", "operator": "eq", "value": "0041"}], "groupBy": ["Transporter"], "metrics": [{"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}]}
- "What is the count of unique transporters recorded in Aug 2026?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "groupId", "operator": "eq", "value": "0041"}], "groupBy": ["Transporter"], "metrics": [{"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}]}
- "List all unique vehicles used by transporter INSTANT TRANSPORT SOLUTION (P) LTD in August 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "Transporter", "operator": "eq", "value": "INSTANT TRANSPORT SOLUTION (P) LTD"}], "select": [], "groupBy": ["Transporter"], "metrics": [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]}
"""

