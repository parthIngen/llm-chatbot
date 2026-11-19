import pytest
import time
import json
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)

BENCHMARK_QUERIES = [
    # Basic trip queries
    ("How many trips ran in August 2026?", "basic_trip", {"has_dates": True, "tripStatus": "valid"}),
    ("Show all running trips.", "running_trips", {"tripStatus": "running"}),
    ("Show closed trips in September 2026.", "closed_trips", {"has_dates": True, "tripStatus": "closed"}),
    ("Show cancelled trips.", "cancelled_trips", {"tripStatus": "cancelled"}),
    ("Show trips for vehicle UP32AB1234.", "vehicle_specific", {"vehicleNo": "UP32AB1234"}),

    # Delay queries
    ("Show delayed departure trips in August.", "delay_departure", {"analytics": "departureDelayed", "has_dates": True}),
    ("Show delayed arrival trips.", "delay_arrival", {"analytics": "arrivalDelayed"}),
    ("How many trips have missing ATD?", "missing_atd", {"atd": "missing"}),
    ("Show trips where ATA was not captured.", "ata_not_captured", {"ata": "notcaptured"}),

    # GPS queries
    ("Show trips without GPS.", "without_gps", {"analytics": "gpsna"}),
    ("How many vehicles have active GPS?", "active_gps_veh", {"analytics": "gps"}),
    ("Show inactive GPS vehicles.", "inactive_gps_veh", {"analytics": "gps"}),
    ("Show Secutrak GPS trips.", "secutrak_gps", {"vendor": "Secutrak"}),
    ("Show Wheelseye GPS trips.", "wheelseye_gps", {"vendor": "Wheelseye"}),

    # E-Lock queries
    ("Show trips with Fixed E-Lock.", "fixed_elock", {"FixedELock": "existing"}),
    ("Show inactive Portable E-Lock trips.", "inactive_portable_elock", {"PortableELock": "inactive"}),
    ("Show vehicles with active Fixed E-Lock.", "active_fixed_elock_veh", {"analytics": "fixedlock"}),

    # Device combinations
    ("Show vehicles with Fixed GPS only.", "fixed_gps_only", {"GPS": "existing"}),
    ("Show trips with Fixed GPS and Fixed E-Lock.", "fixed_gps_and_elock", {"analytics": "withgps"}),
    ("Show trips with Fixed E-Lock and Portable E-Lock together.", "elock_together", {"analytics": "withgps"}),
    ("Show trips without any device.", "without_any_device", {"analytics": "withoutanygps"}),

    # Comparisons and analytics
    ("Compare Network and Local trips.", "network_vs_local", {"groupBy": "tripType"}),
    ("Show active Fixed GPS device count across Intercity and Intracity trips in Aug 2026", "active_fixed_gps_across_trip_types", {"analytics": "gps", "GPS": "active", "groupBy": "tripType", "has_dates": True}),
    ("Total trips for the North region in August 2026", "north_region_trips", {"region": "North", "has_dates": True}),
    ("Total Feeder type trips in August 2026", "feeder_trips", {"shipmentMethod": "feeder", "tripStatus": "valid", "has_dates": True, "no_group_by": True}),
    ("Show region-wise trip counts.", "region_breakdown", {"groupBy": "region"}),
    ("Show High Priority vs Normal Priority trips.", "priority_comparison", {"groupBy": "priority"}),
    ("Show unique transporters.", "unique_transporters", {"groupBy": "Transporter"}),
    ("Show unique vehicles.", "unique_vehicles", {"groupBy": "vehicleNo"}),

    # Semantic & Ambiguous queries (should fallback to LLM when fast path doesn't match)
    ("kindly explain how many trip operated with no tracking units", "semantic_unusual", {}),
    ("show all trips where vehicle had no connectivity and got stuck", "semantic_edge", {}),
]

