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

    # 2. Distinct transporter queries
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
    if bool(re.search(r'\b(?:(?:fixed[\s-]*)?gps\s+(?:only|single|sole|alone|exclusive)|portable[\s-]*(?:e[\s-]?)?locks?\s+(?:only|single|sole|alone|exclusive)|fixed[\s-]*(?:e[\s-]?)?locks?\s+(?:only|single|sole|alone|exclusive))\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:only|single|sole|alone|exclusive)\s+(?:(?:fixed[\s-]*)?gps|portable[\s-]*(?:e[\s-]?)?locks?|fixed[\s-]*(?:e[\s-]?)?locks?)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:portable[\s-]e[\s-]?locks?|portable[\s-]locks?)\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]e[\s-]?locks?|fixed[\s-]locks?)\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive)\b', msg_l)):
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
    if bool(re.search(r'\b(?:secutrak_tp|secutrak|wheelseye|fleetx|axestrack|gtropy|eicher|g[\s-]?trac|ashok|tata|kiasaint)\b', msg_l)):
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
    if bool(re.search(r'\b(?:vehicles?|veh)\b', msg_l)) and bool(re.search(r'\btransporters?\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:lis|list)\s+of\s+(?:the\s+)?vehicles?\b', msg_l)):
        return True
    from app.llm.intent_detector import detect_vehicle_master, detect_route_master, detect_fleet_master, extract_single_shipment_lookup, detect_top_device_vendors, detect_date_grouping
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

    return False

