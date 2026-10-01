import json
import time
import httpx
from typing import Any, Dict
from app.utils.logger import log_tool_selected, log_api_call, log_api_response, log_error
from app.config import settings

class BaseTool:
    """Base class for all intent-handler tools."""
    async def execute(self, entities: Dict[str, Any], access_token: str, session_id: str) -> Any:
        raise NotImplementedError("Tools must implement the execute method.")

class TripTool(BaseTool):
    def __init__(self):
        pass

    async def execute(self, entities: Dict[str, Any], access_token: str, session_id: str) -> Any:
        """
        Receives the structured JSON query payload in entities,
        logs it, and forwards it directly to settings.TRIP_API_URL.
        """
        log_tool_selected(session_id, "TripTool")
        
        payload = entities

        # Print payload to console for troubleshooting
        print("\n" + "=" * 60)
        print("FORWARDING PAYLOAD TO EXTERNAL TRIP API:")
        print(json.dumps(payload, indent=4))
        print(f"Target URL: {settings.TRIP_API_URL}")
        print("=" * 60 + "\n")

        # Log call details
        log_api_call(session_id, settings.TRIP_API_URL, payload)
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json"
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
            headers["token"] = access_token  # For compatibility

        # Make the actual API request
        try:
            start_time = time.time()
            async with httpx.AsyncClient(timeout=settings.TRIP_API_TIMEOUT, verify=False) as client:
                response = await client.post(
                    settings.TRIP_API_URL,
                    json=payload,
                    headers=headers
                )
            
            duration = time.time() - start_time
            log_api_response(session_id, duration, response.status_code)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    return data
                except ValueError:
                    # If response is not JSON
                    return {"raw_response": response.text}
            else:
                log_error(session_id, "API_CALL_FAILED", f"Status: {response.status_code}", response.text)
                return {
                    "error": f"API returned status code {response.status_code}",
                    "detail": response.text,
                    "success": False
                }
                
        except Exception as e:
            log_error(session_id, "API_CONNECTION_ERROR", str(e))
            return {
                "error": f"Failed to connect to API: {str(e)}",
                "success": False
            }
