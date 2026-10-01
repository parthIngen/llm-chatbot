import os
import time
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from app.schemas.request import ChatMessageRequest
from app.schemas.response import ChatMessageResponse
from app.llm.intent_detector import IntentDetector, IntentDetectorError
from app.llm.response_generator import ResponseGenerator
from app.tools.router import ToolRouter, UnsupportedIntentError
from app.services.excel_service import generate_trips_excel, EXPORTS_DIR
from app.utils.logger import log_request_received, log_error
from app.llm.client import LLMRateLimitError

router = APIRouter()

# Dependency injection helpers
def get_intent_detector() -> IntentDetector:
    return IntentDetector()

def get_tool_router() -> ToolRouter:
    return ToolRouter()

def is_greeting(message: str) -> bool:
    clean_msg = message.strip().lower()
    pattern = r"^(hi|hello|hey|greetings|good\s+(morning|afternoon|evening))(\s+(there|chatbot|bot|assistant))?[?.!]*$"
    return bool(re.match(pattern, clean_msg))

def get_response_generator() -> ResponseGenerator:
    return ResponseGenerator()

def render_download_card_html(download_url: str, record_count: int = 0) -> str:
    count_badge = f'<span style="background: #e0e7ff; color: #3730a3; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">{record_count:,} trips</span>' if record_count > 0 else ''
    
    return (
        f'<div style="margin-top: 12px; padding: 14px 16px; background: #ffffff; border: 1px solid #e2e8f0; '
        f'border-left: 4px solid #10b981; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); '
        f'display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">'
        f'<div style="display: flex; align-items: center; gap: 12px;">'
        f'<div style="width: 38px; height: 38px; border-radius: 8px; background: #ecfdf5; display: flex; align-items: center; justify-content: center; font-size: 20px;">📊</div>'
        f'<div>'
        f'<div style="display: flex; align-items: center; gap: 8px;">'
        f'<span style="font-size: 14px; font-weight: 600; color: #1e293b;">Trips Excel Report</span>'
        f'{count_badge}'
        f'</div>'
        f'<div style="font-size: 12px; color: #64748b; margin-top: 2px;">Formatted spreadsheet (.xlsx) ready to export</div>'
        f'</div>'
        f'</div>'
        f'<a href="{download_url}" target="_blank" download style="display: inline-flex; align-items: center; gap: 6px; '
        f'background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: #ffffff; text-decoration: none; '
        f'font-size: 13px; font-weight: 600; padding: 8px 16px; border-radius: 6px; box-shadow: 0 2px 6px rgba(16, 185, 129, 0.3);">'
        f'<span>Download Excel</span>'
        f'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>'
        f'<polyline points="7 10 12 15 17 10"></polyline>'
        f'<line x1="12" y1="15" x2="12" y2="3"></line>'
        f'</svg>'
        f'</a>'
        f'</div>'
    )

@router.get("/download/{filename}")
async def download_file(filename: str):
    """
    Serves generated Excel files (.xlsx) for direct frontend download.
    """
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(EXPORTS_DIR, safe_filename)
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requested file not found or has expired."
        )
    return FileResponse(
        path=file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=safe_filename
    )

