import json
import datetime
import calendar
import re
from typing import List, Dict, Any
from app.llm.client import LLMClient, LLMRateLimitError
from app.llm.prompts import INTENT_EXTRACTION_SYSTEM_PROMPT, INTENT_EXTRACTION_USER_PROMPT_TEMPLATE
from app.utils.logger import log_intent_detected, log_entities_extracted, log_error
from app.config import settings

class IntentDetectorError(Exception):
    """Exception raised when intent detection fails."""
    pass

# Map of month name / abbreviation -> month number
_MONTH_MAP: dict[str, int] = {
    "january": 1,  "jan": 1,
    "february": 2, "feb": 2,
    "march": 3,    "mar": 3,
    "april": 4,    "apr": 4,
    "may": 5,
    "june": 6,     "jun": 6,
    "july": 7,     "jul": 7,
    "august": 8,   "aug": 8,
    "september": 9,"sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11,"nov": 11,
    "december": 12,"dec": 12,
}

_MONTH_PATTERN = re.compile(
    r'\b(january|jan|february|feb|march|mar|april|apr|may|june|jun'
    r'|july|jul|august|aug|september|sept|sep|october|oct'
    r'|november|nov|december|dec)'
    r'(?:[\s,]+(?P<year>20\d{2}))?\b',
    re.IGNORECASE,
)

def _resolve_month_range(text: str, reference_now: datetime.datetime) -> tuple[str | None, str | None]:
    """Return (start, end) date-time strings for the first month name found in *text*.

    If the message also contains a 4-digit year (e.g. '2026'), that year is used.
    Otherwise the most-recently-passed occurrence of that month relative to
    *reference_now* is used (so 'august' in September 2026 → August 2026).
    """
    m = _MONTH_PATTERN.search(text)
    if not m:
        return None, None

    month_num = _MONTH_MAP.get(m.group(1).lower())
    if not month_num:
        return None, None

    # Explicit year wins; otherwise pick most-recently-passed month occurrence
    year_str = m.group("year")
    if year_str:
        year = int(year_str)
    else:
        year = reference_now.year
        # If the named month is still in the future this calendar year, use last year
        if month_num > reference_now.month:
            year -= 1

    last_day = calendar.monthrange(year, month_num)[1]
    start = f"{year}-{month_num:02d}-01 00:00:00"
    end   = f"{year}-{month_num:02d}-{last_day:02d} 23:59:59"
    return start, end

def extract_date_range(text: str) -> tuple[str | None, str | None]:
    # Match YYYY-MM-DD HH:MM:SS or YYYY-MM-DD
    pattern = r'(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?'
    matches = re.findall(pattern, text)
    
    if len(matches) >= 2:
        # First match is start date
        m1_date, m1_time = matches[0]
        start_date = f"{m1_date} {m1_time}" if m1_time else f"{m1_date} 00:00:00"
        
        # Second match is end date
        m2_date, m2_time = matches[1]
        end_date = f"{m2_date} {m2_time}" if m2_time else f"{m2_date} 23:59:59"
        
        return start_date, end_date
    return None, None

def detect_shipment_method(msg_lower: str) -> str | None:
    """Detect and normalize shipment method names to canonical values:
    feeder, empty, surface, sfc, speed, air, network, delivery, Pick Up, Pick Up and Delivery, Comm/BZ-Out, Comm/BZ-In.
    """
    if re.search(r'\b(?:pick[\s-]?up|pickup)\s*(?:and|&|\+)\s*delivery\b', msg_lower):
        return "Pick Up and Delivery"
    if re.search(r'\b(?:pick[\s-]?up|pickup)\b', msg_lower):
        return "Pick Up"
    if re.search(r'\bcomm[\s/_-]*bz[\s/_-]*out\b', msg_lower):
        return "Comm/BZ-Out"
    if re.search(r'\bcomm[\s/_-]*bz[\s/_-]*in\b', msg_lower):
        return "Comm/BZ-In"
    if re.search(r'\bcustomer[\s_-]+delivery\b|\bcustomer[\s_-]+del\b', msg_lower):
        return "delivery"
    if re.search(r'\bair[\s_-]+(?:network|truck)\b', msg_lower):
        return "air"
    if re.search(r'\bsfc[\s_-]+(?:network|truck|schedule)\b', msg_lower):
        return "sfc"
    if re.search(r'\bspeed[\s_-]+(?:truck|network)\b', msg_lower):
        return "speed"
    if re.search(r'\bfeeder[\s_-]+(?:truck|network)\b', msg_lower):
        return "feeder"
    if re.search(r'\bempty[\s_-]+(?:truck|network)\b', msg_lower):
        return "empty"
    if re.search(r'\bsurface[\s_-]+(?:network|truck)\b', msg_lower):
        return "surface"
    
    # Standalone keywords
    m = re.search(
        r'\b(feeder|empty|surface|sfc|speed|air|delivery)\b',
        msg_lower
    )
    if m:
        return m.group(1)
    if re.search(r'\bnetwork\b', msg_lower):
        return "network"
    return None

