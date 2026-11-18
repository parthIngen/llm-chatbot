# GPS domain prompt: GPS devices, status, vendors, and analytics

GPS_PROMPT = """
GPS DOMAIN RULES:
1. FIXED GPS / GPS QUERIES & ANALYTICS:
   - "Total gps device in <period>" -> analytics: [{"type": "gps"}], select: [] (no GPS filter)
   - "Total gps NA or not available device in <period>" -> analytics: [{"type": "gpsna"}], select: []
   - "GPS with No Connectivity or Inactive GPS" -> analytics: [{"type": "gps"}], filter: GPS eq "inactive", select: []
   - "GPS with active GPS" -> analytics: [{"type": "gps"}], filter: GPS eq "active", select: []
   - "Trip with Only with single GPS or Fixed GPS only" -> analytics: [{"type": "gpsonly"}], select: []
   - "Trip with Only with Inactive single GPS or Inactive Fixed GPS only" -> analytics: [{"type": "fixedgpsonly"}], filter: GPS eq "inactive", select: []
   - "Trip with Only with Active single GPS or Active Fixed GPS only" -> analytics: [{"type": "fixedgpsonly"}], filter: GPS eq "active", select: []

2. NON-GPS / WITHOUT GPS:
   - "trips without GPS" / "vehicles had no GPS" / "Non-GPS" / "no GPS" -> analytics: [{"type": "gpsna"}]

3. GPS VENDORS (field: "vendor" / "vendorgps"):
   - "ICICI" / "ILGIC" devices map to "Secutrak" -> vendor eq "Secutrak" (or vendorgps eq "Secutrak")
   - "Secutrak" devices -> vendor eq "Secutrak" (or vendorgps eq "Secutrak")
   - "3rd party device" / "portable device" -> vendor eq "ThirdParty"
   - "Wheelseye" -> vendor contains "Wheelseye"
   - When query asks for vehicles running on vendor devices: tripStatus eq "running", vendor contains "<Vendor>", select: []. Do not include runDate filters for vehicles running on vendor devices.

4. GPS STATUS FIELD (field: "FixedGPS" or "GPS"):
   - values: "active", "inactive", "nogps", "existing", "NA"

GPS EXAMPLES:
- "Show trips without GPS" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "gpsna"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}], "select": []}
- "Total gps device in august 2026" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "gps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "GPS with active GPS August 2026" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "gps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "GPS", "operator": "eq", "value": "active"}], "select": []}
- "Generate the July 2026 report for all Secutrak devices" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "vendor", "operator": "eq", "value": "Secutrak"}, {"field": "runDate", "operator": "gte", "value": "2026-07-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-07-31 23:59:59"}], "select": []}
- "Generate the Wheelseye device report for April 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "vendor", "operator": "contains", "value": "Wheelseye"}, {"field": "runDate", "operator": "gte", "value": "2026-04-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-04-30 23:59:59"}], "select": []}
"""

