"""RF-02 | Gestión de perfil del negocio. RF-04 | Control on/off del bot."""
import os
import re
import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
 
from app.core.deps import obtener_administrador_actual, obtener_negocio_propio
from app.core.security import cifrar_texto
from app.db.database import get_db
from app.models.administrador import Administrador
from app.models.negocio import Negocio
from app.schemas.negocio import EstadoBotResponse, NegocioCreate, NegocioResponse, NegocioUpdate
from app.schemas.whatsapp import WhatsAppConexionUpdate, WhatsAppEstadoResponse
 
router = APIRouter(prefix="/negocio", tags=["Negocio"])
 
GRAPH_URL = "https://graph.facebook.com/v26.0"
 
 
class SolicitarCodigoWhatsApp(BaseModel):
    telefono: str
    nombre_visible: str
 
 
class VerificarCodigoWhatsApp(BaseModel):
    codigo: str
 
 
def _separar_telefono_chile(telefono: str) -> tuple[str, str]:
    digitos = re.sub(r"\D", "", telefono)
    if digitos.startswith("56") and len(digitos) >= 11:
        return "56", digitos[2:]
    if len(digitos) == 9 and digitos.startswith("9"):
        return "56", digitos
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Ingresa un número válido con código de país (ej. +56912345678)",
    )
 
 
def _credenciales_maestras_meta() -> tuple[str, str]:
    waba_id = (os.getenv("META_WABA_ID") or "").strip()
    master_token = (os.getenv("META_MASTER_TOKEN") or "").strip()
    if not waba_id or not master_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Faltan META_WABA_ID o META_MASTER_TOKEN en la configuración del servidor",
        )
    return waba_id, master_token
 
def _meta(metodo: str, url: str, **kwargs):
    """Llama a la Graph API; si Meta no responde, devuelve un error legible en vez de un 500."""
    kwargs.setdefault("timeout", 20)
    try:
        return getattr(requests, metodo)(url, **kwargs)
    except requests.RequestException as exc:
        print("ERROR META (red):", metodo.upper(), url, repr(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Meta no respondió a tiempo. Espera unos segundos y vuelve a intentarlo.",
        )


def _error_meta(resp, por_defecto: str) -> str:
    """Extrae el motivo real de un error de la Graph API de Meta."""
    try:
        err = resp.json().get("error", {})
    except ValueError:
        return f"{por_defecto} (HTTP {resp.status_code})"
    print("ERROR META:", resp.status_code, err)  # se ve en los logs de Vercel/uvicorn
    partes = [
        err.get("error_user_title"),
        err.get("error_user_msg"),
        (err.get("error_data") or {}).get("details"),
    ]
    detalle = " — ".join(p for p in partes if p) or err.get("message") or por_defecto
    codigo = err.get("code")
    sub = err.get("error_subcode")
    if codigo:
        detalle += f" (Meta {codigo}{'/' + str(sub) if sub else ''})"
    return detalle
 
def _buscar_numero_en_waba(waba_id: str, headers: dict, cc: str, numero_local: str) -> str | None:
    """Devuelve el phone_number_id si el número ya está dado de alta en el WABA."""
    resp = _meta("get",
        f"{GRAPH_URL}/{waba_id}/phone_numbers",
        headers=headers,
        params={"fields": "id,display_phone_number", "limit": 100},
    )
    if not resp.ok:
        return None
    objetivo = cc + numero_local
    for item in resp.json().get("data", []):
        if re.sub(r"\D", "", item.get("display_phone_number", "")) == objetivo:
            return item.get("id")
    return None
 
@router.post("/{id_negocio}/whatsapp/solicitar-codigo")
def solicitar_codigo_whatsapp(
    datos: SolicitarCodigoWhatsApp,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    waba_id, master_token = _credenciales_maestras_meta()
    headers = {"Authorization": f"Bearer {master_token}"}
    cc, numero_local = _separar_telefono_chile(datos.telefono)
 
        # 1) Reutilizar el número si ya existe en el WABA; si no, darlo de alta
    phone_number_id = _buscar_numero_en_waba(waba_id, headers, cc, numero_local)
    if not phone_number_id:
        resp_num = _meta("post",
            f"{GRAPH_URL}/{waba_id}/phone_numbers",
            headers=headers,
            json={
                "cc": cc,
                "phone_number": numero_local,
                "verified_name": datos.nombre_visible.strip(),
            },
        )
        if not resp_num.ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error_meta(resp_num, "No se pudo registrar el número en Meta"),
            )
        phone_number_id = resp_num.json().get("id")
        if not phone_number_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meta no devolvió el ID del número")
 
    ya_usado = (
        negocio.whatsapp_phone_number_id != phone_number_id
        and db.query(Negocio).filter(Negocio.whatsapp_phone_number_id == phone_number_id).first()
    )
    if ya_usado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese número ya está conectado a otro negocio en TrepAI",
        )
 
    # 2) Pedir a Meta que envíe el SMS con el código de 6 dígitos
    resp_sms = _meta("post",
        f"{GRAPH_URL}/{phone_number_id}/request_code",
        headers=headers,
        json={"code_method": "SMS", "language": "es"},
    )
    if not resp_sms.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_meta(resp_sms, "No se pudo enviar el SMS de verificación"),
        )
 
    negocio.whatsapp_phone_number_id = phone_number_id
    db.commit()
 
    return {"ok": True, "phone_number_id": phone_number_id}
 
 
