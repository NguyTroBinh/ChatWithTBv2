from app.core.config import LLM_API_BASE, LLM_API_KEY, LLM_MAX_TOKENS, LLM_MODEL, LLM_TEMPERATURE

class LiteLLMClient:
    def __init__(
        self,
        model: str = LLM_MODEL,
        api_base: str = LLM_API_BASE,
        api_key: str = LLM_API_KEY,
        temperature: float = LLM_TEMPERATURE,
        max_tokens: int = LLM_MAX_TOKENS,
    ):
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

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
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        response = litellm.completion(**kwargs)
        return response.choices[0].message.content
