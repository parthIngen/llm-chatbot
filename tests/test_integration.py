import pytest
import json
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)

@pytest.mark.asyncio
async def test_single_api_query_flow():
    # 1. Prepare query and mock responses
    user_query = "Download all trips for the North region in January 2026."
    
    # Mock LLM Client Response for intent detector (generating the structured query payload)
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "find",
        "filters": [
            {
                "field": "runDate",
                "operator": "gte",
                "value": "2026-01-01 00:00:00"
            },
            {
                "field": "runDate",
                "operator": "lte",
                "value": "2026-01-31 23:59:59"
            },
            {
                "field": "tripStatus",
                "operator": "eq",
                "value": "Running"
            },
            {
                "field": "region",
                "operator": "eq",
                "value": "North"
            }
        ],
        "select": []
    })

    # Mock LLM Client Response for response generator (generating natural language response)
    llm_natural_response = "There are 1138 running trips in the North region."

    # API Response Mock
    api_response_data = {
        "success": True,
        "data": 1138
    }

    # 2. Patch both LLMClient.generate and httpx.AsyncClient.post
    # Since we call LLMClient.generate twice (once in intent detector, once in response generator),
    # we configure side_effect to return the respective mocked values.
    from unittest.mock import MagicMock
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
         
        mock_generate.side_effect = [llm_payload_response, llm_natural_response]
        
        # Configure mocked API call to return status 200 and the JSON response
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = api_response_data
        mock_post.return_value = mock_api_res

        # 3. Call endpoint
        payload = {
            "message": user_query,
            "history": [],
            "session_id": "test-session-123",
            "AccessToken": "mock-token"
        }
        
        response = client.post("/api/v1/chat", json=payload)
        
        # 4. Assert responses
        assert response.status_code == 200
        data = response.json()
        
        assert data["reply"] == llm_natural_response
        assert data["intent"] == "trip_report"
        
        # Verify normalization (groupId eq 0041 was automatically appended)
        entities = data["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "find"
        
        filters = entities["filters"]
        # There should be 4 filters: runDate (gte), runDate (lte), region eq North, and groupId eq 0041
        assert len(filters) == 4
        fields = [f["field"] for f in filters]
        assert "groupId" in fields
        assert "region" in fields
        assert "tripStatus" not in fields
        assert "runDate" in fields
        
        # Verify the actual API request details
        mock_post.assert_called_once()
        called_args, called_kwargs = mock_post.call_args
        assert called_args[0] == settings.TRIP_API_URL
        assert called_kwargs["json"] == entities
        
        # Verify raw API data returned in the response
        assert data["data"] == api_response_data

        # Verify that response generator was called with filters in the context
        assert mock_generate.call_count == 2
        gen_call_args, gen_call_kwargs = mock_generate.call_args_list[1]
        assert "QueryFilters" in gen_call_kwargs["prompt"]
        assert "region" in gen_call_kwargs["prompt"]
        assert "North" in gen_call_kwargs["prompt"]


@pytest.mark.asyncio
async def test_all_trips_east_region_flow():
    # 1. Prepare query and mock responses
    user_query = "all trips for the East region from 2026-01-01 to 2026-01-31"
    
    # Mock LLM Client Response for intent detector (simulating model returning count and region without status/groupId/dates)
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "count",
        "filters": [
            {
                "field": "region",
                "operator": "eq",
                "value": "East"
            }
        ],
        "select": []
    })

    # Mock LLM Client Response for response generator (generating natural language response)
    llm_natural_response = "There are 512 running trips in the East region."

    # API Response Mock
    api_response_data = {
        "success": True,
        "data": 512
    }

    # 2. Patch both LLMClient.generate and httpx.AsyncClient.post
    from unittest.mock import MagicMock
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
         
        mock_generate.side_effect = [llm_payload_response, llm_natural_response]
        
        # Configure mocked API call to return status 200 and the JSON response
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = api_response_data
        mock_post.return_value = mock_api_res

        # 3. Call endpoint
        payload = {
            "message": user_query,
            "history": [],
            "session_id": "test-session-456",
            "AccessToken": "mock-token"
        }
        
        response = client.post("/api/v1/chat", json=payload)
        
        # 4. Assert responses
        assert response.status_code == 200
        data = response.json()
        
        assert data["reply"] == llm_natural_response
        assert data["intent"] == "trip_report"
        
        # Verify normalization (groupId eq 0041 was automatically appended)
        entities = data["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "count"
        
        filters = entities["filters"]
        # Filters should contain runDate (gte), runDate (lte), region, groupId
        assert len(filters) == 4
        fields = [f["field"] for f in filters]
        assert "groupId" in fields
        assert "region" in fields
        assert "tripStatus" not in fields
        assert "runDate" in fields
        
        # Verify specific filter values
        for f in filters:
            if f["field"] == "groupId":
                assert f["value"] == "0041"
            elif f["field"] == "region":
                assert f["value"] == "East"
        
        # Verify the actual API request details
        mock_post.assert_called_once()
        called_args, called_kwargs = mock_post.call_args
        assert called_args[0] == settings.TRIP_API_URL
        assert called_kwargs["json"] == entities
        
        # Verify raw API data returned in the response
        assert data["data"] == api_response_data

        # Verify that response generator was called with filters in the context
        assert mock_generate.call_count == 2
        gen_call_args, gen_call_kwargs = mock_generate.call_args_list[1]
        assert "QueryFilters" in gen_call_kwargs["prompt"]
        assert "East" in gen_call_kwargs["prompt"]


@pytest.mark.asyncio
async def test_trip_report_missing_date_range_flow():
    # Turn 1: User asks query without date range
    user_query_1 = "Download all trips where GPS and Portable Lock are active but ATD is missing"
    
    # Mock LLM Client Response for intent detector (first turn - no date range)
    llm_payload_response_1 = json.dumps({
        "entity": "trip",
        "operation": "find",
        "filters": [
            {
                "field": "gps",
                "operator": "eq",
                "value": "active"
            },
            {
                "field": "portableelock",
                "operator": "eq",
                "value": "active"
            },
            {
                "field": "atd",
                "operator": "eq",
                "value": "missing"
            }
        ],
        "select": []
    })

    # Mock LLM Client Response for intent detector (second turn - with date range in follow up response)
    llm_payload_response_2 = llm_payload_response_1

    # Mock LLM Client Response for response generator (second turn)
    llm_natural_response = "There are 15 trips matching the criteria."

    # API Response Mock
    api_response_data = {
        "success": True,
        "data": 15
    }

    from unittest.mock import MagicMock
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
         
        # We call LLM generate once in Turn 1 (intent detection),
        # and twice in Turn 2 (intent detection + response generation).
        mock_generate.side_effect = [llm_payload_response_1, llm_payload_response_2, llm_natural_response]
        
        # Configure mocked API call to return status 200 and the JSON response
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = api_response_data
        mock_post.return_value = mock_api_res

        # --- TURN 1 ---
        payload_1 = {
            "message": user_query_1,
            "history": [],
            "session_id": "test-session-date-validation",
            "AccessToken": "mock-token"
        }
        
        response_1 = client.post("/api/v1/chat", json=payload_1)
        
        # Assertions for Turn 1
        assert response_1.status_code == 200
        data_1 = response_1.json()
        assert data_1["reply"] == "Please provide the date range (start date and end date) for the trips."
        assert data_1["intent"] == "trip_report"
        assert data_1["data"] is None
        
        # Verify normalization of groupId
        filters_1 = data_1["entities"]["filters"]
        fields_1 = [f["field"] for f in filters_1]
        assert "groupId" in fields_1
        assert "gps" in fields_1
        assert "portableelock" in fields_1
        assert "atd" in fields_1
        
        # Verify no external API was called yet
        mock_post.assert_not_called()

        # --- TURN 2 ---
        history = [
            {"role": "user", "content": user_query_1},
            {"role": "assistant", "content": data_1["reply"]}
        ]
        
        payload_2 = {
            "message": "from 2026-01-01 to 2026-01-31",
            "history": history,
            "session_id": "test-session-date-validation",
            "AccessToken": "mock-token"
        }
        
        response_2 = client.post("/api/v1/chat", json=payload_2)
        
        # Assertions for Turn 2
        assert response_2.status_code == 200
        data_2 = response_2.json()
        assert data_2["reply"] == llm_natural_response
        assert data_2["intent"] == "trip_report"
        
        # Verify final payload contains runDate gte and lte
        entities_2 = data_2["entities"]
        filters_2 = entities_2["filters"]
        
        run_date_filters = [f for f in filters_2 if f.get("field") == "runDate"]
        assert len(run_date_filters) == 2
        
        start_filter = [f for f in run_date_filters if f.get("operator") == "gte"][0]
        end_filter = [f for f in run_date_filters if f.get("operator") == "lte"][0]
        assert start_filter["value"] == "2026-01-01 00:00:00"
        assert end_filter["value"] == "2026-01-31 23:59:59"
        
        # Verify the actual API request details
        mock_post.assert_called_once()
        called_args, called_kwargs = mock_post.call_args
        assert called_args[0] == settings.TRIP_API_URL
        assert called_kwargs["json"] == entities_2
        
        # Verify raw API data returned in the response
        assert data_2["data"] == api_response_data

@pytest.mark.asyncio
async def test_today_trips_flow():
    # 1. Prepare query and mock responses
    user_query = "Download all trips for the North region today."
    
    # Mock LLM Client Response for intent detector
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "find",
        "filters": [
            {
                "field": "runDate",
                "operator": "gte",
                "value": "TODAY_START"
            },
            {
                "field": "runDate",
                "operator": "lte",
                "value": "TODAY_END"
            },
            {
                "field": "region",
                "operator": "eq",
                "value": "North"
            }
        ],
        "select": []
    })

    # Mock LLM Client Response for response generator
    llm_natural_response = "There are no running trips in the North region today."

    # API Response Mock
    api_response_data = {
        "success": True,
        "data": 0
    }

    # 2. Patch both LLMClient.generate and httpx.AsyncClient.post
    from unittest.mock import MagicMock
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
         
        mock_generate.side_effect = [llm_payload_response, llm_natural_response]
        
        # Configure mocked API call to return status 200 and the JSON response
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = api_response_data
        mock_post.return_value = mock_api_res

        # 3. Call endpoint
        payload = {
            "message": user_query,
            "history": [],
            "session_id": "test-session-today",
            "AccessToken": "mock-token"
        }
        
        response = client.post("/api/v1/chat", json=payload)
        
        # 4. Assert responses
        assert response.status_code == 200
        data = response.json()
        
        assert data["reply"] == llm_natural_response
        assert data["intent"] == "trip_report"
        
        # Verify normalization (groupId eq 0041 was automatically appended)
        entities = data["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "find"
        
        filters = entities["filters"]
        # Filters should contain runDate (gte), runDate (lte), region, groupId
        assert len(filters) == 4
        fields = [f["field"] for f in filters]
        assert "groupId" in fields
        assert "region" in fields
        assert "tripStatus" not in fields
        assert "runDate" in fields
        
        # Verify that TODAY_START/TODAY_END were replaced with actual datetime strings
        import datetime
        now = datetime.datetime.now()
        expected_today_start = now.strftime("%Y-%m-%d 00:00:00")
        expected_today_end = now.strftime("%Y-%m-%d 23:59:59")
        
        for f in filters:
            if f["field"] == "runDate":
                if f["operator"] == "gte":
                    assert f["value"] == expected_today_start
                elif f["operator"] == "lte":
                    assert f["value"] == expected_today_end
                    
        # Verify the actual API request details
        mock_post.assert_called_once()
        called_args, called_kwargs = mock_post.call_args
        assert called_args[0] == settings.TRIP_API_URL
        assert called_kwargs["json"] == entities
        
        # Verify raw API data returned in the response
        assert data["data"] == api_response_data

@pytest.mark.asyncio
async def test_greeting_flow():
    # Call endpoint with a greeting
    payload = {
        "message": "hello chatbot",
        "history": [],
        "session_id": "test-session-greeting",
        "AccessToken": "mock-token"
    }
    
    response = client.post("/api/v1/chat", json=payload)
    
    # Assert responses
    assert response.status_code == 200
    data = response.json()
    
    assert data["reply"] == "I am Secutrak AI chatbot ,I am here to resolve your queries kindly ask me question about trips,"
    assert data["intent"] == "greeting"
    assert data["entities"] == {}
    assert data["data"] is None

@pytest.mark.asyncio
async def test_completed_trips_flow():
    # 1. Prepare query and mock responses
    user_query = "all trips for the North region last month completed trips"
    
    # Mock LLM Client Response for intent detector
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "count",
        "filters": [
            {
                "field": "runDate",
                "operator": "gte",
                "value": "LAST_MONTH_START"
            },
            {
                "field": "runDate",
                "operator": "lte",
                "value": "LAST_MONTH_END"
            },
            {
                "field": "region",
                "operator": "eq",
                "value": "North"
            }
        ],
        "select": []
    })

    # Mock LLM Client Response for response generator
    llm_natural_response = "There are no closed trips available for the North region from last month."

    # API Response Mock
    api_response_data = {
        "success": True,
        "data": 0
    }

    # 2. Patch both LLMClient.generate and httpx.AsyncClient.post
    from unittest.mock import MagicMock
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
         
        mock_generate.side_effect = [llm_payload_response, llm_natural_response]
        
        # Configure mocked API call to return status 200 and the JSON response
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = api_response_data
        mock_post.return_value = mock_api_res

        # 3. Call endpoint
        payload = {
            "message": user_query,
            "history": [],
            "session_id": "test-session-completed",
            "AccessToken": "mock-token"
        }
        
        response = client.post("/api/v1/chat", json=payload)
        
        # 4. Assert responses
        assert response.status_code == 200
        data = response.json()
        
        assert data["reply"] == llm_natural_response
        assert data["intent"] == "trip_report"
        
        # Verify normalization (groupId eq 0041 was automatically appended, tripStatus eq completed automatically appended/normalized)
        entities = data["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "count"
        
        filters = entities["filters"]
        # Filters should contain runDate (gte), runDate (lte), region, groupId, tripStatus
        assert len(filters) == 5
        fields = [f["field"] for f in filters]
        assert "groupId" in fields
        assert "region" in fields
        assert "tripStatus" in fields
        assert "runDate" in fields
        
        # Verify specific filter values
        for f in filters:
            if f["field"] == "tripStatus":
                assert f["value"] == "completed"
            elif f["field"] == "region":
                assert f["value"] == "North"
                
        # Verify the actual API request details
        mock_post.assert_called_once()
        called_args, called_kwargs = mock_post.call_args
        assert called_args[0] == settings.TRIP_API_URL
        assert called_kwargs["json"] == entities
        
        # Verify raw API data returned in the response
        assert data["data"] == api_response_data


@pytest.mark.asyncio
async def test_show_all_trips_vehicle_number_flow():
    # 1. Prepare query and mock responses
    user_query = "Show all trips of DL01HU5859 for last month"
    
    # Mock LLM Client Response for intent detector
    # We mock it to output a JSON containing the "entities" nested key as reported in the issue,
    # as well as the date range placeholders that the LLM extracts for "last month".
    llm_payload_response = json.dumps({
        "entities": {
            "entity": "trip",
            "operation": "find",
            "filters": [
                {
                    "field": "runDate",
                    "operator": "gte",
                    "value": "LAST_MONTH_START"
                },
                {
                    "field": "runDate",
                    "operator": "lte",
                    "value": "LAST_MONTH_END"
                },
                {
                    "field": "groupId",
                    "operator": "eq",
                    "value": "0041"
                },
                {
                    "field": "vehicleNo",
                    "operator": "eq",
                    "value": "DL01HU5859"
                }
            ]
        }
    })

    # Mock LLM Client Response for response generator
    llm_natural_response = "I found 5 active trips for vehicle DL01HU5859 from last month."

    # API Response Mock
    api_response_data = {
        "success": True,
        "data": [
            {"tripId": "1", "vehicleNo": "DL01HU5859", "status": "Active"},
            {"tripId": "2", "vehicleNo": "DL01HU5859", "status": "Active"}
        ]
    }

    # 2. Patch both LLMClient.generate and httpx.AsyncClient.post
    from unittest.mock import MagicMock
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
         
        mock_generate.side_effect = [llm_payload_response, llm_natural_response]
        
        # Configure mocked API call to return status 200 and the JSON response
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = api_response_data
        mock_post.return_value = mock_api_res

        # 3. Call endpoint
        payload = {
            "message": user_query,
            "history": [],
            "session_id": "test-session-vehicle-query",
            "AccessToken": "mock-token"
        }
        
        response = client.post("/api/v1/chat", json=payload)
        
        # 4. Assert responses
        assert response.status_code == 200
        data = response.json()
        
        assert data["reply"] == llm_natural_response
        assert data["intent"] == "trip_report"
        
        # Verify normalization (nested entities is unwrapped, dates are handled)
        entities = data["entities"]
        assert "entities" not in entities  # verify it was unwrapped!
        assert entities["entity"] == "trip"
        assert entities["operation"] == "find"
        assert entities["select"] == []
        
        filters = entities["filters"]
        # Filters should contain runDate (gte), runDate (lte), groupId, vehicleNo
        assert len(filters) == 4
        
        fields = [f["field"] for f in filters]
        assert "groupId" in fields
        assert "vehicleNo" in fields
        assert "tripStatus" not in fields
        assert "runDate" in fields
        
        # Verify specific filter values
        for f in filters:
            if f["field"] == "groupId":
                assert f["value"] == "0041"
            elif f["field"] == "vehicleNo":
                assert f["value"] == "DL01HU5859"
                
        # Verify the actual API request details
        mock_post.assert_called_once()
        called_args, called_kwargs = mock_post.call_args
        assert called_args[0] == settings.TRIP_API_URL
        assert called_kwargs["json"] == entities
        
        # Verify raw API data returned in the response
        assert data["data"] == api_response_data


@pytest.mark.asyncio
async def test_download_3rd_party_trips_no_status_flow():
    # Test "Download all 3rd-party device trips for January 2026"
    # This query does NOT contain any status keywords.
    user_query = "Download all 3rd-party device trips for January 2026"
    
    # Mock LLM Client Response for intent detector
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "find",
        "filters": [
            {
                "field": "runDate",
                "operator": "gte",
                "value": "2026-01-01 00:00:00"
            },
            {
                "field": "runDate",
                "operator": "lte",
                "value": "2026-01-31 23:59:59"
            },
            {
                "field": "portableelock",
                "operator": "eq",
                "value": ""
            }
        ],
        "select": []
    })

    llm_natural_response = "I found all 3rd-party device trips for January 2026."
    api_response_data = {"success": True, "data": []}

    from unittest.mock import MagicMock
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
            "session_id": "test-session-3rd-party",
            "AccessToken": "mock-token"
        }
        
        response = client.post("/api/v1/chat", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        
        entities = data["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "find"
        
        filters = entities["filters"]
        # Filters should contain runDate (gte), runDate (lte), groupId, portableelock
        assert len(filters) == 4
        fields = [f["field"] for f in filters]
        assert "groupId" in fields
        assert "portableelock" in fields
        assert "tripStatus" not in fields
        assert "runDate" in fields


@pytest.mark.asyncio
async def test_trip_status_keywords_mapping_flow():
    # Test mapping of different status keywords: running, active, completed, cancelled
    from unittest.mock import MagicMock
    
    # 1. "running" keyword
    user_query_running = "Download running trips for January 2026"
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "find",
        "filters": [],
        "select": []
    })
    
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_generate.side_effect = [llm_payload_response, "Done"]
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = {"success": True, "data": []}
        mock_post.return_value = mock_api_res

        payload = {
            "message": user_query_running,
            "history": [],
            "session_id": "test-session-running-kw",
            "AccessToken": "mock-token"
        }
        response = client.post("/api/v1/chat", json=payload)
        assert response.status_code == 200
        filters = response.json()["entities"]["filters"]
        status_filter = [f for f in filters if f.get("field") == "tripStatus"][0]
        assert status_filter["value"] == "running"

    # 2. "active" keyword
    user_query_active = "Download active trips for January 2026"
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_generate.side_effect = [llm_payload_response, "Done"]
        mock_post.return_value = mock_api_res

        payload = {
            "message": user_query_active,
            "history": [],
            "session_id": "test-session-active-kw",
            "AccessToken": "mock-token"
        }
        response = client.post("/api/v1/chat", json=payload)
        assert response.status_code == 200
        filters = response.json()["entities"]["filters"]
        status_filter = [f for f in filters if f.get("field") == "tripStatus"][0]
        assert status_filter["value"] == "active"

    # 3. "cancelled" keyword
    user_query_cancelled = "Download cancelled trips for January 2026"
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_generate.side_effect = [llm_payload_response, "Done"]
        mock_post.return_value = mock_api_res

        payload = {
            "message": user_query_cancelled,
            "history": [],
            "session_id": "test-session-cancelled-kw",
            "AccessToken": "mock-token"
        }
        response = client.post("/api/v1/chat", json=payload)
        assert response.status_code == 200
        filters = response.json()["entities"]["filters"]
        status_filter = [f for f in filters if f.get("field") == "tripStatus"][0]
        assert status_filter["value"] == "cancelled"