@router.post("/chat", response_model=ChatMessageResponse)
async def chat_endpoint(
    http_request: Request,
    request: ChatMessageRequest,
    intent_detector: IntentDetector = Depends(get_intent_detector),
    tool_router: ToolRouter = Depends(get_tool_router),
    response_generator: ResponseGenerator = Depends(get_response_generator)
):
    try:
        session_id = request.session_id
        log_request_received(session_id, request.message)
        
        # Check if message is a simple greeting
        if is_greeting(request.message):
            reply = "I am Secutrak AI chatbot ,I am here to resolve your queries kindly ask me question about trips,"
            return ChatMessageResponse(
                reply=reply,
                intent="greeting",
                entities={},
                data=None,
                download_url=None,
                query_payload=None
            )
        
        # 1. Intent Detection and Entity Extraction
        try:
            extraction = await intent_detector.detect(
                message=request.message,
                history=[h.model_dump() for h in request.history[-3:]],
                session_id=session_id
            )
        except IntentDetectorError as ide:
            log_error(session_id, "INTENT_DETECTION_ERROR", str(ide))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Intent detection service error: {str(ide)}"
            )

        intent = extraction.get("intent", "unknown")
        entities = extraction.get("entities", {})

        # If LLM classified intent as unknown/unrecognized, raise or return generic response
        if intent == "unknown":
            reply = "I'm sorry, I could not understand your request. Could you please rephrase it?"
            return ChatMessageResponse(
                reply=reply,
                intent=intent,
                entities=entities,
                data=None,
                download_url=None,
                query_payload=entities if entities else None
            )

        # Validate date range for trip report intent
        if intent == "trip_report":
            has_start_date = False
            has_end_date = False
            filters = entities.get("filters", [])

            # Check if this is a currently-active/live trip query (tripStatus = "Active" or "Running").
            # Active trips are live by definition and do not require a historical date range.
            is_active_trip_query = any(
                isinstance(f, dict)
                and f.get("field") == "tripStatus"
                and str(f.get("value", "")).lower() in ("running", "active")
                for f in filters
            ) or bool(re.search(r'\b(?:in[\s-]transit|transit|running|currently\s+active)\b', request.message.lower()))

            if not is_active_trip_query:
                for f in filters:
                    if isinstance(f, dict) and f.get("field") == "runDate":
                        op = f.get("operator")
                        if op == "gte":
                            has_start_date = True
                        elif op == "lte":
                            has_end_date = True

                if not (has_start_date and has_end_date):
                    reply = "Please provide the date range (start date and end date) for the trips."
                    return ChatMessageResponse(
                        reply=reply,
                        intent=intent,
                        entities=entities,
                        data=None,
                        download_url=None,
                        query_payload=entities if entities else None
                    )

        # 2. Tool Execution
        try:
            api_data = await tool_router.route_and_execute(
                intent=intent,
                entities=entities,
                access_token=request.AccessToken,
                session_id=session_id
            )
        except UnsupportedIntentError as uie:
            log_error(session_id, "UNSUPPORTED_INTENT", str(uie))
            return ChatMessageResponse(
                reply=f"I recognized the intent as '{intent}', but that capability is not enabled on this backend yet.",
                intent=intent,
                entities=entities,
                data=None,
                download_url=None,
                query_payload=entities if entities else None
            )
        except Exception as e:
            log_error(session_id, "UNEXPECTED_SYSTEM_ERROR", str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while processing the request."
            )

        # 3. Handle Excel Export if record list is returned
        download_url: Optional[str] = None
        records = []
        if isinstance(api_data, dict):
            if isinstance(api_data.get("data"), list):
                records = api_data["data"]
            elif isinstance(api_data.get("Report"), list):
                records = api_data["Report"]
            elif isinstance(api_data.get("trips"), list):
                records = api_data["trips"]
        elif isinstance(api_data, list):
            records = api_data

        if records and len(records) > 0:
            try:
                excel_filename = generate_trips_excel(records)
                base_url = str(http_request.base_url).rstrip("/")
                download_url = f"{base_url}/api/v1/download/{excel_filename}"
            except Exception as exp_err:
                log_error(session_id, "EXCEL_GENERATION_FAILED", str(exp_err))

        # 4. Response Generation
        try:
            reply = await response_generator.generate_response(
                message=request.message,
                api_result=api_data,
                session_id=session_id,
                entities=entities
            )
        except Exception as exc:
            log_error(session_id, "RESPONSE_GEN_ERROR", str(exc))
            reply = f"I found {len(records)} trip details matching your query."

        # Append plain text download link if download was requested and link isn't already included
        is_download_request = any(w in request.message.lower() for w in ["download", "export", "excel", "sheet", "csv", "xlsx", "file"])
        if download_url and is_download_request:
            if download_url not in reply:
                reply = reply.strip() + f"\n\nDownload Excel report: {download_url}"

        return ChatMessageResponse(
            reply=reply,
            intent=intent,
            entities=entities,
            data=api_data,
            download_url=download_url,
            query_payload=entities if entities else None
        )
    except LLMRateLimitError:
        return ChatMessageResponse(
            reply="Server is busy , please retry in a few seconds",
            intent="error",
            entities={},
            data=None,
            download_url=None,
            query_payload=None
        )
