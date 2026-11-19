import json
import re
from typing import Any, Dict, List
from app.llm.client import LLMClient, LLMRateLimitError
from app.llm.prompts import RESPONSE_GENERATION_SYSTEM_PROMPT, RESPONSE_GENERATION_USER_PROMPT_TEMPLATE
from app.utils.logger import log_error

def build_natural_response(
    total_count: int,
    filters: List[Dict[str, Any]] = None,
    analytics: List[Dict[str, Any]] = None,
    is_distinct_vehicle: bool = False,
    is_distinct_transporter: bool = False,
    message: str = "",
    start_date: str = None,
    end_date: str = None,
) -> str:
    """
    Converts API results and all active query filters into a clean, human-readable natural language response.
    """
    filters = filters or []
    analytics = analytics or []
    msg_lower = (message or "").lower()

    # Extract filter fields
    filter_map: Dict[str, Any] = {}
    for f in filters:
        if not isinstance(f, dict):
            continue
        field = f.get("field")
        val = f.get("value")
        op = f.get("operator", "eq")
        if field == "runDate":
            if op == "gte" and not start_date:
                start_date = val
            elif op == "lte" and not end_date:
                end_date = val
        elif field and field != "groupId":
            filter_map[field] = val

    # Handle analytics (e.g. delayed departure/arrival)
    for a in analytics:
        if isinstance(a, dict) and "type" in a:
            t = a["type"]
            filter_map.setdefault(t, True)
            filter_map[f"analytics_{t}"] = True

    # Date clause
    date_clause = ""
    if start_date and end_date:
        date_clause = f"in date range from {start_date} to {end_date}"
    elif start_date:
        date_clause = f"from {start_date}"
    elif end_date:
        date_clause = f"up to {end_date}"

    # 1. Distinct Transporter
    if is_distinct_transporter:
        entity_name = "unique Transporter" if total_count == 1 else "unique Transporter"
        parts = [f"Total {total_count}  {entity_name}"]
        if "region" in filter_map:
            reg = filter_map["region"]
            parts.append(f"in the {', '.join(reg) if isinstance(reg, list) else reg} region")
        if date_clause:
            parts.append(date_clause)
        return " ".join(parts).strip() + "."

    # 2. Distinct Vehicle
    if is_distinct_vehicle:
        trip_status = str(filter_map.get("tripStatus", "")).lower()
        route_category = str(filter_map.get("routeCategory", "")).lower()
        shipment_method = filter_map.get("shipmentMethod")
        transporter = filter_map.get("Transporter") or filter_map.get("transporter")
        region = filter_map.get("region")
        vendor = str(filter_map.get("vendor") or filter_map.get("vendorgps") or filter_map.get("vendorfixedelock") or filter_map.get("vendorportableelock", "")).lower()
        gps = str(filter_map.get("GPS") or filter_map.get("gps", "") or filter_map.get("fixed-gps-status", "")).lower().strip()
        fixed_gps = str(filter_map.get("FixedGPS") or filter_map.get("fixedgps", "") or filter_map.get("fixed-gps-status", "")).lower().strip()
        fixed_elock = str(filter_map.get("FixedELock") or filter_map.get("fixedelock", "") or filter_map.get("fixed-elock-status", "")).lower().strip()
        portable_elock = str(filter_map.get("PortableELock") or filter_map.get("portableelock", "") or filter_map.get("portable-elock-status", "")).lower().strip()

        # Determine vehicle subject
        is_active = (
            trip_status in ("valid", "active", "running")
            or "active vehicle" in msg_lower
            or "operating on the field" in msg_lower
            or "on the field" in msg_lower
        )
        is_operating_on_field = "operating on the field" in msg_lower or "on the field" in msg_lower

        is_active_vehicle = "active" in msg_lower or trip_status == "active"
        if "on trip" in msg_lower or "on trips" in msg_lower or "was on trip" in msg_lower:
            base_entity = "vehicles were on trip"
        elif is_operating_on_field:
            base_entity = "active vehicles operating on the field"
        elif is_active_vehicle:
            base_entity = "active vehicles"
        else:
            base_entity = "distinct vehicles"

        # Adjectives before entity (e.g. intercity, feeder)
        prefix_adjs = []
        if route_category in ("intercity", "intracity"):
            prefix_adjs.append(route_category)
        if shipment_method:
            if isinstance(shipment_method, list):
                prefix_adjs.append(" and ".join(shipment_method).lower())
            else:
                prefix_adjs.append(str(shipment_method).lower())

        if prefix_adjs:
            if base_entity.startswith("distinct "):
                base_entity = f"distinct {' '.join(prefix_adjs)} vehicles"
            elif base_entity.startswith("active "):
                base_entity = f"active {' '.join(prefix_adjs)} " + base_entity[7:]
            else:
                base_entity = f"{' '.join(prefix_adjs)} {base_entity}"

        # Post qualifiers
        post_qualifiers = []

        # Devices
        if gps in ("nogps", "nogps ") or filter_map.get("gpsna"):
            base_entity = base_entity.replace("distinct vehicles", "vehicles").replace("active vehicles", "vehicles")
            post_qualifiers.append("without GPS")
        elif fixed_elock == "existing" and (gps == "existing" or fixed_gps == "existing"):
            post_qualifiers.append("with Fixed GPS and equipped with Fixed E-Lock")
        elif fixed_elock in ("existing", "") and any(k.lower() == "fixedelock" for k in filter_map):
            post_qualifiers.append("equipped with Fixed E-Lock")
        elif portable_elock in ("existing", "") and any(k.lower() == "portableelock" for k in filter_map):
            post_qualifiers.append("equipped with Portable E-Lock")
        elif fixed_gps == "inactive":
            post_qualifiers.append("with inactive Fixed GPS")
        elif fixed_gps in ("active", "existing"):
            post_qualifiers.append("with active Fixed GPS")
        elif gps == "existing":
            post_qualifiers.append("with GPS")

        # Transporter
        if transporter:
            if "vehicles were on trip" in base_entity:
                post_qualifiers.append(f"for the transporter {transporter}")
            else:
                post_qualifiers.append(f"used by transporter {transporter}")
        elif not post_qualifiers and "distinct" in base_entity:
            post_qualifiers.append("used")

        # Region
        if region:
            reg_str = ", ".join(region) if isinstance(region, list) else region
            post_qualifiers.append(f"in the {reg_str} region")

        # Vendor
        if vendor == "secutrak":
            post_qualifiers.append("using Secutrak devices")
        elif vendor == "wheelseye":
            post_qualifiers.append("using Wheelseye devices")
        elif vendor in ("thirdparty", "3rdparty"):
            post_qualifiers.append("using 3rd-party devices")
        elif vendor:
            post_qualifiers.append(f"for {filter_map.get('vendor')} vendor")

        parts = [f"Total {total_count} {base_entity}"]
        if post_qualifiers:
            parts.extend(post_qualifiers)
        if date_clause:
            parts.append(date_clause)
        resp = " ".join(parts).strip() + "."
        if trip_status == "valid":
            resp += " It does not include cancelled trips."
        return resp

    # 3. Regular Trips
    trip_status = str(filter_map.get("tripStatus", "")).lower()
    route_category = str(filter_map.get("routeCategory", "")).lower()
    shipment_method = filter_map.get("shipmentMethod")
    transporter = filter_map.get("Transporter") or filter_map.get("transporter")
    region = filter_map.get("region")
    vendor = str(filter_map.get("vendor") or filter_map.get("vendorgps") or filter_map.get("vendorfixedelock") or filter_map.get("vendorportableelock", "")).lower()
    vehicle_no = filter_map.get("vehicleNo")
    route = filter_map.get("Route") or filter_map.get("RouteName") or filter_map.get("route")
    from_loc = filter_map.get("source") or filter_map.get("From") or filter_map.get("origin")
    to_loc = filter_map.get("destination") or filter_map.get("To") or filter_map.get("dest")
    close_remarks = str(filter_map.get("closeremark") or filter_map.get("closeRemarks", "")).lower().strip()
    atd = str(filter_map.get("atd") or filter_map.get("ATD", "")).lower().strip()
    ata = str(filter_map.get("ata") or filter_map.get("ATA", "")).lower().strip()
    gps = str(filter_map.get("GPS") or filter_map.get("gps", "") or filter_map.get("fixed-gps-status", "")).lower().strip()
    fixed_gps = str(filter_map.get("FixedGPS") or filter_map.get("fixedgps", "") or filter_map.get("fixed-gps-status", "")).lower().strip()
    fixed_elock = str(filter_map.get("FixedELock") or filter_map.get("fixedelock", "") or filter_map.get("fixed-elock-status", "")).lower().strip()
    portable_elock = str(filter_map.get("PortableELock") or filter_map.get("portableelock", "") or filter_map.get("portable-elock-status", "")).lower().strip()
    priority = str(filter_map.get("priority", "")).lower().strip()

    prefix_parts = []
    if trip_status in ("running", "active"):
        prefix_parts.append("running")
    elif trip_status == "closed":
        prefix_parts.append("closed")
    elif trip_status in ("cancelled", "cancel"):
        prefix_parts.append(trip_status)
    elif trip_status == "valid":
        prefix_parts.append("valid")

    if route_category in ("intercity", "intracity"):
        prefix_parts.append(route_category)

    if shipment_method:
        if isinstance(shipment_method, list):
            prefix_parts.append(" and ".join(shipment_method).lower())
        else:
            prefix_parts.append(str(shipment_method).lower())

    trip_noun = "trip" if total_count == 1 else "trips"
    prefix_str = (" ".join(prefix_parts) + " ") if prefix_parts else ""

    device_qualifiers = []
    # Remarks
    if close_remarks in ("manual", "manually"):
        device_qualifiers.append("closed manually")
    elif close_remarks in ("forcefully", "forcefull", "forceful"):
        device_qualifiers.append("closed forcefully")
    elif close_remarks == "geofence":
        device_qualifiers.append("closed by geofence")
    elif close_remarks == "polygon":
        device_qualifiers.append("closed by polygon")
    elif close_remarks == "api":
        device_qualifiers.append("closed via API")
    elif close_remarks == "backend":
        device_qualifiers.append("closed via backend")
    elif close_remarks == "web":
        device_qualifiers.append("closed via web")
    elif close_remarks:
        device_qualifiers.append(f"closed with {close_remarks} remark")

    # Priority
    if priority:
        device_qualifiers.append(f"marked as {priority} priority")

    # Devices
    has_withgps = bool(filter_map.get("withgps")) or (analytics and any(isinstance(a, dict) and a.get("type") == "withgps" for a in analytics))
    if has_withgps:
        has_gps_present = any(k.lower() in ("gps", "fixedgps", "fixed-gps-status") for k in filter_map) or "gps" in msg_lower
        has_fixed_present = any(k.lower() in ("fixedelock", "fixed-elock-status") for k in filter_map) or "fixed" in msg_lower
        has_portable_present = any(k.lower() in ("portableelock", "portable-elock-status") for k in filter_map) or "portable" in msg_lower

        if has_gps_present and (has_fixed_present or has_portable_present):
            if fixed_gps == "active" or gps == "active" or fixed_elock == "active" or portable_elock == "active":
                device_qualifiers.append("using active Fixed GPS, Fixed E-Lock, and Portable E-Lock together")
            else:
                device_qualifiers.append("using all three devices together (Fixed GPS, Fixed E-Lock, and Portable E-Lock)" if (has_fixed_present and has_portable_present) else "using Fixed GPS and Fixed E-Lock together")
        else:
            device_qualifiers.append("using both Fixed E-Lock and Portable E-Lock")
    else:
        if gps in ("nogps", "nogps ") or filter_map.get("gpsna"):
            device_qualifiers.append("without GPS")
        elif fixed_gps == "inactive" or gps == "inactive":
            device_qualifiers.append("with inactive Fixed GPS")
        elif fixed_gps == "active" or gps == "active":
            device_qualifiers.append("with active Fixed GPS")
        elif fixed_gps == "gpsinactive":
            device_qualifiers.append("with Fixed GPS inactive")
        elif fixed_gps == "na":
            device_qualifiers.append("where Fixed GPS is NA")
        elif fixed_gps == "existing" or gps == "existing":
            device_qualifiers.append("with Fixed GPS" if fixed_gps == "existing" else "with GPS")

        if fixed_elock in ("existing", "") and any(k.lower() == "fixedelock" for k in filter_map):
            device_qualifiers.append("equipped with Fixed E-Lock")
        elif fixed_elock == "inactive":
            device_qualifiers.append("with inactive Fixed E-Lock")
        elif fixed_elock in ("active", "Active"):
            device_qualifiers.append("with active Fixed E-Lock")
        elif fixed_elock == "na":
            device_qualifiers.append("without Fixed E-Lock")

        if portable_elock in ("existing", "") and any(k.lower() == "portableelock" for k in filter_map):
            device_qualifiers.append("equipped with Portable E-Lock")
        elif portable_elock == "inactive":
            device_qualifiers.append("with inactive Portable E-Lock")
        elif portable_elock in ("active", "Active"):
            device_qualifiers.append("with active Portable E-Lock")
        elif portable_elock == "na":
            device_qualifiers.append("without Portable E-Lock")
    # Timing / ATD / ATA
    if atd == "notcaptured":
        device_qualifiers.append("where ATD was not captured")
    elif atd == "missing":
        device_qualifiers.append("where ATD was missing")
    elif atd == "captured":
        device_qualifiers.append("where ATD was captured")
    elif atd == "notmissing":
        device_qualifiers.append("where ATD was not missing")

    if ata == "notcaptured":
        device_qualifiers.append("where ATA was not captured")
    elif ata == "missing":
        device_qualifiers.append("where ATA was missing")
    elif ata == "captured":
        device_qualifiers.append("where ATA was captured")
    elif ata == "notmissing":
        device_qualifiers.append("where ATA was not missing")

    # Analytics
    if filter_map.get("departureDelayed"):
        device_qualifiers.append("where departure was delayed")
    if filter_map.get("arrivalDelayed"):
        device_qualifiers.append("where arrival was delayed")
    if filter_map.get("withoutanygps"):
        device_qualifiers.append("without any device")
    elif filter_map.get("withoutorgps"):
        device_qualifiers.append("matching device combination")
    elif filter_map.get("withgps"):
        pass
    elif filter_map.get("gpsonly"):
        device_qualifiers.append("with single Fixed GPS only")
    elif filter_map.get("fixedgpsonly"):
        if gps == "active":
            device_qualifiers.append("with active single Fixed GPS only")
        elif gps == "inactive":
            device_qualifiers.append("with inactive single Fixed GPS only")
        else:
            device_qualifiers.append("with single Fixed GPS only")
    elif filter_map.get("fixedlockonly"):
        device_qualifiers.append("with single Fixed E-Lock only")
    elif filter_map.get("fixedelockonly"):
        if fixed_elock == "active":
            device_qualifiers.append("with active single Fixed E-Lock only")
        elif fixed_elock == "inactive":
            device_qualifiers.append("with inactive single Fixed E-Lock only")
        else:
            device_qualifiers.append("with single Fixed E-Lock only")
    elif filter_map.get("fixedlockna"):
        device_qualifiers.append("where Fixed E-Lock is not available")
    elif filter_map.get("portablelockonly"):
        device_qualifiers.append("with single Portable E-Lock only")
    elif filter_map.get("portableelockonly"):
        if portable_elock == "active":
            device_qualifiers.append("with active single Portable E-Lock only")
        elif portable_elock == "inactive":
            device_qualifiers.append("with inactive single Portable E-Lock only")
        else:
            device_qualifiers.append("with single Portable E-Lock only")
    elif filter_map.get("portablelockna"):
        device_qualifiers.append("where Portable E-Lock is not available")
    elif filter_map.get("fixedlock"):
        if fixed_elock not in ("active", "inactive"):
            device_qualifiers.append("equipped with Fixed E-Lock")
    elif filter_map.get("portablelock"):
        if portable_elock not in ("active", "inactive"):
            device_qualifiers.append("equipped with Portable E-Lock")
    elif filter_map.get("gps"):
        if gps not in ("active", "inactive"):
            device_qualifiers.append("with Fixed GPS")

    # Vehicle Master qualifier
    vm_val = str(filter_map.get("vehicleMaster") or filter_map.get("vehicle_master", "")).lower().strip()
    if vm_val == "outside":
        device_qualifiers.append("with vehicle outside of master")
    elif vm_val == "inside":
        device_qualifiers.append("with vehicle inside of master")

    # Route Master qualifier
    rm_val = str(filter_map.get("routeMaster") or filter_map.get("route_master", "")).lower().strip()
    if rm_val == "outside":
        device_qualifiers.append("with route outside of master")
    elif rm_val == "inside":
        device_qualifiers.append("with route inside of master")

    # Fleet Master qualifier
    fm_val = str(filter_map.get("fleetMaster") or filter_map.get("fleet_master", "")).lower().strip()
    if fm_val == "outside":
        device_qualifiers.append("with fleet outside of master")
    elif fm_val == "inside":
        device_qualifiers.append("with fleet inside of master")

    # Contextual qualifiers (Locations, route, vehicle, transporter, region, vendor)
    context_qualifiers = []
    if from_loc and to_loc:
        context_qualifiers.append(f"from {from_loc} to {to_loc}")
    elif from_loc:
        context_qualifiers.append(f"from {from_loc}")
    elif to_loc:
        context_qualifiers.append(f"to {to_loc}")

    if route:
        context_qualifiers.append(f"on route {route}")

    if vehicle_no:
        context_qualifiers.append(f"for vehicle {vehicle_no}")

    if transporter:
        context_qualifiers.append(f"for transporter {transporter}")

    if region:
        reg_str = ", ".join(region) if isinstance(region, list) else region
        context_qualifiers.append(f"in the {reg_str} region")

    if vendor == "secutrak":
        context_qualifiers.append("using Secutrak devices")
    elif vendor == "wheelseye":
        context_qualifiers.append("using Wheelseye devices")
    elif vendor in ("thirdparty", "3rdparty"):
        context_qualifiers.append("using 3rd-party devices")
    elif vendor:
        context_qualifiers.append(f"for {filter_map.get('vendor') or filter_map.get('vendorgps') or filter_map.get('vendorfixedelock') or filter_map.get('vendorportableelock')} vendor")

    dev_str = (" " + " and ".join(device_qualifiers)) if device_qualifiers else ""
    ctx_str = (" " + " ".join(context_qualifiers)) if context_qualifiers else ""

    sentence_parts = [f"Total {total_count} {prefix_str}{trip_noun}{dev_str} found{ctx_str}".strip()]
    if date_clause:
        if not ctx_str and not dev_str and not prefix_str and date_clause.startswith("in date range"):
            sentence_parts.append(f"for {date_clause[3:]}")
        else:
            sentence_parts.append(date_clause)

    resp = " ".join(sentence_parts).strip() + "."
    if trip_status == "valid":
        resp += " It does not include cancelled trips."
    return resp


