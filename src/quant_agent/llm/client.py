from __future__ import annotations

import os

from quant_agent.llm.deepseek_client import DeepSeekChatClient
from quant_agent.llm.mock import MockLLMClient
from quant_agent.llm.openai_client import OpenAIResponsesClient
from quant_agent.llm.secrets import get_secret


def create_llm_client(provider: str = "auto", model: str | None = None):
    if provider == "mock":
        return MockLLMClient()
    if provider == "openai":
        return OpenAIResponsesClient(model=model)
    if provider == "deepseek":
        return DeepSeekChatClient(model=model)
    if provider != "auto":
        raise ValueError(f"Unknown LLM provider: {provider}")
    if get_secret("OPENAI_API_KEY"):
        return OpenAIResponsesClient(model=model)
    if get_secret("DEEPSEEK_API_KEY"):
        return DeepSeekChatClient(model=model)
    return MockLLMClient()
