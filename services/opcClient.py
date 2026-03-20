import asyncio
import json
import logging
import os

import websockets
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("opc_client")

OPC_WS_URL3 = os.getenv("OPC_WS_URL3")

# Cache en memoria con los últimos valores recibidos del WS, indexados por tag
_datos_opc: dict[str, dict] = {}
_lock = asyncio.Lock()

# Último buffer de ciclo recibido
_buffer_ciclo: dict | None = None
_ciclo_change_event = asyncio.Event()


# ─────────────────────────────────────────────────────────────
#  API pública para consultar datos desde otros módulos
# ─────────────────────────────────────────────────────────────

async def obtener_datos_ciclo() -> dict[str, dict]:
    """Retorna solo los datos cuyo tag contiene 'ciclo' (case-insensitive)."""
    async with _lock:
        return {
            tag: payload
            for tag, payload in _datos_opc.items()
            if "ciclo" in tag.lower()
        }


async def obtener_todos_los_datos() -> dict[str, dict]:
    """Retorna todos los datos OPC recibidos."""
    async with _lock:
        return dict(_datos_opc)


def get_buffer_ciclo() -> dict | None:
    """Retorna el último buffer de ciclo recibido."""
    return _buffer_ciclo


def get_ciclo_change_event() -> asyncio.Event:
    """Retorna el evento que se dispara al recibir datos de ciclo."""
    return _ciclo_change_event


# ─────────────────────────────────────────────────────────────
#  Procesamiento interno
# ─────────────────────────────────────────────────────────────

async def _procesar_mensaje(data: dict):
    """
    Procesa un mensaje recibido del WebSocket OPC.
    Detecta el buffer de ciclo por la presencia de claves típicas
    del buffer OPC (Nivel*, inicioCiclo, recetaBuffer, torreBuffer, etc.).
    """
    global _buffer_ciclo

    async with _lock:
        _datos_opc["ultimo_buffer"] = data

    # Detectar si es un buffer de ciclo por sus claves conocidas
    claves = set(data.keys())
    claves_buffer = {"inicioCiclo", "finCiclo", "recetaBuffer1", "torreBuffer1"}
    tiene_niveles = any(k.startswith("Nivel") for k in claves)

    if claves & claves_buffer or tiene_niveles:
        _buffer_ciclo = data
        logger.info("Buffer ciclo recibido — claves: %s", list(claves))
        _ciclo_change_event.set()
    else:
        logger.info("Mensaje OPC no reconocido como buffer: %s", list(claves))


# ─────────────────────────────────────────────────────────────
#  WebSocket listener con reconexión automática
# ─────────────────────────────────────────────────────────────

async def opc_listener():
    """Cliente WebSocket que escucha la API OPC y almacena datos de ciclos."""
    while True:
        try:
            logger.info("Conectando a OPC WS: %s …", OPC_WS_URL3)
            async with websockets.connect(
                OPC_WS_URL3,
                additional_headers={"Origin": "http://localhost:8020"},
            ) as ws:
                logger.info("Conectado a API OPC en %s", OPC_WS_URL3)
                async for message in ws:
                    try:
                        data = json.loads(message)
                        await _procesar_mensaje(data)
                    except json.JSONDecodeError:
                        logger.warning("Mensaje no JSON recibido: %s", message[:120])
        except asyncio.CancelledError:
            logger.info("OPC listener cancelado")
            break
        except (
            websockets.ConnectionClosed,
            websockets.exceptions.InvalidStatus,
            ConnectionRefusedError,
            OSError,
        ) as e:
            logger.warning("Desconectado de OPC: %s — reintentando en 3 s…", e)
            await asyncio.sleep(3)
        except Exception:
            logger.exception("Error inesperado en OPC listener")
            await asyncio.sleep(3)