@pytest.mark.parametrize("query,case_id,expected_traits", BENCHMARK_QUERIES)
def test_benchmark_query(query, case_id, expected_traits):
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {"success": True, "data": 42}

    # Semantic fallback mock in case LLM is called
    llm_mock_json = json.dumps({
        "entity": "trip",
        "operation": "count",
        "filters": [
            {"field": "groupId", "operator": "eq", "value": "0041"},
            {"field": "tripStatus", "operator": "eq", "value": "valid"}
        ],
        "select": []
    })

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        
        mock_llm.return_value = llm_mock_json
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": f"bench-{case_id}",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200, f"Failed with {response.text}"
        res_data = response.json()

        # 1. Assert required response structure
        assert "reply" in res_data
        assert res_data["intent"] == "trip_report"
        assert "entities" in res_data
        assert "debug" in res_data

        entities = res_data["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "count"

        filters = entities.get("filters", [])
        analytics = entities.get("analytics", [])
        group_by = entities.get("groupBy", [])

        # Verify groupId is always present
        group_ids = [f["value"] for f in filters if f.get("field") == "groupId"]
        assert "0041" in group_ids, f"groupId 0041 missing in {filters}"

        # 2. Check traits
        if "tripStatus" in expected_traits:
            status_vals = [str(f.get("value")).lower() for f in filters if f.get("field") == "tripStatus"]
            expected_val = expected_traits["tripStatus"].lower()
            assert expected_val in status_vals or ("running" in status_vals and expected_val in ("running", "active")), \
                f"Expected tripStatus {expected_val} in {status_vals}"

        if "has_dates" in expected_traits:
            date_fields = [f.get("field") for f in filters if f.get("field") == "runDate"]
            assert len(date_fields) >= 1, f"Expected runDate filter in {filters}"

        if "vehicleNo" in expected_traits:
            veh_vals = [f.get("value") for f in filters if f.get("field") == "vehicleNo"]
            assert expected_traits["vehicleNo"] in veh_vals, f"Expected vehicleNo in {filters}"

        if "atd" in expected_traits:
            atd_vals = [f.get("value") for f in filters if f.get("field") == "atd"]
            assert expected_traits["atd"] in atd_vals, f"Expected atd in {filters}"

        if "ata" in expected_traits:
            ata_vals = [f.get("value") for f in filters if f.get("field") == "ata"]
            assert expected_traits["ata"] in ata_vals, f"Expected ata in {filters}"

        if "analytics" in expected_traits:
            ana_types = [a.get("type") for a in analytics]
            assert expected_traits["analytics"] in ana_types, f"Expected {expected_traits['analytics']} in {ana_types}"

        if "groupBy" in expected_traits:
            assert expected_traits["groupBy"] in group_by, f"Expected {expected_traits['groupBy']} in {group_by}"

        if "GPS" in expected_traits:
            gps_vals = [str(f.get("value")).lower() for f in filters if f.get("field") == "GPS"]
            assert expected_traits["GPS"].lower() in gps_vals, f"Expected GPS in {filters}"

        if "vendor" in expected_traits:
            vendor_vals = [str(f.get("value")).lower() for f in filters if f.get("field") in ("vendor", "vendorgps")]
            expected_v = expected_traits["vendor"].lower()
            assert any(expected_v in v for v in vendor_vals), f"Expected vendor {expected_v} in {vendor_vals}"

        if "region" in expected_traits:
            region_vals = [str(f.get("value")).lower() for f in filters if f.get("field") == "region"]
            assert expected_traits["region"].lower() in region_vals, f"Expected region in {filters}"

        if "shipmentMethod" in expected_traits:
            sm_vals = [str(f.get("value")).lower() for f in filters if f.get("field") == "shipmentMethod"]
            expected_sm = expected_traits["shipmentMethod"].lower()
            assert any(expected_sm in v for v in sm_vals), f"Expected shipmentMethod {expected_sm} in {sm_vals}"

        if expected_traits.get("no_group_by"):
            assert not group_by, f"Expected no groupBy but got {group_by}"

        # 3. Check performance
        assert duration < 1.0, f"Expected sub-second response, got {duration:.3f}s"

def test_feeder_trips_exact_payload_and_metric_aggregation():
    """
    Validates exact payload, fast-path routing, and metric count extraction
    for 'Total Feeder type trips in August 2026'.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [{"Feeder_trips": 80290, "shipmentMethod": "FEEDER"}]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": "Total Feeder type trips in August 2026",
            "history": [],
            "session_id": "feeder-test-1",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        # Check fast path execution
        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"

        # Check payload
        entities = res["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "count"
        assert "groupBy" not in entities or not entities["groupBy"]
        assert "metrics" not in entities or not entities["metrics"]

        filters = {f["field"]: f["value"] for f in entities["filters"] if "field" in f}
        assert filters.get("groupId") == "0041"
        assert filters.get("tripStatus") == "valid"
        assert filters.get("shipmentMethod") == "feeder"

        run_dates = [f for f in entities["filters"] if f.get("field") == "runDate"]
        assert len(run_dates) == 2
        gte = next(f["value"] for f in run_dates if f.get("operator") == "gte")
        lte = next(f["value"] for f in run_dates if f.get("operator") == "lte")
        assert gte == "2026-08-01 00:00:00"
        assert lte == "2026-08-31 23:59:59"

        # Check response contains correct count 80290, not 1
        assert "80290" in res["reply"]
        assert "1 valid" not in res["reply"]
        assert "feeder" in res["reply"].lower()
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"

def test_all_three_devices_together_exact_payload_and_reply():
    """
    Validates exact payload and natural language response for:
    'How many trips in August 2026 used all three devices together (Fixed GPS + Fixed E-Lock + Portable E-Lock)?'
    Ensures device filter values are 'existing' when not specified as active/na.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": 35125
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": "How many trips in August 2026 used all three devices together (Fixed GPS + Fixed E-Lock + Portable E-Lock)?",
            "history": [],
            "session_id": "three-devices-test-1",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"

        entities = res["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "count"
        assert entities["analytics"] == [{"type": "withgps"}]
        assert entities["select"] == []

        filters = {f["field"]: f["value"] for f in entities["filters"] if "field" in f}
        assert filters.get("groupId") == "0041"
        assert filters.get("tripStatus") == "valid"
        assert filters.get("FixedELock") == "existing"
        assert filters.get("PortableELock") == "existing"
        assert filters.get("GPS") == "existing"

        # Check reply phrasing
        assert "35125" in res["reply"]
        assert "using all three devices together (Fixed GPS, Fixed E-Lock, and Portable E-Lock)" in res["reply"]
        assert "active Fixed GPS" not in res["reply"]
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"

def test_groq_limit_exceeded_fallback_to_local_llm():
    """
    Validates that when Groq returns HTTP 429 (rate/quota limit exceeded),
    the system automatically falls back to the local LLM and returns a successful response.
    """
    import app.llm.client as client_module
    client_module._groq_rate_limited_until = 0.0

    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {"retry-after": "10"}
    mock_429.text = '{"error": {"message": "Rate limit exceeded", "code": "rate_limit_exceeded"}}'

    mock_local_llm = MagicMock()
    mock_local_llm.status_code = 200
    mock_local_llm.json.return_value = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "entity": "trip",
                    "operation": "count",
                    "filters": [
                        {"field": "groupId", "operator": "eq", "value": "0041"},
                        {"field": "tripStatus", "operator": "eq", "value": "valid"}
                    ],
                    "select": []
                })
            }
        }]
    }

    mock_api = MagicMock()
    mock_api.status_code = 200
    mock_api.json.return_value = {"success": True, "data": 999}

    async def mock_network_post(url, *args, **kwargs):
        url_str = str(url)
        if "api.groq.com" in url_str:
            return mock_429
        elif "prompt.secutrak.in" in url_str:
            return mock_api
        else:
            return mock_local_llm

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_network_post):
        response = client.post("/api/v1/chat", json={
            "message": "kindly explain how many trip operated with no tracking units",
            "history": [],
            "session_id": "rate-limit-fallback-test",
            "AccessToken": "test-token"
        })

        assert response.status_code == 200
        res = response.json()
        assert res["intent"] == "trip_report"
        assert res["llm_status"] == "success"
        debug = res["debug"]
        assert debug["fallback_to_local"] is True
        assert debug["llm_provider"] == "ollama"
        assert debug["llm_model"] == "qwen2.5-coder:0.5b"


