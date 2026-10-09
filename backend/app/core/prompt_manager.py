"""
Prompt Manager Module.
Centralized, decoupled and validated prompt registry for the Multi-Agent Financial Assistant.
Loads prompt templates from YAML files and validates required template parameters via Pydantic.
"""

from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml
from pydantic import BaseModel, Field


class PromptTemplateModel(BaseModel):
    name: str
    version: str = "1.0.0"
    description: Optional[str] = None
    required_variables: List[str] = Field(default_factory=list)
    system_template: str


class PromptManager:
    """
    Manages loading, caching, validation and rendering of system prompt templates.
    """

    def __init__(self, prompts_dir: Optional[Path] = None):
        if prompts_dir is None:
            # Default to backend/app/prompts relative to this file
            self.prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        else:
            self.prompts_dir = Path(prompts_dir)

        self._prompts: Dict[str, PromptTemplateModel] = {}
        self.reload()

    def reload(self) -> None:
        """Loads and caches all YAML prompt templates from the prompts directory."""
        self._prompts.clear()
        if not self.prompts_dir.exists():
            return

        for file_path in self.prompts_dir.glob("*.yaml"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    prompt_obj = PromptTemplateModel(**data)
                    self._prompts[prompt_obj.name] = prompt_obj
            except Exception as e:
                raise RuntimeError(f"Error cargando el prompt desde {file_path}: {e}") from e

    def list_prompts(self) -> List[str]:
        """Returns the list of registered prompt names."""
        return list(self._prompts.keys())

    def get_prompt(self, name: str) -> PromptTemplateModel:
        """Retrieves the PromptTemplateModel for a given name."""
        if name not in self._prompts:
            raise KeyError(
                f"Prompt '{name}' no encontrado. Prompts disponibles: {list(self._prompts.keys())}"
            )
        return self._prompts[name]

    def render(self, name: str, **kwargs: Any) -> str:
        """
        Validates required parameters and renders the system template string.
        """
        prompt = self.get_prompt(name)

        missing = [var for var in prompt.required_variables if var not in kwargs]
        if missing:
            raise ValueError(
                f"Variables faltantes para renderizar el prompt '{name}': {missing}"
            )

        template_str = prompt.system_template.strip()
        return template_str.format(**kwargs)


# Singleton instance
prompt_manager = PromptManager()
