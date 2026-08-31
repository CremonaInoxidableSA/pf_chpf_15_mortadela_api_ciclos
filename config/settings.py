import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logging.getLogger("asyncua").setLevel(logging.WARNING)

OPC_ENDPOINT = os.getenv("OPC_SERVER_URL", "opc.tcp://localhost:4840")
PUBLISHING_INTERVAL_MS = int(os.getenv("PUBLISHING_INTERVAL_MS", "100"))
SAMPLING_INTERVAL_MS = float(os.getenv("SAMPLING_INTERVAL_MS", "0.0"))
QUEUE_MAXSIZE = int(os.getenv("QUEUE_MAXSIZE", "500"))
BROWSE_DEPTH = int(os.getenv("BROWSE_DEPTH", "3"))

ID_EQUIPO = int(os.getenv("ID_EQUIPO", "1"))

#Solo estos 3 nodos serán suscritos para cambios
CICLO_INICIADO = "inicioCiclo"
CICLO_FINALIZADO = "finCiclo"
BUFFER_ROOT = "buffer"

#Estos se leen bajo demanda, no se suscriben
RECETA_ACTUAL = "recetaActual"
RACK_ACTUAL = "rackActual"
EQUIPO_ESTADO = "estadoEquipo"
CICLO_FALLO = "falloCiclos"

#Nodos a buscar al conectar
ALL_OBJECT_NAMES = {
    CICLO_INICIADO,
    CICLO_FINALIZADO,
    BUFFER_ROOT,
    RECETA_ACTUAL,
    RACK_ACTUAL,
    EQUIPO_ESTADO,
    CICLO_FALLO,
}

#Nodos a los que suscribirse para cambios
SUBSCRIBED_NODES = {
    CICLO_INICIADO,
    CICLO_FINALIZADO,
    BUFFER_ROOT,
    CICLO_FALLO,
}

BUFFER_LEVELS = [f"Nivel{i}" for i in range(1, 14)]

buffer_cache = {}
ciclo_cache = None
