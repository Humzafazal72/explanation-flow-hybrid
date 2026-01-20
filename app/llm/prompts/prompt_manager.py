import yaml
from pathlib import Path
from typing import Optional, Literal


def load_yaml(path: Path | str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


class PromptManager:
    def __init__(self, type_: Literal["VOICE_AGENT", "CODER"]):
        if type_ == "VOICE_AGENT":
            data = load_yaml(path=Path("llm/prompts/system_prompt_voice.yaml"))
        elif type_ == "CODER":
            data = load_yaml(path=Path("llm/prompts/system_prompt_coder.yaml"))

        if "versions" not in data:
            raise KeyError("YAML file must contain a top-level 'versions' key")

        self.versions: dict = data["versions"]

        if not self.versions:
            raise ValueError("No prompt versions found in YAML")

    def get_sys_prompt(self, version: Optional[str] = None) -> str:

        if version is None or version not in self.versions:
            # semantic versioning as strings: "1.10" > "1.2"
            version = max(
                self.versions.keys(), key=lambda v: tuple(map(int, str(v).split(".")))
            )
        print(self.versions)
        prompt = self.versions[version].get("SYSTEM_PROMPT")

        if prompt is None:
            raise KeyError(f"'SYSTEM_PROMPT' missing for version '{version}'")

        return prompt
