"""
Configuration settings for Financial Action MCP Server.
"""
import os
from pydantic import BaseModel, Field


class Settings(BaseModel):
    server_name: str = "FinancialActionServer"
    server_version: str = "1.0.0"
    host: str = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
    port: int = int(os.getenv("MCP_SERVER_PORT", "8001"))
    environment: str = os.getenv("ENVIRONMENT", "development")
    daily_transfer_limit: float = float(os.getenv("DAILY_TRANSFER_LIMIT", "1000000.0"))
    allowed_currencies: list[str] = Field(default_factory=lambda: ["ARS", "USD"])


settings = Settings()
