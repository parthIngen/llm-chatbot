import re
from typing import List, Tuple
from app.utils.logger import logger
from app.llm.prompts.base_prompt import BASE_PROMPT
from app.llm.prompts.trip_prompt import TRIP_PROMPT
from app.llm.prompts.gps_prompt import GPS_PROMPT
from app.llm.prompts.elock_prompt import ELOCK_PROMPT
from app.llm.prompts.device_combination_prompt import DEVICE_COMBINATION_PROMPT
from app.llm.prompts.transporter_prompt import TRANSPORTER_PROMPT
from app.llm.prompts.vehicle_prompt import VEHICLE_PROMPT
from app.llm.prompts.delay_prompt import DELAY_PROMPT
from app.llm.prompts.comparison_prompt import COMPARISON_PROMPT

# Domain keyword and regex patterns
_GPS_PATTERN = re.compile(
    r'\b(?:gps|fixed[\s-]gps|secutrak|wheelseye|icici|ilgic|3rd[\s-]party|third[\s-]party|non[\s-]?gps|no[\s-]?gps|nogps)\b',
    re.IGNORECASE
)

_ELOCK_PATTERN = re.compile(
    r'\b(?:e[\s-]?lock|fixed[\s-]e?lock|portable[\s-]e?lock|fixedlock|portablelock|fixed[\s-]lock|portable[\s-]lock)\b',
    re.IGNORECASE
)

_DEVICE_COMBO_PATTERN = re.compile(
    r'\b(?:together|without\s+any\s+device|without\s+(?:any\s+)?gps|fixed\s+gps\s+only|gps\s+only|portable\s+e[\s-]?lock\s+only|fixed\s+e[\s-]?lock\s+only|both\s+fixed|combination)\b'
    r'|(?:\bgps\b.*?\b(?:e[\s-]?lock|lock)\b)'
    r'|(?:\b(?:fixed[\s-]e?lock|fixed[\s-]lock)\b.*?\b(?:portable[\s-]e?lock|portable[\s-]lock)\b)',
    re.IGNORECASE
)

_TRANSPORTER_PATTERN = re.compile(
    r'\b(?:transporters?|fleet)\b',
    re.IGNORECASE
)

_VEHICLE_PATTERN = re.compile(
    r'\b(?:vehicles?|vehicle[\s-]?(?:no|number|num)|[a-z]{2}\s*\d{1,2}\s*[a-z]{1,3}\s*\d{4})\b',
    re.IGNORECASE
)

_DELAY_PATTERN = re.compile(
    r'\b(?:delayed?|delay|departure\s+delayed|arrival\s+delayed|atd|ata)\b',
    re.IGNORECASE
)

_COMPARISON_PATTERN = re.compile(
    r'\b(?:compare|comparison|versus|vs\.?|difference|ratio|breakdown|in\s+each\s+region|each\s+region|region[\s-]wise|across\s+regions?|priorit(?:y|ies)|high\s+priority|standard\s+priority|normal\s+priority|low\s+priority)\b'
    r'|(?:\bnetwork\b.*?\blocal\b)'
    r'|(?:\bintercity\b.*?\bintracity\b)',
    re.IGNORECASE
)

_TRIP_PATTERN = re.compile(
    r'\b(?:trips?|running|active|closed|completed|cancelled|cancel|valid|intercity|intracity|network|local|feeder|empty|surface|sfc|speed|air|delivery|pickup|pick\s+up|source|destination|from|to|route|north|south|east|west|geofence|forcefull?|forcefully|branch|branches|area|areas)\b',
    re.IGNORECASE
)

def select_domains(message: str) -> List[str]:
    """
    Identifies relevant domains for the query message.
    """
    domains: List[str] = []
    clean_msg = message.strip()

    has_gps = bool(_GPS_PATTERN.search(clean_msg))
    has_elock = bool(_ELOCK_PATTERN.search(clean_msg))
    has_device_combo = bool(_DEVICE_COMBO_PATTERN.search(clean_msg)) or (has_gps and has_elock)
    has_transporter = bool(_TRANSPORTER_PATTERN.search(clean_msg))
    has_vehicle = bool(_VEHICLE_PATTERN.search(clean_msg))
    has_delay = bool(_DELAY_PATTERN.search(clean_msg))
    has_comparison = bool(_COMPARISON_PATTERN.search(clean_msg))
    has_trip = bool(_TRIP_PATTERN.search(clean_msg))

    if has_device_combo:
        domains.append("DEVICE_COMBINATION")
    if has_gps:
        domains.append("GPS")
    if has_elock:
        domains.append("ELOCK")
    if has_delay:
        domains.append("DELAY")
    if has_transporter:
        domains.append("TRANSPORTER")
    if has_vehicle:
        domains.append("VEHICLE")
    if has_comparison:
        domains.append("COMPARISON")
    if has_trip or not domains:
        domains.append("TRIP")

    return domains

def route_prompt(message: str, user_prompt: str = "") -> Tuple[str, List[str]]:
    """
    Deterministically selects and combines required domain prompt modules.
    Always includes BASE_PROMPT.
    Logs prompt metrics.
    """
    domains = select_domains(message)

    prompt_parts = [BASE_PROMPT]
    
    if "TRIP" in domains:
        prompt_parts.append(TRIP_PROMPT)
    if "DELAY" in domains:
        prompt_parts.append(DELAY_PROMPT)
    if "GPS" in domains:
        prompt_parts.append(GPS_PROMPT)
    if "ELOCK" in domains:
        prompt_parts.append(ELOCK_PROMPT)
    if "DEVICE_COMBINATION" in domains:
        prompt_parts.append(DEVICE_COMBINATION_PROMPT)
    if "TRANSPORTER" in domains:
        prompt_parts.append(TRANSPORTER_PROMPT)
    if "VEHICLE" in domains:
        prompt_parts.append(VEHICLE_PROMPT)
    if "COMPARISON" in domains:
        prompt_parts.append(COMPARISON_PROMPT)

    system_prompt = "\n\n".join(prompt_parts)

    if user_prompt:
        logger.info(
            "LLM prompt metrics | system_chars=%s | user_chars=%s | total_chars=%s | domains=%s",
            len(system_prompt),
            len(user_prompt),
            len(system_prompt) + len(user_prompt),
            domains
        )

    return system_prompt, domains

