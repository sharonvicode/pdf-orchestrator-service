"""Configuración centralizada del Orchestrator, leída de variables de entorno.

Son variables propuestas por el Orchestrator, no contratos acordados con el equipo.
Las URLs no tienen valor por defecto: dependen del despliegue y deben definirse.
"""

from typing import Literal

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    validator_url: HttpUrl
    extractor_url: HttpUrl
    persistence_url: HttpUrl
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    port: int = Field(default=8000, ge=1, le=65535)
