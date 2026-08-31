import asyncio
import logging
from datetime import datetime

from asyncua import Client, Node, ua

from config.settings import (
    PUBLISHING_INTERVAL_MS,
    SAMPLING_INTERVAL_MS,
    QUEUE_MAXSIZE,
    CICLO_INICIADO,
    CICLO_FINALIZADO,
    BUFFER_ROOT,
    CICLO_FALLO,
    RECETA_ACTUAL,
    RACK_ACTUAL,
    BUFFER_LEVELS,
    ID_EQUIPO,
    buffer_cache,
    ciclo_cache as global_ciclo_cache,
)
from opc.browser import find_objects_by_name, read_node_tree
from opc.handler import DataChangeHandler
from services.db import get_connection

_ciclo_abierto_id = None
_ciclo_abierto_fecha_inicio = None

def _get_cursor():
    """Obtiene cursor de BD con diccionario como retorno."""
    conn = get_connection()
    return conn, conn.cursor(dictionary=True)


def _cerrar_cursor(conn, cursor):
    """Cierra cursor y conexión."""
    try:
        cursor.close()
        conn.close()
    except Exception:
        pass


def _calcular_estado_nivel(seleccionado: bool, finalizado: bool, cancelado: bool) -> str:
    """Calcula el estado_nivel basado en los atributos del nivel.
    
    Lógica:
    - Si seleccionado es false → "NO SELECCIONADO"
    - Si finalizó pero tuvo cancelaciones → "FINALIZADO CON CANCELACIONES"
    - Si finalizó sin cancelaciones → "FINALIZADO"
    - Si no finalizó, sin cancelaciones pero está seleccionado → "NO PROCESADO"
    - Si no finalizó, está seleccionado y tiene cancelaciones → "CANCELADO"
    
    Args:
        seleccionado: Si el nivel fue seleccionado
        finalizado: Si el nivel finalizó
        cancelado: Si el nivel tuvo cancelaciones
    
    Returns:
        Estado del nivel como string
    """
    if not seleccionado:
        return "NO SELECCIONADO"
    
    if finalizado:
        if cancelado:
            return "FINALIZADO CON CANCELACIONES"
        else:
            return "FINALIZADO"
    else:  # No finalizó
        if cancelado:
            return "CANCELADO"
        else:
            return "NO PROCESADO"


def _calcular_estado_ciclo(niveles_datos: list[dict]) -> str:
    """Calcula el estado_ciclo basado en todos los niveles procesados.
    
    Lógica:
    - Si todos los niveles con seleccionado=true tienen finalizado=true
      (sin importar cancelaciones) → "FINALIZADO"
    - Si no todos los niveles seleccionados finalizaron → "CANCELADO"
    
    Args:
        niveles_datos: Lista de dicts con datos de cada nivel
                       Cada dict debe contener: seleccionado, finalizado
    
    Returns:
        Estado del ciclo como string ("FINALIZADO" o "CANCELADO")
    """
    # Filtrar solo niveles seleccionados
    niveles_seleccionados = [
        nivel for nivel in niveles_datos 
        if nivel.get("seleccionado", False)
    ]
    
    # Si no hay niveles seleccionados, considerar ciclo finalizado
    if not niveles_seleccionados:
        return "FINALIZADO"
    
    # Verificar que todos los niveles seleccionados finalizaron
    todos_finalizados = all(
        nivel.get("finalizado", False) for nivel in niveles_seleccionados
    )
    
    if todos_finalizados:
        return "FINALIZADO"
    else:
        return "CANCELADO"


def _guardar_fallo(tipo_fallo: str) -> None:
    """Guarda un registro de fallo en la tabla falloCapturaCiclos.
    
    Args:
        tipo_fallo: Descripción del tipo de fallo (ej: "Fallo reportado por PLC", "Fallo buffer perdido/no matcheado")
    """
    conn, cursor = _get_cursor()
    try:
        cursor.execute(
            """INSERT INTO falloCapturaCiclos (fecha, tipo)
               VALUES (%s, %s)""",
            (datetime.now(), tipo_fallo),
        )
        conn.commit()
        logging.info("Fallo registrado: %s", tipo_fallo)
    except Exception as e:
        logging.error("Error guardando fallo en BD: %s", e)
        conn.rollback()
    finally:
        _cerrar_cursor(conn, cursor)


