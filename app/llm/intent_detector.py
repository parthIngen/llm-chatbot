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
                        
            # Normalize fixedelock and portableelock values to empty string unless active/inactive status is requested
            for f in query_payload["filters"]:
                if isinstance(f, dict) and f.get("field") in ["fixedelock", "portableelock"]:
                    val = f.get("value")
                    if isinstance(val, str) and val.lower() not in ["active", "inactive"]:
                        f["value"] = ""

            # If fixedelock or portableelock is inactive, remove the redundant/duplicate gps inactive filter
            has_inactive_elock = any(
                isinstance(f, dict) and f.get("field") in ["fixedelock", "portableelock"] and f.get("value") == "inactive"
                for f in query_payload["filters"]
            )
            if has_inactive_elock:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "gps" and f.get("value") == "inactive")
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
                        if isinstance(f, dict) and f.get("field") in ["gps", "portableelock", "fixedelock"] and f.get("value") == "inactive"
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
            elif word_in_text(["running"], msg_lower):
                has_status_keyword = True
                status_override = "running"
            elif word_in_text(["scheduled", "sceduled", "transit", "ongoing"], msg_lower) or re.search(r'\b(?:in[\s-]transit|en[\s-]route|on\s+the\s+way)\b', msg_lower):
                has_status_keyword = True
                status_override = "running"
            elif word_in_text(["valid", "used"], msg_lower) or re.search(r'\bdevice\s+(?:report|data)\b', msg_lower) or re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower):
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
                        if isinstance(f, dict) and f.get("field") in ["gps", "portableelock", "fixedelock"] and f.get("value") == "active"
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

            # --- Vendor Post-Processing ---
            target_vendor = None

            if re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower):
                target_vendor = "ThirdParty"
            else:
                dev_vendor_match = re.search(r'\b([A-Za-z0-9_-]+)\s+device\s+(?:report|data)\b', msg_lower)
                if dev_vendor_match:
                    candidate = dev_vendor_match.group(1)
                    if candidate.lower() not in ["the", "a", "an", "generate", "download", "get", "show", "fetch", "all", "party"]:
                        target_vendor = candidate

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

            # --- Device Combo Post-Processing ---
            # Detect explicit E-Lock device-type mentions and rewrite to the canonical
            # field names expected by the external API, adding groupBy + metrics.
            # NOTE: "Fixed GPS" / GPS device mentions do NOT create a separate filter field.
            #       Only fixed-e-lock status and portable-e-lock status are real API fields.
            has_fixed_elock    = bool(re.search(r'\bfixed[\s-]e[\s-]?lock\b', msg_lower))
            has_portable_elock = bool(re.search(r'\bportable[\s-]e[\s-]?lock\b', msg_lower))

            if has_fixed_elock or has_portable_elock:
                # Remove legacy-style device filters (gps, fixedelock, portableelock)
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["gps", "fixedelock", "portableelock", "fixed-gps status"])
                ]

                group_by_fields: list = []

                if has_fixed_elock:
                    query_payload["filters"].append(
                        {"field": "fixed-e-lock status", "operator": "eq", "value": "existing"}
                    )
                    group_by_fields.append("fixed-e-lock status")

                if has_portable_elock:
                    query_payload["filters"].append(
                        {"field": "portable-e-lock status", "operator": "eq", "value": "existing"}
                    )
                    group_by_fields.append("portable-e-lock status")

                query_payload["groupBy"] = group_by_fields
                query_payload["metrics"] = [
                    {"field": "shipmentNo", "function": "count", "alias": "trip_count"}
                ]

            # --- RouteCategory Post-Processing ---
            # Detect "intracity" or "intercity" keywords and inject a routeCategory filter.
            # Server-side detection ensures the LLM can't hallucinate or miss this field.
            route_category_value: str | None = None
            if re.search(r'\bintracity\b', msg_lower):
                route_category_value = "intracity"
            elif re.search(r'\bintercity\b', msg_lower):
                route_category_value = "intercity"

            if route_category_value:
                # Remove any routeType or routeCategory filter the LLM may have generated (to avoid duplicates)
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory"])
                ]
                query_payload["filters"].append({
                    "field": "routeCategory",
                    "operator": "eq",
                    "value": route_category_value
                })

            # --- ShipmentMethod Post-Processing ---
            sm_val: str | None = None
            if re.search(r'\b(?:pick[\s-]?up|pickup)\b', msg_lower):
                sm_val = "Pick Up"
            else:
                sm_match = re.search(r'\b(feeder|air|surface|rail|express|sea)\b', msg_lower)
                if sm_match:
                    sm_val = sm_match.group(1)

            if sm_val:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                query_payload["filters"].append({
                    "field": "shipmentMethod",
                    "operator": "eq",
                    "value": sm_val
                })

            # --- Transporter & Vehicle Metrics Post-Processing ---
            has_vehicle_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            has_transporter_mention = bool(re.search(r'\btransporters?\b', msg_lower))
            has_count_unique_distinct = bool(re.search(r'\b(?:unique|distinct|total\s+number|count)\b', msg_lower))

            if has_transporter_mention and has_count_unique_distinct:
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
            elif has_vehicle_mention and has_count_unique_distinct:
                query_payload["groupBy"] = ["Transporter"]
                query_payload["metrics"] = [
                    {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                ]

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
                # the user explicitly mentioned a status term. Never default.
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
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["running", "scheduled", "sceduled", "active", "open", "transit", "ongoing"]) or re.search(r'\b(?:in[\s-]transit|en[\s-]route|on\s+the\s+way)\b', msg_lower):
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

            # Apply the same device-combo post-processing as the main path
            fb_has_fixed_elock    = bool(re.search(r'\bfixed[\s-]e[\s-]?lock\b', msg_lower))
            fb_has_portable_elock = bool(re.search(r'\bportable[\s-]e[\s-]?lock\b', msg_lower))

            if fb_has_fixed_elock or fb_has_portable_elock:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["gps", "fixedelock", "portableelock", "fixed-gps status"])
                ]
                fb_group_by: list = []
                if fb_has_fixed_elock:
                    fallback_payload["filters"].append(
                        {"field": "fixed-e-lock status", "operator": "eq", "value": "existing"}
                    )
                    fb_group_by.append("fixed-e-lock status")
                if fb_has_portable_elock:
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
                    if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory"])
                ]
                fallback_payload["filters"].append(
                    {"field": "routeCategory", "operator": "eq", "value": fb_route_category}
                )

            fb_sm_val: str | None = None
            if re.search(r'\b(?:pick[\s-]?up|pickup)\b', msg_lower):
                fb_sm_val = "Pick Up"
            else:
                fb_sm_match = re.search(r'\b(feeder|air|surface|rail|express|sea)\b', msg_lower)
                if fb_sm_match:
                    fb_sm_val = fb_sm_match.group(1)

            if fb_sm_val:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                fallback_payload["filters"].append(
                    {"field": "shipmentMethod", "operator": "eq", "value": fb_sm_val}
                )

            # Apply Transporter / Unique Vehicle post-processing to fallback
            fb_has_vehicle_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            fb_has_transporter_mention = bool(re.search(r'\btransporters?\b', msg_lower))
            fb_has_count_unique_distinct = bool(re.search(r'\b(?:unique|distinct|total\s+number|count)\b', msg_lower))

            if fb_has_transporter_mention and fb_has_count_unique_distinct:
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
            elif fb_has_vehicle_mention and fb_has_count_unique_distinct:
                fallback_payload["groupBy"] = ["Transporter"]
                fallback_payload["metrics"] = [
                    {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                ]

            return {
                "intent": "trip_report",
                "entities": fallback_payload
            }