@pytest.mark.parametrize("query", [
    "What is the trip count across major feeder types in August 2026?",
    "What is the trip count across major route types in August 2026?",
    "What is the trip count across major shipment method in August 2026?",
])
def test_shipment_method_breakdown_queries_exact_payload_and_reply(query):
    """
    Validates exact payload, grouping by shipmentMethod, metrics,
    and natural language formatting for all shipment method breakdown query variations.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [
            {"trip_count": 21011, "shipmentMethod": "AIR_NETWORK"},
            {"trip_count": 13490, "shipmentMethod": "PICK UP AND DELIVERY"},
            {"trip_count": 20329, "shipmentMethod": "DELIVERY"},
            {"trip_count": 656, "shipmentMethod": "COMM/BZ-OUT"},
            {"trip_count": 80290, "shipmentMethod": "FEEDER"},
            {"trip_count": 16363, "shipmentMethod": "PICK UP"},
            {"trip_count": 1188, "shipmentMethod": "EMPTY"},
            {"trip_count": 1390, "shipmentMethod": "SPEED_TRUCK"},
            {"trip_count": 18527, "shipmentMethod": "SFC_NETWORK"},
            {"trip_count": 295, "shipmentMethod": "COMM/BZ-IN"}
        ]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "shipment-method-test",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        # Check fast path execution
        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"

        # Check exact payload structure
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "count"
        assert entities.get("groupBy") == ["shipmentMethod"]
        assert entities.get("metrics") == [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]
        assert entities.get("select") == []

        filters = {f["field"]: f["value"] for f in entities["filters"] if "field" in f}
        assert filters.get("groupId") == "0041"
        assert filters.get("tripStatus") == "valid"
        assert "Route" not in filters
        assert "routeCategory" not in filters
        assert "shipmentMethod" not in filters

        run_dates = [f for f in entities["filters"] if f.get("field") == "runDate"]
        assert len(run_dates) == 2
        gte = next(f["value"] for f in run_dates if f.get("operator") == "gte")
        lte = next(f["value"] for f in run_dates if f.get("operator") == "lte")
        assert gte == "2026-08-01 00:00:00"
        assert lte == "2026-08-31 23:59:59"

        # Check response contains key breakdown items
        reply = res["reply"]
        assert "FEEDER is 80290" in reply or "80290" in reply
        assert "21011" in reply
        assert "20329" in reply
        assert "does not include cancelled trips" in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"


@pytest.mark.parametrize("query", [
    "How many trips were Speed_Truck, Empty, Comm/BZ-Out, or Comm/BZ-In in August 2026?",
    "breakdown of trips that were Speed_Truck, Empty, Comm/BZ-Out, or Comm/BZ-In in August 2026?",
    "trips that were Speed_Truck v/s Empty v/s  Comm/BZ-Out v/sComm/BZ-In in August 2026?",
])
def test_multiple_shipment_methods_breakdown_exact_payload_and_reply(query):
    """
    Validates exact payload, grouping by shipmentMethod, metrics,
    and natural language formatting when multiple shipment methods are queried.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [
            {"trip_count": 1390, "shipmentMethod": "SPEED_TRUCK"},
            {"trip_count": 1188, "shipmentMethod": "EMPTY"},
            {"trip_count": 656, "shipmentMethod": "COMM/BZ-OUT"},
            {"trip_count": 295, "shipmentMethod": "COMM/BZ-IN"}
        ]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "multi-shipment-test",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        # Check fast path execution
        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"

        # Check exact payload structure
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "count"
        assert entities.get("groupBy") == ["shipmentMethod"]
        assert entities.get("metrics") == [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]
        assert entities.get("select") == []

        filters = {f["field"]: f for f in entities["filters"] if "field" in f}
        assert filters.get("groupId", {}).get("value") == "0041"
        assert filters.get("tripStatus", {}).get("value") == "valid"
        assert filters.get("shipmentMethod", {}).get("operator") == "in"
        assert filters.get("shipmentMethod", {}).get("value") == ["speed", "empty", "Comm/BZ-Out", "Comm/BZ-In"]

        run_dates = [f for f in entities["filters"] if f.get("field") == "runDate"]
        assert len(run_dates) == 2
        gte = next(f["value"] for f in run_dates if f.get("operator") == "gte")
        lte = next(f["value"] for f in run_dates if f.get("operator") == "lte")
        assert gte == "2026-08-01 00:00:00"
        assert lte == "2026-08-31 23:59:59"

        # Check response contains key breakdown items
        reply = res["reply"]
        assert "1390" in reply
        assert "1188" in reply
        assert "656" in reply
        assert "295" in reply
        assert "does not include cancelled trips" in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"


