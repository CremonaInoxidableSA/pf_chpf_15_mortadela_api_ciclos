from fastapi import APIRouter, HTTPException, Query

from services.ciclosService import listarCiclos, obtenerCiclo, listarCancelaciones
from services.opcClient import obtener_datos_ciclo

RouterCiclos = APIRouter(
    prefix="/ciclos",
    tags=["Ciclos"],
)


# ─────────────────────────────────────────────────────────────
#  GET  /ciclos/datos-opc-ciclos
# ─────────────────────────────────────────────────────────────

@RouterCiclos.get("/datos-opc-ciclos", summary="Datos de ciclos en tiempo real (OPC)")
async def get_datos_opc_ciclos():
    """
    Retorna los últimos valores de ciclos recibidos
    desde la API OPC vía WebSocket.
    """
    datos = await obtener_datos_ciclo()
    return {"ciclos": datos}


# ─────────────────────────────────────────────────────────────
#  GET  /ciclos/lista-ciclos
# ─────────────────────────────────────────────────────────────

@RouterCiclos.get("/lista-ciclos", summary="Listado de ciclos registrados")
def get_lista_ciclos():
    """
    Retorna todos los ciclos registrados en la base de datos,
    ordenados del más reciente al más antiguo.
    """
    try:
        return listarCiclos()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
#  GET  /ciclos/detalle-ciclo
# ─────────────────────────────────────────────────────────────

@RouterCiclos.get("/detalle-ciclo", summary="Detalle de un ciclo con sus niveles")
def get_detalle_ciclo(
    id_ciclo: int = Query(..., description="ID del ciclo"),
):
    """
    Retorna los datos del ciclo y todos sus niveles asociados.
    """
    try:
        return obtenerCiclo(id_ciclo)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
#  GET  /ciclos/diccionario-cancelaciones
# ─────────────────────────────────────────────────────────────

@RouterCiclos.get("/diccionario-cancelaciones", summary="Diccionario de cancelaciones")
def get_diccionario_cancelaciones():
    """
    Retorna el listado completo de motivos de cancelación.
    """
    try:
        return listarCancelaciones()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
