import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from services.opcClient import get_buffer_ciclo, get_ciclo_change_event
from services.ciclosService import guardarCiclo, ciclo_ya_existe

logger = logging.getLogger("ciclos_ws")


# ─────────────────────────────────────────────────────────────
#  WebSocket Connection Manager
# ─────────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, message: dict):
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)


ws_ciclos = ConnectionManager()

# Último payload enviado (para enviar a nuevos suscriptores)
_ultimo_payload: dict | None = None


# ─────────────────────────────────────────────────────────────
#  Monitor de cambios: escucha el evento, persiste y broadcast
# ─────────────────────────────────────────────────────────────

async def monitor_ciclos():
    """
    Espera a que el opcClient reciba un buffer de ciclo,
    lo almacena en la BD y lo emite por /ws/ciclos.
    """
    global _ultimo_payload
    event = get_ciclo_change_event()

    while True:
        try:
            await event.wait()
            event.clear()

            buffer = get_buffer_ciclo()
            if buffer is None:
                continue

            fecha_inicio = buffer.get("inicioCiclo")
            logger.info("Buffer ciclo recibido — fecha_inicio=%s", fecha_inicio)

            # Verificar que no sea un ciclo duplicado
            if await asyncio.to_thread(ciclo_ya_existe, fecha_inicio):
                logger.info("Ciclo con fecha_inicio=%s ya existe, descartado", fecha_inicio)
                continue

            # Persistir en la base de datos
            resultado = await asyncio.to_thread(guardarCiclo, buffer)

            _ultimo_payload = resultado
            await ws_ciclos.broadcast_json(resultado)
            logger.info("Ciclo almacenado y enviado por WS")

        except asyncio.CancelledError:
            logger.info("Monitor de ciclos cancelado")
            break
        except Exception:
            logger.exception("Error en monitor de ciclos")
            await asyncio.sleep(1)


# ─────────────────────────────────────────────────────────────
#  Handler del WebSocket endpoint
# ─────────────────────────────────────────────────────────────

async def ws_ciclos_endpoint(websocket: WebSocket):
    """Handler para /ws/ciclos. Envía el estado actual al conectarse."""
    global _ultimo_payload
    await ws_ciclos.connect(websocket)
    try:
        # Enviar último estado conocido al nuevo suscriptor
        if _ultimo_payload is not None:
            await websocket.send_json(_ultimo_payload)
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        ws_ciclos.disconnect(websocket)
