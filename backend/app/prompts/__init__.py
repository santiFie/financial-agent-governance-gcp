"""
Prompts package for Multi-Agent Financial Assistant.
Contains YAML-defined system prompts and templates.
"""

from app.core.prompt_manager import PromptManager, PromptTemplateModel, prompt_manager

__all__ = ["prompt_manager", "PromptManager", "PromptTemplateModel"]
