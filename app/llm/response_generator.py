import json
from typing import Any, Dict, List
from app.llm.client import LLMClient, LLMRateLimitError
from app.llm.prompts import RESPONSE_GENERATION_SYSTEM_PROMPT, RESPONSE_GENERATION_USER_PROMPT_TEMPLATE
from app.utils.logger import log_error

class ResponseGenerator:
    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()

    def _truncate_data_for_llm(self, data: Any, max_list_len: int = 3) -> Any:
        """
        Recursively truncates lists inside the data to avoid overwhelming the LLM context.
        """
        if isinstance(data, list):
            if len(data) > max_list_len:
                return [self._truncate_data_for_llm(item, max_list_len) for item in data[:max_list_len]] + [
                    f"... [TRUNCATED {len(data) - max_list_len} MORE ITEMS FROM THIS LIST FOR LLM CONTEXT]"
                ]
            else:
                return [self._truncate_data_for_llm(item, max_list_len) for item in data]
        elif isinstance(data, dict):
            return {k: self._truncate_data_for_llm(v, max_list_len) for k, v in data.items()}
        return data

    async def generate_response(self, message: str, api_result: Any, session_id: str, entities: Dict[str, Any] = None) -> str:
        """
        Generates a natural language response based on user message and API results.
        """
        total_trips = 0
        api_data = None
        
        if isinstance(api_result, dict):
            # Check for the new format {"success": True/False, "data": ...}
            if "data" in api_result:
                data_val = api_result["data"]
                if isinstance(data_val, int):
                    total_trips = data_val
                    api_data = data_val
                elif isinstance(data_val, list):
                    total_trips = len(data_val)
                    api_data = data_val
                else:
                    api_data = data_val
            else:
                # Fallback to old format
                report_list = api_result.get("Report") or api_result.get("trips") or []
                if isinstance(report_list, list):
                    total_trips = len(report_list)
                api_data = api_result
        elif isinstance(api_result, list):
            total_trips = len(api_result)
            api_data = api_result
        else:
            api_data = api_result

        # Truncate lists in API result to protect local LLM context window
        truncated_result = self._truncate_data_for_llm(api_data)
        
        context_data = {
            "TotalTripsCount": total_trips,
            "API_Result": truncated_result
        }
        if entities and "filters" in entities:
            context_data["QueryFilters"] = entities["filters"]
        
        # Serialize the API response to pass it to the prompt
        try:
            context_str = json.dumps(context_data, indent=2)
        except Exception:
            context_str = str(context_data)
            
        prompt = RESPONSE_GENERATION_USER_PROMPT_TEMPLATE.format(message=message, context=context_str)
        
        try:
            response = await self.llm_client.generate(
                prompt=prompt,
                system=RESPONSE_GENERATION_SYSTEM_PROMPT,
                format_json=False
            )
            return response
        except LLMRateLimitError as rle:
            raise rle
        except Exception as exc:
            log_error(session_id, "RESPONSE_GENERATION_FAILED", str(exc))
            # Graceful fallback: return a default message and serialized information
            return f"I found the trip details, but had trouble formatting the response: {total_trips} trips found."