class IntentDetector:
    def __init__(self, llm_client: LLMClient = None):
        provider = getattr(settings, "INTENT_LLM_PROVIDER", "groq")
        self.llm_client = llm_client or LLMClient(provider=provider)

    async def detect(self, message: str, history: List[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
        """
        Calls Ollama LLM to translate natural language queries into the structured JSON query format.
        """
        # Restrict history to at most 3 past messages for concise context
        history = history[-3:] if history else []

        # Check if this is a follow-up answer to a date range clarification request.
        # IMPORTANT: match ONLY the specific clarification question the bot asks when
        # it needs a date range from the user.  Do NOT match on ordinary result responses
        # like "Total 19 trips found for date range from …" — those also contain "date
        # range" but are final answers, not pending questions.
        _DATE_CLARIFICATION_MARKER = "please provide the date range (start date and end date)"
        is_follow_up = False
        prev_user_msg = None
        if history and len(history) >= 2:
            last_assistant_msg = history[-1]
            if (
                last_assistant_msg.get("role") == "assistant"
                and _DATE_CLARIFICATION_MARKER in last_assistant_msg.get("content", "").lower()
            ):
                for item in reversed(history[:-1]):
                    if item.get("role") == "user":
                        prev_user_msg = item.get("content")
                        is_follow_up = True
                        break

        if is_follow_up and prev_user_msg:
            message = f"{prev_user_msg} for date range {message}"

        # --- Date-Refinement Follow-Up Detection ---
        # Handle cases like "i want for july" / "for last month" / "july 2026" sent
        # AFTER the bot already returned a result (not a clarification question).
        # If the current message is short and contains only a temporal reference,
        # automatically prepend the last substantive user query so the full intent
        # (e.g. "sfc network schedule trips for july") is reconstructed.
        if not is_follow_up and history:
            _DATE_REF_PATTERNS = [
                r'\b(january|february|march|april|may|june|july|august|september|october|november|december'
                r'|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b',
                r'\blast\s+(?:week|month|year)\b',
                r'\bthis\s+(?:week|month|year)\b',
                r'\btoday\b',
                r'\byesterday\b',
                r'\b\d{4}-\d{2}-\d{2}\b',
            ]
            _msg_lower_tmp = message.lower()
            _msg_is_date_ref = any(re.search(p, _msg_lower_tmp) for p in _DATE_REF_PATTERNS)
            _msg_is_short = len(message.strip().split()) <= 8

            if _msg_is_date_ref and _msg_is_short:
                # Find the last substantive user query in history (more than 3 words)
                for _item in reversed(history):
                    if _item.get("role") == "user":
                        _prev_q = _item.get("content", "").strip()
                        if len(_prev_q.split()) > 3:
                            message = f"{_prev_q} {message}"
                            break

        # Extract dates using regex as a reliable fallback
        extracted_start, extracted_end = extract_date_range(message)

        # 1. Compute dynamic date context
        now = datetime.datetime.now()
        
        # Calculate today
        today_start = now.strftime("%Y-%m-%d 00:00:00")
        today_end = now.strftime("%Y-%m-%d 23:59:59")

        # Calculate yesterday
        yesterday = now - datetime.timedelta(days=1)
        yesterday_start = yesterday.strftime("%Y-%m-%d 00:00:00")
        yesterday_end = yesterday.strftime("%Y-%m-%d 23:59:59")

        # Calculate last week (relative to current date, matching July 9 to July 15 when today is July 23)
        last_week_start = (now - datetime.timedelta(days=14)).strftime("%Y-%m-%d 00:00:00")
        last_week_end = (now - datetime.timedelta(days=8)).strftime("%Y-%m-%d 23:59:59")
        
        # Calculate last month (previous calendar month)
        try:
            first_day_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_day_last_month = first_day_current_month - datetime.timedelta(seconds=1)
            first_day_last_month = last_day_last_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_start = first_day_last_month.strftime("%Y-%m-%d 00:00:00")
            last_month_end = last_day_last_month.strftime("%Y-%m-%d 23:59:59")
        except Exception:
            # Fallback if date replacement fails
            last_month_start = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d 00:00:00")
            last_month_end = now.strftime("%Y-%m-%d 23:59:59")
        
        date_context = (
            f"Current Date: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- TODAY_START: {today_start}\n"
            f"- TODAY_END: {today_end}\n"
            f"- YESTERDAY_START: {yesterday_start}\n"
            f"- YESTERDAY_END: {yesterday_end}\n"
            f"- LAST_WEEK_START: {last_week_start}\n"
            f"- LAST_WEEK_END: {last_week_end}\n"
            f"- LAST_MONTH_START: {last_month_start}\n"
            f"- LAST_MONTH_END: {last_month_end}"
        )

        msg_lower = message.lower()
        has_status_keyword = False
        status_override = None

        # Format user prompt
        prompt = INTENT_EXTRACTION_USER_PROMPT_TEMPLATE.format(message=message, date_context=date_context)
        
        try:
            # We enforce JSON output from LLM
            response_text = await self.llm_client.generate(
                prompt=prompt,
                system=INTENT_EXTRACTION_SYSTEM_PROMPT,
                format_json=True
            )
            
            # Parse response
            query_payload = json.loads(response_text)
            
            if not isinstance(query_payload, dict):
                query_payload = {}
            
            # If the LLM response is wrapped in an 'entities' or 'query_payload' key, unwrap it
            if "entities" in query_payload and isinstance(query_payload["entities"], dict):
                query_payload = query_payload["entities"]
            elif "query_payload" in query_payload and isinstance(query_payload["query_payload"], dict):
                query_payload = query_payload["query_payload"]
                
            # Normalize schema
            query_payload["entity"] = "trip"
            query_payload["operation"] = "count"
                
            if "filters" not in query_payload or not isinstance(query_payload["filters"], list):
                query_payload["filters"] = []
                
            if "select" not in query_payload or not isinstance(query_payload["select"], list):
                query_payload["select"] = []
                
            # Post-process analytics filters: extract from filters and place at the top level
            analytics_list = []
            new_filters = []
            for f in query_payload.get("filters", []):
                if isinstance(f, dict) and f.get("field") == "analytics":
                    val = f.get("value")
                    if isinstance(val, list):
                        for item in val:
                            analytics_list.append({"type": item})
                    elif isinstance(val, str):
                        analytics_list.append({"type": val})
                else:
                    new_filters.append(f)
            query_payload["filters"] = new_filters

            # Also handle if LLM generated analytics directly as top-level key
            if "analytics" in query_payload:
                existing_analytics = query_payload["analytics"]
                if isinstance(existing_analytics, list):
                    for item in existing_analytics:
                        if isinstance(item, dict) and "type" in item:
                            analytics_list.append(item)
                        elif isinstance(item, str):
                            analytics_list.append({"type": item})
                elif isinstance(existing_analytics, str):
                    analytics_list.append({"type": existing_analytics})
                elif isinstance(existing_analytics, dict):
                    if "type" in existing_analytics:
                        analytics_list.append(existing_analytics)
                    else:
                        for k, v in existing_analytics.items():
                            analytics_list.append({"type": v})
            
            # Deduplicate items by type
            seen_types = set()
            deduped_analytics = []
            for item in analytics_list:
                t = item.get("type")
                if t and t not in seen_types:
                    seen_types.add(t)
                    deduped_analytics.append(item)

            if deduped_analytics:
                query_payload["analytics"] = deduped_analytics
                
            # Replace placeholder dates in filters
            for f in query_payload["filters"]:
                if not isinstance(f, dict):
                    continue
                val = f.get("value")
                if isinstance(val, str):
                    if "TODAY_START" in val:
                        f["value"] = today_start
                    elif "TODAY_END" in val:
                        f["value"] = today_end
                    elif "YESTERDAY_START" in val:
                        f["value"] = yesterday_start
                    elif "YESTERDAY_END" in val:
                        f["value"] = yesterday_end
                    elif "LAST_WEEK_START" in val:
                        f["value"] = last_week_start
                    elif "LAST_WEEK_END" in val:
                        f["value"] = last_week_end
                    elif "LAST_MONTH_START" in val:
                        f["value"] = last_month_start
                    elif "LAST_MONTH_END" in val:
                        f["value"] = last_month_end
                        
            # Normalize any runDate filters that might have been extracted by LLM
            for f in query_payload["filters"]:
                if isinstance(f, dict) and f.get("field") == "runDate":
                    val = f.get("value")
                    if isinstance(val, str) and len(val) == 10 and re.match(r'^\d{4}-\d{2}-\d{2}$', val):
                        if f.get("operator") == "gte":
                            f["value"] = f"{val} 00:00:00"
                        elif f.get("operator") == "lte":
                            f["value"] = f"{val} 23:59:59"

            # Apply regex-extracted date ranges as override
            if extracted_start and extracted_end:
                # Remove any existing runDate filters first to avoid duplicates
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "runDate")
                ]
                # Append correct runDate filters
                query_payload["filters"].append({
                    "field": "runDate",
                    "operator": "gte",
                    "value": extracted_start
                })
                query_payload["filters"].append({
                    "field": "runDate",
                    "operator": "lte",
                    "value": extracted_end
                })

            # If no runDate filter was extracted by regex or LLM, check for relative date keywords in user query
            has_run_date = any(isinstance(f, dict) and f.get("field") == "runDate" for f in query_payload["filters"])
            if not has_run_date:
                if re.search(r'\btoday\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": today_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": today_end})
                elif re.search(r'\byesterday\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": yesterday_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": yesterday_end})
                elif re.search(r'\blast\s+week\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": last_week_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": last_week_end})
                elif re.search(r'\blast\s+month\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": last_month_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": last_month_end})
                else:
                    # Resolve named month (e.g. "august", "of august", "august 2026", "aug")
                    m_start, m_end = _resolve_month_range(msg_lower, now)
                    if m_start and m_end:
                        query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": m_start})
                        query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": m_end})

            # Ensure operator "missing" is corrected to "eq" with value "missing"
            for f in query_payload["filters"]:
                if isinstance(f, dict):
                    if f.get("operator") == "missing":
                        f["operator"] = "eq"
                        f["value"] = "missing"
                        
            # Normalize fixedelock/portableelock/gps legacy lowercase field names to canonical PascalCase
            field_alias_map = {
                "fixedelock": "FixedELock",
                "portableelock": "PortableELock",
                "gps": "FixedGPS",
                "atd": "ATD",
                "ata": "ATA",
            }
            for f in query_payload["filters"]:
                if isinstance(f, dict):
                    raw_field = f.get("field", "")
                    if raw_field == "GPS":
                        continue
                    if raw_field.lower() == "gps" and f.get("value") == "existing":
                        f["field"] = "GPS"
                        continue
                    alias = raw_field.lower()
                    if alias in field_alias_map:
                        f["field"] = field_alias_map[alias]

            # Normalize FixedELock and PortableELock values to empty string unless a known status is requested
            _ELOCK_KNOWN_VALUES = {"active", "inactive", "nogps", "gpsinactive", "na", "existing"}
            for f in query_payload["filters"]:
                if isinstance(f, dict) and f.get("field") in ["FixedELock", "PortableELock"]:
                    val = f.get("value")
                    if isinstance(val, str) and val.lower() not in _ELOCK_KNOWN_VALUES:
                        f["value"] = ""

            # If FixedELock or PortableELock is inactive/nogps/etc., remove the redundant FixedGPS inactive filter
            has_inactive_elock = any(
                isinstance(f, dict)
                and f.get("field") in ["FixedELock", "PortableELock"]
                and f.get("value", "").lower() in {"inactive", "nogps", "gpsinactive", "na"}
                for f in query_payload["filters"]
            )
            if has_inactive_elock:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "FixedGPS" and f.get("value", "").lower() in {"inactive", "nogps", "gpsinactive", "na"})
                ]
                        
            # Ensure groupId is always present as a filter with "0041"
            has_group_id = False
            for f in query_payload["filters"]:
                if isinstance(f, dict) and f.get("field") == "groupId":
                    has_group_id = True
                    f["value"] = "0041"
                    f["operator"] = "eq"
                    break
            if not has_group_id:
                query_payload["filters"].append({
                    "field": "groupId",
                    "operator": "eq",
                    "value": "0041"
                })
                
            # Determine if a status was requested based on keywords in user message
            msg_lower = message.lower()
            detected_sm = detect_shipment_method(msg_lower)
            has_status_keyword = False
            status_override = None

            def word_in_text(words, text):
                return any(re.search(r'\b' + re.escape(w) + r'\b', text) for w in words)

            if word_in_text(["completed", "finish", "finished", "closed", "inactive"], msg_lower):
                is_inactive_for_trip = True
                has_inactive = re.search(r'\binactive\b', msg_lower) is not None
                if has_inactive:
                    inactive_field_filters = [
                        f for f in query_payload.get("filters", [])
                        if isinstance(f, dict) and f.get("field") in ["FixedGPS", "PortableELock", "FixedELock"] and f.get("value", "").lower() in {"inactive", "nogps", "gpsinactive", "na"}
                    ]
                    if inactive_field_filters:
                        explicit_inactive_trip = (
                            (re.search(r'\binactive\s+trips?\b', msg_lower) is not None and not re.search(r'\b(?:gps|lock|elock|e-lock|portable|fixed|device)\s+inactive\s+trips?\b', msg_lower)) or
                            re.search(r'\btrips?\s+(?:status\s+)?is\s+inactive\b', msg_lower) is not None or
                            re.search(r'\btrips?\s+are\s+inactive\b', msg_lower) is not None
                        )
                        if not explicit_inactive_trip:
                            inactive_count = len(re.findall(r'\binactive\b', msg_lower))
                            if inactive_count <= len(inactive_field_filters):
                                is_inactive_for_trip = False
                
                if is_inactive_for_trip:
                    has_status_keyword = True
                    status_override = "closed"
            elif word_in_text(["cancelled", "canceled"], msg_lower):
                has_status_keyword = True
                status_override = "cancelled"
            elif detected_sm:
                # Queries mentioning any shipment method ("AIR_Network", "SFC_Network", "Speed_Truck",
                # "Feeder", "Empty", "Delivery", "Customer Delivery", "Surface", "Network", etc.)
                # default to tripStatus="running" unless an explicit closed/cancelled status was requested.
                has_status_keyword = True
                status_override = "running"
            elif word_in_text(["running", "scheduled", "sceduled", "transit", "ongoing", "moving", "intransit", "live"], msg_lower) or re.search(r'\b(?:in[\s-]transit|en[\s-]route|on\s+the\s+way)\b', msg_lower):
                has_status_keyword = True
                status_override = "running"
            elif (
                word_in_text(["valid"], msg_lower)
                or re.search(r'\bdevice\s+(?:report|data)\b', msg_lower)
                or re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower)
                or (re.search(r'\btransporters?\b', msg_lower) and word_in_text(["used"], msg_lower))
            ):
                has_status_keyword = True
                status_override = "valid"
            elif word_in_text(["active", "open"], msg_lower):
                # If "active" is present, check if it was only extracted for gps/lock filters
                is_active_for_trip = True
                has_active = re.search(r'\bactive\b', msg_lower) is not None
                has_open = re.search(r'\bopen\b', msg_lower) is not None
                if has_active and not has_open:
                    active_field_filters = [
                        f for f in query_payload.get("filters", [])
                        if isinstance(f, dict) and f.get("field") in ["FixedGPS", "PortableELock", "FixedELock"] and f.get("value") in ["Active", "active"]
                    ]
                    if active_field_filters:
                        # Check if "active" explicitly describes trips/trip
                        explicit_active_trip = (
                            (re.search(r'\bactive\s+trips?\b', msg_lower) is not None and not re.search(r'\b(?:gps|lock|elock|e-lock|portable|fixed|device)\s+active\s+trips?\b', msg_lower)) or
                            re.search(r'\btrips?\s+(?:status\s+)?is\s+active\b', msg_lower) is not None or
                            re.search(r'\btrips?\s+are\s+active\b', msg_lower) is not None
                        )
                        if not explicit_active_trip:
                            active_count = len(re.findall(r'\bactive\b', msg_lower))
                            if active_count <= len(active_field_filters):
                                is_active_for_trip = False
                
                if is_active_for_trip:
                    has_status_keyword = True
                    status_override = "active"

            if has_status_keyword:
                # Normalize any existing tripStatus filters first
                has_trip_status = False
                for f in query_payload["filters"]:
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        has_trip_status = True
                        f["value"] = status_override
                        f["operator"] = "eq"
                        break

                if not has_trip_status:
                    query_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": status_override
                    })
            else:
                # No explicit status keyword in the query — remove any tripStatus filter
                # the LLM may have hallucinated. Never inject a default tripStatus.
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                ]

            # --- ShipmentMethod Injection ---
            if detected_sm:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                query_payload["filters"].append({
                    "field": "shipmentMethod",
                    "operator": "eq",
                    "value": detected_sm
                })

            # --- Vendor Post-Processing ---
            target_vendor = None
            dev_vendor_match = None

            if re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower):
                target_vendor = "ThirdParty"
            else:
                dev_vendor_match = re.search(r'\b([A-Za-z0-9_-]+)\s+device\s+(?:report|data)\b', msg_lower)
                if dev_vendor_match:
                    candidate = dev_vendor_match.group(1)
                    if candidate.lower() not in ["the", "a", "an", "generate", "download", "get", "show", "fetch", "all", "party"]:
                        target_vendor = candidate
                    else:
                        dev_vendor_match = None

                if not target_vendor and re.search(r'\bdevice\s+(?:report|data)\b', msg_lower):
                    v_match = re.search(r'\b(wheelseye|secutrak|icici)\b', msg_lower)
                    if v_match:
                        target_vendor = v_match.group(1)

            if target_vendor:
                if target_vendor.lower() == "wheelseye":
                    canonical_vendor = "Wheelseye"
                elif target_vendor.lower() in ["secutrak", "icici"]:
                    canonical_vendor = "Secutrak"
                elif target_vendor == "ThirdParty" or target_vendor.lower() in ["3rdparty", "thirdparty", "3rd-party"]:
                    canonical_vendor = "ThirdParty"
                else:
                    canonical_vendor = target_vendor.capitalize()

                op = "eq" if canonical_vendor == "ThirdParty" else ("contains" if dev_vendor_match or canonical_vendor == "Wheelseye" else "eq")

                if canonical_vendor == "ThirdParty":
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "portableelock")
                    ]

                has_vendor = False
                for f in query_payload["filters"]:
                    if isinstance(f, dict) and f.get("field") == "vendor":
                        has_vendor = True
                        f["value"] = canonical_vendor
                        f["operator"] = op
                        break
                if not has_vendor:
                    query_payload["filters"].append({
                        "field": "vendor",
                        "operator": op,
                        "value": canonical_vendor
                    })

            # --- Single Device & Combo Post-Processing ---
            has_fixed_elock = bool(re.search(r'\bfixed[\s-]e[\s-]?lock\b|\bfixed[\s-]lock\b', msg_lower))
            has_portable_elock = bool(re.search(r'\bportable[\s-]e[\s-]?lock\b|\bportable[\s-]lock\b', msg_lower))
            _is_devices_together = bool(re.search(r'\btogether\b', msg_lower))
            _is_device_trips = bool(re.search(r'\b(?:device\s+trips?|device\s+data|device\s+report)\b', msg_lower))
            _has_portable_status = bool(
                re.search(r'\bportable[\s-]e?[\s-]?lock\s+(?:is\s+)?(?:active|inactive|nogps|na)\b', msg_lower)
                or re.search(r'\b(?:active|inactive|nogps|na)\s+portable[\s-]e?[\s-]?lock\b', msg_lower)
                or re.search(r'\bportable[\s-]e?[\s-]?lock\s+trips?\s+where\b', msg_lower)
                or re.search(r'\bwhere\s+.*portable[\s-]e?[\s-]?lock\s+is\b', msg_lower)
            )
            _has_fixed_status = bool(
                re.search(r'\bfixed[\s-]e?[\s-]?lock\s+(?:is\s+)?(?:active|inactive|nogps|na)\b', msg_lower)
                or re.search(r'\b(?:active|inactive|nogps|na)\s+fixed[\s-]e?[\s-]?lock\b', msg_lower)
                or re.search(r'\bfixed[\s-]e?[\s-]?lock\s+trips?\s+where\b', msg_lower)
                or re.search(r'\bwhere\s+.*fixed[\s-]e?[\s-]?lock\s+is\b', msg_lower)
            )

            # 1. Fixed GPS Only / Single Device
            _is_fixed_gps_only = bool(
                re.search(r'\bfixed[\s-]gps\b', msg_lower)
                and re.search(r'\b(single|only|sole|alone|exclusive)\b', msg_lower)
            ) or bool(
                re.search(r'\bonly\s+fixed[\s-]gps\b', msg_lower)
            ) or bool(
                re.search(r'\bfixed[\s-]gps\s+(as\s+)?(the\s+)?(single|only|sole)\b', msg_lower)
            )

            # 2. Portable E-Lock Single Device (e.g. "portable E-Lock trips", "portable E-Lock only")
            _is_portable_elock_only = (
                has_portable_elock
                and not has_fixed_elock
                and not _is_devices_together
                and not _is_device_trips
                and not _has_portable_status
            )

            # 3. Fixed E-Lock Single Device (e.g. "Fixed E-Lock trips", "Fixed E-Lock only")
            _is_fixed_elock_only = (
                has_fixed_elock
                and not has_portable_elock
                and not _is_devices_together
                and not _is_device_trips
                and not _has_fixed_status
            )

            if _is_fixed_gps_only:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["filters"].append({"field": "GPS",          "operator": "eq", "value": "existing"})
                query_payload["filters"].append({"field": "FixedELock",   "operator": "eq", "value": "na"})
                query_payload["filters"].append({"field": "PortableELock","operator": "eq", "value": "na"})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if not has_status_keyword:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
            elif _is_portable_elock_only:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["filters"].append({"field": "GPS",          "operator": "eq", "value": "na"})
                query_payload["filters"].append({"field": "FixedELock",   "operator": "eq", "value": "na"})
                query_payload["filters"].append({"field": "PortableELock","operator": "eq", "value": "existing"})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if not has_status_keyword:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
            elif _is_fixed_elock_only:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["filters"].append({"field": "GPS",          "operator": "eq", "value": "na"})
                query_payload["filters"].append({"field": "FixedELock",   "operator": "eq", "value": "existing"})
                query_payload["filters"].append({"field": "PortableELock","operator": "eq", "value": "na"})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if not has_status_keyword:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
            elif _is_devices_together and (bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower)) or has_fixed_elock or has_portable_elock):
                _has_together_gps = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower))
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if not has_status_keyword:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]

                if _has_together_gps:
                    query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "existing"})
                if has_fixed_elock:
                    query_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "existing"})
                if has_portable_elock:
                    query_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "existing"})

                def _filter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "runDate" and op == "gte":
                        return 1
                    elif field == "runDate" and op == "lte":
                        return 2
                    elif field == "groupId":
                        return 3
                    elif field == "GPS":
                        return 4
                    elif field == "FixedELock":
                        return 5
                    elif field == "PortableELock":
                        return 6
                    return 10

                query_payload["filters"].sort(key=_filter_sort_key)
            elif has_fixed_elock and has_portable_elock:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["FixedGPS", "FixedELock", "PortableELock", "fixed-gps status", "gps", "fixedelock", "portableelock"])
                ]
                group_by_fields: list = []
                query_payload["filters"].append(
                    {"field": "fixed-e-lock status", "operator": "eq", "value": "existing"}
                )
                group_by_fields.append("fixed-e-lock status")
                query_payload["filters"].append(
                    {"field": "portable-e-lock status", "operator": "eq", "value": "existing"}
                )
                group_by_fields.append("portable-e-lock status")

                query_payload["groupBy"] = group_by_fields
                query_payload["metrics"] = [
                    {"field": "shipmentNo", "function": "count", "alias": "trip_count"}
                ]

            # --- RouteCategory Post-Processing ---
            # Also handle tripType as a synonym for routeCategory.
            route_category_value: str | None = None
            if re.search(r'\bintracity\b', msg_lower):
                route_category_value = "intracity"
            elif re.search(r'\bintercity\b', msg_lower):
                route_category_value = "intercity"

            if route_category_value:
                # Remove any routeType, routeCategory, or tripType filter the LLM may have generated
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType"])
                ]
                query_payload["filters"].append({
                    "field": "routeCategory",
                    "operator": "eq",
                    "value": route_category_value
                })

            # --- closeRemarks Post-Processing ---
            _VALID_REMARKS = {"manual", "forcefully", "geofence", "api", "backend", "web"}
            remark_match = re.search(
                r'\b(manual|forcefully|geofence|api|backend|web)\b(?:\s+remark)?',
                msg_lower
            )
            if not remark_match:
                remark_match = re.search(
                    r'\bremark[s]?\s+(manual|forcefully|geofence|api|backend|web)\b',
                    msg_lower
                )
            if remark_match:
                remark_val = remark_match.group(1)
                # Remove any existing closeRemarks filter from LLM output first
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "closeRemarks")
                ]
                query_payload["filters"].append({
                    "field": "closeRemarks",
                    "operator": "eq",
                    "value": remark_val
                })

            # --- ATD / ATA Post-Processing ---
            # Normalize ATD/ATA field values to canonical forms
            _ATD_ATA_VALUE_MAP = {
                "notcaptured": "notcaptured",
                "not captured": "notcaptured",
                "captured": "Captured",
                "missing": "missing",
                "notmissing": "NotMissing",
                "not missing": "NotMissing",
            }
            for f in query_payload["filters"]:
                if isinstance(f, dict) and f.get("field") in ["ATD", "ATA"]:
                    raw_val = str(f.get("value", "")).lower().strip()
                    if raw_val in _ATD_ATA_VALUE_MAP:
                        f["value"] = _ATD_ATA_VALUE_MAP[raw_val]

            # --- ShipmentMethod Post-Processing ---
            # Full list of recognized shipmentMethod values.
            if detected_sm:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                query_payload["filters"].append({
                    "field": "shipmentMethod",
                    "operator": "eq",
                    "value": detected_sm
                })

            # --- Transporter & Vehicle Metrics Post-Processing ---
            has_vehicle_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            has_transporter_mention = bool(re.search(r'\btransporters?\b', msg_lower))
            has_count_unique_distinct = bool(re.search(r'\b(?:unique|distinct|total\s+number|count)\b', msg_lower))

            if ("groupBy" not in query_payload or not query_payload["groupBy"]) and has_transporter_mention and has_count_unique_distinct:
                if has_vehicle_mention:
                    # Query asks for unique vehicles per transporter / breakdown
                    query_payload["groupBy"] = ["Transporter"]
                    query_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                    ]
                else:
                    # Query asks ONLY for total number of unique/distinct transporters
                    query_payload.pop("groupBy", None)
                    query_payload["metrics"] = [
                        {"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}
                    ]

            if _is_fixed_gps_only or _is_portable_elock_only or _is_fixed_elock_only:
                def _single_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "runDate" and op == "gte":
                        return 2
                    elif field == "runDate" and op == "lte":
                        return 3
                    elif field == "GPS":
                        return 4
                    elif field == "FixedELock":
                        return 5
                    elif field == "PortableELock":
                        return 6
                    elif field == "routeCategory":
                        return 7
                    return 10

                query_payload["filters"].sort(key=_single_device_sort_key)

            log_intent_detected(session_id, "trip_report")
            log_entities_extracted(session_id, query_payload)

            return {
                "intent": "trip_report",
                "entities": query_payload
            }
            
        except LLMRateLimitError as rle:
            raise rle
        except Exception as exc:
            log_error(session_id, "INTENT_DETECTION_FAILED", str(exc))
            # Fallback structure
            fallback_payload = {
                "entity": "trip",
                "operation": "count",
                "filters": [
                    {
                        "field": "groupId",
                        "operator": "eq",
                        "value": "0041"
                    }
                ],
                "select": []
            }
            if has_status_keyword and status_override:
                fallback_payload["filters"].append({
                    "field": "tripStatus",
                    "operator": "eq",
                    "value": status_override
                })
            else:
                # Basic keyword check for fallback — only inject tripStatus when
                # the user explicitly mentioned a status term or a shipmentMethod. Never default otherwise.
                fb_detected_sm = detect_shipment_method(msg_lower)
                if any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["completed", "finish", "finished", "closed", "inactive"]):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "closed"
                    })
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["cancelled", "canceled"]):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "cancelled"
                    })
                elif fb_detected_sm or any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["running", "scheduled", "sceduled", "active", "open", "transit", "ongoing", "moving", "intransit", "live"]) or re.search(r'\b(?:in[\s-]transit|en[\s-]route|on\s+the\s+way)\b', msg_lower):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "running"
                    })
                # else: no status keyword → do NOT inject a default tripStatus
            if extracted_start and extracted_end:
                fallback_payload["filters"].append({
                    "field": "runDate",
                    "operator": "gte",
                    "value": extracted_start
                })
                fallback_payload["filters"].append({
                    "field": "runDate",
                    "operator": "lte",
                    "value": extracted_end
                })
            else:
                if re.search(r'\btoday\b', msg_lower):
                    fallback_payload["filters"].append({"field": "runDate", "operator": "gte", "value": today_start})
                    fallback_payload["filters"].append({"field": "runDate", "operator": "lte", "value": today_end})
                elif re.search(r'\byesterday\b', msg_lower):
                    fallback_payload["filters"].append({"field": "runDate", "operator": "gte", "value": yesterday_start})
                    fallback_payload["filters"].append({"field": "runDate", "operator": "lte", "value": yesterday_end})
                elif re.search(r'\blast\s+week\b', msg_lower):
                    fallback_payload["filters"].append({"field": "runDate", "operator": "gte", "value": last_week_start})
                    fallback_payload["filters"].append({"field": "runDate", "operator": "lte", "value": last_week_end})
                elif re.search(r'\blast\s+month\b', msg_lower):
                    fallback_payload["filters"].append({"field": "runDate", "operator": "gte", "value": last_month_start})
                    fallback_payload["filters"].append({"field": "runDate", "operator": "lte", "value": last_month_end})
                else:
                    # Resolve named-month fallback (e.g. "of august", "for august", "aug 2026")
                    fb_m_start, fb_m_end = _resolve_month_range(msg_lower, now)
                    if fb_m_start and fb_m_end:
                        fallback_payload["filters"].append(
                            {"field": "runDate", "operator": "gte", "value": fb_m_start}
                        )
                        fallback_payload["filters"].append(
                            {"field": "runDate", "operator": "lte", "value": fb_m_end}
                        )

            # Apply the same single-device and device-combo post-processing to fallback
            fb_has_fixed_elock = bool(re.search(r'\bfixed[\s-]e[\s-]?lock\b|\bfixed[\s-]lock\b', msg_lower))
            fb_has_portable_elock = bool(re.search(r'\bportable[\s-]e[\s-]?lock\b|\bportable[\s-]lock\b', msg_lower))
            fb_is_devices_together = bool(re.search(r'\btogether\b', msg_lower))
            fb_is_device_trips = bool(re.search(r'\b(?:device\s+trips?|device\s+data|device\s+report)\b', msg_lower))
            fb_has_portable_status = bool(
                re.search(r'\bportable[\s-]e?[\s-]?lock\s+(?:is\s+)?(?:active|inactive|nogps|na)\b', msg_lower)
                or re.search(r'\b(?:active|inactive|nogps|na)\s+portable[\s-]e?[\s-]?lock\b', msg_lower)
                or re.search(r'\bportable[\s-]e?[\s-]?lock\s+trips?\s+where\b', msg_lower)
                or re.search(r'\bwhere\s+.*portable[\s-]e?[\s-]?lock\s+is\b', msg_lower)
            )
            fb_has_fixed_status = bool(
                re.search(r'\bfixed[\s-]e?[\s-]?lock\s+(?:is\s+)?(?:active|inactive|nogps|na)\b', msg_lower)
                or re.search(r'\b(?:active|inactive|nogps|na)\s+fixed[\s-]e?[\s-]?lock\b', msg_lower)
                or re.search(r'\bfixed[\s-]e?[\s-]?lock\s+trips?\s+where\b', msg_lower)
                or re.search(r'\bwhere\s+.*fixed[\s-]e?[\s-]?lock\s+is\b', msg_lower)
            )

            fb_is_fixed_gps_only = bool(
                re.search(r'\bfixed[\s-]gps\b', msg_lower)
                and re.search(r'\b(single|only|sole|alone|exclusive)\b', msg_lower)
            ) or bool(
                re.search(r'\bonly\s+fixed[\s-]gps\b', msg_lower)
            ) or bool(
                re.search(r'\bfixed[\s-]gps\s+(as\s+)?(the\s+)?(single|only|sole)\b', msg_lower)
            )

            fb_is_portable_elock_only = (
                fb_has_portable_elock
                and not fb_has_fixed_elock
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_has_portable_status
            )

            fb_is_fixed_elock_only = (
                fb_has_fixed_elock
                and not fb_has_portable_elock
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_has_fixed_status
            )

            if fb_is_fixed_gps_only:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["filters"].append({"field": "GPS",          "operator": "eq", "value": "existing"})
                fallback_payload["filters"].append({"field": "FixedELock",   "operator": "eq", "value": "na"})
                fallback_payload["filters"].append({"field": "PortableELock","operator": "eq", "value": "na"})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if not has_status_keyword:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
            elif fb_is_portable_elock_only:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["filters"].append({"field": "GPS",          "operator": "eq", "value": "na"})
                fallback_payload["filters"].append({"field": "FixedELock",   "operator": "eq", "value": "na"})
                fallback_payload["filters"].append({"field": "PortableELock","operator": "eq", "value": "existing"})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if not has_status_keyword:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
            elif fb_is_fixed_elock_only:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["filters"].append({"field": "GPS",          "operator": "eq", "value": "na"})
                fallback_payload["filters"].append({"field": "FixedELock",   "operator": "eq", "value": "existing"})
                fallback_payload["filters"].append({"field": "PortableELock","operator": "eq", "value": "na"})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if not has_status_keyword:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
            elif fb_is_devices_together and (bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower)) or fb_has_fixed_elock or fb_has_portable_elock):
                fb_has_together_gps = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower))
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if not has_status_keyword:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]

                if fb_has_together_gps:
                    fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "existing"})
                if fb_has_fixed_elock:
                    fallback_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "existing"})
                if fb_has_portable_elock:
                    fallback_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "existing"})

                def _fb_filter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "runDate" and op == "gte":
                        return 1
                    elif field == "runDate" and op == "lte":
                        return 2
                    elif field == "groupId":
                        return 3
                    elif field == "GPS":
                        return 4
                    elif field == "FixedELock":
                        return 5
                    elif field == "PortableELock":
                        return 6
                    return 10

                fallback_payload["filters"].sort(key=_fb_filter_sort_key)
            elif fb_has_fixed_elock and fb_has_portable_elock:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["FixedGPS", "FixedELock", "PortableELock", "fixed-gps status", "gps", "fixedelock", "portableelock"])
                ]
                fb_group_by: list = []
                fallback_payload["filters"].append(
                    {"field": "fixed-e-lock status", "operator": "eq", "value": "existing"}
                )
                fb_group_by.append("fixed-e-lock status")
                fallback_payload["filters"].append(
                    {"field": "portable-e-lock status", "operator": "eq", "value": "existing"}
                )
                fb_group_by.append("portable-e-lock status")
                fallback_payload["groupBy"] = fb_group_by
                fallback_payload["metrics"] = [
                    {"field": "shipmentNo", "function": "count", "alias": "trip_count"}
                ]

            # Apply the same routeCategory post-processing as the main path
            fb_route_category: str | None = None
            if re.search(r'\bintracity\b', msg_lower):
                fb_route_category = "intracity"
            elif re.search(r'\bintercity\b', msg_lower):
                fb_route_category = "intercity"
            if fb_route_category:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType"])
                ]
                fallback_payload["filters"].append(
                    {"field": "routeCategory", "operator": "eq", "value": fb_route_category}
                )

            fb_sm_val = detect_shipment_method(msg_lower)
            if fb_sm_val:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                fallback_payload["filters"].append(
                    {"field": "shipmentMethod", "operator": "eq", "value": fb_sm_val}
                )
                has_fb_trip_status = any(
                    isinstance(f, dict) and f.get("field") == "tripStatus"
                    for f in fallback_payload["filters"]
                )
                if not has_fb_trip_status:
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "running"
                    })

            # Apply Transporter / Unique Vehicle post-processing to fallback
            fb_has_vehicle_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            fb_has_transporter_mention = bool(re.search(r'\btransporters?\b', msg_lower))
            fb_has_count_unique_distinct = bool(re.search(r'\b(?:unique|distinct|total\s+number|count)\b', msg_lower))

            if ("groupBy" not in fallback_payload or not fallback_payload["groupBy"]) and fb_has_transporter_mention and fb_has_count_unique_distinct:
                if fb_has_vehicle_mention:
                    fallback_payload["groupBy"] = ["Transporter"]
                    fallback_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                    ]
                else:
                    fallback_payload.pop("groupBy", None)
                    fallback_payload["metrics"] = [
                        {"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}
                    ]

            if fb_is_fixed_gps_only or fb_is_portable_elock_only or fb_is_fixed_elock_only:
                def _fb_single_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "runDate" and op == "gte":
                        return 2
                    elif field == "runDate" and op == "lte":
                        return 3
                    elif field == "GPS":
                        return 4
                    elif field == "FixedELock":
                        return 5
                    elif field == "PortableELock":
                        return 6
                    elif field == "routeCategory":
                        return 7
                    return 10

                fallback_payload["filters"].sort(key=_fb_single_device_sort_key)

            return {
                "intent": "trip_report",
                "entities": fallback_payload
            }
