import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_download_trips_excel_flow():
    user_query = "Download all trips for the North region today"

    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "find",
        "filters": [
            {
                "field": "runDate",
                "operator": "gte",
                "value": "2026-08-31 00:00:00"
            },
            {
                "field": "runDate",
                "operator": "lte",
                "value": "2026-08-31 23:59:59"
            },
            {
                "field": "groupId",
                "operator": "eq",
                "value": "0041"
            },
            {
                "field": "region",
                "operator": "eq",
                "value": "North"
            },
            {
                "field": "tripStatus",
                "operator": "eq",
                "value": "Active"
            }
        ],
        "select": []
    })

    llm_natural_response = "### Summary\nThere are **753 active trips** for the **North** region on **2026-08-31** (today) in group **0041**. Your Excel file with all these trips is ready for download."

    api_response_data = {
        "success": True,
        "data": [
            {
                "_id": {"$oid": "6a9567814f8b67fcff0bae79"},
                "vehicle_no": "HR37F4240",
                "source_name": "BDE-JAW - (JAMMUWAREHOUSE)",
                "destination_name": "BDE-AMH - (AMBALA HUB)",
                "run_date": "2026-08-31 17:07:04",
                "trip_status": 1,
                "driver_name": "niyaz",
                "driver_mobile": "9906022874"
            },
            {
                "_id": {"$oid": "6a9567469ed6612b860eff03"},
                "vehicle_no": "HR37F7463",
                "source_name": "BDE-JAW - (JAMMUWAREHOUSE)",
                "destination_name": "BDE-AMH - (AMBALA HUB)",
                "run_date": "2026-08-31 17:06:00",
                "trip_status": 1,
                "driver_name": "niyaz",
                "driver_mobile": "9906022874"
            }
        ]
    }

    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:

        mock_generate.side_effect = [llm_payload_response, llm_natural_response]

        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = api_response_data
        mock_post.return_value = mock_api_res

        payload = {
            "message": user_query,
            "history": [],
            "session_id": "test-session-download-north",
            "AccessToken": "mock-token"
        }

        response = client.post("/api/v1/chat", json=payload)
        assert response.status_code == 200
        res_json = response.json()

        assert "There are **753 active trips**" in res_json["reply"]
        assert "Download Excel" in res_json["reply"]
        assert "Trips Excel Report" in res_json["reply"]
        assert res_json["download_url"] is not None
        assert "/api/v1/download/" in res_json["download_url"]

        # Test downloading the Excel file via the returned download endpoint
        filename = res_json["download_url"].split("/api/v1/download/")[1]
        download_res = client.get(f"/api/v1/download/{filename}")
        assert download_res.status_code == 200
        assert download_res.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert len(download_res.content) > 0
