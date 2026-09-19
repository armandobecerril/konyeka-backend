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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
