import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from services.reportService import generar_reporte_excel

router = APIRouter(prefix="/reportes", tags=["Reportes"])

logger = logging.getLogger("uvicorn")


@router.get("/ciclos", summary="Descargar reporte de ciclos")
async def descargar_reporte_ciclos(
    fecha_inicio: str,
    fecha_fin: str
):
    """
    Genera y descarga un reporte Excel con resumen de ciclos.
    """
    try:
        try:
            datetime.strptime(fecha_inicio, "%Y-%m-%d")
            datetime.strptime(fecha_fin, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Las fechas deben estar en formato YYYY-MM-DD"
            )
        
        if fecha_inicio > fecha_fin:
            raise HTTPException(
                status_code=400,
                detail="fecha_inicio no puede ser mayor que fecha_fin"
            )
        
        logger.info(f"Generando reporte de ciclos: {fecha_inicio} a {fecha_fin}")
        
        excel_bytes = generar_reporte_excel(fecha_inicio, fecha_fin)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Reporte_Ciclos_{fecha_inicio}_{fecha_fin}_{timestamp}.xlsx"
        
        return StreamingResponse(
            iter([excel_bytes.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en descargar_reporte_ciclos: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error generando el reporte")
