import logging
import sys
import time
from typing import Any, Dict
from fastapi import Request

# Configure structured console logging
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)

logger = logging.getLogger("chatbot")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(handler)
logger.propagate = False

# Also configure root logger with force=True for any other loggers
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)

def log_request_received(session_id: str, message: str):
    logger.info(f"[REQUEST_RECEIVED] Session: {session_id} | Message: '{message}'")

def log_intent_detected(session_id: str, intent: str, confidence: float = 1.0):
    logger.info(f"[INTENT_DETECTED] Session: {session_id} | Intent: {intent}")

def log_entities_extracted(session_id: str, entities: Dict[str, Any]):
    logger.info(f"[ENTITIES_EXTRACTED] Session: {session_id} | Entities: {entities}")

def log_tool_selected(session_id: str, tool_name: str):
    logger.info(f"[TOOL_SELECTED] Session: {session_id} | Tool: {tool_name}")

def log_api_call(session_id: str, url: str, payload: Dict[str, Any]):
    logger.info(f"[API_CALL] Session: {session_id} | URL: {url} | Payload: {payload}")

def log_api_response(session_id: str, duration: float, status_code: int):
    logger.info(f"[API_RESPONSE] Session: {session_id} | Status: {status_code} | Duration: {duration:.3f}s")

def log_error(session_id: str, error_type: str, message: str, details: Any = None):
    logger.error(f"[ERROR] Session: {session_id} | Type: {error_type} | Message: {message} | Details: {details}")
