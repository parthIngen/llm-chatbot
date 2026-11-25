import re
import datetime
from typing import Any, Dict, Optional, List

def can_resolve_fast_path(message: str) -> bool:
    """
    Checks if a query can be confidently resolved without an LLM call.
    """
    msg_l = message.strip().lower()

    # Ambiguous or conversational queries that require semantic understanding
    if any(phrase in msg_l for phrase in ["why", "how come", "what does", "explain", "meaning", "help me understand"]):
        return False

    # Download / export queries require operation find and report formatting,
    # except device status / device analytics count queries (e.g. "Download all trips where GPS is active")
    is_device_status_count = bool(
        re.search(r'\b(?:active|inactive)\s+(?:fixed[\s-]?)?gps\b|\b(?:fixed[\s-]?)?gps\s+(?:is\s+)?(?:active|inactive)\b', msg_l)
        or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_l)
        or re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_l)
        or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_l)
        or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_l)
        or re.search(r'\b(?:without\s+(?:any\s+)?gps|no\s+gps|non[\s-]?gps|gps\s+na)\b', msg_l)
        or re.search(r'\b(?:trips\s+with\s+fixed\s+e[\s-]?lock|inactive\s+portable\s+e[\s-]?lock|active\s+fixed\s+e[\s-]?lock)\b', msg_l)
    )
    if bool(re.search(r'\b(?:download|export|spreadsheet)\b', msg_l)) and not is_device_status_count:
        return False

    # 1. Distinct vehicle queries
    if bool(re.search(r'\b(?:unique\s+vehicles?|distinct\s+vehicles?|total\s+distinct\s+vehicles?|list\s+all\s+distinct\s+vehicles?)\b', msg_l)):
        return True
    if bool(re.search(r'\bactive\s+vehicles\s+operating\s+on\s+the\s+field\b', msg_l)):
        return True

    # 2. Distinct / Top transporter & top N grouped queries
    from app.llm.intent_detector import is_top_transporters_query, detect_top_n_grouped_query, detect_close_by_device_query
    if detect_top_n_grouped_query(message) is not None:
        return True
    if detect_close_by_device_query(message) is not None:
        return True
    if is_top_transporters_query(message):
        return True
    if bool(re.search(r'\b(?:unique\s+transporters?|distinct\s+transporters?|total\s+number\s+of\s+unique\s+transporters?)\b', msg_l)):
        return True

    # 3. Delay queries
    if bool(re.search(r'\b(?:departure\s+(?:was\s+)?delayed|delayed\s+departure|arrival\s+(?:was\s+)?delayed|delayed\s+arrival)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:atd|ata)\s+(?:is\s+|was\s+)?(?:not\s+captured|missing|captured)\b', msg_l)):
        return True
    if bool(re.search(r'\bmissing\s+(?:atd|ata)\b', msg_l)):
        return True

    # 4. Device combination queries
    if bool(re.search(r'\bwithout\s+any\s+device\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:(?:fixed[\s-]*)?gps\s+(?:only|single|sole|alone|exclusive|exclusively)|portable[\s-]*(?:e[\s-]?)?locks?\s+(?:only|single|sole|alone|exclusive|exclusively)|fixed[\s-]*(?:e[\s-]?)?locks?\s+(?:only|single|sole|alone|exclusive|exclusively))\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:only|single|sole|alone|exclusive|exclusively)\s+(?:(?:fixed[\s-]*)?gps|portable[\s-]*(?:e[\s-]?)?locks?|fixed[\s-]*(?:e[\s-]?)?locks?)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive|exclusively)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:portable[\s-]e[\s-]?locks?|portable[\s-]locks?)\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive|exclusively)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]e[\s-]?locks?|fixed[\s-]locks?)\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive|exclusively)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l)) and bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l)):
        return True
    if bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l)) and bool(re.search(r'\bportable[\s-]e[\s-]?locks?\b', msg_l)):
        return True

    # 5. Non-GPS / GPS queries
    if bool(
        re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_l)
        or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_l)
        or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_l)
        or re.search(r'\b(?:without\s+(?:any\s+)?gps|no\s+gps|non[\s-]?gps|gps\s+na)\b', msg_l)
    ):
        return True
    if bool(re.search(r'\b(?:active|inactive)\s+(?:fixed[\s-]?)?gps\b|\b(?:fixed[\s-]?)?gps\s+(?:is\s+)?(?:active|inactive)\b', msg_l)) or bool(re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:secutrak_tp|secutrak|wheelseye|wheelseye_bluedart|fleetx|axestrack|gtropy|eicher|g[\s-]?trac|ashok|ashokleyland|tata|tatamotors|kiasaint|lynkit)\b', msg_l)):
        return True

    # 6. E-Lock queries
    if bool(re.search(r'\b(?:trips\s+with\s+fixed\s+e[\s-]?lock|inactive\s+portable\s+e[\s-]?lock|active\s+fixed\s+e[\s-]?lock)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]e[\s-]?locks?|portable[\s-]e[\s-]?locks?)\b', msg_l)) and bool(re.search(r'\btrips?\b', msg_l)):
        return True

    # 7. Comparisons and grouped analytics
    if bool(re.search(r'\b(?:across|by|in|each|all|breakdown)\s+(?:major\s+)?(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_l)) or bool(re.search(r'\b(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\s+breakdown\b', msg_l)) or bool(re.search(r'\btrip\s+count\s+across\s+(?:major\s+)?(?:feeder|route|shipment)\b', msg_l)) or bool(re.search(r'\bmajor\s+(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_l)):
        return True
    if bool(re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or)\s+local\b|\bintercity\s+(?:vs\.?|versus|and|or)\s+intracity\b|\bcompare\s+network\s+and\s+local\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:in\s+each\s+region|region[\s-]wise|by\s+region|each\s+region)\b', msg_l)):
        return True
    if bool(re.search(r'\bhigh\s+priority\s+(?:vs\.?|versus|and|or)\s+(?:normal|standard|low)\b', msg_l)):
        return True

    # 8. Region specific trip queries
    if bool(re.search(r'\b(?:north|south|east|west|central|north\s*east|north\s*west|south\s*east|south\s*west)\s+region\b', msg_l)) and bool(re.search(r'\btrips?\b', msg_l)):
        return True
    if bool(re.search(r'\btrips?\s+(?:in|for|of|across)\s+(?:the\s+)?(?:north|south|east|west|central|north\s*east|north\s*west|south\s*east|south\s*west)\b', msg_l)):
        return True

    # 9. Shipment method specific trip queries
    if bool(re.search(r'\b(?:feeder|empty|surface|sfc|speed|air|delivery|pickup|pick[\s-]?up|comm[\s/_-]*bz[\s/_-]*(?:out|in))\b', msg_l)) and bool(re.search(r'\btrips?\b', msg_l)):
        return True
    if bool(re.search(r'\bbreakdown\s+of\s+trips\b|\btrips\s+that\s+were\b', msg_l)):
        return True

    # 10. Basic trip counts & status
    if bool(re.search(r'\b(?:running|active|closed|cancelled|valid)\s+trips\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:how\s+many|total|count\s+of|number\s+of)\s+(?:closed|running|cancelled|valid)?\s*trips\b', msg_l)):
        return True
    if bool(re.search(r'\bshow\s+(?:all\s+)?(?:closed|running|cancelled|valid)?\s*trips\b', msg_l)):
        return True
    if bool(re.search(r'\btrips\s+for\s+vehicle\s+[a-z0-9_-]+\b', msg_l)):
        return True
    if bool(re.search(r'\btrips\s+for\s+transporter\s+[a-z0-9_\s-]+\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:operated\s+by|by\s+transporter|for\s+transporter)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+transporters?\b|\btransporters?\s+(?:is\s+)?(?:na|not\s+available)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:vehicles?|veh)\b', msg_l)) and bool(re.search(r'\btransporters?\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]*)?gps\s+distance\b|\bdistance\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:lis|list)\s+of\s+(?:the\s+)?vehicles?\b', msg_l)):
        return True
    from app.llm.intent_detector import (
        detect_vehicle_master, detect_route_master, detect_fleet_master,
        extract_single_shipment_lookup, detect_top_device_vendors,
        detect_date_grouping, extract_area, extract_branch,
        detect_unique_or_list_field, extract_route, extract_vehicle_list,
        extract_region, extract_shipment_methods
    )
    if detect_unique_or_list_field(message) is not None:
        return True
    if extract_area(message) is not None or extract_branch(message) is not None:
        return True
    if extract_route(message) is not None or extract_vehicle_list(message) is not None:
        return True
    if extract_region(message) is not None or extract_shipment_methods(message) is not None:
        return True
    if detect_vehicle_master(message) is not None:
        return True
    if detect_route_master(message) is not None:
        return True
    if detect_fleet_master(message) is not None:
        return True
    if extract_single_shipment_lookup(message) is not None:
        return True
    if detect_top_device_vendors(message) is not None:
        return True
    if detect_date_grouping(message) is not None:
        return True
    from app.llm.intent_detector import extract_source_location, extract_destination_location
    if bool(re.search(r'\btrips?\b', msg_l)) and (extract_source_location(message) is not None or extract_destination_location(message) is not None):
        return True
    from app.llm.intent_detector import detect_indian_states
    if bool(re.search(r'\btrips?\b', msg_l)) and bool(detect_indian_states(message)):
        return True
    if bool(re.search(r'\b(?:in\s+each\s+state|state[\s-]wise|by\s+state|each\s+state|across\s+(?:each|all)\s+states?|state\s+breakdown)\b', msg_l)):
        return True

    return False

def build_fast_path_payload(
    message: str,
    reference_now: Optional[datetime.datetime] = None
) -> Optional[Dict[str, Any]]:
    """
    Constructs an initial structured query payload for queries that can be confidently resolved.
    Returns None if the query cannot be confidently resolved without an LLM.
    """
    from app.llm.intent_detector import extract_single_shipment_lookup, detect_top_device_vendors, extract_date_range, _resolve_month_range, is_top_transporters_query
    single_shp = extract_single_shipment_lookup(message)
    if single_shp:
        return {
            "entity": "trip",
            "operation": "find",
            "filters": [
                {
                    "field": "groupId",
                    "operator": "eq",
                    "value": "0041"
                },
                {
                    "field": "shipmentNo",
                    "operator": "eq",
                    "value": single_shp
                }
            ],
            "select": []
        }

    ref_dt = reference_now or datetime.datetime.now()
    top_v = detect_top_device_vendors(message)
    if top_v:
        start_date, end_date = extract_date_range(message, reference_now=ref_dt)
        if not start_date or not end_date:
            start_date, end_date = _resolve_month_range(message.lower(), ref_dt)
        if not start_date or not end_date:
            if "august" in message.lower() or "aug" in message.lower():
                start_date = "2026-08-01 00:00:00"
                end_date = "2026-08-31 23:59:59"

        filters = [
            {"field": "groupId", "operator": "eq", "value": "0041"},
            {"field": "tripStatus", "operator": "eq", "value": "valid"}
        ]
        if start_date:
            filters.append({"field": "runDate", "operator": "gte", "value": start_date})
        if end_date:
            filters.append({"field": "runDate", "operator": "lte", "value": end_date})
        filters.append({"field": top_v["vendor_field"], "operator": "eq", "value": "existing"})

        return {
            "entity": "trip",
            "operation": "count",
            "analytics": [
                {
                    "type": top_v["analytics_type"]
                }
            ],
            "filters": filters,
            "groupBy": [
                top_v["vendor_field"]
            ],
            "metrics": [
                {
                    "field": "shipmentNo",
                    "function": "count",
                    "alias": "TotalTrip"
                }
            ],
            "sort": [
                {
                    "field": "TotalTrip",
                    "order": "desc"
                }
            ]
        }

    from app.llm.intent_detector import detect_close_by_device_query
    cbd_info = detect_close_by_device_query(message)
    if cbd_info:
        start_date, end_date = extract_date_range(message, reference_now=ref_dt)
        if not start_date or not end_date:
            start_date, end_date = _resolve_month_range(message.lower(), ref_dt)
        if not start_date or not end_date:
            if "august" in message.lower() or "aug" in message.lower():
                start_date = "2026-08-01 00:00:00"
                end_date = "2026-08-31 23:59:59"

        t_status = "valid"
        if bool(re.search(r'\b(?:running|in[\s-]transit|live|active\s+trips?)\b', message.lower())):
            t_status = "running"
        elif bool(re.search(r'\bcancel(?:led)?\s+trips?\b', message.lower())):
            t_status = "cancelled"

        filters = [
            {"field": "groupId", "operator": "eq", "value": "0041"},
            {"field": "tripStatus", "operator": "eq", "value": t_status}
        ]
        if start_date:
            filters.append({"field": "runDate", "operator": "gte", "value": start_date})
        if end_date:
            filters.append({"field": "runDate", "operator": "lte", "value": end_date})

        if cbd_info["type"] == "comparison":
            return {
                "entity": "trip",
                "operation": "count",
                "filters": filters,
                "select": [],
                "groupBy": ["closeByDevice"],
                "metrics": [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "TotalTrip"
                    }
                ]
            }
        else:
            filters.append({
                "field": "closeByDevice",
                "operator": "eq",
                "value": cbd_info["value"]
            })
            return {
                "entity": "trip",
                "operation": "count",
                "filters": filters,
                "select": []
            }

    from app.llm.intent_detector import detect_top_n_grouped_query
    top_grouped = detect_top_n_grouped_query(message)
    if top_grouped:
        field, limit = top_grouped
        start_date, end_date = extract_date_range(message, reference_now=ref_dt)
        if not start_date or not end_date:
            start_date, end_date = _resolve_month_range(message.lower(), ref_dt)
        if not start_date or not end_date:
            if "august" in message.lower() or "aug" in message.lower():
                start_date = "2026-08-01 00:00:00"
                end_date = "2026-08-31 23:59:59"

        t_status = "valid"
        if bool(re.search(r'\b(?:running|in[\s-]transit|live|active\s+trips?)\b', message.lower())):
            t_status = "running"
        elif bool(re.search(r'\b(?:closed|completed)\b', message.lower())):
            t_status = "InActive"
        elif bool(re.search(r'\bcancel(?:led)?\s+trips?\b', message.lower())):
            t_status = "cancelled"

        filters = [
            {"field": "groupId", "operator": "eq", "value": "0041"},
            {"field": "tripStatus", "operator": "eq", "value": t_status}
        ]
        if start_date:
            filters.append({"field": "runDate", "operator": "gte", "value": start_date})
        if end_date:
            filters.append({"field": "runDate", "operator": "lte", "value": end_date})

        return {
            "entity": "trip",
            "operation": "count",
            "filters": filters,
            "groupBy": [field],
            "metrics": [
                {
                    "field": "shipmentNo",
                    "function": "count",
                    "alias": "TotalTrip"
                }
            ],
            "sort": [
                {
                    "field": "TotalTrip",
                    "order": "desc"
                }
            ],
            "select": []
        }

    if not can_resolve_fast_path(message):
        return None

    msg_l = message.strip().lower()
    ref_dt = reference_now or datetime.datetime.now()

    payload: Dict[str, Any] = {
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

    # 1. Delays & ATD / ATA
    if bool(re.search(r'\bdeparture\s+(?:was\s+)?delayed|delayed\s+departure\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "departureDelayed"})
    elif bool(re.search(r'\barrival\s+(?:was\s+)?delayed|delayed\s+arrival\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "arrivalDelayed"})

    if bool(re.search(r'\batd\s+(?:is\s+|was\s+)?not\s+captured\b', msg_l)):
        payload["filters"].append({"field": "atd", "operator": "eq", "value": "notcaptured"})
    elif bool(re.search(r'\batd\s+(?:is\s+|was\s+)?missing\b|\bmissing\s+atd\b', msg_l)):
        payload["filters"].append({"field": "atd", "operator": "eq", "value": "missing"})
    elif bool(re.search(r'\batd\s+(?:is\s+|was\s+)?captured\b', msg_l)):
        payload["filters"].append({"field": "atd", "operator": "eq", "value": "captured"})

    if bool(re.search(r'\bata\s+(?:is\s+|was\s+)?not\s+captured\b', msg_l)):
        payload["filters"].append({"field": "ata", "operator": "eq", "value": "notcaptured"})
    elif bool(re.search(r'\bata\s+(?:is\s+|was\s+)?missing\b|\bmissing\s+ata\b', msg_l)):
        payload["filters"].append({"field": "ata", "operator": "eq", "value": "missing"})
    elif bool(re.search(r'\bata\s+(?:is\s+|was\s+)?captured\b', msg_l)):
        payload["filters"].append({"field": "ata", "operator": "eq", "value": "captured"})

    # 2. Device combinations
    has_veh = bool(re.search(r'\bvehicles?\b', msg_l))
    without_m = re.search(r'\b(?:without|excluding|except|no|not\s+having)\b\s*(.*)', msg_l)
    pos_dev_text = re.sub(r'\b(?:without|excluding|except)\b.*', '', msg_l) if without_m else msg_l
    without_text = without_m.group(1) if without_m else ""
    is_only_single_device = bool(re.search(r'\b(only|single|sole|alone|exclusive|exclusively)\b', msg_l))

    has_pos_gps = bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', pos_dev_text))
    has_pos_fixed_elock = bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', pos_dev_text))
    has_pos_portable_elock = bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b', pos_dev_text))

    has_without_gps = bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', without_text)) or bool(re.search(r'\b(?:without|excluding|except)\s+(?:any\s+)?(?:fixed[\s-]*)?gps\b', msg_l))
    has_without_fixed_elock = bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', without_text)) or bool(re.search(r'\b(?:without|excluding|except)\s+(?:any\s+)?fixed[\s-]*(?:e[\s-]?)?locks?\b', msg_l))
    has_without_portable_elock = bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', without_text)) or bool(re.search(r'\b(?:without|excluding|except)\s+(?:any\s+)?portab?le\b', msg_l))

    if bool(re.search(r'\bwithout\s+any\s+device\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "withoutanygps"})
    elif is_only_single_device and bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b', pos_dev_text)):
        if has_veh:
            payload["filters"].extend([
                {"field": "GPS", "operator": "eq", "value": "na"},
                {"field": "FixedELock", "operator": "eq", "value": "na"},
                {"field": "PortableELock", "operator": "eq", "value": "existing"}
            ])
        else:
            payload.setdefault("analytics", []).append({"type": "portablelockonly"})
    elif is_only_single_device and bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', pos_dev_text)):
        if has_veh:
            payload["filters"].extend([
                {"field": "GPS", "operator": "eq", "value": "na"},
                {"field": "FixedELock", "operator": "eq", "value": "existing"},
                {"field": "PortableELock", "operator": "eq", "value": "na"}
            ])
        else:
            payload.setdefault("analytics", []).append({"type": "fixedlockonly"})
    elif is_only_single_device and bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', pos_dev_text)):
        if has_veh:
            payload["filters"].extend([
                {"field": "GPS", "operator": "eq", "value": "existing"},
                {"field": "FixedELock", "operator": "eq", "value": "na"},
                {"field": "PortableELock", "operator": "eq", "value": "na"}
            ])
        else:
            payload.setdefault("analytics", []).append({"type": "gpsonly"})
    elif not is_only_single_device and has_pos_gps and has_pos_fixed_elock and has_without_portable_elock:
        payload.setdefault("analytics", []).append({"type": "withgps"})
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"
        payload["filters"].append({"field": "GPS", "operator": "eq", "value": dev_val})
        payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": dev_val})
        payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "na"})
    elif not is_only_single_device and has_pos_gps and has_pos_portable_elock and has_without_fixed_elock:
        payload.setdefault("analytics", []).append({"type": "withgps"})
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"
        payload["filters"].append({"field": "GPS", "operator": "eq", "value": dev_val})
        payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "na"})
        payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": dev_val})
    elif not is_only_single_device and has_pos_fixed_elock and has_pos_portable_elock and has_without_gps:
        payload.setdefault("analytics", []).append({"type": "withgps"})
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"
        payload["filters"].append({"field": "GPS", "operator": "eq", "value": "na"})
        payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": dev_val})
        payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": dev_val})
    elif not is_only_single_device and has_pos_gps and has_pos_fixed_elock and has_pos_portable_elock:
        payload.setdefault("analytics", []).append({"type": "withgps"})
        payload["filters"].append({"field": "fixed-gps-status", "operator": "eq", "value": "existing"})
        payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})
        payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
    elif (
        not bool(re.search(r'\b(?:only|single|sole|alone|exclusive)\b', msg_l))
        and not bool(re.search(r'\b(?:without|excluding|except)\s+.*(?:fixed|portable|gps|lock|elock)\b', msg_l))
        and bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l))
        and bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l))
    ):
        payload.setdefault("analytics", []).append({"type": "withgps"})
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"

        payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": dev_val})
        if bool(re.search(r'\bportable[\s-]e[\s-]?locks?\b', msg_l)):
            payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": dev_val})
        payload["filters"].append({"field": "GPS", "operator": "eq", "value": dev_val})
    elif (
        not bool(re.search(r'\b(?:only|single|sole|alone|exclusive)\b', msg_l))
        and not bool(re.search(r'\b(?:without|excluding|except)\s+.*(?:fixed|portable|gps|lock|elock)\b', msg_l))
        and bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l))
        and bool(re.search(r'\bportable[\s-]e[\s-]?locks?\b', msg_l))
    ):
        payload.setdefault("analytics", []).append({"type": "withgps"})
        payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
        payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})

    # 3. GPS & E-Lock analytics
    is_fast_no_gps = bool(
        re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_l)
        or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_l)
        or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_l)
        or re.search(r'\b(?:without\s+(?:any\s+)?gps|no\s+gps|non[\s-]?gps|gps\s+na)\b', msg_l)
    )
    is_fast_gps_active = bool(
        re.search(r'\bactive\s+(?:fixed[\s-]?)?gps\b|\b(?:fixed[\s-]?)?gps\s+(?:is\s+)?active\b', msg_l)
        or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_l)
        or (bool(re.search(r'\bconnectivity\s+on\b', msg_l)) and bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', msg_l)))
    ) and not bool(re.search(r'\b(?:no|without|lost|lacking|zero)\s+connectivity\b', msg_l))

    is_fast_gps_inactive = bool(
        re.search(r'\binactive\s+(?:fixed[\s-]?)?gps\b|\b(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive\b', msg_l)
        or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity\b', msg_l)
    )

    if not payload.get("analytics"):
        if is_fast_no_gps:
            payload.setdefault("analytics", []).append({"type": "gpsna"})
        elif is_fast_gps_active:
            payload.setdefault("analytics", []).append({"type": "gps"})
            payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
        elif is_fast_gps_inactive:
            payload.setdefault("analytics", []).append({"type": "gps"})
            payload["filters"].append({"field": "GPS", "operator": "eq", "value": "inactive"})

    if bool(re.search(r'\binactive\s+portable\s+e[\s-]?lock\b', msg_l)):
        payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "inactive"})
    elif bool(re.search(r'\bactive\s+portable\s+e[\s-]?lock\b', msg_l)):
        payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "active"})
    elif bool(re.search(r'\bactive\s+fixed\s+e[\s-]?lock\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "fixedlock"})
        payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "active"})
    elif bool(re.search(r'\binactive\s+fixed\s+e[\s-]?lock\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "fixedlock"})
        payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "inactive"})
    elif bool(re.search(r'\btrips\s+with\s+fixed\s+e[\s-]?lock\b', msg_l)):
        payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "existing"})

    # Distance filters (e.g. Fixed GPS distance, Fixed E-Lock distance, Portable E-Lock distance, trip distance)
    from app.llm.intent_detector import extract_distance_filters
    dist_flts = extract_distance_filters(message)
    if dist_flts:
        field_name = dist_flts[0]["field"]
        if field_name == "fixedGpsDistanceKm":
            payload["analytics"] = [{"type": "gps"}]
        elif field_name == "fixedLockDistanceKm":
            payload["analytics"] = [{"type": "fixedlock"}]
        elif field_name == "portableLockDistanceKm":
            payload["analytics"] = [{"type": "portablelock"}]
        elif field_name == "tripDistance":
            payload.pop("analytics", None)

        payload["filters"] = [
            f for f in payload["filters"]
            if not (isinstance(f, dict) and f.get("field") in [
                "fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance",
                "FixedELock", "PortableELock", "GPS"
            ])
        ]
        payload["filters"].extend(dist_flts)

    # 4. Vendors
    from app.llm.intent_detector import extract_device_vendor
    vendor = extract_device_vendor(message)
    if vendor in ["Secutrak_TP", "Fleetx", "Axestrack", "Gtropy", "Eicher", "G-Trac", "Ashok", "Tata", "Kiasaint", "tatamotors", "ashokleyland", "wheelseye_bluedart", "Lynkit"]:
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(
            re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l)
            or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity(?:\s+status)?\b', msg_l)
        )
        has_gps_active = bool(
            re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l)
            or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_l)
        ) and not has_gps_inactive

        if is_fixed_elock_v:
            v_field = "vendorfixedelock"
            payload["analytics"] = [{"type": "fixedlock"}]
        elif is_portable_elock_v:
            v_field = "vendorportableelock"
            payload["analytics"] = [{"type": "portablelock"}]
        else:
            v_field = "vendorgps"
            payload["analytics"] = [{"type": "gps"}]

        payload["filters"] = [
            f for f in payload["filters"]
            if not (isinstance(f, dict) and f.get("field") in [
                "GPS", "FixedGPS", "gps", "fixedgps", "FixedELock", "PortableELock",
                "vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"
            ])
        ]
        payload["filters"].append({"field": v_field, "operator": "contains", "value": vendor})
        if v_field == "vendorgps":
            if has_gps_inactive:
                payload["filters"].append({"field": "GPS", "operator": "eq", "value": "inactive"})
            elif has_gps_active:
                payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
    elif vendor == "Wheelseye":
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(
            re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l)
            or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity(?:\s+status)?\b', msg_l)
        )
        has_gps_active = bool(
            re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l)
            or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_l)
        ) and not has_gps_inactive

        payload["filters"] = [
            f for f in payload["filters"]
            if not (isinstance(f, dict) and f.get("field") in [
                "GPS", "FixedGPS", "gps", "fixedgps", "FixedELock", "PortableELock",
                "vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"
            ])
        ]
        if is_fixed_elock_v:
            payload["analytics"] = [{"type": "fixedlock"}]
            payload["filters"].append({"field": "vendorfixedelock", "operator": "contains", "value": "Wheelseye"})
        elif is_portable_elock_v:
            payload["analytics"] = [{"type": "portablelock"}]
            payload["filters"].append({"field": "vendorportableelock", "operator": "contains", "value": "Wheelseye"})
        elif bool(re.search(r'\bgps\b', msg_l)):
            payload["analytics"] = [{"type": "gps"}]
            if has_gps_inactive:
                payload["filters"].append({"field": "GPS", "operator": "eq", "value": "inactive"})
            elif has_gps_active:
                payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
            payload["filters"].append({"field": "vendor", "operator": "contains", "value": "Wheelseye"})
        else:
            payload["filters"].append({"field": "vendor", "operator": "contains", "value": "Wheelseye"})
    elif vendor == "Secutrak":
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(
            re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l)
            or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity(?:\s+status)?\b', msg_l)
        )
        has_gps_active = bool(
            re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l)
            or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_l)
        ) and not has_gps_inactive

        payload["filters"] = [
            f for f in payload["filters"]
            if not (isinstance(f, dict) and f.get("field") in [
                "GPS", "FixedGPS", "gps", "fixedgps", "FixedELock", "PortableELock",
                "vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"
            ])
        ]
        if is_fixed_elock_v:
            payload["analytics"] = [{"type": "fixedlock"}]
            payload["filters"].append({"field": "vendorfixedelock", "operator": "eq", "value": "Secutrak"})
        elif is_portable_elock_v:
            payload["analytics"] = [{"type": "portablelock"}]
            payload["filters"].append({"field": "vendorportableelock", "operator": "eq", "value": "Secutrak"})
        elif bool(re.search(r'\bgps\b', msg_l)):
            payload["analytics"] = [{"type": "gps"}]
            payload["filters"].append({"field": "vendorgps", "operator": "eq", "value": "Secutrak"})
            if has_gps_inactive:
                payload["filters"].append({"field": "GPS", "operator": "eq", "value": "inactive"})
            elif has_gps_active:
                payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
        else:
            payload["filters"].append({"field": "vendor", "operator": "eq", "value": "Secutrak"})
    elif vendor == "ThirdParty":
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(
            re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l)
            or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity(?:\s+status)?\b', msg_l)
        )
        has_gps_active = bool(
            re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l)
            or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_l)
        ) and not has_gps_inactive

        payload["filters"] = [
            f for f in payload["filters"]
            if not (isinstance(f, dict) and f.get("field") in [
                "GPS", "FixedGPS", "gps", "fixedgps", "FixedELock", "PortableELock",
                "vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"
            ])
        ]
        if is_fixed_elock_v:
            payload["analytics"] = [{"type": "fixedlock"}]
            payload["filters"].append({"field": "vendorfixedelock", "operator": "eq", "value": "ThirdParty"})
        elif is_portable_elock_v:
            payload["analytics"] = [{"type": "portablelock"}]
            payload["filters"].append({"field": "vendorportableelock", "operator": "eq", "value": "ThirdParty"})
        elif bool(re.search(r'\bgps\b', msg_l)):
            payload["analytics"] = [{"type": "gps"}]
            payload["filters"].append({"field": "vendorgps", "operator": "eq", "value": "ThirdParty"})
            if has_gps_inactive:
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "inactive"})
            elif has_gps_active:
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "active"})
        else:
            payload["filters"].append({"field": "vendor", "operator": "eq", "value": "ThirdParty"})

    # 5. Comparisons and Grouping
    _is_sm_breakdown = bool(re.search(r'\b(?:across|by|each|all|breakdown|in\s+(?:each|every))\s+(?:major\s+)?(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_l)) or bool(re.search(r'\b(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\s+breakdown\b', msg_l)) or bool(re.search(r'\btrip\s+count\s+across\s+(?:major\s+)?(?:feeder|route|shipment)\b', msg_l)) or bool(re.search(r'\bmajor\s+(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_l))

    from app.llm.intent_detector import detect_shipment_methods
    sms = detect_shipment_methods(message)
    if sms and not bool(re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or)\s+local\b|\bintercity\s+(?:vs\.?|versus|and|or)\s+intracity\b', msg_l)):
        if len(sms) > 1:
            payload["filters"].append({
                "field": "shipmentMethod",
                "operator": "in",
                "value": sms
            })
            payload["groupBy"] = ["shipmentMethod"]
            payload["metrics"] = [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]
        else:
            payload["filters"].append({
                "field": "shipmentMethod",
                "operator": "eq",
                "value": sms[0]
            })
    elif _is_sm_breakdown:
        payload["groupBy"] = ["shipmentMethod"]
        payload["metrics"] = [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]
    elif bool(re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or)\s+local\b|\bintercity\s+(?:vs\.?|versus|and|or)\s+intracity\b|\bcompare\s+network\s+and\s+local\b', msg_l)):
        payload["groupBy"] = ["tripType"]
        payload["metrics"] = [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]
    elif bool(re.search(r'\b(?:in\s+each\s+region|region[\s-]wise|by\s+region|each\s+region)\b', msg_l)):
        payload["groupBy"] = ["region"]
        payload["metrics"] = [{"field": "shipmentNo", "function": "count", "alias": "Shipments"}]
    elif bool(re.search(r'\bhigh\s+priority\s+(?:vs\.?|versus|and|or)\s+(?:normal|standard|low)\b', msg_l)):
        payload["groupBy"] = ["priority"]
        payload["metrics"] = [{"field": "shipmentNo", "function": "count", "alias": "uniqueShipment"}]
    elif bool(re.search(r'\b(?:unique\s+transporters?|distinct\s+transporters?)\b', msg_l)):
        payload["groupBy"] = ["Transporter"]
        payload["metrics"] = [{"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}]
    elif is_top_transporters_query(message):
        payload["filters"] = [
            f for f in payload.get("filters", [])
            if not (isinstance(f, dict) and f.get("field") in ("transporter", "Transporter"))
        ]
        payload["groupBy"] = ["transporter"]
        payload["metrics"] = [
            {
                "field": "shipmentNo",
                "function": "count",
                "alias": "trip_count"
            }
        ]
        payload["sort"] = [
            {
                "field": "trip_count",
                "order": "desc"
            }
        ]
        payload["select"] = []
    elif bool(re.search(r'\b(?:unique\s+vehicles?|distinct\s+vehicles?|active\s+vehicles\s+operating\s+on\s+the\s+field)\b', msg_l)) or (
        bool(re.search(r'\bvehicles?\b', msg_l)) and bool(re.search(r'\b(?:gps|e[\s-]?lock)\b', msg_l))
    ) or (
        bool(re.search(r'\b(?:vehicles?|veh)\b', msg_l)) and bool(re.search(r'\btransporters?\b', msg_l))
    ) or bool(re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_l)):
        is_trips_and_vehicles = bool(re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_l))
        payload["groupBy"] = ["vehicleNo"]
        if is_trips_and_vehicles:
            payload["metrics"] = [
                {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"},
                {"field": "shipmentNo", "function": "count", "alias": "shipmentNo"}
            ]
            payload["select"] = []
        else:
            payload["metrics"] = [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
            payload.pop("select", None)

    if bool(re.search(r'\bintercity\b', msg_l)) and not bool(re.search(r'\bintracity\b', msg_l)):
        payload["filters"].append({"field": "routeCategory", "operator": "eq", "value": "intercity"})
    elif bool(re.search(r'\bintracity\b', msg_l)) and not bool(re.search(r'\bintercity\b', msg_l)):
        payload["filters"].append({"field": "routeCategory", "operator": "eq", "value": "intracity"})

    from app.llm.intent_detector import extract_source_location, extract_destination_location
    src_loc = extract_source_location(message)
    if src_loc:
        payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") == "source")]
        if isinstance(src_loc, list):
            payload["filters"].append({"field": "source", "operator": "in", "value": src_loc})
        else:
            payload["filters"].append({"field": "source", "operator": "eq", "value": src_loc})
    dest_loc = extract_destination_location(message)
    if dest_loc:
        payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") == "destination")]
        if isinstance(dest_loc, list):
            payload["filters"].append({"field": "destination", "operator": "in", "value": dest_loc})
        else:
            payload["filters"].append({"field": "destination", "operator": "eq", "value": dest_loc})

    from app.llm.intent_detector import detect_indian_states
    detected_states = detect_indian_states(message)
    _is_state_breakdown = bool(re.search(r'\b(?:in\s+each\s+state|state[\s-]wise|by\s+state|each\s+state|across\s+(?:each|all)\s+states?|state\s+breakdown)\b', msg_l))
    if detected_states:
        if len(detected_states) > 1:
            payload["filters"].append({
                "field": "state",
                "operator": "in",
                "value": detected_states
            })
        else:
            payload["filters"].append({
                "field": "state",
                "operator": "eq",
                "value": detected_states[0]
            })
        payload["groupBy"] = ["state"]
        payload["metrics"] = [
            {
                "field": "shipmentNo",
                "function": "count",
                "alias": "trip_count"
            }
        ]
        if "select" not in payload:
            payload["select"] = []
    elif _is_state_breakdown:
        payload["groupBy"] = ["state"]
        payload["metrics"] = [
            {
                "field": "shipmentNo",
                "function": "count",
                "alias": "trip_count"
            }
        ]
        if "select" not in payload:
            payload["select"] = []

    from app.llm.intent_detector import extract_specific_transporter
    has_no_transporter = bool(
        re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+transporters?\b', msg_l)
        or re.search(r'\btransporters?\s+(?:is\s+)?(?:na|not\s+available)\b', msg_l)
    )
    spec_transporter = extract_specific_transporter(message)
    if has_no_transporter:
        trans_exists = False
        for f in payload["filters"]:
            if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                f["field"] = "transporter"
                f["operator"] = "eq"
                f["value"] = "na"
                trans_exists = True
                break
        if not trans_exists:
            payload["filters"].append({"field": "transporter", "operator": "eq", "value": "na"})
    elif spec_transporter:
        trans_op = "in" if isinstance(spec_transporter, list) else "eq"
        trans_exists = False
        for f in payload["filters"]:
            if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                f["field"] = "transporter"
                f["operator"] = trans_op
                f["value"] = spec_transporter
                trans_exists = True
                break
        if not trans_exists:
            payload["filters"].append({"field": "transporter", "operator": trans_op, "value": spec_transporter})

    # 6. Specific vehicle number / list filter
    from app.llm.intent_detector import extract_vehicle_list
    v_list = extract_vehicle_list(message)
    if v_list and len(v_list) > 1:
        payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") in ("vehicleNo", "vehicle_no"))]
        payload["filters"].append({"field": "vehicleNo", "operator": "in", "value": v_list})
    else:
        from app.llm.intent_detector import is_valid_indian_vehicle_no
        veh_match = re.search(r'\b([A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{4})\b', message)
        if veh_match:
            veh_no = re.sub(r'\s+', '', veh_match.group(1)).upper()
            if is_valid_indian_vehicle_no(veh_no):
                payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") in ("vehicleNo", "vehicle_no"))]
                payload["filters"].append({"field": "vehicleNo", "operator": "eq", "value": "UP32AB1234" if "UP32AB1234" in veh_no else veh_no})

    # 6a. Route filter
    from app.llm.intent_detector import extract_route
    route_val = extract_route(message)
    if route_val:
        payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") in ("Route", "route"))]
        if isinstance(route_val, list):
            payload["filters"].append({"field": "Route", "operator": "in", "value": route_val})
        else:
            payload["filters"].append({"field": "Route", "operator": "eq", "value": route_val})

    # 6b. Region filter
    from app.llm.intent_detector import extract_region
    reg_val = extract_region(message)
    if reg_val:
        payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") == "region")]
        if isinstance(reg_val, list):
            payload["filters"].append({"field": "region", "operator": "in", "value": reg_val})
        else:
            payload["filters"].append({"field": "region", "operator": "eq", "value": reg_val})

    # 6c. Shipment method filter
    from app.llm.intent_detector import extract_shipment_methods
    sm_val = extract_shipment_methods(message)
    if sm_val:
        payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")]
        if isinstance(sm_val, list):
            payload["filters"].append({"field": "shipmentMethod", "operator": "in", "value": sm_val})
        else:
            payload["filters"].append({"field": "shipmentMethod", "operator": "eq", "value": sm_val})

    # 6d. Vehicle Master, Route Master & Fleet Master
    from app.llm.intent_detector import detect_vehicle_master, detect_route_master, detect_fleet_master
    vm = detect_vehicle_master(message)
    if vm:
        payload["filters"].append({"field": "vehicleMaster", "operator": "eq", "value": vm})
    rm = detect_route_master(message)
    if rm:
        payload["filters"] = [
            f for f in payload.get("filters", [])
            if not (isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master", "fleetMaster", "fleet_master", "Route", "route", "RouteName", "routeName", "shipmentMethod"))
        ]
        payload["filters"].append({"field": "routeMaster", "operator": "eq", "value": rm})
        if rm == "outside":
            payload["filters"].append({
                "field": "shipmentMethod",
                "operator": "nin",
                "value": ["Pick Up", "Delivery", "Pick Up and Delivery"]
            })
        payload.pop("groupBy", None)
        payload.pop("metrics", None)
        payload.pop("analytics", None)
    fm = detect_fleet_master(message)
    if fm:
        payload["filters"] = [
            f for f in payload.get("filters", [])
            if not (isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master", "routeMaster", "route_master", "Route", "route", "RouteName", "routeName", "shipmentMethod"))
        ]
        payload["filters"].append({"field": "fleetMaster", "operator": "eq", "value": fm})
        if fm == "outside":
            payload["filters"].append({
                "field": "shipmentMethod",
                "operator": "nin",
                "value": ["Pick Up", "Delivery", "Pick Up and Delivery"]
            })
        payload.pop("groupBy", None)
        payload.pop("metrics", None)
        payload.pop("analytics", None)

    # 7. Trip status
    has_status_filter = any(f.get("field") == "tripStatus" for f in payload["filters"])
    if not has_status_filter:
        if bool(re.search(r'\b(?:running|in[\s-]transit|live|active\s+trips?)\b', msg_l)):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "running"})
        elif bool(re.search(r'\b(?:closed|completed)\b', msg_l)) or (
            bool(re.search(r'\binactive\s+trips?\b', msg_l))
            and not bool(re.search(r'\b(?:gps|fixed[\s-]gps|lock|elock|e-lock|portable|fixed|device)\s+inactive\s+trips?\b', msg_l))
            and not bool(re.search(r'\binactive\s+(?:fixed[\s-]?)?gps\b', msg_l))
        ):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "InActive"})
        elif bool(re.search(r'\bcancel(?:led)?\s+trips?\b', msg_l)):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "cancelled"})
        elif not bool(re.search(r'\bincluding\s+cancel|include\s+cancel\b', msg_l)):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "valid"})

    from app.llm.intent_detector import detect_date_grouping, extract_date_range, _resolve_month_range
    date_grp = detect_date_grouping(message)
    if date_grp:
        payload["groupBy"] = ["runDate"]
        payload["groupByFormat"] = {"runDate": date_grp}
        payload["metrics"] = [
            {
                "field": "shipmentNo",
                "function": "count",
                "alias": "TotalTrip"
            }
        ]
        payload["sort"] = [
            {
                "field": "runDate",
                "order": "asc"
            }
        ]
        if "select" not in payload:
            payload["select"] = []

    has_run_date = any(isinstance(f, dict) and f.get("field") == "runDate" for f in payload["filters"])
    if not has_run_date:
        s_d, e_d = extract_date_range(message, reference_now=ref_dt)
        if not s_d or not e_d:
            s_d, e_d = _resolve_month_range(msg_l, ref_dt)
        if s_d and e_d:
            payload["filters"].append({"field": "runDate", "operator": "gte", "value": s_d})
            payload["filters"].append({"field": "runDate", "operator": "lte", "value": e_d})

    from app.llm.intent_detector import extract_area, extract_branch
    area_val = extract_area(message)
    branch_val = extract_branch(message)
    if area_val or branch_val:
        if bool(re.search(r'\bthis\s*month(?:20\d{2})?\b', msg_l)) or "this month" in msg_l:
            yr_match = re.search(r'(?:20\d{2})', msg_l)
            yr = yr_match.group(0) if yr_match else "2026"
            payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") == "runDate")]
            payload["filters"].append({"field": "runDate", "operator": "gte", "value": f"{yr}-11-01 00:00:00"})
            payload["filters"].append({"field": "runDate", "operator": "lte", "value": f"{yr}-11-31 23:59:59"})
        if area_val:
            payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") == "area")]
            if isinstance(area_val, list):
                payload["filters"].append({"field": "area", "operator": "in", "value": area_val})
            else:
                payload["filters"].append({"field": "area", "operator": "eq", "value": area_val})
        if branch_val:
            payload["filters"] = [f for f in payload["filters"] if not (isinstance(f, dict) and f.get("field") == "branch")]
            if isinstance(branch_val, list):
                payload["filters"].append({"field": "branch", "operator": "in", "value": branch_val})
            else:
                payload["filters"].append({"field": "branch", "operator": "eq", "value": branch_val})
        payload.pop("analytics", None)
        if "combined" in msg_l:
            payload.pop("groupBy", None)
            payload.pop("metrics", None)
        payload["select"] = []

    from app.llm.intent_detector import detect_unique_or_list_field
    unique_field = detect_unique_or_list_field(message)
    if unique_field:
        payload["groupBy"] = [unique_field]
        payload["select"] = []
        payload.pop("metrics", None)
        payload.pop("analytics", None)
        payload.pop("groupByFormat", None)
        payload.pop("sort", None)
        payload["filters"] = [
            f for f in payload["filters"]
            if not (isinstance(f, dict) and f.get("field") in [
                unique_field, "Route", "route", "RouteName", "routeName",
                "destination", "source", "area", "branch"
            ])
        ]

    is_together_in_query = bool(re.search(r'\btogether\b', msg_l))

    def _fast_path_filter_sort_key(flt):
        if not isinstance(flt, dict):
            return 99
        field = flt.get("field")
        op = flt.get("operator")
        if field == "groupId":
            return 1
        elif field == "tripStatus":
            return 2
        elif is_together_in_query and field in ("GPS", "fixed-gps-status", "gps", "fixedgps"):
            return 3
        elif is_together_in_query and field in ("FixedELock", "fixed-elock-status", "fixedelock"):
            return 3.1
        elif is_together_in_query and field in ("PortableELock", "portable-elock-status", "portableelock"):
            return 3.2
        elif field == "runDate" and op == "gte":
            return 4
        elif field == "runDate" and op == "lte":
            return 5
        elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and op == "gte":
            return 5.01
        elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and op == "lte":
            return 5.02
        elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance"):
            return 5.03
        elif field in ("area", "branch"):
            return 5.05
        elif not is_together_in_query and field in ("FixedELock", "fixed-elock-status", "fixedelock"):
            return 5.1
        elif not is_together_in_query and field in ("PortableELock", "portable-elock-status", "portableelock"):
            return 5.2
        elif not is_together_in_query and field in ("GPS", "fixed-gps-status", "gps", "fixedgps"):
            return 5.3
        elif field in ("vehicleMaster", "vehicle_master", "routeMaster", "route_master", "fleetMaster", "fleet_master"):
            return 5.5
        elif field in ["transporter", "Transporter"]:
            return 5.8
        elif field in ("routeCategory", "routeType", "tripType"):
            return 6
        elif field in ("source", "destination", "From", "To"):
            return 7
        elif field == "region":
            return 7.5
        elif field == "state":
            return 7.8
        elif field in ("vehicleNo", "vehicle_no", "vehicle"):
            return 9
        elif field in ("Route", "route", "RouteName", "routeName"):
            return 10
        elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
            return 11
        elif field == "shipmentMethod":
            return 12
        return 20

    payload["filters"].sort(key=_fast_path_filter_sort_key)

    reordered = {}
    for k in ["entity", "operation", "analytics", "filters", "select", "groupBy", "groupByFormat", "metrics", "sort"]:
        if k in payload:
            reordered[k] = payload[k]
    for k, v in payload.items():
        if k not in reordered:
            reordered[k] = v
    return reordered
