import json
import datetime
import calendar
import re
from typing import List, Dict, Any, Optional, Union
from app.llm.client import LLMClient, LLMRateLimitError
from app.llm.prompts import INTENT_EXTRACTION_SYSTEM_PROMPT, INTENT_EXTRACTION_USER_PROMPT_TEMPLATE, route_prompt
from app.llm.fast_path import build_fast_path_payload
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

_MONTHS_STR = r'(?:january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)'

_MONTH_PATTERN = re.compile(
    r'\b(january|jan|february|feb|march|mar|april|apr|may|june|jun'
    r'|july|jul|august|aug|september|sept|sep|october|oct'
    r'|november|nov|december|dec)'
    r'(?:[\s,]+(?P<year>20\d{2}))?\b',
    re.IGNORECASE,
)

_SPECIFIC_DATE_PAT1 = re.compile(
    r'\b(?P<month>' + _MONTHS_STR + r')\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<year>20\d{2}))?\b',
    re.IGNORECASE,
)

_SPECIFIC_DATE_PAT2 = re.compile(
    r'\b(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month>' + _MONTHS_STR + r')(?:\s*,?\s*(?P<year>20\d{2}))?\b',
    re.IGNORECASE,
)

_DAY_RANGE_THEN_MONTH_PAT = re.compile(
    r'\b(?P<day1>\d{1,2})(?:st|nd|rd|th)?\s*(?:to|–|—|-|until|through|\band\b)\s*'
    r'(?P<day2>\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+'
    r'(?P<month>' + _MONTHS_STR + r')(?:[\s,]+(?P<year>20\d{2}))?\b',
    re.IGNORECASE,
)

_MONTH_THEN_DAY_RANGE_PAT = re.compile(
    r'\b(?P<month>' + _MONTHS_STR + r')\s+'
    r'(?P<day1>\d{1,2})(?:st|nd|rd|th)?\s*(?:to|–|—|-|until|through|\band\b)\s*'
    r'(?P<day2>\d{1,2})(?:st|nd|rd|th)?(?:[\s,]+(?P<year>20\d{2}))?\b',
    re.IGNORECASE,
)

_DMY_DATE_PAT = re.compile(
    r'\b(?P<day>\d{1,2})[-/.](?P<month>\d{1,2})[-/.](?P<year>\d{4})(?:\s+(?P<time>\d{2}:\d{2}:\d{2}))?\b'
)

_YMD_DATE_PAT = re.compile(
    r'\b(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?:\s+(?P<time>\d{2}:\d{2}:\d{2}))?\b'
)

def _resolve_month_range(text: str, reference_now: datetime.datetime) -> tuple[str | None, str | None]:
    """Return (start, end) date-time strings for month name(s) found in *text*.

    If multiple months are found (e.g. 'between August 2026 and October 2026' or 'August to October 2026'),
    returns a range from the start of the first month to the end of the last month.
    If the message also contains a 4-digit year (e.g. '2026'), that year is used.
    Otherwise the most-recently-passed occurrence relative to *reference_now* is used.
    """
    if (
        _SPECIFIC_DATE_PAT1.search(text)
        or _SPECIFIC_DATE_PAT2.search(text)
        or _DAY_RANGE_THEN_MONTH_PAT.search(text)
        or _MONTH_THEN_DAY_RANGE_PAT.search(text)
        or _DMY_DATE_PAT.search(text)
        or _YMD_DATE_PAT.search(text)
    ):
        return None, None

    all_matches = list(_MONTH_PATTERN.finditer(text))
    if not all_matches:
        if "this month" in text.lower() or "current month" in text.lower():
            last_day = calendar.monthrange(reference_now.year, reference_now.month)[1]
            return (
                f"{reference_now.year:04d}-{reference_now.month:02d}-01 00:00:00",
                f"{reference_now.year:04d}-{reference_now.month:02d}-{last_day:02d} 23:59:59"
            )
        return None, None

    # Determine explicit year in text if present
    text_year_match = re.search(r'\b(20\d{2})\b', text)
    explicit_year = int(text_year_match.group(1)) if text_year_match else None

    # First month
    m1 = all_matches[0]
    month_num1 = _MONTH_MAP.get(m1.group(1).lower())
    if not month_num1:
        return None, None

    year_str1 = m1.group("year")
    if year_str1:
        year1 = int(year_str1)
    elif explicit_year:
        year1 = explicit_year
    else:
        year1 = reference_now.year
        if month_num1 > reference_now.month:
            year1 -= 1

    # Last month (if multiple months mentioned, e.g. "August to October 2026")
    m2 = all_matches[-1]
    month_num2 = _MONTH_MAP.get(m2.group(1).lower())
    if not month_num2:
        month_num2 = month_num1
        year2 = year1
    else:
        year_str2 = m2.group("year")
        if year_str2:
            year2 = int(year_str2)
        elif explicit_year:
            year2 = explicit_year
        else:
            year2 = year1
            if month_num2 > reference_now.month:
                year2 -= 1

    if year1 > year2 or (year1 == year2 and month_num1 > month_num2):
        year1, year2 = year2, year1
        month_num1, month_num2 = month_num2, month_num1

    last_day2 = calendar.monthrange(year2, month_num2)[1]
    start = f"{year1}-{month_num1:02d}-01 00:00:00"
    end   = f"{year2}-{month_num2:02d}-{last_day2:02d} 23:59:59"
    return start, end

def extract_date_range(text: str, reference_now: datetime.datetime | None = None) -> tuple[str | None, str | None]:
    now_dt = reference_now or datetime.datetime.now()
    ref_year = now_dt.year

    text_year_match = re.search(r'\b(20\d{2})\b', text)
    explicit_text_year = int(text_year_match.group(1)) if text_year_match else None

    dates: list[tuple[int, str, str | None]] = []
    masked_text = text

    # 1. YMD format (e.g. 2026-08-05, 2026/08/05)
    for m in _YMD_DATE_PAT.finditer(text):
        y, mo, d = int(m.group("year")), int(m.group("month")), int(m.group("day"))
        t = m.group("time")
        try:
            dt = datetime.date(y, mo, d)
            dates.append((m.start(), dt.strftime("%Y-%m-%d"), t))
            masked_text = masked_text[:m.start()] + ' ' * (m.end() - m.start()) + masked_text[m.end():]
        except ValueError:
            pass

    # 2. DMY format (e.g. 05-08-2026, 05/08/2026, 5-8-2026)
    for m in _DMY_DATE_PAT.finditer(text):
        d_val, mo_val, y = int(m.group("day")), int(m.group("month")), int(m.group("year"))
        t = m.group("time")
        if mo_val > 12 and d_val <= 12:
            d_val, mo_val = mo_val, d_val
        try:
            dt = datetime.date(y, mo_val, d_val)
            dates.append((m.start(), dt.strftime("%Y-%m-%d"), t))
            masked_text = masked_text[:m.start()] + ' ' * (m.end() - m.start()) + masked_text[m.end():]
        except ValueError:
            pass

    # 3. Day range then month (e.g. '6 to 10 July 2026', '6-10 July 2026', '6th to 10th July 2026')
    for m in _DAY_RANGE_THEN_MONTH_PAT.finditer(masked_text):
        m_name = m.group("month").lower()
        m_num = _MONTH_MAP.get(m_name)
        if not m_num:
            continue
        day1 = int(m.group("day1"))
        day2 = int(m.group("day2"))
        year_str = m.group("year")
        if year_str:
            year = int(year_str)
        elif explicit_text_year:
            year = explicit_text_year
        else:
            year = ref_year
            if m_num > now_dt.month:
                year -= 1
        try:
            last_day = calendar.monthrange(year, m_num)[1]
            d1 = max(1, min(day1, last_day))
            d2 = max(1, min(day2, last_day))
            if d1 > d2:
                d1, d2 = d2, d1
            dt1 = datetime.date(year, m_num, d1)
            dt2 = datetime.date(year, m_num, d2)
            dates.append((m.start(), dt1.strftime("%Y-%m-%d"), None))
            dates.append((m.start() + 1, dt2.strftime("%Y-%m-%d"), None))
            masked_text = masked_text[:m.start()] + ' ' * (m.end() - m.start()) + masked_text[m.end():]
        except ValueError:
            pass

    # 4. Month then day range (e.g. 'July 6 to 10 2026', 'July 6 - 10, 2026', 'July 6th to 10th')
    for m in _MONTH_THEN_DAY_RANGE_PAT.finditer(masked_text):
        m_name = m.group("month").lower()
        m_num = _MONTH_MAP.get(m_name)
        if not m_num:
            continue
        day1 = int(m.group("day1"))
        day2 = int(m.group("day2"))
        year_str = m.group("year")
        if year_str:
            year = int(year_str)
        elif explicit_text_year:
            year = explicit_text_year
        else:
            year = ref_year
            if m_num > now_dt.month:
                year -= 1
        try:
            last_day = calendar.monthrange(year, m_num)[1]
            d1 = max(1, min(day1, last_day))
            d2 = max(1, min(day2, last_day))
            if d1 > d2:
                d1, d2 = d2, d1
            dt1 = datetime.date(year, m_num, d1)
            dt2 = datetime.date(year, m_num, d2)
            dates.append((m.start(), dt1.strftime("%Y-%m-%d"), None))
            dates.append((m.start() + 1, dt2.strftime("%Y-%m-%d"), None))
            masked_text = masked_text[:m.start()] + ' ' * (m.end() - m.start()) + masked_text[m.end():]
        except ValueError:
            pass

    # 5. Natural language specific day patterns (e.g. 'Aug 1, 2026')
    for m in _SPECIFIC_DATE_PAT1.finditer(masked_text):
        m_name = m.group("month").lower()
        m_num = _MONTH_MAP.get(m_name)
        if not m_num:
            continue
        day = int(m.group("day"))
        year_str = m.group("year")
        if year_str:
            year = int(year_str)
        elif explicit_text_year:
            year = explicit_text_year
        else:
            year = ref_year
            if m_num > now_dt.month:
                year -= 1
        try:
            last_day = calendar.monthrange(year, m_num)[1]
            d = max(1, min(day, last_day))
            dt = datetime.date(year, m_num, d)
            dates.append((m.start(), dt.strftime("%Y-%m-%d"), None))
            masked_text = masked_text[:m.start()] + ' ' * (m.end() - m.start()) + masked_text[m.end():]
        except ValueError:
            pass

    # 6. Natural language specific day patterns (e.g. '1st August 2026')
    for m in _SPECIFIC_DATE_PAT2.finditer(masked_text):
        m_name = m.group("month").lower()
        m_num = _MONTH_MAP.get(m_name)
        if not m_num:
            continue
        day = int(m.group("day"))
        year_str = m.group("year")
        if year_str:
            year = int(year_str)
        elif explicit_text_year:
            year = explicit_text_year
        else:
            year = ref_year
            if m_num > now_dt.month:
                year -= 1
        try:
            last_day = calendar.monthrange(year, m_num)[1]
            d = max(1, min(day, last_day))
            dt = datetime.date(year, m_num, d)
            dates.append((m.start(), dt.strftime("%Y-%m-%d"), None))
            masked_text = masked_text[:m.start()] + ' ' * (m.end() - m.start()) + masked_text[m.end():]
        except ValueError:
            pass

    dates.sort(key=lambda x: x[0])

    if len(dates) >= 2:
        start_d, start_t = dates[0][1], dates[0][2]
        end_d, end_t = dates[-1][1], dates[-1][2]
        if start_d > end_d:
            start_d, end_d = end_d, start_d
            start_t, end_t = end_t, start_t
        start_str = f"{start_d} {start_t}" if start_t else f"{start_d} 00:00:00"
        end_str = f"{end_d} {end_t}" if end_t else f"{end_d} 23:59:59"
        return start_str, end_str
    elif len(dates) == 1:
        d, t = dates[0][1], dates[0][2]
        start_str = f"{d} {t}" if t else f"{d} 00:00:00"
        end_str = f"{d} {t}" if t else f"{d} 23:59:59"
        return start_str, end_str

    return None, None

extract_date_range_from_message = extract_date_range

def extract_device_vendor(text: str) -> Optional[str]:
    """Detect and normalize known tracking device vendors."""
    text_l = text.lower()
    # If query mentions transporters or operated by, and does not mention GPS / vendor / tracking / device, don't match Tata/Eicher as vendor
    if bool(re.search(r'\b(?:transporters?|operated\s+by|handled\s+by|fleet)\b', text_l)) and not bool(re.search(r'\b(?:gps|devices?|vendors?|tracking|fitted|installed)\b', text_l)):
        return None
    if re.search(r'\bsecutrak_tp\b', text_l):
        return "Secutrak_TP"
    elif re.search(r'\bwheelseye_bluedart\b', text_l):
        return "wheelseye_bluedart"
    elif re.search(r'\bfleetx(?:_bluedart)?\b', text_l):
        return "Fleetx"
    elif re.search(r'\baxestrack\b', text_l):
        return "Axestrack"
    elif re.search(r'\bgtropy\b', text_l):
        return "Gtropy"
    elif re.search(r'\beicher\b', text_l):
        return "Eicher"
    elif re.search(r'\bg[\s-]?trac\b', text_l):
        return "G-Trac"
    elif re.search(r'\bashokleyland\b', text_l):
        return "ashokleyland"
    elif re.search(r'\bashok(?:\s+leyland)?\b', text_l):
        return "Ashok"
    elif re.search(r'\btatamotors\b', text_l):
        return "tatamotors"
    elif re.search(r'\btata(?:\s+motors?)?\b', text_l):
        return "Tata"
    elif re.search(r'\bkiasaint\b', text_l):
        return "Kiasaint"
    elif re.search(r'\blynkit\b', text_l):
        return "Lynkit"
    elif re.search(r'\bwheelseye\b', text_l):
        return "Wheelseye"
    elif re.search(r'\b(?:secutrak|icici|ilgic)\b', text_l):
        return "Secutrak"
    elif re.search(r'\b(?:3rd[\s-]party|third[\s-]party|3rdparty|thirdparty)\b', text_l):
        return "ThirdParty"
    return None

def detect_vehicle_master(text: str) -> Optional[str]:
    """Detect whether query asks for trips with vehicle inside or outside of vehicle master.
    Returns: 'outside' or 'inside', or None if not applicable.
    """
    text_l = text.lower()
    if bool(re.search(r'\broutes?\b|\bnetwork\s+module\b|\bfleets?\b', text_l)) and not bool(re.search(r'\bvehicles?\b', text_l)):
        return None
    # Check outside first (as it might contain "vehicle master" or "in vehicle master")
    if (
        bool(re.search(r'\b(?:vehicle\s+outside\s+(?:of\s+)?(?:the\s+)?(?:vehicle\s+)?master|outside\s+(?:of\s+)?(?:the\s+)?(?:vehicle\s+)?master|vehicle\s+na\s+in\s+(?:the\s+)?(?:vehicle\s+)?master|na\s+in\s+(?:the\s+)?vehicle\s+master|not\s+in\s+(?:the\s+)?vehicle\s+master|vehicle\s+not\s+in\s+(?:the\s+)?(?:vehicle\s+)?master)\b', text_l))
        or (
            bool(re.search(r'\boutside\s+(?:of\s+)?(?:the\s+)?(?:vehicle\s+)?master\b', text_l))
            and bool(re.search(r'\bvehicles?\b', text_l))
        )
    ):
        return "outside"
    elif (
        bool(re.search(r'\b(?:vehicle\s+inside\s+(?:of\s+)?(?:the\s+)?(?:vehicle\s+)?master|inside\s+(?:of\s+)?(?:the\s+)?(?:vehicle\s+)?master|in\s+(?:the\s+)?vehicle\s+master|vehicle\s+in\s+(?:the\s+)?(?:vehicle\s+)?master)\b', text_l))
        or (
            bool(re.search(r'\binside\s+(?:of\s+)?(?:the\s+)?(?:vehicle\s+)?master\b', text_l))
            and bool(re.search(r'\bvehicles?\b', text_l))
        )
    ):
        return "inside"
    return None

def detect_route_master(text: str) -> Optional[str]:
    """Detect whether query asks for trips with route inside or outside of route master / network module.
    Returns: 'outside' or 'inside', or None if not applicable.
    """
    text_l = text.lower()
    if bool(re.search(r'\bvehicles?\b|\bvehicle\s+master\b|\bfleets?\b', text_l)) and not bool(re.search(r'\broutes?\b|\bnetwork\s+module\b', text_l)):
        return None
    # Check outside first
    if (
        bool(re.search(r'\b(?:route\s+outside\s+(?:of\s+)?(?:the\s+)?(?:route\s+)?master|outside\s+(?:of\s+)?(?:the\s+)?route\s+master|route\s+na\s+in\s+(?:the\s+)?(?:network\s+)?module|na\s+in\s+(?:the\s+)?network\s+module|not\s+in\s+(?:the\s+)?network\s+module|route\s+not\s+in\s+(?:the\s+)?(?:route\s+)?master)\b', text_l))
        or (
            bool(re.search(r'\boutside\s+(?:of\s+)?(?:the\s+)?(?:route\s+)?master\b', text_l))
            and bool(re.search(r'\broutes?\b|\bnetwork\s+module\b', text_l))
        )
    ):
        return "outside"
    elif (
        bool(re.search(r'\b(?:route\s+inside\s+(?:of\s+)?(?:the\s+)?(?:route\s+)?master|inside\s+(?:of\s+)?(?:the\s+)?route\s+master|route\s+in\s+(?:the\s+)?(?:route\s+)?master|(?:present|available)\s+in\s+(?:the\s+)?network\s+module|in\s+(?:the\s+)?network\s+module)\b', text_l))
        or (
            bool(re.search(r'\binside\s+(?:of\s+)?(?:the\s+)?(?:route\s+)?master\b', text_l))
            and bool(re.search(r'\broutes?\b|\bnetwork\s+module\b', text_l))
        )
    ):
        return "inside"
    return None

def detect_fleet_master(text: str) -> Optional[str]:
    """Detect whether query asks for trips with fleet inside or outside of fleet master / network module.
    Returns: 'outside' or 'inside', or None if not applicable.
    """
    text_l = text.lower()
    if not bool(re.search(r'\bfleets?\b', text_l)):
        return None
    # Check outside first
    if (
        bool(re.search(r'\b(?:fleet\s+outside\s+(?:of\s+)?(?:the\s+)?(?:fleet\s+)?master|outside\s+(?:of\s+)?(?:the\s+)?(?:fleet\s+)?master|fleet\s+na\s+in\s+(?:the\s+)?(?:network\s+)?module|na\s+in\s+(?:the\s+)?network\s+module|not\s+in\s+(?:the\s+)?network\s+module|fleet\s+not\s+in\s+(?:the\s+)?(?:fleet\s+)?master)\b', text_l))
        or bool(re.search(r'\boutside\s+(?:of\s+)?(?:the\s+)?(?:fleet\s+)?master\b', text_l))
    ):
        return "outside"
    elif (
        bool(re.search(r'\b(?:fleet\s+inside\s+(?:of\s+)?(?:the\s+)?(?:fleet\s+)?master|inside\s+(?:of\s+)?(?:the\s+)?(?:fleet\s+)?master|fleet\s+in\s+(?:the\s+)?(?:fleet\s+)?master|fleet\s+in\s+(?:the\s+)?(?:network\s+)?module|(?:present|available)\s+in\s+(?:the\s+)?network\s+module)\b', text_l))
        or bool(re.search(r'\binside\s+(?:of\s+)?(?:the\s+)?(?:fleet\s+)?master\b', text_l))
    ):
        return "inside"
    return None

def extract_single_shipment_lookup(text: str) -> Optional[str]:
    """Detects if query asks for details, report, info or information of a specific shipment number or trip ID.
    Returns the shipment number / trip ID string if detected, else None.
    """
    text_l = text.lower()
    # 1. Prefix: e.g. "shipment 12543311", "shipment no 12543311", "trip id 12543311", "trip no 12543311"
    m = re.search(r'\b(?:shipment\s*(?:no|number|id)?|trip\s*(?:id|no|number))\s*[:#]?\s*([A-Za-z0-9_-]{5,25})\b', text_l)
    if m:
        val = m.group(1).strip()
        if bool(re.search(r'\d{3,}', val)) and val not in ("report", "details", "information", "summary", "status"):
            return val

    # 2. Suffix: e.g. "12543311 trip id", "12543311 shipment number", "12543311 shipment"
    m = re.search(r'\b([A-Za-z0-9_-]{5,25})\s*[:#-]?\s*(?:trip\s*(?:id|no|number)|shipment\s*(?:no|number|id)?)\b', text_l)
    if m:
        val = m.group(1).strip()
        if bool(re.search(r'\d{3,}', val)) and val not in ("report", "details", "information", "summary", "status"):
            return val

    # 3. Fallback: query has words like report/details/info and mentions shipment or trip id with a standalone 6-15 digit number
    if bool(re.search(r'\b(?:report|details|info|information|summary)\b', text_l)) and bool(re.search(r'\b(?:shipment|trip\s*id)\b', text_l)):
        m = re.search(r'\b(\d{6,15})\b', text_l)
        if m:
            return m.group(1).strip()

    return None

def detect_top_device_vendors(text: str) -> Optional[Dict[str, str]]:
    """Detects whether the query asks for top tracking device vendors (Fixed GPS, Fixed E-Lock, or Portable E-Lock).
    Returns a dict with 'analytics_type', 'vendor_field', 'label' or None.
    """
    text_l = text.lower()
    if not (
        bool(re.search(r'\b(?:top|major|leading|highest|most\s+used)\b', text_l))
        and bool(re.search(r'\bvendors?\b', text_l))
    ):
        return None

    # Check Portable E-Lock FIRST (handles queries like 'top portable elock GPS vendors')
    if bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?locks?|locks?|elocks?)\b', text_l)):
        return {
            "analytics_type": "portablelock",
            "vendor_field": "vendorportableelock",
            "label": "Portable E-Lock"
        }
    elif bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?locks?|locks?|elocks?)\b', text_l)):
        return {
            "analytics_type": "fixedelock",
            "vendor_field": "vendorfixedelock",
            "label": "Fixed E-Lock"
        }
    elif bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', text_l)):
        return {
            "analytics_type": "gps",
            "vendor_field": "vendorgps",
            "label": "Fixed GPS"
        }
    return None

def detect_date_grouping(text: str) -> Optional[str]:
    """
    Detects whether query requests date grouping (daywise or monthwise).
    Returns "day", "month", or None.
    """
    if not text:
        return None
    text_l = text.lower()

    # Day-wise patterns:
    # "dayswise", "daywise", "day wise", "days wise", "breakdown each day", "breakdown of each day",
    # "details of each day", "each day", "every day", "daily breakdown", "per day", "by day", "daily", "day-by-day"
    if bool(re.search(
        r'\b(?:days?[\s-]?wise|breakdown\s+(?:of\s+|for\s+)?(?:each|every|per)\s+day|breakdown\s+by\s+day|details?\s+of\s+(?:each|every)\s+day|day[\s-]by[\s-]day|daily\s+breakdown|per\s+day|each\s+day|by\s+day|daily)\b',
        text_l
    )):
        return "day"

    # Month-wise patterns:
    # "monthwise", "month wise", "months-wise", "breakdown each month", "breakdown of each month",
    # "details of each month", "each month", "every month", "monthly breakdown", "per month", "by month", "monthly", "month-by-month"
    if bool(re.search(
        r'\b(?:months?[\s-]?wise|breakdown\s+(?:of\s+|for\s+)?(?:each|every|per)\s+month|breakdown\s+by\s+month|details?\s+of\s+(?:each|every)\s+month|month[\s-]by[\s-]month|monthly\s+breakdown|per\s+month|each\s+month|by\s+month|monthly)\b',
        text_l
    )):
        return "month"

    return None

def detect_shipment_methods(text: str) -> list[str]:
    """Detect and normalize all shipment method names present in text, returned in order of appearance.
    Canonical values:
    feeder, empty, surface, sfc, speed, air, network, delivery / Delivery, Pick Up, Pick Up and Delivery, Comm/BZ-Out, Comm/BZ-In.
    """
    # Normalize 'v/s' or 'vs' delimiters (e.g. 'v/sComm/BZ-In' -> 'v/s Comm/BZ-In')
    msg_lower = re.sub(r'v/s(?=[a-zA-Z0-9])', 'v/s ', text.lower())
    msg_lower = re.sub(r'\bvs(?=[a-zA-Z0-9])', 'vs ', msg_lower)
    # Mask out non-shipment-method phrases like 'network module'
    msg_lower = re.sub(r'\bnetwork\s+module\b', lambda m: ' ' * len(m.group(0)), msg_lower)
    msg_lower = re.sub(r'\b(?:major\s+)?(?:route\s+types?|feeder\s+types)\b', lambda m: ' ' * len(m.group(0)), msg_lower)
    msg_lower = re.sub(r'\b(?:major\s+)?shipment\s+methods?\b', lambda m: ' ' * len(m.group(0)), msg_lower)
    methods: list[tuple[str, int]] = []

    # 1. Pick Up and Delivery (e.g. 'pick & delivery', 'pick up and delivery', 'pickup and delivery')
    pud_pat = r'\b(?:pick[\s-]?up|pick)\s*(?:and|&|\+)\s*(?:delivery|del)\b'
    for m in re.finditer(pud_pat, msg_lower):
        methods.append(('Pick Up and Delivery', m.start()))
    # Mask out matches so 'delivery' and 'pick up' don't re-match the same span
    msg_masked = re.sub(pud_pat, lambda m: ' ' * len(m.group(0)), msg_lower)

    # 2. Comm/BZ-Out and In
    for m in re.finditer(r'\bcomm[\s/_-]*bz[\s/_-]*out\b', msg_masked):
        methods.append(('Comm/BZ-Out', m.start()))
    msg_masked = re.sub(r'\bcomm[\s/_-]*bz[\s/_-]*out\b', lambda m: ' ' * len(m.group(0)), msg_masked)

    for m in re.finditer(r'\bcomm[\s/_-]*bz[\s/_-]*in\b', msg_masked):
        methods.append(('Comm/BZ-In', m.start()))
    msg_masked = re.sub(r'\bcomm[\s/_-]*bz[\s/_-]*in\b', lambda m: ' ' * len(m.group(0)), msg_masked)

    # 3. Pick Up (standalone)
    for m in re.finditer(r'\b(?:pick[\s-]?up|pickup)\b', msg_masked):
        methods.append(('Pick Up', m.start()))
    msg_masked = re.sub(r'\b(?:pick[\s-]?up|pickup)\b', lambda m: ' ' * len(m.group(0)), msg_masked)

    # 4. Delivery
    del_pat = r'\b(?:customer[\s_-]+delivery|customer[\s_-]+del|delivery)\b'
    has_pud = any(name == 'Pick Up and Delivery' for name, _ in methods)
    for m in re.finditer(del_pat, msg_masked):
        orig_match = text[m.start():m.end()]
        # If accompanied by Pick Up and Delivery or capitalized in original text, use 'Delivery'
        if has_pud or (orig_match and orig_match[0].isupper()):
            methods.append(('Delivery', m.start()))
        else:
            methods.append(('delivery', m.start()))
    msg_masked = re.sub(del_pat, lambda m: ' ' * len(m.group(0)), msg_masked)

    # 5. Network compound keywords
    for sm, pat in [
        ('air', r'\bair[\s_-]+(?:network|truck)\b'),
        ('sfc', r'\bsfc[\s_-]+(?:network|truck|schedule)\b'),
        ('speed', r'\bspeed[\s_-]+(?:truck|network)\b'),
        ('feeder', r'\bfeeder[\s_-]+(?:truck|network)\b'),
        ('empty', r'\bempty[\s_-]+(?:truck|network)\b'),
        ('surface', r'\bsurface[\s_-]+(?:network|truck)\b'),
    ]:
        for m in re.finditer(pat, msg_masked):
            methods.append((sm, m.start()))
        msg_masked = re.sub(pat, lambda m: ' ' * len(m.group(0)), msg_masked)

    # 6. Standalone keywords
    for sm in ['feeder', 'empty', 'surface', 'sfc', 'speed', 'air']:
        for m in re.finditer(r'\b' + sm + r'\b', msg_masked):
            if sm not in [name for name, _ in methods]:
                methods.append((sm, m.start()))
        msg_masked = re.sub(r'\b' + sm + r'\b', lambda m: ' ' * len(m.group(0)), msg_masked)

    for m in re.finditer(r'\bnetwork\b', msg_masked):
        if 'network' not in [name for name, _ in methods]:
            methods.append(('network', m.start()))
        msg_masked = re.sub(r'\bnetwork\b', lambda m: ' ' * len(m.group(0)), msg_masked)

    # Sort methods by appearance index in original text
    methods.sort(key=lambda x: x[1])

    # If both empty and feeder are detected, normalize to capitalized ['Empty', 'Feeder']
    method_names_lower = [name.lower() for name, _ in methods]
    if 'empty' in method_names_lower and 'feeder' in method_names_lower:
        methods = [
            ('Empty' if name.lower() == 'empty' else ('Feeder' if name.lower() == 'feeder' else name), pos)
            for name, pos in methods
        ]

    return [name for name, _ in methods]

def detect_shipment_method(msg_lower: str) -> str | None:
    methods = detect_shipment_methods(msg_lower)
    return methods[0] if methods else None

_TRANSPORTER_STOP_WORDS = {
    "used", "count", "data", "report", "trips", "trip", "all", "unique",
    "distinct", "the", "a", "an", "is", "and", "or", "name", "names",
    "for", "in", "during", "between", "on", "with", "",
    "recorded", "operating", "operated", "active", "running", "listed",
    "registered", "available", "found", "present", "working", "assigned",
    "involved", "participated", "total", "number", "numbers", "breakdown",
    "details", "summary", "list", "each", "every", "per",
    "highest", "volume", "volumes", "leading", "top", "most", "maximum"
}

def is_top_transporters_query(text: str) -> bool:
    """Detects whether query asks for top transporters by trip volume/count."""
    text_l = text.lower()
    if not bool(re.search(r'\btransporters?\b', text_l)):
        return False
    return bool(
        re.search(r'\b(?:top\s*\d*|highest|maximum|most|leading)\s+(?:\w+\s+){0,3}transporters?\b', text_l)
        or re.search(r'\btransporters?\s+(?:that\s+|which\s+)?(?:recorded|had|have|with|generated)\s+(?:the\s+)?(?:highest|maximum|most|top)\s+(?:trip\s+volumes?|trips?|trip\s+counts?)\b', text_l)
        or re.search(r'\btransporters?\s+by\s+(?:trip\s+volumes?|trip\s+counts?|trips)\b', text_l)
        or re.search(r'\b(?:top\s*\d*|highest)\s+(?:trip\s+volumes?|trip\s+counts?|trips?)\s+(?:by|for|of|across)\s+transporters?\b', text_l)
        or (re.search(r'\btransporters?\b', text_l) and re.search(r'\b(?:highest|top\s*\d*|maximum)\s+trip\s+volumes?\b', text_l))
    )

