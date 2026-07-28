from typing import Any, Dict
from app.tools.trip_tool import TripTool, BaseTool
from app.utils.logger import log_error

class UnsupportedIntentError(Exception):
    """Exception raised when the detected intent is not supported."""
    pass

class ToolRouter:
    def __init__(self):
        # Register tools by mapping intent strings to Tool instances
        self.tools: Dict[str, BaseTool] = {
            "trip_report": TripTool(),
            # Future tools can be added here easily:
            # "vehicle": VehicleTool(),
            # "driver": DriverTool(),
        }

    async def route_and_execute(self, intent: str, entities: Dict[str, Any], access_token: str, session_id: str) -> Any:
        """
        Dispatches request to appropriate tool based on intent.
        Raises UnsupportedIntentError if intent cannot be handled.
        """
        tool = self.tools.get(intent)
        if not tool:
            log_error(session_id, "UNSUPPORTED_INTENT", f"No registered tool found for intent '{intent}'")
            raise UnsupportedIntentError(f"Intent '{intent}' is currently not supported by the backend.")
            
        return await tool.execute(entities, access_token, session_id)
