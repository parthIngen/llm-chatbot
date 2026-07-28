import os
from typing import Any
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Groq settings
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-8b-instant"
    GROQ_MAX_TOKENS: int = 1500
    GROQ_TEMPERATURE: float = 0.2
    LLM_TIMEOUT: float = 500.0

    # Trip API settings
    TRIP_API_URL: str = "https://prompt.secutrak.in/api/tripsTest"
    TRIP_API_TIMEOUT: float = 120.0
    
    # Defaults for API calls
    DEFAULT_GROUP_ID: int = 1001
    DEFAULT_TRIP_TYPE: str = "Standard"
    DEFAULT_TRIP_STATUS: str = "Active"
    
    # Application settings
    APP_NAME: str = "AI Chatbot Backend"
    DEBUG: bool = False

    @model_validator(mode="before")
    @classmethod
    def strip_string_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
        return data

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
# Force override TRIP_API_URL to use the correct tripsTest endpoint, preventing environment overrides
settings.TRIP_API_URL = "https://prompt.secutrak.in/api/tripsTest"
