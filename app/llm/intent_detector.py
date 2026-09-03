import json
import datetime
import re
from typing import List, Dict, Any
from app.llm.client import LLMClient, LLMRateLimitError
from app.llm.prompts import INTENT_EXTRACTION_SYSTEM_PROMPT, INTENT_EXTRACTION_USER_PROMPT_TEMPLATE
from app.utils.logger import log_intent_detected, log_entities_extracted, log_error
from app.config import settings

class IntentDetectorError(Exception):
    """Exception raised when intent detection fails."""
    pass

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
        # Check if this is a follow-up answer to a date range request
        is_follow_up = False
        prev_user_msg = None
        if history and len(history) >= 2:
            last_assistant_msg = history[-1]
            if last_assistant_msg.get("role") == "assistant" and "date range" in last_assistant_msg.get("content", "").lower():
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
            
            if "operation" not in query_payload:
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
                    status_override = "InActive"
            elif word_in_text(["cancelled", "canceled"], msg_lower):
                has_status_keyword = True
                status_override = "cancelled"
            elif word_in_text(["running", "scheduled", "sceduled"], msg_lower):
                has_status_keyword = True
                status_override = "running"
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
                # Basic keyword check for fallback
                if any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["completed", "finish", "finished", "closed", "inactive"]):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "InActive"
                    })
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["cancelled", "canceled"]):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "cancelled"
                    })
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["running", "scheduled", "sceduled"]):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "running"
                    })
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["active", "open"]):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "active"
                    })
                else:
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": settings.DEFAULT_TRIP_STATUS
                    })
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
            return {
                "intent": "trip_report",
                "entities": fallback_payload
            }