def extract_specific_transporter(text: str) -> str | list[str] | None:
    """Extract specific transporter name(s) from user message if mentioned."""
    if is_top_transporters_query(text):
        return None
    msg_l = text.lower()

    # 0. Parenthesized list of transporters, e.g. "top 3 Transporters combined (LOCALCASH, DINESH TEMPO SERVICE, TATA)"
    m_trans_paren = re.search(
        r'\b(?:(?:top\s*\d*\s*)?transporters?|operated\s+by|handled\s+by|run\s+by)(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)',
        text,
        re.IGNORECASE
    )
    if not m_trans_paren:
        m_trans_paren = re.search(
            r'\(([^)]+)\)\s*(?:combined|together)?\s*(?:across\s+)?(?:the\s+)?(?:top\s*\d*\s*)?transporters?\b',
            text,
            re.IGNORECASE
        )
    if m_trans_paren:
        raw_list = m_trans_paren.group(1)
        items = [
            x.strip().strip('"\'')
            for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)
        ]
        items = [x for x in items if x and x.lower() not in _TRANSPORTER_STOP_WORDS and len(x) > 1]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # 0b. 3+ comma-separated transporters: e.g. "operated by LOCALCASH, DINESH TEMPO SERVICE, and TATA combined"
    m_multi = re.search(
        r'\b(?:operated\s+by|handled\s+by|run\s+by|across\s+(?:the\s+)?(?:top\s*\d*\s*)?transporters?)\s+(?:transporters?\s+)?'
        r'([A-Za-z0-9&._\-()]+(?:\s*,\s*[A-Za-z0-9&._\-()]+)+(?:\s*,?\s*and\s+[A-Za-z0-9&._\-()]+)?)(?:\s+(?:combined|together))?',
        text,
        re.IGNORECASE
    )
    if m_multi:
        raw_list = m_multi.group(1)
        items = [
            re.sub(r'\s+(?:combined|together)\b', '', x, flags=re.IGNORECASE).strip(' ?.,')
            for x in re.split(r'[,]|(?:\s+and\s+)', raw_list)
        ]
        items = [
            re.sub(r'\s+(?:in|for|from|during|between|with|on)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|\d{4}).*', '', x, flags=re.IGNORECASE).strip(' ?.,')
            for x in items
        ]
        items = [x for x in items if x and x.lower() not in _TRANSPORTER_STOP_WORDS and len(x) > 1]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # 1. Combined multiple transporters: 'operated by X and Y [combined/together]'
    combined_pat = (
        r'\b(?:operated\s+by|handled\s+by|run\s+by)\s+(?:transporters?\s+)?'
        r'([A-Za-z0-9&._\-()]+(?:\s+[A-Za-z0-9&._\-()]+)*)\s+and\s+'
        r'([A-Za-z0-9&._\-()]+(?:\s+[A-Za-z0-9&._\-()]+)*)(?:\s+(?:combined|together))?'
    )
    m_comb = re.search(combined_pat, text, re.IGNORECASE)
    if m_comb:
        t1 = m_comb.group(1).strip()
        t2 = m_comb.group(2).strip()
        t2 = re.sub(r'\s+(?:combined|together)\b', '', t2, flags=re.IGNORECASE).strip(' ?.,')
        t2 = re.sub(r'\s+(?:in|for|from|during|between|with|on)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|\d{4}).*', '', t2, flags=re.IGNORECASE).strip(' ?.,')
        if t1.lower() not in _TRANSPORTER_STOP_WORDS and t2.lower() not in _TRANSPORTER_STOP_WORDS:
            return [t1, t2]

    # If the query asks for unique/distinct/total count of transporters, it's a distinct transporter query,
    # not a filter for a specific transporter (unless explicitly named, e.g. 'named ...' or 'called ...')
    if (
        (
            re.search(r'\b(?:unique|distinct|total\s+number|count\s+of)\s+transporters?\b', msg_l)
            or re.search(r'\bhow\s+many\s+transporters?\b', msg_l)
            or re.search(r'\bshow\s+(?:all\s+)?unique\s+transporters?\b', msg_l)
        )
        and not re.search(r'\b(?:unique|distinct|how\s+many|total\s+number|count\s+of|count)\b.*?\bvehicles?\b', msg_l)
        and not re.search(r'\btransporters?\s+(?:named|called|is|:|=|-)\b', msg_l)
    ):
        return None

    # 2. Single transporter with 'transporter' keyword
    single_pat = r'\btransporters?\s+(?:is\s+|as\s+|named\s+|called\s+|[:\-=]+\s*)?([A-Za-z0-9&._\-()]+(?:\s+[A-Za-z0-9&._\-()]+)*)'
    m = re.search(single_pat, text, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        candidate = re.sub(r'^(?:used\s+by|belonging\s+to|of)\s+', '', candidate, flags=re.IGNORECASE).strip()
        parts = re.split(r'\s+(?:in|for|from|during|between|with|having|on|where|whose|dated?)\b', candidate, flags=re.IGNORECASE)
        candidate = parts[0].strip(".,;: \t\r\n-–—?")
        candidate = re.sub(r'\s+(?:combined|together)\b', '', candidate, flags=re.IGNORECASE).strip(".,;: \t\r\n-–—?")
        month_names = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
        cand_words = set(candidate.lower().split())
        if not cand_words.intersection(month_names) and not any(re.match(r'^(?:19|20)\d{2}$', w) for w in cand_words):
            if candidate.lower() not in _TRANSPORTER_STOP_WORDS and len(candidate) > 1:
                return candidate

    # 3. 'operated by <Name>' without explicit 'transporter' keyword
    op_pat = r'\b(?:operated\s+by|run\s+by|handled\s+by)\s+([A-Za-z0-9&._\-()]+(?:\s+[A-Za-z0-9&._\-()]+)*)'
    m_op = re.search(op_pat, text, re.IGNORECASE)
    if m_op:
        cand = m_op.group(1).strip(' ?.,')
        cand = re.sub(r'\s+(?:in|for|from|during|between|with|on)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|\d{4}).*', '', cand, flags=re.IGNORECASE).strip()
        cand = re.sub(r'\s+(?:combined|together)\b', '', cand, flags=re.IGNORECASE).strip(' ?.,')
        cand_words = set(cand.lower().split())
        month_names = {'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december', 'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec'}
        if not cand_words.intersection(month_names) and not any(re.match(r'^(?:19|20)\d{2}$', w) for w in cand_words):
            if cand.lower() not in _TRANSPORTER_STOP_WORDS and len(cand) > 1:
                return cand

    return None

def detect_regions(text: str) -> list[str]:
    """
    Detects and normalizes geographic regions mentioned in the text.
    Canonical values: "North", "South", "East", "West", "Central",
    "North East", "North West", "South East", "South West".
    """
    text_lower = text.lower()

    # Check if there is explicit region/zone mention or directional preposition with region
    has_region_word = bool(re.search(r'\b(?:regions?|zones?)\b', text_lower))
    has_directional_prep = bool(re.search(
        r'\b(?:in|for|of|across)\s+(?:the\s+)?(north|south|east|west|central|north[\s-]?east|north[\s-]?west|south[\s-]?east|south[\s-]?west)\b',
        text_lower
    ))

    if not (has_region_word or has_directional_prep):
        return []

    found_regions: list[tuple[str, int]] = []
    masked = text_lower

    # 1. Match compound directions first
    compound_patterns = [
        ("North East", r'\b(?:north[\s-]?east|northeast)\b'),
        ("North West", r'\b(?:north[\s-]?west|northwest)\b'),
        ("South East", r'\b(?:south[\s-]?east|southeast)\b'),
        ("South West", r'\b(?:south[\s-]?west|southwest)\b'),
    ]
    for canon, pat in compound_patterns:
        for m in re.finditer(pat, masked):
            found_regions.append((canon, m.start()))
            masked = masked[:m.start()] + ' ' * (m.end() - m.start()) + masked[m.end():]

    # 2. Match cardinal directions
    cardinal_patterns = [
        ("North", r'\bnorth\b'),
        ("South", r'\bsouth\b'),
        ("East", r'\beast\b'),
        ("West", r'\bwest\b'),
        ("Central", r'\bcentral\b'),
    ]
    for canon, pat in cardinal_patterns:
        for m in re.finditer(pat, masked):
            found_regions.append((canon, m.start()))
            masked = masked[:m.start()] + ' ' * (m.end() - m.start()) + masked[m.end():]

    found_regions.sort(key=lambda x: x[1])

    seen = set()
    result = []
    for r, _ in found_regions:
        if r not in seen:
            seen.add(r)
            result.append(r)

    return result

def extract_route_code(text: str) -> str | None:
    """Extract specific route code or name from query (e.g. 'route ABL', 'route: ABL').
    Excludes non-route words like 'intercity', 'intracity', 'feeder', 'trips', etc.
    """
    if detect_route_master(text) is not None:
        return None
    _NON_ROUTE_WORDS = {
        "intercity", "intracity", "network", "local", "feeder", "empty",
        "delivery", "air", "surface", "speed", "category", "type", "types",
        "major", "categories", "method", "methods", "route", "routes",
        "breakdown", "schedule", "trip", "trips", "count", "counts",
        "report", "reports", "details", "data", "for", "in", "of", "to", "from",
        "the", "all", "valid", "closed", "running", "active", "cancelled", "canceled", "cancel",
        "outside", "inside", "master", "na", "not", "module"
    }
    m = re.search(r'\broute\s*(?:code|name)?[:\s]+([A-Za-z0-9_-]+)\b', text, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        if candidate.lower() not in _NON_ROUTE_WORDS:
            return candidate
    return None

def should_ask_running_trips_clarification(message: str) -> bool:
    """Checks whether the query is asking about running trips in a specific past period
    (specific month, last month, last week), but NOT current period (this month, current month,
    this week, present week, present month), and NOT past tense ('was running', 'were running').
    """
    msg_l = message.lower()

    # Must mention running (present or ambiguous)
    has_running = bool(re.search(r'\b(?:is\s+running|running|currently\s+running)\b', msg_l))
    if not has_running:
        return False

    # Exclude past-tense intent like "was running", "were running", "had been running", "operated"
    has_past_running = bool(re.search(r'\b(?:was\s+running|were\s+running|had\s+been\s+running|operated)\b', msg_l))
    if has_past_running:
        return False

    # Exclude queries where closed/completed/valid is already explicitly specified
    if bool(re.search(r'\b(?:closed|completed|valid)\b', msg_l)):
        return False

    # Exclude current/present period: current month, this month, this week, present week, present month, today
    has_current_period = bool(re.search(
        r'\b(?:this|current|present)\s+(?:month|week)\b|\btoday\b|\bthis\s+year\b|\bcurrent\s+year\b',
        msg_l
    ))
    if has_current_period:
        return False

    # Must mention a past period: specific month name, last month, or last week
    _SPECIFIC_MONTHS_PAT = (
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december'
        r'|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b'
    )
    has_past_period = bool(
        re.search(r'\blast\s+(?:month|week)\b|\bpast\s+(?:month|week)\b|\bprevious\s+(?:month|week)\b', msg_l)
        or re.search(_SPECIFIC_MONTHS_PAT, msg_l)
    )
    if not has_past_period:
        return False

    return True

def is_included_cancel_trips_query(message: str) -> bool:
    """Checks if the user is asking whether cancelled trips are included."""
    clean_msg = message.strip().lower()
    if bool(re.search(r'\b(?:show|count|list|download|export|generate|fetch|get|give|display)\b', clean_msg)):
        return False
        
    patterns = [
        r'^(?:is|are|does|did|was|were)?\s*(?:this|it|these)?\s*(?:included|includes|including|include)\s+(?:any\s+)?(?:cancelled|canceled|cancel)\s+trips?[?.]*$',
        r'^(?:is|are|does|did|was|were)\s+(?:any\s+)?(?:cancelled|canceled|cancel)\s+trips?\s+(?:included|include)[?.]*$',
        r'^(?:is|are|does|did|was|were)?\s*(?:this|it)?\s*(?:included|includes|including|include)\s+(?:any\s+)?(?:cancelled|canceled|cancel)[?.]*$',
        r'^(?:cancelled|canceled|cancel)\s+trips?\s+(?:included|part\s+of\s+(?:this|it))[?.]*$',
        r'^(?:are|is|was|were)\s+(?:cancelled|canceled|cancel)\s+(?:trips?\s+)?included[?.]*$',
    ]
    return any(re.search(p, clean_msg) for p in patterns)

def check_has_valid_trip_status_filter(filters: Any) -> bool:
    """Returns True if filters contain {'field': 'tripStatus', 'operator': 'eq', 'value': 'valid'}."""
    if not isinstance(filters, list):
        return False
    for f in filters:
        if isinstance(f, dict):
            field = f.get("field")
            op = f.get("operator")
            val = str(f.get("value", "")).strip().lower()
            if field == "tripStatus" and op == "eq" and val == "valid":
                return True
    return False

def check_history_has_valid_status(history: list) -> Optional[bool]:
    """Inspects history items for existing payload or filters with tripStatus == valid."""
    for item in reversed(history):
        item_dict = item.model_dump() if hasattr(item, "model_dump") else (item if isinstance(item, dict) else {})
        payload = (
            item_dict.get("query_payload")
            or item_dict.get("entities")
            or getattr(item, "query_payload", None)
            or getattr(item, "entities", None)
        )
        if isinstance(payload, dict):
            flts = payload.get("filters")
            if isinstance(flts, list) and len(flts) > 0:
                return check_has_valid_trip_status_filter(flts)
        flts = item_dict.get("filters") or getattr(item, "filters", None)
        if isinstance(flts, list) and len(flts) > 0:
            return check_has_valid_trip_status_filter(flts)
    return None

def detect_device_analytics(message: str) -> Optional[Dict[str, Any]]:
    """
    Detects Device/GPS Related Analytics queries (BASIC Question availability/na/inactive/active/only)
    and returns a dict with:
      - 'analytics_type': e.g. 'gps', 'gpsna', 'gpsonly', 'fixedgpsonly', 'fixedlock', 'fixedlockna',
                          'fixedlockonly', 'fixedelockonly', 'portablelock', 'portablelockna',
                          'portablelockonly', 'portableelockonly', 'withoutanygps', 'withoutorgps'
      - 'device_filters': list of specific device filter dicts to inject into filters
    or None if this is not a device analytics query.
    """
    msg_l = message.lower()

    # Do not intercept Fixed E-Lock and Portable E-Lock combination queries (handled by dedicated flow)
    has_fixed_elock_mention_combo = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixed[\s-]e?locks?\b', msg_l))
    has_portable_mention_combo = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportab?le\b', msg_l))
    has_gps_mention_combo = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l))
    if (
        has_fixed_elock_mention_combo
        and has_portable_mention_combo
        and not has_gps_mention_combo
        and not bool(re.search(r'\b(?:not\s+(?:fixed|portable)|without\s+(?:fixed|portable))\b', msg_l))
        and not bool(re.search(r'\b(?:only|single|sole)\b', msg_l))
    ):
        return None

    # Do not intercept vehicle unique/distinct count queries, transporter queries,
    # or Network vs Local / route breakdown queries.
    if bool(re.search(r'\bvehicles?\b', msg_l)):
        return None
    if bool(re.search(r'\btransporters?\b', msg_l)):
        return None
    if bool(
        re.search(r'\b(?:network\s+(?:vs\.?|versus|and|or|to)\s+local|local\s+(?:vs\.?|versus|and|or|to)\s+network)\b', msg_l)
        or (re.search(r'\bbreakdown\b', msg_l) and re.search(r'\b(?:network|intercity|local|intracity)\b', msg_l))
    ):
        return None

    # Check for withoutanygps:
    # "Trip without any Device or GPS and Fixed Elock and Portable Elock August 2026", "without any device", "trips without any device"
    if not re.search(r'\b(?:no|without)\s+(?:any\s+)?gps\b', msg_l) and bool(
        re.search(r'\bwithout\s+any\s+(?:device|gps\s+and\s+fixed\s+e?lock\s+and\s+portable\s+e?lock)\b', msg_l)
        or (re.search(r'\bwithout\s+(?:any\s+)?devices?\b', msg_l) and not re.search(r'\bgps\b', msg_l))
        or (re.search(r'\bno\s+(?:any\s+)?devices?\b', msg_l) and not re.search(r'\bgps\b', msg_l))
    ):
        return {
            "analytics_type": "withoutanygps",
            "device_filters": []
        }

    # Check for withgps (Fixed GPS, Fixed E-Lock, and Portable E-Lock together):
    # e.g. "How many trips used Fixed GPS, Fixed E-Lock, and Portable E-Lock together Aug 2026?"
    is_devices_together = bool(re.search(r'\btogether\b', msg_l))
    has_gps_mention_together = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_l))
    has_fixed_elock_mention_together = bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', msg_l))
    has_portable_mention_together = bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', msg_l))

    without_m_det = re.search(r'\b(?:without|excluding|except|no|not\s+having)\b\s*(.*)', msg_l)
    without_text_det = without_m_det.group(1) if without_m_det else ""
    without_portable_det = bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', without_text_det)) or bool(re.search(r'\b(?:without|excluding|except)\s+(?:any\s+)?portab?le\b', msg_l))
    without_fixed_elock_det = bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', without_text_det)) or bool(re.search(r'\b(?:without|excluding|except)\s+(?:any\s+)?fixed[\s-]*(?:e[\s-]?)?locks?\b', msg_l))
    without_gps_det = bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', without_text_det)) or bool(re.search(r'\b(?:without|excluding|except)\s+(?:any\s+)?(?:fixed[\s-]*)?gps\b', msg_l))

    if (is_devices_together or without_portable_det) and has_gps_mention_together and has_fixed_elock_mention_together and without_portable_det:
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"
        flts = [
            {"field": "FixedELock", "operator": "eq", "value": dev_val},
            {"field": "PortableELock", "operator": "eq", "value": "na"},
            {"field": "GPS", "operator": "eq", "value": dev_val}
        ] if not is_devices_together else [
            {"field": "GPS", "operator": "eq", "value": dev_val},
            {"field": "FixedELock", "operator": "eq", "value": dev_val},
            {"field": "PortableELock", "operator": "eq", "value": "na"}
        ]
        return {
            "analytics_type": "withgps",
            "device_filters": flts
        }
    elif (is_devices_together or without_fixed_elock_det) and has_gps_mention_together and has_portable_mention_together and without_fixed_elock_det:
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"
        flts = [
            {"field": "FixedELock", "operator": "eq", "value": "na"},
            {"field": "PortableELock", "operator": "eq", "value": dev_val},
            {"field": "GPS", "operator": "eq", "value": dev_val}
        ] if not is_devices_together else [
            {"field": "GPS", "operator": "eq", "value": dev_val},
            {"field": "FixedELock", "operator": "eq", "value": "na"},
            {"field": "PortableELock", "operator": "eq", "value": dev_val}
        ]
        return {
            "analytics_type": "withgps",
            "device_filters": flts
        }
    elif (is_devices_together or without_gps_det) and has_fixed_elock_mention_together and has_portable_mention_together and without_gps_det:
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"
        flts = [
            {"field": "FixedELock", "operator": "eq", "value": dev_val},
            {"field": "PortableELock", "operator": "eq", "value": dev_val},
            {"field": "GPS", "operator": "eq", "value": "na"}
        ] if not is_devices_together else [
            {"field": "GPS", "operator": "eq", "value": "na"},
            {"field": "FixedELock", "operator": "eq", "value": dev_val},
            {"field": "PortableELock", "operator": "eq", "value": dev_val}
        ]
        return {
            "analytics_type": "withgps",
            "device_filters": flts
        }
    elif is_devices_together and (
        (has_gps_mention_together and has_fixed_elock_mention_together and has_portable_mention_together)
        or re.search(r'\b(?:all\s+)?devices?\s+together\b', msg_l)
    ):
        dev_val = "existing"
        if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_l):
            dev_val = "inactive"
        elif re.search(r'\bactive\b', msg_l):
            dev_val = "active"
        elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_l):
            dev_val = "na"

        return {
            "analytics_type": "withgps",
            "device_filters": [
                {"field": "FixedELock", "operator": "eq", "value": dev_val},
                {"field": "PortableELock", "operator": "eq", "value": dev_val},
                {"field": "GPS", "operator": "eq", "value": dev_val}
            ]
        }

    # Check for withoutorgps combinations:
    # Patterns: having X and Y (only) but not Z / with not X but having Y and Z
    has_combo_marker = bool(
        re.search(r'\bnot\s+(?:fixed|portable|portbale|gps)\b', msg_l)
        and re.search(r'\bhaving\b|\bwith\b|\bbut\b', msg_l)
    ) or bool(
        re.search(r'\b(?:without\s+fixed|without\s+portable|without\s+portbale|without\s+gps)\b', msg_l)
        and re.search(r'\bhaving\b|\bwith\b|\bbut\b', msg_l)
    )

    if has_combo_marker:
        is_both_active = bool(re.search(r'\bboth\s+(?:are\s+)?active\b', msg_l))
        is_both_inactive = bool(re.search(r'\bboth\s+(?:are\s+)?(?:inactive|no\s+connectivity)\b', msg_l))

        has_gps_mention = bool(re.search(r'\bgps\b', msg_l))
        has_fixed_elock_mention = bool(re.search(r'\bfixed[\s-]e?locks?\b|\bfixed[\s-]locks?\b', msg_l))
        has_portable_mention = bool(re.search(r'\bportab?le[\s-]e?locks?\b|\bportab?le[\s-]locks?\b|\bportab?le\b', msg_l))

        not_fixed_elock = bool(re.search(r'\bnot\s+fixed\s+e?locks?\b|\bwithout\s+fixed\s+e?locks?\b', msg_l))
        not_portable_elock = bool(re.search(r'\bnot\s+portab?le\s+e?locks?\b|\bwithout\s+portab?le\s+e?locks?\b', msg_l))
        not_gps = bool(re.search(r'\bnot\s+(?:fixed\s+)?gps\b|\bwithout\s+(?:fixed\s+)?gps\b', msg_l))

        # 1. Not Fixed Elock but having GPS and Portable Elock only
        if not_fixed_elock and has_gps_mention and has_portable_mention and not not_portable_elock:
            return {
                "analytics_type": "withoutorgps",
                "device_filters": [
                    {"field": "fixed-gps-status", "operator": "eq", "value": "existing"},
                    {"field": "portable-elock-status", "operator": "eq", "value": "existing"},
                    {"field": "fixed-elock-status", "operator": "eq", "value": "na"}
                ]
            }

        # 2. Not Portable Elock but having GPS and Fixed Elock only
        if not_portable_elock and has_gps_mention and has_fixed_elock_mention and not not_fixed_elock:
            if is_both_active:
                return {
                    "analytics_type": "withoutorgps",
                    "device_filters": [
                        {"field": "fixed-gps-status", "operator": "eq", "value": "existing"},
                        {"field": "GPS", "operator": "eq", "value": "active"},
                        {"field": "fixed-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "FixedELock", "operator": "eq", "value": "active"}
                    ]
                }
            else:
                return {
                    "analytics_type": "withoutorgps",
                    "device_filters": [
                        {"field": "fixed-gps-status", "operator": "eq", "value": "existing"},
                        {"field": "fixed-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "portable-elock-status", "operator": "eq", "value": "na"}
                    ]
                }

        # 3. Not Fixed GPS but having Portable Elock and Fixed Elock only
        if not_gps and has_portable_mention and has_fixed_elock_mention:
            if is_both_active:
                return {
                    "analytics_type": "withoutorgps",
                    "device_filters": [
                        {"field": "portable-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "PortableELock", "operator": "eq", "value": "active"},
                        {"field": "fixed-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "FixedELock", "operator": "eq", "value": "active"}
                    ]
                }
            else:
                return {
                    "analytics_type": "withoutorgps",
                    "device_filters": [
                        {"field": "fixed-gps-status", "operator": "eq", "value": "na"},
                        {"field": "portable-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "fixed-elock-status", "operator": "eq", "value": "existing"}
                    ]
                }

        # 4. Portable Elock and Fixed GPS only (e.g. "not Fixed Elock but Portable Elock and Fixed GPS only" or user's typo "with not Portable Elock but Portable Elock and Fixed GPS only")
        if has_portable_mention and has_gps_mention:
            if is_both_active:
                return {
                    "analytics_type": "withoutorgps",
                    "device_filters": [
                        {"field": "portable-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "PortableELock", "operator": "eq", "value": "active"},
                        {"field": "fixed-gps-status", "operator": "eq", "value": "existing"},
                        {"field": "GPS", "operator": "eq", "value": "active"}
                    ]
                }
            elif is_both_inactive:
                return {
                    "analytics_type": "withoutorgps",
                    "device_filters": [
                        {"field": "portable-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "PortableELock", "operator": "eq", "value": "inactive"},
                        {"field": "fixed-gps-status", "operator": "eq", "value": "existing"},
                        {"field": "GPS", "operator": "eq", "value": "inactive"}
                    ]
                }
            else:
                return {
                    "analytics_type": "withoutorgps",
                    "device_filters": [
                        {"field": "fixed-gps-status", "operator": "eq", "value": "existing"},
                        {"field": "portable-elock-status", "operator": "eq", "value": "existing"},
                        {"field": "fixed-elock-status", "operator": "eq", "value": "na"}
                    ]
                }

    if is_devices_together:
        return None

    # Single device categories
    without_match = re.search(r'\b(?:without|excluding|except|no|not\s+having)\b\s*(.*)', msg_l)
    without_text = without_match.group(1) if without_match else ""

    without_fixed_elock = (
        bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', without_text))
        or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?fixed[\s-]*(?:e[\s-]?)?locks?\b', msg_l))
        or (bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:e[\s-]?)?locks?\b', msg_l)) and not bool(re.search(r'\bportab?le\b', without_text)))
    )
    without_gps = (
        bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', without_text))
        or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:fixed\s+)?gps\b', msg_l))
    )
    without_portable = (
        bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', without_text))
        or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?portab?le\b', msg_l))
        or (bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:e[\s-]?)?locks?\b', msg_l)) and not bool(re.search(r'\bfixed\b', without_text)))
    )

    pos_text = re.sub(r'\b(?:without|excluding|except)\b.*', '', msg_l) if without_match else msg_l

    has_portable = bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\s+gps\b|\bportab?le\b', pos_text)) and not without_portable
    has_fixed_elock = bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', pos_text)) and not without_fixed_elock
    has_gps = bool(re.search(r'\b(?:fixed[\s-]gps|gps|fixed\s+device)\b', pos_text)) and not has_portable and not has_fixed_elock and not without_gps

    is_na = bool(
        re.search(r'\b(?:na|not\s+available|notavailable)\b', msg_l)
        or re.search(r'\b(?:no|without)\s+(?:any\s+)?gps\b', msg_l)
        or re.search(r'\bhad\s+no\s+(?:any\s+)?gps\b', msg_l)
        or re.search(r'\bnon[\s\-_]*gps\b', msg_l)
        or re.search(r'\bnon\s+gps\b', msg_l)
        or re.search(r'\bnogps\b', msg_l)
    )
    is_inactive = bool(
        re.search(r'\b(?:inactive|no\s+connectivity|without\s+connectivity|lost\s+connectivity|zero\s+connectivity|lacking\s+connectivity)\b', msg_l)
    )
    is_active = (
        bool(
            re.search(r'\bactive\b', msg_l)
            or re.search(r'\bconnectivity\s+on\b', msg_l)
            or re.search(r'\b(?:had|with)\s+(?:fixed[\s-]*(?:gps|e?locks?)\s+|portable[\s-]*e?locks?\s+|gps\s+|device\s+)?connectivity\b', msg_l)
            or (bool(re.search(r'\bconnectivity\b', msg_l)) and not is_inactive)
        )
        and not bool(re.search(r'\bactive\s+trips?\b', msg_l))
        and not is_inactive
    )
    is_only_or_single = bool(re.search(r'\b(?:single|only|sole|alone|exclusive|exclusively)\b', msg_l))

    # Download / export queries without explicit device status are report downloads, not device analytics
    if bool(re.search(r'\b(?:download|export)\b', msg_l)) and not (is_na or is_inactive or is_active or is_only_or_single or without_gps or without_fixed_elock or without_portable):
        return None

    # --- PORTABLE E-LOCK ---
    if has_portable:
        if is_only_or_single:
            if is_inactive:
                return {"analytics_type": "portableelockonly", "device_filters": [{"field": "PortableELock", "operator": "eq", "value": "inactive"}]}
            elif is_active:
                return {"analytics_type": "portableelockonly", "device_filters": [{"field": "PortableELock", "operator": "eq", "value": "active"}]}
            else:
                return {"analytics_type": "portablelockonly", "device_filters": []}
        elif is_na:
            return {"analytics_type": "portablelockna", "device_filters": []}
        else:
            if is_inactive:
                return {"analytics_type": "portablelock", "device_filters": [{"field": "PortableELock", "operator": "eq", "value": "inactive"}]}
            elif is_active:
                return {"analytics_type": "portablelock", "device_filters": [{"field": "PortableELock", "operator": "eq", "value": "active"}]}
            else:
                return {"analytics_type": "portablelock", "device_filters": []}

    # --- FIXED E-LOCK ---
    if has_fixed_elock:
        if is_only_or_single:
            if is_inactive:
                return {"analytics_type": "fixedelockonly", "device_filters": [{"field": "FixedELock", "operator": "eq", "value": "inactive"}]}
            elif is_active:
                return {"analytics_type": "fixedelockonly", "device_filters": [{"field": "FixedELock", "operator": "eq", "value": "active"}]}
            else:
                return {"analytics_type": "fixedlockonly", "device_filters": []}
        elif is_na:
            return {"analytics_type": "fixedlockna", "device_filters": []}
        else:
            if is_inactive:
                return {"analytics_type": "fixedlock", "device_filters": [{"field": "FixedELock", "operator": "eq", "value": "inactive"}]}
            elif is_active:
                return {"analytics_type": "fixedlock", "device_filters": [{"field": "FixedELock", "operator": "eq", "value": "active"}]}
            else:
                return {"analytics_type": "fixedlock", "device_filters": []}

    # --- GPS / FIXED GPS ---
    if has_gps:
        if is_na:
            return {"analytics_type": "gpsna", "device_filters": []}
        if is_only_or_single:
            if is_inactive:
                return {"analytics_type": "fixedgpsonly", "device_filters": [{"field": "GPS", "operator": "eq", "value": "inactive"}]}
            elif is_active:
                return {"analytics_type": "fixedgpsonly", "device_filters": [{"field": "GPS", "operator": "eq", "value": "active"}]}
            else:
                return {"analytics_type": "gpsonly", "device_filters": []}
        else:
            if is_inactive:
                return {"analytics_type": "gps", "device_filters": [{"field": "GPS", "operator": "eq", "value": "inactive"}]}
            elif is_active:
                return {"analytics_type": "gps", "device_filters": [{"field": "GPS", "operator": "eq", "value": "active"}]}
            else:
                return {"analytics_type": "gps", "device_filters": []}

    return None

_STOP_LOCATION_WORDS = {
    "the", "a", "an", "all", "device", "devices", "gps", "elock", "vendor", "vendors",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "today", "yesterday", "tomorrow",
    "last", "this", "next", "week", "month", "year", "date", "days", "day", "valid",
    "active", "running", "closed", "completed", "cancelled", "cancel", "location", "locations",
    "is", "are", "was", "were", "be", "been", "being", "as", "at", "in", "on", "of", "for",
    "to", "from", "with", "where", "which", "that", "and", "or", "not", "show", "get", "give",
    "list", "find", "trip", "trips", "secutrak", "icici", "ilgic", "thirdparty",
    "total", "na", "fixed", "portable", "fixedgps", "fixedelock", "portableelock", "status", "count", "tal",
    "comm", "bz", "out", "in", "shipment", "shipments", "method", "methods", "feeder", "empty", "surface",
    "sfc", "speed", "air", "delivery", "pickup", "network", "combined", "together", "both", "either",
    "each", "every", "transporter", "transporters", "fleet", "contractor", "contractors", "vehicle",
    "vehicles", "route", "routes", "master", "module", "category", "type", "types", "priority",
    "ated", "ating", "origin", "originated", "originating", "destination", "source", "site", "hub", "station", "branch", "city",
    "maharashtra", "tamil", "nadu", "karnataka", "delhi", "punjab", "uttar", "pradesh", "gujarat", "rajasthan",
    "haryana", "bihar", "bengal", "kerala", "andhra", "telangana", "odisha", "orissa", "assam", "jharkhand",
    "chhattisgarh", "goa", "himachal", "uttarakhand", "uttaranchal", "sikkim", "tripura", "manipur", "meghalaya",
    "mizoram", "nagaland", "chandigarh", "ladakh", "jammu", "kashmir", "puducherry", "pondicherry"
}

def extract_source_location(text: str) -> Optional[Union[str, List[str]]]:
    """
    Extracts the source/origin location name from user query.
    Handles phrases like 'originated from location BOB', 'from location BOB',
    'origin BOB', 'starting from BOB', 'across Sources combined (HUB, PNQ, BLR)', etc.
    """
    if not text:
        return None

    # Check parenthesized list: e.g. "Sources combined (HUB, PNQ, BLR)"
    m_paren = re.search(
        r'\b(?:(?:top\s*\d*\s*)?sources?|(?:top\s*\d*\s*)?origins?|originat(?:ed|ing|es?)|start(?:ing|ed|s)?)(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)',
        text,
        re.IGNORECASE
    )
    if m_paren:
        raw_list = m_paren.group(1)
        items = [re.sub(r'[^a-zA-Z0-9_-]', '', x).upper() for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)]
        items = [x for x in items if x and x not in _AREA_BRANCH_STOP_WORDS and len(x) > 1]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # Mask out non-location date ranges like 'from 2026-08-01'
    clean_text = re.sub(r'\bfrom\s+\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b', lambda m: ' ' * len(m.group(0)), text, flags=re.IGNORECASE)
    pat = re.compile(
        r'\b(?:(?:source|origin|originat(?:ed|ing|es?))\b\s*(?:is|from|at|as)?|start(?:ing|ed|s)?\s+(?:from|at)|\bfrom\b)\s*[:=]?\s*(?:the\s+|a\s+|an\s+)?(?:location\s+|hub\s+|city\s+|station\s+|branch\s+|site\s+)?([A-Za-z0-9_-]+)(?:\s+(?:location|hub|city|station|branch|site))?\b',
        re.IGNORECASE
    )
    for m in pat.finditer(clean_text):
        val = m.group(1).strip()
        val_lower = val.lower()
        if (
            val_lower not in _STOP_LOCATION_WORDS
            and not re.match(r'^\d{4}', val)
            and not val.isdigit()
            and not re.match(r'^\d{1,4}[-/.]', val)
            and len(val) > 1
        ):
            return val
    return None

def extract_destination_location(text: str) -> Optional[Union[str, List[str]]]:
    """
    Extracts the destination location name from user query.
    Handles phrases like 'destination BOB', 'reaching BOB', 'ends at location BOB', 'to location BOB',
    'across Destinations combined (HUB, PNQ, BLR)', etc.
    """
    if not text:
        return None

    # Check parenthesized list: e.g. "Destinations combined (HUB, PNQ, BLR)"
    m_paren = re.search(
        r'\b(?:(?:top\s*\d*\s*)?destinations?|destinat(?:ed|ing|es?)|reaching|ends?|arrival)(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)',
        text,
        re.IGNORECASE
    )
    if m_paren:
        raw_list = m_paren.group(1)
        items = [re.sub(r'[^a-zA-Z0-9_-]', '', x).upper() for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)]
        items = [x for x in items if x and x not in _AREA_BRANCH_STOP_WORDS and len(x) > 1]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # Mask out non-destination phrases like 'belonged to', 'related to', etc.
    clean_text = re.sub(
        r'\b(?:belong(?:ed|ing|s)?|relat(?:ed|ing|es?)|pertain(?:ed|ing|s)?|prior|due|according|thanks|similar|close|next|equal)\s+to\b',
        lambda m: ' ' * len(m.group(0)),
        text,
        flags=re.IGNORECASE
    )
    # Mask out date ranges like 'to 2026-08-31'
    clean_text = re.sub(r'\bto\s+\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b', lambda m: ' ' * len(m.group(0)), clean_text, flags=re.IGNORECASE)
    pat = re.compile(
        r'\b(?:(?:destination|destinat(?:ed|ing|es?))\b\s*(?:is|to|at|as)?|reaching|ends?\s+(?:at|on)?|arrival\s+(?:at)?|\bto\b)\s*[:=]?\s*(?:the\s+|a\s+|an\s+)?(?:location\s+|hub\s+|city\s+|station\s+|branch\s+|site\s+)?([A-Za-z0-9_-]+)(?:\s+(?:location|hub|city|station|branch|site))?\b',
        re.IGNORECASE
    )
    for m in pat.finditer(clean_text):
        val = m.group(1).strip()
        val_lower = val.lower()
        if (
            val_lower not in _STOP_LOCATION_WORDS
            and not re.match(r'^\d{4}', val)
            and not val.isdigit()
            and not re.match(r'^\d{1,4}[-/.]', val)
            and len(val) > 1
        ):
            return val
    return None

_INDIAN_STATES_MAP = [
    ("Andhra Pradesh", r'\bandhra(?:\s+pradesh)?\b'),
    ("Arunachal Pradesh", r'\barunachal(?:\s+pradesh)?\b'),
    ("Assam", r'\bassam\b'),
    ("Bihar", r'\bbihar\b'),
    ("Chhattisgarh", r'\bchhatt?isgarh\b'),
    ("Goa", r'\bgoa\b'),
    ("Gujarat", r'\bgujarat\b'),
    ("Haryana", r'\bharyana\b'),
    ("Himachal Pradesh", r'\bhimachal(?:\s+pradesh)?\b'),
    ("Jharkhand", r'\bjharkhand\b'),
    ("Karnataka", r'\bkarnataka\b'),
    ("Kerala", r'\bkerala\b'),
    ("Madhya Pradesh", r'\bmadhya\s+pradesh\b'),
    ("Maharashtra", r'\bmaharashtra\b'),
    ("Manipur", r'\bmanipur\b'),
    ("Meghalaya", r'\bmeghalaya\b'),
    ("Mizoram", r'\bmizoram\b'),
    ("Nagaland", r'\bnagaland\b'),
    ("Odisha", r'\b(?:odisha|orissa)\b'),
    ("Punjab", r'\bpunjab\b'),
    ("Rajasthan", r'\brajasthan\b'),
    ("Sikkim", r'\bsikkim\b'),
    ("Tamil Nadu", r'\btamil\s*nadu\b'),
    ("Telangana", r'\btelangana\b'),
    ("Tripura", r'\btripura\b'),
    ("Uttar Pradesh", r'\buttar\s+pradesh\b'),
    ("Uttarakhand", r'\b(?:uttarakhand|uttaranchal)\b'),
    ("West Bengal", r'\b(?:west\s+bengal|bengal)\b'),
    ("Delhi", r'\b(?:new\s+)?delhi\b'),
    ("Jammu and Kashmir", r'\b(?:jammu\s+(?:and|&)\s+kashmir|j&k|kashmir)\b'),
    ("Ladakh", r'\bladakh\b'),
    ("Chandigarh", r'\bchandigarh\b'),
    ("Puducherry", r'\b(?:puducherry|pondicherry)\b'),
]

def detect_indian_states(text: str) -> list[str]:
    """
    Detects Indian states and UTs mentioned in text and returns canonical names
    ordered by their position of appearance in the text.
    """
    if not text:
        return []
    text_lower = text.lower()
    matches = []
    for canon, pat in _INDIAN_STATES_MAP:
        for m in re.finditer(pat, text_lower):
            matches.append((canon, m.start()))
    matches.sort(key=lambda x: x[1])
    seen = set()
    result = []
    for canon, _ in matches:
        if canon not in seen:
            seen.add(canon)
            result.append(canon)
    return result

_ATD_ATA_VALUE_MAP = {
    "notcaptured": "notcaptured",
    "not captured": "notcaptured",
    "captured": "captured",
    "missing": "missing",
    "notmissing": "notmissing",
    "not missing": "notmissing",
    "false": "notcaptured",
    "true": "captured",
    "0": "notcaptured",
    "1": "captured",
    "no": "notcaptured",
    "yes": "captured",
    "null": "notcaptured",
    "none": "notcaptured",
    "na": "notcaptured",
}

def extract_distance_filters(message: str) -> List[Dict[str, str]]:
    """
    Extracts distance filters from message based on device context:
    - Fixed GPS or GPS -> fixedGpsDistanceKm
    - Fixed E-Lock -> fixedLockDistanceKm
    - Portable E-Lock -> portableLockDistanceKm
    - Distance only (no device) -> tripDistance

    Supports:
    - between X [km] and Y [km] -> gte: X, lte: Y
    - exceeding / greater than / more than / above / over X [km] -> gte: X
    - less than / under / below / up to X [km] -> lte: X
    """
    if not message:
        return []
    msg_l = message.lower()
    if not re.search(r'\bdistance\b|\bkm\b|\bkms\b|\bkilometers?\b', msg_l):
        return []

    is_portable_elock = bool(re.search(r'\b(?:portab?le[\s-]*(?:e[\s-]?)?locks?|portablelock|portableelock)\b', msg_l))
    is_fixed_elock = bool(re.search(r'\b(?:fixed[\s-]*(?:e[\s-]?)?locks?|fixedlock|fixedelock)\b', msg_l))
    is_gps = bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', msg_l))

    if is_portable_elock:
        field_name = "portableLockDistanceKm"
    elif is_fixed_elock:
        field_name = "fixedLockDistanceKm"
    elif is_gps:
        field_name = "fixedGpsDistanceKm"
    else:
        field_name = "tripDistance"

    dist_filters = []
    # 1. Between X and Y km
    m_between = re.search(r'\b(?:between|from)\s+(\d+(?:\.\d+)?)\s*(?:km|kms|kilometers?)?\s*(?:to|and|-)\s*(\d+(?:\.\d+)?)\s*(?:km|kms|kilometers?)\b', msg_l)
    if not m_between and re.search(r'\bdistance\b', msg_l):
        m_between = re.search(r'\bdistance\s+(?:of\s+)?(?:between|from)\s+(\d+(?:\.\d+)?)\s*(?:km|kms|kilometers?)?\s*(?:to|and|-)\s*(\d+(?:\.\d+)?)\b', msg_l)
    if m_between:
        dist_filters.append({"field": field_name, "operator": "gte", "value": m_between.group(1)})
        dist_filters.append({"field": field_name, "operator": "lte", "value": m_between.group(2)})
        return dist_filters

    # 2. Exceeding / Greater than / More than / Above / Over X km
    m_gte = re.search(r'\b(?:exceeding|greater\s+than|more\s+than|above|over|>|>=)\s*(\d+(?:\.\d+)?)\s*(?:km|kms|kilometers?)\b', msg_l)
    if not m_gte and re.search(r'\bdistance\b', msg_l):
        m_gte = re.search(r'\bdistance\s*(?:exceeding|greater\s+than|more\s+than|above|over|>|>=)\s*(\d+(?:\.\d+)?)\b', msg_l)
    if m_gte:
        dist_filters.append({"field": field_name, "operator": "gte", "value": m_gte.group(1)})
        return dist_filters

    # 3. Less than / Under / Below / Up to X km
    m_lte = re.search(r'\b(?:less\s+than|under|below|up\s+to|<=|<)\s*(\d+(?:\.\d+)?)\s*(?:km|kms|kilometers?)\b', msg_l)
    if not m_lte and re.search(r'\bdistance\b', msg_l):
        m_lte = re.search(r'\bdistance\s*(?:less\s+than|under|below|up\s+to|<=|<)\s*(\d+(?:\.\d+)?)\b', msg_l)
    if m_lte:
        dist_filters.append({"field": field_name, "operator": "lte", "value": m_lte.group(1)})
        return dist_filters

    return dist_filters

_AREA_BRANCH_STOP_WORDS = {
    "CODE", "WISE", "BREAKDOWN", "LIST", "NAME", "NAMES", "REPORT",
    "RECORDED", "UTILIZED", "FOUND", "OPERATED", "USED", "ACTIVE",
    "ON", "IN", "DURING", "WITH", "WITHOUT", "UNDER", "FOR", "OF",
    "ACROSS", "THERE", "WERE", "ARE", "HAD", "HAVE", "HAS", "TRIPS",
    "TRIP", "COUNT", "TOTAL", "UNIQUE", "DISTINCT", "COMBINED",
    "TOGETHER", "TOP", "BETWEEN", "BOTH", "EITHER", "ALL", "AND",
    "OR", "BRANCH", "BRANCHES", "AREA", "AREAS"
}

def extract_area(message: str) -> Optional[Union[str, List[str]]]:
    """
    Extracts area code(s) from message.
    e.g. 'under top Area DEL' -> 'DEL'
         'under Area BLR' -> 'BLR'
         'across the top 5 Areas combined (DEL, BOM, BLR, MAA, CCU)' -> ['DEL', 'BOM', 'BLR', 'MAA', 'CCU']
         'Areas (DEL, BOM, BLR)' -> ['DEL', 'BOM', 'BLR']
         'Areas DEL, BOM, and CCU combined' -> ['DEL', 'BOM', 'CCU']
    """
    if not message:
        return None
    if re.search(r'\b(?:unique|distinct)\s+areas?\b', message, re.IGNORECASE):
        return None

    # 1. Parenthesized list of areas, e.g. "Areas combined (DEL, BOM, BLR)" or "(DEL, BOM, BLR) combined"
    m_paren = re.search(
        r'\b(?:(?:top\s*\d*\s*)?areas?)(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)',
        message,
        re.IGNORECASE
    )
    if not m_paren:
        m_paren = re.search(
            r'\(([^)]+)\)\s*(?:combined|together)?\s*(?:across\s+)?(?:the\s+)?(?:top\s*\d*\s*)?areas?\b',
            message,
            re.IGNORECASE
        )
    if m_paren:
        raw_list = m_paren.group(1)
        items = [
            re.sub(r'[^a-zA-Z0-9_-]', '', x).upper()
            for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)
        ]
        items = [x for x in items if x and x not in _AREA_BRANCH_STOP_WORDS and len(x) >= 2]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # 2. Comma/and-separated list with combined or colon
    m_list = re.search(
        r'\b(?:top\s*\d*\s*)?areas?\s*(?:combined\s*[:=-]?\s*|[:=-]\s*)([a-zA-Z0-9_,\s\-\/&]+?)(?:\s+(?:combined|together)|\s+(?:in|for|from|during|between|dated?)\b|\s*\?|$)',
        message,
        re.IGNORECASE
    )
    if not m_list:
        m_list = re.search(
            r'\b(?:across|for|under|in)\s+(?:the\s+)?(?:top\s*\d*\s*)?areas?\s+([a-zA-Z0-9_,\s\-\/&]+?)\s+(?:combined|together)\b',
            message,
            re.IGNORECASE
        )
    if m_list:
        raw_list = m_list.group(1)
        items = [
            re.sub(r'[^a-zA-Z0-9_-]', '', x).upper()
            for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)
        ]
        items = [x for x in items if x and x not in _AREA_BRANCH_STOP_WORDS and len(x) >= 2]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # 3. Single area code
    m = re.search(r'\b(?:top\s+)?areas?\s*(?:[:=-]\s*|\s+)([a-zA-Z]{2,10})\b', message, re.IGNORECASE)
    if m:
        val = m.group(1).upper()
        if val not in _AREA_BRANCH_STOP_WORDS:
            return val
    return None

def extract_branch(message: str) -> Optional[Union[str, List[str]]]:
    """
    Extracts branch code(s) from message.
    e.g. 'under Branch PNQ' -> 'PNQ'
         'under top Branch HUB' -> 'HUB'
         'across the top 5 Branches combined (HUB, PNQ, BLR, HYD, NBM)' -> ['HUB', 'PNQ', 'BLR', 'HYD', 'NBM']
         'Branches (HUB, PNQ, BLR)' -> ['HUB', 'PNQ', 'BLR']
         'Branches HUB, PNQ, and BLR combined' -> ['HUB', 'PNQ', 'BLR']
    """
    if not message:
        return None
    if re.search(r'\b(?:unique|distinct)\s+branch(?:es)?\b', message, re.IGNORECASE):
        return None

    # 1. Parenthesized list of branches, e.g. "Branches combined (HUB, PNQ, BLR, HYD, NBM)" or "(HUB, PNQ, BLR) combined"
    m_paren = re.search(
        r'\b(?:(?:top\s*\d*\s*)?branch(?:es)?)(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)',
        message,
        re.IGNORECASE
    )
    if not m_paren:
        m_paren = re.search(
            r'\(([^)]+)\)\s*(?:combined|together)?\s*(?:across\s+)?(?:the\s+)?(?:top\s*\d*\s*)?branch(?:es)?\b',
            message,
            re.IGNORECASE
        )
    if m_paren:
        raw_list = m_paren.group(1)
        items = [
            re.sub(r'[^a-zA-Z0-9_-]', '', x).upper()
            for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)
        ]
        items = [x for x in items if x and x not in _AREA_BRANCH_STOP_WORDS and len(x) >= 2]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # 2. Comma/and-separated list with combined or colon
    m_list = re.search(
        r'\b(?:top\s*\d*\s*)?branch(?:es)?\s*(?:combined\s*[:=-]?\s*|[:=-]\s*)([a-zA-Z0-9_,\s\-\/&]+?)(?:\s+(?:combined|together)|\s+(?:in|for|from|during|between|dated?)\b|\s*\?|$)',
        message,
        re.IGNORECASE
    )
    if not m_list:
        m_list = re.search(
            r'\b(?:across|for|under|in)\s+(?:the\s+)?(?:top\s*\d*\s*)?branch(?:es)?\s+([a-zA-Z0-9_,\s\-\/&]+?)\s+(?:combined|together)\b',
            message,
            re.IGNORECASE
        )
    if m_list:
        raw_list = m_list.group(1)
        items = [
            re.sub(r'[^a-zA-Z0-9_-]', '', x).upper()
            for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)
        ]
        items = [x for x in items if x and x not in _AREA_BRANCH_STOP_WORDS and len(x) >= 2]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]

    # 3. Single branch
    m = re.search(r'\b(?:top\s+)?branch(?:es)?\s*(?:[:=-]\s*|\s+)([a-zA-Z0-9_-]{2,10})\b', message, re.IGNORECASE)
    if m:
        val = m.group(1).upper()
        if val not in _AREA_BRANCH_STOP_WORDS:
            return val
    return None

