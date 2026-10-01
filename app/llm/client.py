import httpx
import json
import asyncio
from typing import Any, Dict, Optional
from app.config import settings
from app.utils.logger import log_error

class LLMClientError(Exception):
    """Exception raised for LLM communication errors."""
    pass

class LLMRateLimitError(LLMClientError):
    """Exception raised when LLM returns a 429 rate limit error after all retries."""
    pass

class LLMClient:
    def __init__(self, provider: Optional[str] = None):
        selected_provider = (provider or settings.LLM_PROVIDER or "ollama").lower()
        self.provider = selected_provider

        if selected_provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
            self.url = f"{base_url}/chat/completions"
            self.api_key = settings.GROQ_API_KEY
            self.model = settings.GROQ_MODEL or "qwen-2.5-coder-32b"
            self.max_tokens = settings.GROQ_MAX_TOKENS
            self.temperature = settings.GROQ_TEMPERATURE
        elif selected_provider in ["ollama", "local", "lmstudio"]:
            base_url = (settings.LOCAL_LLM_BASE_URL or settings.LLM_BASE_URL).rstrip('/')
            self.url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
            self.api_key = settings.LOCAL_LLM_API_KEY or settings.LLM_API_KEY or "ollama"
            self.model = settings.LOCAL_LLM_MODEL or settings.LLM_MODEL or "qwen2.5-coder:0.5b"
            self.max_tokens = settings.LOCAL_LLM_MAX_TOKENS or settings.LLM_MAX_TOKENS
            self.temperature = settings.LOCAL_LLM_TEMPERATURE or settings.LLM_TEMPERATURE
        else:
            # Fallback to generic settings
            base_url = (settings.LLM_BASE_URL).rstrip('/')
            self.url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
            self.api_key = settings.LLM_API_KEY or "ollama"
            self.model = settings.LLM_MODEL or "qwen2.5-coder:0.5b"
            self.max_tokens = settings.LLM_MAX_TOKENS
            self.temperature = settings.LLM_TEMPERATURE

        self.timeout = settings.LLM_TIMEOUT

        # Retry settings for 429 rate-limit responses
        self.max_retries = 3
        self.retry_base_delay = 2.0  # seconds (doubles each attempt: 2s, 4s, 8s)

    async def generate(self, prompt: str, system: Optional[str] = None, format_json: bool = False, max_tokens: Optional[int] = None) -> str:
        """
        Sends a generation request to the LLM chat completions endpoint (Ollama / LM Studio / Groq).
        Automatically retries up to self.max_retries times on HTTP 429 (rate limit)
        using exponential backoff before raising LLMRateLimitError.
        """
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature
        }
        
        if format_json:
            payload["response_format"] = {"type": "json_object"}

        last_exception = None
        for attempt in range(self.max_retries + 1):  # 0, 1, 2, 3
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(self.url, headers=headers, json=payload)

                if response.status_code == 429:
                    # Try to read Retry-After header
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        wait_time = float(retry_after)
                    else:
                        wait_time = self.retry_base_delay * (2 ** attempt)  # 2s, 4s, 8s

                    if attempt < self.max_retries:
                        log_error(
                            "LLM", "LLM_RATE_LIMIT_RETRY",
                            f"LLM Provider ({self.provider}) 429 on attempt {attempt + 1}/{self.max_retries}. "
                            f"Retrying in {wait_time:.1f}s..."
                        )
                        await asyncio.sleep(wait_time)
                        last_exception = LLMRateLimitError(
                            f"LLM Provider ({self.provider}) returned 429 (attempt {attempt + 1}): {response.text}"
                        )
                        continue  # retry
                    else:
                        raise LLMRateLimitError(
                            f"LLM Provider ({self.provider}) returned 429 after {self.max_retries} retries: {response.text}"
                        )

                elif response.status_code != 200:
                    raise LLMClientError(
                        f"LLM Provider ({self.provider}) returned status code {response.status_code}: {response.text}"
                    )

                result = response.json()
                choices = result.get("choices", [])
                if not choices:
                    raise LLMClientError(f"No choices returned from LLM API response ({self.provider}).")

                return choices[0].get("message", {}).get("content", "").strip()

            except httpx.TimeoutException as te:
                log_error("LLM", "LLM_TIMEOUT", f"Timeout contacting LLM server ({self.url}).")
                raise LLMClientError(f"LLM response timed out contacting {self.url}.") from te
            except LLMRateLimitError as rle:
                raise rle
            except Exception as e:
                log_error("LLM", "LLM_ERROR", str(e))
                raise LLMClientError(f"Failed to communicate with LLM ({self.provider}): {str(e)}") from e

        # All retries exhausted
        raise last_exception or LLMRateLimitError(f"LLM Provider ({self.provider}) rate limit exceeded after all retries.")
