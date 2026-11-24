import json
import re
from typing import Any, Dict, List, Optional
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
    filter_ops: Dict[str, str] = {}
    dist_field = None
    dist_gte = None
    dist_lte = None
    for f in (filters or []):
        if not isinstance(f, dict):
            continue
        field = f.get("field")
        val = f.get("value")
        op = f.get("operator", "eq")
        if field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance"):
            dist_field = field
            if op in ("gte", "gt"):
                dist_gte = str(val)
            elif op in ("lte", "lt"):
                dist_lte = str(val)
        if field == "runDate":
            if op == "gte" and not start_date:
                start_date = val
            elif op == "lte" and not end_date:
                end_date = val
        elif field and field != "groupId":
            filter_map[field] = val
            filter_ops[field] = op

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

        if bool(re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_lower)):
            date_str = f" {date_clause}" if date_clause else ""
            trans_str = f" for transporter {transporter}" if transporter else ""
            return f"Total {total_count} valid trips and unique vehicles operated{trans_str}{date_str}. It does not include cancelled trips."

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
        if shipment_method and filter_ops.get("shipmentMethod") != "nin":
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
            if isinstance(transporter, list):
                post_qualifiers.append(f"operated by {' and '.join(transporter)}")
            elif str(transporter).lower() == "na":
                post_qualifiers.append("with no transporter")
            elif "vehicles were on trip" in base_entity:
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
    state_val = filter_map.get("state")
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

    if shipment_method and filter_ops.get("shipmentMethod") != "nin":
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
        if portable_elock == "na" and (gps in ("existing", "active") or fixed_gps in ("existing", "active") or "gps" in msg_lower) and (fixed_elock in ("existing", "active") or "fixed" in msg_lower):
            device_qualifiers.append("using Fixed GPS and Fixed E-Lock together (without Portable E-Lock)" if "together" in msg_lower else "using Fixed GPS and Fixed E-Lock without Portable E-Lock")
        elif fixed_elock == "na" and (gps in ("existing", "active") or fixed_gps in ("existing", "active") or "gps" in msg_lower) and (portable_elock in ("existing", "active") or "portable" in msg_lower):
            device_qualifiers.append("using Fixed GPS and Portable E-Lock together (without Fixed E-Lock)" if "together" in msg_lower else "using Fixed GPS and Portable E-Lock without Fixed E-Lock")
        elif (gps == "na" or fixed_gps == "na") and (fixed_elock in ("existing", "active") or "fixed" in msg_lower) and (portable_elock in ("existing", "active") or "portable" in msg_lower):
            device_qualifiers.append("using Fixed E-Lock and Portable E-Lock together (without Fixed GPS)" if "together" in msg_lower else "using Fixed E-Lock and Portable E-Lock without Fixed GPS")
        else:
            has_gps_present = any(k.lower() in ("gps", "fixedgps", "fixed-gps-status") for k in filter_map) or "gps" in msg_lower
            has_fixed_present = any(k.lower() in ("fixedelock", "fixed-elock-status") for k in filter_map) or "fixed" in msg_lower
            has_portable_present = (any(k.lower() in ("portableelock", "portable-elock-status") for k in filter_map) or "portable" in msg_lower) and portable_elock != "na"

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
        device_qualifiers.append("using Fixed GPS only")
    elif filter_map.get("fixedgpsonly"):
        if gps == "active":
            device_qualifiers.append("with active Fixed GPS only")
        elif gps == "inactive":
            device_qualifiers.append("with inactive Fixed GPS only")
        else:
            device_qualifiers.append("using Fixed GPS only")
    elif filter_map.get("fixedlockonly"):
        device_qualifiers.append("using Fixed E-Lock only")
    elif filter_map.get("fixedelockonly"):
        if fixed_elock == "active":
            device_qualifiers.append("with active Fixed E-Lock only")
        elif fixed_elock == "inactive":
            device_qualifiers.append("with inactive Fixed E-Lock only")
        else:
            device_qualifiers.append("using Fixed E-Lock only")
    elif filter_map.get("fixedlockna"):
        device_qualifiers.append("where Fixed E-Lock is not available")
    elif filter_map.get("portablelockonly"):
        device_qualifiers.append("using Portable E-Lock only")
    elif filter_map.get("portableelockonly"):
        if portable_elock == "active":
            device_qualifiers.append("with active Portable E-Lock only")
        elif portable_elock == "inactive":
            device_qualifiers.append("with inactive Portable E-Lock only")
        else:
            device_qualifiers.append("using Portable E-Lock only")
    elif filter_map.get("portablelockna"):
        device_qualifiers.append("where Portable E-Lock is not available")
    dist_label = None
    if dist_field == "fixedGpsDistanceKm":
        dist_label = "Fixed GPS distance"
    elif dist_field == "fixedLockDistanceKm":
        dist_label = "Fixed E-Lock distance"
    elif dist_field == "portableLockDistanceKm":
        dist_label = "Portable E-Lock distance"
    elif dist_field == "tripDistance":
        dist_label = "trip distance"

    dist_qual = None
    if dist_label:
        if dist_gte and dist_lte:
            dist_qual = f"recording {dist_label} between {dist_gte} km and {dist_lte} km"
        elif dist_gte:
            dist_qual = f"recording {dist_label} exceeding {dist_gte} km"
        elif dist_lte:
            dist_qual = f"recording {dist_label} up to {dist_lte} km"

    if filter_map.get("fixedlock"):
        if dist_field == "fixedLockDistanceKm" and dist_qual:
            device_qualifiers.append(dist_qual)
        elif fixed_elock not in ("active", "inactive"):
            v_lock = filter_map.get("vendorfixedelock")
            if v_lock:
                device_qualifiers.append(f"equipped with {v_lock} Fixed E-Lock")
            else:
                device_qualifiers.append("equipped with Fixed E-Lock")
    elif filter_map.get("portablelock"):
        if dist_field == "portableLockDistanceKm" and dist_qual:
            device_qualifiers.append(dist_qual)
        elif portable_elock not in ("active", "inactive"):
            v_lock = filter_map.get("vendorportableelock")
            if v_lock:
                device_qualifiers.append(f"equipped with {v_lock} Portable E-Lock")
            else:
                device_qualifiers.append("equipped with Portable E-Lock")
    elif filter_map.get("gps"):
        if dist_field == "fixedGpsDistanceKm" and dist_qual:
            device_qualifiers.append(dist_qual)
        elif gps not in ("active", "inactive"):
            device_qualifiers.append("with Fixed GPS")

    if dist_qual and not any(dist_qual in q for q in device_qualifiers):
        device_qualifiers.append(dist_qual)

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
        if isinstance(transporter, list):
            context_qualifiers.append(f"operated by {' and '.join(transporter)}")
        elif str(transporter).lower() == "na":
            context_qualifiers.append("with no transporter")
        else:
            context_qualifiers.append(f"for transporter {transporter}")

    if region:
        reg_str = ", ".join(region) if isinstance(region, list) else region
        context_qualifiers.append(f"in the {reg_str} region")

    area = filter_map.get("area")
    if area:
        if "top area" in msg_lower:
            context_qualifiers.append(f"recorded under top Area {area}")
        else:
            context_qualifiers.append(f"recorded under Area {area}")

    branch = filter_map.get("branch")
    if branch:
        if "top branch" in msg_lower:
            context_qualifiers.append(f"recorded under top Branch {branch}")
        else:
            context_qualifiers.append(f"recorded under Branch {branch}")

    if state_val:
        st_str = " and ".join(state_val) if isinstance(state_val, list) else state_val
        if "generated" in msg_lower:
            context_qualifiers.append(f"generated by {st_str}")
        else:
            context_qualifiers.append(f"in {st_str}")

    if vendor == "secutrak":
        context_qualifiers.append("using Secutrak devices")
    elif vendor == "wheelseye":
        context_qualifiers.append("using Wheelseye devices")
    elif vendor in ("thirdparty", "3rdparty"):
        context_qualifiers.append("using 3rd-party devices")
    elif vendor:
        v_raw = filter_map.get('vendor') or filter_map.get('vendorgps') or filter_map.get('vendorfixedelock') or filter_map.get('vendorportableelock')
        if any(v_raw in dq for dq in device_qualifiers):
            pass
        elif "tracked by" in msg_lower or "tracked" in msg_lower:
            context_qualifiers.append(f"tracked by {v_raw}")
        else:
            context_qualifiers.append(f"for {v_raw} vendor")

    dev_str = (" " + " and ".join(device_qualifiers)) if device_qualifiers else ""
    ctx_str = (" " + " ".join(context_qualifiers)) if context_qualifiers else ""

    from app.llm.intent_detector import is_top_transporters_query
    if is_top_transporters_query(message):
        date_str = f" {date_clause}" if date_clause else ""
        return f"Total {total_count} valid trips recorded by top transporters{date_str}. It does not include cancelled trips."

    from app.llm.intent_detector import detect_unique_or_list_field
    unique_field = detect_unique_or_list_field(message) if message else None
    if unique_field:
        date_str = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""
        if unique_field == "route":
            return f"Total {total_count} unique routes were utilized across valid trips{date_str}. It does not include cancelled trips."
        elif unique_field == "destination":
            return f"Total {total_count} unique destination locations were there across valid trips{date_str}. It does not include cancelled trips."
        elif unique_field == "source":
            return f"Total {total_count} unique source locations recorded valid trips{date_str}. It does not include cancelled trips."
        elif unique_field == "area":
            return f"Total {total_count} unique Areas recorded valid trips{date_str}. It does not include cancelled trips."
        elif unique_field == "branch":
            return f"Total {total_count} unique Branches recorded valid trips{date_str}. It does not include cancelled trips."
        elif unique_field == "transporter":
            return f"Total {total_count} unique transporters operated valid trips{date_str}. It does not include cancelled trips."

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



