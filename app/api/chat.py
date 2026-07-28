import time
import re
from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas.request import ChatMessageRequest
from app.schemas.response import ChatMessageResponse
from app.llm.intent_detector import IntentDetector, IntentDetectorError
from app.llm.response_generator import ResponseGenerator
from app.tools.router import ToolRouter, UnsupportedIntentError
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

@router.post("/chat", response_model=ChatMessageResponse)
async def chat_endpoint(
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
                data=None
            )
        
        # 1. Intent Detection and Entity Extraction
        try:
            extraction = await intent_detector.detect(
                message=request.message,
                history=[h.model_dump() for h in request.history],
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
                data=None
            )

        # Validate date range for trip report intent
        if intent == "trip_report":
            has_start_date = False
            has_end_date = False
            filters = entities.get("filters", [])
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
                    data=None
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
                data=None
            )
        except Exception as e:
            log_error(session_id, "UNEXPECTED_SYSTEM_ERROR", str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while processing the request."
            )

        # 3. Response Generation
        try:
            reply = await response_generator.generate_response(
                message=request.message,
                api_result=api_data,
                session_id=session_id,
                entities=entities
            )
        except Exception as exc:
            log_error(session_id, "RESPONSE_GEN_ERROR", str(exc))
            reply = "I found the trip details, but had trouble styling the answer. Please check the raw data below."

        return ChatMessageResponse(
            reply=reply,
            intent=intent,
            entities=entities,
            data=api_data
        )
    except LLMRateLimitError:
        return ChatMessageResponse(
            reply="Server is busy , please retry in a few seconds",
            intent="error",
            entities={},
            data=None
        )
