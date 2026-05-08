from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from fastapi import HTTPException
import logging

from services.db import get_connection

logger = logging.getLogger("ciclos_service")

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
#  Procesamiento de buffer desde WebSocket
# ─────────────────────────────────────────────────────────────

def _extraer_niveles_del_buffer(buffer: dict) -> dict:
    """
    Extrae Nivel1..Nivel13 del buffer y retorna dict con info de cada uno.
    Especifico para el nuevo formato del cliente OPC.
    
    Buffer esperado:
    {
        "recetaBuffer1": 1,
        "rackBuffer1": 0,
        "pausaBuffer1": 5,
        "Nivel1": {"cancelaciones": [...], "finalizado": bool, "tiempoNivel": int, "seleccionado": bool},
        ...
    }
    """
    niveles = {}
    for i in range(1, 14):
        key = f"Nivel{i}"
        if key in buffer and isinstance(buffer[key], dict):
            niveles[i] = buffer[key]
    return niveles


def _extraer_numero_equipo_del_buffer(buffer: dict) -> int | None:
    """Retorna el número de equipo del buffer o None si no está presente."""
    value = buffer.get("numeroEquipo")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Valor de numeroEquipo inválido en buffer: %r", value)
        return None


def _obtener_datos_receta(cursor, id_receta: int) -> dict:
    """Retorna los campos requeridos de la receta asociada al id_receta."""
    cursor.execute(
        "SELECT peso_producto, productos_fila, productos_columna FROM recetas WHERE id_receta = %s",
        (id_receta,)
    )
    receta = cursor.fetchone()
    return receta or {}


def _calcular_peso_procesado(niveles_dict: dict, peso_producto: float, productos_fila: int, productos_columna: int) -> float:
    """Calcula peso_procesado como niveles finalizados * peso_producto * (productos_fila * productos_columna)."""
    niveles_finalizados = sum(
        1 for nivel in niveles_dict.values()
        if isinstance(nivel, dict) and nivel.get("finalizado", False)
    )
    total_productos = productos_fila * productos_columna
    return niveles_finalizados * peso_producto * total_productos


def _calcular_estado(niveles_dict: dict) -> int:
    """
    Calcula el estado del ciclo basándose en el estado_nivel de los niveles seleccionados.

    Estado 1 (FINALIZADO):                    todos los niveles seleccionados son "FINALIZADO".
    Estado 2 (FINALIZADO CON CANCELACIONES):  todos son "FINALIZADO" o "FINALIZADO CON CANCELACIONES",
                                              pero al menos uno es "FINALIZADO CON CANCELACIONES".
    Estado 3 (CANCELADO):                     al menos un nivel seleccionado es "CANCELADO" o "NO PROCESADO".
    """
    estados = [
        _calcular_estado_nivel(n)
        for n in niveles_dict.values()
        if isinstance(n, dict) and n.get("seleccionado", False)
    ]

    if not estados:
        return "CANCELADO"

    if any(e in ("CANCELADO", "NO PROCESADO") for e in estados):
        return "CANCELADO"

    if any(e == "FINALIZADO CON CANCELACIONES" for e in estados):
        return "FINALIZADO CON CANCELACIONES"

    return "FINALIZADO"


def _calcular_estado_nivel(nivel_data: dict) -> str:
    """
    Calcula el estado de un nivel individual.

    NO SELECCIONADO:            seleccionado=false
    CANCELADO:                  seleccionado=true, finalizado=false, cancelaciones!=[]
    FINALIZADO CON CANCELACIONES: seleccionado=true, finalizado=true,  cancelaciones!=[]
    FINALIZADO:                 seleccionado=true, finalizado=true,  cancelaciones==[]
    NO PROCESADO:               seleccionado=true, finalizado=false, cancelaciones==[]
    """
    seleccionado = nivel_data.get("seleccionado", False)
    if not seleccionado:
        return "NO SELECCIONADO"

    finalizado = nivel_data.get("finalizado", False)
    hay_cancelaciones = bool(nivel_data.get("cancelaciones", []))

    if finalizado and hay_cancelaciones:
        return "FINALIZADO CON CANCELACIONES"
    if finalizado and not hay_cancelaciones:
        return "FINALIZADO"
    if not finalizado and hay_cancelaciones:
        return "CANCELADO"
    return "NO PROCESADO"


