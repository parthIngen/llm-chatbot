# Device combination prompt: multi-device configurations, only-device queries, and without-device rules

DEVICE_COMBINATION_PROMPT = """
DEVICE COMBINATION RULES:
1. ONLY SINGLE DEVICE RULES:
   - "Fixed GPS only" / "only with Fixed GPS" / "Fixed GPS as single device":
     filters: GPS eq "existing", FixedELock eq "na", PortableELock eq "na"
   - "Portable E-Lock only" / "only portable E-Lock":
     filters: GPS eq "na", FixedELock eq "na", PortableELock eq "existing"
   - "Fixed E-Lock only" / "only Fixed E-Lock":
     filters: GPS eq "na", FixedELock eq "existing", PortableELock eq "na"

2. COMBINATION OF DEVICES TOGETHER:
   - When query asks about Fixed GPS + Fixed/Portable E-Lock together (e.g. "Fixed GPS and Fixed E-Lock", "Fixed GPS, Fixed E-Lock, and Portable E-Lock together"):
     * top-level: "analytics": [{"type": "withgps"}]
     * field: "GPS", operator: "eq", value: "existing"
     * field: "FixedELock", operator: "eq", value: "existing" (if Fixed E-Lock mentioned)
     * field: "PortableELock", operator: "eq", value: "existing" (if Portable E-Lock mentioned)
   - When query mentions BOTH "Fixed E-Lock" AND "Portable E-Lock" together without GPS:
     * top-level: "analytics": [{"type": "withgps"}]
     * field: "PortableELock", operator: "eq", value: "existing"
     * field: "FixedELock", operator: "eq", value: "existing"

3. WITHOUT ANY DEVICE:
   - "Trip without any Device or GPS and Fixed Elock and Portable Elock":
     * top-level: "analytics": [{"type": "withoutanygps"}]

4. WITHOUT ONE DEVICE BUT WITH OTHERS (type: "withoutorgps"):
   - "not Fixed Elock but having GPS and Portable Elock only":
     analytics: [{"type": "withoutorgps"}], GPS eq "existing", PortableELock eq "existing", FixedELock eq "na"
   - "not Portable Elock but having GPS and Fixed Elock only":
     analytics: [{"type": "withoutorgps"}], GPS eq "existing", FixedELock eq "existing", PortableELock eq "na"
   - "not Fixed GPS but having Portable Elock and Fixed Elock only":
     analytics: [{"type": "withoutorgps"}], GPS eq "na", PortableELock eq "existing", FixedELock eq "existing"
   - With active / inactive statuses: add GPS / FixedELock / PortableELock status filters accordingly.

DEVICE COMBINATION EXAMPLES:
- "Show vehicles with Fixed GPS only" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "GPS", "operator": "eq", "value": "existing"}, {"field": "FixedELock", "operator": "eq", "value": "na"}, {"field": "PortableELock", "operator": "eq", "value": "na"}], "groupBy": ["vehicleNo"], "metrics": [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]}
- "Show trips with Fixed GPS and Fixed E-Lock" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "GPS", "operator": "eq", "value": "existing"}, {"field": "FixedELock", "operator": "eq", "value": "existing"}], "select": []}
- "How many trips used Fixed GPS, Fixed E-Lock, and Portable E-Lock together Aug 2026?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "GPS", "operator": "eq", "value": "existing"}, {"field": "PortableELock", "operator": "eq", "value": "existing"}, {"field": "FixedELock", "operator": "eq", "value": "existing"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "How many trips in August 2026 used Fixed GPS and Fixed E-Lock together (without Portable E-Lock)?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "GPS", "operator": "eq", "value": "existing"}, {"field": "FixedELock", "operator": "eq", "value": "existing"}, {"field": "PortableELock", "operator": "eq", "value": "na"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "How many trips in August 2026 used Fixed GPS and Fixed E-Lock without Portable E-Lock?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "FixedELock", "operator": "eq", "value": "existing"}, {"field": "PortableELock", "operator": "eq", "value": "na"}, {"field": "GPS", "operator": "eq", "value": "existing"}], "select": []}
- "How many trips in August 2026 used Fixed GPS and Portable E-Lock together (without Fixed E-Lock)?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "GPS", "operator": "eq", "value": "existing"}, {"field": "FixedELock", "operator": "eq", "value": "na"}, {"field": "PortableELock", "operator": "eq", "value": "existing"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "How many trips in August 2026 used Fixed GPS and Portable E-Lock without Fixed E-Lock?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "FixedELock", "operator": "eq", "value": "na"}, {"field": "PortableELock", "operator": "eq", "value": "existing"}, {"field": "GPS", "operator": "eq", "value": "existing"}], "select": []}
- "How many trips in August 2026 used both Fixed E-Lock and Portable E-Lock without Fixed GPS?" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "FixedELock", "operator": "eq", "value": "existing"}, {"field": "PortableELock", "operator": "eq", "value": "existing"}, {"field": "GPS", "operator": "eq", "value": "na"}], "select": []}
- "Show Intracity trip count for vehicles using both Fixed E-Lock and Portable E-Lock Aug 2026" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withgps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "PortableELock", "operator": "eq", "value": "existing"}, {"field": "FixedELock", "operator": "eq", "value": "existing"}, {"field": "routeCategory", "operator": "eq", "value": "intracity"}]}
- "Trip without any Device or GPS and Fixed Elock and Portable Elock August 2026" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "withoutanygps"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
"""

