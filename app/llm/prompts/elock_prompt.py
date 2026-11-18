# E-Lock domain prompt: Fixed E-Lock, Portable E-Lock, device status, and analytics

ELOCK_PROMPT = """
E-LOCK DOMAIN RULES:
1. FIXED E-LOCK QUERIES & ANALYTICS:
   - "Total fixed elock device in <period>" -> analytics: [{"type": "fixedlock"}], select: []
   - "Total elock NA or not available device in <period>" -> analytics: [{"type": "fixedlockna"}], select: []
   - "Fixed lock with No Connectivity or Inactive" -> analytics: [{"type": "fixedlock"}], filter: FixedELock eq "inactive", select: []
   - "Fixed Elock with active" -> analytics: [{"type": "fixedlock"}], filter: FixedELock eq "active", select: []
   - "Trip with Only with single Fixed Elock" -> analytics: [{"type": "fixedlockonly"}], select: []
   - "Trip with Only with Inactive single Fixed ELOCK only" -> analytics: [{"type": "fixedelockonly"}], filter: FixedELock eq "inactive", select: []
   - "Trip with Only with Active single Elock" -> analytics: [{"type": "fixedelockonly"}], filter: FixedELock eq "active", select: []
   - "active device count for Fixed E-Lock trips" -> FixedELock eq "active"

2. PORTABLE E-LOCK QUERIES & ANALYTICS:
   - "Total portable elock device in <period>" -> analytics: [{"type": "portablelock"}], select: []
   - "Total portable elock NA or not available device in <period>" -> analytics: [{"type": "portablelockna"}], select: []
   - "Portable lock with No Connectivity or Inactive" -> analytics: [{"type": "portablelock"}], filter: PortableELock eq "inactive", select: []
   - "Portable Elock with active" -> analytics: [{"type": "portablelock"}], filter: PortableELock eq "active", select: []
   - "Trip with Only with single Portable Elock" -> analytics: [{"type": "portablelockonly"}], select: []
   - "Trip with Only with Inactive single Portable ELOCK only" -> analytics: [{"type": "portableelockonly"}], filter: PortableELock eq "inactive", select: []
   - "Trip with Only with Active single Portbale Elock" -> analytics: [{"type": "portableelockonly"}], filter: PortableELock eq "active", select: []
   - "active device count for Portable E-Lock trips" -> PortableELock eq "active"

3. FIELD NAMES & VALUES:
   - Fixed E-Lock field: "FixedELock" (values: "active", "inactive", "existing", "na")
   - Portable E-Lock field: "PortableELock" (values: "active", "inactive", "existing", "na")
   - Vendor fields: "vendorfixedelock", "vendorportableelock"

E-LOCK EXAMPLES:
- "Show trips with Fixed E-Lock" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "FixedELock", "operator": "eq", "value": "existing"}], "select": []}
- "Total fixed elock device in august 2026" ->
  {"entity": "trip", "operation": "count", "analytics": [{"type": "fixedlock"}], "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}], "select": []}
- "Show inactive Portable E-Lock trips" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "tripStatus", "operator": "eq", "value": "valid"}, {"field": "PortableELock", "operator": "eq", "value": "inactive"}], "select": []}
- "Show total count of portable E-Lock trips in Aug 2026" ->
  {"entity": "trip", "operation": "count", "filters": [{"field": "groupId", "operator": "eq", "value": "0041"}, {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"}, {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}, {"field": "PortableELock", "operator": "eq", "value": "existing"}], "select": []}
"""

