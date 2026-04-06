import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from services.opcClient import get_buffer, get_buffer_change_event
from services.ciclosService import procesar_buffer_ciclo

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
#  Monitor de cambios en buffers: procesa y emite por WebSocket
# ─────────────────────────────────────────────────────────────

async def monitor_ciclos():
    """
    Escucha cambios en ambos buffers (buffer1 y buffer2).
    Cuando uno de ellos cambia, procesa el ciclo y lo emite por WebSocket.
    """
    global _ultimo_payload
    
    event_buffer1 = get_buffer_change_event("buffer1")
    event_buffer2 = get_buffer_change_event("buffer2")
    
    while True:
        try:
            # Esperar a que cambie uno de los dos buffers
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(event_buffer1.wait()),
                    asyncio.create_task(event_buffer2.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancelar la tarea que no se completó
            for task in pending:
                task.cancel()
            
            # Limpiar el evento que se completó y obtener el buffer
            if event_buffer1.is_set():
                event_buffer1.clear()
                buffer = get_buffer("buffer1")
                buffer_name = "buffer1"
            else:
                event_buffer2.clear()
                buffer = get_buffer("buffer2")
                buffer_name = "buffer2"
            
            if buffer is None:
                logger.warning("Buffer %s es None, saltando", buffer_name)
                continue
            
            logger.info("Buffer %s recibido, procesando ciclo…", buffer_name)
            
            # Procesar buffer en un thread para no bloquear
            resultado = await asyncio.to_thread(procesar_buffer_ciclo, buffer, buffer_name)
            
            if resultado:
                _ultimo_payload = resultado
                await ws_ciclos.broadcast_json(resultado)
                logger.info("Ciclo procesado y enviado por WS desde %s", buffer_name)
            
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
