import json
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
            filter_map[a["type"]] = True

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
        transporter = filter_map.get("transporter")
        region = filter_map.get("region")
        vendor = str(filter_map.get("vendor", "")).lower()
        gps = str(filter_map.get("GPS") or filter_map.get("gps", "")).lower().strip()
        fixed_gps = str(filter_map.get("FixedGPS") or filter_map.get("fixedgps", "")).lower().strip()
        fixed_elock = str(filter_map.get("FixedELock") or filter_map.get("fixedelock", "")).lower().strip()
        portable_elock = str(filter_map.get("PortableELock") or filter_map.get("portableelock", "")).lower().strip()

        # Determine vehicle subject
        is_active = (
            trip_status in ("valid", "active", "running")
            or "active vehicle" in msg_lower
            or "operating on the field" in msg_lower
            or "on the field" in msg_lower
        )
        is_operating_on_field = "operating on the field" in msg_lower or "on the field" in msg_lower

        is_active_vehicle = "active" in msg_lower or trip_status == "active"
        if is_operating_on_field:
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
        if gps in ("nogps", "nogps "):
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
        elif vendor in ("thirdparty", "3rdparty"):
            post_qualifiers.append("using 3rd-party devices")
        elif vendor:
            post_qualifiers.append(f"for {filter_map.get('vendor')} vendor")

        parts = [f"Total {total_count} {base_entity}"]
        if post_qualifiers:
            parts.extend(post_qualifiers)
        if date_clause:
            parts.append(date_clause)
        return " ".join(parts).strip() + "."

    # 3. Regular Trips
    trip_status = str(filter_map.get("tripStatus", "")).lower()
    route_category = str(filter_map.get("routeCategory", "")).lower()
    shipment_method = filter_map.get("shipmentMethod")
    transporter = filter_map.get("transporter")
    region = filter_map.get("region")
    vendor = str(filter_map.get("vendor", "")).lower()
    vehicle_no = filter_map.get("vehicleNo")
    route = filter_map.get("Route") or filter_map.get("RouteName")
    from_loc = filter_map.get("From")
    to_loc = filter_map.get("To")
    close_remarks = str(filter_map.get("closeRemarks", "")).lower()
    atd = str(filter_map.get("ATD", "")).lower()
    ata = str(filter_map.get("ATA", "")).lower()
    gps = str(filter_map.get("GPS") or filter_map.get("gps", "")).lower().strip()
    fixed_gps = str(filter_map.get("FixedGPS") or filter_map.get("fixedgps", "")).lower().strip()
    fixed_elock = str(filter_map.get("FixedELock") or filter_map.get("fixedelock", "")).lower().strip()
    portable_elock = str(filter_map.get("PortableELock") or filter_map.get("portableelock", "")).lower().strip()

    prefix_parts = []
    if trip_status in ("running", "active"):
        prefix_parts.append("running")
    elif trip_status == "closed":
        prefix_parts.append("closed")
    elif trip_status == "cancelled":
        prefix_parts.append("cancelled")
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
    if close_remarks == "manual":
        device_qualifiers.append("closed manually")
    elif close_remarks == "forcefully":
        device_qualifiers.append("closed forcefully")
    elif close_remarks == "geofence":
        device_qualifiers.append("closed by geofence")
    elif close_remarks == "api":
        device_qualifiers.append("closed via API")
    elif close_remarks == "backend":
        device_qualifiers.append("closed via backend")
    elif close_remarks == "web":
        device_qualifiers.append("closed via web")
    elif close_remarks:
        device_qualifiers.append(f"closed with {close_remarks} remark")

    # Devices
    if gps in ("nogps", "nogps "):
        device_qualifiers.append("without GPS")
    elif fixed_gps == "inactive":
        device_qualifiers.append("with inactive Fixed GPS")
    elif fixed_gps in ("active", "existing"):
        device_qualifiers.append("with active Fixed GPS")
    elif fixed_gps == "gpsinactive":
        device_qualifiers.append("with Fixed GPS inactive")
    elif fixed_gps == "na":
        device_qualifiers.append("where Fixed GPS is NA")
    elif gps == "existing":
        device_qualifiers.append("with GPS")

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
    elif vendor in ("thirdparty", "3rdparty"):
        context_qualifiers.append("using 3rd-party devices")
    elif vendor:
        context_qualifiers.append(f"for {filter_map.get('vendor')} vendor")

    dev_str = (" " + " and ".join(device_qualifiers)) if device_qualifiers else ""
    ctx_str = (" " + " ".join(context_qualifiers)) if context_qualifiers else ""

    sentence_parts = [f"Total {total_count} {prefix_str}{trip_noun}{dev_str} found{ctx_str}".strip()]
    if date_clause:
        if not ctx_str and not dev_str and not prefix_str and date_clause.startswith("in date range"):
            sentence_parts.append(f"for {date_clause[3:]}")
        else:
            sentence_parts.append(date_clause)

    return " ".join(sentence_parts).strip() + "."


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

        start_date = None
        end_date = None
        if entities and "filters" in entities:
            for f in entities["filters"]:
                if isinstance(f, dict) and f.get("field") == "runDate":
                    if f.get("operator") == "gte":
                        start_date = f.get("value")
                    elif f.get("operator") == "lte":
                        end_date = f.get("value")

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

        if not (start_date and end_date) and message:
            try:
                from app.llm.intent_detector import extract_date_range_from_message
                s_d, e_d = extract_date_range_from_message(message)
                if s_d and e_d:
                    start_date = start_date or s_d
                    end_date = end_date or e_d
            except Exception:
                pass

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

        # Fast-path: Instant 0ms response formatting for simple count queries
        if _is_distinct_transporter_result or _is_distinct_vehicle_result:
            return natural_formatted_response

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
            response = await self.llm_client.generate(
                prompt=prompt,
                system=RESPONSE_GENERATION_SYSTEM_PROMPT,
                format_json=False,
                max_tokens=60
            )
            cleaned = response.strip()
            if cleaned.startswith("```html") and cleaned.endswith("```"):
                cleaned = cleaned[7:-3].strip()
            elif cleaned.startswith("```") and cleaned.endswith("```"):
                cleaned = cleaned[3:-3].strip()
            import re
            cleaned = re.sub(r'<[^>]+>', '', cleaned).strip()
            return cleaned
        except LLMRateLimitError as rle:
            raise rle
        except Exception as exc:
            log_error(session_id, "RESPONSE_GENERATION_FAILED", str(exc))
            return natural_formatted_response
