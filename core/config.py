import os

from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────
#  Metadatos de la aplicación
# ─────────────────────────────────────────────────────────────

APP_TITLE = "API Mortadela CICLOS"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = "API para captura y registro de ciclos de producción."

# ─────────────────────────────────────────────────────────────
#  CORS — Orígenes permitidos
# ─────────────────────────────────────────────────────────────

CORS_ORIGINS = [
    f"http://{os.getenv('FRONTEND_IP')}:3000",
    "http://localhost:3000",
    "http://192.168.20.150:3000",
    "http://127.0.0.1:3000",
]
