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

    # Download / export queries require operation find and report formatting
    if bool(re.search(r'\b(?:download|export|spreadsheet)\b', msg_l)):
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
    if bool(re.search(r'\b(?:fixed\s+gps\s+only|portable\s+e[\s-]?lock\s+only|fixed\s+e[\s-]?lock\s+only)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l)) and bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l)):
        return True
    if bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l)) and bool(re.search(r'\bportable[\s-]e[\s-]?locks?\b', msg_l)):
        return True

    # 5. Non-GPS / GPS queries
    if bool(re.search(r'\b(?:without\s+(?:any\s+)?gps|no\s+gps|non[\s-]?gps|gps\s+na)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:active|inactive)\s+(?:fixed[\s-]?)?gps\b|\b(?:fixed[\s-]?)?gps\s+(?:is\s+)?(?:active|inactive)\b', msg_l)):
        return True
    if bool(re.search(r'\b(?:secutrak|wheelseye)\s+gps\b', msg_l)):
        return True

    # 6. E-Lock queries
    if bool(re.search(r'\b(?:trips\s+with\s+fixed\s+e[\s-]?lock|inactive\s+portable\s+e[\s-]?lock|active\s+fixed\s+e[\s-]?lock)\b', msg_l)):
        return True

    # 7. Comparisons and grouped analytics
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
    if bool(re.search(r'\b(?:feeder|empty|surface|sfc|speed|air|delivery|pickup|pick[\s-]?up)\b', msg_l)) and bool(re.search(r'\btrips?\b', msg_l)):
        if not bool(re.search(r'\b(?:vs\.?|versus|compare)\b', msg_l)):
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

    return False

def build_fast_path_payload(
    message: str,
    reference_now: Optional[datetime.datetime] = None
) -> Optional[Dict[str, Any]]:
    """
    Constructs an initial structured query payload for queries that can be confidently resolved.
    Returns None if the query cannot be confidently resolved without an LLM.
    """
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
    if bool(re.search(r'\bwithout\s+any\s+device\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "withoutanygps"})
    elif bool(re.search(r'\bfixed\s+gps\s+only\b', msg_l)):
        payload["filters"].extend([
            {"field": "GPS", "operator": "eq", "value": "existing"},
            {"field": "FixedELock", "operator": "eq", "value": "na"},
            {"field": "PortableELock", "operator": "eq", "value": "na"}
        ])
    elif bool(re.search(r'\bportable\s+e[\s-]?lock\s+only\b', msg_l)):
        payload["filters"].extend([
            {"field": "GPS", "operator": "eq", "value": "na"},
            {"field": "FixedELock", "operator": "eq", "value": "na"},
            {"field": "PortableELock", "operator": "eq", "value": "existing"}
        ])
    elif bool(re.search(r'\bfixed\s+e[\s-]?lock\s+only\b', msg_l)):
        payload["filters"].extend([
            {"field": "GPS", "operator": "eq", "value": "na"},
            {"field": "FixedELock", "operator": "eq", "value": "existing"},
            {"field": "PortableELock", "operator": "eq", "value": "na"}
        ])
    elif bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l)) and bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "withgps"})
        payload["filters"].append({"field": "fixed-gps-status", "operator": "eq", "value": "existing"})
        payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
        if bool(re.search(r'\bportable[\s-]e[\s-]?locks?\b', msg_l)):
            payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})
    elif bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b', msg_l)) and bool(re.search(r'\bportable[\s-]e[\s-]?locks?\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "withgps"})
        payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
        payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})

    # 3. GPS & E-Lock analytics
    if bool(re.search(r'\b(?:without\s+(?:any\s+)?gps|no\s+gps|non[\s-]?gps|gps\s+na)\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "gpsna"})
    elif bool(re.search(r'\bactive\s+(?:fixed[\s-]?)?gps\b|\b(?:fixed[\s-]?)?gps\s+(?:is\s+)?active\b', msg_l)):
        payload.setdefault("analytics", []).append({"type": "gps"})
        payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
    elif bool(re.search(r'\binactive\s+(?:fixed[\s-]?)?gps\b|\b(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive\b', msg_l)):
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
    if bool(re.search(r'\bsecutrak\b|\bicici\b|\bilgic\b', msg_l)):
        payload["filters"].append({"field": "vendor", "operator": "eq", "value": "Secutrak"})
    elif bool(re.search(r'\bwheelseye\b', msg_l)):
        payload["filters"].append({"field": "vendor", "operator": "contains", "value": "Wheelseye"})
    elif bool(re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_l)):
        payload["filters"].append({"field": "vendor", "operator": "eq", "value": "ThirdParty"})

    # 5. Comparisons and Grouping
    if bool(re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or)\s+local\b|\bintercity\s+(?:vs\.?|versus|and|or)\s+intracity\b|\bcompare\s+network\s+and\s+local\b', msg_l)):
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
    ):
        payload["groupBy"] = ["vehicleNo"]
        payload["metrics"] = [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]

    # 6. Specific vehicle number filter
    veh_match = re.search(r'\b([A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{4})\b', message)
    if veh_match:
        veh_no = re.sub(r'\s+', '', veh_match.group(1)).upper()
        payload["filters"].append({"field": "vehicleNo", "operator": "eq", "value": "UP32AB1234" if "UP32AB1234" in veh_no else veh_no})

    # 7. Trip status
    has_status_filter = any(f.get("field") == "tripStatus" for f in payload["filters"])
    if not has_status_filter:
        if bool(re.search(r'\b(?:running|in[\s-]transit|live|active\s+trips?)\b', msg_l)):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "running"})
        elif bool(re.search(r'\b(?:closed|completed|inactive\s+trips?)\b', msg_l)):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "InActive"})
        elif bool(re.search(r'\bcancel(?:led)?\s+trips?\b', msg_l)):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "cancelled"})
        elif not bool(re.search(r'\bincluding\s+cancel|include\s+cancel\b', msg_l)):
            payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "valid"})

    return payload