def procesar_buffer_ciclo(buffer: dict, buffer_name: str = "buffer1") -> dict:
    """
    Procesa un buffer recibido desde WebSocket.
    
    1. Busca un ciclo activo con mismo id_receta e id_rack
    2. Si existe: actualiza con datos del buffer
    3. Si no existe: crea uno nuevo
    4. Guarda los niveles
    
    Retorna {"ciclo": {...}, "niveles": [...]}
    """
    # Determinar qué campos usar según el buffer
    if buffer_name == "buffer2":
        receta_key = "recetaBuffer2"
        rack_key = "rackBuffer2"
    else:
        receta_key = "recetaBuffer1"
        rack_key = "rackBuffer1"
    
    id_receta = buffer.get(receta_key)
    id_rack = buffer.get(rack_key)
    
    if id_receta is None or id_rack is None:
        logger.error("Buffer %s inválido: falta %s o %s", buffer_name, receta_key, rack_key)
        return {}
    
    with _get_cursor() as (conn, cursor):
        receta = _obtener_datos_receta(cursor, id_receta)
        if not receta:
            logger.error("Receta %s no encontrada para buffer %s", id_receta, buffer_name)
            return {}

        peso_producto = receta.get("peso_producto", 0)
        productos_fila = receta.get("productos_fila", 0)
        productos_columna = receta.get("productos_columna", 0)

        # Extraer niveles del buffer
        niveles_dict = _extraer_niveles_del_buffer(buffer)
        
        # Calcular valores para la BD
        id_equipo = _extraer_numero_equipo_del_buffer(buffer)
        peso_procesado = _calcular_peso_procesado(
            niveles_dict,
            peso_producto,
            productos_fila,
            productos_columna,
        )
        estado = _calcular_estado(niveles_dict)
        # Buscar ciclo activo en la misma transacción
        cursor.execute(
            """
            SELECT * FROM ciclos 
            WHERE activo = true AND id_receta = %s AND id_rack = %s
            LIMIT 1
            """,
            (id_receta, id_rack)
        )
        ciclo_activo = cursor.fetchone()
        
        if ciclo_activo:
            # Actualizar ciclo existente
            id_ciclo = ciclo_activo["id_ciclo"]
            logger.info("Actualizando ciclo activo id=%s", id_ciclo)
            
            cursor.execute(
                """
                UPDATE ciclos 
                SET estado_ciclo = %s, id_equipo = %s, peso_procesado = %s, activo = false
                WHERE id_ciclo = %s
                """,
                (estado, id_equipo, peso_procesado, id_ciclo)
            )
        else:
            # Crear ciclo nuevo
            logger.info(
                "Creando ciclo nuevo para receta=%s, rack=%s",
                id_receta, id_rack
            )
            
            cursor.execute(
                """
                INSERT INTO ciclos 
                (fecha_inicio, fecha_fin, estado_ciclo, id_receta, 
                 id_rack, id_equipo, peso_procesado, activo)
                VALUES (NULL, NULL, %s, %s, %s, %s, %s, false)
                """,
                (estado, id_receta, id_rack, id_equipo, peso_procesado)
            )
            id_ciclo = cursor.lastrowid
        
        # Guardar niveles
        for num_nivel, nivel_data in sorted(niveles_dict.items()):
            if isinstance(nivel_data, dict):
                # Primero, eliminar niveles existentes para este ciclo
                cursor.execute(
                    "DELETE FROM nivelesciclos WHERE id_ciclo = %s AND nivel = %s",
                    (id_ciclo, num_nivel)
                )
                
                # Luego, insertar el nuevo
                cancelaciones = nivel_data.get("cancelaciones", [])
                cancelaciones_json = json.dumps(cancelaciones) if cancelaciones else None
                estado_nivel = _calcular_estado_nivel(nivel_data)
                
                cursor.execute(
                    """
                    INSERT INTO nivelesciclos 
                    (id_ciclo, nivel, finalizado, tiempo_nivel, cancelaciones, seleccionado, estado_nivel)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        id_ciclo,
                        num_nivel,
                        1 if nivel_data.get("finalizado", False) else 0,
                        nivel_data.get("tiempoNivel", 0),
                        cancelaciones_json,
                        1 if nivel_data.get("seleccionado", False) else 0,
                        estado_nivel
                    )
                )
        
        conn.commit()
        
        # Leer y retornar el ciclo actualizado
        cursor.execute("SELECT * FROM ciclos WHERE id_ciclo = %s", (id_ciclo,))
        ciclo = _serializar_fechas(cursor.fetchone())
        
        cursor.execute(
            "SELECT * FROM nivelesciclos WHERE id_ciclo = %s ORDER BY nivel",
            (id_ciclo,)
        )
        niveles = cursor.fetchall()
    
    return {"ciclo": ciclo, "niveles": niveles}


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