async def run_cycle_monitor(
    client: Client,
    obj_map: dict[str, Node],
):
    """Monitorea inicioCiclo y finCiclo.
    
    Cuando detecta flanco ascendente:
    - inicioCiclo: crea un nuevo ciclo en BD
    - finCiclo: cierra el ciclo actual con fecha_fin
    """
    global _ciclo_abierto_id, _ciclo_abierto_fecha_inicio
    
    queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    node_labels = {}
    node_to_queues = {}
    nodes_list = []

    # Resolver nodos de ciclos
    inicio_node = obj_map.get(CICLO_INICIADO)
    fin_node = obj_map.get(CICLO_FINALIZADO)
    receta_node = obj_map.get(RECETA_ACTUAL)
    rack_node = obj_map.get(RACK_ACTUAL)

    if not inicio_node or not fin_node:
        logging.error("Nodos de ciclo no encontrados")
        return

    if not receta_node or not rack_node:
        logging.error("Nodos de receta/rack no encontrados")
        return

    # Mapear nodos para la suscripción
    for name, node in [(CICLO_INICIADO, inicio_node), (CICLO_FINALIZADO, fin_node)]:
        nid = node.nodeid.to_string()
        node_labels[nid] = name
        node_to_queues[nid] = [queue]
        nodes_list.append(node)

    prev_values = {}

    # Crear suscripción
    handler = DataChangeHandler(node_labels, node_to_queues)
    subscription = await client.create_subscription(PUBLISHING_INTERVAL_MS, handler)
    await subscription.subscribe_data_change(
        nodes_list, queuesize=1, sampling_interval=SAMPLING_INTERVAL_MS
    )
    logging.info("Monitor de ciclos activo")

    try:
        while True:
            item = await queue.get()
            try:
                tag = item["tag"]
                val = bool(item["value"])
                prev = prev_values.get(tag, False)
                prev_values[tag] = val

                # Detectar flanco ascendente (false → true)
                if val and not prev:
                    if tag == CICLO_INICIADO:
                        # ── Iniciar ciclo
                        try:
                            id_receta = await receta_node.read_value()
                            id_rack = await rack_node.read_value()
                        except Exception as e:
                            logging.warning("Error leyendo receta/rack: %s", e)
                            id_receta = None
                            id_rack = None

                        # Si hay ciclo anterior abierto, cerrarlo
                        if _ciclo_abierto_id:
                            logging.warning(
                                "Ciclo anterior abierto (id=%s). Cerrando sin marcar como inactivo.",
                                _ciclo_abierto_id,
                            )
                            conn, cursor = _get_cursor()
                            try:
                                ahora = datetime.now()
                                
                                # Obtener fecha_inicio para calcular tiempo_total
                                cursor.execute(
                                    "SELECT fecha_inicio FROM ciclos WHERE id_ciclo = %s",
                                    (_ciclo_abierto_id,)
                                )
                                result = cursor.fetchone()
                                fecha_inicio = result.get("fecha_inicio") if result else None
                                
                                # Calcular tiempo_total en segundos
                                tiempo_total = None
                                if fecha_inicio:
                                    if isinstance(fecha_inicio, str):
                                        fecha_inicio = datetime.fromisoformat(fecha_inicio)
                                    tiempo_total = int((ahora - fecha_inicio).total_seconds())
                                
                                # Actualizar con fecha_fin y tiempo_total
                                cursor.execute(
                                    "UPDATE ciclos SET fecha_fin = %s, tiempo_total = %s WHERE id_ciclo = %s",
                                    (ahora, tiempo_total, _ciclo_abierto_id),
                                )
                                conn.commit()
                                logging.info(
                                    "Ciclo anterior cerrado forzadamente: ID=%s, tiempo_total=%s segundos",
                                    _ciclo_abierto_id, tiempo_total
                                )
                            except Exception as e:
                                logging.error("Error cerrando ciclo anterior: %s", e)
                                conn.rollback()
                            finally:
                                _cerrar_cursor(conn, cursor)

                        # Crear nuevo ciclo
                        conn, cursor = _get_cursor()
                        try:
                            cursor.execute(
                                """INSERT INTO ciclos 
                                   (fecha_inicio, id_receta, id_rack, id_equipo, activo)
                                   VALUES (%s, %s, %s, %s, true)""",
                                (datetime.now(), id_receta, id_rack, ID_EQUIPO),
                            )
                            conn.commit()
                            _ciclo_abierto_id = cursor.lastrowid
                            _ciclo_abierto_fecha_inicio = datetime.now()
                            logging.info(
                                "Ciclo iniciado: ID=%s, Receta=%s, Rack=%s",
                                _ciclo_abierto_id, id_receta, id_rack,
                            )
                        except Exception as e:
                            logging.error("Error creando ciclo: %s", e)
                            conn.rollback()
                        finally:
                            _cerrar_cursor(conn, cursor)

                    elif tag == CICLO_FINALIZADO:
                        # ── Finalizar ciclo
                        ciclo_id_a_cerrar = _ciclo_abierto_id
                        
                        # Si no hay ciclo en memoria, buscar en BD
                        if not ciclo_id_a_cerrar:
                            conn, cursor = _get_cursor()
                            try:
                                cursor.execute(
                                    """SELECT id_ciclo FROM ciclos 
                                       WHERE activo = true 
                                       AND fecha_fin IS NULL 
                                       ORDER BY id_ciclo DESC 
                                       LIMIT 1"""
                                )
                                result = cursor.fetchone()
                                if result:
                                    ciclo_id_a_cerrar = result.get("id_ciclo")
                                    logging.info("Ciclo encontrado en BD para cerrar: ID=%s", ciclo_id_a_cerrar)
                            except Exception as e:
                                logging.error("Error buscando ciclo activo en BD: %s", e)
                            finally:
                                _cerrar_cursor(conn, cursor)
                        
                        if ciclo_id_a_cerrar:
                            conn, cursor = _get_cursor()
                            try:
                                ahora = datetime.now()
                                
                                # Obtener fecha_inicio para calcular tiempo_total
                                cursor.execute(
                                    "SELECT fecha_inicio FROM ciclos WHERE id_ciclo = %s",
                                    (ciclo_id_a_cerrar,)
                                )
                                result = cursor.fetchone()
                                fecha_inicio = result.get("fecha_inicio") if result else None
                                
                                # Calcular tiempo_total en segundos
                                tiempo_total = None
                                if fecha_inicio:
                                    if isinstance(fecha_inicio, str):
                                        fecha_inicio = datetime.fromisoformat(fecha_inicio)
                                    tiempo_total = int((ahora - fecha_inicio).total_seconds())
                                
                                # Actualizar con fecha_fin y tiempo_total
                                cursor.execute(
                                    """UPDATE ciclos 
                                       SET fecha_fin = %s, tiempo_total = %s
                                       WHERE id_ciclo = %s""",
                                    (ahora, tiempo_total, ciclo_id_a_cerrar),
                                )
                                conn.commit()
                                logging.info(
                                    "Ciclo finalizado: ID=%s, tiempo_total=%s segundos",
                                    ciclo_id_a_cerrar, tiempo_total
                                )
                                
                                # Limpiar variable global solo si era el ciclo en memoria
                                if _ciclo_abierto_id == ciclo_id_a_cerrar:
                                    _ciclo_abierto_id = None
                                    _ciclo_abierto_fecha_inicio = None
                            except Exception as e:
                                logging.error("Error finalizando ciclo: %s", e)
                                conn.rollback()
                            finally:
                                _cerrar_cursor(conn, cursor)
                        else:
                            logging.warning("Fin de ciclo detectado pero no hay ciclo activo (ni en memoria ni en BD)")

            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Error en cycle monitor")
            finally:
                queue.task_done()

    finally:
        try:
            await subscription.delete()
        except Exception:
            pass


