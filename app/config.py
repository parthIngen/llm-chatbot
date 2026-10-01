import os
from typing import Any
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # LLM Provider Routing Settings
    LLM_PROVIDER: str = "groq"  # groq, hybrid, ollama
    INTENT_LLM_PROVIDER: str = "groq"  # Used for intent detection & payload generation
    RESPONSE_LLM_PROVIDER: str = "ollama"  # Used for natural language response generation (Local Ollama qwen2.5-coder:0.5b)

    # Local LLM Settings (Ollama / LM Studio)
    LOCAL_LLM_BASE_URL: str = "http://localhost:11434/v1"
    LOCAL_LLM_MODEL: str = "qwen2.5-coder:0.5b"
    LOCAL_LLM_API_KEY: str = "ollama"
    LOCAL_LLM_MAX_TOKENS: int = 1500
    LOCAL_LLM_TEMPERATURE: float = 0.2
    LOCAL_LLM_TIMEOUT: float = 60.0

    # Generic LLM Fallbacks
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "qwen2.5-coder:0.5b"
    LLM_API_KEY: str = "ollama"
    LLM_MAX_TOKENS: int = 1500
    LLM_TEMPERATURE: float = 0.2
    LLM_TIMEOUT: float = 150.0

    # Groq Settings (Remote Cloud API)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "qwen/qwen3.8-27b"
    GROQ_MAX_TOKENS: int = 1500
    GROQ_TEMPERATURE: float = 0.2

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


