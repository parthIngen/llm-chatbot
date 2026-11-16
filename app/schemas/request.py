from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional

class ChatHistoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str = Field(..., description="Role of the speaker (user or assistant)")
    content: str = Field(..., description="Content of the message")
    query_payload: Optional[Dict[str, Any]] = None
    entities: Optional[Dict[str, Any]] = None
    filters: Optional[List[Dict[str, Any]]] = None

class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: str = Field(..., description="The user query")
    history: List[ChatHistoryItem] = Field(default_factory=list, description="Conversation history")
    session_id: str = Field(..., description="Session identifier for tracking")
    AccessToken: str = Field(..., description="Access token for authenticating external calls")
