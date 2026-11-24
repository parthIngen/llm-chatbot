import pytest
from app.llm.fast_path import build_fast_path_payload, can_resolve_fast_path
from app.llm.response_generator import build_natural_response

def test_branch_combined_top_5():
    query = "How many trips in August 2026 were recorded across the top 5 Branches combined (HUB, PNQ, BLR, HYD, NBM)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    assert payload["entity"] == "trip"
    assert payload["operation"] == "count"
    assert payload["select"] == []
    assert "groupBy" not in payload

    expected_filters = [
        {"field": "groupId", "operator": "eq", "value": "0041"},
        {"field": "tripStatus", "operator": "eq", "value": "valid"},
        {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"},
        {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"},
        {"field": "branch", "operator": "in", "value": ["HUB", "PNQ", "BLR", "HYD", "NBM"]}
    ]
    assert payload["filters"] == expected_filters

    reply = build_natural_response(total_count=0, filters=payload["filters"], message=query)
    assert "recorded across the top 5 Branches combined (HUB, PNQ, BLR, HYD, NBM)" in reply
    assert "Branch COMBINED" not in reply

def test_single_branch_still_eq():
    query = "How many trips in August 2026 were recorded under Branch PNQ?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    branch_filter = [f for f in payload["filters"] if f.get("field") == "branch"][0]
    assert branch_filter == {"field": "branch", "operator": "eq", "value": "PNQ"}

def test_area_combined_top_3():
    query = "How many trips in August 2026 were recorded across the top 3 Areas combined (DEL, BOM, BLR)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    area_filter = [f for f in payload["filters"] if f.get("field") == "area"][0]
    assert area_filter == {"field": "area", "operator": "in", "value": ["DEL", "BOM", "BLR"]}
    assert "groupBy" not in payload

    reply = build_natural_response(total_count=10, filters=payload["filters"], message=query)
    assert "recorded across the top 3 Areas combined (DEL, BOM, BLR)" in reply

def test_transporters_combined():
    query = "How many trips in August 2026 were operated across Transporters combined (LOCALCASH, DINESH TEMPO SERVICE, TATA)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    trans_filter = [f for f in payload["filters"] if f.get("field") == "transporter"][0]
    assert trans_filter == {"field": "transporter", "operator": "in", "value": ["LOCALCASH", "DINESH TEMPO SERVICE", "TATA"]}

    reply = build_natural_response(total_count=42, filters=payload["filters"], message=query)
    assert "operated by LOCALCASH, DINESH TEMPO SERVICE, and TATA combined" in reply

def test_routes_combined():
    query = "How many trips in August 2026 were recorded across Routes combined (R1, R2, R3)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    route_filter = [f for f in payload["filters"] if f.get("field") == "Route"][0]
    assert route_filter == {"field": "Route", "operator": "in", "value": ["R1", "R2", "R3"]}

def test_sources_combined():
    query = "How many trips in August 2026 originated across Sources combined (HUB, PNQ, BLR)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    source_filter = [f for f in payload["filters"] if f.get("field") == "source"][0]
    assert source_filter == {"field": "source", "operator": "in", "value": ["HUB", "PNQ", "BLR"]}

def test_destinations_combined():
    query = "How many trips in August 2026 reached across Destinations combined (HUB, PNQ, BLR)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    dest_filter = [f for f in payload["filters"] if f.get("field") == "destination"][0]
    assert dest_filter == {"field": "destination", "operator": "in", "value": ["HUB", "PNQ", "BLR"]}

def test_vehicles_combined():
    query = "How many trips in August 2026 were run across Vehicles combined (UP32AB1234, DL01CD5678)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    veh_filter = [f for f in payload["filters"] if f.get("field") == "vehicleNo"][0]
    assert veh_filter == {"field": "vehicleNo", "operator": "in", "value": ["UP32AB1234", "DL01CD5678"]}

def test_regions_combined():
    query = "How many trips in August 2026 were in Regions combined (North, South)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    reg_filter = [f for f in payload["filters"] if f.get("field") == "region"][0]
    assert reg_filter == {"field": "region", "operator": "in", "value": ["North", "South"]}

def test_shipment_methods_combined():
    query = "How many trips in August 2026 were across Shipment Methods combined (speed, empty)?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    sm_filter = [f for f in payload["filters"] if f.get("field") == "shipmentMethod"][0]
    assert sm_filter == {"field": "shipmentMethod", "operator": "in", "value": ["speed", "empty"]}

