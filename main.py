from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import APP_TITLE, APP_VERSION, APP_DESCRIPTION, CORS_ORIGINS
from core.lifespan import lifespan
from routes.rootRoutes import RouterRoot
from routes.ciclosHTTP import RouterCiclos
from routes.websocketRoutes import RouterWebSocket

# ─────────────────────────────────────────────────────────────
#  Creación de la aplicación
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=APP_DESCRIPTION,
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────────────
#  Middleware
# ─────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────
#  Registro de rutas
# ─────────────────────────────────────────────────────────────

app.include_router(RouterRoot)
app.include_router(RouterCiclos)
app.include_router(RouterWebSocket)