@pytest.mark.parametrize("query,expected_field,expected_vendor,expected_analytics", [
    ("How many trips used Secutrak_TP Fixed GPS in August 2026?", "vendorgps", "Secutrak_TP", "gps"),
    ("How many trips used Fleetx (fleetx_bluedart) Fixed GPS in August 2026?", "vendorgps", "Fleetx", "gps"),
    ("How many trips used Axestrack Fixed GPS in August 2026?", "vendorgps", "Axestrack", "gps"),
    ("How many trips used Gtropy Fixed GPS in August 2026?", "vendorgps", "Gtropy", "gps"),
    ("How many trips used Eicher Fixed GPS in August 2026?", "vendorgps", "Eicher", "gps"),
    ("How many trips used G-Trac Fixed GPS in August 2026?", "vendorgps", "G-Trac", "gps"),
    ("How many trips used Ashok Leyland Fixed GPS in August 2026?", "vendorgps", "Ashok", "gps"),
    ("How many trips used Tata Motors Fixed GPS in August 2026?", "vendorgps", "Tata", "gps"),
    ("How many trips used Kiasaint Fixed E-Lock in August 2026?", "vendorfixedelock", "Kiasaint", "fixedlock"),
    ("How many trips used G-Trac Fixed E-Lock in August 2026?", "vendorfixedelock", "G-Trac", "fixedlock"),
    ("How many trips used Kiasaint Portable E-Lock in August 2026?", "vendorportableelock", "Kiasaint", "portablelock"),
    ("How many trips used G-Trac Portable E-Lock in August 2026?", "vendorportableelock", "G-Trac", "portablelock"),
])
def test_tracking_device_vendor_queries_exact_payload_and_reply(query, expected_field, expected_vendor, expected_analytics):
    """
    Validates exact payload, device-specific vendor field (vendorgps, vendorfixedelock, vendorportableelock),
    analytics type (gps, fixedlock, portablelock), and sub-500ms latency.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": 1234
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "vendor-test",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        # Check fast path execution
        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"

        # Check exact payload structure
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "count"
        assert entities.get("analytics") == [{"type": expected_analytics}]
        assert entities.get("select") == []
        assert "groupBy" not in entities or not entities["groupBy"]
        assert "metrics" not in entities or not entities["metrics"]

        filters = entities["filters"]
        # Exact field order check: groupId, tripStatus, runDate (gte), runDate (lte), vendor field
        field_order = [f.get("field") for f in filters]
        assert field_order == ["groupId", "tripStatus", "runDate", "runDate", expected_field], f"Unexpected field order: {field_order}"

        f_map = {f["field"]: f for f in filters if "field" in f}
        assert f_map["groupId"]["value"] == "0041"
        assert f_map["groupId"]["operator"] == "eq"
        assert f_map["tripStatus"]["value"] == "valid"
        assert f_map["tripStatus"]["operator"] == "eq"

        run_dates = [f for f in filters if f.get("field") == "runDate"]
        assert len(run_dates) == 2
        gte = next(f["value"] for f in run_dates if f.get("operator") == "gte")
        lte = next(f["value"] for f in run_dates if f.get("operator") == "lte")
        assert gte == "2026-08-01 00:00:00"
        assert lte == "2026-08-31 23:59:59"

        assert f_map[expected_field]["operator"] == "contains"
        assert f_map[expected_field]["value"] == expected_vendor

        # Check response
        reply = res["reply"]
        assert "1234" in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"


def test_transporter_vehicles_queries_exact_payload_and_reply():
    """
    Validates exact payload, groupBy vehicleNo, metrics, clean transporter value,
    first 10 vehicle listing with Excel link for list query, and count-only for how-many query.
    """
    mock_vehicle_data = [
        {"vehicleNo": "DL01MB2525", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MB4852", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MB5985", "uniqueVehicleCount": 1},
        {"vehicleNo": "HR55AU2959", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MA6293", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MB2983", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MB4349", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MB5589", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MA2844", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01LAF2969", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MB1854", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01AF2969", "uniqueVehicleCount": 1},
        {"vehicleNo": "DL01MA9719", "uniqueVehicleCount": 1}
    ]

    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": mock_vehicle_data
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        # --- 1. List of vehicles query ---
        q1 = "Lis of the vehicle was on trip this month for the transporter - DIDAR SINGH"
        t_start = time.perf_counter()
        resp1 = client.post("/api/v1/chat", json={
            "message": q1,
            "history": [],
            "session_id": "test-list-vehicles",
            "AccessToken": "test-token"
        })
        d1 = time.perf_counter() - t_start
        assert resp1.status_code == 200
        r1 = resp1.json()

        assert r1["source"] == "fast_path"
        assert r1["llm_status"] == "bypassed"
        ent1 = r1["entities"]
        assert ent1.get("entity") == "trip"
        assert ent1.get("operation") == "count"
        assert ent1.get("groupBy") == ["vehicleNo"]
        assert ent1.get("metrics") == [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
        assert ent1.get("select") == []

        f_order1 = [f.get("field") for f in ent1["filters"]]
        assert f_order1 == ["groupId", "tripStatus", "runDate", "runDate", "transporter"]

        f_map1 = {f["field"]: f for f in ent1["filters"] if "field" in f}
        assert f_map1["groupId"]["value"] == "0041"
        assert f_map1["tripStatus"]["value"] == "valid"
        assert f_map1["transporter"]["value"] == "DIDAR SINGH"
        assert f_map1["transporter"]["operator"] == "eq"

        reply1 = r1["reply"]
        assert "DL01MB2525" in reply1
        assert "DL01LAF2969" in reply1
        assert "DL01MB1854" not in reply1  # 11th item should be omitted from text preview
        assert "Download complete Excel list" in reply1 or "Download Excel" in reply1
        assert r1["download_url"] is not None
        assert d1 < 0.5, f"Expected fast-path sub-500ms execution, got {d1:.3f}s"

        # --- 2. How many vehicles query ---
        q2 = "how many vehicle was on trip this month for the transporter - DIDAR SINGH"
        t_start = time.perf_counter()
        resp2 = client.post("/api/v1/chat", json={
            "message": q2,
            "history": [],
            "session_id": "test-count-vehicles",
            "AccessToken": "test-token"
        })
        d2 = time.perf_counter() - t_start
        assert resp2.status_code == 200
        r2 = resp2.json()

        assert r2["source"] == "fast_path"
        assert r2["llm_status"] == "bypassed"
        ent2 = r2["entities"]
        assert ent2.get("entity") == "trip"
        assert ent2.get("operation") == "count"
        assert ent2.get("groupBy") == ["vehicleNo"]
        assert ent2.get("metrics") == [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
        assert ent2.get("select") == []

        f_order2 = [f.get("field") for f in ent2["filters"]]
        assert f_order2 == ["groupId", "tripStatus", "runDate", "runDate", "transporter"]

        f_map2 = {f["field"]: f for f in ent2["filters"] if "field" in f}
        assert f_map2["groupId"]["value"] == "0041"
        assert f_map2["tripStatus"]["value"] == "valid"
        assert f_map2["transporter"]["value"] == "DIDAR SINGH"
        assert f_map2["transporter"]["operator"] == "eq"

        reply2 = r2["reply"]
        assert "Total 13 vehicles were on trip" in reply2
        assert "DL01MB2525" not in reply2  # No list of vehicle numbers in count-only reply
        assert "Download complete Excel list" not in reply2
        assert "Download Excel report" not in reply2
        assert d2 < 0.5, f"Expected fast-path sub-500ms execution, got {d2:.3f}s"


def test_secutrak_gps_inactive_exact_payload_and_reply():
    """
    Validates exact payload, field order, and response for:
    'Total Secutrak GPS inactive trips in August 2026'
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [{"total_count": 77152}]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        query = "Total Secutrak GPS inactive trips in August 2026"
        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "test-secutrak-inactive-gps",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "count"
        assert entities.get("analytics") == [{"type": "gps"}]
        assert entities.get("select") == []

        filters = entities["filters"]
        field_order = [f.get("field") for f in filters]
        assert field_order == ["groupId", "tripStatus", "runDate", "runDate", "vendorgps", "gps"], f"Unexpected field order: {field_order}"

        f_map = {f["field"]: f for f in filters if "field" in f}
        assert f_map["groupId"]["value"] == "0041"
        assert f_map["tripStatus"]["value"] == "valid"
        assert f_map["vendorgps"]["value"] == "Secutrak"
        assert f_map["vendorgps"]["operator"] == "eq"
        assert f_map["gps"]["value"] == "inactive"
        assert f_map["gps"]["operator"] == "eq"

        date_filters = [f for f in filters if f.get("field") == "runDate"]
        assert len(date_filters) == 2
        assert date_filters[0]["operator"] == "gte"
        assert date_filters[0]["value"] == "2026-08-01 00:00:00"
        assert date_filters[1]["operator"] == "lte"
        assert date_filters[1]["value"] == "2026-08-31 23:59:59"

        reply = res["reply"]
        assert "Total 77152 valid trips with inactive Fixed GPS found using Secutrak devices" in reply
        assert "in date range from 2026-08-01 00:00:00 to 2026-08-31 23:59:59" in reply
        assert "It does not include cancelled trips." in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"


