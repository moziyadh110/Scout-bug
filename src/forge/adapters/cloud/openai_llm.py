"""OpenAI-compatible chat endpoint: DigitalOcean Gradient serverless inference, DeepSeek, etc."""

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from forge.ports import Completion

_RETRYABLE = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0) -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY is not set")
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(min=2, max=20),
        reraise=True,
    )
    def complete(self, model: str, system: str, user: str) -> Completion:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": user}
        ]
        r = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=4096,  # type: ignore[arg-type]
        )
        text = r.choices[0].message.content or ""
        tokens = r.usage.total_tokens if r.usage else 0
        return Completion(text, tokens, r.model or model)
