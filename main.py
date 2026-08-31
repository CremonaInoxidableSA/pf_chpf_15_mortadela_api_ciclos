from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import APP_TITLE, APP_VERSION, APP_DESCRIPTION, CORS_ORIGINS
from core.lifespan import lifespan
from routes.reportes import router as Reportes

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=APP_DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(Reportes)