"""Almacenamiento de los XML de CFDI descargados del SAT.

En producción se usa Azure Blob Storage (barato, escalable, no infla Postgres).
En desarrollo local, si no hay AZURE_STORAGE_CONNECTION_STRING configurado,
se cae automáticamente a disco local bajo data/blob/ (misma convención que
ya usa app/sat_portal.py para las carpetas por cliente).
"""
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.core.config import settings

BASE_LOCAL_DIR = Path("data") / "blob"


def _blob_name(rfc: str, tipo: str, uuid: str) -> str:
    rfc = rfc.upper().strip()
    return f"{rfc}/{tipo}/{uuid}.xml"


class XmlStorage(ABC):
    @abstractmethod
    def save(self, rfc: str, tipo: str, uuid: str, content: bytes) -> str:
        """Guarda el XML y regresa una referencia (path/key) para leerlo después."""

    @abstractmethod
    def read(self, path: str) -> bytes:
        """Lee de vuelta el XML guardado con save()."""


class LocalXmlStorage(XmlStorage):
    def save(self, rfc: str, tipo: str, uuid: str, content: bytes) -> str:
        name = _blob_name(rfc, tipo, uuid)
        full_path = BASE_LOCAL_DIR / name
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        return str(full_path)

    def read(self, path: str) -> bytes:
        return Path(path).read_bytes()


class AzureBlobXmlStorage(XmlStorage):
    def __init__(self, connection_string: str, container: str):
        # Import perezoso: azure-storage-blob solo hace falta si de verdad se usa Blob Storage.
        from azure.storage.blob import BlobServiceClient

        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._container_name = container
        container_client = self._client.get_container_client(container)
        if not container_client.exists():
            container_client.create_container()

    def save(self, rfc: str, tipo: str, uuid: str, content: bytes) -> str:
        name = _blob_name(rfc, tipo, uuid)
        blob_client = self._client.get_blob_client(container=self._container_name, blob=name)
        blob_client.upload_blob(content, overwrite=True, content_type="application/xml")
        return name

    def read(self, path: str) -> bytes:
        blob_client = self._client.get_blob_client(container=self._container_name, blob=path)
        return blob_client.download_blob().readall()


@lru_cache
def get_xml_storage() -> XmlStorage:
    if settings.AZURE_STORAGE_CONNECTION_STRING:
        return AzureBlobXmlStorage(
            connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
            container=settings.AZURE_STORAGE_CONTAINER,
        )
    return LocalXmlStorage()
