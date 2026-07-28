from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class ChatHistoryItem(BaseModel):
    role: str = Field(..., description="Role of the speaker (user or assistant)")
    content: str = Field(..., description="Content of the message")

class ChatMessageRequest(BaseModel):
    message: str = Field(..., description="The user query")
    history: List[ChatHistoryItem] = Field(default_factory=list, description="Conversation history")
    session_id: str = Field(..., description="Session identifier for tracking")
    AccessToken: str = Field(..., description="Access token for authenticating external calls")
