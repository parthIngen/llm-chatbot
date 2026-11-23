import sys, os
sys.path.insert(0, os.path.abspath("."))
import asyncio
import json
from app.llm.fast_path import build_fast_path_payload
from app.llm.intent_detector import detect_device_analytics
from app.llm.response_generator import build_natural_response

query = "How many trips in August 2026 used Fixed GPS and Fixed E-Lock together (without Portable E-Lock)?"

print("--- Testing fast_path payload ---")
payload = build_fast_path_payload(query)
print(json.dumps(payload, indent=2))

expected_filters = [
    {"field": "groupId", "operator": "eq", "value": "0041"},
    {"field": "tripStatus", "operator": "eq", "value": "valid"},
    {"field": "GPS", "operator": "eq", "value": "existing"},
    {"field": "FixedELock", "operator": "eq", "value": "existing"},
    {"field": "PortableELock", "operator": "eq", "value": "na"},
    {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"},
    {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}
]

assert payload["entity"] == "trip", f"Entity mismatch: {payload.get('entity')}"
assert payload["operation"] == "count", f"Operation mismatch: {payload.get('operation')}"
assert payload["analytics"] == [{"type": "withgps"}], f"Analytics mismatch: {payload.get('analytics')}"
assert payload["select"] == [], f"Select mismatch: {payload.get('select')}"
assert payload["filters"] == expected_filters, f"Filters mismatch:\nGot: {payload.get('filters')}\nExpected: {expected_filters}"

print("SUCCESS: Fast path payload perfectly matches expected structure!")

print("--- Testing detect_device_analytics ---")
dev_res = detect_device_analytics(query)
print(json.dumps(dev_res, indent=2))
assert dev_res is not None
assert dev_res["analytics_type"] == "withgps"
assert dev_res["device_filters"] == [
    {"field": "GPS", "operator": "eq", "value": "existing"},
    {"field": "FixedELock", "operator": "eq", "value": "existing"},
    {"field": "PortableELock", "operator": "eq", "value": "na"}
]
print("SUCCESS: detect_device_analytics matches expected structure!")

print("--- Testing response generator ---")
resp = build_natural_response(
    total_count=123,
    filters=expected_filters,
    analytics=[{"type": "withgps"}],
    message=query
)
print("Response:", resp)
assert "using Fixed GPS and Fixed E-Lock together (without Portable E-Lock)" in resp, f"Unexpected response: {resp}"
assert "all three devices" not in resp, f"Hallucination found in response: {resp}"
print("SUCCESS: Response generator output is verified!")

print("--- Testing regression queries ---")
# 0. Fixed GPS and Portable E-Lock together (without Fixed E-Lock)
q_gps_portable = "How many trips in August 2026 used Fixed GPS and Portable E-Lock together (without Fixed E-Lock)?"
p_gps_portable = build_fast_path_payload(q_gps_portable)
assert p_gps_portable["analytics"] == [{"type": "withgps"}]
assert p_gps_portable["filters"] == [
    {"field": "groupId", "operator": "eq", "value": "0041"},
    {"field": "tripStatus", "operator": "eq", "value": "valid"},
    {"field": "GPS", "operator": "eq", "value": "existing"},
    {"field": "FixedELock", "operator": "eq", "value": "na"},
    {"field": "PortableELock", "operator": "eq", "value": "existing"},
    {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"},
    {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}
]
resp_gps_portable = build_natural_response(total_count=50, filters=p_gps_portable["filters"], analytics=[{"type": "withgps"}], message=q_gps_portable)
assert "using Fixed GPS and Portable E-Lock together (without Fixed E-Lock)" in resp_gps_portable

# 1. Exclusive portable lock
q_portable = "How many trips in August 2026 used Portable E-Lock exclusively (without Fixed GPS or Fixed E-Lock)?"
p_portable = build_fast_path_payload(q_portable)
assert p_portable["analytics"] == [{"type": "portablelockonly"}], f"Portable lock only failed: {p_portable}"

# 2. Tatamotors GPS
q_tata = "How many trips in August 2026 were tracked by tatamotors Fixed GPS?"
p_tata = build_fast_path_payload(q_tata)
assert p_tata["analytics"] == [{"type": "gps"}], f"Tatamotors analytics failed: {p_tata}"
assert any(f.get("field") == "vendorgps" and f.get("value") == "tatamotors" for f in p_tata["filters"]), f"Tatamotors filter failed: {p_tata}"

# 3. No transporter
q_notrans = "How many trips in August 2026 had no Transporter?"
p_notrans = build_fast_path_payload(q_notrans)
assert any(f.get("field") == "transporter" and f.get("value") == "na" for f in p_notrans["filters"]), f"No transporter failed: {p_notrans}"

# 4. Outside vehicle master
q_outside = "How many trips in August 2026 had a vehicle outside the Vehicle Master?"
p_outside = build_fast_path_payload(q_outside)
assert any(f.get("field") == "vehicleMaster" and f.get("value") == "outside" for f in p_outside["filters"]), f"Outside vehicle master failed: {p_outside}"

# 5. BOB source location
q_bob = "How many Pick Up trips originated from location BOB in August 2026?"
p_bob = build_fast_path_payload(q_bob)
assert any(f.get("field") == "source" and f.get("value") == "BOB" for f in p_bob["filters"]), f"BOB location failed: {p_bob}"

print("SUCCESS: All regression tests passed flawlessly!")
