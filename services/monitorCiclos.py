from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from services.db import get_connection

logger = logging.getLogger("monitor_ciclos")

INTERVALO_SEGUNDOS = 180
LIMITE_HORAS_ACTIVO = 1
ULTIMOS_CICLOS = 100


async def monitor_ciclos_activos():
    """
    Tarea en segundo plano que cada 3 minutos consulta los últimos 100 ciclos.
    Si alguno tiene activo=true y su fecha_fin supera 2 horas de diferencia
    con la hora actual, lo marca como activo=false.
    """
    while True:
        try:
            _desactivar_ciclos_vencidos()
        except Exception:
            logger.exception("Error en monitor_ciclos_activos")
        await asyncio.sleep(INTERVALO_SEGUNDOS)


def _desactivar_ciclos_vencidos() -> None:
    """Consulta los últimos 100 ciclos y desactiva los que superan el límite de tiempo."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id_ciclo, fecha_fin
            FROM ciclos
            WHERE activo = true
            ORDER BY id_ciclo DESC
            LIMIT %s
            """,
            (ULTIMOS_CICLOS,)
        )
        ciclos_activos = cursor.fetchall()

        if not ciclos_activos:
            return

        ahora = datetime.now()
        limite = timedelta(minutes=LIMITE_HORAS_ACTIVO)
        ids_a_desactivar = []

        for ciclo in ciclos_activos:
            fecha_fin = ciclo.get("fecha_fin")
            if fecha_fin and isinstance(fecha_fin, datetime):
                if (ahora - fecha_fin) > limite:
                    ids_a_desactivar.append(ciclo["id_ciclo"])

        if ids_a_desactivar:
            formato = ",".join(["%s"] * len(ids_a_desactivar))
            cursor.execute(
                f"UPDATE ciclos SET activo = false WHERE id_ciclo IN ({formato})",
                ids_a_desactivar
            )
            conn.commit()
            logger.info(
                "Monitor: %d ciclo(s) desactivado(s): %s",
                len(ids_a_desactivar),
                ids_a_desactivar
            )

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
