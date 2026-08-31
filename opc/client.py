import asyncio
import logging

from asyncua import Client, Node

from config.settings import (
    OPC_ENDPOINT,
    PUBLISHING_INTERVAL_MS,
    SAMPLING_INTERVAL_MS,
    QUEUE_MAXSIZE,
    BROWSE_DEPTH,
    ALL_OBJECT_NAMES,
    SUBSCRIBED_NODES,
)
from opc.browser import find_objects_by_name
from opc.handler import DataChangeHandler
from opc.buffer_monitor import run_buffer_monitor, run_cycle_monitor, run_fallo_monitor


async def queue_worker(queue: asyncio.Queue, cache: dict):
    """Consume payloads de la cola y los guarda en cache."""
    while True:
        item = await queue.get()
        try:
            logging.info("Cambio | %s | valor=%s", item["tag"], item["value"])
            cache[item["tag"]] = item
        finally:
            queue.task_done()


async def run_client():
    """Cliente OPC-UA con reconexión automática.
    Solo se suscribe a: inicioCiclo, finCiclo, buffer
    Otros valores se leen bajo demanda."""
    while True:
        worker_tasks: list[asyncio.Task] = []
        subscription = None
        try:
            logging.info("Conectando a %s …", OPC_ENDPOINT)

            async with Client(url=OPC_ENDPOINT, timeout=4) as client:
                # ── Buscar objetos declarados ──────
                obj_map = await find_objects_by_name(
                    client.nodes.objects, ALL_OBJECT_NAMES, BROWSE_DEPTH,
                )
                missing = ALL_OBJECT_NAMES - obj_map.keys()
                if missing:
                    logging.warning("Objetos NO encontrados: %s", missing)
                if not obj_map:
                    raise RuntimeError("Ningún objeto encontrado")

                # ── Solo suscribirse a nodos específicos ──────
                nodes_to_subscribe: list[Node] = []
                all_labels: dict[str, str] = {}
                node_to_queues: dict[str, list[asyncio.Queue]] = {}
                main_queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

                for obj_name in SUBSCRIBED_NODES:
                    if obj_name in obj_map:
                        n = obj_map[obj_name]
                        nid = n.nodeid.to_string()
                        nodes_to_subscribe.append(n)
                        all_labels[nid] = obj_name
                        node_to_queues[nid] = [main_queue]

                if not nodes_to_subscribe:
                    raise RuntimeError("No se encontraron nodos para suscribirse")
                logging.info("Suscrito a %d nodos: inicioCiclo, finCiclo, buffer", len(nodes_to_subscribe))

                # ── Worker para la cola principal ─────────────────────────────
                cache = {}
                worker_task = asyncio.create_task(queue_worker(main_queue, cache))
                worker_tasks.append(worker_task)

                # ── Monitores especializados ──────────────────────────────
                # Monitor de ciclos (inicioCiclo, finCiclo)
                cycle_task = asyncio.create_task(
                    run_cycle_monitor(client, obj_map)
                )
                worker_tasks.append(cycle_task)
                logging.info("Monitor de ciclos iniciado")

                # Monitor de buffers (buscarBuffer)
                buffer_task = asyncio.create_task(
                    run_buffer_monitor(client, obj_map)
                )
                worker_tasks.append(buffer_task)
                logging.info("Monitor de buffers iniciado")

                # Monitor de fallos (falloCiclos)
                fallo_task = asyncio.create_task(
                    run_fallo_monitor(client, obj_map)
                )
                worker_tasks.append(fallo_task)
                logging.info("Monitor de fallos iniciado")

                # ── Suscripción solo a nodos especificados ─────────────────────────────
                handler = DataChangeHandler(all_labels, node_to_queues)
                subscription = await client.create_subscription(
                    PUBLISHING_INTERVAL_MS, handler
                )
                await subscription.subscribe_data_change(
                    nodes_to_subscribe,
                    queuesize=1,
                    sampling_interval=SAMPLING_INTERVAL_MS,
                )
                logging.info("Suscripción activa a nodos: %s", ", ".join(SUBSCRIBED_NODES))

                # Esperar a que se cancele
                await asyncio.gather(*worker_tasks)

        except asyncio.CancelledError:
            logging.info("Cliente OPC cancelado")
            break
        except Exception as e:
            logging.error("Error en cliente OPC: %s. Reconectando en 5s…", e)
            await asyncio.sleep(5)
        finally:
            if subscription:
                try:
                    await subscription.delete()
                except Exception:
                    pass
