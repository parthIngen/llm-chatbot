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


def test_highest_trip_in_which_region_payload_and_multi_turn_response():
    from app.llm.response_generator import ResponseGenerator
    query = "highest trip in which region"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    assert payload["groupBy"] == ["region"]
    assert payload["metrics"] == [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]
    assert payload["sort"] == [{"field": "TotalTrip", "order": "desc"}]

    # In turn 2, user sends "in aug", entities is populated with date range and preserved groupBy/sort
    rg = ResponseGenerator()
    message = "in aug"
    entities = {
        "entity": "trip",
        "operation": "count",
        "filters": [
            {"field": "groupId", "operator": "eq", "value": "0041"},
            {"field": "tripStatus", "operator": "eq", "value": "valid"},
            {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"},
            {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}
        ],
        "select": [],
        "groupBy": ["region"],
        "metrics": [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}],
        "sort": [{"field": "TotalTrip", "order": "desc"}]
    }
    api_data = [
        {"TotalTrip": 60337, "region": "NORTH"},
        {"TotalTrip": 54135, "region": "WEST"},
        {"TotalTrip": 43699, "region": "SOUTH"},
        {"TotalTrip": 14786, "region": "EAST"},
        {"TotalTrip": 580, "region": ""},
        {"TotalTrip": 2, "region": None}
    ]

    response = _run_sync(rg.generate_response(message, api_data, "test_session", entities))

    # Must state the winning region directly
    assert "highest trip volume in August 2026 was recorded in the **NORTH** region with **60,337 valid trips**" in response
    # Must NOT say "Total 6 valid trips found"
    assert "Total 6 valid trips found" not in response
    # Must render styled table
    assert "<table" in response
    assert "NORTH" in response
    assert "WEST" in response
    assert "SOUTH" in response
    assert "EAST" in response
    # Empty string and None regions must be filtered out
    assert response.count("<tr style=\"border-bottom: 1px solid #f1f5f9;") == 4
    assert "60,337 trips" in response
    assert "54,135 trips" in response


# ==============================================================================
# TESTS FOR CLOSE BY DEVICE QUERIES (Primary, Secondary, Tertiary, Comparison)
# ==============================================================================

@pytest.mark.parametrize("query,expected_device", [
    ("How many trips in August 2026 were closed by Primary Close By Device?", "Primary"),
    ("How many  trips in August 2026 were closed by Primary Close By Device?", "Primary"),
    ("How many trips in August 2026 were closed by Secondary Close By Device?", "Secondary"),
    ("How many  trips in August 2026 were closed by Secondary Close By Device?", "Secondary"),
    ("How many trips in August 2026 were closed by Tertiary Close By Device?", "Tertiary"),
    ("How many  trips in August 2026 were closed by Tertiary Close By Device?", "Tertiary"),
])
def test_close_by_device_single_payload(query, expected_device):
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
        {"field": "closeByDevice", "operator": "eq", "value": expected_device}
    ]
    assert payload["filters"] == expected_filters

    # Test natural language response
    reply = build_natural_response(total_count=100, filters=payload["filters"], message=query)
    assert f"closed by {expected_device} Close By Device" in reply
    assert "Total 100 valid trips" in reply
    assert "It does not include cancelled trips." in reply


