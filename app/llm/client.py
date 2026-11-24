import time
import httpx
import json
import asyncio
from typing import Any, Dict, Optional
from app.config import settings
from app.utils.logger import log_error, logger

class LLMClientError(Exception):
    """Exception raised for LLM communication errors."""
    pass

class LLMRateLimitError(LLMClientError):
    """Exception raised when LLM returns a 429 rate limit error after all retries."""
    pass

# Shared persistent HTTP client with connection pooling
_shared_client: Optional[httpx.AsyncClient] = None
_groq_rate_limited_until: float = 0.0

def get_shared_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """
    Returns a process-wide shared httpx.AsyncClient with connection pooling.
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
            keepalive_expiry=60.0
        )
        _shared_client = httpx.AsyncClient(limits=limits, timeout=timeout)
    return _shared_client

async def close_shared_client() -> None:
    """
    Gracefully closes the shared HTTP client on application shutdown.
    """
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None

class LLMClient:
    def __init__(self, provider: Optional[str] = None):
        selected_provider = (provider or settings.LLM_PROVIDER or "ollama").lower()
        self.provider = selected_provider

        self.last_used_provider = self.provider
        self.last_used_model = getattr(settings, "GROQ_MODEL", "llama-3.1-8b-instant") if selected_provider == "groq" else (settings.LOCAL_LLM_MODEL or "qwen2.5-coder:0.5b")
        self.last_fallback_to_local = False

        if selected_provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
            self.url = f"{base_url}/chat/completions"
            self.api_key = settings.GROQ_API_KEY
            self.model = settings.GROQ_MODEL or "llama-3.1-8b-instant"
            self.max_tokens = settings.GROQ_MAX_TOKENS
            self.temperature = settings.GROQ_TEMPERATURE if settings.GROQ_TEMPERATURE is not None else 0.2
            self.timeout = settings.LLM_TIMEOUT
        elif selected_provider in ["ollama", "local", "lmstudio"]:
            base_url = (settings.LOCAL_LLM_BASE_URL or settings.LLM_BASE_URL).rstrip('/')
            self.url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
            self.api_key = settings.LOCAL_LLM_API_KEY or settings.LLM_API_KEY or "ollama"
            self.model = settings.LOCAL_LLM_MODEL or settings.LLM_MODEL or "qwen2.5-coder:0.5b"
            self.max_tokens = settings.LOCAL_LLM_MAX_TOKENS or settings.LLM_MAX_TOKENS
            self.temperature = (
                settings.LOCAL_LLM_TEMPERATURE
                if settings.LOCAL_LLM_TEMPERATURE is not None
                else (settings.LLM_TEMPERATURE if settings.LLM_TEMPERATURE is not None else 0.0)
            )
            self.timeout = settings.LOCAL_LLM_TIMEOUT
        else:
            # Fallback to generic settings
            base_url = settings.LLM_BASE_URL.rstrip('/')
            self.url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
            self.api_key = settings.LLM_API_KEY or "ollama"
            self.model = settings.LLM_MODEL or "qwen2.5-coder:0.5b"
            self.max_tokens = settings.LLM_MAX_TOKENS
            self.temperature = settings.LLM_TEMPERATURE if settings.LLM_TEMPERATURE is not None else 0.0
            self.timeout = settings.LLM_TIMEOUT

        # Retry settings for 429 rate-limit responses (cloud providers like Groq)
        self.max_retries = 3 if selected_provider == "groq" else 0
        self.retry_base_delay = 2.0  # seconds (doubles each attempt: 2s, 4s, 8s)

    async def _generate_local(
        self,
        prompt: str,
        system: Optional[str] = None,
        format_json: bool = False,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Executes generation using the local LLM (Ollama / LM Studio) when primary LLM limit is exceeded.
        """
        local_base_url = (settings.LOCAL_LLM_BASE_URL or settings.LLM_BASE_URL).rstrip('/')
        url = local_base_url if local_base_url.endswith("/chat/completions") else f"{local_base_url}/chat/completions"
        api_key = settings.LOCAL_LLM_API_KEY or settings.LLM_API_KEY or "ollama"
        model = settings.LOCAL_LLM_MODEL or settings.LLM_MODEL or "qwen2.5-coder:0.5b"
        temperature = (
            settings.LOCAL_LLM_TEMPERATURE
            if settings.LOCAL_LLM_TEMPERATURE is not None
            else (settings.LLM_TEMPERATURE if settings.LLM_TEMPERATURE is not None else 0.0)
        )
        timeout = settings.LOCAL_LLM_TIMEOUT or 60.0
        effective_max_tokens = max_tokens if max_tokens is not None else (settings.LOCAL_LLM_MAX_TOKENS or 512)

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": effective_max_tokens,
            "temperature": temperature
        }
        if format_json:
            payload["response_format"] = {"type": "json_object"}

        keep_alive = getattr(settings, "LLM_KEEP_ALIVE", None)
        if keep_alive:
            payload["keep_alive"] = keep_alive

        t_start = time.perf_counter()
        client = get_shared_client(timeout=timeout)
        response = await client.post(url, headers=headers, json=payload)
        call_duration = time.perf_counter() - t_start

        if response.status_code != 200:
            raise LLMClientError(f"Local LLM fallback failed with status {response.status_code}: {response.text}")

        result = response.json()
        choices = result.get("choices", [])
        if not choices:
            raise LLMClientError("No choices returned from local LLM fallback.")

        content = choices[0].get("message", {}).get("content", "").strip()
        self.last_used_provider = "ollama"
        self.last_used_model = model
        self.last_fallback_to_local = True

        logger.info(
            "Local LLM Fallback Call Finished | model=%s | duration=%.3fs | response_chars=%d",
            model, call_duration, len(content)
        )
        return content

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        format_json: bool = False,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Sends a generation request to the LLM chat completions endpoint (Ollama / LM Studio / Groq).
        Reuses a shared httpx.AsyncClient with connection pooling.
        Logs latency, token metrics, and Ollama evaluation duration.
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

        global _groq_rate_limited_until

        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens

        # If Groq is currently in rate-limit cooldown, directly use local LLM
        if self.provider == "groq" and time.time() < _groq_rate_limited_until:
            remaining = _groq_rate_limited_until - time.time()
            logger.warning(
                "[LLM_COOLDOWN] Groq limit exceeded cooldown active (%.1fs remaining). "
                "Directly routing to local LLM (%s)...",
                remaining, settings.LOCAL_LLM_MODEL
            )
            try:
                return await self._generate_local(
                    prompt=prompt,
                    system=system,
                    format_json=format_json,
                    max_tokens=effective_max_tokens
                )
            except Exception as e:
                logger.error("[LOCAL_LLM_FAILED] Local LLM also failed: %s", str(e))
                # Fall through to attempt Groq anyway

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": effective_max_tokens,
            "temperature": self.temperature
        }
        
        if format_json:
            payload["response_format"] = {"type": "json_object"}

        # Configure keep_alive for Ollama to prevent model unloading between requests
        if self.provider in ["ollama", "local", "lmstudio"]:
            keep_alive = getattr(settings, "LLM_KEEP_ALIVE", None)
            if keep_alive:
                payload["keep_alive"] = keep_alive

        system_len = len(system) if system else 0
        prompt_len = len(prompt)
        total_prompt_chars = system_len + prompt_len

        last_exception = None
        for attempt in range(self.max_retries + 1):
            t_start = time.perf_counter()
            try:
                client = get_shared_client(timeout=self.timeout)
                response = await client.post(self.url, headers=headers, json=payload)
                call_duration = time.perf_counter() - t_start

                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    wait_time = float(retry_after) if retry_after else 30.0
                    _groq_rate_limited_until = time.time() + wait_time
                    log_error(
                        "LLM", "LLM_RATE_LIMIT_EXCEEDED",
                        f"Groq limit exceeded (429) for model {self.model}. "
                        f"Switching to local LLM ({settings.LOCAL_LLM_MODEL}). Cooldown set for {wait_time:.1f}s."
                    )
                    if self.provider == "groq":
                        try:
                            return await self._generate_local(
                                prompt=prompt,
                                system=system,
                                format_json=format_json,
                                max_tokens=effective_max_tokens
                            )
                        except Exception as local_err:
                            log_error("LLM", "LOCAL_LLM_FALLBACK_FAILED", str(local_err))
                            raise LLMRateLimitError(
                                f"Groq limit exceeded and local LLM fallback failed: {str(local_err)}"
                            ) from local_err
                    raise LLMRateLimitError(
                        f"LLM Provider ({self.provider}) returned 429: {response.text}"
                    )

                elif response.status_code != 200:
                    raise LLMClientError(
                        f"LLM Provider ({self.provider}) returned status code {response.status_code}: {response.text}"
                    )

                result = response.json()
                choices = result.get("choices", [])
                if not choices:
                    raise LLMClientError(f"No choices returned from LLM API response ({self.provider}).")

                content = choices[0].get("message", {}).get("content", "").strip()
                resp_len = len(content)

                # Extract Ollama / OpenAI usage or timing metadata if present
                usage = result.get("usage", {})
                ollama_metrics = {
                    k: result[k] for k in (
                        "load_duration", "prompt_eval_duration", "eval_duration",
                        "prompt_eval_count", "eval_count", "total_duration"
                    ) if k in result
                }

                self.last_used_provider = self.provider
                self.last_used_model = self.model
                self.last_fallback_to_local = False

                logger.info(
                    "LLM Call Finished | provider=%s | model=%s | duration=%.3fs | "
                    "system_chars=%d | prompt_chars=%d | total_prompt_chars=%d | "
                    "response_chars=%d | usage=%s | ollama_metrics=%s",
                    self.provider, self.model, call_duration,
                    system_len, prompt_len, total_prompt_chars,
                    resp_len, usage, ollama_metrics or "N/A"
                )

                return content

            except httpx.TimeoutException as te:
                call_duration = time.perf_counter() - t_start
                log_error("LLM", "LLM_TIMEOUT", f"Timeout after {call_duration:.2f}s contacting LLM server ({self.url}).")
                if self.provider == "groq":
                    logger.warning("[LLM_TIMEOUT_FALLBACK] Groq timed out. Switching to local LLM...")
                    try:
                        return await self._generate_local(
                            prompt=prompt,
                            system=system,
                            format_json=format_json,
                            max_tokens=effective_max_tokens
                        )
                    except Exception as local_err:
                        log_error("LLM", "LOCAL_LLM_FALLBACK_FAILED", str(local_err))
                raise LLMClientError(f"LLM response timed out contacting {self.url}.") from te
            except LLMRateLimitError as rle:
                raise rle
            except LLMClientError:
                raise
            except Exception as e:
                log_error("LLM", "LLM_ERROR", str(e))
                raise LLMClientError(f"Failed to communicate with LLM ({self.provider}): {str(e)}") from e

        # All retries exhausted
        raise last_exception or LLMRateLimitError(f"LLM Provider ({self.provider}) rate limit exceeded after all retries.")
