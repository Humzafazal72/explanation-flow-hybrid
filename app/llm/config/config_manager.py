import json
from typing import Literal, Any


class ConfigManager:
    def __init__(self, provider: Literal["gemini", "openai"]):
        path = f"llm/config/{provider}_config.json"
        with open(path, "r", encoding="utf-8") as file:
            self.config: dict[str, Any] = json.load(file)

    def get_config(self) -> dict[str, Any]:
        return self.config