@router.post("/{id_negocio}/whatsapp/verificar-codigo", response_model=WhatsAppEstadoResponse)
def verificar_codigo_whatsapp(
    datos: VerificarCodigoWhatsApp,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    if not negocio.whatsapp_phone_number_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Primero solicita el código SMS con tu número",
        )
 
    _, master_token = _credenciales_maestras_meta()
    headers = {"Authorization": f"Bearer {master_token}"}
    phone_id = negocio.whatsapp_phone_number_id
 
    # 1) Validar código de 6 dígitos. Si un intento anterior se cortó por timeout pero Meta
    #    alcanzó a verificarlo, el número ya figura como VERIFIED y se pasa directo a activarlo.
    resp_estado = _meta("get", f"{GRAPH_URL}/{phone_id}", headers=headers,
                        params={"fields": "code_verification_status"})
    ya_verificado = resp_estado.ok and resp_estado.json().get("code_verification_status") == "VERIFIED"

    if not ya_verificado:
        resp_verify = _meta("post",
            f"{GRAPH_URL}/{phone_id}/verify_code",
            headers=headers,
            json={"code": re.sub(r"\D", "", datos.codigo)},
            timeout=30,
        )
        if not resp_verify.ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error_meta(resp_verify, "Código incorrecto o expirado"),
            )

    # 2) Registrar el número en Cloud API para habilitar mensajería
    resp_reg = _meta("post",
        f"{GRAPH_URL}/{phone_id}/register",
        headers=headers,
        json={"messaging_product": "whatsapp", "pin": "123456"},
        timeout=30,
    )
    if not resp_reg.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_meta(resp_reg, "Código verificado, pero falló la activación en Cloud API"),
        )
 
    try:
        negocio.whatsapp_token = cifrar_texto(master_token)
        negocio.estado_bot = True
        db.commit()
    except Exception as exc:
        db.rollback()
        print("ERROR al guardar la conexión de WhatsApp:", repr(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El número quedó verificado en Meta, pero no se pudo guardar en TrepAI ({type(exc).__name__})",
        )
    db.refresh(negocio)
 
    return WhatsAppEstadoResponse(conectado=True, phone_number_id=negocio.whatsapp_phone_number_id)
 
 
def _enmascarar(numero: str) -> str:
    """+56 9 9495 1451 -> +56 9 •••• 1451 (para números de otros negocios)."""
    digitos = re.sub(r"\D", "", numero)
    return f"+{digitos[:3]} •••• {digitos[-4:]}" if len(digitos) > 7 else "••••"
 
 
def _numeros_del_waba(waba_id: str, headers: dict) -> list[dict]:
    resp = _meta("get",
        f"{GRAPH_URL}/{waba_id}/phone_numbers",
        headers=headers,
        params={
            "fields": "id,display_phone_number,verified_name,status,code_verification_status,quality_rating",
            "limit": 100,
        },
    )
    if not resp.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_meta(resp, "No se pudieron leer los números de la cuenta de Meta"),
        )
    return resp.json().get("data", [])
 
 
