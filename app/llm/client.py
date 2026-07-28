import httpx
import json
from typing import Any, Dict, Optional
from app.config import settings
from app.utils.logger import log_error

class LLMClientError(Exception):
    """Exception raised for LLM communication errors."""
    pass

class LLMRateLimitError(LLMClientError):
    """Exception raised when LLM returns a 429 rate limit error."""
    pass

class LLMClient:
    def __init__(self):
        self.url = "https://api.groq.com/openai/v1/chat/completions"
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.GROQ_MODEL
        self.max_tokens = settings.GROQ_MAX_TOKENS
        self.temperature = settings.GROQ_TEMPERATURE
        self.timeout = settings.LLM_TIMEOUT

    async def generate(self, prompt: str, system: Optional[str] = None, format_json: bool = False) -> str:
        """
        Sends a generation request to the Groq chat completions endpoint.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature
        }
        
        if format_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.url, headers=headers, json=payload)
                
            if response.status_code == 429:
                raise LLMRateLimitError(f"Groq returned status code 429: {response.text}")
            elif response.status_code != 200:
                raise LLMClientError(f"Groq returned status code {response.status_code}: {response.text}")
                
            result = response.json()
            choices = result.get("choices", [])
            if not choices:
                raise LLMClientError("No choices returned from Groq API response.")
                
            return choices[0].get("message", {}).get("content", "").strip()
            
        except httpx.TimeoutException as te:
            log_error("LLM", "LLM_TIMEOUT", "Timeout contacting Groq server.")
            raise LLMClientError("LLM response timed out.") from te
        except LLMRateLimitError as rle:
            raise rle
        except Exception as e:
            log_error("LLM", "LLM_ERROR", str(e))
            raise LLMClientError(f"Failed to communicate with LLM: {str(e)}") from e
