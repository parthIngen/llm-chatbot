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


# ==============================================================================
# TESTS FOR TOP N GROUPED QUERIES (Branches, Areas, States, Transporters, Vehicles)
# ==============================================================================

@pytest.mark.parametrize("query,expected_field,expected_limit", [
    ("Which top 5 Branches generated the highest trip counts in August 2026?", "branch", 5),
    ("Which top 5 Areas generated the highest trip counts in August 2026?", "area", 5),
    ("Which top 5 states generated the highest trip volumes in August 2026?", "state", 5),
    ("Which top 5 transporter generated the highest trip volumes in August 2026?", "transporter", 5),
    ("Which top 5 vehicle generated the highest trip volumes in August 2026?", "vehicleNo", 5),
    ("Which top 10 routes had the highest trip volumes in August 2026?", "Route", 10),
    ("Top 3 shipment methods by trip count in August 2026", "shipmentMethod", 3),
    ("Top 4 regions by trip volumes in August 2026", "region", 4),
    ("Which top 20 branches had highest trip volume in August 2026?", "branch", 20),
])
def test_top_n_payload_generation(query, expected_field, expected_limit):
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    assert payload["entity"] == "trip"
    assert payload["operation"] == "count"
    assert payload["groupBy"] == [expected_field]
    assert payload["metrics"] == [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]
    assert payload["sort"] == [{"field": "TotalTrip", "order": "desc"}]
    assert payload["select"] == []

    # Check date filters for August 2026
    date_gte = [f for f in payload["filters"] if f.get("field") == "runDate" and f.get("operator") == "gte"][0]
    date_lte = [f for f in payload["filters"] if f.get("field") == "runDate" and f.get("operator") == "lte"][0]
    assert date_gte["value"] == "2026-08-01 00:00:00"
    assert date_lte["value"] == "2026-08-31 23:59:59"

    # Make sure no eq/in filter was added for the group field itself
    assert not any(f.get("field") == expected_field for f in payload["filters"])


def _run_sync(coro):
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise RuntimeError("Coroutine did not complete synchronously")


def test_top_5_branches_html_response_formatting_and_strict_limit():
    from app.llm.response_generator import ResponseGenerator
    query = "Which top 5 Branches generated the highest trip counts in August 2026?"
    rg = ResponseGenerator()

    # Simulate API returning 8 records
    mock_api_data = [
        {"branch": "HUB", "TotalTrip": 1500},
        {"branch": "PNQ", "TotalTrip": 1200},
        {"branch": "BLR", "TotalTrip": 950},
        {"branch": "HYD", "TotalTrip": 800},
        {"branch": "NBM", "TotalTrip": 650},
        {"branch": "DEL", "TotalTrip": 500},
        {"branch": "CCU", "TotalTrip": 400},
        {"branch": "MAA", "TotalTrip": 300},
    ]

    response = _run_sync(rg.generate_response(query, mock_api_data, session_id="test_top5"))

    # Assert proper HTML table formatting with inline CSS
    assert "<table" in response
    assert "<thead>" in response
    assert "<tbody>" in response
    assert "Top 5 Branches by Trip Volume" in response
    assert "August 2026" in response
    assert "HUB" in response
    assert "PNQ" in response
    assert "BLR" in response
    assert "HYD" in response
    assert "NBM" in response

    # DEL, CCU, MAA should NOT be in the table because limit is strictly 5
    assert "DEL" not in response
    assert "CCU" not in response
    assert "MAA" not in response

    # Verify exactly 5 table body rows in the rendered table
    assert response.count("<tr style=\"border-bottom: 1px solid #f1f5f9;") == 5
    assert "1,500 trips" in response
    assert "1,200 trips" in response


def test_top_3_vehicles_html_response_formatting():
    from app.llm.response_generator import ResponseGenerator
    query = "Which top 3 vehicle generated the highest trip volumes in August 2026?"
    rg = ResponseGenerator()

    mock_api_data = [
        {"vehicleNo": "MH12AB1234", "TotalTrip": 85},
        {"vehicleNo": "DL01CD5678", "TotalTrip": 72},
        {"vehicleNo": "KA04EF9012", "TotalTrip": 60},
        {"vehicleNo": "GJ06GH3456", "TotalTrip": 45},
    ]

    response = _run_sync(rg.generate_response(query, mock_api_data, session_id="test_veh"))

    # Must contain table and IND badge
    assert "<table" in response
    assert "IND" in response
    assert "MH12AB1234" in response
    assert "DL01CD5678" in response
    assert "KA04EF9012" in response
    # 4th item must not be included
    assert "GJ06GH3456" not in response
    assert response.count("<tr style=\"border-bottom: 1px solid #f1f5f9;") == 3


def test_top_5_transporters_html_response_formatting():
    from app.llm.response_generator import ResponseGenerator
    query = "Which top 5 transporter generated the highest trip volumes in August 2026?"
    rg = ResponseGenerator()

    mock_api_data = [
        {"transporter": "VRL LOGISTICS", "TotalTrip": 3200},
        {"transporter": "TCI FREIGHT", "TotalTrip": 2800},
        {"transporter": "GATI", "TotalTrip": 2100},
        {"transporter": "SAFEXPRESS", "TotalTrip": 1900},
        {"transporter": "SPOTON", "TotalTrip": 1500},
        {"transporter": "DELHIVERY", "TotalTrip": 1100},
    ]

    response = _run_sync(rg.generate_response(query, mock_api_data, session_id="test_trans"))

    assert "<table" in response
    assert "VRL LOGISTICS" in response
    assert "TCI FREIGHT" in response
    assert "GATI" in response
    assert "SAFEXPRESS" in response
    assert "SPOTON" in response
    assert "DELHIVERY" not in response
    assert response.count("<tr style=\"border-bottom: 1px solid #f1f5f9;") == 5
    assert "3,200 trips" in response



