from fastapi import APIRouter

from core.config import APP_TITLE, APP_VERSION

RouterRoot = APIRouter(tags=["Root"])


@RouterRoot.get("/", summary="Información general de la API")
def read_root():
    return {
        "api": APP_TITLE,
        "version": APP_VERSION,
        "docs": "/docs",
    }
