"""Configuración central de la aplicación (lee variables de entorno / .env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Base de datos
    DATABASE_URL: str = "postgresql+psycopg://konyeka:konyeka@localhost:5432/konyeka"

    # Autenticación
    JWT_SECRET_KEY: str = "cambia-esta-clave-en-produccion"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 8  # 8 horas

    # CORS: orígenes del frontend permitidos, separados por coma
    CORS_ORIGINS: str = "http://localhost:3000"

    # e.firma: llave simétrica (Fernet) para cifrar .key y contraseña en BD.
    # Generar con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    EFIRMA_ENCRYPTION_KEY: str = ""

    # Storage de XMLs descargados. Si AZURE_STORAGE_CONNECTION_STRING está vacío,
    # se usa disco local (carpeta data/) — útil para desarrollo.
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_CONTAINER: str = "cfdi-xmls"

    # Ambiente del webservice del SAT: PRODUCTION | TEST
    SAT_ENVIRONMENT: str = "PRODUCTION"

    # RPA contra el portal del SAT (Opinión de Cumplimiento y Constancia de
    # Situación Fiscal) -- el SAT no tiene webservice público para estos dos
    # trámites, a diferencia de la Descarga Masiva de CFDIs.
    # SAT_RPA_HEADLESS=false sirve para ver el navegador y ajustar selectores en desarrollo.
    SAT_RPA_HEADLESS: bool = True
    # Resolución del CAPTCHA del login del portal del SAT -- OPCIONAL: el
    # login confirmado es con e.firma, y entrando así el SAT ya no lo pide
    # (lo confirmó el contador en el portal real), así que en el caso normal
    # esto nunca se usa. Se deja configurado solo como red de seguridad por
    # si el SAT lo vuelve a mostrar. Dos proveedores intercambiables
    # (CAPTCHA_SOLVER_PROVIDER):
    #   "2captcha"  -> servicio de terceros compatible con la API de 2Captcha.
    #   "azure_llm" -> un modelo con visión desplegado en Azure AI Foundry
    #                  (o cualquier endpoint compatible con Chat Completions
    #                  de Azure OpenAI), al que se le manda la imagen del
    #                  CAPTCHA y se le pide el texto. Sirve para CAPTCHAs de
    #                  texto/números distorsionados simples.
    CAPTCHA_SOLVER_PROVIDER: str = "2captcha"

    CAPTCHA_SOLVER_API_KEY: str = ""
    CAPTCHA_SOLVER_BASE_URL: str = "https://2captcha.com"

    AZURE_LLM_ENDPOINT: str = ""
    AZURE_LLM_API_KEY: str = ""
    AZURE_LLM_DEPLOYMENT: str = ""
    AZURE_LLM_API_VERSION: str = "2024-06-01"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
