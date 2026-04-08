import asyncio
import logging

from services.opcClient import get_buffer, get_buffer_change_event, buffer_igual_cache, _escribir_cache
from services.ciclosService import procesar_buffer_ciclo

logger = logging.getLogger("ciclos_ws")


# ─────────────────────────────────────────────────────────────
#  Monitor de cambios en buffers: procesa y limpia remotamente
# ─────────────────────────────────────────────────────────────

async def monitor_ciclos():
    """
    Escucha cambios en ambos buffers (buffer1 y buffer2).
    Cuando uno de ellos cambia, procesa el ciclo y solicita limpieza remota.
    """
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
            
            # Comparar con caché
            if buffer_igual_cache(buffer):
                logger.info("Buffer %s igual al caché, ignorando", buffer_name)
                continue
            
            logger.info("Buffer %s recibido, procesando ciclo…", buffer_name)
            
            # Procesar buffer en un thread para no bloquear
            resultado = await asyncio.to_thread(procesar_buffer_ciclo, buffer, buffer_name)
            
            if resultado:
                logger.info("Ciclo procesado desde %s", buffer_name)
                # Guardar buffer en caché
                _escribir_cache(buffer)
            
        except asyncio.CancelledError:
            logger.info("Monitor de ciclos cancelado")
            break
        except Exception:
            logger.exception("Error en monitor de ciclos")
            await asyncio.sleep(1)