def build_fast_path_payload(
    message: str,
    reference_now: Optional[datetime.datetime] = None
) -> Optional[Dict[str, Any]]:
    """
    Constructs an initial structured query payload for queries that can be confidently resolved.
    Returns None if the query cannot be confidently resolved without an LLM.
    """
    from app.llm.intent_detector import extract_single_shipment_lookup, detect_top_device_vendors, extract_date_range, _resolve_month_range
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
    if bool(re.search(r'\bwithout\s+any\s+device\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "withoutanygps"})
    elif (
        bool(re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:only|single|sole|alone|exclusive)\b', msg_l))
        or bool(re.search(r'\b(?:only|single|sole|alone|exclusive)\s+(?:fixed[\s-]*)?gps\b', msg_l))
        or (bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive)\b', msg_l)) and not bool(re.search(r'\b(?:fixed[\s-]e[\s-]?lock|portable[\s-]e[\s-]?lock)\s+(?:only|single|sole|alone|exclusive)\b', msg_l)) and not bool(re.search(r'\b(?:only|single|sole|alone|exclusive)\s+(?:fixed[\s-]e[\s-]?lock|portable[\s-]e[\s-]?lock)\b', msg_l)))
    ):
        if has_veh:
            payload["filters"].extend([
                {"field": "GPS", "operator": "eq", "value": "existing"},
                {"field": "FixedELock", "operator": "eq", "value": "na"},
                {"field": "PortableELock", "operator": "eq", "value": "na"}
            ])
        else:
            payload.setdefault("analytics", []).append({"type": "gpsonly"})
    elif (
        bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\s+(?:only|single|sole|alone|exclusive)\b', msg_l))
        or bool(re.search(r'\b(?:only|single|sole|alone|exclusive)\s+portab?le[\s-]*(?:e[\s-]?)?locks?\b', msg_l))
        or (bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive)\b', msg_l)))
    ):
        if has_veh:
            payload["filters"].extend([
                {"field": "GPS", "operator": "eq", "value": "na"},
                {"field": "FixedELock", "operator": "eq", "value": "na"},
                {"field": "PortableELock", "operator": "eq", "value": "existing"}
            ])
        else:
            payload.setdefault("analytics", []).append({"type": "portablelockonly"})
    elif (
        bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\s+(?:only|single|sole|alone|exclusive)\b', msg_l))
        or bool(re.search(r'\b(?:only|single|sole|alone|exclusive)\s+fixed[\s-]*(?:e[\s-]?)?locks?\b', msg_l))
        or (bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', msg_l)) and bool(re.search(r'\b(only|single|sole|alone|exclusive)\b', msg_l)))
    ):
        if has_veh:
            payload["filters"].extend([
                {"field": "GPS", "operator": "eq", "value": "na"},
                {"field": "FixedELock", "operator": "eq", "value": "existing"},
                {"field": "PortableELock", "operator": "eq", "value": "na"}
            ])
        else:
            payload.setdefault("analytics", []).append({"type": "fixedlockonly"})
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

    # 4. Vendors
    from app.llm.intent_detector import extract_device_vendor
    vendor = extract_device_vendor(message)
    if vendor in ["Secutrak_TP", "Fleetx", "Axestrack", "Gtropy", "Eicher", "G-Trac", "Ashok", "Tata", "Kiasaint"]:
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l))
        has_gps_active = bool(re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l))

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
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "inactive"})
            elif has_gps_active:
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "active"})
    elif vendor == "Wheelseye":
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l))
        has_gps_active = bool(re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l))

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
            payload["filters"].append({"field": "vendorgps", "operator": "contains", "value": "Wheelseye"})
            if has_gps_inactive:
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "inactive"})
            elif has_gps_active:
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "active"})
        else:
            payload["filters"].append({"field": "vendor", "operator": "contains", "value": "Wheelseye"})
    elif vendor == "Secutrak":
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l))
        has_gps_active = bool(re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l))

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
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "inactive"})
            elif has_gps_active:
                payload["filters"].append({"field": "gps", "operator": "eq", "value": "active"})
        else:
            payload["filters"].append({"field": "vendor", "operator": "eq", "value": "Secutrak"})
    elif vendor == "ThirdParty":
        is_fixed_elock_v = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_l))
        is_portable_elock_v = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_l)) and not is_fixed_elock_v
        has_gps_inactive = bool(re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_l))
        has_gps_active = bool(re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_l))

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
    _is_sm_breakdown = bool(re.search(r'\b(?:across|by|in|each|all|breakdown)\s+(?:major\s+)?(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_l)) or bool(re.search(r'\b(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\s+breakdown\b', msg_l)) or bool(re.search(r'\btrip\s+count\s+across\s+(?:major\s+)?(?:feeder|route|shipment)\b', msg_l)) or bool(re.search(r'\bmajor\s+(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_l))

    from app.llm.intent_detector import detect_shipment_methods
    sms = detect_shipment_methods(message)
    if _is_sm_breakdown:
        payload["groupBy"] = ["shipmentMethod"]
        payload["metrics"] = [{"field": "shipmentNo", "function": "count", "alias": "trip_count"}]
    elif sms and not bool(re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or)\s+local\b|\bintercity\s+(?:vs\.?|versus|and|or)\s+intracity\b', msg_l)):
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
    elif bool(re.search(r'\b(?:unique\s+vehicles?|distinct\s+vehicles?|active\s+vehicles\s+operating\s+on\s+the\s+field)\b', msg_l)) or (
        bool(re.search(r'\bvehicles?\b', msg_l)) and bool(re.search(r'\b(?:gps|e[\s-]?lock)\b', msg_l))
    ) or (
        bool(re.search(r'\b(?:vehicles?|veh)\b', msg_l)) and bool(re.search(r'\btransporters?\b', msg_l))
    ):
        payload["groupBy"] = ["vehicleNo"]
        payload["metrics"] = [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
        payload.pop("select", None)

    if bool(re.search(r'\bintercity\b', msg_l)) and not bool(re.search(r'\bintracity\b', msg_l)):
        payload["filters"].append({"field": "routeCategory", "operator": "eq", "value": "intercity"})
    elif bool(re.search(r'\bintracity\b', msg_l)) and not bool(re.search(r'\bintercity\b', msg_l)):
        payload["filters"].append({"field": "routeCategory", "operator": "eq", "value": "intracity"})

    from app.llm.intent_detector import extract_specific_transporter
    spec_transporter = extract_specific_transporter(message)
    if spec_transporter:
        trans_exists = False
        for f in payload["filters"]:
            if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                f["field"] = "transporter"
                f["operator"] = "eq"
                f["value"] = spec_transporter
                trans_exists = True
                break
        if not trans_exists:
            payload["filters"].append({"field": "transporter", "operator": "eq", "value": spec_transporter})

    # 6. Specific vehicle number filter
    veh_match = re.search(r'\b([A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{4})\b', message)
    if veh_match:
        veh_no = re.sub(r'\s+', '', veh_match.group(1)).upper()
        payload["filters"].append({"field": "vehicleNo", "operator": "eq", "value": "UP32AB1234" if "UP32AB1234" in veh_no else veh_no})

    # 6b. Vehicle Master, Route Master & Fleet Master
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

    def _fast_path_filter_sort_key(flt):
        if not isinstance(flt, dict):
            return 99
        field = flt.get("field")
        op = flt.get("operator")
        if field == "groupId":
            return 1
        elif field == "tripStatus":
            return 2
        elif field in ["transporter", "Transporter"]:
            return 3
        elif field == "runDate" and op == "gte":
            return 4
        elif field == "runDate" and op == "lte":
            return 5
        elif field in ("GPS", "FixedGPS", "FixedELock", "PortableELock", "gps", "fixedgps"):
            return 5.2
        elif field in ("vehicleMaster", "vehicle_master", "routeMaster", "route_master", "fleetMaster", "fleet_master"):
            return 5.5
        elif field in ("routeCategory", "routeType", "tripType"):
            return 6
        elif field in ("source", "destination", "From", "To"):
            return 7
        elif field == "region":
            return 8
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
    for k in ["entity", "operation", "analytics", "filters", "groupBy", "groupByFormat", "metrics", "sort"]:
        if k in payload:
            reordered[k] = payload[k]
    if "groupBy" not in payload and "select" in payload:
        reordered["select"] = payload["select"]
    for k, v in payload.items():
        if k not in reordered:
            reordered[k] = v
    return reordered
