"""RF-03 | Inyección de contexto: catálogo, precios y reglas de negocio.

Esto es lo que el administrador llena en el Dashboard y que luego se usa
para armar el prompt dinámico que ve la IA (ver services/ai_service.py).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.core.deps import obtener_negocio_propio
from app.models.negocio import Negocio
from app.models.contexto_ia import ContextoIA
from app.models.servicio import Servicio
from app.schemas.contexto import (
    ContextoIAUpdate,
    ContextoIAResponse,
    ServicioCreate,
    ServicioUpdate,
    ServicioResponse,
    MODELOS_GEMINI,
    MODELOS_OPENAI,
)

router = APIRouter(prefix="/negocio/{id_negocio}/contexto", tags=["Contexto IA"])


def _obtener_o_crear_contexto(db: Session, negocio: Negocio) -> ContextoIA:
    """El contexto se crea recién cuando el administrador lo llena por
    primera vez, así no queda una fila vacía por cada negocio nuevo."""
    contexto = (
        db.query(ContextoIA)
        .options(joinedload(ContextoIA.negocio))
        .filter(ContextoIA.id_negocio == negocio.id_negocio)
        .first()
    )
    if contexto is None:
        contexto = ContextoIA(id_negocio=negocio.id_negocio)
        db.add(contexto)
        db.commit()
        db.refresh(contexto)
    return contexto


def _con_servicios(db: Session, contexto: ContextoIA) -> ContextoIAResponse:
    servicios = db.query(Servicio).filter(Servicio.id_negocio == contexto.id_negocio).all()
    return ContextoIAResponse(
        id_contexto=contexto.id_contexto,
        id_negocio=contexto.id_negocio,
        reglas_negocio=contexto.reglas_negocio,
        instrucciones=contexto.instrucciones,
        consultar_catalogo=contexto.consultar_catalogo,
        agendar_automatico=contexto.agendar_automatico,
        ai_provider=contexto.ai_provider,
        ai_model=contexto.ai_model,
        fecha_actualizacion=contexto.fecha_actualizacion,
        servicios=servicios,
    )


@router.get("", response_model=ContextoIAResponse)
def obtener_contexto(db: Session = Depends(get_db), negocio: Negocio = Depends(obtener_negocio_propio)):
    contexto = _obtener_o_crear_contexto(db, negocio)
    return _con_servicios(db, contexto)


@router.put("", response_model=ContextoIAResponse)
def actualizar_contexto(
    datos: ContextoIAUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    contexto = _obtener_o_crear_contexto(db, negocio)

    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(contexto, campo, valor)

    # Cubre updates parciales: si solo llega ai_model (o solo ai_provider),
    # igual validamos que la combinación FINAL sea coherente.
    permitidos = MODELOS_GEMINI if contexto.ai_provider == "gemini" else MODELOS_OPENAI
    if contexto.ai_model not in permitidos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El modelo '{contexto.ai_model}' no pertenece al proveedor '{contexto.ai_provider}'",
        )

    db.commit()
    db.refresh(contexto)
    return _con_servicios(db, contexto)


# --- Catálogo de servicios: precio y duración que ve la IA ---

@router.post("/servicios", response_model=ServicioResponse, status_code=status.HTTP_201_CREATED)
def agregar_servicio(
    datos: ServicioCreate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    nuevo_servicio = Servicio(id_negocio=negocio.id_negocio, **datos.model_dump())
    db.add(nuevo_servicio)
    db.commit()
    db.refresh(nuevo_servicio)
    return nuevo_servicio


@router.get("/servicios", response_model=list[ServicioResponse])
def listar_servicios(db: Session = Depends(get_db), negocio: Negocio = Depends(obtener_negocio_propio)):
    return db.query(Servicio).filter(Servicio.id_negocio == negocio.id_negocio).all()


def _obtener_servicio_propio(id_servicio: int, db: Session, negocio: Negocio) -> Servicio:
    servicio = (
        db.query(Servicio)
        .filter(Servicio.id_servicio == id_servicio, Servicio.id_negocio == negocio.id_negocio)
        .first()
    )
    if servicio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
    return servicio


@router.put("/servicios/{id_servicio}", response_model=ServicioResponse)
def actualizar_servicio(
    id_servicio: int,
    datos: ServicioUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    servicio = _obtener_servicio_propio(id_servicio, db, negocio)

    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(servicio, campo, valor)

    db.commit()
    db.refresh(servicio)
    return servicio


@router.delete("/servicios/{id_servicio}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_servicio(
    id_servicio: int,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    servicio = _obtener_servicio_propio(id_servicio, db, negocio)
    db.delete(servicio)
    db.commit()


@router.post("/probar-ia")
def probar_ia(
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Botón "Probar IA" del Dashboard: hace una consulta corta con el motor
    elegido y los respaldos, y devuelve el resultado de cada intento."""
    from app.services import ai_service

    contexto = db.query(ContextoIA).filter(ContextoIA.id_negocio == negocio.id_negocio).first()
    registro: list = []
    texto = ai_service.generar_respuesta_o_none(
        "Responde solo con la palabra OK.",
        provider=contexto.ai_provider if contexto else None,
        model=contexto.ai_model if contexto else None,
        registro=registro,
    )
    return {"funciona": texto is not None, "intentos": registro}
