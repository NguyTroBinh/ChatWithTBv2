from app.core.config import (
    LLM_API_BASE,
    LLM_API_KEY,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_REASONING_EFFORT,
    LLM_TEMPERATURE,
    LLM_THINK,
    LLM_TIMEOUT,
)

class LiteLLMClient:
    def __init__(
        self,
        model: str = LLM_MODEL,
        api_base: str = LLM_API_BASE,
        api_key: str = LLM_API_KEY,
        temperature: float = LLM_TEMPERATURE,
        max_tokens: int = LLM_MAX_TOKENS,
        timeout: int = LLM_TIMEOUT,
        think: bool = LLM_THINK,
        reasoning_effort: str = LLM_REASONING_EFFORT,
    ):
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.think = think
        self.reasoning_effort = reasoning_effort

    def generate(self, messages: list[dict]) -> str:
        try:
            import litellm
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install litellm from requirements.txt.") from exc

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.model.startswith("ollama"):
            kwargs["extra_body"] = {"think": self.think}
        if self.model.startswith("nvidia_nim/") and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
            kwargs["allowed_openai_params"] = ["reasoning_effort"]

        response = litellm.completion(**kwargs)
        return response.choices[0].message.content
