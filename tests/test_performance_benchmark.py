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

        # 3. Check performance
        assert duration < 1.0, f"Expected sub-second response, got {duration:.3f}s"