def extract_route(message: str) -> Optional[Union[str, List[str]]]:
    """
    Extracts route name(s) from user query.
    e.g. 'Route R1' -> 'R1'
         'across the top 3 Routes combined (R1, R2, R3)' -> ['R1', 'R2', 'R3']
    """
    if not message:
        return None
    if re.search(r'\b(?:unique|distinct)\s+routes?\b', message, re.IGNORECASE):
        return None
    m_paren = re.search(
        r'\b(?:(?:top\s*\d*\s*)?routes?)(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)',
        message,
        re.IGNORECASE
    )
    if m_paren:
        raw_list = m_paren.group(1)
        items = [x.strip() for x in re.split(r'[,/]|(?:\s+and\s+)', raw_list)]
        items = [x for x in items if x and len(x) > 1]
        if len(items) > 1:
            return items
        elif len(items) == 1:
            return items[0]
    m = re.search(r'\broutes?\s+([A-Za-z0-9_\-\/]+)\b', message, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        if val.lower() not in ("types", "type", "category", "categories", "master", "inside", "outside", "combined", "together"):
            return val
    return None

def extract_vehicle_list(message: str) -> Optional[List[str]]:
    """
    Extracts multiple vehicle numbers from message.
    e.g. 'Vehicles combined (UP32AB1234, DL01CD5678)' -> ['UP32AB1234', 'DL01CD5678']
    """
    if not message:
        return None
    m_paren = re.search(r'\bvehicles?(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)', message, re.IGNORECASE)
    if m_paren:
        raw_list = m_paren.group(1)
        vehs = re.findall(r'\b[A-Za-z]{2}\s*\d{1,2}\s*[A-Za-z]{1,3}\s*\d{4}\b', raw_list)
        if vehs:
            return [re.sub(r'\s+', '', v).upper() for v in vehs]
    all_vehs = re.findall(r'\b[A-Za-z]{2}\s*\d{1,2}\s*[A-Za-z]{1,3}\s*\d{4}\b', message)
    if len(all_vehs) > 1:
        return [re.sub(r'\s+', '', v).upper() for v in all_vehs]
    return None

def extract_region(message: str) -> Optional[Union[str, List[str]]]:
    """
    Extracts region(s) from message.
    e.g. 'North and South regions combined' -> ['North', 'South']
         'Regions (North, South)' -> ['North', 'South']
    """
    if not message:
        return None
    m_paren = re.search(r'\bregions?(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)', message, re.IGNORECASE)
    if m_paren:
        raw = m_paren.group(1).lower()
        regs = []
        for r in ["north east", "north west", "south east", "south west", "north", "south", "east", "west", "central"]:
            if r in raw and r.title() not in regs:
                regs.append(r.title())
        if len(regs) > 1:
            return regs
        elif len(regs) == 1:
            return regs[0]
    regs = []
    msg_l = message.lower()
    for r in ["north east", "north west", "south east", "south west", "north", "south", "east", "west", "central"]:
        if re.search(rf'\b{r}\b', msg_l) and ("region" in msg_l or "regions" in msg_l):
            if r.title() not in regs:
                regs.append(r.title())
    if len(regs) > 1:
        return regs
    elif len(regs) == 1:
        return regs[0]
    return None

def extract_shipment_methods(message: str) -> Optional[Union[str, List[str]]]:
    """
    Extracts shipment methods from parenthesized or combined queries.
    e.g. 'Shipment Methods combined (speed, empty)' -> ['speed', 'empty']
    """
    if not message:
        return None
    m_paren = re.search(r'\b(?:shipment\s+methods?|feeder\s+types?|route\s+types?)(?:\s+(?:combined|together))?\s*[:=-]?\s*\(([^)]+)\)', message, re.IGNORECASE)
    if m_paren:
        raw = m_paren.group(1).lower()
        sm_map = {"air": "air", "sfc": "sfc", "speed": "speed", "feeder": "feeder", "empty": "empty", "delivery": "delivery", "pickup": "Pick Up"}
        sms = []
        for k, v in sm_map.items():
            if k in raw and v not in sms:
                sms.append(v)
        if len(sms) > 1:
            return sms
        elif len(sms) == 1:
            return sms[0]
    return None

def detect_unique_or_list_field(message: str) -> Optional[str]:
    """
    Detects if the query is asking for unique count or a list of a specific field:
    - route: unique routes, routes utilized, list of routes
    - destination: unique destination locations, unique destinations, list of destinations
    - source: unique source locations, unique origins, list of sources
    - area: unique areas, list of areas
    - branch: unique branches, list of branches
    - transporter: unique transporters, list of transporters
    """
    if not message:
        return None
    msg_l = message.lower()

    has_unique = bool(re.search(r'\b(?:unique|distinct|different|how\s+many\s+unique|how\s+many\s+distinct)\b', msg_l))
    has_list = bool(re.search(r'\b(?:lis|list|show\s+all|get\s+all|which)\b', msg_l)) and not bool(re.search(r'\b(?:how\s+many|total\s+count)\b', msg_l))

    if not (has_unique or has_list):
        return None

    # 1. Routes
    if re.search(r'\b(?:routes?|route\s+names?)\b', msg_l) and not re.search(r'\broute\s+(?:master|type|category)\b', msg_l):
        return "route"

    # 2. Destination
    if re.search(r'\b(?:destination\s+locations?|destinations?|dest\s+locations?|destination\s+points?|destination\s+sites?)\b', msg_l):
        return "destination"

    # 3. Source / Origin
    if re.search(r'\b(?:source\s+locations?|sources?|origin\s+locations?|origins?|start\s+locations?)\b', msg_l):
        return "source"

    # 4. Area
    if re.search(r'\b(?:areas?)\b', msg_l):
        return "area"

    # 5. Branch
    if re.search(r'\b(?:branch(?:es)?)\b', msg_l):
        return "branch"

    # 6. Transporter
    if re.search(r'\b(?:transporters?)\b', msg_l) and not re.search(r'\b(?:vehicles?|veh)\b', msg_l):
        return "transporter"

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

        if is_included_cancel_trips_query(message):
            has_valid = check_history_has_valid_status(history)
            if has_valid is None:
                prev_user_msg = None
                for h in reversed(history):
                    role = h.get("role") if isinstance(h, dict) else getattr(h, "role", None)
                    content = h.get("content") if isinstance(h, dict) else getattr(h, "content", None)
                    if role == "user" and content and not is_included_cancel_trips_query(content):
                        prev_user_msg = content
                        break
                if prev_user_msg:
                    try:
                        prev_res = await self.detect(prev_user_msg, [], session_id)
                        prev_filters = prev_res.get("entities", {}).get("filters", [])
                        has_valid = check_has_valid_trip_status_filter(prev_filters)
                    except Exception:
                        has_valid = False
                else:
                    has_valid = False

            return {
                "intent": "cancel_trips_check",
                "entities": {
                    "has_valid_trip_status": has_valid,
                    "reply": "no" if has_valid else "yes"
                },
                "source": "rule",
                "llm_status": "bypassed",
                "debug": {
                    "source": "rule",
                    "fallback": False,
                    "llm_attempted": False,
                    "llm_success": None,
                    "llm_status": "bypassed",
                    "error_type": None,
                    "error": None
                }
            }

        # Check if this is a follow-up answer to a running trips clarification request
        _RUNNING_CLARIFICATION_MARKER = "are you asking about closed and completed or current running"
        is_running_follow_up = False
        if history and len(history) >= 1:
            last_assistant_msg = history[-1]
            last_content = last_assistant_msg.get("content", "").lower() if isinstance(last_assistant_msg, dict) else getattr(last_assistant_msg, "content", "").lower()
            last_role = last_assistant_msg.get("role") if isinstance(last_assistant_msg, dict) else getattr(last_assistant_msg, "role", None)
            if (
                last_role == "assistant"
                and (_RUNNING_CLARIFICATION_MARKER in last_content or "cloased and completed" in last_content)
            ):
                is_running_follow_up = True

        if is_running_follow_up and history:
            prev_payload = None
            for item in reversed(history):
                payload = (
                    item.get("query_payload")
                    or item.get("entities")
                    or getattr(item, "query_payload", None)
                    or getattr(item, "entities", None)
                )
                if isinstance(payload, dict) and isinstance(payload.get("filters"), list):
                    import copy
                    prev_payload = copy.deepcopy(payload)
                    break

            msg_ans = message.strip().lower()
            if re.search(r'\b(?:closed|completed|close|complete)\b', msg_ans):
                chosen_status = "valid"
            elif re.search(r'\b(?:current|running|unclosed)\b', msg_ans):
                chosen_status = "running"
            else:
                chosen_status = "valid"

            prev_user_msg = None
            for item in reversed(history):
                r = item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
                c = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
                if r == "user" and c:
                    prev_user_msg = c
                    break

            if (not prev_payload or len(prev_payload.get("filters", [])) <= 2) and prev_user_msg:
                # Re-detect entities from the previous user message
                orig_res = await self.detect(message=prev_user_msg, history=[], session_id=session_id)
                orig_payload = orig_res.get("entities")
                if isinstance(orig_payload, dict) and isinstance(orig_payload.get("filters"), list):
                    import copy
                    prev_payload = copy.deepcopy(orig_payload)

            if prev_payload:
                found_status = False
                for f in prev_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        f["value"] = chosen_status
                        f["operator"] = "eq"
                        found_status = True
                        break
                if not found_status:
                    prev_payload.setdefault("filters", []).append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": chosen_status
                    })

                def _running_followup_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 3
                    elif field == "runDate" and op == "gte":
                        return 4
                    elif field == "runDate" and op == "lte":
                        return 5
                    elif field in ("source", "destination", "From", "To"):
                        return 6
                    elif field in ("Route", "route", "RouteName", "routeName"):
                        return 7
                    elif field in ("transporter", "Transporter"):
                        return 8
                    elif field == "region":
                        return 9
                    elif field == "shipmentMethod":
                        return 10
                    elif field in ("routeCategory", "routeType", "tripType"):
                        return 11
                    elif field == "priority":
                        return 12
                    elif field in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                        return 13
                    elif field in ("atd", "ata", "ATD", "ATA"):
                        return 14
                    return 20

                prev_payload["filters"].sort(key=_running_followup_sort_key)

                log_intent_detected(session_id, "trip_report")
                log_entities_extracted(session_id, prev_payload)
                return {
                    "intent": "trip_report",
                    "entities": prev_payload,
                    "source": "rule",
                    "llm_status": "bypassed",
                    "debug": {
                        "source": "rule",
                        "fallback": False,
                        "llm_attempted": False,
                        "llm_success": None,
                        "llm_status": "bypassed",
                        "error_type": None,
                        "error": None
                    }
                }

        # Check if this is a follow-up answer to a date range clarification request.
        # IMPORTANT: match ONLY the specific clarification question the bot asks when
        # it needs a date range from the user.  Do NOT match on ordinary result responses
        # like "Total 19 trips found for date range from …" — those also contain "date
        # range" but are final answers, not pending questions.
        _DATE_CLARIFICATION_MARKER = "please provide the date range (start date and end date)"
        is_follow_up = False
        prev_user_msg = None
        prev_filters = []
        if history and len(history) >= 1:
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

        if is_follow_up and history:
            for item in reversed(history):
                payload = (
                    item.get("query_payload")
                    or item.get("entities")
                    or getattr(item, "query_payload", None)
                    or getattr(item, "entities", None)
                )
                if isinstance(payload, dict) and isinstance(payload.get("filters"), list):
                    prev_filters = payload.get("filters")
                    break
                flts = item.get("filters") or getattr(item, "filters", None)
                if isinstance(flts, list) and flts:
                    prev_filters = flts
                    break

        if is_follow_up and prev_user_msg:
            cleaned_prev = prev_user_msg.rstrip(". ")
            message = f"{cleaned_prev} for date range {message}"

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
                r'\bcurrent\s+(?:week|month|year)\b',
                r'\btoday\b',
                r'\byesterday\b',
                r'\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b',
                r'\b\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b',
            ]
            _msg_lower_tmp = message.lower()
            _msg_is_date_ref = any(re.search(p, _msg_lower_tmp) for p in _DATE_REF_PATTERNS)
            _msg_is_short = len(message.strip().split()) <= 8

            # Ensure this is truly a follow-up date refinement and not a standalone query
            _is_standalone_query = bool(re.search(
                r'\b(?:generate|download|show|count|get|fetch)\b'
                r'|\b(?:trips?|devices?|report|data|wheelseye|secutrak|icici|ilgic|thirdparty|3rd[\s-]party)\b'
                r'|\b(?:intracity|intercity|feeder|air|surface|speed|sfc|delivery|pickup|pick\s+up)\b',
                _msg_lower_tmp
            ))

            if not _is_standalone_query and _msg_is_date_ref and _msg_is_short:
                # Find the last substantive user query in history (more than 3 words)
                for _item in reversed(history):
                    if _item.get("role") == "user":
                        _prev_q = _item.get("content", "").strip()
                        if len(_prev_q.split()) > 3:
                            message = f"{_prev_q} {message}"
                            break

        # 1. Compute dynamic date context
        now = datetime.datetime.now()

        # Extract dates using regex as a reliable fallback
        extracted_start, extracted_end = extract_date_range(message, now)
        
        # Calculate today
        today_start = now.strftime("%Y-%m-%d 00:00:00")
        today_end = now.strftime("%Y-%m-%d 23:59:59")

        # Calculate yesterday
        yesterday = now - datetime.timedelta(days=1)
        yesterday_start = yesterday.strftime("%Y-%m-%d 00:00:00")
        yesterday_end = yesterday.strftime("%Y-%m-%d 23:59:59")

        # Calculate this week (Monday to Sunday of the current week)
        try:
            this_week_monday = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            this_week_sunday = this_week_monday + datetime.timedelta(days=6)
            this_week_start = this_week_monday.strftime("%Y-%m-%d 00:00:00")
            this_week_end = this_week_sunday.strftime("%Y-%m-%d 23:59:59")
        except Exception:
            this_week_start = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
            this_week_end = now.strftime("%Y-%m-%d 23:59:59")

        # Calculate last week (relative to current date, matching July 9 to July 15 when today is July 23)
        last_week_start = (now - datetime.timedelta(days=14)).strftime("%Y-%m-%d 00:00:00")
        last_week_end = (now - datetime.timedelta(days=8)).strftime("%Y-%m-%d 23:59:59")

        # Calculate this month (current calendar month)
        try:
            first_day_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_day_num = calendar.monthrange(now.year, now.month)[1]
            last_day_this_month = now.replace(day=last_day_num, hour=23, minute=59, second=59, microsecond=0)
            this_month_start = first_day_this_month.strftime("%Y-%m-%d 00:00:00")
            this_month_end = last_day_this_month.strftime("%Y-%m-%d 23:59:59")
        except Exception:
            this_month_start = now.strftime("%Y-%m-01 00:00:00")
            this_month_end = now.strftime("%Y-%m-28 23:59:59")
        
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
            f"- THIS_WEEK_START: {this_week_start}\n"
            f"- THIS_WEEK_END: {this_week_end}\n"
            f"- LAST_WEEK_START: {last_week_start}\n"
            f"- LAST_WEEK_END: {last_week_end}\n"
            f"- THIS_MONTH_START: {this_month_start}\n"
            f"- THIS_MONTH_END: {this_month_end}\n"
            f"- LAST_MONTH_START: {last_month_start}\n"
            f"- LAST_MONTH_END: {last_month_end}"
        )

        msg_lower = message.lower()
        has_status_keyword = False
        status_override = None

        is_past_running = bool(re.search(r'\b(?:was\s+running|were\s+running|had\s+been\s+running|operated)\b', msg_lower))

        is_vendor_running_query = (not is_past_running) and (not bool(re.search(r'\boperating\s+on\s+(?:the\s+)?field\b', msg_lower))) and bool(
            re.search(r'\b(?:was\s+running|is\s+running|running)\s+on\s+(?:\w+\s+)?devices?\b', msg_lower)
            or re.search(r'\b(?:vehicles?|trips?)\s+(?:which\s+was\s+running|running|operating)\s+on\b', msg_lower)
            or (
                re.search(r'\b(?:was\s+running|running)\b', msg_lower)
                and re.search(r'\b(?:wheelseye|secutrak|icici|ilgic)\b', msg_lower)
                and re.search(r'\b(?:vehicles?|devices?)\b', msg_lower)
            )
        )

        _is_each_region_query = bool(
            re.search(
                r'\b(?:in\s+each\s+region|for\s+each\s+region|across\s+(?:each|all)\s+regions?|per\s+region|each\s+region|by\s+region|region[\s-]wise|region\s+breakdown|breakdown\s+by\s+region|breakdown\s+across\s+regions?|breakdown\s+of\s+regions?)\b',
                msg_lower
            )
        )

        _is_priority_query = bool(
            re.search(r'\bpriorit(?:y|ies)\b', msg_lower)
        )

        _is_priority_vs_query = bool(
            re.search(r'\b(?:high\s+priority\s+(?:vs\.?|versus|and|or|to)\s+(?:standard|normal|low)(?:\s+priority)?)\b', msg_lower)
            or re.search(r'\b(?:(?:standard|normal|low)\s+(?:priority\s+)?(?:vs\.?|versus|and|or|to)\s+high\s+priority)\b', msg_lower)
            or (
                re.search(r'\b(?:compare|comparison|versus|vs\.?|difference|ratio)\b', msg_lower)
                and re.search(r'\bhigh\s+priority\b', msg_lower)
                and re.search(r'\b(?:standard|normal|low)\b', msg_lower)
            )
            or re.search(r'\b(?:in\s+each\s+priority|per\s+priority|by\s+priority|priority[\s-]wise|priority\s+breakdown|breakdown\s+by\s+priority|breakdown\s+across\s+priorit(?:y|ies))\b', msg_lower)
        )

        _REMARK_MAP = {
            "manual": "manual",
            "manually": "manual",
            "forcefully": "forcefully",
            "forcefull": "forcefully",
            "forceful": "forcefully",
            "geofence": "geofence",
            "geo-fence": "geofence",
            "geo fence": "geofence",
            "polygon": "polygon",
            "api": "api",
            "backend": "backend",
            "web": "web",
        }

        _is_closeremark_query = bool(
            re.search(r'\b(?:closed|close|closing)\s+(?:automatically\s+)?(?:via\s+|by\s+|through\s+)?(?:manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b', msg_lower)
            or re.search(r'\b(?:close\s*remark[s]?|closeremark[s]?|remark[s]?)\s*[:=]?\s*(?:manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b', msg_lower)
            or re.search(r'\b(?:closed|close|closing)\s+(?:with\s+(?:remark\s+)?)?(?:manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b', msg_lower)
            or re.search(r'\bvia\s+(?:forcefull|forcefully|forceful|geofence|geo[\s-]fence|polygon)\b', msg_lower)
            or re.search(r'\b(?:forcefully|forcefull|forceful|geofence|polygon)\s+(?:closed|remark)\b', msg_lower)
            or ((re.search(r'\b(?:forcefully|forcefull|forceful|geofence|polygon)\b', msg_lower) is not None) and (re.search(r'\b(?:closed|close)\b', msg_lower) is not None))
        )

        _is_atd_ata_query = bool(
            re.search(r'\b(?:atd|ata)\b', msg_lower)
        )

        _is_network_vs_local_query = bool(
            re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or|to)\s+local\b', msg_lower)
            or re.search(r'\blocal\s+(?:vs\.?|versus|and|or|to)\s+network\b', msg_lower)
            or re.search(r'\bintercity\s+(?:vs\.?|versus|and|or|to)\s+intracity\b', msg_lower)
            or re.search(r'\bintracity\s+(?:vs\.?|versus|and|or|to)\s+intercity\b', msg_lower)
            or (
                re.search(r'\b(?:compare|comparison|versus|vs\.?|difference|ratio)\b', msg_lower)
                and (
                    (re.search(r'\bnetwork\b', msg_lower) and re.search(r'\blocal\b', msg_lower))
                    or (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
                )
            )
            or (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
        )
        _is_trip_type_breakdown_query = bool(
            re.search(r'\bbreakdown\b', msg_lower)
            and (
                re.search(r'\b(?:network\s+or\s+intercity|intercity\s+or\s+network)\b', msg_lower)
                or re.search(r'\bintercity\s+routes?\b', msg_lower)
                or re.search(r'\bnetwork\s+routes?\b', msg_lower)
                or re.search(r'\b(?:local\s+or\s+intracity|intracity\s+or\s+local)\b', msg_lower)
                or re.search(r'\bintracity\s+routes?\b', msg_lower)
                or re.search(r'\blocal\s+routes?\b', msg_lower)
                or (re.search(r'\b(?:intercity|network)\b', msg_lower) and re.search(r'\broutes?\b', msg_lower))
            )
        )
        _is_across_trip_types = bool(
            (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
            or (re.search(r'\bnetwork\b', msg_lower) and re.search(r'\blocal\b', msg_lower))
            or re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or|to)\s+local\b', msg_lower)
            or re.search(r'\blocal\s+(?:vs\.?|versus|and|or|to)\s+network\b', msg_lower)
            or re.search(r'\bintercity\s+(?:vs\.?|versus|and|or|to)\s+intracity\b', msg_lower)
            or re.search(r'\bintracity\s+(?:vs\.?|versus|and|or|to)\s+intercity\b', msg_lower)
            or re.search(r'\bacross\s+(?:both\s+)?trip\s+types\b', msg_lower)
            or (re.search(r'\bacross\b', msg_lower) and (
                (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
                or (re.search(r'\bnetwork\b', msg_lower) and re.search(r'\blocal\b', msg_lower))
            ))
        )

        _multi_device_gps = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower))
        _multi_device_fixed = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_lower))
        _multi_device_portable = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_lower))
        _multi_device_count = sum([_multi_device_gps, _multi_device_fixed, _multi_device_portable])
        _is_multi_device_trip_query = bool(
            _multi_device_count >= 2
            and _multi_device_gps
            and not re.search(r'\bvehicles?\b', msg_lower)
            and not re.search(r'\btransporters?\b', msg_lower)
            and not _is_network_vs_local_query
            and not re.search(r'\b(only|single|sole|alone|exclusive|exclusively)\b', msg_lower)
            and not re.search(r'\b(?:without|excluding|except)\s+.*(?:fixed|portable|gps|lock|elock)\b', msg_lower)
        )

        # Format user prompt
        prompt = INTENT_EXTRACTION_USER_PROMPT_TEMPLATE.format(message=message, date_context=date_context)
        
        extraction_source = "llm"
        llm_status = "success"

        # Check Python Fast-Path first for confident resolution
        single_shipment = extract_single_shipment_lookup(message)
        if single_shipment:
            query_payload = {
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
                        "value": single_shipment
                    }
                ],
                "select": []
            }
            log_intent_detected(session_id, "trip_report")
            log_entities_extracted(session_id, query_payload)
            print(f"[DEBUG_QUERY] Session: {session_id} | Source: fast_path | LLM Status: bypassed | Mode: single_shipment_lookup")
            return {
                "intent": "trip_report",
                "entities": query_payload,
                "source": "fast_path",
                "llm_status": "bypassed",
                "debug": {
                    "source": "fast_path",
                    "llm_status": "bypassed"
                }
            }

        top_vendors = detect_top_device_vendors(message)
        if top_vendors:
            start_date, end_date = extract_date_range(message, reference_now=now)
            if not start_date or not end_date:
                start_date, end_date = _resolve_month_range(msg_lower, now)
            if not start_date or not end_date:
                if "august" in msg_lower or "aug" in msg_lower:
                    start_date = "2026-08-01 00:00:00"
                    end_date = "2026-08-31 23:59:59"
                elif "this month" in msg_lower or "current month" in msg_lower:
                    start_date = this_month_start
                    end_date = this_month_end
            filters = [
                {"field": "groupId", "operator": "eq", "value": "0041"},
                {"field": "tripStatus", "operator": "eq", "value": "valid"}
            ]
            if start_date:
                filters.append({"field": "runDate", "operator": "gte", "value": start_date})
            if end_date:
                filters.append({"field": "runDate", "operator": "lte", "value": end_date})
            filters.append({"field": top_vendors["vendor_field"], "operator": "eq", "value": "existing"})
            query_payload = {
                "entity": "trip",
                "operation": "count",
                "analytics": [
                    {
                        "type": top_vendors["analytics_type"]
                    }
                ],
                "filters": filters,
                "groupBy": [
                    top_vendors["vendor_field"]
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
            log_intent_detected(session_id, "trip_report")
            log_entities_extracted(session_id, query_payload)
            print(f"[DEBUG_QUERY] Session: {session_id} | Source: fast_path | LLM Status: bypassed | Mode: top_device_vendors")
            return {
                "intent": "trip_report",
                "entities": query_payload,
                "source": "fast_path",
                "llm_status": "bypassed",
                "debug": {
                    "source": "fast_path",
                    "llm_status": "bypassed"
                }
            }

        fast_payload = None
        if getattr(settings, "ENABLE_FAST_PATH", True):
            fast_payload = build_fast_path_payload(message, reference_now=now)

        try:
            if fast_payload is not None:
                query_payload = fast_payload
                extraction_source = "fast_path"
                llm_status = "bypassed"
            else:
                # Select domain-specific prompt modules using prompt router
                system_prompt, selected_domains = route_prompt(message, user_prompt=prompt)

                # We enforce JSON output from LLM with configured token limits
                response_text = await self.llm_client.generate(
                    prompt=prompt,
                    system=system_prompt,
                    format_json=True,
                    max_tokens=getattr(settings, "INTENT_MAX_TOKENS", 512)
                )
                
                # Parse response with robust markdown fence removal
                clean_json_str = response_text.strip()
                if clean_json_str.startswith("```"):
                    clean_json_str = re.sub(r"^```(?:json)?\s*", "", clean_json_str)
                    clean_json_str = re.sub(r"\s*```$", "", clean_json_str)

                try:
                    query_payload = json.loads(clean_json_str)
                except json.JSONDecodeError:
                    json_match = re.search(r"(\{[\s\S]*\})", clean_json_str)
                    if json_match:
                        query_payload = json.loads(json_match.group(1))
                    else:
                        raise
                
                if not isinstance(query_payload, dict):
                    query_payload = {}
                
                # If the LLM response is wrapped in an 'entities' or 'query_payload' key, unwrap it
                if "entities" in query_payload and isinstance(query_payload["entities"], dict):
                    query_payload = query_payload["entities"]
                elif "query_payload" in query_payload and isinstance(query_payload["query_payload"], dict):
                    query_payload = query_payload["query_payload"]
                
            # Normalize schema
            query_payload["entity"] = query_payload.get("entity") or "trip"
            if not query_payload.get("operation"):
                query_payload["operation"] = "count"

            def _device_analytics_sort_key(flt):
                if not isinstance(flt, dict):
                    return 99
                fld = flt.get("field")
                if fld == "groupId":
                    return 1
                if fld == "tripStatus":
                    return 2
                if fld == "runDate" and flt.get("operator") == "gte":
                    return 3
                if fld == "runDate" and flt.get("operator") == "lte":
                    return 4
                if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and flt.get("operator") == "gte":
                    return 5
                if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and flt.get("operator") == "lte":
                    return 6
                if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance"):
                    return 7
                if fld in ("area", "branch"):
                    return 8
                return 10
                
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
                    elif "THIS_WEEK_START" in val:
                        f["value"] = this_week_start
                    elif "THIS_WEEK_END" in val:
                        f["value"] = this_week_end
                    elif "LAST_WEEK_START" in val:
                        f["value"] = last_week_start
                    elif "LAST_WEEK_END" in val:
                        f["value"] = last_week_end
                    elif "THIS_MONTH_START" in val:
                        f["value"] = this_month_start
                    elif "THIS_MONTH_END" in val:
                        f["value"] = this_month_end
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

            has_date_specified = bool(
                (extracted_start and extracted_end)
                or re.search(r'\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\b', msg_lower)
                or re.search(r'\b(?:today|yesterday|this\s+week|last\s+week|this\s+month|last\s+month)\b', msg_lower)
            )

            # Apply regex-extracted date ranges as override
            if is_vendor_running_query and not has_date_specified:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "runDate")
                ]
            elif extracted_start and extracted_end:
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
            if not has_run_date and not (is_vendor_running_query and not has_date_specified):
                if re.search(r'\btoday\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": today_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": today_end})
                elif re.search(r'\byesterday\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": yesterday_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": yesterday_end})
                elif re.search(r'\b(?:this|current)\s+week\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": this_week_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": this_week_end})
                elif re.search(r'\blast\s+week\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": last_week_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": last_week_end})
                elif re.search(r'\b(?:this|current)\s+month\b', msg_lower):
                    query_payload["filters"].append({"field": "runDate", "operator": "gte", "value": this_month_start})
                    query_payload["filters"].append({"field": "runDate", "operator": "lte", "value": this_month_end})
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
                "atd": "atd",
                "ata": "ata",
                "route": "Route",
                "routename": "Route",
                "from": "source",
                "origin": "source",
                "source": "source",
                "to": "destination",
                "dest": "destination",
                "destination": "destination",
            }
            for f in query_payload["filters"]:
                if isinstance(f, dict):
                    raw_field = f.get("field", "")
                    if raw_field == "GPS":
                        continue
                    if raw_field.lower() == "gps":
                        if any(isinstance(x, dict) and x.get("field") in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock") for x in query_payload["filters"]) or extract_device_vendor(message):
                            f["field"] = "gps"
                        elif f.get("value") in ("existing", "active", "inactive"):
                            f["field"] = "GPS"
                        continue
                    if raw_field.lower() in ("atd", "ata"):
                        f["field"] = raw_field.lower()
                        continue
                    if raw_field.lower() in ("from", "origin", "source"):
                        f["field"] = "source"
                        continue
                    if raw_field.lower() in ("to", "dest", "destination"):
                        f["field"] = "destination"
                        continue
                    alias = raw_field.lower()
                    if alias in field_alias_map:
                        f["field"] = field_alias_map[alias]

            # Remove hallucinated device analytics & device filters if query has no device mention
            has_device_query = bool(
                detect_device_analytics(message)
                or re.search(r'\b(?:gps|elock|e-lock|lock|device|devices)\b', msg_lower)
            )
            has_delay_query = bool(
                re.search(r'\b(?:departure\s+(?:was\s+)?delayed|delayed\s+departure|arrival\s+(?:was\s+)?delayed|delayed\s+arrival)\b', msg_lower)
            )
            if not has_device_query and not has_delay_query:
                query_payload.pop("analytics", None)
            elif not has_device_query and "analytics" in query_payload:
                query_payload["analytics"] = [
                    a for a in query_payload["analytics"]
                    if isinstance(a, dict) and a.get("type") in ("departureDelayed", "arrivalDelayed")
                ]
                if not query_payload["analytics"]:
                    query_payload.pop("analytics", None)

            if not has_device_query:
                _DEVICE_FIELD_NAMES = {
                    "fixedgps", "fixedelock", "portableelock", "gps", "fixed-gps-status",
                    "fixed-elock-status", "portable-elock-status", "vendorgps",
                    "vendorfixedelock", "vendorportableelock"
                }
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and str(f.get("field", "")).lower() in _DEVICE_FIELD_NAMES)
                ]

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

            if bool(re.search(r'\bwhere\s+(?:gps|fixed[\s-]gps|fixed|lock|elock|device)\s+is\s+(?:active|inactive)\b', msg_lower)):
                query_payload.pop("analytics", None)
                if re.search(r'\bwhere\s+(?:gps|fixed[\s-]gps)\s+is\s+active\b', msg_lower):
                    has_gps_filter = False
                    for f in query_payload.get("filters", []):
                        if isinstance(f, dict) and str(f.get("field", "")).lower() in ("gps", "fixedgps"):
                            f["field"] = "GPS"
                            f["operator"] = "eq"
                            f["value"] = "active"
                            has_gps_filter = True
                            break
                    if not has_gps_filter:
                        query_payload.setdefault("filters", []).append({
                            "field": "GPS",
                            "operator": "eq",
                            "value": "active"
                        })
                        
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

            # Ensure route filter is present if route code detected in message or prev_filters
            detected_route = extract_route_code(message)
            if not detected_route and is_follow_up and prev_filters:
                for pf in prev_filters:
                    if isinstance(pf, dict) and pf.get("field") in ("Route", "route", "RouteName", "routeName"):
                        detected_route = pf.get("value")
                        break

            if detected_route:
                has_route_flt = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ("Route", "route", "RouteName", "routeName"):
                        f["field"] = "Route"
                        f["operator"] = "eq"
                        f["value"] = detected_route
                        has_route_flt = True
                        break
                if not has_route_flt:
                    query_payload.setdefault("filters", []).append({
                        "field": "Route",
                        "operator": "eq",
                        "value": detected_route
                    })
                
            # Determine if a status was requested based on keywords in user message
            msg_lower = message.lower()
            _is_network_vs_local_query = bool(
                re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or|to)\s+local\b', msg_lower)
                or re.search(r'\blocal\s+(?:vs\.?|versus|and|or|to)\s+network\b', msg_lower)
                or re.search(r'\bintercity\s+(?:vs\.?|versus|and|or|to)\s+intracity\b', msg_lower)
                or re.search(r'\bintracity\s+(?:vs\.?|versus|and|or|to)\s+intercity\b', msg_lower)
                or (
                    re.search(r'\b(?:compare|comparison|versus|vs\.?|difference|ratio)\b', msg_lower)
                    and (
                        (re.search(r'\bnetwork\b', msg_lower) and re.search(r'\blocal\b', msg_lower))
                        or (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
                    )
                )
                or (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
            )
            _is_trip_type_breakdown_query = bool(
                re.search(r'\bbreakdown\b', msg_lower)
                and (
                    re.search(r'\b(?:network\s+or\s+intercity|intercity\s+or\s+network)\b', msg_lower)
                    or re.search(r'\bintercity\s+routes?\b', msg_lower)
                    or re.search(r'\bnetwork\s+routes?\b', msg_lower)
                    or re.search(r'\b(?:local\s+or\s+intracity|intracity\s+or\s+local)\b', msg_lower)
                    or re.search(r'\bintracity\s+routes?\b', msg_lower)
                    or re.search(r'\blocal\s+routes?\b', msg_lower)
                    or (re.search(r'\b(?:intercity|network)\b', msg_lower) and re.search(r'\broutes?\b', msg_lower))
                )
            )
            _is_shipment_method_breakdown_query = bool(
                re.search(r'\b(?:across|by|each|all|breakdown|in\s+(?:each|every))\s+(?:major\s+)?(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_lower)
                or re.search(r'\b(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\s+breakdown\b', msg_lower)
                or re.search(r'\btrip\s+count\s+across\s+(?:major\s+)?(?:feeder|route|shipment)\b', msg_lower)
                or re.search(r'\bmajor\s+(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_lower)
            )
            detected_sms = detect_shipment_methods(message)
            if _is_network_vs_local_query or _is_trip_type_breakdown_query or _is_across_trip_types or _is_shipment_method_breakdown_query:
                detected_sms = [sm for sm in detected_sms if sm != 'network']
            detected_sm = detected_sms[0] if detected_sms else None
            has_status_keyword = False
            status_override = None

            def word_in_text(words, text):
                return any(re.search(r'\b' + re.escape(w) + r'\b', text) for w in words)

            _is_completed_and_closed = bool(
                re.search(r'\b(?:completed\s+(?:and|&)\s+closed|closed\s+(?:and|&)\s+completed)\b', msg_lower)
            )
            _is_non_cancelled = bool(
                re.search(r'\b(?:non[\s-]?cancell?ed|not\s+cancell?ed)\b', msg_lower)
            )
            _is_include_cancelled = bool(
                re.search(
                    r'\b(?:includ(?:e|ing|es)|with)\s+(?:all\s+|any\s+|the\s+)?(?:cancell?ed|cancel)(?:\s+trips?)?\b'
                    r'|\b(?:cancell?ed|cancel)\s+trips?\s+(?:includ(?:ed?|ing))\b'
                    r'|\binclud(?:e|ing|es)\s+cancel\b',
                    msg_lower
                )
            )

            _is_active_vehicle_query = bool(
                re.search(r'\b(?:active\s+vehicles?|vehicles?\s+(?:that\s+were\s+|that\s+are\s+)?active)\b', msg_lower)
            )

            _is_no_gps_vehicle_query = bool(
                (
                    re.search(r'\bno\s+gps\b', msg_lower)
                    or re.search(r'\bwithout\s+gps\b', msg_lower)
                    or re.search(r'\bhad\s+no\s+gps\b', msg_lower)
                    or re.search(r'\bgps\s+not\s+available\b', msg_lower)
                    or re.search(r'\bgps\s+absent\b', msg_lower)
                    or re.search(r'\blacking\s+gps\b', msg_lower)
                    or re.search(r'\bnon[\s\-_]*gps\b', msg_lower)
                    or re.search(r'\bnon\s+gps\b', msg_lower)
                )
                and re.search(r'\bvehicles?\b', msg_lower)
            )

            specific_transporter = extract_specific_transporter(message)
            if not specific_transporter:
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                        val = f.get("value")
                        if isinstance(val, str) and val.strip() and val.strip().lower() not in _TRANSPORTER_STOP_WORDS:
                            specific_transporter = val.strip()
                            break

            has_vehicle_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            has_transporter_mention = bool(re.search(r'\btransporters?\b', msg_lower))
            has_count_unique_distinct = bool(re.search(r'\b(?:unique|distinct|total\s+number|count)\b', msg_lower))
            has_no_transporter = bool(
                re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+transporters?\b', msg_lower)
                or re.search(r'\btransporters?\s+(?:is\s+)?(?:na|not\s+available)\b', msg_lower)
            )
            _is_trips_and_vehicles_query = bool(
                re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_lower)
            )
            _is_transporter_unique_vehicles_query = bool(
                _is_trips_and_vehicles_query or (
                    specific_transporter and has_vehicle_mention and not _is_no_gps_vehicle_query and (
                        has_count_unique_distinct
                        or bool(re.search(r'\b(?:lis|list|how\s+many|which|show|all|what|get)\b', msg_lower))
                        or bool(re.search(r'\b(?:on\s+trip|was\s+on|were\s+on)\b', msg_lower))
                    )
                )
            )
            _is_distinct_transporter_query = bool(
                has_transporter_mention
                and has_count_unique_distinct
                and not has_vehicle_mention
                and not has_no_transporter
                and not is_top_transporters_query(message)
            )

            if has_no_transporter:
                transporter_field_name = "transporter"
                has_transporter_flt = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                        f["field"] = transporter_field_name
                        f["operator"] = "eq"
                        f["value"] = "na"
                        has_transporter_flt = True
                        break
                if not has_transporter_flt:
                    query_payload.setdefault("filters", []).append({
                        "field": transporter_field_name,
                        "operator": "eq",
                        "value": "na"
                    })
            elif specific_transporter:
                transporter_field_name = "transporter"
                trans_op = "in" if isinstance(specific_transporter, list) else "eq"
                has_transporter_flt = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                        f["field"] = transporter_field_name
                        f["operator"] = trans_op
                        f["value"] = specific_transporter
                        has_transporter_flt = True
                        break
                if not has_transporter_flt:
                    query_payload.setdefault("filters", []).append({
                        "field": transporter_field_name,
                        "operator": trans_op,
                        "value": specific_transporter
                    })

            # --- Region Extraction & Normalization ---
            detected_regions = detect_regions(message)
            if not detected_regions and prev_filters:
                for pf in prev_filters:
                    if isinstance(pf, dict) and pf.get("field") == "region":
                        pv = pf.get("value")
                        if isinstance(pv, list):
                            detected_regions = pv
                        elif isinstance(pv, str) and pv:
                            detected_regions = [pv]
                        break

            # Remove invalid/temporal values that might have been mistakenly assigned to region
            _INVALID_REGION_VALUES = {
                "today", "yesterday", "tomorrow", "last week", "last month", "this month",
                "january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december", "jan", "feb", "mar", "apr",
                "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"
            }
            query_payload["filters"] = [
                f for f in query_payload.get("filters", [])
                if not (
                    isinstance(f, dict)
                    and f.get("field") == "region"
                    and (
                        str(f.get("value", "")).lower() in _INVALID_REGION_VALUES
                        or bool(re.search(r'\d', str(f.get("value", ""))))
                    )
                )
            ]

            has_region_flt = False
            for f in query_payload.get("filters", []):
                if isinstance(f, dict) and f.get("field") == "region":
                    has_region_flt = True
                    if detected_regions:
                        if len(detected_regions) > 1:
                            f["operator"] = "in"
                            f["value"] = detected_regions
                        else:
                            f["operator"] = "eq"
                            f["value"] = detected_regions[0]
                    else:
                        val = f.get("value")
                        if isinstance(val, str) and val.capitalize() in ["North", "South", "East", "West", "Central", "North East", "North West", "South East", "South West"]:
                            f["value"] = val.capitalize()
                    break

            if _is_each_region_query:
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "region")
                ]
            elif not has_region_flt and detected_regions:
                if len(detected_regions) > 1:
                    query_payload.setdefault("filters", []).append({
                        "field": "region",
                        "operator": "in",
                        "value": detected_regions
                    })
                else:
                    query_payload.setdefault("filters", []).append({
                        "field": "region",
                        "operator": "eq",
                        "value": detected_regions[0]
                    })

            is_past_running = bool(re.search(r'\b(?:was\s+running|were\s+running|had\s+been\s+running|operated)\b', msg_lower))

            is_vendor_running_query = (not is_past_running) and (not bool(re.search(r'\boperating\s+on\s+(?:the\s+)?field\b', msg_lower))) and bool(
                re.search(r'\b(?:was\s+running|is\s+running|running)\s+on\s+(?:\w+\s+)?devices?\b', msg_lower)
                or re.search(r'\b(?:vehicles?|trips?)\s+(?:which\s+was\s+running|running|operating)\s+on\b', msg_lower)
                or (
                    re.search(r'\b(?:was\s+running|running)\b', msg_lower)
                    and re.search(r'\b(?:wheelseye|secutrak|icici|ilgic)\b', msg_lower)
                    and re.search(r'\b(?:vehicles?|devices?)\b', msg_lower)
                )
            )

            if is_past_running:
                has_status_keyword = True
                status_override = "valid"
            elif is_vendor_running_query:
                has_status_keyword = True
                status_override = "running"
            elif _is_include_cancelled:
                has_status_keyword = False
                status_override = None
            elif (
                word_in_text(["valid"], msg_lower)
                or _is_non_cancelled
                or _is_completed_and_closed
                or re.search(r'\bdevice\s+(?:report|data)\b|\breport\s+for\s+(?:all\s+)?(?:\w+\s+)?devices?\b|\bdevices?\s+report\b', msg_lower)
                or re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower)
                or _is_distinct_transporter_query
                or _is_transporter_unique_vehicles_query
                or (re.search(r'\btransporters?\b', msg_lower) and word_in_text(["used"], msg_lower))
                or (
                    re.search(r'\bvehicles?\b', msg_lower)
                    and word_in_text(["used"], msg_lower)
                    and re.search(r'\b(?:distinct|unique|list|all)\b', msg_lower)
                )
                or _is_active_vehicle_query
                or _is_no_gps_vehicle_query
                or _is_network_vs_local_query
                or _is_trip_type_breakdown_query
                or _is_each_region_query
                or _is_priority_query
                or _is_closeremark_query
                or _is_atd_ata_query
            ):
                has_status_keyword = True
                status_override = "valid"
            elif word_in_text(["completed", "finish", "finished", "closed", "inactive"], msg_lower):
                is_inactive_for_trip = True
                has_inactive = re.search(r'\binactive\b', msg_lower) is not None
                if has_inactive:
                    explicit_inactive_trip = (
                        (re.search(r'\binactive\s+trips?\b', msg_lower) is not None and not re.search(r'\b(?:gps|fixed[\s-]gps|lock|elock|e-lock|portable|fixed|device)\s+inactive\s+trips?\b', msg_lower)) or
                        re.search(r'\btrips?\s+(?:status\s+)?is\s+inactive\b', msg_lower) is not None or
                        re.search(r'\btrips?\s+are\s+inactive\b', msg_lower) is not None
                    )
                    has_device_inactive = bool(
                        re.search(r'\b(?:gps|fixed[\s-]gps|lock|elock|e-lock|portable|fixed|device)\s+inactive\b', msg_lower)
                        or (detect_device_analytics(message) and any(
                            f.get("value") == "inactive" for f in (detect_device_analytics(message).get("device_filters") or [])
                        ))
                    )
                    if not explicit_inactive_trip and has_device_inactive:
                        is_inactive_for_trip = False
                    else:
                        inactive_field_filters = [
                            f for f in query_payload.get("filters", [])
                            if isinstance(f, dict) and f.get("field") in ["GPS", "FixedGPS", "PortableELock", "FixedELock", "gps", "fixedgps", "fixedelock", "portableelock"] and f.get("value", "").lower() in {"inactive", "nogps", "gpsinactive", "na"}
                        ]
                        if inactive_field_filters and not explicit_inactive_trip:
                            inactive_count = len(re.findall(r'\binactive\b', msg_lower))
                            if inactive_count <= len(inactive_field_filters):
                                is_inactive_for_trip = False
                
                if is_inactive_for_trip:
                    has_status_keyword = True
                    status_override = "closed"
                else:
                    status_override = "valid"
            elif word_in_text(["cancelled", "canceled"], msg_lower) and not _is_non_cancelled:
                has_status_keyword = True
                status_override = "cancelled"
            elif word_in_text(["cancel"], msg_lower) and not _is_include_cancelled and not _is_non_cancelled:
                has_status_keyword = True
                status_override = "cancel"
            elif word_in_text(["active", "open"], msg_lower):
                # If "active" is present, check if it was only extracted for gps/lock filters
                is_active_for_trip = True
                has_active = re.search(r'\bactive\b', msg_lower) is not None
                has_open = re.search(r'\bopen\b', msg_lower) is not None
                if has_active and not has_open:
                    explicit_active_trip = (
                        (re.search(r'\bactive\s+trips?\b', msg_lower) is not None and not re.search(r'\b(?:gps|fixed[\s-]gps|lock|elock|e-lock|portable|fixed|device|devices)\s+active\s+trips?\b', msg_lower)) or
                        re.search(r'\btrips?\s+(?:status\s+)?is\s+active\b', msg_lower) is not None or
                        re.search(r'\btrips?\s+are\s+active\b', msg_lower) is not None
                    )
                    has_device_active = bool(
                        re.search(r'\bactive\s+(?:device|devices|fixed|portable|gps|lock|elock|e-lock)\b', msg_lower)
                        or re.search(r'\b(?:where\s+)?(?:gps|fixed[\s-]gps|fixed|lock|elock|e-lock|portable|device|devices)\s+(?:is\s+|are\s+|was\s+)?active\b', msg_lower)
                        or _is_active_vehicle_query
                        or bool(detect_device_analytics(message))
                    )
                    if not explicit_active_trip and has_device_active:
                        is_active_for_trip = False
                    else:
                        active_field_filters = [
                            f for f in query_payload.get("filters", [])
                            if isinstance(f, dict) and str(f.get("field", "")).lower() in [
                                "gps", "fixedgps", "fixed-gps-status", "fixedelock", "fixed-elock-status",
                                "portableelock", "portable-elock-status", "vendorgps", "vendorfixedelock",
                                "vendorportableelock"
                            ] and str(f.get("value", "")).lower() in ["active", "existing"]
                        ]
                        if active_field_filters and not explicit_active_trip:
                            active_count = len(re.findall(r'\bactive\b', msg_lower))
                            if active_count <= len(active_field_filters):
                                is_active_for_trip = False
                
                if is_active_for_trip:
                    has_status_keyword = True
                    if len(detected_sms) > 1 or re.search(r'\bdelivery\s+with\s+pick\b', msg_lower):
                        status_override = "running"
                    else:
                        status_override = "active"
                else:
                    status_override = "valid"
            elif word_in_text(["running", "scheduled", "sceduled", "transit", "ongoing", "moving", "intransit", "live"], msg_lower) or re.search(r'\b(?:in[\s-]transit|en[\s-]route|on\s+the\s+way)\b', msg_lower):
                has_status_keyword = True
                status_override = "running"
            elif detected_sm and not any(isinstance(f, dict) and f.get("field") == "runDate" for f in query_payload["filters"]):
                # Queries mentioning any shipment method default to tripStatus="running" ONLY when no date is given
                has_status_keyword = True
                status_override = "running"
            else:
                # Default for all other payloads: tripStatus eq "valid"
                has_status_keyword = False
                status_override = "valid"

            if _is_include_cancelled:
                # When query asks to include cancel trips, remove any tripStatus filter
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                ]
            else:
                target_status = status_override if status_override is not None else "valid"
                has_trip_status = False
                for f in query_payload["filters"]:
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        has_trip_status = True
                        f["value"] = target_status
                        f["operator"] = "eq"
                        break

                if not has_trip_status:
                    query_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": target_status
                    })

            # --- ShipmentMethod Injection ---
            if _is_shipment_method_breakdown_query:
                # Group by shipmentMethod across all types, remove any specific shipmentMethod or Route filters
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in [
                        "shipmentMethod", "Route", "route", "RouteName", "routeName",
                        "routeCategory", "routeType", "tripType"
                    ])
                ]
                query_payload["select"] = []
                query_payload["groupBy"] = ["shipmentMethod"]
                query_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "trip_count"
                    }
                ]
            elif detected_sms and not _is_network_vs_local_query and not _is_trip_type_breakdown_query:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                if len(detected_sms) > 1:
                    query_payload["filters"].append({
                        "field": "shipmentMethod",
                        "operator": "in",
                        "value": detected_sms
                    })
                    query_payload["groupBy"] = ["shipmentMethod"]
                    query_payload["metrics"] = [
                        {
                            "field": "shipmentNo",
                            "function": "count",
                            "alias": "trip_count"
                        }
                    ]
                else:
                    query_payload["filters"].append({
                        "field": "shipmentMethod",
                        "operator": "eq",
                        "value": detected_sms[0]
                    })
                if len(detected_sms) <= 1 and query_payload.get("groupBy") == ["shipmentMethod"]:
                    query_payload.pop("groupBy", None)
                    query_payload.pop("metrics", None)

            # --- Vendor Post-Processing ---
            target_vendor = None
            dev_vendor_match = None

            known_device_vendor = extract_device_vendor(msg_lower)
            if known_device_vendor in ["Secutrak_TP", "Fleetx", "Axestrack", "Gtropy", "Eicher", "G-Trac", "Ashok", "Tata", "Kiasaint", "tatamotors", "ashokleyland", "wheelseye_bluedart", "Lynkit"]:
                target_vendor = known_device_vendor
                canonical_vendor = known_device_vendor
                op = "contains"

                is_vendor_fixed_elock = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_lower))
                is_vendor_portable_elock = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_lower)) and not is_vendor_fixed_elock

                if is_vendor_fixed_elock:
                    vendor_field = "vendorfixedelock"
                    query_payload["analytics"] = [{"type": "fixedlock"}]
                elif is_vendor_portable_elock:
                    vendor_field = "vendorportableelock"
                    query_payload["analytics"] = [{"type": "portablelock"}]
                else:
                    vendor_field = "vendorgps"
                    query_payload["analytics"] = [{"type": "gps"}]

                has_gps_inactive = bool(
                    re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_lower)
                    or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity(?:\s+status)?\b', msg_lower)
                )
                has_gps_active = bool(
                    re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_lower)
                    or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_lower)
                ) and not has_gps_inactive

                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "gps", "fixedgps", "FixedELock", "fixedelock",
                        "PortableELock", "portableelock", "fixed-gps-status", "fixed-elock-status",
                        "portable-elock-status", "fixed-e-lock status", "portable-e-lock status",
                        "vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"
                    ])
                ]
                query_payload["filters"].append({
                    "field": vendor_field,
                    "operator": "contains",
                    "value": canonical_vendor
                })
                if vendor_field == "vendorgps":
                    if has_gps_inactive:
                        query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "inactive"})
                    elif has_gps_active:
                        query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
            elif re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower):
                target_vendor = "ThirdParty"
            elif re.search(r'\b(secutrak|icici|ilgic)\b', msg_lower):
                target_vendor = "Secutrak"
            elif re.search(r'\bwheelseye\b', msg_lower):
                target_vendor = "Wheelseye"
            else:
                dev_vendor_match = re.search(r'\b([A-Za-z0-9_-]+)\s+devices?\s+(?:report|data)\b', msg_lower)
                if not dev_vendor_match:
                    dev_vendor_match = re.search(r'\breport\s+(?:for\s+)?(?:all\s+)?([A-Za-z0-9_-]+)\s+devices?\b', msg_lower)
                if not dev_vendor_match:
                    dev_vendor_match = re.search(r'\b(?:was\s+running|is\s+running|running)\s+on\s+([A-Za-z0-9_-]+)\s+devices?\b', msg_lower)
                if dev_vendor_match:
                    candidate = dev_vendor_match.group(1)
                    if candidate.lower() not in ["the", "a", "an", "generate", "download", "get", "show", "fetch", "all", "party", "gps", "elock", "e-lock"]:
                        target_vendor = candidate
                    else:
                        dev_vendor_match = None

                if not target_vendor and re.search(r'\bdevice\s+(?:report|data)\b', msg_lower):
                    v_match = re.search(r'\b(wheelseye|secutrak|icici|ilgic)\b', msg_lower)
                    if v_match:
                        target_vendor = v_match.group(1)

                if not target_vendor:
                    for f in query_payload.get("filters", []):
                        if isinstance(f, dict) and f.get("field") in ["vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"]:
                            target_vendor = f.get("value")
                            break

            if target_vendor and not (known_device_vendor and known_device_vendor in ["Secutrak_TP", "Fleetx", "Axestrack", "Gtropy", "Eicher", "G-Trac", "Ashok", "Tata", "Kiasaint", "tatamotors", "ashokleyland", "wheelseye_bluedart", "Lynkit"]):
                if target_vendor.lower() == "wheelseye":
                    canonical_vendor = "Wheelseye"
                elif target_vendor.lower() in ["secutrak", "icici", "ilgic"]:
                    canonical_vendor = "Secutrak"
                elif target_vendor == "ThirdParty" or target_vendor.lower() in ["3rdparty", "thirdparty", "3rd-party"]:
                    canonical_vendor = "ThirdParty"
                else:
                    canonical_vendor = target_vendor.capitalize()

                op = "contains" if (canonical_vendor == "Wheelseye" or (dev_vendor_match and canonical_vendor not in ["Secutrak", "ThirdParty"])) else "eq"

                is_vendor_fixed_elock = bool(
                    (target_vendor or dev_vendor_match) and (
                        re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b', msg_lower)
                    )
                )
                is_vendor_portable_elock = bool(
                    (target_vendor or dev_vendor_match) and not is_vendor_fixed_elock and (
                        re.search(r'\bportable[\s-]e[\s-]?locks?\b|\bportable[\s-]locks?\b', msg_lower)
                    )
                )
                is_vendor_gps = bool(
                    not is_vendor_fixed_elock and not is_vendor_portable_elock and (target_vendor or dev_vendor_match) and (
                        re.search(r'\b(?:[A-Za-z0-9_-]+)\s+gps\s+devices?\b', msg_lower)
                        or re.search(r'\bgps\s+devices?\s+(?:of|for|from|using|with)\s+[A-Za-z0-9_-]+\b', msg_lower)
                        or re.search(r'\b(?:ilgic|secutrak|icici|wheelseye)\s+(?:fixed\s+)?gps\b', msg_lower)
                        or (target_vendor and re.search(rf'\b{re.escape(target_vendor.lower())}\s+(?:fixed\s+)?gps\b', msg_lower))
                        or re.search(r'\b(?:fixed[\s-]*)?gps\b', msg_lower)
                    )
                )

                if is_vendor_fixed_elock:
                    vendor_field = "vendorfixedelock"
                    query_payload["analytics"] = [{"type": "fixedlock"}]
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in [
                            "FixedELock", "fixedelock", "fixed-elock-status", "fixed-e-lock status"
                        ])
                    ]
                elif is_vendor_portable_elock:
                    vendor_field = "vendorportableelock"
                    query_payload["analytics"] = [{"type": "portablelock"}]
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in [
                            "PortableELock", "portableelock", "portable-elock-status", "portable-e-lock status"
                        ])
                    ]
                elif is_vendor_gps:
                    vendor_field = "vendor" if canonical_vendor == "Wheelseye" else "vendorgps"
                    query_payload["analytics"] = [{"type": "gps"}]
                    has_gps_inactive = bool(
                        re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_lower)
                        or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity(?:\s+status)?\b', msg_lower)
                    )
                    has_gps_active = bool(
                        re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_lower)
                        or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_lower)
                    ) and not has_gps_inactive
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["GPS", "FixedGPS", "gps", "fixedgps"])
                    ]
                    if has_gps_inactive:
                        query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "inactive"})
                    elif has_gps_active:
                        query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
                else:
                    vendor_field = "vendor"

                if canonical_vendor == "ThirdParty":
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "portableelock")
                    ]

                has_vendor = False
                for f in query_payload["filters"]:
                    if isinstance(f, dict) and f.get("field") in ["vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"]:
                        has_vendor = True
                        f["field"] = vendor_field
                        f["value"] = canonical_vendor
                        f["operator"] = op
                        break
                if not has_vendor:
                    query_payload["filters"].append({
                        "field": vendor_field,
                        "operator": op,
                        "value": canonical_vendor
                    })

                if vendor_field == "vendorgps":
                    has_gps_inactive = bool(re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_lower))
                    has_gps_active = bool(re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_lower))
                    gps_status_val = "inactive" if has_gps_inactive else ("active" if has_gps_active else None)
                    if gps_status_val:
                        has_gps_f = False
                        for f in query_payload["filters"]:
                            if isinstance(f, dict) and f.get("field") == "gps":
                                f["value"] = gps_status_val
                                f["operator"] = "eq"
                                has_gps_f = True
                                break
                        if not has_gps_f:
                            query_payload["filters"].append({
                                "field": "gps",
                                "operator": "eq",
                                "value": gps_status_val
                            })

            # --- Single Device & Combo Post-Processing ---
            has_veh_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            without_match = re.search(r'\b(?:without|excluding|except|no|not\s+having)\b\s*(.*)', msg_lower)
            without_text = without_match.group(1) if without_match else ""

            without_fixed_elock = (
                bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', without_text))
                or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?fixed[\s-]*(?:e[\s-]?)?locks?\b', msg_lower))
                or (bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:e[\s-]?)?locks?\b', msg_lower)) and not bool(re.search(r'\bportab?le\b', without_text)))
            )
            without_portable = (
                bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', without_text))
                or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?portab?le\b', msg_lower))
                or (bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:e[\s-]?)?locks?\b', msg_lower)) and not bool(re.search(r'\bfixed\b', without_text)))
            )
            without_gps = (
                bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', without_text))
                or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:fixed\s+)?gps\b', msg_lower))
            )

            pos_text = re.sub(r'\b(?:without|excluding|except)\b.*', '', msg_lower) if without_match else msg_lower

            has_fixed_elock = (bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', pos_text)) or bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', msg_lower))) and not without_fixed_elock
            has_portable_elock = (bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', pos_text)) or bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', msg_lower))) and not without_portable
            has_fixed_gps = (bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', pos_text)) or bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', msg_lower))) and not without_gps
            _is_devices_together = bool(re.search(r'\btogether\b', msg_lower))
            _is_device_trips = bool(re.search(r'\b(?:device\s+trips?|device\s+data|device\s+report)\b', msg_lower))
            _is_gps_na = bool(
                re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_lower)
                or re.search(r'\bgps\s+is\s+(?:na|inactive|not\s+applicable|not\s+captured)\b', msg_lower)
                or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_lower)
                or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_lower)
            )
            _is_fixed_portable_elock_combo = (
                has_fixed_elock
                and has_portable_elock
                and not has_fixed_gps
                and not bool(re.search(r'\b(?:gps|withoutorgps|not\s+(?:fixed|portable)|without\s+(?:fixed|portable))\b', msg_lower))
                and not bool(re.search(r'\b(?:only|single|sole)\b', msg_lower))
            )

            def _get_device_status(msg: str, default: str = "existing") -> str:
                if re.search(r'\b(?:(?:no|without)\s+any\s+gps|without\s+(?:any\s+)?gps\s+devices?)\b', msg):
                    return "nogps "
                elif re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg):
                    return "nogps"
                elif re.search(r'\b(?:no|without|lost|lacking|zero)\s+connectivity\b', msg):
                    return "inactive"
                elif re.search(r'\bactive\b|\bconnectivity\s+on\b|\b(?:had|with)\s+(?:fixed[\s-]*)?gps\s+connectivity\b|\b(?:fixed[\s-]*)?gps\s+connectivity\b', msg):
                    return "active"
                elif re.search(r'\binactive\b', msg):
                    return "inactive"
                elif re.search(r'\b(?:na|n/a)\b', msg):
                    return "na"
                return default

            # 1. Fixed GPS Only / Single Device
            _is_fixed_gps_only = (
                not has_veh_mention
                and has_fixed_gps
                and not has_fixed_elock
                and not has_portable_elock
                and not without_gps
                and (
                    bool(re.search(r'\bfixed[\s-]gps\b', pos_text) and re.search(r'\b(single|only|sole|alone|exclusive|exclusively)\b', msg_lower))
                    or bool(re.search(r'\bonly\s+fixed[\s-]gps\b', pos_text))
                    or bool(re.search(r'\bfixed[\s-]gps\s+(as\s+)?(the\s+)?(single|only|sole)\b', pos_text))
                    or bool(re.search(r'\b(?:(?:fixed[\s-]*)?gps)\s+(?:only|single|sole|alone|exclusive|exclusively)\b', pos_text))
                    or bool(re.search(r'\bonly\s+(?:fixed[\s-]*)?gps\b', pos_text))
                )
            )

            # 1b. Fixed GPS + E-Lock Combo (e.g. "Fixed GPS + Fixed E-Lock combination", "Fixed GPS and Fixed E-Lock combo")
            _is_fixed_gps_combo = (
                has_fixed_gps
                and (has_fixed_elock or has_portable_elock)
                and not _is_devices_together
                and not _is_device_trips
                and not _is_gps_na
                and not _is_fixed_gps_only
            )

            _is_active_device_count = bool(re.search(r'\b(?:active\s+)?device\s+count\b', msg_lower))

            # 0a. Active Device Count queries (e.g. "What was the active device count for Fixed E-Lock trips on Intercity routes in Aug 2026?")
            _is_active_device_elock_query = (
                has_fixed_elock
                and _is_active_device_count
                and not has_portable_elock
                and not _is_devices_together
                and not _is_device_trips
                and not _is_gps_na
            )
            _is_active_device_portable_query = (
                has_portable_elock
                and _is_active_device_count
                and not has_fixed_elock
                and not _is_devices_together
                and not _is_device_trips
                and not _is_gps_na
            )

            # 2a. Portable E-Lock Single Device ONLY (when "only" / "single" is mentioned)
            _is_portable_elock_only = (
                not has_veh_mention
                and has_portable_elock
                and not has_fixed_elock
                and not _is_devices_together
                and not _is_device_trips
                and not _is_active_device_count
                and bool(re.search(r'\b(single|only|sole|alone|exclusive|exclusively)\b', msg_lower))
            )

            # 2b. Portable E-Lock Trip Query (regular count without "only")
            _is_portable_elock_trip_query = (
                has_portable_elock
                and not has_fixed_elock
                and not _is_devices_together
                and not _is_fixed_gps_combo
                and not _is_device_trips
                and not _is_gps_na
                and not _is_active_device_count
                and not _is_portable_elock_only
                and not target_vendor
                and not dev_vendor_match
            )

            # 3a. Fixed E-Lock Single Device ONLY (when "only" / "single" is mentioned)
            _is_fixed_elock_only = (
                not has_veh_mention
                and has_fixed_elock
                and not has_portable_elock
                and not _is_devices_together
                and not _is_device_trips
                and not _is_active_device_count
                and bool(re.search(r'\b(single|only|sole|alone|exclusive|exclusively)\b', msg_lower))
            )

            # 3b. Fixed E-Lock Trip Query (regular count without "only" and without "device count")
            _is_fixed_elock_trip_query = (
                has_fixed_elock
                and not has_portable_elock
                and not _is_devices_together
                and not _is_fixed_gps_combo
                and not _is_device_trips
                and not _is_gps_na
                and not _is_active_device_count
                and not _is_fixed_elock_only
                and not target_vendor
                and not dev_vendor_match
            )

            # 4a. No GPS / Vehicles without GPS (e.g. "How many vehicles had no GPS in Aug 2026?")
            _is_no_gps_vehicle_query = bool(
                (
                    re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_lower)
                    or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_lower)
                    or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_lower)
                    or re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower)
                )
                and re.search(r'\bvehicles?\b', msg_lower)
            )

            # 4b. GPS Trip Query (e.g. "Show GPS trip counts for Empty feeder routes in Aug 2026")
            _is_gps_exception = bool(
                re.search(r'\bfixed[\s-]gps\s+(?:as\s+)?(?:nogps|inactive|na|gpsinactive)\b', msg_lower)
                or re.search(r'\bgps\s+as\s+(?:nogps|inactive|na|gpsinactive)\b', msg_lower)
                or re.search(r'\bgpsinactive\b', msg_lower)
            )
            _is_gps_trip_query = (
                bool(re.search(r'\bgps\b', msg_lower))
                and not has_fixed_elock
                and not has_portable_elock
                and not _is_devices_together
                and not _is_device_trips
                and not _is_gps_na
                and not _is_fixed_gps_only
                and not _is_gps_exception
                and not _is_no_gps_vehicle_query
                and not _is_network_vs_local_query
                and not _is_trip_type_breakdown_query
                and not _is_across_trip_types
                and not bool(detect_device_analytics(message))
                and not target_vendor
                and not dev_vendor_match
            )

            if _is_trip_type_breakdown_query:
                is_non_gps = bool(re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower))
                target_trip_type = "intracity" if re.search(r'\b(?:intracity|local)\b', msg_lower) and not re.search(r'\b(?:intercity|network)\b', msg_lower) else "intercity"
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus",
                        "shipmentMethod", "routeCategory", "routeType", "tripType"
                    ])
                ]
                query_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                query_payload["filters"].append({"field": "tripType", "operator": "eq", "value": target_trip_type})
                if is_non_gps:
                    query_payload["analytics"] = [{"type": "gpsna"}]
                query_payload["select"] = []
                query_payload["groupBy"] = ["tripType"]
                query_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "count"
                    }
                ]
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "select", "groupBy", "metrics"]:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                for k, v in query_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                query_payload.clear()
                query_payload.update(reordered)
            elif _is_network_vs_local_query and not bool(detect_device_analytics(message)):
                is_non_gps = bool(re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower))
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus",
                        "shipmentMethod", "routeCategory", "routeType", "tripType"
                    ])
                ]
                query_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                if is_non_gps:
                    query_payload["analytics"] = [{"type": "gpsna"}]
                    query_payload["select"] = []
                else:
                    query_payload.pop("select", None)
                query_payload["groupBy"] = ["tripType"]
                query_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "TotalTrip"
                    }
                ]
                reordered = {}
                key_order = ["entity", "operation", "analytics", "filters", "select", "groupBy", "metrics"] if is_non_gps else ["entity", "operation", "filters", "groupBy", "metrics"]
                for k in key_order:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                for k, v in query_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                query_payload.clear()
                query_payload.update(reordered)
            elif _is_multi_device_trip_query:
                query_payload["analytics"] = [{"type": "withgps"}]
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = "valid"
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": "valid"
                        })

                dev_val = "existing"
                if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_lower):
                    dev_val = "inactive"
                elif re.search(r'\bactive\b', msg_lower):
                    dev_val = "active"
                elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_lower):
                    dev_val = "na"
                elif re.search(r'\bexisting\b', msg_lower):
                    dev_val = "existing"

                if _multi_device_fixed:
                    query_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": dev_val})
                if _multi_device_portable:
                    query_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": dev_val})
                if _multi_device_gps:
                    query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": dev_val})

                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "select"]:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                for k, v in query_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                query_payload.clear()
                query_payload.update(reordered)
            elif detect_device_analytics(message) and not (target_vendor or extract_device_vendor(msg_lower)):
                _device_analytics = detect_device_analytics(message)
                query_payload["analytics"] = [{"type": _device_analytics["analytics_type"]}]
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                has_explicit_trip_status = bool(
                    re.search(r'\b(?:valid\s+trips?|active\s+trips?|running\s+trips?|closed\s+trips?|completed\s+trips?|cancelled\s+trips?|cancel\s+trips?)\b', msg_lower)
                    or re.search(r'\bvalid\s+(?:\w+\s+)?trips?\b', msg_lower)
                    or re.search(r'\bvalid\b', msg_lower)
                    or re.search(r'\bnon[\s-]?cancell?ed\b', msg_lower)
                    or re.search(r'\bnot\s+cancell?ed\b', msg_lower)
                    or re.search(r'\btrip\s+status\b', msg_lower)
                )
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
                for df in _device_analytics["device_filters"]:
                    query_payload["filters"].append(df)

                dev_single_route_category = "intracity" if re.search(r'\bintracity\b', msg_lower) and not re.search(r'\bintercity\b', msg_lower) else ("intercity" if re.search(r'\bintercity\b', msg_lower) and not re.search(r'\bintracity\b', msg_lower) else None)

                if _is_across_trip_types:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType", "shipmentMethod"])
                    ]
                    query_payload.pop("select", None)
                    query_payload["groupBy"] = ["tripType"]
                    query_payload["metrics"] = [
                        {
                            "field": "shipmentNo",
                            "function": "count",
                            "alias": "TotalTrip"
                        }
                    ]
                elif dev_single_route_category:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType", "shipmentMethod"])
                    ]
                    query_payload["filters"].append({
                        "field": "routeCategory",
                        "operator": "eq",
                        "value": dev_single_route_category
                    })
                    query_payload.pop("select", None)
                    query_payload.pop("groupBy", None)
                    query_payload.pop("metrics", None)
                else:
                    query_payload["select"] = []
                    query_payload.pop("groupBy", None)
                    query_payload.pop("metrics", None)

                def _device_analytics_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    fld = flt.get("field")
                    if dev_single_route_category:
                        if fld == "tripStatus":
                            return 1
                        if fld == "groupId":
                            return 2
                        if fld == "runDate":
                            return 3 if flt.get("operator") == "gte" else 4
                        if fld in ("fixed-gps-status", "fixed-elock-status", "portable-elock-status", "GPS", "FixedELock", "PortableELock"):
                            return 5
                        if fld == "routeCategory":
                            return 6
                        if fld == "shipmentMethod":
                            return 7
                        return 10
                    if fld == "groupId":
                        return 1
                    if fld == "tripStatus":
                        return 2
                    if fld in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 3
                    if _is_across_trip_types:
                        if fld == "runDate":
                            return 4 if flt.get("operator") == "gte" else 5
                        if fld in ("fixed-gps-status", "fixed-elock-status", "portable-elock-status", "GPS", "FixedELock", "PortableELock"):
                            return 6
                    else:
                        if fld in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                            return 3
                        if fld == "runDate":
                            return 4 if flt.get("operator") == "gte" else 5
                        if fld in ("fixed-gps-status", "fixed-elock-status", "portable-elock-status", "GPS", "FixedELock", "PortableELock"):
                            return 6
                    if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and flt.get("operator") == "gte":
                        return 5.1
                    if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and flt.get("operator") == "lte":
                        return 5.2
                    if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance"):
                        return 5.3
                    if fld in ("area", "branch"):
                        return 5.4
                    if fld == "shipmentMethod":
                        return 7
                    if str(fld).lower() in ("atd", "ata"):
                        return 8
                    return 9

                query_payload["filters"].sort(key=_device_analytics_sort_key)
                reordered = {}
                key_order = ["entity", "operation", "analytics", "filters", "groupBy", "metrics"] if _is_across_trip_types else (["entity", "operation", "analytics", "filters"] if dev_single_route_category else ["entity", "operation", "analytics", "filters", "select", "groupBy", "metrics"])
                for k in key_order:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                for k, v in query_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                query_payload.clear()
                query_payload.update(reordered)
            elif _is_active_device_elock_query:
                fixed_val = _get_device_status(msg_lower, "active")
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus"
                    ])
                ]
                query_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                query_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": fixed_val})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
            elif _is_active_device_portable_query:
                portable_val = _get_device_status(msg_lower, "active")
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus"
                    ])
                ]
                query_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                query_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": portable_val})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
            elif _is_fixed_gps_only:
                query_payload["analytics"] = [{"type": "gpsonly"}]
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif _is_portable_elock_only:
                query_payload["analytics"] = [{"type": "portablelockonly"}]
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif _is_portable_elock_trip_query:
                portable_val = _get_device_status(msg_lower, "existing")
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": portable_val})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif _is_fixed_elock_only:
                query_payload["analytics"] = [{"type": "fixedlockonly"}]
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif _is_fixed_elock_trip_query:
                fixed_val = _get_device_status(msg_lower, "existing")
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": fixed_val})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif _is_fixed_gps_combo:
                query_payload["analytics"] = [{"type": "withgps"}]
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
                query_payload["filters"].append({"field": "fixed-gps-status", "operator": "eq", "value": "existing"})
                if has_fixed_elock:
                    query_payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
                if has_portable_elock:
                    query_payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})
            elif _is_no_gps_vehicle_query:
                # Vehicles with no GPS: tripStatus eq "valid", analytics: [{"type": "gpsna"}], grouped by vehicleNo with countDistinct
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                has_trip_status = any(isinstance(f, dict) and f.get("field") == "tripStatus" for f in query_payload["filters"])
                if not has_trip_status:
                    query_payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "valid"})
                if specific_transporter:
                    query_payload.pop("analytics", None)
                    query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "nogps"})
                    if "select" not in query_payload:
                        query_payload["select"] = []
                else:
                    query_payload["analytics"] = [{"type": "gpsna"}]
                    query_payload.pop("select", None)
                query_payload["groupBy"] = ["vehicleNo"]
                query_payload["metrics"] = [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
            elif _is_gps_trip_query:
                gps_val = _get_device_status(msg_lower, "existing")
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": gps_val})
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif _is_devices_together and (bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower)) or has_fixed_elock or has_portable_elock):
                _has_together_gps = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower))
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                query_payload["select"] = []
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    explicit_closed_trip = bool(
                        re.search(r'\b(?:closed\s+trips?|completed\s+trips?)\b', msg_lower)
                        or re.search(r'\btrips?\s+(?:status\s+)?is\s+(?:closed|completed)\b', msg_lower)
                    )
                    if explicit_closed_trip:
                        target_status = "closed"
                    elif status_override and status_override != "closed":
                        target_status = status_override
                    else:
                        target_status = "valid"
                    found_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })

                if _has_together_gps and has_fixed_elock and without_portable:
                    query_payload["analytics"] = [{"type": "withgps"}]
                    query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "existing"})
                    query_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "existing"})
                    query_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "na"})
                elif _has_together_gps and has_portable_elock and without_fixed_elock:
                    query_payload["analytics"] = [{"type": "withgps"}]
                    query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "existing"})
                    query_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "na"})
                    query_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "existing"})
                elif has_fixed_elock and has_portable_elock and without_gps:
                    query_payload["analytics"] = [{"type": "withgps"}]
                    query_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "na"})
                    query_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "existing"})
                    query_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "existing"})
                elif _has_together_gps and has_fixed_elock and has_portable_elock:
                    query_payload["analytics"] = [{"type": "withgps"}]
                    query_payload["filters"].append({"field": "fixed-gps-status", "operator": "eq", "value": "existing"})
                    query_payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})
                    query_payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
                else:
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
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field in ("fixed-gps-status", "GPS", "gps", "fixedgps"):
                        return 3
                    elif field in ("fixed-elock-status", "FixedELock", "fixedelock"):
                        return 3.1
                    elif field in ("portable-elock-status", "PortableELock", "portableelock"):
                        return 3.2
                    elif field == "runDate" and op == "gte":
                        return 4
                    elif field == "runDate" and op == "lte":
                        return 5
                    return 10

                query_payload["filters"].sort(key=_filter_sort_key)
                reordered = {}
                key_order = ["entity", "operation", "analytics", "filters", "select"]
                for k in key_order:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                for k, v in query_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                query_payload.clear()
                query_payload.update(reordered)
            elif _is_fixed_portable_elock_combo:
                query_payload["analytics"] = [{"type": "withgps"}]
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "FixedGPS", "FixedELock", "PortableELock", "GPS", "gps", "fixedgps",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "fixedelock", "portableelock"
                    ])
                ]
                query_payload["filters"].append(
                    {"field": "portable-elock-status", "operator": "eq", "value": "existing"}
                )
                query_payload["filters"].append(
                    {"field": "fixed-elock-status", "operator": "eq", "value": "existing"}
                )
                query_payload.pop("select", None)
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)

            # --- RouteCategory Post-Processing ---
            # Also handle tripType as a synonym for routeCategory.
            route_category_value: str | None = None
            if _is_across_trip_types or (re.search(r'\bintracity\b', msg_lower) and re.search(r'\bintercity\b', msg_lower)):
                route_category_value = None
            elif re.search(r'\bintracity\b', msg_lower):
                route_category_value = "intracity"
            elif re.search(r'\bintercity\b', msg_lower):
                route_category_value = "intercity"

            if _is_across_trip_types:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType"])
                ]
            elif route_category_value and not _is_network_vs_local_query and not _is_trip_type_breakdown_query and not _is_shipment_method_breakdown_query and (not bool(detect_device_analytics(message)) or _is_fixed_portable_elock_combo):
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

            # --- closeremark Post-Processing ---
            if _is_closeremark_query or any(isinstance(f, dict) and f.get("field") in ("closeremark", "closeRemarks", "closeRemark", "close_remark") for f in query_payload.get("filters", [])):
                detected_remark_val = None
                remark_pattern = r'\b(manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b'
                rm_match = re.search(remark_pattern, msg_lower)
                if rm_match:
                    raw_val = rm_match.group(1).replace("-", "").replace(" ", "")
                    detected_remark_val = _REMARK_MAP.get(raw_val, raw_val)
                else:
                    for f in query_payload.get("filters", []):
                        if isinstance(f, dict) and f.get("field") in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                            v = str(f.get("value", "")).lower().strip()
                            detected_remark_val = _REMARK_MAP.get(v, v)
                            break
                if detected_remark_val:
                    query_payload["filters"] = [
                        f for f in query_payload.get("filters", [])
                        if not (isinstance(f, dict) and f.get("field") in ("closeremark", "closeRemarks", "closeRemark", "close_remark"))
                    ]
                    query_payload["filters"].append({
                        "field": "closeremark",
                        "operator": "eq",
                        "value": detected_remark_val
                    })

            # --- Priority Post-Processing ---
            if _is_priority_vs_query:
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "priority")
                ]
                query_payload["groupBy"] = ["priority"]
                query_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "uniqueShipment"
                    }
                ]
                query_payload.pop("select", None)
            elif _is_priority_query or any(isinstance(f, dict) and f.get("field") == "priority" for f in query_payload.get("filters", [])):
                is_high_priority = bool(
                    re.search(r'\b(?:high|critical|urgent)\s+priority\b', msg_lower)
                    or re.search(r'\bpriority\s+(?:is\s+|as\s+|to\s+)?(?:high|critical|urgent)\b', msg_lower)
                    or re.search(r'\bmarked\s+as\s+(?:high|critical|urgent)\s+priority\b', msg_lower)
                    or re.search(r'\bmarked\s+(?:as\s+)?priority\s+(?:high|critical|urgent)\b', msg_lower)
                )
                priority_val = "high" if is_high_priority else "low"
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "priority")
                ]
                query_payload["filters"].append({
                    "field": "priority",
                    "operator": "eq",
                    "value": priority_val
                })

            # --- ATD / ATA Post-Processing ---
            # Normalize ATD/ATA field values to canonical lowercase forms
            if _is_atd_ata_query or any(isinstance(f, dict) and str(f.get("field", "")).lower() in ("atd", "ata") for f in query_payload.get("filters", [])):
                detected_atd_val = None
                detected_ata_val = None
                if re.search(r'\batd\b', msg_lower):
                    if re.search(r'\b(?:not\s*captured|notcaptured)\b', msg_lower):
                        detected_atd_val = "notcaptured"
                    elif re.search(r'\b(?:not\s*missing|notmissing)\b', msg_lower):
                        detected_atd_val = "notmissing"
                    elif re.search(r'\bmissing\b', msg_lower):
                        detected_atd_val = "missing"
                    elif re.search(r'\bcaptured\b', msg_lower):
                        detected_atd_val = "captured"
                    else:
                        detected_atd_val = "notcaptured"

                if re.search(r'\bata\b', msg_lower):
                    if re.search(r'\b(?:not\s*captured|notcaptured)\b', msg_lower):
                        detected_ata_val = "notcaptured"
                    elif re.search(r'\b(?:not\s*missing|notmissing)\b', msg_lower):
                        detected_ata_val = "notmissing"
                    elif re.search(r'\bmissing\b', msg_lower):
                        detected_ata_val = "missing"
                    elif re.search(r'\bcaptured\b', msg_lower):
                        detected_ata_val = "captured"
                    else:
                        detected_ata_val = "notcaptured"

                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and str(f.get("field", "")).lower() in ("atd", "ata"):
                        f["field"] = str(f["field"]).lower()
                        raw_val = str(f.get("value", "")).lower().replace(" ", "").replace("_", "")
                        if f["field"] == "atd" and detected_atd_val:
                            f["value"] = detected_atd_val
                        elif f["field"] == "ata" and detected_ata_val:
                            f["value"] = detected_ata_val
                        else:
                            f["value"] = _ATD_ATA_VALUE_MAP.get(raw_val, raw_val)
                            if f["field"] == "atd":
                                detected_atd_val = f["value"]
                            elif f["field"] == "ata":
                                detected_ata_val = f["value"]

                if detected_atd_val and not any(isinstance(f, dict) and f.get("field") == "atd" for f in query_payload.get("filters", [])):
                    query_payload["filters"].append({
                        "field": "atd",
                        "operator": "eq",
                        "value": detected_atd_val
                    })
                if detected_ata_val and not any(isinstance(f, dict) and f.get("field") == "ata" for f in query_payload.get("filters", [])):
                    query_payload["filters"].append({
                        "field": "ata",
                        "operator": "eq",
                        "value": detected_ata_val
                    })

                if detect_device_analytics(message):
                    query_payload["filters"].sort(key=_device_analytics_sort_key)

            # --- Source / Destination Post-Processing ---
            for f in query_payload.get("filters", []):
                if isinstance(f, dict):
                    fld_lower = str(f.get("field", "")).lower()
                    if fld_lower in ("from", "origin", "source"):
                        f["field"] = "source"
                    elif fld_lower in ("to", "dest", "destination"):
                        f["field"] = "destination"

            # Remove any source/destination filters with invalid values (stop words, numeric years/dates, etc.)
            query_payload["filters"] = [
                f for f in query_payload.get("filters", [])
                if not (
                    isinstance(f, dict)
                    and f.get("field") in ("source", "destination")
                    and (
                        not str(f.get("value", "")).strip()
                        or str(f.get("value", "")).strip().lower() in _STOP_LOCATION_WORDS
                        or str(f.get("value", "")).strip().isdigit()
                        or bool(re.match(r'^\d{1,4}[-/.].*', str(f.get("value", "")).strip()))
                    )
                )
            ]

            has_source_filter = any(isinstance(f, dict) and f.get("field") == "source" for f in query_payload.get("filters", []))
            if not has_source_filter:
                src_val = extract_source_location(message)
                if src_val:
                    query_payload["filters"].append({
                        "field": "source",
                        "operator": "eq",
                        "value": src_val
                    })

            has_dest_filter = any(isinstance(f, dict) and f.get("field") == "destination" for f in query_payload.get("filters", []))
            if not has_dest_filter:
                dest_val = extract_destination_location(message)
                if dest_val:
                    query_payload["filters"].append({
                        "field": "destination",
                        "operator": "eq",
                        "value": dest_val
                    })

            # --- ShipmentMethod Post-Processing ---
            # Full list of recognized shipmentMethod values.
            if detected_sms and not _is_network_vs_local_query and not _is_trip_type_breakdown_query and not _is_across_trip_types:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                if len(detected_sms) > 1:
                    query_payload["filters"].append({
                        "field": "shipmentMethod",
                        "operator": "in",
                        "value": detected_sms
                    })
                    query_payload["groupBy"] = ["shipmentMethod"]
                    query_payload["metrics"] = [
                        {
                            "field": "shipmentNo",
                            "function": "count",
                            "alias": "trip_count"
                        }
                    ]
                else:
                    query_payload["filters"].append({
                        "field": "shipmentMethod",
                        "operator": "eq",
                        "value": detected_sms[0]
                    })
                if len(detected_sms) <= 1 and query_payload.get("groupBy") == ["shipmentMethod"]:
                    query_payload.pop("groupBy", None)
                    query_payload.pop("metrics", None)
                if detect_device_analytics(message):
                    query_payload["filters"].sort(key=_device_analytics_sort_key)
            elif _is_shipment_method_breakdown_query:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["shipmentMethod", "Route", "route", "RouteName", "routeName", "routeCategory", "routeType", "tripType"])
                ]
                query_payload["groupBy"] = ["shipmentMethod"]
                query_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "trip_count"
                    }
                ]
            elif _is_across_trip_types:
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]

            # --- Transporter & Vehicle Metrics Post-Processing ---
            has_vehicle_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            has_transporter_mention = bool(re.search(r'\btransporters?\b', msg_lower))
            has_count_unique_distinct = bool(re.search(r'\b(?:unique|distinct|total\s+number|count)\b', msg_lower))
            has_explicit_vehicle_count = bool(
                re.search(r'\b(?:count\s+of\s+vehicles?|vehicles?\s+count|count\s+of\s+vehicle)\b', msg_lower)
                or (has_vehicle_mention and bool(re.search(r'\b(?:distinct|unique)\b', msg_lower)))
            )
            _is_distinct_vehicle_query = (
                (
                    (
                        has_vehicle_mention
                        and has_count_unique_distinct
                        and not has_transporter_mention
                        and bool(
                            re.search(r'\b(?:distinct|unique|list|all)\b', msg_lower)
                            or re.search(r'\b(?:count\s+of\s+vehicles?|vehicles?\s+count|count\s+of\s+vehicle)\b', msg_lower)
                        )
                    )
                    or _is_active_vehicle_query
                )
                and ((not is_vendor_running_query) or has_explicit_vehicle_count)
                and not bool(detect_vehicle_master(message))
            )
            _is_distinct_transporter_query = (
                has_transporter_mention
                and has_count_unique_distinct
                and not has_vehicle_mention
                and not has_no_transporter
            )

            # --- Vehicle Number Post-Processing ---
            has_vehicle_filter = any(isinstance(f, dict) and f.get("field") in ("vehicleNo", "vehicle_no") for f in query_payload.get("filters", []))
            if not has_vehicle_filter and not _is_distinct_vehicle_query and not _is_active_vehicle_query and not _is_no_gps_vehicle_query:
                veh_match = re.search(r'\b(?:vehicle(?:\s*(?:no|number|num))?\s*[:=]?\s*|trips\s+of\s+)?([A-Za-z]{2}\s*\d{1,2}\s*[A-Za-z]{1,3}\s*\d{4})\b', message, re.IGNORECASE)
                if not veh_match:
                    veh_match = re.search(r'\bvehicle\s+(?:no|number|num|#)\s*[:=]?\s*([A-Za-z0-9_-]+)\b', message, re.IGNORECASE)
                if veh_match:
                    v_val = re.sub(r'\s+', '', veh_match.group(1)).strip().upper()
                    if v_val.lower() not in _STOP_LOCATION_WORDS and not v_val.isdigit() and len(v_val) > 2 and any(c.isdigit() for c in v_val):
                        query_payload["filters"].append({
                            "field": "vehicleNo",
                            "operator": "eq",
                            "value": v_val
                        })

            # --- Vehicle Master Post-Processing ---
            vm_detected = detect_vehicle_master(message)
            if vm_detected:
                has_vm = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master"):
                        f["field"] = "vehicleMaster"
                        f["operator"] = "eq"
                        f["value"] = vm_detected
                        has_vm = True
                        break
                if not has_vm:
                    query_payload.setdefault("filters", []).append({
                        "field": "vehicleMaster",
                        "operator": "eq",
                        "value": vm_detected
                    })
                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                query_payload.pop("analytics", None)

            # --- Route Master Post-Processing ---
            rm_detected = detect_route_master(message)
            if rm_detected:
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master", "fleetMaster", "fleet_master", "Route", "route", "RouteName", "routeName", "shipmentMethod"))
                ]
                has_rm = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ("routeMaster", "route_master"):
                        f["field"] = "routeMaster"
                        f["operator"] = "eq"
                        f["value"] = rm_detected
                        has_rm = True
                        break
                if not has_rm:
                    query_payload.setdefault("filters", []).append({
                        "field": "routeMaster",
                        "operator": "eq",
                        "value": rm_detected
                    })
                if rm_detected == "outside":
                    query_payload.setdefault("filters", []).append({
                        "field": "shipmentMethod",
                        "operator": "nin",
                        "value": ["Pick Up", "Delivery", "Pick Up and Delivery"]
                    })
                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                query_payload.pop("analytics", None)

            # --- Fleet Master Post-Processing ---
            fm_detected = detect_fleet_master(message)
            if fm_detected:
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master", "routeMaster", "route_master", "Route", "route", "RouteName", "routeName", "shipmentMethod"))
                ]
                has_fm = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ("fleetMaster", "fleet_master"):
                        f["field"] = "fleetMaster"
                        f["operator"] = "eq"
                        f["value"] = fm_detected
                        has_fm = True
                        break
                if not has_fm:
                    query_payload.setdefault("filters", []).append({
                        "field": "fleetMaster",
                        "operator": "eq",
                        "value": fm_detected
                    })
                if fm_detected == "outside":
                    query_payload.setdefault("filters", []).append({
                        "field": "shipmentMethod",
                        "operator": "nin",
                        "value": ["Pick Up", "Delivery", "Pick Up and Delivery"]
                    })
                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)
                query_payload.pop("analytics", None)

            if _is_each_region_query:
                query_payload["groupBy"] = ["region"]
                query_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "Shipments"
                    }
                ]
                query_payload.pop("select", None)
            elif _is_transporter_unique_vehicles_query:
                # Query asks for unique vehicles used by a specific transporter
                is_trips_and_vehicles = bool(re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_lower))
                query_payload["groupBy"] = ["vehicleNo"]
                if is_trips_and_vehicles:
                    query_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"},
                        {"field": "shipmentNo", "function": "count", "alias": "shipmentNo"}
                    ]
                else:
                    query_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                    ]
                query_payload["select"] = []
                if _is_include_cancelled:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    has_trip_status = False
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = "valid"
                            f["operator"] = "eq"
                            has_trip_status = True
                            break
                    if not has_trip_status:
                        query_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": "valid"
                        })
                if specific_transporter:
                    has_transporter_filter = False
                    trans_op = "in" if isinstance(specific_transporter, list) else "eq"
                    for f in query_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                            f["field"] = "transporter"
                            f["operator"] = trans_op
                            f["value"] = specific_transporter
                            has_transporter_filter = True
                            break
                    if not has_transporter_filter:
                        query_payload["filters"].append({
                            "field": "transporter",
                            "operator": trans_op,
                            "value": specific_transporter
                        })
                else:
                    query_payload["filters"] = [
                        f for f in query_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"])
                    ]
            elif _is_distinct_vehicle_query:
                # Query asks to list / count distinct vehicles (grouped by vehicleNo)
                query_payload["groupBy"] = ["vehicleNo"]
                query_payload["metrics"] = [
                    {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                ]
                # Remove select key — not needed for grouped vehicle queries
                query_payload.pop("select", None)
            elif is_top_transporters_query(message):
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"])
                ]
                query_payload["groupBy"] = ["transporter"]
                query_payload["metrics"] = [
                    {"field": "shipmentNo", "function": "count", "alias": "trip_count"}
                ]
                query_payload["sort"] = [
                    {"field": "trip_count", "order": "desc"}
                ]
                query_payload["select"] = []
            elif _is_distinct_transporter_query:
                # Query asks for total number of unique/distinct transporters
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"])
                ]
                query_payload["groupBy"] = ["Transporter"]
                query_payload["metrics"] = [
                    {"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}
                ]
                query_payload.pop("select", None)
                has_trip_status = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        f["operator"] = "eq"
                        f["value"] = "valid"
                        has_trip_status = True
                        break
                if not has_trip_status:
                    if "filters" not in query_payload:
                        query_payload["filters"] = []
                    query_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
            elif ("groupBy" not in query_payload or not query_payload["groupBy"]) and has_transporter_mention and has_count_unique_distinct:
                if has_vehicle_mention:
                    # Query asks for unique vehicles per transporter / breakdown
                    query_payload["groupBy"] = ["Transporter"]
                    query_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                    ]

            if is_vendor_running_query:
                if not has_date_specified:
                    query_payload["filters"] = [
                        f for f in query_payload.get("filters", [])
                        if not (isinstance(f, dict) and f.get("field") == "runDate")
                    ]
                if not _is_distinct_vehicle_query:
                    query_payload.pop("groupBy", None)
                    query_payload.pop("metrics", None)
                    query_payload["select"] = []

            if _is_transporter_unique_vehicles_query:
                def _transporter_unique_vehicles_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ["transporter", "Transporter"]:
                        return 5
                    return 10

                query_payload["filters"].sort(key=_transporter_unique_vehicles_sort_key)
                query_payload["select"] = []
            elif _is_active_vehicle_query:
                def _active_vehicle_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    return 10

                query_payload["filters"].sort(key=_active_vehicle_sort_key)
            elif _is_active_device_elock_query or _is_active_device_portable_query:
                def _active_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "groupId":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ["FixedELock", "PortableELock", "GPS", "FixedGPS"]:
                        return 5
                    elif field in ["routeCategory", "shipmentMethod"]:
                        return 6
                    return 10

                query_payload["filters"].sort(key=_active_device_sort_key)
            elif is_top_transporters_query(message):
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"])
                ]
                query_payload["groupBy"] = ["transporter"]
                query_payload["metrics"] = [
                    {"field": "shipmentNo", "function": "count", "alias": "trip_count"}
                ]
                query_payload["sort"] = [
                    {"field": "trip_count", "order": "desc"}
                ]
                query_payload["select"] = []
            elif _is_distinct_transporter_query:
                def _transporter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "runDate" and op == "gte":
                        return 2
                    elif field == "runDate" and op == "lte":
                        return 3
                    elif field == "groupId":
                        return 4
                    return 10

                query_payload["filters"].sort(key=_transporter_sort_key)
            elif _is_portable_elock_trip_query or _is_fixed_elock_trip_query:
                def _device_trip_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "groupId":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ["PortableELock", "FixedELock", "GPS", "FixedGPS"]:
                        return 5
                    elif field in ["shipmentMethod", "routeCategory"]:
                        return 6
                    return 10

                query_payload["filters"].sort(key=_device_trip_sort_key)
            elif _is_fixed_gps_combo:
                def _fixed_gps_combo_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "fixed-gps-status":
                        return 5
                    elif field in ["fixed-elock-status", "portable-elock-status"]:
                        return 6
                    elif field in ["shipmentMethod", "routeCategory"]:
                        return 7
                    return 10

                query_payload["filters"].sort(key=_fixed_gps_combo_sort_key)
            elif _is_fixed_gps_only or _is_portable_elock_only or _is_fixed_elock_only:
                def _single_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "GPS":
                        return 5
                    elif field == "FixedELock":
                        return 6
                    elif field == "PortableELock":
                        return 7
                    elif field == "routeCategory":
                        return 8
                    elif field == "shipmentMethod":
                        return 9
                    return 10

                query_payload["filters"].sort(key=_single_device_sort_key)
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "select"]:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                for k, v in query_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                query_payload.clear()
                query_payload.update(reordered)
            elif _is_trip_type_breakdown_query:
                def _trip_type_breakdown_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "groupId":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "tripType":
                        return 5
                    return 10

                query_payload["filters"].sort(key=_trip_type_breakdown_sort_key)
            elif _is_network_vs_local_query and not bool(detect_device_analytics(message)):
                is_non_gps = bool(re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower))
                def _network_vs_local_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if is_non_gps:
                        if field == "tripStatus":
                            return 1
                        elif field == "groupId":
                            return 2
                    else:
                        if field == "groupId":
                            return 1
                        elif field == "tripStatus":
                            return 2
                    if field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    return 10

                query_payload["filters"].sort(key=_network_vs_local_sort_key)
                if not is_non_gps:
                    query_payload.pop("select", None)
            elif _is_no_gps_vehicle_query:
                def _no_gps_vehicle_sort_key(flt):
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
                    elif field == "GPS":
                        return 3.5
                    elif field == "runDate" and op == "gte":
                        return 4
                    elif field == "runDate" and op == "lte":
                        return 5
                    elif field in ["routeCategory", "routeType", "tripType"]:
                        return 6
                    elif field == "shipmentMethod":
                        return 7
                    return 10

                query_payload["filters"].sort(key=_no_gps_vehicle_sort_key)
                if not specific_transporter:
                    query_payload.pop("select", None)
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "groupBy", "metrics"]:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                for k, v in query_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                query_payload.clear()
                query_payload.update(reordered)
            elif _is_gps_trip_query:
                def _gps_trip_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "GPS":
                        return 3
                    elif field == "runDate" and op == "gte":
                        return 4
                    elif field == "runDate" and op == "lte":
                        return 5
                    elif field == "shipmentMethod":
                        return 6
                    elif field == "routeCategory":
                        return 7
                    return 10

                query_payload["filters"].sort(key=_gps_trip_sort_key)
            elif target_vendor and not _is_devices_together:
                def _vendor_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 5
                    elif field in ("gps", "GPS", "FixedGPS", "fixedgps"):
                        return 6
                    elif field == "routeCategory":
                        return 7
                    elif field == "shipmentMethod":
                        return 8
                    return 10

                query_payload["filters"].sort(key=_vendor_sort_key)

            if _is_include_cancelled:
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                ]
            else:
                target_status = status_override if status_override is not None else "valid"
                found_status = False
                for f in query_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        f["value"] = target_status
                        f["operator"] = "eq"
                        found_status = True
                        break
                if not found_status:
                    query_payload.setdefault("filters", []).append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": target_status
                    })
                if target_vendor and not _is_devices_together:
                    query_payload["filters"].sort(key=_vendor_sort_key)

            if is_vendor_running_query and not has_date_specified:
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "runDate")
                ]
            if is_vendor_running_query and target_vendor and not _is_devices_together:
                query_payload["filters"].sort(key=_vendor_sort_key)

            if _is_transporter_unique_vehicles_query:
                query_payload["filters"].sort(key=_transporter_unique_vehicles_sort_key)
                query_payload["select"] = []

            if _is_fixed_portable_elock_combo:
                def _fixed_portable_combo_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "portable-elock-status":
                        return 5
                    elif field == "fixed-elock-status":
                        return 6
                    elif field in ("routeCategory", "routeType"):
                        return 7
                    return 10

                query_payload["filters"].sort(key=_fixed_portable_combo_sort_key)
                query_payload.pop("select", None)
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)

            if _is_multi_device_trip_query:
                def _multi_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "FixedELock":
                        return 5
                    elif field == "PortableELock":
                        return 6
                    elif field == "GPS":
                        return 7
                    elif field in ("atd", "ata", "ATD", "ATA"):
                        return 8
                    return 10

                query_payload["filters"].sort(key=_multi_device_sort_key)
                query_payload["select"] = []
                query_payload.pop("groupBy", None)
                query_payload.pop("metrics", None)

            if detected_regions or any(isinstance(f, dict) and f.get("field") == "region" for f in query_payload.get("filters", [])):
                def _region_filter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ("source", "destination", "From", "To"):
                        return 5
                    elif field == "region":
                        return 6
                    elif field in ("Route", "route", "RouteName", "routeName"):
                        return 7
                    elif field in ("transporter", "Transporter"):
                        return 8
                    elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 9
                    elif field == "shipmentMethod":
                        return 10
                    elif field in ("routeCategory", "routeType", "tripType"):
                        return 11
                    elif field == "priority":
                        return 12
                    elif field in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                        return 13
                    elif field in ("atd", "ata", "ATD", "ATA"):
                        return 14
                    return 20

                if not (_is_fixed_portable_elock_combo or _is_transporter_unique_vehicles_query or is_vendor_running_query):
                    query_payload["filters"].sort(key=_region_filter_sort_key)

            if detected_route or any(isinstance(f, dict) and f.get("field") in ("Route", "route", "RouteName", "routeName") for f in query_payload.get("filters", [])):
                def _route_filter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ("source", "destination", "From", "To"):
                        return 5
                    elif field == "region":
                        return 6
                    elif field in ("Route", "route", "RouteName", "routeName"):
                        return 7
                    elif field in ("transporter", "Transporter"):
                        return 8
                    elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 9
                    elif field == "shipmentMethod":
                        return 10
                    elif field in ("routeCategory", "routeType", "tripType"):
                        return 11
                    elif field == "priority":
                        return 12
                    elif field in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                        return 13
                    elif field in ("atd", "ata", "ATD", "ATA"):
                        return 14
                    return 20

                if not (_is_fixed_portable_elock_combo or _is_transporter_unique_vehicles_query or is_vendor_running_query):
                    query_payload["filters"].sort(key=_route_filter_sort_key)

            def _standard_filter_sort_key(flt):
                if not isinstance(flt, dict):
                    return 99
                field = flt.get("field")
                op = flt.get("operator")
                if field == "groupId":
                    return 1
                elif field == "tripStatus":
                    return 2
                elif field in ("GPS", "FixedGPS", "FixedELock", "PortableELock", "gps", "fixedgps"):
                    return 2.5
                elif field == "runDate" and op == "gte":
                    return 3
                elif field == "runDate" and op == "lte":
                    return 4
                elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and op == "gte":
                    return 4.1
                elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and op == "lte":
                    return 4.2
                elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance"):
                    return 4.3
                elif field in ("area", "branch"):
                    return 4.4
                elif field in ("vehicleMaster", "vehicle_master", "routeMaster", "route_master", "fleetMaster", "fleet_master"):
                    return 4.5
                elif field in ("source", "destination", "From", "To"):
                    return 5
                elif field == "region":
                    return 6
                elif field in ("vehicleNo", "vehicle_no", "vehicle"):
                    return 7
                elif field in ("Route", "route", "RouteName", "routeName"):
                    return 8
                elif field in ("transporter", "Transporter"):
                    return 9
                elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                    return 10
                elif field == "shipmentMethod":
                    return 11
                elif field in ("routeCategory", "routeType", "tripType"):
                    return 12
                elif field == "priority":
                    return 13
                elif field in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                    return 14
                elif field in ("atd", "ata", "ATD", "ATA"):
                    return 15
                return 20

            norm_top_vendors = detect_top_device_vendors(message)
            if norm_top_vendors:
                query_payload["analytics"] = [{"type": norm_top_vendors["analytics_type"]}]
                query_payload["groupBy"] = [norm_top_vendors["vendor_field"]]
                query_payload["metrics"] = [{"field": "shipmentNo", "function": "count", "alias": "TotalTrip"}]
                query_payload["sort"] = [{"field": "TotalTrip", "order": "desc"}]
                query_payload.pop("select", None)
                v_fld = norm_top_vendors["vendor_field"]
                query_payload["filters"] = [
                    f for f in query_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock", "gps", "GPS", "FixedGPS", "FixedELock", "PortableELock"))
                ]
                query_payload["filters"].append({
                    "field": v_fld,
                    "operator": "eq",
                    "value": "existing"
                })
                query_payload["filters"].sort(key=_device_analytics_sort_key)
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "groupBy", "metrics", "sort"]:
                    if k in query_payload:
                        reordered[k] = query_payload[k]
                query_payload.clear()
                query_payload.update(reordered)
            elif not (_is_fixed_portable_elock_combo or _is_multi_device_trip_query or _is_transporter_unique_vehicles_query or is_vendor_running_query or _is_active_device_elock_query or _is_active_device_portable_query or _is_distinct_transporter_query or _is_portable_elock_trip_query or _is_fixed_elock_trip_query or _is_fixed_gps_combo or _is_fixed_gps_only or _is_portable_elock_only or _is_fixed_elock_only or _is_trip_type_breakdown_query or _is_network_vs_local_query or _is_no_gps_vehicle_query or _is_gps_trip_query or (target_vendor and not _is_devices_together) or bool(detect_device_analytics(message))):
                query_payload["filters"].sort(key=_standard_filter_sort_key)

            date_grp = detect_date_grouping(message)
            if date_grp:
                query_payload["groupBy"] = ["runDate"]
                query_payload["groupByFormat"] = {"runDate": date_grp}
                query_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "TotalTrip"
                    }
                ]
                query_payload["sort"] = [
                    {
                        "field": "runDate",
                        "order": "asc"
                    }
                ]
                if "select" not in query_payload:
                    query_payload["select"] = []

            if "filters" in query_payload and isinstance(query_payload["filters"], list):
                query_payload["filters"] = [
                    f for f in query_payload["filters"]
                    if isinstance(f, dict) and f.get("value") is not None and str(f.get("value")).lower() != "null"
                ]

            reordered = {}
            for k in ["entity", "operation", "analytics", "filters", "select", "groupBy", "groupByFormat", "metrics", "sort"]:
                if k in query_payload:
                    reordered[k] = query_payload[k]
            for k, v in query_payload.items():
                if k not in reordered:
                    reordered[k] = v
            query_payload.clear()
            query_payload.update(reordered)

            log_intent_detected(session_id, "trip_report")
            log_entities_extracted(session_id, query_payload)

            print(f"[DEBUG_QUERY] Session: {session_id} | Source: {extraction_source} | LLM Status: {llm_status} | Provider: {getattr(self.llm_client, 'provider', 'unknown')} | Model: {getattr(self.llm_client, 'model', 'unknown')}")

            return {
                "intent": "trip_report",
                "entities": query_payload,
                "source": extraction_source,
                "llm_status": llm_status,
                "debug": {
                    "source": extraction_source,
                    "fallback": getattr(self.llm_client, "last_fallback_to_local", False),
                    "fallback_to_local": getattr(self.llm_client, "last_fallback_to_local", False),
                    "llm_attempted": (extraction_source == "llm"),
                    "llm_success": (llm_status == "success") if extraction_source == "llm" else None,
                    "llm_provider": getattr(self.llm_client, "last_used_provider", getattr(self.llm_client, "provider", "unknown")) if extraction_source == "llm" else None,
                    "llm_model": getattr(self.llm_client, "last_used_model", getattr(self.llm_client, "model", "unknown")) if extraction_source == "llm" else None,
                    "llm_status": llm_status,
                    "error_type": None,
                    "error": None
                }
            }
            
        except Exception as exc:
            log_error(session_id, "INTENT_DETECTION_FAILED", str(exc))
            fb_error_str = str(exc)
            is_rate_limit = (
                isinstance(exc, LLMRateLimitError)
                or "rate_limit_exceeded" in fb_error_str.lower()
                or "429" in fb_error_str
                or "itpm" in fb_error_str.lower()
                or "tokens per minute" in fb_error_str.lower()
            )
            fb_error_type = "rate_limit_exceeded" if is_rate_limit else ("timeout" if "timeout" in fb_error_str.lower() else "llm_error")
            fb_llm_status = "rate_limit_exceeded" if is_rate_limit else "error"
            # Fallback structure
            fb_single_shipment = extract_single_shipment_lookup(message)
            if fb_single_shipment:
                fallback_payload = {
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
                            "value": fb_single_shipment
                        }
                    ],
                    "select": []
                }
                print(f"[DEBUG_QUERY] Session: {session_id} | Source: regex | LLM Status: {fb_llm_status or 'fallback'} | Mode: single_shipment_lookup")
                return {
                    "intent": "trip_report",
                    "entities": fallback_payload,
                    "source": "regex",
                    "llm_status": fb_llm_status or "fallback",
                    "debug": {
                        "source": "regex",
                        "llm_status": fb_llm_status or "fallback",
                        "error_type": fb_error_type
                    }
                }

            fb_top_vendors = detect_top_device_vendors(message)
            if fb_top_vendors:
                start_date, end_date = extract_date_range(message, reference_now=now)
                if not start_date or not end_date:
                    start_date, end_date = _resolve_month_range(msg_lower, now)
                if not start_date or not end_date:
                    if "august" in msg_lower or "aug" in msg_lower:
                        start_date = "2026-08-01 00:00:00"
                        end_date = "2026-08-31 23:59:59"
                    elif "this month" in msg_lower or "current month" in msg_lower:
                        start_date = this_month_start
                        end_date = this_month_end
                filters = [
                    {"field": "groupId", "operator": "eq", "value": "0041"},
                    {"field": "tripStatus", "operator": "eq", "value": "valid"}
                ]
                if start_date:
                    filters.append({"field": "runDate", "operator": "gte", "value": start_date})
                if end_date:
                    filters.append({"field": "runDate", "operator": "lte", "value": end_date})
                filters.append({"field": fb_top_vendors["vendor_field"], "operator": "eq", "value": "existing"})
                fallback_payload = {
                    "entity": "trip",
                    "operation": "count",
                    "analytics": [
                        {
                            "type": fb_top_vendors["analytics_type"]
                        }
                    ],
                    "filters": filters,
                    "groupBy": [
                        fb_top_vendors["vendor_field"]
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
                print(f"[DEBUG_QUERY] Session: {session_id} | Source: regex | LLM Status: {fb_llm_status or 'fallback'} | Mode: top_device_vendors")
                return {
                    "intent": "trip_report",
                    "entities": fallback_payload,
                    "source": "regex",
                    "llm_status": fb_llm_status or "fallback",
                    "debug": {
                        "source": "regex",
                        "llm_status": fb_llm_status or "fallback",
                        "error_type": fb_error_type
                    }
                }

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
            fb_target_vendor = None
            fb_dev_vendor_match = None
            fb_is_vendor_running_query = False
            fb_is_past_running = False

            if re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower):
                fb_target_vendor = "ThirdParty"
            elif re.search(r'\b(secutrak|icici|ilgic)\b', msg_lower):
                fb_target_vendor = "Secutrak"
            elif re.search(r'\bwheelseye\b', msg_lower):
                fb_target_vendor = "Wheelseye"
            else:
                fb_dev_vendor_match = re.search(r'\b([A-Za-z0-9_-]+)\s+devices?\s+(?:report|data)\b', msg_lower)
                if not fb_dev_vendor_match:
                    fb_dev_vendor_match = re.search(r'\breport\s+(?:for\s+)?(?:all\s+)?([A-Za-z0-9_-]+)\s+devices?\b', msg_lower)
                if not fb_dev_vendor_match:
                    fb_dev_vendor_match = re.search(r'\b(?:was\s+running|is\s+running|running)\s+on\s+([A-Za-z0-9_-]+)\s+devices?\b', msg_lower)
                if fb_dev_vendor_match:
                    candidate = fb_dev_vendor_match.group(1)
                    if candidate.lower() not in ["the", "a", "an", "generate", "download", "get", "show", "fetch", "all", "party", "gps", "elock", "e-lock"]:
                        fb_target_vendor = candidate

            fb_is_network_vs_local_query = bool(
                re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or|to)\s+local\b', msg_lower)
                or re.search(r'\blocal\s+(?:vs\.?|versus|and|or|to)\s+network\b', msg_lower)
                or re.search(r'\bintercity\s+(?:vs\.?|versus|and|or|to)\s+intracity\b', msg_lower)
                or re.search(r'\bintracity\s+(?:vs\.?|versus|and|or|to)\s+intercity\b', msg_lower)
                or (
                    re.search(r'\b(?:compare|comparison|versus|vs\.?|difference|ratio)\b', msg_lower)
                    and (
                        (re.search(r'\bnetwork\b', msg_lower) and re.search(r'\blocal\b', msg_lower))
                        or (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
                    )
                )
                or (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
            )
            fb_is_each_region_query = bool(
                re.search(
                    r'\b(?:in\s+each\s+region|for\s+each\s+region|across\s+(?:each|all)\s+regions?|per\s+region|each\s+region|by\s+region|region[\s-]wise|region\s+breakdown|breakdown\s+by\s+region|breakdown\s+across\s+regions?|breakdown\s+of\s+regions?)\b',
                    msg_lower
                )
            )
            fb_is_priority_query = bool(
                re.search(r'\bpriorit(?:y|ies)\b', msg_lower)
            )
            fb_is_priority_vs_query = bool(
                re.search(r'\b(?:high\s+priority\s+(?:vs\.?|versus|and|or|to)\s+(?:standard|normal|low)(?:\s+priority)?)\b', msg_lower)
                or re.search(r'\b(?:(?:standard|normal|low)\s+(?:priority\s+)?(?:vs\.?|versus|and|or|to)\s+high\s+priority)\b', msg_lower)
                or (
                    re.search(r'\b(?:compare|comparison|versus|vs\.?|difference|ratio)\b', msg_lower)
                    and re.search(r'\bhigh\s+priority\b', msg_lower)
                    and re.search(r'\b(?:standard|normal|low)\b', msg_lower)
                )
                or re.search(r'\b(?:in\s+each\s+priority|per\s+priority|by\s+priority|priority[\s-]wise|priority\s+breakdown|breakdown\s+by\s+priority|breakdown\s+across\s+priorit(?:y|ies))\b', msg_lower)
            )
            fb_is_closeremark_query = bool(
                re.search(r'\b(?:closed|close|closing)\s+(?:automatically\s+)?(?:via\s+|by\s+|through\s+)?(?:manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b', msg_lower)
                or re.search(r'\b(?:close\s*remark[s]?|closeremark[s]?|remark[s]?)\s*[:=]?\s*(?:manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b', msg_lower)
                or re.search(r'\b(?:closed|close|closing)\s+(?:with\s+(?:remark\s+)?)?(?:manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b', msg_lower)
                or re.search(r'\bvia\s+(?:forcefull|forcefully|forceful|geofence|geo[\s-]fence|polygon)\b', msg_lower)
                or re.search(r'\b(?:forcefully|forcefull|forceful|geofence|polygon)\s+(?:closed|remark)\b', msg_lower)
                or ((re.search(r'\b(?:forcefully|forcefull|forceful|geofence|polygon)\b', msg_lower) is not None) and (re.search(r'\b(?:closed|close)\b', msg_lower) is not None))
            )
            fb_is_atd_ata_query = bool(
                re.search(r'\b(?:atd|ata)\b', msg_lower)
            )
            fb_is_trip_type_breakdown_query = bool(
                re.search(r'\bbreakdown\b', msg_lower)
                and (
                    re.search(r'\b(?:network\s+or\s+intercity|intercity\s+or\s+network)\b', msg_lower)
                    or re.search(r'\bintercity\s+routes?\b', msg_lower)
                    or re.search(r'\bnetwork\s+routes?\b', msg_lower)
                    or re.search(r'\b(?:local\s+or\s+intracity|intracity\s+or\s+local)\b', msg_lower)
                    or re.search(r'\bintracity\s+routes?\b', msg_lower)
                    or re.search(r'\blocal\s+routes?\b', msg_lower)
                    or (re.search(r'\b(?:intercity|network)\b', msg_lower) and re.search(r'\broutes?\b', msg_lower))
                )
            )
            fb_is_shipment_method_breakdown_query = bool(
                re.search(r'\b(?:across|by|each|all|breakdown|in\s+(?:each|every))\s+(?:major\s+)?(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_lower)
                or re.search(r'\b(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\s+breakdown\b', msg_lower)
                or re.search(r'\btrip\s+count\s+across\s+(?:major\s+)?(?:feeder|route|shipment)\b', msg_lower)
                or re.search(r'\bmajor\s+(?:feeder\s+types?|route\s+types?|shipment\s+methods?)\b', msg_lower)
            )
            fb_is_across_trip_types = bool(
                (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
                or (re.search(r'\bnetwork\b', msg_lower) and re.search(r'\blocal\b', msg_lower))
                or re.search(r'\bnetwork\s+(?:vs\.?|versus|and|or|to)\s+local\b', msg_lower)
                or re.search(r'\blocal\s+(?:vs\.?|versus|and|or|to)\s+network\b', msg_lower)
                or re.search(r'\bintercity\s+(?:vs\.?|versus|and|or|to)\s+intracity\b', msg_lower)
                or re.search(r'\bintracity\s+(?:vs\.?|versus|and|or|to)\s+intercity\b', msg_lower)
                or re.search(r'\bacross\s+(?:both\s+)?trip\s+types\b', msg_lower)
            or (re.search(r'\bacross\b', msg_lower) and (
                (re.search(r'\bintercity\b', msg_lower) and re.search(r'\bintracity\b', msg_lower))
                or (re.search(r'\bnetwork\b', msg_lower) and re.search(r'\blocal\b', msg_lower))
            ))
            )
            fb_multi_device_gps = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower))
            fb_multi_device_fixed = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_lower))
            fb_multi_device_portable = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_lower))
            fb_multi_device_count = sum([fb_multi_device_gps, fb_multi_device_fixed, fb_multi_device_portable])
            fb_is_multi_device_trip_query = bool(
                fb_multi_device_count >= 2
                and fb_multi_device_gps
                and not re.search(r'\bvehicles?\b', msg_lower)
                and not re.search(r'\btransporters?\b', msg_lower)
                and not fb_is_network_vs_local_query
                and not re.search(r'\b(only|single|sole|alone|exclusive|exclusively)\b', msg_lower)
                and not re.search(r'\b(?:without|excluding|except)\s+.*(?:fixed|portable|gps|lock|elock)\b', msg_lower)
            )
            fb_specific_transporter = extract_specific_transporter(message)
            fb_is_no_gps_vehicle_query = bool(
                (
                    re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_lower)
                    or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_lower)
                    or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_lower)
                    or re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower)
                )
                and re.search(r'\bvehicles?\b', msg_lower)
            )
            fb_has_vehicle_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            fb_has_transporter_mention = bool(re.search(r'\btransporters?\b', msg_lower))
            fb_has_count_unique_distinct = bool(re.search(r'\b(?:unique|distinct|total\s+number|count)\b', msg_lower))
            fb_is_trips_and_vehicles_query = bool(
                re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_lower)
            )
            fb_is_transporter_unique_vehicles_query = bool(
                fb_is_trips_and_vehicles_query or (
                    fb_specific_transporter and fb_has_vehicle_mention and not fb_is_no_gps_vehicle_query and (
                        fb_has_count_unique_distinct
                        or bool(re.search(r'\b(?:lis|list|how\s+many|which|show|all|what|get)\b', msg_lower))
                        or bool(re.search(r'\b(?:on\s+trip|was\s+on|were\s+on)\b', msg_lower))
                    )
                )
            )
            fb_has_no_transporter = bool(
                re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+transporters?\b', msg_lower)
                or re.search(r'\btransporters?\s+(?:is\s+)?(?:na|not\s+available)\b', msg_lower)
            )
            fb_is_distinct_transporter_query = bool(
                fb_has_transporter_mention
                and fb_has_count_unique_distinct
                and not fb_has_vehicle_mention
                and not fb_has_no_transporter
                and not is_top_transporters_query(message)
            )

            if fb_has_no_transporter:
                transporter_field_name = "transporter"
                has_fb_transporter_flt = False
                for f in fallback_payload["filters"]:
                    if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                        f["field"] = transporter_field_name
                        f["operator"] = "eq"
                        f["value"] = "na"
                        has_fb_transporter_flt = True
                        break
                if not has_fb_transporter_flt:
                    fallback_payload["filters"].append({
                        "field": transporter_field_name,
                        "operator": "eq",
                        "value": "na"
                    })
            elif fb_specific_transporter:
                transporter_field_name = "transporter"
                fb_trans_op = "in" if isinstance(fb_specific_transporter, list) else "eq"
                has_fb_transporter_flt = False
                for f in fallback_payload["filters"]:
                    if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                        f["field"] = transporter_field_name
                        f["operator"] = fb_trans_op
                        f["value"] = fb_specific_transporter
                        has_fb_transporter_flt = True
                        break
                if not has_fb_transporter_flt:
                    fallback_payload["filters"].append({
                        "field": transporter_field_name,
                        "operator": fb_trans_op,
                        "value": fb_specific_transporter
                    })

            # --- Region Fallback Extraction ---
            fb_detected_regions = detect_regions(message)
            if not fb_detected_regions and prev_filters:
                for pf in prev_filters:
                    if isinstance(pf, dict) and pf.get("field") == "region":
                        pv = pf.get("value")
                        if isinstance(pv, list):
                            fb_detected_regions = pv
                        elif isinstance(pv, str) and pv:
                            fb_detected_regions = [pv]
                        break

            has_fb_region_flt = False
            for f in fallback_payload.get("filters", []):
                if isinstance(f, dict) and f.get("field") == "region":
                    has_fb_region_flt = True
                    if fb_detected_regions:
                        if len(fb_detected_regions) > 1:
                            f["operator"] = "in"
                            f["value"] = fb_detected_regions
                        else:
                            f["operator"] = "eq"
                            f["value"] = fb_detected_regions[0]
                    break

            if fb_is_each_region_query:
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "region")
                ]
            elif not has_fb_region_flt and fb_detected_regions:
                if len(fb_detected_regions) > 1:
                    fallback_payload.setdefault("filters", []).append({
                        "field": "region",
                        "operator": "in",
                        "value": fb_detected_regions
                    })
                else:
                    fallback_payload.setdefault("filters", []).append({
                        "field": "region",
                        "operator": "eq",
                        "value": fb_detected_regions[0]
                    })

            # --- Route Fallback Extraction ---
            fb_detected_route = extract_route_code(message)
            if not fb_detected_route and prev_filters:
                for pf in prev_filters:
                    if isinstance(pf, dict) and pf.get("field") in ("Route", "route", "RouteName", "routeName"):
                        fb_detected_route = pf.get("value")
                        break

            has_fb_route_flt = False
            for f in fallback_payload.get("filters", []):
                if isinstance(f, dict) and f.get("field") in ("Route", "route", "RouteName", "routeName"):
                    has_fb_route_flt = True
                    if fb_detected_route:
                        f["field"] = "Route"
                        f["operator"] = "eq"
                        f["value"] = fb_detected_route
                    break

            if not has_fb_route_flt and fb_detected_route:
                fallback_payload.setdefault("filters", []).append({
                    "field": "Route",
                    "operator": "eq",
                    "value": fb_detected_route
                })

            if has_status_keyword and status_override:
                fallback_payload["filters"].append({
                    "field": "tripStatus",
                    "operator": "eq",
                    "value": status_override
                })
            else:
                # Basic keyword check for fallback — only inject tripStatus when
                # the user explicitly mentioned a status term or a shipmentMethod. Never default otherwise.
                fb_detected_sms = detect_shipment_methods(message)
                if fb_is_network_vs_local_query or fb_is_trip_type_breakdown_query or fb_is_across_trip_types or fb_is_shipment_method_breakdown_query:
                    fb_detected_sms = [sm for sm in fb_detected_sms if sm != 'network']
                fb_detected_sm = fb_detected_sms[0] if fb_detected_sms else None
                fb_is_completed_and_closed = bool(
                    re.search(r'\b(?:completed\s+(?:and|&)\s+closed|closed\s+(?:and|&)\s+completed)\b', msg_lower)
                )
                fb_is_non_cancelled = bool(
                    re.search(r'\b(?:non[\s-]?cancell?ed|not\s+cancell?ed)\b', msg_lower)
                )
                fb_is_include_cancelled = bool(
                    re.search(
                        r'\b(?:includ(?:e|ing|es)|with)\s+(?:all\s+|any\s+|the\s+)?(?:cancell?ed|cancel)(?:\s+trips?)?\b'
                        r'|\b(?:cancell?ed|cancel)\s+trips?\s+(?:includ(?:ed?|ing))\b'
                        r'|\binclud(?:e|ing|es)\s+cancel\b',
                        msg_lower
                    )
                )
                fb_is_active_vehicle_query = bool(
                    re.search(r'\b(?:active\s+vehicles?|vehicles?\s+(?:that\s+were\s+|that\s+are\s+)?active)\b', msg_lower)
                )

                fb_is_past_running = bool(re.search(r'\b(?:was\s+running|were\s+running|had\s+been\s+running|operated)\b', msg_lower))

                fb_is_vendor_running_query = (not fb_is_past_running) and (not bool(re.search(r'\boperating\s+on\s+(?:the\s+)?field\b', msg_lower))) and bool(
                    re.search(r'\b(?:was\s+running|is\s+running|running)\s+on\s+(?:\w+\s+)?devices?\b', msg_lower)
                    or re.search(r'\b(?:vehicles?|trips?)\s+(?:which\s+was\s+running|running|operating)\s+on\b', msg_lower)
                    or (
                        re.search(r'\b(?:was\s+running|running)\b', msg_lower)
                        and re.search(r'\b(?:wheelseye|secutrak|icici|ilgic)\b', msg_lower)
                        and re.search(r'\b(?:vehicles?|devices?)\b', msg_lower)
                    )
                )

                if fb_is_past_running:
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "valid"
                    })
                    has_status_keyword = True
                    status_override = "valid"
                elif fb_is_vendor_running_query:
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "running"
                    })
                    has_status_keyword = True
                    status_override = "running"
                elif fb_is_include_cancelled:
                    has_status_keyword = False
                    status_override = None
                elif (
                    any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["valid"])
                    or fb_is_non_cancelled
                    or fb_is_completed_and_closed
                    or re.search(r'\bdevice\s+(?:report|data)\b|\breport\s+for\s+(?:all\s+)?(?:\w+\s+)?devices?\b|\bdevices?\s+report\b', msg_lower)
                    or re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower)
                    or fb_is_distinct_transporter_query
                    or fb_is_transporter_unique_vehicles_query
                    or (re.search(r'\btransporters?\b', msg_lower) and any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["used"]))
                    or (
                        re.search(r'\bvehicles?\b', msg_lower)
                        and re.search(r'\bused\b', msg_lower)
                        and re.search(r'\b(?:distinct|unique|list|all)\b', msg_lower)
                    )
                    or fb_is_active_vehicle_query
                    or fb_is_no_gps_vehicle_query
                    or fb_is_network_vs_local_query
                    or fb_is_trip_type_breakdown_query
                    or fb_is_each_region_query
                    or fb_is_priority_query
                    or fb_is_closeremark_query
                    or fb_is_atd_ata_query
                ):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "valid"
                    })
                    has_status_keyword = True
                    status_override = "valid"
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["completed", "finish", "finished", "closed", "inactive"]):
                    fb_is_inactive_for_trip = True
                    has_inactive = re.search(r'\binactive\b', msg_lower) is not None
                    if has_inactive:
                        explicit_inactive_trip = (
                            (re.search(r'\binactive\s+trips?\b', msg_lower) is not None and not re.search(r'\b(?:gps|fixed[\s-]gps|lock|elock|e-lock|portable|fixed|device)\s+inactive\s+trips?\b', msg_lower)) or
                            re.search(r'\btrips?\s+(?:status\s+)?is\s+inactive\b', msg_lower) is not None or
                            re.search(r'\btrips?\s+are\s+inactive\b', msg_lower) is not None
                        )
                        has_device_inactive = bool(
                            re.search(r'\b(?:gps|fixed[\s-]gps|lock|elock|e-lock|portable|fixed|device)\s+inactive\b', msg_lower)
                            or (detect_device_analytics(message) and any(
                                f.get("value") == "inactive" for f in (detect_device_analytics(message).get("device_filters") or [])
                            ))
                        )
                        if not explicit_inactive_trip and has_device_inactive:
                            fb_is_inactive_for_trip = False

                    if fb_is_inactive_for_trip:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": "closed"
                        })
                        has_status_keyword = True
                        status_override = "closed"
                    else:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": "valid"
                        })
                        has_status_keyword = True
                        status_override = "valid"
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["cancelled", "canceled"]) and not fb_is_non_cancelled:
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "cancelled"
                    })
                    has_status_keyword = True
                    status_override = "cancelled"
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["cancel"]) and not fb_is_non_cancelled and not fb_is_include_cancelled:
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "cancel"
                    })
                    has_status_keyword = True
                    status_override = "cancel" 
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["active", "open"]):
                    fb_is_active_for_trip = True
                    has_active = re.search(r'\bactive\b', msg_lower) is not None
                    has_open = re.search(r'\bopen\b', msg_lower) is not None
                    if has_active and not has_open:
                        explicit_active_trip = (
                            (re.search(r'\bactive\s+trips?\b', msg_lower) is not None and not re.search(r'\b(?:gps|lock|elock|e-lock|portable|fixed|device)\s+active\s+trips?\b', msg_lower)) or
                            re.search(r'\btrips?\s+(?:status\s+)?is\s+active\b', msg_lower) is not None or
                            re.search(r'\btrips?\s+are\s+active\b', msg_lower) is not None
                        )
                        if not explicit_active_trip:
                            has_device_active = bool(
                                re.search(r'\bactive\s+(?:device|devices|fixed|portable|gps|lock|elock|e-lock)\b', msg_lower)
                                or re.search(r'\b(?:where\s+)?(?:gps|fixed[\s-]gps|fixed|lock|elock|e-lock|portable|device|devices)\s+(?:is\s+|are\s+|was\s+)?active\b', msg_lower)
                                or fb_is_active_vehicle_query
                                or bool(detect_device_analytics(message))
                            )
                            if has_device_active:
                                fb_is_active_for_trip = False

                    if fb_is_active_for_trip:
                        status_val = "running" if (len(fb_detected_sms) > 1 or re.search(r'\bdelivery\s+with\s+pick\b', msg_lower)) else "active"
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": status_val
                        })
                        has_status_keyword = True
                        status_override = status_val
                    else:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": "valid"
                        })
                        has_status_keyword = False
                        status_override = "valid"
                elif any(re.search(r'\b' + re.escape(w) + r'\b', msg_lower) for w in ["running", "scheduled", "sceduled", "transit", "ongoing", "moving", "intransit", "live"]) or re.search(r'\b(?:in[\s-]transit|en[\s-]route|on\s+the\s+way)\b', msg_lower):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "running"
                    })
                    has_status_keyword = True
                    status_override = "running"
                elif fb_detected_sm and not (bool(extracted_start and extracted_end) or bool(re.search(r'\b(today|yesterday|this\s+week|current\s+week|last\s+week|this\s+month|current\s+month|last\s+month)\b', msg_lower)) or bool(_resolve_month_range(msg_lower, now)[0])):
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "running"
                    })
                    has_status_keyword = True
                    status_override = "running"
                else:
                    # In all other payloads, default to valid
                    fallback_payload["filters"].append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": "valid"
                    })
                    has_status_keyword = False
                    status_override = "valid"
            fb_has_date_specified = bool(
                (extracted_start and extracted_end)
                or re.search(r'\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\b', msg_lower)
                or re.search(r'\b(?:today|yesterday|this\s+week|last\s+week|this\s+month|last\s+month)\b', msg_lower)
            )
            if fb_is_vendor_running_query and not fb_has_date_specified:
                pass
            elif extracted_start and extracted_end:
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
                elif re.search(r'\b(?:this|current)\s+week\b', msg_lower):
                    fallback_payload["filters"].append({"field": "runDate", "operator": "gte", "value": this_week_start})
                    fallback_payload["filters"].append({"field": "runDate", "operator": "lte", "value": this_week_end})
                elif re.search(r'\blast\s+week\b', msg_lower):
                    fallback_payload["filters"].append({"field": "runDate", "operator": "gte", "value": last_week_start})
                    fallback_payload["filters"].append({"field": "runDate", "operator": "lte", "value": last_week_end})
                elif re.search(r'\b(?:this|current)\s+month\b', msg_lower):
                    fallback_payload["filters"].append({"field": "runDate", "operator": "gte", "value": this_month_start})
                    fallback_payload["filters"].append({"field": "runDate", "operator": "lte", "value": this_month_end})
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
            fb_has_veh_mention = bool(re.search(r'\bvehicles?\b', msg_lower))
            fb_without_match = re.search(r'\b(?:without|excluding|except|no|not\s+having)\b\s*(.*)', msg_lower)
            fb_without_text = fb_without_match.group(1) if fb_without_match else ""

            fb_without_fixed_elock = (
                bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', fb_without_text))
                or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?fixed[\s-]*(?:e[\s-]?)?locks?\b', msg_lower))
                or (bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:e[\s-]?)?locks?\b', msg_lower)) and not bool(re.search(r'\bportab?le\b', fb_without_text)))
            )
            fb_without_portable = (
                bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', fb_without_text))
                or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?portab?le\b', msg_lower))
                or (bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:e[\s-]?)?locks?\b', msg_lower)) and not bool(re.search(r'\bfixed\b', fb_without_text)))
            )
            fb_without_gps = (
                bool(re.search(r'\b(?:fixed[\s-]*)?gps\b', fb_without_text))
                or bool(re.search(r'\b(?:without|no|not\s+having|excluding|except)\s+(?:any\s+)?(?:fixed\s+)?gps\b', msg_lower))
            )

            fb_pos_text = re.sub(r'\b(?:without|excluding|except)\b.*', '', msg_lower) if fb_without_match else msg_lower

            fb_has_fixed_elock = (bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', fb_pos_text)) or bool(re.search(r'\bfixed[\s-]*(?:e[\s-]?)?locks?\b', msg_lower))) and not fb_without_fixed_elock
            fb_has_portable_elock = (bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', fb_pos_text)) or bool(re.search(r'\bportab?le[\s-]*(?:e[\s-]?)?locks?\b|\bportab?le\b', msg_lower))) and not fb_without_portable
            fb_has_fixed_gps = (bool(re.search(r'\bfixed[\s-]gps\b', fb_pos_text)) or bool(re.search(r'\bfixed[\s-]gps\b', msg_lower))) and not fb_without_gps
            fb_is_devices_together = bool(re.search(r'\btogether\b', msg_lower))
            fb_is_device_trips = bool(re.search(r'\b(?:device\s+trips?|device\s+data|device\s+report)\b', msg_lower))
            fb_is_gps_na = bool(
                re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_lower)
                or re.search(r'\bgps\s+is\s+(?:na|inactive|not\s+applicable|not\s+captured)\b', msg_lower)
                or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_lower)
                or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_lower)
            )
            fb_is_fixed_portable_elock_combo = (
                fb_has_fixed_elock
                and fb_has_portable_elock
                and not fb_has_fixed_gps
                and not bool(re.search(r'\b(?:gps|withoutorgps|not\s+(?:fixed|portable)|without\s+(?:fixed|portable))\b', msg_lower))
                and not bool(re.search(r'\b(?:only|single|sole)\b', msg_lower))
            )

            def _fb_get_device_status(msg: str, default: str = "existing") -> str:
                if re.search(r'\b(?:(?:no|without)\s+any\s+gps|without\s+(?:any\s+)?gps\s+devices?)\b', msg):
                    return "nogps "
                elif re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg):
                    return "nogps"
                elif re.search(r'\b(?:no|without|lost|lacking|zero)\s+connectivity\b', msg):
                    return "inactive"
                elif re.search(r'\bactive\b|\bconnectivity\s+on\b|\b(?:had|with)\s+(?:fixed[\s-]*)?gps\s+connectivity\b|\b(?:fixed[\s-]*)?gps\s+connectivity\b', msg):
                    return "active"
                elif re.search(r'\binactive\b', msg):
                    return "inactive"
                elif re.search(r'\b(?:na|n/a)\b', msg):
                    return "na"
                return default

            fb_is_active_device_count = bool(re.search(r'\b(?:active\s+)?device\s+count\b', msg_lower))

            # 0a. Active Device Count queries (e.g. "What was the active device count for Fixed E-Lock trips on Intercity routes in Aug 2026?")
            fb_is_active_device_elock_query = (
                fb_has_fixed_elock
                and fb_is_active_device_count
                and not fb_has_portable_elock
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_is_gps_na
            )
            fb_is_active_device_portable_query = (
                fb_has_portable_elock
                and fb_is_active_device_count
                and not fb_has_fixed_elock
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_is_gps_na
            )

            # 1. Fixed GPS Only / Single Device
            fb_is_fixed_gps_only = (
                not fb_has_veh_mention
                and fb_has_fixed_gps
                and not fb_has_fixed_elock
                and not fb_has_portable_elock
                and not fb_without_gps
                and (
                    bool(re.search(r'\bfixed[\s-]gps\b', fb_pos_text) and re.search(r'\b(single|only|sole|alone|exclusive|exclusively)\b', msg_lower))
                    or bool(re.search(r'\bonly\s+fixed[\s-]gps\b', fb_pos_text))
                    or bool(re.search(r'\bfixed[\s-]gps\s+(as\s+)?(the\s+)?(single|only|sole)\b', fb_pos_text))
                    or bool(re.search(r'\b(?:(?:fixed[\s-]*)?gps)\s+(?:only|single|sole|alone|exclusive|exclusively)\b', fb_pos_text))
                    or bool(re.search(r'\bonly\s+(?:fixed[\s-]*)?gps\b', fb_pos_text))
                )
            )

            # 1b. Fixed GPS + E-Lock Combo (e.g. "Fixed GPS + Fixed E-Lock combination", "Fixed GPS and Fixed E-Lock combo")
            fb_is_fixed_gps_combo = (
                fb_has_fixed_gps
                and (fb_has_fixed_elock or fb_has_portable_elock)
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_is_gps_na
                and not fb_is_fixed_gps_only
            )

            # 2a. Portable E-Lock Single Device ONLY (when "only" / "single" is mentioned)
            fb_is_portable_elock_only = (
                not fb_has_veh_mention
                and fb_has_portable_elock
                and not fb_has_fixed_elock
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_is_active_device_count
                and bool(re.search(r'\b(single|only|sole|alone|exclusive|exclusively)\b', msg_lower))
            )

            # 2b. Portable E-Lock Trip Query (regular count without "only")
            fb_is_portable_elock_trip_query = (
                fb_has_portable_elock
                and not fb_has_fixed_elock
                and not fb_is_devices_together
                and not fb_is_fixed_gps_combo
                and not fb_is_device_trips
                and not fb_is_gps_na
                and not fb_is_active_device_count
                and not fb_is_portable_elock_only
                and not fb_target_vendor
                and not fb_dev_vendor_match
            )

            # 3a. Fixed E-Lock Single Device ONLY (when "only" / "single" is mentioned)
            fb_is_fixed_elock_only = (
                not fb_has_veh_mention
                and fb_has_fixed_elock
                and not fb_has_portable_elock
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_is_active_device_count
                and bool(re.search(r'\b(single|only|sole|alone|exclusive|exclusively)\b', msg_lower))
            )

            # 3b. Fixed E-Lock Trip Query (regular count without "only" and without "device count")
            fb_is_fixed_elock_trip_query = (
                fb_has_fixed_elock
                and not fb_has_portable_elock
                and not fb_is_devices_together
                and not fb_is_fixed_gps_combo
                and not fb_is_device_trips
                and not fb_is_gps_na
                and not fb_is_active_device_count
                and not fb_is_fixed_elock_only
                and not fb_target_vendor
                and not fb_dev_vendor_match
            )

            fb_is_no_gps_vehicle_query = bool(
                (
                    re.search(r'\b(?:no|without(?:\s+any)?|had\s+no|lacking)\s+(?:a\s+)?(?:fixed[\s-]*)?gps(?:\s+device)?\b', msg_lower)
                    or re.search(r'\b(?:fixed[\s-]*)?gps\s+(?:is\s+)?(?:not\s+available|absent|na)\b', msg_lower)
                    or re.search(r'\bnon[\s\-_]*(?:fixed[\s-]*)?gps\b', msg_lower)
                    or re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower)
                )
                and re.search(r'\bvehicles?\b', msg_lower)
            )

            fb_is_gps_exception = bool(
                re.search(r'\bfixed[\s-]gps\s+(?:as\s+)?(?:nogps|inactive|na|gpsinactive)\b', msg_lower)
                or re.search(r'\bgps\s+as\s+(?:nogps|inactive|na|gpsinactive)\b', msg_lower)
                or re.search(r'\bgpsinactive\b', msg_lower)
            )
            fb_is_gps_trip_query = (
                bool(re.search(r'\bgps\b', msg_lower))
                and not fb_has_fixed_elock
                and not fb_has_portable_elock
                and not fb_is_devices_together
                and not fb_is_device_trips
                and not fb_is_gps_na
                and not fb_is_fixed_gps_only
                and not fb_is_gps_exception
                and not fb_is_no_gps_vehicle_query
                and not fb_is_network_vs_local_query
                and not fb_is_trip_type_breakdown_query
                and not fb_is_across_trip_types
                and not bool(detect_device_analytics(message))
                and not fb_target_vendor
                and not fb_dev_vendor_match
            )

            if fb_is_trip_type_breakdown_query:
                fb_is_non_gps = bool(re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower))
                target_trip_type = "intracity" if re.search(r'\b(?:intracity|local)\b', msg_lower) and not re.search(r'\b(?:intercity|network)\b', msg_lower) else "intercity"
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus",
                        "shipmentMethod", "routeCategory", "routeType", "tripType"
                    ])
                ]
                fallback_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                fallback_payload["filters"].append({"field": "tripType", "operator": "eq", "value": target_trip_type})
                if fb_is_non_gps:
                    fallback_payload["analytics"] = [{"type": "gpsna"}]
                fallback_payload["select"] = []
                fallback_payload["groupBy"] = ["tripType"]
                fallback_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "TotalTrip"
                    }
                ]
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "select", "groupBy", "metrics"]:
                    if k in fallback_payload:
                        reordered[k] = fallback_payload[k]
                for k, v in fallback_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                fallback_payload.clear()
                fallback_payload.update(reordered)
            elif fb_is_network_vs_local_query and not bool(detect_device_analytics(message)):
                fb_is_non_gps = bool(re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower))
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus",
                        "shipmentMethod", "routeCategory", "routeType", "tripType"
                    ])
                ]
                fallback_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                if fb_is_non_gps:
                    fallback_payload["analytics"] = [{"type": "gpsna"}]
                    fallback_payload["select"] = []
                else:
                    fallback_payload.pop("select", None)
                fallback_payload["groupBy"] = ["tripType"]
                fallback_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "TotalTrip"
                    }
                ]
                reordered = {}
                key_order = ["entity", "operation", "analytics", "filters", "select", "groupBy", "metrics"] if fb_is_non_gps else ["entity", "operation", "filters", "groupBy", "metrics"]
                for k in key_order:
                    if k in fallback_payload:
                        reordered[k] = fallback_payload[k]
                for k, v in fallback_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                fallback_payload.clear()
                fallback_payload.update(reordered)
            elif fb_is_multi_device_trip_query:
                fallback_payload["analytics"] = [{"type": "withgps"}]
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    fb_found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = "valid"
                            f["operator"] = "eq"
                            fb_found_status = True
                            break
                    if not fb_found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": "valid"
                        })

                fb_dev_val = "existing"
                if re.search(r'\b(?:inactive|no\s+connectivity)\b', msg_lower):
                    fb_dev_val = "inactive"
                elif re.search(r'\bactive\b', msg_lower):
                    fb_dev_val = "active"
                elif re.search(r'\b(?:na|n/a|not\s+available|not\s+applicable)\b', msg_lower):
                    fb_dev_val = "na"
                elif re.search(r'\bexisting\b', msg_lower):
                    fb_dev_val = "existing"

                if fb_multi_device_fixed:
                    fallback_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": fb_dev_val})
                if fb_multi_device_portable:
                    fallback_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": fb_dev_val})
                if fb_multi_device_gps:
                    fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": fb_dev_val})

                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "select"]:
                    if k in fallback_payload:
                        reordered[k] = fallback_payload[k]
                for k, v in fallback_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                fallback_payload.clear()
                fallback_payload.update(reordered)
            elif detect_device_analytics(message) and not (fb_target_vendor or extract_device_vendor(msg_lower)):
                fb_device_analytics = detect_device_analytics(message)
                fallback_payload["analytics"] = [{"type": fb_device_analytics["analytics_type"]}]
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                has_explicit_trip_status = bool(
                    re.search(r'\b(?:valid\s+trips?|active\s+trips?|running\s+trips?|closed\s+trips?|completed\s+trips?|cancelled\s+trips?|cancel\s+trips?)\b', msg_lower)
                    or re.search(r'\bvalid\s+(?:\w+\s+)?trips?\b', msg_lower)
                    or re.search(r'\bvalid\b', msg_lower)
                    or re.search(r'\bnon[\s-]?cancell?ed\b', msg_lower)
                    or re.search(r'\bnot\s+cancell?ed\b', msg_lower)
                    or re.search(r'\btrip\s+status\b', msg_lower)
                )
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
                for df in fb_device_analytics["device_filters"]:
                    fallback_payload["filters"].append(df)

                fb_single_route_category = "intracity" if re.search(r'\bintracity\b', msg_lower) and not re.search(r'\bintercity\b', msg_lower) else ("intercity" if re.search(r'\bintercity\b', msg_lower) and not re.search(r'\bintracity\b', msg_lower) else None)

                if fb_is_across_trip_types:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType", "shipmentMethod"])
                    ]
                    fallback_payload.pop("select", None)
                    fallback_payload["groupBy"] = ["tripType"]
                    fallback_payload["metrics"] = [
                        {
                            "field": "shipmentNo",
                            "function": "count",
                            "alias": "TotalTrip"
                        }
                    ]
                elif fb_single_route_category:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType", "shipmentMethod"])
                    ]
                    fallback_payload["filters"].append({
                        "field": "routeCategory",
                        "operator": "eq",
                        "value": fb_single_route_category
                    })
                    fallback_payload.pop("select", None)
                    fallback_payload.pop("groupBy", None)
                    fallback_payload.pop("metrics", None)
                else:
                    fallback_payload["select"] = []
                    fallback_payload.pop("groupBy", None)
                    fallback_payload.pop("metrics", None)

                def _fb_device_analytics_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    fld = flt.get("field")
                    if fb_single_route_category:
                        if fld == "tripStatus":
                            return 1
                        if fld == "groupId":
                            return 2
                        if fld == "runDate":
                            return 3 if flt.get("operator") == "gte" else 4
                        if fld in ("fixed-gps-status", "fixed-elock-status", "portable-elock-status", "GPS", "FixedELock", "PortableELock"):
                            return 5
                        if fld == "routeCategory":
                            return 6
                        if fld == "shipmentMethod":
                            return 7
                        return 10
                    if fld == "groupId":
                        return 1
                    if fld == "tripStatus":
                        return 2
                    if fld in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 3
                    if fb_is_across_trip_types:
                        if fld == "runDate":
                            return 4 if flt.get("operator") == "gte" else 5
                        if fld in ("fixed-gps-status", "fixed-elock-status", "portable-elock-status", "GPS", "FixedELock", "PortableELock"):
                            return 6
                    else:
                        if fld in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                            return 3
                        if fld == "runDate":
                            return 4 if flt.get("operator") == "gte" else 5
                        if fld in ("fixed-gps-status", "fixed-elock-status", "portable-elock-status", "GPS", "FixedELock", "PortableELock"):
                            return 6
                    if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and flt.get("operator") == "gte":
                        return 5.1
                    if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and flt.get("operator") == "lte":
                        return 5.2
                    if fld in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance"):
                        return 5.3
                    if fld in ("area", "branch"):
                        return 5.4
                    if fld == "shipmentMethod":
                        return 7
                    if str(fld).lower() in ("atd", "ata"):
                        return 8
                    return 9

                fallback_payload["filters"].sort(key=_fb_device_analytics_sort_key)
                reordered = {}
                key_order = ["entity", "operation", "analytics", "filters", "groupBy", "metrics"] if fb_is_across_trip_types else (["entity", "operation", "analytics", "filters"] if fb_single_route_category else ["entity", "operation", "analytics", "filters", "select", "groupBy", "metrics"])
                for k in key_order:
                    if k in fallback_payload:
                        reordered[k] = fallback_payload[k]
                for k, v in fallback_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                fallback_payload.clear()
                fallback_payload.update(reordered)
            elif fb_is_active_device_elock_query:
                fb_fixed_val = _fb_get_device_status(msg_lower, "active")
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus"
                    ])
                ]
                fallback_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                fallback_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": fb_fixed_val})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
            elif fb_is_active_device_portable_query:
                fb_portable_val = _fb_get_device_status(msg_lower, "active")
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock", "tripStatus"
                    ])
                ]
                fallback_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
                fallback_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": fb_portable_val})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
            elif fb_is_fixed_gps_only:
                fallback_payload["analytics"] = [{"type": "gpsonly"}]
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif fb_is_portable_elock_only:
                fallback_payload["analytics"] = [{"type": "portablelockonly"}]
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif fb_is_portable_elock_trip_query:
                fb_portable_val = _fb_get_device_status(msg_lower, "existing")
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": fb_portable_val})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif fb_is_fixed_elock_only:
                fallback_payload["analytics"] = [{"type": "fixedlockonly"}]
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif fb_is_fixed_elock_trip_query:
                fb_fixed_val = _fb_get_device_status(msg_lower, "existing")
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": fb_fixed_val})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif fb_is_fixed_gps_combo:
                fallback_payload["analytics"] = [{"type": "withgps"}]
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
                fallback_payload["filters"].append({"field": "fixed-gps-status", "operator": "eq", "value": "existing"})
                if fb_has_fixed_elock:
                    fallback_payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
                if fb_has_portable_elock:
                    fallback_payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})
            elif fb_is_no_gps_vehicle_query:
                # Vehicles with no GPS: tripStatus eq "valid", analytics: [{"type": "gpsna"}], grouped by vehicleNo with countDistinct
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                has_trip_status = any(isinstance(f, dict) and f.get("field") == "tripStatus" for f in fallback_payload["filters"])
                if not has_trip_status:
                    fallback_payload["filters"].append({"field": "tripStatus", "operator": "eq", "value": "valid"})
                if fb_specific_transporter:
                    fallback_payload.pop("analytics", None)
                    fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "nogps"})
                    if "select" not in fallback_payload:
                        fallback_payload["select"] = []
                else:
                    fallback_payload["analytics"] = [{"type": "gpsna"}]
                    fallback_payload.pop("select", None)
                fallback_payload["groupBy"] = ["vehicleNo"]
                fallback_payload["metrics"] = [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
            elif fb_is_gps_trip_query:
                fb_gps_val = _fb_get_device_status(msg_lower, "existing")
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": fb_gps_val})
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
            elif fb_is_devices_together and (bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower)) or fb_has_fixed_elock or fb_has_portable_elock):
                fb_has_together_gps = bool(re.search(r'\b(?:fixed[\s-]gps|gps)\b', msg_lower))
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "FixedELock", "PortableELock",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "gps", "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                fallback_payload["select"] = []
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })

                if fb_has_together_gps and fb_has_fixed_elock and fb_without_portable:
                    fallback_payload["analytics"] = [{"type": "withgps"}]
                    fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "existing"})
                    fallback_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "existing"})
                    fallback_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "na"})
                elif fb_has_together_gps and fb_has_portable_elock and fb_without_fixed_elock:
                    fallback_payload["analytics"] = [{"type": "withgps"}]
                    fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "existing"})
                    fallback_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "na"})
                    fallback_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "existing"})
                elif fb_has_fixed_elock and fb_has_portable_elock and fb_without_gps:
                    fallback_payload["analytics"] = [{"type": "withgps"}]
                    fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "na"})
                    fallback_payload["filters"].append({"field": "FixedELock", "operator": "eq", "value": "existing"})
                    fallback_payload["filters"].append({"field": "PortableELock", "operator": "eq", "value": "existing"})
                elif fb_has_together_gps and fb_has_fixed_elock and fb_has_portable_elock:
                    fallback_payload["analytics"] = [{"type": "withgps"}]
                    fallback_payload["filters"].append({"field": "fixed-gps-status", "operator": "eq", "value": "existing"})
                    fallback_payload["filters"].append({"field": "portable-elock-status", "operator": "eq", "value": "existing"})
                    fallback_payload["filters"].append({"field": "fixed-elock-status", "operator": "eq", "value": "existing"})
                else:
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
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field in ("fixed-gps-status", "GPS", "gps", "fixedgps"):
                        return 3
                    elif field in ("fixed-elock-status", "FixedELock", "fixedelock"):
                        return 3.1
                    elif field in ("portable-elock-status", "PortableELock", "portableelock"):
                        return 3.2
                    elif field == "runDate" and op == "gte":
                        return 4
                    elif field == "runDate" and op == "lte":
                        return 5
                    return 10

                fallback_payload["filters"].sort(key=_fb_filter_sort_key)
                reordered = {}
                key_order = ["entity", "operation", "analytics", "filters", "select"]
                for k in key_order:
                    if k in fallback_payload:
                        reordered[k] = fallback_payload[k]
                for k, v in fallback_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                fallback_payload.clear()
                fallback_payload.update(reordered)
            elif fb_is_fixed_portable_elock_combo:
                fallback_payload["analytics"] = [{"type": "withgps"}]
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in [
                        "FixedGPS", "FixedELock", "PortableELock", "GPS", "gps", "fixedgps",
                        "fixed-gps status", "fixed-e-lock status", "portable-e-lock status",
                        "fixed-gps-status", "fixed-elock-status", "portable-elock-status",
                        "fixedelock", "portableelock"
                    ])
                ]
                fallback_payload["filters"].append(
                    {"field": "portable-elock-status", "operator": "eq", "value": "existing"}
                )
                fallback_payload["filters"].append(
                    {"field": "fixed-elock-status", "operator": "eq", "value": "existing"}
                )
                fallback_payload.pop("select", None)
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)

            # Apply the same routeCategory post-processing as the main path
            fb_route_category: str | None = None
            if fb_is_across_trip_types or (re.search(r'\bintracity\b', msg_lower) and re.search(r'\bintercity\b', msg_lower)):
                fb_route_category = None
            elif re.search(r'\bintracity\b', msg_lower):
                fb_route_category = "intracity"
            elif re.search(r'\bintercity\b', msg_lower):
                fb_route_category = "intercity"
            if fb_is_across_trip_types:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType"])
                ]
            elif fb_route_category and not fb_is_network_vs_local_query and not fb_is_trip_type_breakdown_query and (not bool(detect_device_analytics(message)) or fb_is_fixed_portable_elock_combo):
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["routeType", "routeCategory", "tripType"])
                ]
                fallback_payload["filters"].append(
                    {"field": "routeCategory", "operator": "eq", "value": fb_route_category}
                )

            fb_sms = detect_shipment_methods(message)
            if fb_is_across_trip_types:
                fb_sms = [sm for sm in fb_sms if sm != 'network']
            if fb_sms and not fb_is_network_vs_local_query and not fb_is_trip_type_breakdown_query and not fb_is_across_trip_types:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                if len(fb_sms) > 1:
                    fallback_payload["filters"].append(
                        {"field": "shipmentMethod", "operator": "in", "value": fb_sms}
                    )
                    fallback_payload["groupBy"] = ["shipmentMethod"]
                    fallback_payload["metrics"] = [
                        {
                            "field": "shipmentNo",
                            "function": "count",
                            "alias": "trip_count"
                        }
                    ]
                else:
                    fallback_payload["filters"].append(
                        {"field": "shipmentMethod", "operator": "eq", "value": fb_sms[0]}
                    )
                if len(fb_sms) <= 1 and fallback_payload.get("groupBy") == ["shipmentMethod"]:
                    fallback_payload.pop("groupBy", None)
                    fallback_payload.pop("metrics", None)
                if detect_device_analytics(message):
                    fallback_payload["filters"].sort(key=_fb_device_analytics_sort_key)
            elif fb_is_shipment_method_breakdown_query:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["shipmentMethod", "Route", "route", "RouteName", "routeName", "routeCategory", "routeType", "tripType"])
                ]
                fallback_payload["groupBy"] = ["shipmentMethod"]
                fallback_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "trip_count"
                    }
                ]
            elif fb_is_across_trip_types:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") == "shipmentMethod")
                ]
                fb_has_date = any(isinstance(f, dict) and f.get("field") == "runDate" for f in fallback_payload["filters"])
                if not fb_has_date and not has_status_keyword:
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
            fb_has_explicit_vehicle_count = bool(
                re.search(r'\b(?:count\s+of\s+vehicles?|vehicles?\s+count|count\s+of\s+vehicle)\b', msg_lower)
                or (fb_has_vehicle_mention and bool(re.search(r'\b(?:distinct|unique)\b', msg_lower)))
            )
            fb_is_distinct_vehicle_query = (
                (
                    (
                        fb_has_vehicle_mention
                        and fb_has_count_unique_distinct
                        and not fb_has_transporter_mention
                        and bool(
                            re.search(r'\b(?:distinct|unique|list|all)\b', msg_lower)
                            or re.search(r'\b(?:count\s+of\s+vehicles?|vehicles?\s+count|count\s+of\s+vehicle)\b', msg_lower)
                        )
                    )
                    or fb_is_active_vehicle_query
                )
                and ((not fb_is_vendor_running_query) or fb_has_explicit_vehicle_count)
                and not bool(detect_vehicle_master(message))
            )
            fb_is_distinct_transporter_query = (
                fb_has_transporter_mention
                and fb_has_count_unique_distinct
                and not fb_has_vehicle_mention
                and not fb_has_no_transporter
            )

            if fb_is_shipment_method_breakdown_query:
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in [
                        "shipmentMethod", "Route", "route", "RouteName", "routeName",
                        "routeCategory", "routeType", "tripType"
                    ])
                ]
                fallback_payload["select"] = []
                fallback_payload["groupBy"] = ["shipmentMethod"]
                fallback_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "trip_count"
                    }
                ]
            elif fb_is_each_region_query:
                fallback_payload["groupBy"] = ["region"]
                fallback_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "Shipments"
                    }
                ]
                fallback_payload.pop("select", None)
            elif fb_is_transporter_unique_vehicles_query:
                fb_is_trips_and_vehicles = bool(re.search(r'\b(?:trips?\s+and\s+(?:unique\s+|distinct\s+)?vehicles?|(?:unique\s+|distinct\s+)?vehicles?\s+and\s+trips?)\b', msg_lower))
                fallback_payload["groupBy"] = ["vehicleNo"]
                if fb_is_trips_and_vehicles:
                    fallback_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"},
                        {"field": "shipmentNo", "function": "count", "alias": "shipmentNo"}
                    ]
                else:
                    fallback_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                    ]
                fallback_payload["select"] = []
                if fb_is_include_cancelled:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                    ]
                else:
                    target_status = status_override if status_override is not None else "valid"
                    found_status = False
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") == "tripStatus":
                            f["value"] = target_status
                            f["operator"] = "eq"
                            found_status = True
                            break
                    if not found_status:
                        fallback_payload["filters"].append({
                            "field": "tripStatus",
                            "operator": "eq",
                            "value": target_status
                        })
                if fb_specific_transporter:
                    has_fb_transporter_filter = False
                    fb_trans_op = "in" if isinstance(fb_specific_transporter, list) else "eq"
                    for f in fallback_payload["filters"]:
                        if isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"]:
                            f["field"] = "transporter"
                            f["operator"] = fb_trans_op
                            f["value"] = fb_specific_transporter
                            has_fb_transporter_filter = True
                            break
                    if not has_fb_transporter_filter:
                        fallback_payload["filters"].append({
                            "field": "transporter",
                            "operator": fb_trans_op,
                            "value": fb_specific_transporter
                        })
                else:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"])
                    ]
            elif fb_is_distinct_vehicle_query:
                fallback_payload["groupBy"] = ["vehicleNo"]
                fallback_payload["metrics"] = [
                    {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                ]
                fallback_payload.pop("select", None)
            elif fb_is_distinct_transporter_query:
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if not (isinstance(f, dict) and f.get("field") in ["transporter", "Transporter"])
                ]
                fallback_payload["groupBy"] = ["Transporter"]
                fallback_payload["metrics"] = [
                    {"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}
                ]
                fallback_payload.pop("select", None)
                fb_has_trip_status = False
                for f in fallback_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        f["operator"] = "eq"
                        f["value"] = "valid"
                        fb_has_trip_status = True
                        break
                if not fb_has_trip_status:
                    if "filters" not in fallback_payload:
                        fallback_payload["filters"] = []
                    fallback_payload["filters"].insert(0, {"field": "tripStatus", "operator": "eq", "value": "valid"})
            elif ("groupBy" not in fallback_payload or not fallback_payload["groupBy"]) and fb_has_transporter_mention and fb_has_count_unique_distinct:
                if fb_has_vehicle_mention:
                    fallback_payload["groupBy"] = ["Transporter"]
                    fallback_payload["metrics"] = [
                        {"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}
                    ]

            # Apply Vendor post-processing to fallback
            fb_target_vendor = None
            fb_dev_vendor_match = None
            fb_is_vendor_running_query = False
            fb_is_past_running = False

            fb_known_device_vendor = extract_device_vendor(msg_lower)
            if fb_known_device_vendor in ["Secutrak_TP", "Fleetx", "Axestrack", "Gtropy", "Eicher", "G-Trac", "Ashok", "Tata", "Kiasaint", "tatamotors", "ashokleyland", "wheelseye_bluedart", "Lynkit"]:
                fb_target_vendor = fb_known_device_vendor
                fb_canonical_vendor = fb_known_device_vendor
                fb_op = "contains"

                fb_is_vendor_fixed_elock = bool(re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b|\bfixedelock\b', msg_lower))
                fb_is_vendor_portable_elock = bool(re.search(r'\bportab?le[\s-]e[\s-]?locks?\b|\bportab?le[\s-]locks?\b|\bportableelock\b', msg_lower)) and not fb_is_vendor_fixed_elock

                if fb_is_vendor_fixed_elock:
                    fb_vendor_field = "vendorfixedelock"
                    fallback_payload["analytics"] = [{"type": "fixedlock"}]
                elif fb_is_vendor_portable_elock:
                    fb_vendor_field = "vendorportableelock"
                    fallback_payload["analytics"] = [{"type": "portablelock"}]
                else:
                    fb_vendor_field = "vendorgps"
                    fallback_payload["analytics"] = [{"type": "gps"}]

                has_gps_inactive = bool(re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_lower))
                has_gps_active = bool(re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_lower))

                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in [
                        "GPS", "FixedGPS", "gps", "fixedgps", "FixedELock", "fixedelock",
                        "PortableELock", "portableelock", "fixed-gps-status", "fixed-elock-status",
                        "portable-elock-status", "fixed-e-lock status", "portable-e-lock status",
                        "vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"
                    ])
                ]
                fallback_payload["filters"].append({
                    "field": fb_vendor_field,
                    "operator": "contains",
                    "value": fb_canonical_vendor
                })
                if fb_vendor_field == "vendorgps":
                    if has_gps_inactive:
                        fallback_payload["filters"].append({"field": "gps", "operator": "eq", "value": "inactive"})
                    elif has_gps_active:
                        fallback_payload["filters"].append({"field": "gps", "operator": "eq", "value": "active"})
            elif re.search(r'\b3rd[\s-]party\b|\bthird[\s-]party\b', msg_lower):
                fb_target_vendor = "ThirdParty"
            elif re.search(r'\b(secutrak|icici|ilgic)\b', msg_lower):
                fb_target_vendor = "Secutrak"
            elif re.search(r'\bwheelseye\b', msg_lower):
                fb_target_vendor = "Wheelseye"
            else:
                fb_dev_vendor_match = re.search(r'\b([A-Za-z0-9_-]+)\s+devices?\s+(?:report|data)\b', msg_lower)
                if not fb_dev_vendor_match:
                    fb_dev_vendor_match = re.search(r'\breport\s+(?:for\s+)?(?:all\s+)?([A-Za-z0-9_-]+)\s+devices?\b', msg_lower)
                if not fb_dev_vendor_match:
                    fb_dev_vendor_match = re.search(r'\b(?:was\s+running|is\s+running|running)\s+on\s+([A-Za-z0-9_-]+)\s+devices?\b', msg_lower)
                if fb_dev_vendor_match:
                    candidate = fb_dev_vendor_match.group(1)
                    if candidate.lower() not in ["the", "a", "an", "generate", "download", "get", "show", "fetch", "all", "party", "gps", "elock", "e-lock"]:
                        fb_target_vendor = candidate

            if fb_target_vendor and not (fb_known_device_vendor and fb_known_device_vendor in ["Secutrak_TP", "Fleetx", "Axestrack", "Gtropy", "Eicher", "G-Trac", "Ashok", "Tata", "Kiasaint", "tatamotors", "ashokleyland", "wheelseye_bluedart", "Lynkit"]):
                if fb_target_vendor.lower() == "wheelseye":
                    fb_canonical_vendor = "Wheelseye"
                elif fb_target_vendor.lower() in ["secutrak", "icici", "ilgic"]:
                    fb_canonical_vendor = "Secutrak"
                elif fb_target_vendor == "ThirdParty" or fb_target_vendor.lower() in ["3rdparty", "thirdparty", "3rd-party"]:
                    fb_canonical_vendor = "ThirdParty"
                else:
                    fb_canonical_vendor = fb_target_vendor.capitalize()

                fb_op = "contains" if (fb_canonical_vendor == "Wheelseye" or (fb_dev_vendor_match and fb_canonical_vendor not in ["Secutrak", "ThirdParty"])) else "eq"

                fb_is_vendor_fixed_elock = bool(
                    (fb_target_vendor or fb_dev_vendor_match) and (
                        re.search(r'\bfixed[\s-]e[\s-]?locks?\b|\bfixed[\s-]locks?\b', msg_lower)
                    )
                )
                fb_is_vendor_portable_elock = bool(
                    (fb_target_vendor or fb_dev_vendor_match) and not fb_is_vendor_fixed_elock and (
                        re.search(r'\bportable[\s-]e[\s-]?locks?\b|\bportable[\s-]locks?\b', msg_lower)
                    )
                )
                fb_is_vendor_gps = bool(
                    not fb_is_vendor_fixed_elock and not fb_is_vendor_portable_elock and (fb_target_vendor or fb_dev_vendor_match) and (
                        re.search(r'\b(?:[A-Za-z0-9_-]+)\s+gps\s+devices?\b', msg_lower)
                        or re.search(r'\bgps\s+devices?\s+(?:of|for|from|using|with)\s+[A-Za-z0-9_-]+\b', msg_lower)
                        or re.search(r'\b(?:ilgic|secutrak|icici|wheelseye)\s+(?:fixed\s+)?gps\b', msg_lower)
                        or (fb_target_vendor and re.search(rf'\b{re.escape(fb_target_vendor.lower())}\s+(?:fixed\s+)?gps\b', msg_lower))
                        or re.search(r'\b(?:fixed[\s-]*)?gps\b', msg_lower)
                    )
                )

                if fb_is_vendor_fixed_elock:
                    fb_vendor_field = "vendorfixedelock"
                    fallback_payload["analytics"] = [{"type": "fixedlock"}]
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in [
                            "FixedELock", "fixedelock", "fixed-elock-status", "fixed-e-lock status"
                        ])
                    ]
                elif fb_is_vendor_portable_elock:
                    fb_vendor_field = "vendorportableelock"
                    fallback_payload["analytics"] = [{"type": "portablelock"}]
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in [
                            "PortableELock", "portableelock", "portable-elock-status", "portable-e-lock status"
                        ])
                    ]
                elif fb_is_vendor_gps:
                    fb_vendor_field = "vendor" if fb_canonical_vendor == "Wheelseye" else "vendorgps"
                    fallback_payload["analytics"] = [{"type": "gps"}]
                    has_gps_inactive = bool(
                        re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_lower)
                        or re.search(r'\b(?:no|without|lost|lacking|zero)\s+(?:(?:fixed[\s-]?)?gps\s+)?connectivity(?:\s+status)?\b', msg_lower)
                    )
                    has_gps_active = bool(
                        re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_lower)
                        or re.search(r'\b(?:had\s+(?:fixed[\s-]?)?gps\s+connectivity|with\s+(?:fixed[\s-]?)?gps\s+connectivity|(?:fixed[\s-]?)?gps\s+connectivity\s+on|(?:fixed[\s-]?)?gps\s+connectivity)\b', msg_lower)
                    ) and not has_gps_inactive
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") in ["GPS", "FixedGPS", "gps", "fixedgps"])
                    ]
                    if has_gps_inactive:
                        fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "inactive"})
                    elif has_gps_active:
                        fallback_payload["filters"].append({"field": "GPS", "operator": "eq", "value": "active"})
                else:
                    fb_vendor_field = "vendor"

                if fb_canonical_vendor == "ThirdParty":
                    fallback_payload["filters"] = [
                        f for f in fallback_payload["filters"]
                        if not (isinstance(f, dict) and f.get("field") == "portableelock")
                    ]

                fb_has_vendor = False
                for f in fallback_payload["filters"]:
                    if isinstance(f, dict) and f.get("field") in ["vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"]:
                        fb_has_vendor = True
                        f["field"] = fb_vendor_field
                        f["value"] = fb_canonical_vendor
                        f["operator"] = fb_op
                        break
                if not fb_has_vendor:
                    fallback_payload["filters"].append({
                        "field": fb_vendor_field,
                        "operator": fb_op,
                        "value": fb_canonical_vendor
                    })

                if fb_vendor_field == "vendorgps":
                    has_gps_inactive = bool(re.search(r'\b(?:inactive\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?inactive)\b', msg_lower))
                    has_gps_active = bool(re.search(r'\b(?:active\s+(?:fixed[\s-]?)?gps|(?:fixed[\s-]?)?gps\s+(?:is\s+)?active)\b', msg_lower))
                    gps_status_val = "inactive" if has_gps_inactive else ("active" if has_gps_active else None)
                    if gps_status_val:
                        has_gps_f = False
                        for f in fallback_payload["filters"]:
                            if isinstance(f, dict) and f.get("field") == "gps":
                                f["value"] = gps_status_val
                                f["operator"] = "eq"
                                has_gps_f = True
                                break
                        if not has_gps_f:
                            fallback_payload["filters"].append({
                                "field": "gps",
                                "operator": "eq",
                                "value": gps_status_val
                            })

            if fb_is_vendor_running_query:
                if not fb_has_date_specified:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload.get("filters", [])
                        if not (isinstance(f, dict) and f.get("field") == "runDate")
                    ]
                if not fb_is_distinct_vehicle_query:
                    fallback_payload.pop("groupBy", None)
                    fallback_payload.pop("metrics", None)
                    fallback_payload["select"] = []

            if fb_is_transporter_unique_vehicles_query:
                def _fb_transporter_unique_vehicles_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ["transporter", "Transporter"]:
                        return 5
                    return 10

                fallback_payload["filters"].sort(key=_fb_transporter_unique_vehicles_sort_key)
                fallback_payload["select"] = []
            elif fb_is_active_vehicle_query:
                def _fb_active_vehicle_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    return 10

                fallback_payload["filters"].sort(key=_fb_active_vehicle_sort_key)
            elif fb_is_active_device_elock_query or fb_is_active_device_portable_query:
                def _fb_active_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "groupId":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ["FixedELock", "PortableELock", "GPS", "FixedGPS"]:
                        return 5
                    elif field in ["routeCategory", "shipmentMethod"]:
                        return 6
                    return 10

                fallback_payload["filters"].sort(key=_fb_active_device_sort_key)
            elif fb_is_distinct_transporter_query:
                def _fb_transporter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "runDate" and op == "gte":
                        return 2
                    elif field == "runDate" and op == "lte":
                        return 3
                    elif field == "groupId":
                        return 4
                    return 10

                fallback_payload["filters"].sort(key=_fb_transporter_sort_key)
            elif fb_is_portable_elock_trip_query or fb_is_fixed_elock_trip_query:
                def _fb_device_trip_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "groupId":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ["PortableELock", "FixedELock", "GPS", "FixedGPS"]:
                        return 5
                    elif field in ["shipmentMethod", "routeCategory"]:
                        return 6
                    return 10

                fallback_payload["filters"].sort(key=_fb_device_trip_sort_key)
            elif fb_is_fixed_gps_combo:
                def _fb_fixed_gps_combo_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "fixed-gps-status":
                        return 5
                    elif field in ["fixed-elock-status", "portable-elock-status"]:
                        return 6
                    elif field in ["shipmentMethod", "routeCategory"]:
                        return 7
                    return 10

                fallback_payload["filters"].sort(key=_fb_fixed_gps_combo_sort_key)
            elif fb_is_fixed_gps_only or fb_is_portable_elock_only or fb_is_fixed_elock_only:
                def _fb_single_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "GPS":
                        return 5
                    elif field == "FixedELock":
                        return 6
                    elif field == "PortableELock":
                        return 7
                    elif field == "routeCategory":
                        return 8
                    elif field == "shipmentMethod":
                        return 9
                    return 10

                fallback_payload["filters"].sort(key=_fb_single_device_sort_key)
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "select"]:
                    if k in fallback_payload:
                        reordered[k] = fallback_payload[k]
                for k, v in fallback_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                fallback_payload.clear()
                fallback_payload.update(reordered)
            elif fb_is_trip_type_breakdown_query:
                def _fb_trip_type_breakdown_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "tripStatus":
                        return 1
                    elif field == "groupId":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "tripType":
                        return 5
                    return 10

                fallback_payload["filters"].sort(key=_fb_trip_type_breakdown_sort_key)
            elif fb_is_network_vs_local_query and not bool(detect_device_analytics(message)):
                fb_is_non_gps = bool(re.search(r'\b(?:non[\s-]?gps|no[\s-]?gps|without\s+(?:any\s+)?gps|had\s+no\s+(?:any\s+)?gps|nogps)\b', msg_lower))
                def _fb_network_vs_local_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if fb_is_non_gps:
                        if field == "tripStatus":
                            return 1
                        elif field == "groupId":
                            return 2
                    else:
                        if field == "groupId":
                            return 1
                        elif field == "tripStatus":
                            return 2
                    if field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    return 10

                fallback_payload["filters"].sort(key=_fb_network_vs_local_sort_key)
                if not fb_is_non_gps:
                    fallback_payload.pop("select", None)
            elif fb_is_no_gps_vehicle_query:
                def _fb_no_gps_vehicle_sort_key(flt):
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
                    elif field == "GPS":
                        return 3.5
                    elif field == "runDate" and op == "gte":
                        return 4
                    elif field == "runDate" and op == "lte":
                        return 5
                    elif field in ["routeCategory", "routeType", "tripType"]:
                        return 6
                    elif field == "shipmentMethod":
                        return 7
                    return 10

                fallback_payload["filters"].sort(key=_fb_no_gps_vehicle_sort_key)
                if not fb_specific_transporter:
                    fallback_payload.pop("select", None)
                reordered = {}
                for k in ["entity", "operation", "analytics", "filters", "groupBy", "metrics"]:
                    if k in fallback_payload:
                        reordered[k] = fallback_payload[k]
                for k, v in fallback_payload.items():
                    if k not in reordered:
                        reordered[k] = v
                fallback_payload.clear()
                fallback_payload.update(reordered)
            elif fb_is_gps_trip_query:
                def _fb_gps_trip_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "GPS":
                        return 3
                    elif field == "runDate" and op == "gte":
                        return 4
                    elif field == "runDate" and op == "lte":
                        return 5
                    elif field == "shipmentMethod":
                        return 6
                    elif field == "routeCategory":
                        return 7
                    return 10

                fallback_payload["filters"].sort(key=_fb_gps_trip_sort_key)
            elif fb_target_vendor and not fb_is_devices_together:
                def _fb_vendor_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 5
                    elif field in ("gps", "GPS", "FixedGPS", "fixedgps"):
                        return 6
                    elif field == "routeCategory":
                        return 7
                    elif field == "shipmentMethod":
                        return 8
                    return 10

                fallback_payload["filters"].sort(key=_fb_vendor_sort_key)

            if fb_is_include_cancelled:
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "tripStatus")
                ]
            else:
                target_status = status_override if status_override is not None else "valid"
                found_status = False
                for f in fallback_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") == "tripStatus":
                        f["value"] = target_status
                        f["operator"] = "eq"
                        found_status = True
                        break
                if not found_status:
                    fallback_payload.setdefault("filters", []).append({
                        "field": "tripStatus",
                        "operator": "eq",
                        "value": target_status
                    })
                if fb_target_vendor and not fb_is_devices_together:
                    fallback_payload["filters"].sort(key=_fb_vendor_sort_key)

            if fb_is_vendor_running_query and not fb_has_date_specified:
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "runDate")
                ]
            if fb_is_vendor_running_query and fb_target_vendor and not fb_is_devices_together:
                fallback_payload["filters"].sort(key=_fb_vendor_sort_key)

            if fb_is_transporter_unique_vehicles_query:
                fallback_payload["filters"].sort(key=_fb_transporter_unique_vehicles_sort_key)
                fallback_payload["select"] = []

            if fb_is_fixed_portable_elock_combo:
                def _fb_fixed_portable_combo_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "portable-elock-status":
                        return 5
                    elif field == "fixed-elock-status":
                        return 6
                    elif field in ("routeCategory", "routeType"):
                        return 7
                    return 10

                fallback_payload["filters"].sort(key=_fb_fixed_portable_combo_sort_key)
                fallback_payload.pop("select", None)
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)

            if fb_detected_regions or any(isinstance(f, dict) and f.get("field") == "region" for f in fallback_payload.get("filters", [])):
                def _fb_region_filter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ("source", "destination", "From", "To"):
                        return 5
                    elif field == "region":
                        return 6
                    elif field in ("Route", "route", "RouteName", "routeName"):
                        return 7
                    elif field in ("transporter", "Transporter"):
                        return 8
                    elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 9
                    elif field == "shipmentMethod":
                        return 10
                    elif field in ("routeCategory", "routeType", "tripType"):
                        return 11
                    elif field == "priority":
                        return 12
                    elif field in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                        return 13
                    elif field in ("atd", "ata", "ATD", "ATA"):
                        return 14
                    return 20

                if not (fb_is_fixed_portable_elock_combo or fb_is_transporter_unique_vehicles_query or fb_is_vendor_running_query):
                    fallback_payload["filters"].sort(key=_fb_region_filter_sort_key)

            if fb_detected_route or any(isinstance(f, dict) and f.get("field") in ("Route", "route", "RouteName", "routeName") for f in fallback_payload.get("filters", [])):
                def _fb_route_filter_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field in ("source", "destination", "From", "To"):
                        return 5
                    elif field == "region":
                        return 6
                    elif field in ("Route", "route", "RouteName", "routeName"):
                        return 7
                    elif field in ("transporter", "Transporter"):
                        return 8
                    elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                        return 9
                    elif field == "shipmentMethod":
                        return 10
                    elif field in ("routeCategory", "routeType", "tripType"):
                        return 11
                    elif field == "priority":
                        return 12
                    elif field in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                        return 13
                    elif field in ("atd", "ata", "ATD", "ATA"):
                        return 14
                    return 20

                if not (fb_is_fixed_portable_elock_combo or fb_is_transporter_unique_vehicles_query or fb_is_vendor_running_query):
                    fallback_payload["filters"].sort(key=_fb_route_filter_sort_key)

            # --- Priority Fallback Post-Processing ---
            if fb_is_priority_vs_query:
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "priority")
                ]
                fallback_payload["groupBy"] = ["priority"]
                fallback_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "uniqueShipment"
                    }
                ]
                fallback_payload.pop("select", None)
            elif fb_is_priority_query or any(isinstance(f, dict) and f.get("field") == "priority" for f in fallback_payload.get("filters", [])):
                fb_is_high = bool(
                    re.search(r'\b(?:high|critical|urgent)\s+priority\b', msg_lower)
                    or re.search(r'\bpriority\s+(?:is\s+|as\s+|to\s+)?(?:high|critical|urgent)\b', msg_lower)
                    or re.search(r'\bmarked\s+as\s+(?:high|critical|urgent)\s+priority\b', msg_lower)
                    or re.search(r'\bmarked\s+(?:as\s+)?priority\s+(?:high|critical|urgent)\b', msg_lower)
                )
                fb_priority_val = "high" if fb_is_high else "low"
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") == "priority")
                ]
                fallback_payload["filters"].append({
                    "field": "priority",
                    "operator": "eq",
                    "value": fb_priority_val
                })

            # --- closeremark Fallback Post-Processing ---
            if fb_is_closeremark_query or any(isinstance(f, dict) and f.get("field") in ("closeremark", "closeRemarks", "closeRemark", "close_remark") for f in fallback_payload.get("filters", [])):
                fb_detected_remark = None
                remark_pattern = r'\b(manual|manually|forcefully|forcefull|forceful|geofence|geo[\s-]fence|polygon|api|backend|web)\b'
                rm_match = re.search(remark_pattern, msg_lower)
                if rm_match:
                    raw_val = rm_match.group(1).replace("-", "").replace(" ", "")
                    fb_detected_remark = _REMARK_MAP.get(raw_val, raw_val)
                else:
                    for f in fallback_payload.get("filters", []):
                        if isinstance(f, dict) and f.get("field") in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                            v = str(f.get("value", "")).lower().strip()
                            fb_detected_remark = _REMARK_MAP.get(v, v)
                            break
                if fb_detected_remark:
                    fallback_payload["filters"] = [
                        f for f in fallback_payload.get("filters", [])
                        if not (isinstance(f, dict) and f.get("field") in ("closeremark", "closeRemarks", "closeRemark", "close_remark"))
                    ]
                    fallback_payload["filters"].append({
                        "field": "closeremark",
                        "operator": "eq",
                        "value": fb_detected_remark
                    })

            # --- ATD / ATA Fallback Post-Processing ---
            if fb_is_atd_ata_query or any(isinstance(f, dict) and str(f.get("field", "")).lower() in ("atd", "ata") for f in fallback_payload.get("filters", [])):
                fb_detected_atd_val = None
                fb_detected_ata_val = None
                if re.search(r'\batd\b', msg_lower):
                    if re.search(r'\b(?:not\s*captured|notcaptured)\b', msg_lower):
                        fb_detected_atd_val = "notcaptured"
                    elif re.search(r'\b(?:not\s*missing|notmissing)\b', msg_lower):
                        fb_detected_atd_val = "notmissing"
                    elif re.search(r'\bmissing\b', msg_lower):
                        fb_detected_atd_val = "missing"
                    elif re.search(r'\bcaptured\b', msg_lower):
                        fb_detected_atd_val = "captured"
                    else:
                        fb_detected_atd_val = "notcaptured"

                if re.search(r'\bata\b', msg_lower):
                    if re.search(r'\b(?:not\s*captured|notcaptured)\b', msg_lower):
                        fb_detected_ata_val = "notcaptured"
                    elif re.search(r'\b(?:not\s*missing|notmissing)\b', msg_lower):
                        fb_detected_ata_val = "notmissing"
                    elif re.search(r'\bmissing\b', msg_lower):
                        fb_detected_ata_val = "missing"
                    elif re.search(r'\bcaptured\b', msg_lower):
                        fb_detected_ata_val = "captured"
                    else:
                        fb_detected_ata_val = "notcaptured"

                for f in fallback_payload.get("filters", []):
                    if isinstance(f, dict) and str(f.get("field", "")).lower() in ("atd", "ata"):
                        f["field"] = str(f["field"]).lower()
                        raw_val = str(f.get("value", "")).lower().replace(" ", "").replace("_", "")
                        if f["field"] == "atd" and fb_detected_atd_val:
                            f["value"] = fb_detected_atd_val
                        elif f["field"] == "ata" and fb_detected_ata_val:
                            f["value"] = fb_detected_ata_val
                        else:
                            f["value"] = _ATD_ATA_VALUE_MAP.get(raw_val, raw_val)
                            if f["field"] == "atd":
                                fb_detected_atd_val = f["value"]
                            elif f["field"] == "ata":
                                fb_detected_ata_val = f["value"]

                if fb_detected_atd_val and not any(isinstance(f, dict) and f.get("field") == "atd" for f in fallback_payload.get("filters", [])):
                    fallback_payload["filters"].append({
                        "field": "atd",
                        "operator": "eq",
                        "value": fb_detected_atd_val
                    })
                if fb_detected_ata_val and not any(isinstance(f, dict) and f.get("field") == "ata" for f in fallback_payload.get("filters", [])):
                    fallback_payload["filters"].append({
                        "field": "ata",
                        "operator": "eq",
                        "value": fb_detected_ata_val
                    })

                if detect_device_analytics(message):
                    fallback_payload["filters"].sort(key=_fb_device_analytics_sort_key)

            if fb_is_multi_device_trip_query:
                def _fb_multi_device_sort_key(flt):
                    if not isinstance(flt, dict):
                        return 99
                    field = flt.get("field")
                    op = flt.get("operator")
                    if field == "groupId":
                        return 1
                    elif field == "tripStatus":
                        return 2
                    elif field == "runDate" and op == "gte":
                        return 3
                    elif field == "runDate" and op == "lte":
                        return 4
                    elif field == "FixedELock":
                        return 5
                    elif field == "PortableELock":
                        return 6
                    elif field == "GPS":
                        return 7
                    elif field in ("atd", "ata", "ATD", "ATA"):
                        return 8
                    return 10

                fallback_payload["filters"].sort(key=_fb_multi_device_sort_key)
                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)

            # --- Source / Destination Fallback Post-Processing ---
            for f in fallback_payload.get("filters", []):
                if isinstance(f, dict):
                    fld_lower = str(f.get("field", "")).lower()
                    if fld_lower in ("from", "origin", "source"):
                        f["field"] = "source"
                    elif fld_lower in ("to", "dest", "destination"):
                        f["field"] = "destination"

            # Remove any source/destination filters with invalid values (stop words, numeric years/dates, etc.)
            fallback_payload["filters"] = [
                f for f in fallback_payload.get("filters", [])
                if not (
                    isinstance(f, dict)
                    and f.get("field") in ("source", "destination")
                    and (
                        not str(f.get("value", "")).strip()
                        or str(f.get("value", "")).strip().lower() in _STOP_LOCATION_WORDS
                        or str(f.get("value", "")).strip().isdigit()
                        or bool(re.match(r'^\d{1,4}[-/.].*', str(f.get("value", "")).strip()))
                    )
                )
            ]

            fb_has_source = any(isinstance(f, dict) and f.get("field") == "source" for f in fallback_payload.get("filters", []))
            if not fb_has_source:
                src_val = extract_source_location(message)
                if src_val:
                    fallback_payload["filters"].append({
                        "field": "source",
                        "operator": "eq",
                        "value": src_val
                    })

            fb_has_dest = any(isinstance(f, dict) and f.get("field") == "destination" for f in fallback_payload.get("filters", []))
            if not fb_has_dest:
                dest_val = extract_destination_location(message)
                if dest_val:
                    fallback_payload["filters"].append({
                        "field": "destination",
                        "operator": "eq",
                        "value": dest_val
                    })

            # --- Vehicle Number Fallback Post-Processing ---
            fb_has_vehicle = any(isinstance(f, dict) and f.get("field") in ("vehicleNo", "vehicle_no") for f in fallback_payload.get("filters", []))
            if not fb_has_vehicle and not fb_is_distinct_vehicle_query and not fb_is_active_vehicle_query and not fb_is_no_gps_vehicle_query:
                veh_match = re.search(r'\b(?:vehicle(?:\s*(?:no|number|num))?\s*[:=]?\s*|trips\s+of\s+)?([A-Za-z]{2}\s*\d{1,2}\s*[A-Za-z]{1,3}\s*\d{4})\b', message, re.IGNORECASE)
                if not veh_match:
                    veh_match = re.search(r'\bvehicle\s+(?:no|number|num|#)\s*[:=]?\s*([A-Za-z0-9_-]+)\b', message, re.IGNORECASE)
                if veh_match:
                    v_val = re.sub(r'\s+', '', veh_match.group(1)).strip().upper()
                    if v_val.lower() not in _STOP_LOCATION_WORDS and not v_val.isdigit() and len(v_val) > 2 and any(c.isdigit() for c in v_val):
                        fallback_payload["filters"].append({
                            "field": "vehicleNo",
                            "operator": "eq",
                            "value": v_val
                        })

            # --- Vehicle Master Fallback Post-Processing ---
            fb_vm_detected = detect_vehicle_master(message)
            if fb_vm_detected:
                fb_has_vm = False
                for f in fallback_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master"):
                        f["field"] = "vehicleMaster"
                        f["operator"] = "eq"
                        f["value"] = fb_vm_detected
                        fb_has_vm = True
                        break
                if not fb_has_vm:
                    fallback_payload.setdefault("filters", []).append({
                        "field": "vehicleMaster",
                        "operator": "eq",
                        "value": fb_vm_detected
                    })
                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                fallback_payload.pop("analytics", None)

            # --- Route Master Fallback Post-Processing ---
            fb_rm_detected = detect_route_master(message)
            if fb_rm_detected:
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master", "fleetMaster", "fleet_master", "Route", "route", "RouteName", "routeName", "shipmentMethod"))
                ]
                fb_has_rm = False
                for f in fallback_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ("routeMaster", "route_master"):
                        f["field"] = "routeMaster"
                        f["operator"] = "eq"
                        f["value"] = fb_rm_detected
                        fb_has_rm = True
                        break
                if not fb_has_rm:
                    fallback_payload.setdefault("filters", []).append({
                        "field": "routeMaster",
                        "operator": "eq",
                        "value": fb_rm_detected
                    })
                if fb_rm_detected == "outside":
                    fallback_payload.setdefault("filters", []).append({
                        "field": "shipmentMethod",
                        "operator": "nin",
                        "value": ["Pick Up", "Delivery", "Pick Up and Delivery"]
                    })
                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                fallback_payload.pop("analytics", None)

            # --- Fleet Master Fallback Post-Processing ---
            fb_fm_detected = detect_fleet_master(message)
            if fb_fm_detected:
                fallback_payload["filters"] = [
                    f for f in fallback_payload.get("filters", [])
                    if not (isinstance(f, dict) and f.get("field") in ("vehicleMaster", "vehicle_master", "routeMaster", "route_master", "Route", "route", "RouteName", "routeName", "shipmentMethod"))
                ]
                fb_has_fm = False
                for f in fallback_payload.get("filters", []):
                    if isinstance(f, dict) and f.get("field") in ("fleetMaster", "fleet_master"):
                        f["field"] = "fleetMaster"
                        f["operator"] = "eq"
                        f["value"] = fb_fm_detected
                        fb_has_fm = True
                        break
                if not fb_has_fm:
                    fallback_payload.setdefault("filters", []).append({
                        "field": "fleetMaster",
                        "operator": "eq",
                        "value": fb_fm_detected
                    })
                if fb_fm_detected == "outside":
                    fallback_payload.setdefault("filters", []).append({
                        "field": "shipmentMethod",
                        "operator": "nin",
                        "value": ["Pick Up", "Delivery", "Pick Up and Delivery"]
                    })
                fallback_payload["select"] = []
                fallback_payload.pop("groupBy", None)
                fallback_payload.pop("metrics", None)
                fallback_payload.pop("analytics", None)

            def _fb_standard_filter_sort_key(flt):
                if not isinstance(flt, dict):
                    return 99
                field = flt.get("field")
                op = flt.get("operator")
                if field == "groupId":
                    return 1
                elif field == "tripStatus":
                    return 2
                elif field in ("GPS", "FixedGPS", "FixedELock", "PortableELock", "gps", "fixedgps"):
                    return 2.5
                elif field == "runDate" and op == "gte":
                    return 3
                elif field == "runDate" and op == "lte":
                    return 4
                elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and op == "gte":
                    return 4.1
                elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance") and op == "lte":
                    return 4.2
                elif field in ("fixedGpsDistanceKm", "fixedLockDistanceKm", "portableLockDistanceKm", "tripDistance"):
                    return 4.3
                elif field in ("area", "branch"):
                    return 4.4
                elif field in ("vehicleMaster", "vehicle_master", "routeMaster", "route_master", "fleetMaster", "fleet_master"):
                    return 4.5
                elif field in ("source", "destination", "From", "To"):
                    return 5
                elif field == "region":
                    return 6
                elif field in ("vehicleNo", "vehicle_no", "vehicle"):
                    return 7
                elif field in ("Route", "route", "RouteName", "routeName"):
                    return 8
                elif field in ("transporter", "Transporter"):
                    return 9
                elif field in ("vendor", "vendorgps", "vendorfixedelock", "vendorportableelock"):
                    return 10
                elif field == "shipmentMethod":
                    return 11
                elif field in ("routeCategory", "routeType", "tripType"):
                    return 12
                elif field == "priority":
                    return 13
                elif field in ("closeremark", "closeRemarks", "closeRemark", "close_remark"):
                    return 14
                elif field in ("atd", "ata", "ATD", "ATA"):
                    return 15
                return 20

            if not (fb_is_fixed_portable_elock_combo or fb_is_multi_device_trip_query or fb_is_transporter_unique_vehicles_query or fb_is_vendor_running_query or fb_is_active_device_elock_query or fb_is_active_device_portable_query or fb_is_distinct_transporter_query or fb_is_portable_elock_trip_query or fb_is_fixed_elock_trip_query or fb_is_fixed_gps_combo or fb_is_fixed_gps_only or fb_is_portable_elock_only or fb_is_fixed_elock_only or fb_is_trip_type_breakdown_query or fb_is_network_vs_local_query or fb_is_no_gps_vehicle_query or fb_is_gps_trip_query or (fb_target_vendor and not fb_is_devices_together) or bool(detect_device_analytics(message))):
                fallback_payload["filters"].sort(key=_fb_standard_filter_sort_key)

            fb_date_grp = detect_date_grouping(message)
            if fb_date_grp:
                fallback_payload["groupBy"] = ["runDate"]
                fallback_payload["groupByFormat"] = {"runDate": fb_date_grp}
                fallback_payload["metrics"] = [
                    {
                        "field": "shipmentNo",
                        "function": "count",
                        "alias": "TotalTrip"
                    }
                ]
                fallback_payload["sort"] = [
                    {
                        "field": "runDate",
                        "order": "asc"
                    }
                ]
                if "select" not in fallback_payload:
                    fallback_payload["select"] = []

            if "filters" in fallback_payload and isinstance(fallback_payload["filters"], list):
                fallback_payload["filters"] = [
                    f for f in fallback_payload["filters"]
                    if isinstance(f, dict) and f.get("value") is not None and str(f.get("value")).lower() != "null"
                ]

            reordered = {}
            for k in ["entity", "operation", "analytics", "filters", "select", "groupBy", "groupByFormat", "metrics", "sort"]:
                if k in fallback_payload:
                    reordered[k] = fallback_payload[k]
            for k, v in fallback_payload.items():
                if k not in reordered:
                    reordered[k] = v
            fallback_payload.clear()
            fallback_payload.update(reordered)

            print(f"[DEBUG_QUERY] Session: {session_id} | Source: regex | LLM Status: {fb_llm_status or 'fallback'} | Error Type: {fb_error_type} | Details: {str(fb_error_str)[:120] if fb_error_str else 'None'}")

            return {
                "intent": "trip_report",
                "entities": fallback_payload,
                "source": "regex",
                "llm_status": fb_llm_status or "fallback",
                "debug": {
                    "source": "regex",
                    "fallback": True,
                    "llm_attempted": True,
                    "llm_success": False,
                    "llm_provider": getattr(self.llm_client, "provider", "unknown"),
                    "llm_model": getattr(self.llm_client, "model", "unknown"),
                    "llm_status": fb_llm_status or "fallback",
                    "error_type": fb_error_type,
                    "error": fb_error_str
                }
            }
