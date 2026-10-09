"""
Prompts package for Multi-Agent Financial Assistant.
Contains YAML-defined system prompts and templates.
"""
from app.core.prompt_manager import prompt_manager, PromptManager, PromptTemplateModel

__all__ = ["prompt_manager", "PromptManager", "PromptTemplateModel"]
