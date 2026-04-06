import asyncio
import json
import logging
import os

import websockets
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("opc_client")

OPC_WS_URL3 = os.getenv("OPC_WS_URL3")  # ws://localhost:8015/ws/buffer1
OPC_WS_URL4 = os.getenv("OPC_WS_URL4")  # ws://localhost:8015/ws/buffer2

# Cache en memoria de buffers por origen
_buffers: dict[str, dict] = {}  # { "buffer1": {...}, "buffer2": {...} }
_lock = asyncio.Lock()

# Eventos de cambio para cada buffer
_buffer_change_events: dict[str, asyncio.Event] = {
    "buffer1": asyncio.Event(),
    "buffer2": asyncio.Event(),
}

# ─────────────────────────────────────────────────────────────
#  API pública para consultar datos desde otros módulos
# ─────────────────────────────────────────────────────────────

def get_buffer(buffer_name: str) -> dict | None:
    """Retorna el último buffer recibido de buffer1 o buffer2."""
    return _buffers.get(buffer_name)


def get_buffer_change_event(buffer_name: str) -> asyncio.Event:
    """Retorna el evento que se dispara al recibir un buffer."""
    return _buffer_change_events.get(buffer_name, asyncio.Event())


# ─────────────────────────────────────────────────────────────
#  Procesamiento interno
# ─────────────────────────────────────────────────────────────

async def _procesar_mensaje(buffer_name: str, data: dict):
    """
    Procesa un mensaje recibido de uno de los WebSockets.
    Almacena el buffer en cache y dispara el evento de cambio.
    """
    async with _lock:
        _buffers[buffer_name] = data
    
    logger.info("Buffer '%s' actualizado con claves: %s", buffer_name, list(data.keys()))
    _buffer_change_events[buffer_name].set()


# ─────────────────────────────────────────────────────────────
#  WebSocket listeners con reconexión automática
# ─────────────────────────────────────────────────────────────

async def _opc_listener_buffer(buffer_name: str, ws_url: str):
    """Cliente WebSocket que escucha un buffer específico."""
    while True:
        try:
            logger.info("Conectando a %s: %s …", buffer_name, ws_url)
            async with websockets.connect(
                ws_url,
                additional_headers={"Origin": "http://localhost:8020"},
            ) as ws:
                logger.info("Conectado a %s en %s", buffer_name, ws_url)
                async for message in ws:
                    try:
                        data = json.loads(message)
                        await _procesar_mensaje(buffer_name, data)
                    except json.JSONDecodeError:
                        logger.error("Error decodificando JSON en %s", buffer_name)
                    except Exception:
                        logger.exception("Error procesando mensaje de %s", buffer_name)

        except asyncio.CancelledError:
            logger.info("Listener de %s cancelado", buffer_name)
            break
        except Exception:
            logger.exception("Error en listener de %s, reintentando en 5s…", buffer_name)
            await asyncio.sleep(5)


async def opc_listener():
    """Lanza listeners para ambos buffers en paralelo."""
    tasks = [
        asyncio.create_task(_opc_listener_buffer("buffer1", OPC_WS_URL3)),
        asyncio.create_task(_opc_listener_buffer("buffer2", OPC_WS_URL4)),
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Listener OPC general cancelado")
        for task in tasks:
            task.cancel()

