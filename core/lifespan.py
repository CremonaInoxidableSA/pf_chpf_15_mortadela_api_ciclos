import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.opcClient import opc_listener
from services.ciclosWS import monitor_ciclos
from services.monitorCiclos import monitor_ciclos_activos


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia las tareas en segundo plano y las cancela al cerrar la aplicación."""
    opc_task = asyncio.create_task(opc_listener())
    monitor_task = asyncio.create_task(monitor_ciclos())
    monitor_activos_task = asyncio.create_task(monitor_ciclos_activos())
    try:
        yield
    finally:
        for task in (opc_task, monitor_task, monitor_activos_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
