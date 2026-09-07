"""
Configuration module for the Financial Assistant Defense-in-Depth System.
Uses pydantic-settings to manage environment variables with strong typing.
"""
import os
from typing import Literal, Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

class Settings(BaseSettings):
    # Application & Environment
    app_name: str = "Financial Assistant Multi-Agent API"
    environment: Literal["development", "production", "testing"] = "development"
    debug: bool = True

    # Security & Execution Mode:
    # - "local_mock": Deterministic local regex-based DLP and pattern-based Model Armor.
    # - "gcp_live": Real Cloud DLP API, Vertex AI Gemini, and Firestore.
    security_mode: Literal["local_mock", "gcp_live"] = "local_mock"

    # LLM Provider Configuration:
    # - "mock": Deterministic local rule-based simulation (0 cost, offline, for tests)
    # - "groq": Ultra-fast free tier on console.groq.com (Llama 3.3 70B)
    # - "nvidia": Free tier on build.nvidia.com (NIM Llama 3.3)
    # - "vertexai": Google Cloud Vertex AI Gemini (uses GCP Cloud Billing / credits)
    # - "google_genai": Google AI Studio free tier via GEMINI_API_KEY
    llm_provider: Literal["mock", "groq", "nvidia", "vertexai", "google_genai"] = "groq"
    llm_model: Optional[str] = "llama-3.3-70b-versatile"
    groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
    nvidia_api_key: Optional[str] = os.getenv("NVIDIA_API_KEY")
    gemini_api_key: Optional[str] = os.getenv("GEMINI_API_KEY")
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")

    # Google Cloud Platform configuration
    gcp_project_id: str = "demo-fintech-project"
    gcp_location: str = "us-central1"
    vertex_model_name: str = "gemini-1.5-flash-002"

    # LangGraph Checkpointer
    # - "memory": MemorySaver (zero external dependency, ideal for local tests)
    # - "firestore": Cloud Firestore (scalable, zero idle cost in GCP Free Tier)
    checkpointer_type: Literal["memory", "firestore"] = "memory"
    firestore_collection: str = "langgraph_checkpoints"

    # Secret key for the reversible token vault (never leaked to LLM)
    token_vault_secret: str = "super-secret-vault-key-change-in-prod-32bytes!"

    # Server binding
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
