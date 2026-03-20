from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from fastapi import HTTPException

from services.db import get_connection

@contextmanager
def _get_cursor():
    """Context manager que abre conexión, crea cursor y los cierra al salir."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _row_or_404(row, detail: str):
    if row is None:
        raise HTTPException(status_code=404, detail=detail)
    return row


def _serializar_fechas(row: dict) -> dict:
    """Convierte campos datetime a string ISO para serialización JSON."""
    for key, val in row.items():
        if isinstance(val, datetime):
            row[key] = val.isoformat()
    return row


# ─────────────────────────────────────────────────────────────
#  Verificación de duplicados
# ─────────────────────────────────────────────────────────────

def ciclo_ya_existe(fecha_inicio: str | None) -> bool:
    """Retorna True si ya existe un ciclo con esa fecha_inicio."""
    if not fecha_inicio:
        return False
    with _get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT 1 FROM ciclos WHERE fecha_inicio = %s LIMIT 1",
            (fecha_inicio,),
        )
        return cursor.fetchone() is not None


# ─────────────────────────────────────────────────────────────
#  Guardar ciclo (INSERT) + niveles
# ─────────────────────────────────────────────────────────────

def _mapear_buffer(data: dict) -> dict:
    """
    Transforma el buffer OPC al formato esperado por la BD.

    Buffer OPC:
        inicioCiclo, finCiclo, recetaBuffer1, torreBuffer1,
        Nivel1 .. Nivel12  (cada Nivel es un dict con campos del nivel)

    Resultado:
        { fecha_inicio, fecha_fin, id_receta, id_torre, ..., niveles: [...] }
    """
    id_receta = data.get("recetaBuffer1")
    id_torre = data.get("torreBuffer1")

    mapped = {
        "fecha_inicio": data.get("inicioCiclo"),
        "fecha_fin":    data.get("finCiclo"),
        "estado":       data.get("estado"),
        "id_receta":    id_receta if id_receta else None,
        "id_torre":     id_torre if id_torre else None,
        "id_equipo":    data.get("id_equipo"),
        "tiempo_total": data.get("tiempo_total"),
        "tiempo_pausa": data.get("tiempo_pausa"),
        "tiempo_ciclo": data.get("tiempo_ciclo"),
    }

    niveles = []
    for i in range(1, 13):
        key = f"Nivel{i}"
        if key in data:
            val = data[key]
            if isinstance(val, dict):
                # cancelaciones es un array de ids; tomar el primero o None
                cancelaciones = val.get("cancelaciones", [])
                id_cancelacion = cancelaciones[0] if cancelaciones else None

                niveles.append({
                    "nivel":            i,
                    "finalizado":       1 if val.get("finalizado") else 0,
                    "tiempo_nivel":     val.get("tiempoNivel"),
                    "id_cancelaciones": id_cancelacion,
                })

    mapped["niveles"] = niveles
    return mapped


def guardarCiclo(data: dict) -> dict:
    """
    Recibe el buffer OPC crudo, lo mapea y lo almacena en la BD.
    Retorna el ciclo guardado con sus niveles.
    """
    # Mapear buffer OPC → formato BD
    mapped = _mapear_buffer(data)
    niveles = mapped.pop("niveles", [])

    with _get_cursor() as (_, cursor):

        cursor.execute(
            """
            INSERT INTO ciclos
                (fecha_inicio, fecha_fin, estado, id_receta,
                 id_torre, id_equipo, tiempo_total, tiempo_pausa, tiempo_ciclo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                mapped.get("fecha_inicio"),
                mapped.get("fecha_fin"),
                mapped.get("estado"),
                mapped.get("id_receta"),
                mapped.get("id_torre"),
                mapped.get("id_equipo"),
                mapped.get("tiempo_total"),
                mapped.get("tiempo_pausa"),
                mapped.get("tiempo_ciclo"),
            ),
        )
        id_ciclo = cursor.lastrowid

        # ── Guardar niveles ──
        for nivel_data in niveles:
            cursor.execute(
                """
                INSERT INTO nivelesciclos
                    (id_ciclo, nivel, finalizado, tiempo_nivel, id_cancelaciones)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    id_ciclo,
                    nivel_data.get("nivel"),
                    nivel_data.get("finalizado"),
                    nivel_data.get("tiempo_nivel"),
                    nivel_data.get("id_cancelaciones"),
                ),
            )

        # ── Leer registro guardado ──
        cursor.execute("SELECT * FROM ciclos WHERE id_ciclo = %s", (id_ciclo,))
        ciclo = _serializar_fechas(cursor.fetchone())

        cursor.execute(
            "SELECT * FROM nivelesciclos WHERE id_ciclo = %s ORDER BY nivel",
            (id_ciclo,),
        )
        niveles_guardados = cursor.fetchall()

    return {"ciclo": ciclo, "niveles": niveles_guardados}


# ─────────────────────────────────────────────────────────────
#  Consultas
# ─────────────────────────────────────────────────────────────

def listarCiclos() -> dict:
    """Retorna todos los ciclos ordenados por id descendente."""
    with _get_cursor() as (_, cursor):
        cursor.execute("SELECT * FROM ciclos ORDER BY id_ciclo DESC")
        rows = [_serializar_fechas(r) for r in cursor.fetchall()]
    return {"ListadoCiclos": rows}


def obtenerCiclo(id_ciclo: int) -> dict:
    """Retorna un ciclo y sus niveles asociados."""
    with _get_cursor() as (_, cursor):
        cursor.execute("SELECT * FROM ciclos WHERE id_ciclo = %s", (id_ciclo,))
        ciclo = cursor.fetchone()
        _row_or_404(ciclo, f"Ciclo {id_ciclo} no encontrado.")
        ciclo = _serializar_fechas(ciclo)

        cursor.execute(
            "SELECT * FROM nivelesciclos WHERE id_ciclo = %s ORDER BY nivel",
            (id_ciclo,),
        )
        niveles = cursor.fetchall()

    return {"ciclo": ciclo, "niveles": niveles}


def listarCancelaciones() -> dict:
    """Retorna el diccionario completo de cancelaciones."""
    with _get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT * FROM diccionariocancelaciones ORDER BY id_cancelaciones"
        )
        rows = cursor.fetchall()
    return {"ListadoCancelaciones": rows}