def _render_items_list_html_card(
    items: List[str],
    total_items: int,
    title: str,
    column_label: str,
    icon: str = "📋"
) -> str:
    """
    Renders an elegant, modern HTML card displaying items (routes, destinations, sources, areas, branches, transporters) in a sleek table.
    """
    display_items = items[:10]
    plural_suffix = "es" if column_label.lower().endswith(("sh", "ch", "x", "s", "z")) else "s"
    badge_text = f"Showing 10 of {total_items:,}" if total_items > 10 else f"{total_items} {column_label.lower()}{plural_suffix if total_items != 1 else ''}"

    rows_html = []
    for idx, item in enumerate(display_items, 1):
        bg = "#ffffff" if idx % 2 != 0 else "#f8fafc"
        clean_item = str(item).strip()
        row = (
            f'<tr style="border-bottom: 1px solid #f1f5f9; background: {bg};">\n'
            f'  <td style="padding: 10px 16px; color: #64748b; font-weight: 600; width: 44px; text-align: center; font-size: 12px;">{idx}</td>\n'
            f'  <td style="padding: 10px 16px;">\n'
            f'    <span style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; font-weight: 700; color: #0f172a; background: #f1f5f9; padding: 4px 10px; border-radius: 6px; border: 1px solid #cbd5e1; letter-spacing: 0.5px;">\n'
            f'      {clean_item}\n'
            f'    </span>\n'
            f'  </td>\n'
            f'</tr>'
        )
        rows_html.append(row)

    tbody_content = "\n".join(rows_html)

    footer_note = ""
    if total_items > 10:
        footer_note = (
            f'<div style="background: #f8fafc; padding: 8px 16px; border-top: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between; font-size: 12px; color: #64748b;">\n'
            f'  <span>Showing first 10 of {total_items:,} {column_label.lower()}{plural_suffix}</span>\n'
            f'  <span style="font-weight: 500; color: #3b82f6;">Complete list available in Excel export</span>\n'
            f'</div>\n'
        )

    html = (
        f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; '
        f'max-width: 650px; margin: 12px 0; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; '
        f'box-shadow: 0 2px 8px rgba(0,0,0,0.04); overflow: hidden;">\n'
        f'  <div style="background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%); padding: 12px 16px; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between;">\n'
        f'    <div style="display: flex; align-items: center; gap: 8px;">\n'
        f'      <span style="font-size: 16px;">{icon}</span>\n'
        f'      <span style="font-weight: 600; font-size: 14px; color: #1e293b;">{title}</span>\n'
        f'    </div>\n'
        f'    <span style="background: #e0e7ff; color: #3730a3; font-size: 11px; font-weight: 600; padding: 2px 10px; border-radius: 9999px;">\n'
        f'      {badge_text}\n'
        f'    </span>\n'
        f'  </div>\n'
        f'  <table style="width: 100%; border-collapse: collapse; font-size: 13px;">\n'
        f'    <thead>\n'
        f'      <tr style="background: #f8fafc; border-bottom: 2px solid #e2e8f0; text-align: left;">\n'
        f'        <th style="padding: 8px 16px; color: #64748b; font-weight: 600; width: 44px; text-align: center;">#</th>\n'
        f'        <th style="padding: 8px 16px; color: #64748b; font-weight: 600;">{column_label}</th>\n'
        f'      </tr>\n'
        f'    </thead>\n'
        f'    <tbody>\n'
        f'{tbody_content}\n'
        f'    </tbody>\n'
        f'  </table>\n'
        f'{footer_note}'
        f'</div>'
    )
    return html


