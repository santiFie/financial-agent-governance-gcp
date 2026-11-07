"""
Configuration module for the Financial Assistant Defense-in-Depth System.
Uses pydantic-settings to manage environment variables with strong typing.
"""
import os
from typing import Literal, Optional
from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

DEFAULT_TOKEN_VAULT_SECRET = "super-secret-vault-key-change-in-prod-32bytes!"
WEAK_TOKEN_VAULT_SECRETS = {DEFAULT_TOKEN_VAULT_SECRET, "development-secret-key-32-chars!"}


class Settings(BaseSettings):
    # Application & Environment
    app_name: str = "Financial Assistant Multi-Agent API"
    environment: Literal["development", "production", "testing"] = "development"
    debug: bool = True

    # Security & Execution Mode:
    # - "local_mock": Deterministic local regex-based DLP and pattern-based Model Armor.
    # - "gcp_live": Real Cloud DLP API, Vertex AI Gemini, and Firestore.
    security_mode: Literal["local_mock", "gcp_live"] = "gcp_live"

    # LLM Provider Configuration:
    # - "litellm": Multi-provider with dynamic fallbacks (groq -> nvidia -> vertexai -> openrouter)
    # - "groq": Ultra-fast free tier on console.groq.com (Llama 3.3 70B)
    # - "nvidia": Free tier on build.nvidia.com (NIM Llama 3.3)
    # - "vertexai": Google Cloud Vertex AI Gemini (uses GCP Cloud Billing / credits)
    # - "openrouter": OpenRouter API
    # - "google_genai": Google AI Studio free tier via GEMINI_API_KEY
    llm_provider: Literal["litellm", "groq", "nvidia", "vertexai", "openrouter", "google_genai"] = "litellm"
    llm_model: Optional[str] = "openai/gpt-oss-120b"
    # Orchestrator model override. When unset, the orchestrator uses the same provider/model as the executor agents.
    # Attack success depends on the model: see docs/modelos_probados.md.
    orchestrator_provider: Optional[Literal["litellm", "groq", "nvidia", "vertexai", "openrouter", "google_genai"]] = None
    orchestrator_model: Optional[str] = None
    groq_model: str = "openai/gpt-oss-120b"
    nvidia_model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    openrouter_model: str = "openai/gpt-oss-120b"
    groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
    nvidia_api_key: Optional[str] = os.getenv("NVIDIA_API_KEY")
    gemini_api_key: Optional[str] = os.getenv("GEMINI_API_KEY")
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    openrouter_api_key: Optional[str] = os.getenv("OPENROUTER_API_KEY")

    # Google Cloud Platform configuration
    gcp_project_id: str = "demo-fintech-project"
    gcp_location: str = "us-central1"
    vertex_model_name: str = "gemini-1.5-flash-002"

    # LangGraph Checkpointer
    # - "memory": MemorySaver (zero external dependency, ideal for local tests)
    # - "firestore": Cloud Firestore (scalable, zero idle cost in GCP Free Tier)
    checkpointer_type: Literal["memory", "firestore"] = "memory"
    firestore_collection: str = "langgraph_checkpoints"

    # Secret key for the reversible token vault (never leaked to LLM).
    # The default is for local development only: ENVIRONMENT=production refuses to start with it.
    token_vault_secret: str = DEFAULT_TOKEN_VAULT_SECRET

    # Observability & Tracing (LangSmith / Langfuse)
    langchain_tracing_v2: bool = False
    langchain_api_key: Optional[str] = os.getenv("LANGCHAIN_API_KEY")
    langchain_project: str = "financial-assistant-agents"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    # Server binding
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    # Document Ingestion Configuration
    use_gcs_ingestion: bool = Field(default=False, description="Enable document ingestion from GCS")
    gcs_bucket_name: Optional[str] = Field(default=None, description="GCS bucket name for document ingestion")
    local_document_path: str = Field(
        default="local_storage/comprobante.txt",
        description="Local file loaded when GCS ingestion is disabled or the GCS object does not exist (relative to backend/)",
    )

    # Google Cloud Model Armor Configuration
    use_gcp_model_armor: bool = Field(default=False, description="Enable GCP Model Armor")
    model_armor_template_id: Optional[str] = Field(default=None, description="GCP Model Armor template ID")

    # Policy Enforcement Point (PEP) & Policy Decision Point (PDP) Configuration
    pdp_url: str = Field(default="http://localhost:8181/v1/data/transaction/decision", description="Policy Decision Point URL")
    pdp_timeout_seconds: float = Field(default=3.0, description="PDP request timeout in seconds")
    pdp_fallback_mode: str = Field(default="deny", description="PDP fallback behavior on timeout/failure: 'deny' (FAILED) or 'hitl'")

    # Orchestrator Agent safety limits
    orchestrator_max_plan_steps: int = Field(default=8, description="Maximum number of steps an execution plan may contain")
    orchestrator_max_tool_iterations: int = Field(default=3, description="Maximum consecutive Orchestrator tool rounds before a plan update")

    @model_validator(mode="after")
    def _require_own_vault_secret_in_production(self):
        if self.environment == "production" and self.token_vault_secret in WEAK_TOKEN_VAULT_SECRETS:
            raise ValueError("TOKEN_VAULT_SECRET debe definirse con un valor propio cuando ENVIRONMENT=production.")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

# Automatically propagate LangSmith settings to environment if tracing is active
if settings.langchain_tracing_v2 and settings.langchain_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
    os.environ["LANGCHAIN_ENDPOINT"] = settings.langchain_endpoint
