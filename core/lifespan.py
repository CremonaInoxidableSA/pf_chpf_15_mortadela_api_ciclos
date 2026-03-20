import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.opcClient import opc_listener
from services.ciclosWS import monitor_ciclos


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia las tareas en segundo plano y las cancela al cerrar la aplicación."""
    opc_task = asyncio.create_task(opc_listener())
    monitor_task = asyncio.create_task(monitor_ciclos())
    try:
        yield
    finally:
        for task in (opc_task, monitor_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