def _render_vehicle_list_html_card(vehicle_list: List[str], total_veh: int) -> str:
    """
    Renders an elegant, modern HTML card displaying vehicle registration numbers in a sleek table.
    """
    display_items = vehicle_list[:10]
    badge_text = f"Showing 10 of {total_veh:,}" if total_veh > 10 else f"{total_veh} vehicle{'s' if total_veh != 1 else ''}"

    rows_html = []
    for idx, v_no in enumerate(display_items, 1):
        bg = "#ffffff" if idx % 2 != 0 else "#f8fafc"
        clean_v_no = str(v_no).strip().upper()
        row = (
            f'<tr style="border-bottom: 1px solid #f1f5f9; background: {bg};">\n'
            f'  <td style="padding: 10px 16px; color: #64748b; font-weight: 600; width: 44px; text-align: center; font-size: 12px;">{idx}</td>\n'
            f'  <td style="padding: 10px 16px;">\n'
            f'    <span style="display: inline-flex; align-items: center; gap: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; font-weight: 700; color: #0f172a; background: #f1f5f9; padding: 4px 10px; border-radius: 6px; border: 1px solid #cbd5e1; letter-spacing: 0.5px;">\n'
            f'      <span style="font-size: 10px; font-weight: 800; color: #2563eb; background: #dbeafe; padding: 1px 4px; border-radius: 3px;">IND</span> {clean_v_no}\n'
            f'    </span>\n'
            f'  </td>\n'
            f'  <td style="padding: 10px 16px; text-align: right;">\n'
            f'    <span style="display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: #059669; font-weight: 600; background: #ecfdf5; padding: 3px 8px; border-radius: 9999px; border: 1px solid #a7f3d0;">\n'
            f'      <span style="width: 6px; height: 6px; border-radius: 50%; background: #10b981;"></span> On Trip\n'
            f'    </span>\n'
            f'  </td>\n'
            f'</tr>'
        )
        rows_html.append(row)

    tbody_content = "\n".join(rows_html)

    footer_note = ""
    if total_veh > 10:
        footer_note = (
            f'<div style="background: #f8fafc; padding: 8px 16px; border-top: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between; font-size: 12px; color: #64748b;">\n'
            f'  <span>Showing first 10 of {total_veh:,} vehicles</span>\n'
            f'  <span style="font-weight: 500; color: #3b82f6;">Complete list available in Excel export</span>\n'
            f'</div>\n'
        )

    html = (
        f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; '
        f'max-width: 650px; margin: 12px 0; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; '
        f'box-shadow: 0 2px 8px rgba(0,0,0,0.04); overflow: hidden;">\n'
        f'  <div style="background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%); padding: 12px 16px; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between;">\n'
        f'    <div style="display: flex; align-items: center; gap: 8px;">\n'
        f'      <span style="font-size: 16px;">🚛</span>\n'
        f'      <span style="font-weight: 600; font-size: 14px; color: #1e293b;">Vehicles on Trip</span>\n'
        f'    </div>\n'
        f'    <span style="background: #e0e7ff; color: #3730a3; font-size: 11px; font-weight: 600; padding: 2px 10px; border-radius: 9999px;">\n'
        f'      {badge_text}\n'
        f'    </span>\n'
        f'  </div>\n'
        f'  <table style="width: 100%; border-collapse: collapse; font-size: 13px;">\n'
        f'    <thead>\n'
        f'      <tr style="background: #f8fafc; border-bottom: 2px solid #e2e8f0; text-align: left;">\n'
        f'        <th style="padding: 8px 16px; color: #64748b; font-weight: 600; width: 44px; text-align: center;">#</th>\n'
        f'        <th style="padding: 8px 16px; color: #64748b; font-weight: 600;">Vehicle Registration</th>\n'
        f'        <th style="padding: 8px 16px; color: #64748b; font-weight: 600; text-align: right;">Status</th>\n'
        f'      </tr>\n'
        f'    </thead>\n'
        f'    <tbody>\n'
        f'{tbody_content}\n'
        f'    </tbody>\n'
        f'  </table>\n'
        f'{footer_note}'
        f'</div>'
    )
    return html


