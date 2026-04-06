from fastapi import APIRouter, HTTPException, Query

from services.ciclosService import listarCiclos, obtenerCiclo, listarCancelaciones

RouterCiclos = APIRouter(
    prefix="/ciclos",
    tags=["Ciclos"],
)


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