@pytest.mark.parametrize("query,expected_vm", [
    ("Total trips with vehicle outside of master (Vehicle NA in Vehicle Master) in August 2026", "outside"),
    ("Total trips with vehicle outside of master in August 2026", "outside"),
    ("Total trips with Vehicle NA in Vehicle Master in August 2026", "outside"),
    ("total trips with vehicle inside of master in August 2026", "inside"),
])
def test_vehicle_master_queries_exact_payload_and_reply(query, expected_vm):
    """
    Validates exact payload, field order, and response for vehicle master queries.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [{"total_count": 500}]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "test-vehicle-master",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "count"
        assert entities.get("select") == []
        assert "analytics" not in entities or entities.get("analytics") == []

        filters = entities["filters"]
        field_order = [f.get("field") for f in filters]
        assert field_order == ["groupId", "tripStatus", "runDate", "runDate", "vehicleMaster"], f"Unexpected field order: {field_order}"

        f_map = {f["field"]: f for f in filters if "field" in f}
        assert f_map["groupId"]["value"] == "0041"
        assert f_map["tripStatus"]["value"] == "valid"
        assert f_map["vehicleMaster"]["value"] == expected_vm
        assert f_map["vehicleMaster"]["operator"] == "eq"

        date_filters = [f for f in filters if f.get("field") == "runDate"]
        assert len(date_filters) == 2
        assert date_filters[0]["operator"] == "gte"
        assert date_filters[0]["value"] == "2026-08-01 00:00:00"
        assert date_filters[1]["operator"] == "lte"
        assert date_filters[1]["value"] == "2026-08-31 23:59:59"

        reply = res["reply"]
        assert f"Total 500 valid trips with vehicle {expected_vm} of master found" in reply
        assert "in date range from 2026-08-01 00:00:00 to 2026-08-31 23:59:59" in reply
        assert "It does not include cancelled trips." in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"


@pytest.mark.parametrize("query,expected_rm", [
    ("Total trips with route outside of master (Route NA in Network Module) in August 2026", "outside"),
    ("Total trips with (Route NA in Network Module in August 2026", "outside"),
    ("Total trips with route outside of master in August 2026", "outside"),
    ("Total trips with route inside of master in August 2026", "inside"),
])
def test_route_master_queries_exact_payload_and_reply(query, expected_rm):
    """
    Validates exact payload, field order, and response for route master queries.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [{"total_count": 500}]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "test-route-master",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "count"
        assert entities.get("select") == []
        assert "analytics" not in entities or entities.get("analytics") == []

        filters = entities["filters"]
        field_order = [f.get("field") for f in filters]
        assert field_order == ["groupId", "tripStatus", "runDate", "runDate", "routeMaster"], f"Unexpected field order: {field_order}"

        f_map = {f["field"]: f for f in filters if "field" in f}
        assert f_map["groupId"]["value"] == "0041"
        assert f_map["tripStatus"]["value"] == "valid"
        assert f_map["routeMaster"]["value"] == expected_rm
        assert f_map["routeMaster"]["operator"] == "eq"

        date_filters = [f for f in filters if f.get("field") == "runDate"]
        assert len(date_filters) == 2
        assert date_filters[0]["operator"] == "gte"
        assert date_filters[0]["value"] == "2026-08-01 00:00:00"
        assert date_filters[1]["operator"] == "lte"
        assert date_filters[1]["value"] == "2026-08-31 23:59:59"

        reply = res["reply"]
        assert f"Total 500 valid trips with route {expected_rm} of master found" in reply
        assert "in date range from 2026-08-01 00:00:00 to 2026-08-31 23:59:59" in reply
        assert "It does not include cancelled trips." in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"