def _render_transporter_trips_vehicles_card(
    transporter_name: Optional[str],
    total_trips: int,
    total_vehicles: int,
    date_display: str,
    vehicle_rows: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Renders an elegant, modern HTML card for transporter trips and unique vehicles summary.
    """
    avg_trips = f"{(total_trips / total_vehicles):.1f}" if total_vehicles > 0 else "0.0"

    table_section = ""
    valid_rows = [
        r for r in (vehicle_rows or [])
        if isinstance(r, dict) and (r.get("vehicleNo") or r.get("vehicle_no"))
    ]
    if valid_rows:
        sorted_rows = sorted(
            valid_rows,
            key=lambda x: int(x.get("shipmentNo", 0) or x.get("TotalTrip", 0) or x.get("count", 0) or 0),
            reverse=True
        )
        display_rows = sorted_rows[:10]
        tbody_rows = []
        for idx, row in enumerate(display_rows, 1):
            v_no = str(row.get("vehicleNo") or row.get("vehicle_no", "")).strip().upper()
            v_trips = int(row.get("shipmentNo", 0) or row.get("TotalTrip", 0) or row.get("count", 0) or 0)
            pct = f"{(v_trips / total_trips * 100):.1f}%" if total_trips > 0 else "0.0%"
            bg = "#ffffff" if idx % 2 != 0 else "#f8fafc"
            tbody_rows.append(
                f'<tr style="border-bottom: 1px solid #f1f5f9; background: {bg};">\n'
                f'  <td style="padding: 10px 16px; color: #64748b; font-weight: 600; width: 44px; text-align: center; font-size: 12px;">{idx}</td>\n'
                f'  <td style="padding: 10px 16px;">\n'
                f'    <span style="display: inline-flex; align-items: center; gap: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; font-weight: 700; color: #0f172a; background: #f1f5f9; padding: 4px 10px; border-radius: 6px; border: 1px solid #cbd5e1; letter-spacing: 0.5px;">\n'
                f'      <span style="font-size: 10px; font-weight: 800; color: #2563eb; background: #dbeafe; padding: 1px 4px; border-radius: 3px;">IND</span> {v_no}\n'
                f'    </span>\n'
                f'  </td>\n'
                f'  <td style="padding: 10px 16px; text-align: right;">\n'
                f'    <span style="font-weight: 700; color: #1d4ed8; background: #eff6ff; padding: 3px 9px; border-radius: 6px; border: 1px solid #dbeafe; font-size: 13px;">{v_trips:,}</span>\n'
                f'  </td>\n'
                f'  <td style="padding: 10px 16px; text-align: right; color: #64748b; font-weight: 600; font-size: 12px;">{pct}</td>\n'
                f'</tr>'
            )
        tbody_str = "\n".join(tbody_rows)
        sub_badge = f"Top {min(len(valid_rows), 10)} of {total_vehicles:,}" if total_vehicles > 10 else f"{total_vehicles} vehicle{'s' if total_vehicles != 1 else ''}"
        table_section = (
            f'<div style="padding: 12px 16px; background: #ffffff; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between;">\n'
            f'  <span style="font-size: 13px; font-weight: 700; color: #334155;">Fleet Breakdown</span>\n'
            f'  <span style="background: #f1f5f9; color: #475569; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">{sub_badge}</span>\n'
            f'</div>\n'
            f'<table style="width: 100%; border-collapse: collapse; font-size: 13px;">\n'
            f'  <thead>\n'
            f'    <tr style="background: #f8fafc; border-bottom: 1px solid #e2e8f0; text-align: left;">\n'
            f'      <th style="padding: 8px 16px; color: #64748b; font-weight: 600; width: 44px; text-align: center; font-size: 12px;">#</th>\n'
            f'      <th style="padding: 8px 16px; color: #64748b; font-weight: 600; font-size: 12px;">Vehicle Registration</th>\n'
            f'      <th style="padding: 8px 16px; color: #64748b; font-weight: 600; text-align: right; font-size: 12px;">Trips</th>\n'
            f'      <th style="padding: 8px 16px; color: #64748b; font-weight: 600; text-align: right; font-size: 12px;">Trip Share</th>\n'
            f'    </tr>\n'
            f'  </thead>\n'
            f'  <tbody>\n'
            f'{tbody_str}\n'
            f'  </tbody>\n'
            f'</table>\n'
        )

    if transporter_name and str(transporter_name).strip().lower() not in ("none", "null", "the transporter", ""):
        banner_subtitle = "Transporter Overview"
        banner_title = str(transporter_name).strip()
    else:
        banner_subtitle = "Fleet Overview"
        banner_title = "Trips & Vehicles Summary"

    html = (
        f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; '
        f'max-width: 650px; margin: 12px 0; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; '
        f'box-shadow: 0 4px 12px rgba(0,0,0,0.05); overflow: hidden;">\n'
        f'  <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 16px 20px; display: flex; align-items: center; justify-content: space-between;">\n'
        f'    <div>\n'
        f'      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #94a3b8; font-weight: 700;">{banner_subtitle}</div>\n'
        f'      <div style="font-size: 18px; font-weight: 800; color: #ffffff; margin-top: 2px;">{banner_title}</div>\n'
        f'    </div>\n'
        f'    <div>\n'
        f'      <span style="display: inline-flex; align-items: center; gap: 6px; background: rgba(255, 255, 255, 0.12); padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; color: #e2e8f0; border: 1px solid rgba(255, 255, 255, 0.15);">\n'
        f'        📅 {date_display}\n'
        f'      </span>\n'
        f'    </div>\n'
        f'  </div>\n'
        f'  <div style="padding: 16px 20px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; display: grid; grid-template-columns: 1fr 1fr; gap: 14px;">\n'
        f'    <div style="background: #ffffff; border: 1px solid #dbeafe; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.03);">\n'
        f'      <div style="display: flex; align-items: center; justify-content: space-between;">\n'
        f'        <span style="font-size: 12px; font-weight: 700; color: #2563eb; text-transform: uppercase; letter-spacing: 0.5px;">Total Trips</span>\n'
        f'        <span style="font-size: 18px;">📦</span>\n'
        f'      </div>\n'
        f'      <div style="font-size: 26px; font-weight: 800; color: #1e3a8a; margin: 4px 0 2px 0;">{total_trips:,}</div>\n'
        f'      <div style="font-size: 11px; color: #64748b; font-weight: 500;">Valid trips operated</div>\n'
        f'    </div>\n'
        f'    <div style="background: #ffffff; border: 1px solid #bbf7d0; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.03);">\n'
        f'      <div style="display: flex; align-items: center; justify-content: space-between;">\n'
        f'        <span style="font-size: 12px; font-weight: 700; color: #16a34a; text-transform: uppercase; letter-spacing: 0.5px;">Unique Vehicles</span>\n'
        f'        <span style="font-size: 18px;">🚛</span>\n'
        f'      </div>\n'
        f'      <div style="font-size: 26px; font-weight: 800; color: #14532d; margin: 4px 0 2px 0;">{total_vehicles:,}</div>\n'
        f'      <div style="font-size: 11px; color: #64748b; font-weight: 500;">Distinct vehicles deployed</div>\n'
        f'    </div>\n'
        f'  </div>\n'
        f'{table_section}'
        f'  <div style="background: #f8fafc; padding: 10px 16px; border-top: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between; font-size: 12px; color: #64748b;">\n'
        f'    <span>* Based on valid trips. Cancelled trips are excluded.</span>\n'
        f'    <span style="color: #2563eb; font-weight: 600; background: #eff6ff; padding: 2px 8px; border-radius: 4px; border: 1px solid #dbeafe;">Avg: {avg_trips} trips/vehicle</span>\n'
        f'  </div>\n'
        f'</div>'
    )
    return html


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

        msg_l = (message or "").strip().lower()
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
        has_shipment_no_filter = any(isinstance(f, dict) and f.get("field") in ("shipmentNo", "shipment_no") for f in (entities.get("filters", []) if entities else []))
        has_shipment_msg = False
        if message:
            from app.llm.intent_detector import extract_single_shipment_lookup
            if extract_single_shipment_lookup(message):
                has_shipment_msg = True

        is_shipment_find = has_shipment_no_filter or has_shipment_msg

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
            header_title = f"Shipment Details: {s_id}" if s_id else "Shipment Details"

            if trip_status == "running":
                badge_bg = "#ecfdf5"
                badge_color = "#047857"
            elif trip_status == "closed":
                badge_bg = "#f1f5f9"
                badge_color = "#475569"
            else:
                badge_bg = "#fef3c7"
                badge_color = "#b45309"

            rows = [
                ("Source Code", source_code),
                ("Route Name", route_name),
                ("Region Code", region_code),
                ("Destination Name", destination_name),
                ("Trip Type", trip_type),
                ("Schedule Departure", schedule_departure),
                ("Schedule Arrival", schedule_arrival),
                ("Vehicle No", vehicle_no),
                ("Driver Name", driver_name),
                ("Shipment Method", shipment_method),
                ("Transporter Name", transporter_name),
                ("Actual Destination Arrival Time", actual_destination_arrival_time),
                ("Actual Source Departure Time", actual_source_departure_time),
                ("Trip Status", trip_status),
            ]

            table_rows_html = []
            for i, (label, val) in enumerate(rows):
                bg = "#fafbfc" if i % 2 == 1 else "#ffffff"
                val_style = "color: #94a3b8; font-weight: 500;" if val == "N/A" else "color: #0f172a; font-weight: 600;"
                table_rows_html.append(
                    f'<tr style="border-bottom: 1px solid #f1f5f9; background: {bg};">'
                    f'<td style="padding: 9px 16px; font-weight: 600; color: #64748b; width: 45%;">{label}</td>'
                    f'<td style="padding: 9px 16px; {val_style}">{val}</td>'
                    f'</tr>'
                )

            table_content = "\n".join(table_rows_html)

            html_card = (
                f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; '
                f'max-width: 650px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; '
                f'box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-top: 8px;">\n'
                f'<div style="background: #f8fafc; padding: 12px 16px; border-bottom: 1px solid #e2e8f0; display: flex; '
                f'align-items: center; justify-content: space-between;">\n'
                f'<span style="font-size: 15px; font-weight: 700; color: #1e293b;">📦 {header_title}</span>\n'
                f'<span style="background: {badge_bg}; color: {badge_color}; font-size: 12px; font-weight: 700; '
                f'padding: 3px 10px; border-radius: 9999px; text-transform: uppercase;">{trip_status}</span>\n'
                f'</div>\n'
                f'<table style="width: 100%; border-collapse: collapse; font-size: 13px;">\n'
                f'<tbody>\n{table_content}\n</tbody>\n'
                f'</table>\n'
                f'</div>'
            )

            intro_text = f"Here is the summary details for shipment {s_id}:" if s_id else "Here is the summary details:"
            return f"{intro_text}\n\n{html_card}"

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

        # Detect top tracking device vendors result: list of dicts with TotalTrip and vendorgps/vendorfixedelock/vendorportableelock
        _vendor_key = None
        if isinstance(api_data, list) and len(api_data) > 0 and all(isinstance(r, dict) for r in api_data):
            for vk in ("vendorgps", "vendorfixedelock", "vendorportableelock"):
                if any(vk in r for r in api_data):
                    _vendor_key = vk
                    break
        _is_top_device_vendors_result = False
        if _vendor_key:
            _is_top_device_vendors_result = True
        elif entities and isinstance(entities.get("groupBy"), list):
            for vk in ("vendorgps", "vendorfixedelock", "vendorportableelock"):
                if vk in entities["groupBy"]:
                    _vendor_key = vk
                    _is_top_device_vendors_result = True
                    break

        if _is_top_device_vendors_result:
            if _vendor_key == "vendorportableelock" or (message and "portable" in message.lower()):
                device_label = "Portable E-Lock"
            elif _vendor_key == "vendorfixedelock" or (message and "fixed" in message.lower() and "lock" in message.lower()):
                device_label = "Fixed E-Lock"
            else:
                device_label = "Fixed GPS"

            vendor_rows = []
            if isinstance(api_data, list):
                for r in api_data:
                    if not isinstance(r, dict):
                        continue
                    v_name = r.get(_vendor_key) or r.get("vendor") or r.get("vendorgps") or r.get("vendorfixedelock") or r.get("vendorportableelock")
                    if not v_name:
                        continue
                    v_cnt = r.get("TotalTrip") or r.get("totaltrip") or r.get("count") or r.get("trip_count") or 0
                    try:
                        v_cnt = int(v_cnt)
                    except (ValueError, TypeError):
                        v_cnt = 0
                    vendor_rows.append((str(v_name).strip(), v_cnt))

            # Sort descending by count
            vendor_rows.sort(key=lambda x: x[1], reverse=True)

            date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""
            if not date_clause and message and "august 2026" in message.lower():
                date_clause = " in August 2026"

            if not vendor_rows:
                return f"No trips found for top {device_label} vendors{date_clause}."

            # Build HTML list
            list_items = []
            for rank, (v_name, cnt) in enumerate(vendor_rows, 1):
                if rank == 1:
                    rank_bg = "#fef3c7"
                    rank_color = "#b45309"
                    rank_border = "#fde68a"
                elif rank == 2:
                    rank_bg = "#f1f5f9"
                    rank_color = "#475569"
                    rank_border = "#e2e8f0"
                elif rank == 3:
                    rank_bg = "#ffedd5"
                    rank_color = "#c2410c"
                    rank_border = "#fed7aa"
                else:
                    rank_bg = "#f8fafc"
                    rank_color = "#64748b"
                    rank_border = "#e2e8f0"

                list_items.append(
                    f'<li style="display: flex; align-items: center; justify-content: space-between; '
                    f'padding: 10px 14px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; '
                    f'box-shadow: 0 1px 3px rgba(0,0,0,0.04); margin-bottom: 8px;">\n'
                    f'  <div style="display: flex; align-items: center; gap: 10px;">\n'
                    f'    <span style="display: inline-flex; align-items: center; justify-content: center; '
                    f'width: 26px; height: 26px; border-radius: 50%; background: {rank_bg}; color: {rank_color}; '
                    f'border: 1px solid {rank_border}; font-weight: 700; font-size: 12px;">{rank}</span>\n'
                    f'    <span style="font-weight: 600; font-size: 14px; color: #0f172a;">{v_name}</span>\n'
                    f'  </div>\n'
                    f'  <span style="font-size: 13px; font-weight: 700; color: #2563eb; background: #eff6ff; '
                    f'padding: 4px 10px; border-radius: 6px; border: 1px solid #dbeafe;">{cnt:,} trips</span>\n'
                    f'</li>'
                )

            ol_content = "\n".join(list_items)
            html_card = (
                f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; '
                f'max-width: 650px; margin-top: 10px;">\n'
                f'<ol style="list-style: none; padding: 0; margin: 0;">\n'
                f'{ol_content}\n'
                f'</ol>\n'
                f'<p style="font-size: 12px; color: #64748b; margin-top: 8px; margin-bottom: 0;">* Based on valid trips. Cancelled trips are excluded.</p>\n'
                f'</div>'
            )

            intro_msg = f"Here is the list of top {device_label} vendors by trip count{date_clause}:"
            return f"{intro_msg}\n\n{html_card}"

        # Detect top transporters result
        from app.llm.intent_detector import is_top_transporters_query
        _is_top_transporters_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and (
                (entities.get("groupBy") == ["transporter"] if entities else False)
                or any(isinstance(r, dict) and ("transporter" in r or "Transporter" in r) for r in api_data)
            )
            and is_top_transporters_query(message)
        )
        if _is_top_transporters_result:
            transporter_rows = []
            for r in api_data:
                if not isinstance(r, dict):
                    continue
                t_name = r.get("transporter") or r.get("Transporter")
                if not t_name:
                    continue
                t_cnt = r.get("trip_count") or r.get("count") or r.get("TotalTrip") or r.get("shipments") or 0
                try:
                    t_cnt = int(t_cnt)
                except (ValueError, TypeError):
                    t_cnt = 0
                transporter_rows.append((str(t_name).strip(), t_cnt))

            transporter_rows.sort(key=lambda x: x[1], reverse=True)

            top_n_match = re.search(r'\btop\s*(\d+)\b', message, re.IGNORECASE)
            limit = int(top_n_match.group(1)) if top_n_match else 5
            transporter_rows = transporter_rows[:limit]

            date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""

            if not transporter_rows:
                return f"No trips found for top transporters{date_clause}."

            list_items = []
            for rank, (t_name, cnt) in enumerate(transporter_rows, 1):
                list_items.append(f"{rank}. {t_name} ({cnt:,} trips)")

            lines_str = "\n".join(list_items)
            return (
                f"Here are the top {len(transporter_rows)} transporters by trip volume{date_clause}:\n"
                f"{lines_str}\n\n"
                f"It does not include cancelled trips."
            )

        # Detect date-wise (day-wise or month-wise) grouped result: list of dicts with 'runDate' and 'TotalTrip' (or similar count)
        _is_date_grouped_result = False
        if isinstance(api_data, list) and len(api_data) > 0 and all(isinstance(r, dict) for r in api_data):
            has_rundate_key = any("rundate" in k.lower() for k in api_data[0].keys())
            has_trip_count = any(k in ("TotalTrip", "totaltrip", "count", "trip_count", "TotalTrips") for k in api_data[0].keys())
            if has_rundate_key and has_trip_count:
                _is_date_grouped_result = True
            elif entities and (
                entities.get("groupBy") == ["runDate"]
                or "runDate" in (entities.get("groupBy") or [])
                or bool(entities.get("groupByFormat"))
            ):
                _is_date_grouped_result = True

        if _is_date_grouped_result:
            sample_rd = ""
            for r in api_data:
                val = str(r.get("runDate") or r.get("rundate") or "").strip()
                if val:
                    sample_rd = val
                    break

            grp_fmt = entities.get("groupByFormat", {}).get("runDate") if (entities and isinstance(entities.get("groupByFormat"), dict)) else None
            is_month = False
            if grp_fmt == "month":
                is_month = True
            elif grp_fmt == "day":
                is_month = False
            elif len(sample_rd) == 7 and "-" in sample_rd:
                is_month = True
            elif len(sample_rd) == 10 and sample_rd.count("-") == 2:
                is_month = False
            elif message and ("month" in message.lower() or "monthly" in message.lower()):
                is_month = True

            date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""

            grand_total = 0
            for r in api_data:
                c = r.get("TotalTrip") or r.get("totaltrip") or r.get("count") or r.get("trip_count") or r.get("TotalTrips") or 0
                try:
                    grand_total += int(c)
                except (ValueError, TypeError):
                    pass

            MONTH_NAMES = {
                "01": "January", "02": "February", "03": "March", "04": "April",
                "05": "May", "06": "June", "07": "July", "08": "August",
                "09": "September", "10": "October", "11": "November", "12": "December"
            }

            if is_month:
                period_col_name = "Month"
                card_title = "Month-wise Trip Breakdown"
                intro_msg = f"Here is the month-wise breakdown of trips{date_clause}:"
            else:
                period_col_name = "Date"
                card_title = "Day-wise Trip Breakdown"
                intro_msg = f"Here is the day-wise breakdown of trips{date_clause}:"

            table_rows_html = []
            for i, r in enumerate(api_data):
                rd = str(r.get("runDate") or r.get("rundate") or "").strip()
                cnt_val = r.get("TotalTrip") or r.get("totaltrip") or r.get("count") or r.get("trip_count") or r.get("TotalTrips") or 0
                try:
                    cnt = int(cnt_val)
                except (ValueError, TypeError):
                    cnt = 0

                bg = "#fafbfc" if i % 2 == 1 else "#ffffff"

                if is_month and len(rd) == 7 and "-" in rd:
                    y, m = rd.split("-", 1)
                    m_name = MONTH_NAMES.get(m, m)
                    date_display = f'<span style="font-weight: 600; color: #1e293b;">{m_name} {y}</span> <span style="font-size: 11px; color: #94a3b8; font-weight: 500;">({rd})</span>'
                else:
                    date_display = f'<span style="font-weight: 600; color: #1e293b;">{rd}</span>'

                pct_str = f"{(cnt / grand_total * 100):.1f}%" if grand_total > 0 else "0%"

                table_rows_html.append(
                    f'<tr style="background: {bg}; border-bottom: 1px solid #f1f5f9;">'
                    f'<td style="padding: 10px 16px; font-size: 13px;">{date_display}</td>'
                    f'<td style="padding: 10px 16px; text-align: right; font-size: 13px;">'
                    f'<span style="font-weight: 700; color: #2563eb; background: #eff6ff; padding: 3px 9px; border-radius: 6px; border: 1px solid #dbeafe;">{cnt:,}</span>'
                    f'</td>'
                    f'<td style="padding: 10px 16px; text-align: right; font-size: 12px; font-weight: 600; color: #64748b;">{pct_str}</td>'
                    f'</tr>'
                )

            table_content = "\n".join(table_rows_html)
            scroll_style = "max-height: 480px; overflow-y: auto;" if len(api_data) > 12 else ""

            html_card = (
                f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; '
                f'max-width: 650px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; '
                f'box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-top: 10px;">\n'
                f'  <div style="background: #f8fafc; padding: 12px 16px; border-bottom: 1px solid #e2e8f0; display: flex; '
                f'align-items: center; justify-content: space-between;">\n'
                f'    <span style="font-size: 15px; font-weight: 700; color: #1e293b;">📅 {card_title}</span>\n'
                f'    <span style="background: #eff6ff; color: #1d4ed8; font-size: 12px; font-weight: 700; '
                f'padding: 3px 10px; border-radius: 9999px; border: 1px solid #bfdbfe;">{grand_total:,} Total Trips</span>\n'
                f'  </div>\n'
                f'  <div style="{scroll_style}">\n'
                f'    <table style="width: 100%; border-collapse: collapse; font-size: 13px;">\n'
                f'      <thead style="position: sticky; top: 0; z-index: 2;">\n'
                f'        <tr style="background: #f1f5f9; border-bottom: 1px solid #e2e8f0;">\n'
                f'          <th style="padding: 10px 16px; text-align: left; font-weight: 700; color: #475569; font-size: 13px;">{period_col_name}</th>\n'
                f'          <th style="padding: 10px 16px; text-align: right; font-weight: 700; color: #475569; font-size: 13px;">Trips</th>\n'
                f'          <th style="padding: 10px 16px; text-align: right; font-weight: 700; color: #475569; font-size: 13px;">Share</th>\n'
                f'        </tr>\n'
                f'      </thead>\n'
                f'      <tbody>\n'
                f'{table_content}\n'
                f'      </tbody>\n'
                f'      <tfoot>\n'
                f'        <tr style="background: #f8fafc; border-top: 2px solid #cbd5e1; font-weight: 700;">\n'
                f'          <td style="padding: 10px 16px; color: #0f172a; font-size: 13px;">Total</td>\n'
                f'          <td style="padding: 10px 16px; text-align: right; font-size: 13px;">\n'
                f'            <span style="font-weight: 700; color: #1d4ed8; background: #dbeafe; padding: 4px 10px; border-radius: 6px; border: 1px solid #bfdbfe;">{grand_total:,}</span>\n'
                f'          </td>\n'
                f'          <td style="padding: 10px 16px; text-align: right; font-size: 12px; color: #0f172a;">100%</td>\n'
                f'        </tr>\n'
                f'      </tfoot>\n'
                f'    </table>\n'
                f'  </div>\n'
                f'  <div style="background: #fafbfc; padding: 8px 16px; border-top: 1px solid #f1f5f9; font-size: 12px; color: #64748b;">\n'
                f'    * Based on valid trips. Cancelled trips are excluded.\n'
                f'  </div>\n'
                f'</div>'
            )
            return f"{intro_msg}\n\n{html_card}"

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

        # Detect state breakdown grouped result (e.g. How many trips were generated by Maharashtra and Tamil Nadu combined):
        _is_state_breakdown_result = (
            isinstance(api_data, list)
            and len(api_data) > 0
            and (entities.get("groupBy") == ["state"] if entities else False)
            and any(isinstance(row, dict) and "state" in row for row in api_data)
        )
        if _is_state_breakdown_result:
            state_counts: Dict[str, int] = {}
            total_state_trips = 0

            for row in api_data:
                if not isinstance(row, dict):
                    continue
                cnt = (
                    row.get("trip_count")
                    or row.get("count")
                    or row.get("Shipments")
                    or row.get("shipments")
                    or row.get("total_count")
                    or 0
                )
                total_state_trips += cnt
                raw_state = row.get("state")
                if raw_state is not None and str(raw_state).strip() != "":
                    st_name = str(raw_state).strip().title()
                    state_counts[st_name] = state_counts.get(st_name, 0) + cnt

            # Sort named states by count descending
            sorted_states = sorted(state_counts.items(), key=lambda x: x[1], reverse=True)
            breakdown_items = [f"{st} is {cnt}" for st, cnt in sorted_states]

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
            if "generated" in msg_l:
                return f"Total {total_state_trips} valid trips found generated by state{date_clause}: {breakdown_str}.{status_note}"
            return f"Trip counts by state{date_clause}: {breakdown_str}.{status_note}"

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

        # Check if query asks for trips and unique vehicles together
        is_trips_and_vehicles_query = bool(
            re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_l)
            or (entities and isinstance(entities.get("metrics"), list) and any(m.get("alias") == "uniqueVehicleCount" for m in entities["metrics"]) and any(m.get("alias") == "shipmentNo" for m in entities["metrics"]))
        )
        if is_trips_and_vehicles_query:
            transporter_val = None
            if entities and "filters" in entities:
                for f in entities["filters"]:
                    if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                        transporter_val = f.get("value")
                        break

            vehicle_rows = []
            if isinstance(api_data, list):
                for r in api_data:
                    if isinstance(r, dict) and (r.get("vehicleNo") or r.get("vehicle_no")):
                        vehicle_rows.append(r)
                
                # Calculate sum of shipmentNo
                total_trip_cnt = sum(
                    int(r.get("shipmentNo", 0) or r.get("TotalTrip", 0) or r.get("count", 0) or 0)
                    for r in api_data if isinstance(r, dict)
                )

                # Calculate uniqueVehicleCount (distinct vehicles / sum of uniqueVehicleCount)
                distinct_veh_set = {
                    str(r.get("vehicleNo") or r.get("vehicle_no")).strip()
                    for r in vehicle_rows
                    if str(r.get("vehicleNo") or r.get("vehicle_no")).strip()
                }
                sum_unique_veh = sum(
                    int(r.get("uniqueVehicleCount", 0) or 0)
                    for r in api_data if isinstance(r, dict)
                )
                if sum_unique_veh > 0:
                    total_veh = sum_unique_veh
                elif distinct_veh_set:
                    total_veh = len(distinct_veh_set)
                else:
                    total_veh = len(vehicle_rows)
            elif isinstance(api_data, dict):
                total_veh = int(api_data.get("uniqueVehicleCount", 0) or 0)
                total_trip_cnt = int(api_data.get("shipmentNo", 0) or api_data.get("TotalTrip", 0) or api_data.get("count", 0) or 0)
            else:
                total_veh = total_trips if _is_distinct_vehicle_result else 0
                total_trip_cnt = total_trips or 0

            if total_trip_cnt == 0 and total_trips and not _is_distinct_vehicle_result:
                total_trip_cnt = total_trips

            s_str = str(start_date).split()[0] if start_date else ""
            e_str = str(end_date).split()[0] if end_date else ""
            if str(start_date).startswith("2026-08-01") and str(end_date).startswith("2026-08-31"):
                date_display = "August 2026"
                date_prep = "in"
            elif s_str and e_str and s_str == e_str:
                try:
                    from datetime import datetime
                    dt = datetime.strptime(s_str, "%Y-%m-%d")
                    day = dt.strftime("%d").lstrip("0")
                    month = dt.strftime("%B")
                    year = dt.strftime("%Y")
                    date_display = f"{month} {day}, {year}"
                except Exception:
                    date_display = s_str
                date_prep = "on"
            elif start_date and end_date:
                date_display = f"{start_date} to {end_date}"
                date_prep = "in date range from"
            elif "august 2026" in msg_l or "aug 2026" in msg_l:
                date_display = "August 2026"
                date_prep = "in"
            else:
                date_display = "the selected period"
                date_prep = "during"

            card_html = _render_transporter_trips_vehicles_card(
                transporter_name=transporter_val,
                total_trips=total_trip_cnt,
                total_vehicles=total_veh,
                date_display=date_display,
                vehicle_rows=vehicle_rows
            )
            if transporter_val:
                header = f"Transporter {transporter_val} operated a total of {total_trip_cnt:,} valid trips using {total_veh:,} unique vehicles {date_prep} {date_display}. It does not include cancelled trips."
            else:
                header = f"A total of {total_trip_cnt:,} valid trips were operated using {total_veh:,} unique vehicles {date_prep} {date_display}. It does not include cancelled trips."
            return f"{header}\n\n{card_html}"

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

                card_html = _render_vehicle_list_html_card(vehicle_list, total_veh)
                if total_veh <= 10:
                    header = f"Total {total_veh} vehicles were on trip{trans_clause}{date_clause}:"
                    return f"{header}\n\n{card_html}"
                else:
                    header = f"Total {total_veh} vehicles were on trip{trans_clause}{date_clause}. Here is the list of first 10 vehicles:"
                    footer = f"Showing first 10 of {total_veh} vehicles. Download complete Excel list: {{DOWNLOAD_URL}}"
                    return f"{header}\n\n{card_html}\n\n{footer}"

            return natural_formatted_response

        # Check if query or entities is asking for unique field grouping (route, destination, source, area, branch, transporter)
        from app.llm.intent_detector import detect_unique_or_list_field
        unique_group_field = None
        if entities and isinstance(entities.get("groupBy"), list) and len(entities["groupBy"]) == 1:
            if entities["groupBy"][0] in ("route", "destination", "source", "area", "branch", "transporter"):
                unique_group_field = entities["groupBy"][0]
        if not unique_group_field and message:
            unique_group_field = detect_unique_or_list_field(message)

        if unique_group_field:
            is_list_query = bool(
                re.search(r'\b(?:lis|list|which|show\s+all|get\s+all)\b', msg_l)
                and not re.search(r'\b(?:how\s+many|count\s+of|total\s+count)\b', msg_l)
            )
            items = []
            if isinstance(api_data, list):
                for r in api_data:
                    if isinstance(r, dict):
                        val = r.get(unique_group_field) or r.get(unique_group_field.lower()) or next((v for v in r.values() if isinstance(v, str)), None)
                        if val:
                            items.append(str(val))
                    elif isinstance(r, str):
                        items.append(r)

            field_labels = {
                "route": ("Routes Utilized", "Route", "🛣️"),
                "destination": ("Destination Locations", "Destination Location", "📍"),
                "source": ("Source Locations", "Source Location", "📍"),
                "area": ("Areas Recorded", "Area", "🏢"),
                "branch": ("Branches Recorded", "Branch", "🏬"),
                "transporter": ("Transporters", "Transporter", "🚚"),
            }
            title, col_label, icon = field_labels.get(unique_group_field, (f"{unique_group_field.title()} List", unique_group_field.title(), "📋"))
            total_items = len(items) if items else total_trips
            col_plural = col_label.lower() + ("es" if col_label.lower().endswith(("sh", "ch", "x", "s", "z")) else "s")

            if is_list_query and items:
                date_clause = f" in date range from {start_date} to {end_date}" if start_date and end_date else ""
                card_html = _render_items_list_html_card(items, len(items), title, col_label, icon)
                if len(items) <= 10:
                    header = f"Total {len(items)} unique {col_plural} were found{date_clause}:"
                    footer = f"Download complete Excel list: {{DOWNLOAD_URL}}"
                    return f"{header}\n\n{card_html}\n\n{footer}"
                else:
                    header = f"Total {len(items)} unique {col_plural} were found{date_clause}. Here is the list of first 10 {col_plural}:"
                    footer = f"Showing first 10 of {len(items)} {col_plural}. Download complete Excel list: {{DOWNLOAD_URL}}"
                    return f"{header}\n\n{card_html}\n\n{footer}"

            return build_natural_response(
                total_count=total_items,
                filters=entities.get("filters") if entities else None,
                analytics=entities.get("analytics") if entities else None,
                is_distinct_vehicle=False,
                is_distinct_transporter=False,
                message=message,
                start_date=start_date,
                end_date=end_date,
            )

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
