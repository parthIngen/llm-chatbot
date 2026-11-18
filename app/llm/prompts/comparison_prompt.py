# Comparison prompt: trip type comparisons, region breakdowns, priority comparisons

COMPARISON_PROMPT = """
COMPARISON & BREAKDOWN RULES:
1. TRIP TYPE COMPARISON (Network vs Local / Intercity vs Intracity):
   - "Compare Network vs Local trips", "How many trips were Intracity vs Intercity in August 2026?":
     * groupBy: ["tripType"]
     * metrics: [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]
   - Default tripStatus to "valid" unless specified otherwise.
   - For device comparisons across trip types (e.g. Non-GPS or active Fixed GPS):
     * include analytics (e.g. gpsna or gps) and groupBy: ["tripType"], metrics: [{"field": "shipmentNo", "function": "count", "alias": "count"}]

2. REGION BREAKDOWN:
   - "How many trips occurred in each region", "region-wise trip counts", "show trip counts by region":
     * groupBy: ["region"]
     * metrics: [{"field": "shipmentNo", "function": "count", "alias": "Shipments"}]

3. PRIORITY RULES:
   - Single priority:
     * "marked as high priority", "high priority trips" -> priority eq "high"
     * "marked as low priority", "low priority", "standard priority", "normal priority" -> priority eq "low"
   - Priority comparison:
     * "high priority vs standard in aug 2026", "High Priority vs Normal Priority trips":
       * groupBy: ["priority"]
       * metrics: [{"field": "shipmentNo", "function": "count", "alias": "uniqueShipment"}]

COMPARISON EXAMPLES:
- "Compare Network and Local trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}], "groupBy": ["tripType"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]}
- "How many trips were Intracity vs. Intercity in August 2026?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "groupBy": ["tripType"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]}
- "Show region-wise trip counts" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}], "groupBy": ["region"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "Shipments"}]}
- "How many trips occurred in each region in Aug 2026?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "groupBy": ["region"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "Shipments"}]}
- "Show High Priority vs Normal Priority trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}], "groupBy": ["priority"], "metrics": [{"field": "shipmentNo", "function": "count", "alias": "uniqueShipment"}]}
"""

