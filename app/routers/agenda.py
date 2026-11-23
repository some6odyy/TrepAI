"""Agenda: citas creadas por la IA (RF-07/08) o manualmente desde el Dashboard."""
from datetime import date as date_type
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.deps import obtener_negocio_propio
from app.models.negocio import Negocio
from app.models.agenda import Agenda
from app.models.servicio import Servicio
from app.models.cliente import Cliente
from app.services.cliente_service import obtener_o_crear_cliente
from app.services import agenda_service
from app.services.agenda_service import (
    AnticipacionInsuficienteError,
    DatosContactoError,
    HorarioOcupadoError,
)
from app.services import recordatorios
from app.services.horario_service import FueraDeHorarioError, validar_dentro_de_horario
from app.schemas.agenda import CitaCreate, CitaEstadoUpdate, CitaResponse, CitaUpdate

router = APIRouter(prefix="/negocio/{id_negocio}/agenda", tags=["Agenda"])


def _a_response(cita: Agenda, cliente: Cliente, servicio: Servicio) -> CitaResponse:
    return CitaResponse(
        id_agenda=cita.id_agenda,
        id_cliente=cliente.id_cliente,
        nombre_cliente=cita.nombre_contacto or cliente.nombre_cliente,
        telefono_cliente=cliente.telefono,
        correo_cliente=cita.correo_contacto or cliente.correo,
        telefono_contacto=cita.telefono_contacto,
        id_servicio=servicio.id_servicio,
        nombre_servicio=servicio.nombre_servicio,
        fecha_cita=cita.fecha_cita,
        hora_cita=cita.hora_cita,
        estado_cita=cita.estado_cita,
        recordatorio_24h_enviado=cita.recordatorio_24h_enviado,
        confirmacion_1h_enviada=cita.confirmacion_1h_enviada,
        respuesta_cliente=cita.respuesta_cliente,
    )


