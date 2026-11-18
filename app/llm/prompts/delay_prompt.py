# Delay domain prompt: departure delayed, arrival delayed, ATD, ATA

DELAY_PROMPT = """
DELAY DOMAIN RULES:
1. DELAYED DEPARTURE / ARRIVAL (analytics: "departureDelayed" / "arrivalDelayed"):
   - "departure was delayed", "delayed departure trips", "vehicle departure was delayed" ->
     analytics: [{"type": "departureDelayed"}]
   - "arrival was delayed", "delayed arrival trips", "vehicle arrival was delayed" ->
     analytics: [{"type": "arrivalDelayed"}]

2. ATD (Actual Time of Departure - field: "atd"):
   - "ATD not captured" / "ATD was not captured" -> atd eq "notcaptured"
   - "ATD captured" -> atd eq "captured"
   - "ATD missing" / "missing ATD" -> atd eq "missing"
   - "ATD not missing" -> atd eq "notmissing"

3. ATA (Actual Time of Arrival - field: "ata"):
   - "ATA not captured" / "ATA was not captured" -> ata eq "notcaptured"
   - "ATA captured" -> ata eq "captured"
   - "ATA missing" / "missing ATA" -> ata eq "missing"
   - "ATA not missing" -> ata eq "notmissing"

4. DEVICE ACTIVE BUT ATD / ATA MISSING:
   - "GPS is active but ATD is not captured" ->
     analytics: [{"type": "gps"}], filter: GPS eq "active", filter: atd eq "notcaptured"
   - "GPS is active but ATD is missing" ->
     analytics: [{"type": "gps"}], filter: GPS eq "active", filter: atd eq "missing"
   - "GPS and Fixed Lock are active but ATD is missing" ->
     analytics: [{"type": "withgps"}], filter: GPS eq "active", filter: FixedELock eq "active", filter: atd eq "missing"

DELAY EXAMPLES:
- "Show delayed departure trips in August" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "departureDelayed"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "Show delayed arrival trips" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "arrivalDelayed"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}], "select": []}
- "How many trips have missing ATD?" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "atd", "operator": "eq", "value": "missing"}], "select": []}
- "Show trips where ATA was not captured" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "ata", "operator": "eq", "value": "notcaptured"}], "select": []}
- "Download all trips from January 2026 where ATA was not captured" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "runDate", "operator": "gte", "value": "2026-01-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-01-31 23:59:59"}, {"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "ATA", "operator": "eq", "value": "notcaptured"}], "select": []}
"""