class ResponseGenerator:
    def __init__(self, llm_client: LLMClient = None):
        from app.config import settings
        provider = getattr(settings, "RESPONSE_LLM_PROVIDER", "ollama")
        self.llm_client = llm_client or LLMClient(provider=provider)

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
        if entities and "reply" in entities:
            return entities["reply"]

        total_trips = 0
        api_data = None
        
        if isinstance(api_result, dict):
            curr = api_result
            while isinstance(curr, dict) and "data" in curr and isinstance(curr["data"], dict) and "data" in curr["data"]:
                curr = curr["data"]

            # Check for the new format {"success": True/False, "data": ...}
            if "data" in curr:
                data_val = curr["data"]
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
                report_list = curr.get("Report") or curr.get("trips") or []
                if isinstance(report_list, list):
                    total_trips = len(report_list)
                api_data = curr
        elif isinstance(api_result, list):
            total_trips = len(api_result)
            api_data = api_result
        else:
            api_data = api_result

        # Single shipment / trip lookup formatting
        is_shipment_find = (
            (entities.get("operation") == "find" if entities else False)
            or (any(isinstance(f, dict) and f.get("field") in ("shipmentNo", "shipment_no") for f in (entities.get("filters", []) if entities else [])))
        )
        if not is_shipment_find and message:
            from app.llm.intent_detector import extract_single_shipment_lookup
            if extract_single_shipment_lookup(message):
                is_shipment_find = True

        if is_shipment_find:
            records = []
            if isinstance(api_data, list):
                records = api_data
            elif isinstance(api_data, dict):
                records = [api_data]

            shipment_no = None
            if entities and "filters" in entities:
                for f in entities["filters"]:
                    if isinstance(f, dict) and f.get("field") in ("shipmentNo", "shipment_no"):
                        shipment_no = str(f.get("value"))
                        break
            if not shipment_no and message:
                from app.llm.intent_detector import extract_single_shipment_lookup
                shipment_no = extract_single_shipment_lookup(message)

            if not records or not isinstance(records[0], dict):
                s_label = f" for shipment {shipment_no}" if shipment_no else ""
                return f"No shipment details found{s_label}."

            trip = records[0]

            def _clean(val):
                if val is None:
                    return "N/A"
                s = str(val).strip()
                return s if s else "N/A"

            source_code = _clean(trip.get("source_code") or trip.get("sourceCode"))
            route_name = _clean(trip.get("route_name") or trip.get("routeName"))
            region_code = _clean(trip.get("region_code") or trip.get("regionCode") or trip.get("region"))
            destination_name = _clean(trip.get("destination_name") or trip.get("destinationName"))
            trip_type = _clean(trip.get("trip_type") or trip.get("tripType"))
            schedule_departure = _clean(trip.get("schedule_departure") or trip.get("scheduleDeparture"))
            schedule_arrival = _clean(trip.get("schedule_arrival") or trip.get("scheduleArrival"))
            vehicle_no = _clean(trip.get("vehicle_no") or trip.get("vehicleNo"))
            driver_name = _clean(trip.get("driver_name") or trip.get("driverName"))
            shipment_method = _clean(trip.get("shipment_method") or trip.get("shipmentMethod"))
            transporter_name = _clean(trip.get("transporter_name") or trip.get("transporterName") or trip.get("transporter"))
            actual_destination_arrival_time = _clean(trip.get("actual_destination_arrival_time") or trip.get("actualDestinationArrivalTime"))
            actual_source_departure_time = _clean(trip.get("actual_source_departure_time") or trip.get("actualSourceDepartureTime"))

            raw_status = trip.get("trip_status") if "trip_status" in trip else trip.get("tripStatus")
            if raw_status in (1, "1", "running", "Active", "active"):
                trip_status = "running"
            elif raw_status in (0, "0", "closed", "InActive", "inactive", "Completed", "completed"):
                trip_status = "closed"
            elif raw_status is not None and str(raw_status).strip():
                trip_status = str(raw_status).strip()
            else:
                trip_status = "N/A"

            s_id = shipment_no or trip.get("shipment_no") or trip.get("shipmentNo") or ""
            header = f"Here is the summary details for shipment {s_id}:" if s_id else "Here is the summary details:"

            return (
                f"{header}\n\n"
                f"- **Source Code**: {source_code}\n"
                f"- **Route Name**: {route_name}\n"
                f"- **Region Code**: {region_code}\n"
                f"- **Destination Name**: {destination_name}\n"
                f"- **Trip Type**: {trip_type}\n"
                f"- **Schedule Departure**: {schedule_departure}\n"
                f"- **Schedule Arrival**: {schedule_arrival}\n"
                f"- **Vehicle No**: {vehicle_no}\n"
                f"- **Driver Name**: {driver_name}\n"
                f"- **Shipment Method**: {shipment_method}\n"
                f"- **Transporter Name**: {transporter_name}\n"
                f"- **Actual Destination Arrival Time**: {actual_destination_arrival_time}\n"
                f"- **Actual Source Departure Time**: {actual_source_departure_time}\n"
                f"- **Trip Status**: {trip_status}"
            )

        start_date = None
        end_date = None
        msg_l = message.lower() if message else ""
        if entities and "filters" in entities:
            for f in entities["filters"]:
                if isinstance(f, dict) and f.get("field") == "runDate":
                    if f.get("operator") == "gte":
                        start_date = f.get("value")
                    elif f.get("operator") == "lte":
                        end_date = f.get("value")

        if not (start_date and end_date) and message:
            try:
                from app.llm.intent_detector import extract_date_range_from_message
                s_d, e_d = extract_date_range_from_message(message)
                if s_d and e_d:
                    start_date = start_date or s_d
                    end_date = end_date or e_d
            except Exception:
                pass

        # Detect distinct-vehicle grouped result: list of dicts each with 'uniqueVehicleCount' or 'totalUniqueVehicleCount'
        _is_distinct_vehicle_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and all(isinstance(row, dict) and ("uniqueVehicleCount" in row or "totalUniqueVehicleCount" in row) for row in api_data)
        )
        if _is_distinct_vehicle_result:
            total_trips = sum(row.get("uniqueVehicleCount", 0) or row.get("totalUniqueVehicleCount", 0) for row in api_data)

        # Detect distinct-transporter grouped result: list of dicts each with 'uniqueTransporterCount'
        _is_distinct_transporter_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and all(isinstance(row, dict) and "uniqueTransporterCount" in row for row in api_data)
        )
        if _is_distinct_transporter_result:
            total_trips = sum(row.get("uniqueTransporterCount", 0) for row in api_data)

        # Detect aggregated metric result in list of dicts (e.g. [{"Feeder_trips": 80290, "shipmentMethod": "FEEDER"}]):
        _is_specialized_grouped_result = (
            _is_distinct_vehicle_result
            or _is_distinct_transporter_result
            or (
                isinstance(api_data, list)
                and len(api_data) > 0
                and (
                    any(isinstance(row, dict) and ("tripType" in row or "tripTypeName" in row or "region" in row or "priority" in row or "tripPriorityName" in row) for row in api_data)
                    or ((entities.get("groupBy") == ["shipmentMethod"] if entities else False) and any(isinstance(row, dict) and ("shipmentMethod" in row or "shipment_method" in row) for row in api_data))
                )
            )
        )
        if isinstance(api_data, list) and len(api_data) > 0 and all(isinstance(row, dict) for row in api_data) and not _is_specialized_grouped_result:
            metric_keys = set()
            for row in api_data:
                for k, v in row.items():
                    if isinstance(v, (int, float)) and k.lower() not in (
                        "triptype", "groupid", "id", "_id", "status", "shipmentmethod", "priority", "region"
                    ):
                        metric_keys.add(k)
            if metric_keys:
                total_trips = sum(
                    sum(int(row[k]) for k in metric_keys if k in row and isinstance(row[k], (int, float)))
                    for row in api_data
                )

        # Detect tripType comparison grouped result (e.g. Compare Network vs Local / Intercity vs Intracity):
        _is_trip_type_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and all(isinstance(row, dict) and ("tripType" in row or "tripTypeName" in row) for row in api_data)
        )
        if _is_trip_type_result:
            network_count = 0
            local_count = 0
            for row in api_data:
                tt_name = str(row.get("tripTypeName") or row.get("tripType") or "").lower()
                cnt = row.get("TotalTrip", 0) or row.get("totaltrip", 0) or row.get("count", 0) or row.get("trip_count", 0) or row.get("total_count", 0) or 0
                if "intercity" in tt_name or "network" in tt_name or str(row.get("tripType")) == "1":
                    network_count += cnt
                elif "intracity" in tt_name or "local" in tt_name or str(row.get("tripType")) == "2":
                    local_count += cnt

            msg_l = message.lower() if message else ""
            analytics_types = []
            if entities and "analytics" in entities:
                for a in entities["analytics"]:
                    if isinstance(a, dict) and "type" in a:
                        analytics_types.append(str(a["type"]).lower())

            filter_fields = {}
            if entities and "filters" in entities:
                for f in entities["filters"]:
                    if isinstance(f, dict) and "field" in f:
                        filter_fields[f["field"]] = str(f.get("value", "")).lower()

            is_non_gps = (
                "gpsna" in analytics_types
                or filter_fields.get("GPS") in ("nogps", "nogps ")
                or bool(re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_l))
            )

            if is_non_gps:
                prefix = "Non-GPS trip counts"
            elif (
                "gps" in analytics_types or "fixedgpsonly" in analytics_types or "gpsonly" in analytics_types
                or filter_fields.get("GPS") == "active"
                or (re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l) and re.search(r'\bactive\b', msg_l))
            ) and (filter_fields.get("GPS") == "active" or re.search(r'\bactive\b', msg_l)):
                prefix = "Active Fixed GPS device counts"
            elif (
                "gps" in analytics_types or "fixedgpsonly" in analytics_types or "gpsonly" in analytics_types
                or filter_fields.get("GPS") == "inactive"
                or (re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l) and re.search(r'\binactive\b', msg_l))
            ) and (filter_fields.get("GPS") == "inactive" or re.search(r'\binactive\b', msg_l)):
                prefix = "Inactive Fixed GPS device counts"
            elif (
                "gps" in analytics_types or "fixedgpsonly" in analytics_types or "gpsonly" in analytics_types
                or filter_fields.get("GPS") == "existing"
                or re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l)
            ):
                prefix = "Fixed GPS device counts"
            elif (
                "fixedlock" in analytics_types or "fixedlockonly" in analytics_types or "fixedelockonly" in analytics_types
                or re.search(r'\bfixed[\s-]e[\s-]?lock\b', msg_l)
            ):
                if filter_fields.get("FixedELock") == "active" or re.search(r'\bactive\b', msg_l):
                    prefix = "Active Fixed E-Lock device counts"
                elif filter_fields.get("FixedELock") == "inactive" or re.search(r'\binactive\b', msg_l):
                    prefix = "Inactive Fixed E-Lock device counts"
                else:
                    prefix = "Fixed E-Lock device counts"
            elif (
                "portablelock" in analytics_types or "portablelockonly" in analytics_types or "portableelockonly" in analytics_types
                or re.search(r'\bportable[\s-]e[\s-]?lock\b', msg_l)
            ):
                if filter_fields.get("PortableELock") == "active" or re.search(r'\bactive\b', msg_l):
                    prefix = "Active Portable E-Lock device counts"
                elif filter_fields.get("PortableELock") == "inactive" or re.search(r'\binactive\b', msg_l):
                    prefix = "Inactive Portable E-Lock device counts"
                else:
                    prefix = "Portable E-Lock device counts"
            elif "withoutanygps" in analytics_types or re.search(r'\bwithout\s+any\s+device\b', msg_l):
                prefix = "Trips without any device counts"
            elif "withgps" in analytics_types or (re.search(r'\btogether\b', msg_l) and "fixed" in msg_l and "portable" in msg_l):
                if "fixed-gps" in msg_l or ("gps" in msg_l and "together" in msg_l):
                    prefix = "Trips using Fixed GPS, Fixed E-Lock, and Portable E-Lock together counts"
                else:
                    prefix = "Trips using both Fixed E-Lock and Portable E-Lock counts"
            else:
                prefix = "Trip counts"

            date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""
            if local_count > 0 and network_count > 0:
                return f"{prefix}{date_clause}: Network (Intercity Trip) count is {network_count} and Local (Intracity Trip) count is {local_count}."
            elif network_count > 0 and not ("local" in msg_l and "network" in msg_l):
                return f"{prefix}{date_clause}: Network (Intercity Trip) count is {network_count}."
            elif local_count > 0 and not ("local" in msg_l and "network" in msg_l):
                return f"{prefix}{date_clause}: Local (Intracity Trip) count is {local_count}."
            else:
                return f"{prefix}{date_clause}: Network (Intercity Trip) count is {network_count} and Local (Intracity Trip) count is {local_count}."

        # Detect region breakdown grouped result (e.g. How many trips occurred in each region):
        _is_region_breakdown_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and any(isinstance(row, dict) and "region" in row for row in api_data)
            and any(isinstance(row, dict) and ("Shipments" in row or "shipments" in row or "uniqueShipment" in row or "count" in row or "trip_count" in row) for row in api_data)
        )
        if _is_region_breakdown_result:
            region_counts: Dict[str, int] = {}
            unassigned_count = 0
            total_region_trips = 0

            for row in api_data:
                if not isinstance(row, dict):
                    continue
                cnt = row.get("Shipments") or row.get("shipments") or row.get("uniqueShipment") or row.get("count") or row.get("trip_count") or row.get("total_count") or 0
                total_region_trips += cnt
                raw_region = row.get("region")
                if raw_region is None or str(raw_region).strip() == "" or str(raw_region).lower() in ("null", "none", "unassigned", "blank", "unknown"):
                    unassigned_count += cnt
                else:
                    reg_name = str(raw_region).strip().title()
                    region_counts[reg_name] = region_counts.get(reg_name, 0) + cnt

            # Sort named regions by count descending
            sorted_regions = sorted(region_counts.items(), key=lambda x: x[1], reverse=True)

            breakdown_items = [f"{reg} is {cnt}" for reg, cnt in sorted_regions]
            if unassigned_count > 0:
                breakdown_items.append(f"Unassigned is {unassigned_count}")

            if len(breakdown_items) > 1:
                breakdown_str = ", ".join(breakdown_items[:-1]) + f", and {breakdown_items[-1]}"
            elif breakdown_items:
                breakdown_str = breakdown_items[0]
            else:
                breakdown_str = "no trips found"

            date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""
            trip_status = "valid"
            if entities and "filters" in entities:
                for f in entities["filters"]:
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        trip_status = str(f.get("value", "valid")).lower()
                        break

            status_note = " It does not include cancelled trips." if trip_status == "valid" else ""
            return f"Trip counts by region{date_clause}: {breakdown_str}.{status_note}"

        # Detect shipment method breakdown grouped result (e.g. trip count across major feeder types / route types / shipment methods):
        _is_shipment_method_breakdown_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and (entities.get("groupBy") == ["shipmentMethod"] if entities else False)
            and any(isinstance(row, dict) and ("shipmentMethod" in row or "shipment_method" in row) for row in api_data)
        )
        if _is_shipment_method_breakdown_result:
            sm_counts: Dict[str, int] = {}
            for row in api_data:
                if not isinstance(row, dict):
                    continue
                cnt = (
                    row.get("trip_count")
                    or row.get("count")
                    or row.get("Shipments")
                    or row.get("shipments")
                    or row.get("total_count")
                    or row.get("Feeder_trips")
                    or 0
                )
                raw_sm = row.get("shipmentMethod") or row.get("shipment_method")
                if raw_sm is not None and str(raw_sm).strip() != "":
                    sm_name = str(raw_sm).strip()
                    sm_counts[sm_name] = sm_counts.get(sm_name, 0) + cnt

            # Sort by count descending
            sorted_sm = sorted(sm_counts.items(), key=lambda x: x[1], reverse=True)
            breakdown_items = [f"{sm} is {cnt}" for sm, cnt in sorted_sm]
            if len(breakdown_items) > 1:
                breakdown_str = ", ".join(breakdown_items[:-1]) + f", and {breakdown_items[-1]}"
            elif breakdown_items:
                breakdown_str = breakdown_items[0]
            else:
                breakdown_str = "no trips found"

            date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""
            trip_status = "valid"
            if entities and "filters" in entities:
                for f in entities["filters"]:
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        trip_status = str(f.get("value", "valid")).lower()
                        break

            status_note = " It does not include cancelled trips." if trip_status == "valid" else ""

            if "breakdown" in msg_l:
                intro = f"Trip breakdown across shipment methods{date_clause}"
            elif "vs" in msg_l or "v/s" in msg_l or "compare" in msg_l:
                intro = f"Trip comparison across shipment methods{date_clause}"
            elif "feeder" in msg_l and ("types" in msg_l or "major" in msg_l or "across" in msg_l):
                intro = f"Trip counts across feeder types{date_clause}"
            elif "route" in msg_l and ("types" in msg_l or "major" in msg_l or "across" in msg_l):
                intro = f"Trip counts across route types{date_clause}"
            else:
                intro = f"Trip counts across shipment methods{date_clause}"

            return f"{intro}: {breakdown_str}.{status_note}"

        # Detect priority breakdown/comparison grouped result (e.g. High Priority vs Standard / Normal):
        _is_priority_breakdown_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and any(isinstance(row, dict) and ("priority" in row or "tripPriorityName" in row) for row in api_data)
            and any(isinstance(row, dict) and ("uniqueShipment" in row or "Shipments" in row or "shipments" in row or "count" in row or "trip_count" in row) for row in api_data)
        )
        if _is_priority_breakdown_result:
            high_count = 0
            normal_count = 0
            has_high = False
            has_normal = False
            priority_items = []

            for row in api_data:
                if not isinstance(row, dict):
                    continue
                cnt = row.get("uniqueShipment") or row.get("Shipments") or row.get("shipments") or row.get("count") or row.get("trip_count") or row.get("total_count") or 0
                name = str(row.get("tripPriorityName") or "").strip()
                p_val = row.get("priority")

                if "high" in name.lower() or p_val == 1 or str(p_val).lower() == "high":
                    high_count += cnt
                    has_high = True
                elif "normal" in name.lower() or "standard" in name.lower() or "low" in name.lower() or p_val is None or str(p_val).lower() in ("null", "none", "0", "2", "low"):
                    normal_count += cnt
                    has_normal = True
                else:
                    label = name if name else f"Priority {p_val}"
                    priority_items.append(f"{label} trips are {cnt}")

            if has_high and has_normal:
                return f"High Priority trips are {high_count} and Normal Priority trips are {normal_count}."
            elif has_high and not has_normal:
                return f"High Priority trips are {high_count}."
            elif has_normal and not has_high:
                return f"Normal Priority trips are {normal_count}."
            elif priority_items:
                return " and ".join(priority_items) + "."
            else:
                return f"High Priority trips are {high_count} and Normal Priority trips are {normal_count}."

        # Format natural language response converting all filter fields
        natural_formatted_response = build_natural_response(
            total_count=total_trips,
            filters=entities.get("filters") if entities else None,
            analytics=entities.get("analytics") if entities else None,
            is_distinct_vehicle=_is_distinct_vehicle_result,
            is_distinct_transporter=_is_distinct_transporter_result,
            message=message,
            start_date=start_date,
            end_date=end_date,
        )

        # Check if result is a detailed trip list vs a simple count / aggregated metric
        _is_detailed_trip_list = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and any(isinstance(r, dict) and ("vehicle_no" in r or "source_name" in r or "destination_name" in r or "driver_name" in r) for r in api_data)
        )

        # Fast-path: Instant 0ms response formatting for simple count queries and known analytics
        if _is_distinct_transporter_result:
            return natural_formatted_response

        if _is_distinct_vehicle_result:
            vehicle_list = [
                str(r.get("vehicleNo"))
                for r in api_data
                if isinstance(r, dict) and r.get("vehicleNo")
            ]
            is_list_query = bool(
                re.search(r'\b(?:lis|list)\s+of\s+(?:the\s+)?vehicles?\b|\b(?:lis|list)\s+vehicles?\b|\b(?:which|show\s+all|get\s+all)\s+vehicles?\b', msg_l)
                or (re.search(r'\b(?:lis|list)\b', msg_l) and not re.search(r'\b(?:how\s+many|count\s+of|total\s+count|count)\b', msg_l))
            )
            if is_list_query and vehicle_list:
                total_veh = len(vehicle_list)
                transporter_val = None
                if entities and "filters" in entities:
                    for f in entities["filters"]:
                        if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                            transporter_val = f.get("value")
                            break

                date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""
                trans_clause = f" for the transporter {transporter_val}" if transporter_val else ""

                if total_veh <= 10:
                    lines = [f"Total {total_veh} vehicles were on trip{trans_clause}{date_clause}:"]
                    for idx, v_no in enumerate(vehicle_list, 1):
                        lines.append(f"{idx}. {v_no}")
                    return "\n".join(lines)
                else:
                    lines = [f"Total {total_veh} vehicles were on trip{trans_clause}{date_clause}. Here is the list of first 10 vehicles:"]
                    for idx, v_no in enumerate(vehicle_list[:10], 1):
                        lines.append(f"{idx}. {v_no}")
                    lines.append(f"\nShowing first 10 of {total_veh} vehicles. Download complete Excel list: {{DOWNLOAD_URL}}")
                    return "\n".join(lines)

            return natural_formatted_response

        if not _is_detailed_trip_list:
            if start_date and end_date:
                return natural_formatted_response
            elif total_trips is not None and not (isinstance(api_data, list) and len(api_data) > 0):
                return natural_formatted_response

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
            from app.config import settings
            max_tokens = getattr(settings, "RESPONSE_MAX_TOKENS", 60)
            response = await self.llm_client.generate(
                prompt=prompt,
                system=RESPONSE_GENERATION_SYSTEM_PROMPT,
                format_json=False,
                max_tokens=max_tokens
            )
            cleaned = response.strip()
            if cleaned.startswith("```html") and cleaned.endswith("```"):
                cleaned = cleaned[7:-3].strip()
            elif cleaned.startswith("```") and cleaned.endswith("```"):
                cleaned = cleaned[3:-3].strip()
            cleaned = re.sub(r'<[^>]+>', '', cleaned).strip()
            return cleaned
        except LLMRateLimitError as rle:
            raise rle
        except Exception as exc:
            log_error(session_id, "RESPONSE_GENERATION_FAILED", str(exc))
            return natural_formatted_response