@router.get("/{id_negocio}/whatsapp/numeros")
def listar_numeros_whatsapp(
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Números dados de alta en el WABA de TrepAI (Meta permite 2 sin verificar)."""
    waba_id, master_token = _credenciales_maestras_meta()
    headers = {"Authorization": f"Bearer {master_token}"}
    numeros = _numeros_del_waba(waba_id, headers)
 
    ids = [n.get("id") for n in numeros]
 
    # Si este negocio quedó con un número a medio verificar que ya no existe en Meta, se limpia
    if negocio.whatsapp_phone_number_id and not negocio.whatsapp_token and negocio.whatsapp_phone_number_id not in ids:
        negocio.whatsapp_phone_number_id = None
        db.commit()
 
    # phone_number_id -> (id_negocio, conectado). "Conectado" = verificó el SMS y tiene token
    dueños = {
        n.whatsapp_phone_number_id: (n.id_negocio, bool(n.whatsapp_token))
        for n in db.query(Negocio).filter(Negocio.whatsapp_phone_number_id.in_(ids)).all()
    }
 
    resultado = []
    for n in numeros:
        id_dueño, conectado = dueños.get(n.get("id"), (None, False))
        es_este = id_dueño == negocio.id_negocio
        de_otro = id_dueño is not None and not es_este
        if es_este:
            vinculado_a = "este" if conectado else "este_pendiente"
        else:
            vinculado_a = "otro" if de_otro else None
        resultado.append({
            "phone_number_id": n.get("id"),
            "numero": _enmascarar(n.get("display_phone_number", "")) if de_otro else n.get("display_phone_number"),
            "nombre": None if de_otro else n.get("verified_name"),
            "estado": n.get("status"),
            "verificacion": n.get("code_verification_status"),
            "calidad": n.get("quality_rating"),
            "vinculado_a": vinculado_a,
            # Se puede borrar si está suelto o si es de este negocio pero nunca terminó la verificación
            "se_puede_eliminar": id_dueño is None or (es_este and not conectado),
        })
    # Meta no permite borrar números por API: se hace en el Administrador de WhatsApp
    return {
        "limite": 2,
        "numeros": resultado,
        "url_administrador": f"https://business.facebook.com/wa/manage/phone-numbers/?waba_id={waba_id}",
    }
 
 
@router.post("", response_model=NegocioResponse, status_code=status.HTTP_201_CREATED)
def crear_negocio(
    datos: NegocioCreate,
    db: Session = Depends(get_db),
    administrador_actual: Administrador = Depends(obtener_administrador_actual),
):
    """Alta de un negocio (ej. la barbería) bajo el administrador logueado."""
    nuevo_negocio = Negocio(
        id_administrador=administrador_actual.id_administrador,
        nombre_negocio=datos.nombre_negocio,
        direccion=datos.direccion,
        telefono=datos.telefono,
        horario=datos.horario,
        estado_bot=False,
    )
    db.add(nuevo_negocio)
    db.commit()
    db.refresh(nuevo_negocio)
    return nuevo_negocio
 
 
@router.get("", response_model=list[NegocioResponse])
def listar_mis_negocios(
    db: Session = Depends(get_db),
    administrador_actual: Administrador = Depends(obtener_administrador_actual),
):
    """Devuelve solo los negocios del administrador logueado."""
    return (
        db.query(Negocio)
        .filter(Negocio.id_administrador == administrador_actual.id_administrador)
        .all()
    )
 
 
@router.get("/{id_negocio}", response_model=NegocioResponse)
def obtener_negocio(negocio: Negocio = Depends(obtener_negocio_propio)):
    return negocio
 
 
@router.put("/{id_negocio}/perfil", response_model=NegocioResponse)
def actualizar_perfil(
    datos: NegocioUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Actualiza solo los campos enviados en el body."""
    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(negocio, campo, valor)
 
    db.commit()
    db.refresh(negocio)
    return negocio
 
 
@router.patch("/{id_negocio}/estado-bot", response_model=EstadoBotResponse)
def cambiar_estado_bot(
    activo: bool,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """RF-04 — interruptor inmediato para prender/apagar el bot."""
    negocio.estado_bot = activo
    db.commit()
    db.refresh(negocio)
    return EstadoBotResponse(id_negocio=negocio.id_negocio, estado_bot=negocio.estado_bot)
 
 
@router.get("/{id_negocio}/whatsapp", response_model=WhatsAppEstadoResponse)
def obtener_estado_whatsapp(negocio: Negocio = Depends(obtener_negocio_propio)):
    return WhatsAppEstadoResponse(
        conectado=bool(negocio.whatsapp_phone_number_id and negocio.whatsapp_token),
        phone_number_id=negocio.whatsapp_phone_number_id,
    )
 
 
@router.put("/{id_negocio}/whatsapp", response_model=WhatsAppEstadoResponse)
def conectar_whatsapp(
    datos: WhatsAppConexionUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """RF-06 — guarda las credenciales del número de WhatsApp Business Cloud API."""
    ya_usado = (
        negocio.whatsapp_phone_number_id != datos.phone_number_id
        and db.query(Negocio).filter(Negocio.whatsapp_phone_number_id == datos.phone_number_id).first()
    )
    if ya_usado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese phone_number_id ya está conectado a otro negocio",
        )
 
    negocio.whatsapp_phone_number_id = datos.phone_number_id
    negocio.whatsapp_token = cifrar_texto(datos.access_token)
    db.commit()
    db.refresh(negocio)
 
    return WhatsAppEstadoResponse(conectado=True, phone_number_id=negocio.whatsapp_phone_number_id)
 
 
@router.delete("/{id_negocio}/whatsapp", response_model=WhatsAppEstadoResponse)
def desvincular_whatsapp(
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """RF-06 — desvincula el número de WhatsApp del negocio."""
    negocio.whatsapp_phone_number_id = None
    negocio.whatsapp_token = None
    negocio.estado_bot = False
    db.commit()
    db.refresh(negocio)
 
    return WhatsAppEstadoResponse(conectado=False, phone_number_id=None)