from app.llm.prompts.base_prompt import BASE_PROMPT
from app.llm.prompts.trip_prompt import TRIP_PROMPT
from app.llm.prompts.gps_prompt import GPS_PROMPT
from app.llm.prompts.elock_prompt import ELOCK_PROMPT
from app.llm.prompts.device_combination_prompt import DEVICE_COMBINATION_PROMPT
from app.llm.prompts.transporter_prompt import TRANSPORTER_PROMPT
from app.llm.prompts.vehicle_prompt import VEHICLE_PROMPT
from app.llm.prompts.delay_prompt import DELAY_PROMPT
from app.llm.prompts.comparison_prompt import COMPARISON_PROMPT
from app.llm.prompts.response_prompt import (
    RESPONSE_GENERATION_SYSTEM_PROMPT,
    RESPONSE_GENERATION_USER_PROMPT_TEMPLATE
)
from app.llm.prompts.prompt_router import route_prompt, select_domains

INTENT_EXTRACTION_USER_PROMPT_TEMPLATE = """Date Context:
{date_context}

User Query: "{message}"

JSON Query:"""

# Complete fallback / default system prompt for backward compatibility
INTENT_EXTRACTION_SYSTEM_PROMPT = "\n\n".join([
    BASE_PROMPT,
    TRIP_PROMPT,
    GPS_PROMPT,
    ELOCK_PROMPT,
    DEVICE_COMBINATION_PROMPT,
    TRANSPORTER_PROMPT,
    VEHICLE_PROMPT,
    DELAY_PROMPT,
    COMPARISON_PROMPT
])

__all__ = [
    "BASE_PROMPT",
    "TRIP_PROMPT",
    "GPS_PROMPT",
    "ELOCK_PROMPT",
    "DEVICE_COMBINATION_PROMPT",
    "TRANSPORTER_PROMPT",
    "VEHICLE_PROMPT",
    "DELAY_PROMPT",
    "COMPARISON_PROMPT",
    "RESPONSE_GENERATION_SYSTEM_PROMPT",
    "RESPONSE_GENERATION_USER_PROMPT_TEMPLATE",
    "INTENT_EXTRACTION_USER_PROMPT_TEMPLATE",
    "INTENT_EXTRACTION_SYSTEM_PROMPT",
    "route_prompt",
    "select_domains",
]