def test_close_by_device_comparison_payload():
    queries = [
        "How many trips in August 2026 were closed by Primary, Secondary, vs. Tertiary Close By Device?",
        "How many trips in August 2026 were closed by Primary vs Secondary vs Tertiary Close By Device?",
        "How many  trips in August 2026 were closed by Primary, Secondary, vs. Tertiary Close By Device?",
    ]
    for q in queries:
        assert can_resolve_fast_path(q) is True
        payload = build_fast_path_payload(q)
        assert payload is not None
        assert payload["entity"] == "trip"
        assert payload["operation"] == "count"
        assert payload["select"] == []
        assert payload["groupBy"] == ["closeByDevice"]
        assert payload["metrics"] == [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]

        expected_filters = [
            {"field": "groupId", "operator": "eq", "value": "0041"},
            {"field": "tripStatus", "operator": "eq", "value": "valid"},
            {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"},
            {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"}
        ]
        assert payload["filters"] == expected_filters
        assert not any(f.get("field") == "closeByDevice" for f in payload["filters"])


def test_close_by_device_comparison_response_rendering():
    from app.llm.response_generator import ResponseGenerator
    query = "How many trips in August 2026 were closed by Primary, Secondary, vs. Tertiary Close By Device?"
    rg = ResponseGenerator()

    mock_api_data = [
        {"closeByDevice": "Primary", "TotalTrip": 60337},
        {"closeByDevice": "Secondary", "TotalTrip": 54135},
        {"closeByDevice": "Tertiary", "TotalTrip": 14786},
    ]

    response = _run_sync(rg.generate_response(query, mock_api_data, "test_session"))

    assert "Here is the breakdown of trips closed by Close By Device in August 2026:" in response
    assert "<table" in response
    assert "Primary" in response
    assert "Secondary" in response
    assert "Tertiary" in response
    assert "60,337 trips" in response
    assert "54,135 trips" in response
    assert "14,786 trips" in response
    assert response.count("<tr style=\"border-bottom: 1px solid #f1f5f9;") == 3


def test_top_n_combined_no_spurious_filters():
    from app.llm.intent_detector import IntentDetector
    detector = IntentDetector()

    test_cases = [
        ("How many  trips in August 2026 were generated by the top 5  route combined ?", "Route"),
        ("How many  trips in August 2026 were generated by the top 5 states combined ?", "state"),
        ("How many trips in August 2026 were generated by the top 5 branches combined ?", "branch"),
        ("How many trips in August 2026 were generated by the top 5 transporters combined ?", "transporter"),
        ("How many trips in August 2026 were generated by the top 5 vehicles combined ?", "vehicleNo"),
    ]

    for q, expected_field in test_cases:
        res = _run_sync(detector.detect(q, [], "test_session"))
        entities = res.get("entities", {})

        # Verify groupBy
        assert entities.get("groupBy") == [expected_field], f"Failed for {q}"

        # Verify metrics & sort
        assert entities.get("metrics") is not None
        assert entities.get("sort") is not None

        # Verify filters DO NOT contain spurious Route: "combined" or any other invalid dimension filters
        filters = entities.get("filters", [])
        for f in filters:
            assert f.get("value") != "combined", f"Found 'combined' in filter {f} for {q}"
            if f.get("field") in (expected_field, "Route", "route"):
                assert f.get("operator") != "eq" or f.get("field") not in (expected_field, "Route")

        # Filters should only contain groupId, tripStatus, runDate gte, runDate lte
        fields = [f.get("field") for f in filters]
        assert set(fields) == {"groupId", "tripStatus", "runDate"}


def test_top_n_route_combined_response_rendering():
    from app.llm.response_generator import ResponseGenerator
    query = "How many  trips in August 2026 were generated by the top 5  route combined ?"
    rg = ResponseGenerator()

    mock_data = [
        {"TotalTrip": 12000, "Route": "DEL-BOM"},
        {"TotalTrip": 9500, "Route": "BLR-HYD"},
        {"TotalTrip": 8000, "Route": "MAA-CCU"},
        {"TotalTrip": 6000, "Route": "PNQ-NBM"},
        {"TotalTrip": 4000, "Route": "HUB-IXC"},
        {"TotalTrip": 1500, "Route": "OTHER"},
    ]
    entities = {
        "entity": "trip",
        "operation": "count",
        "filters": [
            {"field": "groupId", "operator": "eq", "value": "0041"},
            {"field": "tripStatus", "operator": "eq", "value": "valid"},
            {"field": "runDate", "operator": "gte", "value": "2026-08-01 00:00:00"},
            {"field": "runDate", "operator": "lte", "value": "2026-08-31 23:59:59"},
        ],
        "groupBy": ["Route"],
        "metrics": [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}],
        "sort": [{"field": "TotalTrip", "order": "desc"}],
        "select": []
    }

    response = _run_sync(rg.generate_response(query, {"data": mock_data, "total_count": 39500}, "test_session", entities=entities))

    assert "The top 5 Routes generated a total of **39,500 valid trips combined** in August 2026." in response
    assert "<table" in response
    assert "DEL-BOM" in response
    assert "BLR-HYD" in response
    assert "12,000 trips" in response
    assert "OTHER" not in response  # limited to top 5
    assert response.count("<tr style=\"border-bottom: 1px solid #f1f5f9;") == 5
    assert "Total: 39,500 trips" in response


def test_fixed_elock_vendor_payload():
    from app.llm.intent_detector import IntentDetector
    query = "Which fixed elock vendor had the highest trip volumes this month 2026?"
    assert can_resolve_fast_path(query) is True
    payload = build_fast_path_payload(query)
    assert payload is not None
    assert payload["entity"] == "trip"
    assert payload["operation"] == "count"
    assert payload["analytics"] == [{"type": "fixedlock"}]
    assert payload["groupBy"] == ["vendorfixedelock"]
    assert payload["metrics"] == [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]
    assert payload["sort"] == [{"field": "TotalTrip", "order": "desc"}]

    expected_filters = [
        {"field": "groupId", "operator": "eq", "value": "0041"},
        {"field": "tripStatus", "operator": "eq", "value": "valid"},
        {"field": "runDate", "operator": "gte", "value": "2026-11-01 00:00:00"},
        {"field": "runDate", "operator": "lte", "value": "2026-11-30 23:59:59"},
        {"field": "vendorfixedelock", "operator": "eq", "value": "existing"}
    ]
    assert payload["filters"] == expected_filters

    detector = IntentDetector()
    res = _run_sync(detector.detect(query, [], "test_session"))
    entities = res.get("entities", {})
    assert entities.get("analytics") == [{"type": "fixedlock"}]
    assert entities.get("groupBy") == ["vendorfixedelock"]
    assert entities.get("filters") == expected_filters


def test_date_range_does_not_match_as_vehicle_number():
    from app.llm.intent_detector import IntentDetector, is_valid_indian_vehicle_no

    # Test vehicle validation function directly
    assert is_valid_indian_vehicle_no("TO24NOV2026") is False
    assert is_valid_indian_vehicle_no("UP24NOV2026") is False
    assert is_valid_indian_vehicle_no("TO15AUG2026") is False
    assert is_valid_indian_vehicle_no("UP32AB1234") is True
    assert is_valid_indian_vehicle_no("DL01CD5678") is True
    assert is_valid_indian_vehicle_no("MH12DE3456") is True

    detector = IntentDetector()
    queries = [
        "hich top 5 states generated the highest trip volumes in 23 to 24 nov 2026?",
        "How many trips in 23 to 24 nov 2026 were generated by Maharashtra and Tamil Nadu combined?",
        "How many trips from 10 to 15 aug 2026 were valid?"
    ]

    for q in queries:
        res = _run_sync(detector.detect(q, [], "test_session"))
        filters = res.get("entities", {}).get("filters", [])
        for f in filters:
            assert f.get("field") not in ("vehicleNo", "vehicle_no"), f"Spurious vehicle filter found in {q}: {f}"
            assert f.get("value") != "TO24NOV2026"








