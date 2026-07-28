from datetime import datetime, timedelta
import re

def resolve_date_expression(date_str: str) -> str:
    """
    Resolves relative date expressions like 'today', 'yesterday', or 'tomorrow'
    to standard YYYY-MM-DD format. Returns current date if format unrecognized.
    """
    if not date_str:
        return datetime.utcnow().strftime("%Y-%m-%d")
    
    clean_str = date_str.lower().strip()
    now = datetime.utcnow()
    
    if "today" in clean_str:
        return now.strftime("%Y-%m-%d")
    elif "yesterday" in clean_str:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    elif "tomorrow" in clean_str:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Try parsing YYYY-MM-DD or DD-MM-YYYY
    match_ymd = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", clean_str)
    if match_ymd:
        return f"{match_ymd.group(1)}-{int(match_ymd.group(2)):02d}-{int(match_ymd.group(3)):02d}"
        
    match_dmy = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", clean_str)
    if match_dmy:
        return f"{match_dmy.group(3)}-{int(match_dmy.group(2)):02d}-{int(match_dmy.group(1)):02d}"
        
    # Default fallback
    return now.strftime("%Y-%m-%d")