@pytest.mark.asyncio
async def test_gps_inactive_no_trip_status_flow():
    # Test that "all GPS inactive trips for January 2026" does NOT trigger tripStatus field since "inactive" should not match "active" word boundary
    user_query = "all GPS inactive trips for January 2026"
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "count",
        "filters": [
            {
                "field": "runDate",
                "operator": "gte",
                "value": "2026-01-01 00:00:00"
            },
            {
                "field": "runDate",
                "operator": "lte",
                "value": "2026-01-31 23:59:59"
            },
            {
                "field": "gps",
                "operator": "eq",
                "value": "inactive"
            }
        ],
        "select": []
    })

    from unittest.mock import MagicMock
    with patch("app.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_generate.side_effect = [llm_payload_response, "Done"]
        mock_api_res = MagicMock()
        mock_api_res.status_code = 200
        mock_api_res.json.return_value = {"success": True, "data": []}
        mock_post.return_value = mock_api_res

        payload = {
            "message": user_query,
            "history": [],
            "session_id": "test-session-gps-inactive",
            "AccessToken": "mock-token"
        }
        response = client.post("/api/v1/chat", json=payload)
        assert response.status_code == 200
        filters = response.json()["entities"]["filters"]
        
        # Verify no tripStatus filter is present
        fields = [f["field"] for f in filters]
        assert "tripStatus" not in fields
        assert "gps" in fields


@pytest.mark.asyncio
async def test_download_fixed_elock_trips_flow():
    # Test "Download all Fixed E-Lock device trips for January 2026"
    # This query specifies Fixed E-Lock and maps to operation find, fixedelock filter with value "".
    user_query = "Download all Fixed E-Lock device trips for January 2026"
    
    # Mock LLM Client Response for intent detector
    llm_payload_response = json.dumps({
        "entity": "trip",
        "operation": "find",
        "filters": [
            {
                "field": "runDate",
                "operator": "gte",
                "value": "2026-01-01 00:00:00"
            },
            {
                "field": "runDate",
                "operator": "lte",
                "value": "2026-01-31 23:59:59"
            },
            {
                "field": "fixedelock",
                "operator": "eq",
                "value": ""
            }
        ],
        "select": []
    })

    llm_natural_response = "I found all Fixed E-Lock device trips for January 2026."
    api_response_data = {"success": True, "data": []}

    from unittest.mock import MagicMock
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
            "session_id": "test-session-fixed-elock",
            "AccessToken": "mock-token"
        }
        
        response = client.post("/api/v1/chat", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        
        entities = data["entities"]
        assert entities["entity"] == "trip"
        assert entities["operation"] == "find"
        
        filters = entities["filters"]
        # Filters should contain runDate (gte), runDate (lte), groupId, fixedelock
        assert len(filters) == 4
        fields = [f["field"] for f in filters]
        assert "groupId" in fields
        assert "fixedelock" in fields
        assert "tripStatus" not in fields
        assert "runDate" in fields
        
        for f in filters:
            if f["field"] == "fixedelock":
                assert f["value"] == ""