@pytest.mark.parametrize("query,expected_fm", [
    ("Total trips with fleet outside of master (Fleet NA in Network Module) in August 2026", "outside"),
    ("Total trips with fleet outside of master in August 2026", "outside"),
    ("Total trips with Fleet NA in Network Module in August 2026", "outside"),
    ("Total trips with fleet inside of master in August 2026", "inside"),
    ("Total trips with fleet in Network Module in August 2026", "inside"),
])
def test_fleet_master_queries_exact_payload_and_reply(query, expected_fm):
    """
    Validates exact payload, field order, and response for fleet master queries.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [{"total_count": 500}]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_llm, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_llm.return_value = '{"entity": "trip", "operation": "count"}'
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "test-fleet-master",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "count"
        assert entities.get("select") == []
        assert "analytics" not in entities or entities.get("analytics") == []

        filters = entities["filters"]
        field_order = [f.get("field") for f in filters]
        assert field_order == ["groupId", "tripStatus", "runDate", "runDate", "fleetMaster"], f"Unexpected field order: {field_order}"

        f_map = {f["field"]: f for f in filters if "field" in f}
        assert f_map["groupId"]["value"] == "0041"
        assert f_map["tripStatus"]["value"] == "valid"
        assert f_map["fleetMaster"]["value"] == expected_fm
        assert f_map["fleetMaster"]["operator"] == "eq"

        date_filters = [f for f in filters if f.get("field") == "runDate"]
        assert len(date_filters) == 2
        assert date_filters[0]["operator"] == "gte"
        assert date_filters[0]["value"] == "2026-08-01 00:00:00"
        assert date_filters[1]["operator"] == "lte"
        assert date_filters[1]["value"] == "2026-08-31 23:59:59"

        reply = res["reply"]
        assert f"Total 500 valid trips with fleet {expected_fm} of master found" in reply
        assert "in date range from 2026-08-01 00:00:00 to 2026-08-31 23:59:59" in reply
        assert "It does not include cancelled trips." in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"


@pytest.mark.parametrize("query", [
    "share the report of the shipment 12543311",
    "share the details of the shipment 12543311",
    "show the report of the shipment 12543311",
    "share the report of the trip id 12543311",
    "information of trip id 12543311",
    "12543311 trip id details",
    "12543311 shipment number details",
])
def test_shipment_details_exact_payload_and_reply(query):
    """
    Validates exact payload, find operation, and natural language summary for single shipment lookup.
    """
    mock_api_res = MagicMock()
    mock_api_res.status_code = 200
    mock_api_res.json.return_value = {
        "success": True,
        "data": [
            {
                "_id": {"$oid": "6af24a0e291e6d387e097cc8"},
                "source_code": "PLW",
                "route_name": "PLW-BDW-ASL",
                "region_code": "WEST",
                "destination_name": "BDE-ASL - (ASLALI WAREHOUSE)",
                "trip_type": 1,
                "shipment_no": "12543311",
                "schedule_departure": "2026-11-10 06:00:00",
                "schedule_arrival": "2026-11-11 15:00:00",
                "vehicle_no": "GJ27TD6497",
                "driver_name": "bablu",
                "shipment_method": "SFC_Network",
                "transporter_name": "SHREE G.K. FREIGHT CARRIER",
                "actual_destination_arrival_time": "2026-11-19 08:38",
                "actual_source_departure_time": "",
                "trip_status": 0
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_api_res

        t_start = time.perf_counter()
        response = client.post("/api/v1/chat", json={
            "message": query,
            "history": [],
            "session_id": "test-shipment-details",
            "AccessToken": "test-token"
        })
        duration = time.perf_counter() - t_start

        assert response.status_code == 200
        res = response.json()

        assert res["source"] == "fast_path"
        assert res["llm_status"] == "bypassed"
        entities = res["entities"]
        assert entities.get("entity") == "trip"
        assert entities.get("operation") == "find"
        assert entities.get("select") == []
        assert "analytics" not in entities or entities.get("analytics") == []
        assert "groupBy" not in entities
        assert "metrics" not in entities

        filters = entities["filters"]
        f_map = {f["field"]: f for f in filters if "field" in f}
        assert f_map["groupId"]["value"] == "0041"
        assert f_map["groupId"]["operator"] == "eq"
        assert f_map["shipmentNo"]["value"] == "12543311"
        assert f_map["shipmentNo"]["operator"] == "eq"
        assert len(filters) == 2

        reply = res["reply"]
        assert "<div" in reply
        assert "<table" in reply
        assert "PLW" in reply
        assert "PLW-BDW-ASL" in reply
        assert "WEST" in reply
        assert "BDE-ASL - (ASLALI WAREHOUSE)" in reply
        assert "1" in reply
        assert "2026-11-10 06:00:00" in reply
        assert "2026-11-11 15:00:00" in reply
        assert "GJ27TD6497" in reply
        assert "bablu" in reply
        assert "SFC_Network" in reply
        assert "SHREE G.K. FREIGHT CARRIER" in reply
        assert "2026-11-19 08:38" in reply
        assert "N/A" in reply
        assert "closed" in reply
        assert duration < 0.5, f"Expected fast-path sub-500ms execution, got {duration:.3f}s"