@router.get("", response_model=list[CitaResponse])
def listar_citas(
    fecha: date_type | None = Query(default=None, description="Filtrar por fecha exacta (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    agenda_service.liberar_pendientes_vencidas(db, negocio.id_negocio)

    query = (
        db.query(Agenda, Cliente, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .join(Cliente, Agenda.id_cliente == Cliente.id_cliente)
        .filter(Servicio.id_negocio == negocio.id_negocio)
    )
    if fecha is not None:
        query = query.filter(Agenda.fecha_cita == fecha)

    resultados = query.order_by(Agenda.fecha_cita, Agenda.hora_cita).all()
    return [_a_response(cita, cliente, servicio) for cita, cliente, servicio in resultados]


@router.post("", response_model=CitaResponse, status_code=status.HTTP_201_CREATED)
def crear_cita(
    datos: CitaCreate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    servicio = (
        db.query(Servicio)
        .filter(Servicio.id_servicio == datos.id_servicio, Servicio.id_negocio == negocio.id_negocio)
        .first()
    )
    if servicio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese servicio no existe en este negocio")

    try:
        nombre, correo, telefono = agenda_service.validar_contacto(
            datos.nombre_cliente, datos.correo_cliente, datos.telefono_cliente
        )
    except DatosContactoError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))

    cliente = obtener_o_crear_cliente(db, telefono, nombre, correo, reemplazar_nombre=True)

    try:
        nueva_cita = agenda_service.crear_cita_validada(
            db,
            id_negocio=negocio.id_negocio,
            id_cliente=cliente.id_cliente,
            servicio=servicio,
            fecha=datos.fecha_cita,
            hora=datos.hora_cita,
            horario_negocio=negocio.horario,
            nombre_contacto=nombre,
            correo_contacto=correo,
            telefono_contacto=telefono,
        )
    except (FueraDeHorarioError, AnticipacionInsuficienteError, DatosContactoError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    except HorarioOcupadoError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))

    # Aviso "cita agendada" al cliente (WhatsApp + correo) apenas se crea
    recordatorios.procesar_cita(db, negocio, nueva_cita.id_agenda)
    db.refresh(nueva_cita)
    return _a_response(nueva_cita, cliente, servicio)


def _obtener_cita_propia(id_agenda: int, db: Session, negocio: Negocio) -> tuple[Agenda, Cliente, Servicio]:
    resultado = (
        db.query(Agenda, Cliente, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .join(Cliente, Agenda.id_cliente == Cliente.id_cliente)
        .filter(Agenda.id_agenda == id_agenda, Servicio.id_negocio == negocio.id_negocio)
        .first()
    )
    if resultado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cita no encontrada")
    return resultado


@router.put("/{id_agenda}", response_model=CitaResponse)
def editar_cita(
    id_agenda: int,
    datos: CitaUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Editar una cita desde la Agenda: servicio, día, hora y datos de
    contacto. Si cambia el día o la hora se revisan las mismas reglas que al
    agendar (horario de atención, bloqueos, choque con otras citas y 2 horas
    de anticipación) y el cliente recibe el aviso con la hora nueva."""
    agenda_service.liberar_pendientes_vencidas(db, negocio.id_negocio)
    cita, cliente, servicio = _obtener_cita_propia(id_agenda, db, negocio)

    servicio_nuevo = servicio
    if datos.id_servicio is not None and datos.id_servicio != servicio.id_servicio:
        servicio_nuevo = (
            db.query(Servicio)
            .filter(Servicio.id_servicio == datos.id_servicio, Servicio.id_negocio == negocio.id_negocio)
            .first()
        )
        if servicio_nuevo is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese servicio no existe en este negocio")
    fecha = datos.fecha_cita or cita.fecha_cita
    hora = (datos.hora_cita or cita.hora_cita).replace(second=0, microsecond=0)
    cambia_momento = fecha != cita.fecha_cita or hora != cita.hora_cita.replace(second=0, microsecond=0)
    cambia_horario = cambia_momento or servicio_nuevo.id_servicio != servicio.id_servicio

    # Datos de contacto (a ellos llegan los recordatorios)
    invalidos = []
    correo = telefono = None
    if datos.correo_cliente is not None:
        correo = agenda_service.normalizar_correo(datos.correo_cliente)
        if correo is None:
            invalidos.append("correo")
    if datos.telefono_contacto is not None:
        telefono = agenda_service.normalizar_telefono(datos.telefono_contacto)
        if telefono is None:
            invalidos.append("teléfono")
    if invalidos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(DatosContactoError([], invalidos)),
        )

    if cambia_horario and cita.estado_cita in agenda_service.ESTADOS_QUE_BLOQUEAN:
        duracion = agenda_service.duracion_minutos(servicio_nuevo)
        try:
            if cambia_momento:
                agenda_service.validar_anticipacion(fecha, hora)
            validar_dentro_de_horario(negocio.horario, fecha, hora, duracion)
            agenda_service.validar_horario_libre(
                db, negocio.id_negocio, fecha, hora, duracion, excluir_id_agenda=cita.id_agenda
            )
        except (FueraDeHorarioError, AnticipacionInsuficienteError) as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
        except HorarioOcupadoError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))

    cita.id_servicio = servicio_nuevo.id_servicio
    cita.fecha_cita = fecha
    cita.hora_cita = hora
    if datos.nombre_cliente is not None:
        cita.nombre_contacto = " ".join(datos.nombre_cliente.split())
    if correo:
        cita.correo_contacto = correo
    if telefono:
        cita.telefono_contacto = telefono
    if cambia_momento:
        # Hora nueva = avisos nuevos (el cliente recibe la hora actualizada)
        cita.aviso_reserva_enviado = None
        cita.recordatorio_24h_enviado = None
        cita.confirmacion_1h_enviada = None
        cita.respuesta_cliente = None
    db.commit()
    db.refresh(cita)

    if cambia_momento and cita.estado_cita in ("pendiente", "confirmada"):
        recordatorios.procesar_cita(db, negocio, cita.id_agenda)
        db.refresh(cita)
    return _a_response(cita, cliente, servicio_nuevo)


@router.patch("/{id_agenda}/estado", response_model=CitaResponse)
def actualizar_estado_cita(
    id_agenda: int,
    datos: CitaEstadoUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    agenda_service.liberar_pendientes_vencidas(db, negocio.id_negocio)
    cita, cliente, servicio = _obtener_cita_propia(id_agenda, db, negocio)
    estado_anterior = cita.estado_cita

    # Si se reactiva una cita que no estaba ocupando horario (cancelada o
    # expirada), hay que comprobar que el espacio siga libre.
    if datos.estado_cita in agenda_service.ESTADOS_QUE_BLOQUEAN and cita.estado_cita not in agenda_service.ESTADOS_QUE_BLOQUEAN:
        try:
            validar_dentro_de_horario(
                negocio.horario, cita.fecha_cita, cita.hora_cita, agenda_service.duracion_minutos(servicio)
            )
            agenda_service.validar_horario_libre(
                db, negocio.id_negocio, cita.fecha_cita, cita.hora_cita,
                agenda_service.duracion_minutos(servicio), excluir_id_agenda=cita.id_agenda,
            )
        except FueraDeHorarioError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
        except HorarioOcupadoError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))

    if datos.estado_cita == "pendiente" and cita.estado_cita != "pendiente":
        cita.fecha_creacion = agenda_service.ahora_utc()  # reinicia las 2 horas
    cita.estado_cita = datos.estado_cita
    db.commit()
    db.refresh(cita)

    if datos.estado_cita == "confirmada" and estado_anterior != "confirmada":
        recordatorios.notificar_confirmacion_del_local(db, negocio, cita, cliente, servicio)

    return _a_response(cita, cliente, servicio)


@router.delete("/{id_agenda}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_cita(
    id_agenda: int,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    cita, _, _ = _obtener_cita_propia(id_agenda, db, negocio)
    db.delete(cita)
    db.commit()
