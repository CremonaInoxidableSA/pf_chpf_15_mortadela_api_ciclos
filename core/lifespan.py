import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from opc.client import run_client
from opc.buffer_monitor import monitor_ciclos_activos


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia las tareas en segundo plano y las cancela al cerrar la aplicación."""
    opc_task = asyncio.create_task(run_client())
    monitor_task = asyncio.create_task(monitor_ciclos_activos())
    try:
        yield
    finally:
        for task in (opc_task, monitor_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