async def run_buffer_monitor(
    client: Client,
    obj_map: dict[str, Node],
):
    """Monitorea buscarBuffer dentro del buffer y procesa el buffer cuando cambia a 1.
    
    Cuando detecta flanco ascendente:
    1. Lee el árbol completo del buffer
    2. Extrae datos de Nivel1-Nivel13
    3. Guarda cada nivel en BD (tabla nivelesCiclos)
    4. Escribe buscarBuffer = 0
    """
    global _ciclo_abierto_id
    
    queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    node_labels = {}
    node_to_queues = {}

    # Resolver nodo raíz del buffer
    buffer_node = obj_map.get(BUFFER_ROOT)

    if not buffer_node:
        logging.error("Nodo buffer raíz no encontrado")
        return

    # ── BUSCAR buscarBuffer dentro del árbol del buffer ──
    logging.info("Buscando nodo buscarBuffer dentro de %s…", BUFFER_ROOT)
    
    inner_nodes = await find_objects_by_name(
        buffer_node,
        {"buscarBuffer"},  # Buscar específicamente este nodo
        max_depth=10,
    )
    
    buscar_node = inner_nodes.get("buscarBuffer")
    
    if not buscar_node:
        logging.error("Nodo buscarBuffer no encontrado dentro de %s", BUFFER_ROOT)
        return

    # Mapear nodo de búsqueda
    nid = buscar_node.nodeid.to_string()
    node_labels[nid] = "buscarBuffer"
    node_to_queues[nid] = [queue]

    prev_buscar_value = False

    # Crear suscripción (solo del nodo buscarBuffer)
    handler = DataChangeHandler(node_labels, node_to_queues)
    subscription = await client.create_subscription(PUBLISHING_INTERVAL_MS, handler)
    await subscription.subscribe_data_change(
        [buscar_node], queuesize=1, sampling_interval=SAMPLING_INTERVAL_MS
    )
    logging.info("Monitor de buffer activo")

    try:
        while True:
            item = await queue.get()
            try:
                val = bool(item.get("value"))

                # Detectar flanco ascendente (false → true)
                if val and not prev_buscar_value:
                    logging.info("Flanco buscarBuffer = true → leyendo buffer…")

                    try:
                        # Leer árbol completo del buffer
                        buffer_data = await read_node_tree(buffer_node)
                        logging.debug("Buffer leído. Keys: %s", list(buffer_data.keys()))

                        # Obtener recetaBuffer y rackBuffer del árbol leído
                        receta_buffer = buffer_data.get("recetaBuffer")
                        rack_buffer = buffer_data.get("rackBuffer")

                        # Si no hay ciclo abierto en memoria, buscar en BD
                        ciclo_id_a_usar = _ciclo_abierto_id
                        if not ciclo_id_a_usar:
                            # Buscar ciclo activo con coincidencia de receta y rack
                            if receta_buffer is not None and rack_buffer is not None:
                                conn, cursor = _get_cursor()
                                try:
                                    cursor.execute(
                                        """SELECT id_ciclo FROM ciclos 
                                           WHERE activo = true 
                                           AND id_receta = %s 
                                           AND id_rack = %s 
                                           ORDER BY id_ciclo DESC 
                                           LIMIT 1""",
                                        (int(receta_buffer), int(rack_buffer))
                                    )
                                    result = cursor.fetchone()
                                    if result:
                                        ciclo_id_a_usar = result.get("id_ciclo")
                                        logging.info(
                                            "Ciclo encontrado en BD: ID=%s (receta=%s, rack=%s)",
                                            ciclo_id_a_usar, receta_buffer, rack_buffer
                                        )
                                    else:
                                        logging.warning(
                                            "No hay ciclo activo que coincida con receta=%s, rack=%s",
                                            receta_buffer, rack_buffer
                                        )
                                except Exception as e:
                                    logging.error("Error buscando ciclo en BD: %s", e)
                                finally:
                                    _cerrar_cursor(conn, cursor)
                            else:
                                logging.warning("recetaBuffer o rackBuffer no disponibles en el buffer")

                        # Si ahora tenemos ciclo_id, procesar niveles
                        if ciclo_id_a_usar:
                            # Procesar niveles
                            conn, cursor = _get_cursor()
                            try:
                                tiempo_util_total = 0  # Acumular tiempo de todos los niveles
                                niveles_procesados = []  # Guardar datos de niveles para calcular estado_ciclo
                                
                                for nivel_name in BUFFER_LEVELS:
                                    if nivel_name in buffer_data:
                                        nivel_data = buffer_data[nivel_name]
                                        nivel_num = int(nivel_name.replace("Nivel", ""))

                                        # Extraer valores (pueden ser dict o valores directos)
                                        if isinstance(nivel_data, dict):
                                            cancelado = nivel_data.get("cancelado", False)
                                            finalizado = nivel_data.get("finalizado", False)
                                            seleccionado = nivel_data.get("seleccionado", False)
                                            tiempo_nivel = nivel_data.get("tiempoNivel", 0)
                                        else:
                                            # Si viene como valor directo, ignorar
                                            logging.warning(
                                                "Nivel %s no es un objeto dict: %s",
                                                nivel_name, nivel_data,
                                            )
                                            continue

                                        # Calcular estado_nivel según la lógica de negocio
                                        estado_nivel = _calcular_estado_nivel(
                                            seleccionado=seleccionado,
                                            finalizado=finalizado,
                                            cancelado=cancelado
                                        )
                                        
                                        # Acumular tiempo_nivel para calcular tiempo_util
                                        tiempo_util_total += int(tiempo_nivel)
                                        
                                        # Guardar datos del nivel para calcular estado_ciclo más tarde
                                        niveles_procesados.append({
                                            "seleccionado": seleccionado,
                                            "finalizado": finalizado,
                                        })

                                        # Insertar en BD
                                        cursor.execute(
                                            """INSERT INTO nivelesCiclos 
                                               (id_ciclo, nivel, finalizado, tiempo_nivel, cancelado, seleccionado, estado_nivel)
                                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                                            (
                                                ciclo_id_a_usar,
                                                nivel_num,
                                                bool(finalizado),
                                                int(tiempo_nivel),
                                                bool(cancelado),
                                                bool(seleccionado),
                                                estado_nivel,
                                            ),
                                        )
                                        logging.debug(
                                            "Nivel %d insertado: finalizado=%s, tiempo=%s, cancelado=%s, seleccionado=%s, estado=%s",
                                            nivel_num, finalizado, tiempo_nivel, cancelado, seleccionado, estado_nivel,
                                        )

                                conn.commit()
                                logging.info(
                                    "Buffer procesado y guardado en BD para ciclo %s (tiempo_util=%s segundos)",
                                    ciclo_id_a_usar, tiempo_util_total
                                )
                                
                                # Actualizar tiempo_util en ciclos con la sumatoria de tiempoNivel
                                try:
                                    cursor.execute(
                                        "UPDATE ciclos SET tiempo_util = %s WHERE id_ciclo = %s",
                                        (tiempo_util_total, ciclo_id_a_usar),
                                    )
                                    conn.commit()
                                    logging.info(
                                        "tiempo_util actualizado para ciclo %s: %s segundos",
                                        ciclo_id_a_usar, tiempo_util_total
                                    )
                                except Exception as util_error:
                                    logging.error("Error actualizando tiempo_util: %s", util_error)
                                    conn.rollback()
                                
                                # Calcular y guardar estado_ciclo basado en todos los niveles procesados
                                try:
                                    estado_ciclo = _calcular_estado_ciclo(niveles_procesados)
                                    cursor.execute(
                                        "UPDATE ciclos SET estado_ciclo = %s WHERE id_ciclo = %s",
                                        (estado_ciclo, ciclo_id_a_usar),
                                    )
                                    conn.commit()
                                    logging.info(
                                        "estado_ciclo actualizado para ciclo %s: %s",
                                        ciclo_id_a_usar, estado_ciclo
                                    )
                                except Exception as estado_error:
                                    logging.error("Error actualizando estado_ciclo: %s", estado_error)
                                    conn.rollback()
                                
                                # Marcar ciclo como inactivo después de guardar buffer
                                try:
                                    cursor.execute(
                                        "UPDATE ciclos SET activo = false WHERE id_ciclo = %s",
                                        (ciclo_id_a_usar,)
                                    )
                                    conn.commit()
                                    logging.info("Ciclo %s marcado como inactivo", ciclo_id_a_usar)
                                except Exception as mark_error:
                                    logging.error("Error marcando ciclo como inactivo: %s", mark_error)
                                    conn.rollback()

                            except Exception as e:
                                logging.error("Error insertando niveles en BD: %s", e)
                                conn.rollback()
                            finally:
                                _cerrar_cursor(conn, cursor)
                        else:
                            logging.warning("No hay ciclo disponible (ni en memoria ni en BD), ignorando buffer")
                            # Guardar fallo de buffer no matcheado
                            _guardar_fallo("Fallo buffer perdido/no matcheado")

                    except Exception as e:
                        logging.error("Error leyendo buffer: %s", e, exc_info=True)

                    # Escribir false en buscarBuffer
                    try:
                        false_dv = ua.DataValue(ua.Variant(False, ua.VariantType.Boolean))
                        await buscar_node.write_attribute(ua.AttributeIds.Value, false_dv)
                        logging.info("buscarBuffer ← false")
                    except Exception as e:
                        logging.error("Error escribiendo false en buscarBuffer: %s", e)

                prev_buscar_value = val

            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Error en buffer monitor")
            finally:
                queue.task_done()

    finally:
        try:
            await subscription.delete()
        except Exception:
            pass


async def monitor_ciclos_activos():
    """
    Tarea en segundo plano que verifica cada 2 horas ciclos activos vencidos.
    
    Si un ciclo tiene activo=true y fecha_fin pasó hace 3+ horas, lo marca como inactivo.
    """
    logging.info("Monitor de ciclos activos iniciado - Intervalo: 2 horas")

    _desactivar_ciclos_vencidos()

    while True:
        try:
            await asyncio.sleep(7200)
            _desactivar_ciclos_vencidos()
        except asyncio.CancelledError:
            logging.info("Monitor de ciclos activos cancelado")
            break
        except Exception:
            logging.exception("Error en monitor de ciclos activos")
            await asyncio.sleep(5)


def _desactivar_ciclos_vencidos() -> None:
    """
    Consulta ciclos activos con fecha_fin pasada hace 3+ horas y los desactiva.
    """
    conn, cursor = _get_cursor()
    try:
        # Obtener últimos ciclos activos
        cursor.execute(
            """
            SELECT id_ciclo, fecha_fin, fecha_inicio
            FROM ciclos
            WHERE activo = true AND fecha_fin IS NOT NULL
            ORDER BY id_ciclo DESC
            LIMIT 100
            """,
        )
        ciclos_activos = cursor.fetchall()

        if not ciclos_activos:
            logging.debug("No hay ciclos activos para verificar")
            return

        ahora = datetime.now()
        limite_horas = 3
        ids_a_desactivar = []

        for ciclo in ciclos_activos:
            fecha_fin = ciclo.get("fecha_fin")

            if fecha_fin:
                # Asegurar que es datetime
                if isinstance(fecha_fin, str):
                    fecha_fin = datetime.fromisoformat(fecha_fin)

                tiempo_pasado = ahora - fecha_fin
                horas_pasadas = tiempo_pasado.total_seconds() / 3600

                if horas_pasadas >= limite_horas:
                    ids_a_desactivar.append(ciclo["id_ciclo"])
                    logging.warning(
                        "Ciclo %s vencido: pasaron %.1f horas desde fecha_fin",
                        ciclo["id_ciclo"], horas_pasadas,
                    )

        # Desactivar los que necesitan
        if ids_a_desactivar:
            placeholders = ",".join(["%s"] * len(ids_a_desactivar))
            cursor.execute(
                f"UPDATE ciclos SET activo = false WHERE id_ciclo IN ({placeholders})",
                ids_a_desactivar,
            )
            conn.commit()
            logging.info("Desactivados %d ciclos vencidos", len(ids_a_desactivar))
            
            # Guardar fallo por cada ciclo desactivado por vencimiento
            for ciclo_id in ids_a_desactivar:
                _guardar_fallo("Fallo cierre de ciclo sin datos de buffer")

    except Exception as e:
        logging.error("Error desactivando ciclos: %s", e)
        conn.rollback()
    finally:
        _cerrar_cursor(conn, cursor)


async def run_fallo_monitor(
    client: Client,
    obj_map: dict[str, Node],
):
    """Monitorea falloCiclos.
    
    Cuando detecta flanco ascendente (false → true):
    - Guarda un registro en falloCapturaCiclos con tipo "Fallo reportado por PLC"
    """
    queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    node_labels = {}
    node_to_queues = {}

    # Resolver nodo de fallo
    fallo_node = obj_map.get(CICLO_FALLO)

    if not fallo_node:
        logging.error("Nodo falloCiclos no encontrado")
        return

    # Mapear nodo de fallo
    nid = fallo_node.nodeid.to_string()
    node_labels[nid] = CICLO_FALLO
    node_to_queues[nid] = [queue]

    prev_fallo_value = False

    # Crear suscripción
    handler = DataChangeHandler(node_labels, node_to_queues)
    subscription = await client.create_subscription(PUBLISHING_INTERVAL_MS, handler)
    await subscription.subscribe_data_change(
        [fallo_node], queuesize=1, sampling_interval=SAMPLING_INTERVAL_MS
    )
    logging.info("Monitor de fallos activo")

    try:
        while True:
            item = await queue.get()
            try:
                val = bool(item.get("value"))

                # Detectar flanco ascendente (false → true)
                if val and not prev_fallo_value:
                    logging.warning("Flanco falloCiclos = true → registrando fallo")
                    _guardar_fallo("Fallo reportado por PLC")

                prev_fallo_value = val

            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Error en fallo monitor")
            finally:
                queue.task_done()

    finally:
        try:
            await subscription.delete()
        except Exception:
            pass
