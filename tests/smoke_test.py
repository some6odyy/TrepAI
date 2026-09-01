"""
Pruebas end-to-end de TrepAI con datos realistas de Silvabarber.

Corre contra un backend real levantado en localhost — no es un mock.
Recorre el journey completo: registro -> negocio -> contexto -> WhatsApp
-> bot activo -> mensaje entrante -> agenda -> historial.

Uso:
    uvicorn app.main:app --port 8000 &   # en otra terminal
    python tests/smoke_test.py

Requiere: pip install requests (ya está en requirements.txt)
"""
import sys
import time
import requests

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 10

fallos = []


def paso(nombre):
    def decorador(func):
        def envoltura(*args, **kwargs):
            try:
                resultado = func(*args, **kwargs)
                print(f"  ✅ {nombre}")
                return resultado
            except AssertionError as error:
                print(f"  ❌ {nombre} — {error}")
                fallos.append(nombre)
            except requests.RequestException as error:
                print(f"  ❌ {nombre} — no se pudo conectar: {error}")
                fallos.append(nombre)
                sys.exit(1)
        return envoltura
    return decorador


@paso("El backend está arriba")
def verificar_backend():
    r = requests.get(f"{BASE_URL}/", timeout=TIMEOUT)
    assert r.status_code == 200, f"esperaba 200, llegó {r.status_code}"


@paso("Registro del administrador de Silvabarber")
def registrar_administrador(correo, contrasena):
    r = requests.post(
        f"{BASE_URL}/auth/registro", timeout=TIMEOUT,
        json={"nombre": "Silva Barber", "correo": correo, "contrasena": contrasena},
    )
    assert r.status_code == 201, f"esperaba 201, llegó {r.status_code}: {r.text}"


@paso("Login y obtención del token JWT")
def login(correo, contrasena):
    r = requests.post(
        f"{BASE_URL}/auth/login", timeout=TIMEOUT,
        json={"correo": correo, "contrasena": contrasena},
    )
    assert r.status_code == 200, f"esperaba 200, llegó {r.status_code}: {r.text}"
    token = r.json()["access_token"]
    assert token, "el token vino vacío"
    return token


@paso("Crear el negocio Silvabarber")
def crear_negocio(headers):
    r = requests.post(
        f"{BASE_URL}/negocio", timeout=TIMEOUT, headers=headers,
        json={
            "nombre_negocio": "Silvabarber",
            "direccion": "Av. Los Carrera 456, Concepción",
            "telefono": "+56941234567",
            "horario": "Lunes a sábado, 10:00 a 19:00",
        },
    )
    assert r.status_code == 201, f"esperaba 201, llegó {r.status_code}: {r.text}"
    return r.json()["id_negocio"]


@paso("Cargar el catálogo de servicios real")
def cargar_catalogo(headers, id_negocio):
    servicios = [
        {"nombre_servicio": "Corte clásico", "precio": 8000, "duracion_estimada": 30},
        {"nombre_servicio": "Corte + barba", "precio": 12000, "duracion_estimada": 45},
        {"nombre_servicio": "Perfilado de barba", "precio": 5000, "duracion_estimada": 15},
    ]
    ids = []
    for s in servicios:
        r = requests.post(
            f"{BASE_URL}/negocio/{id_negocio}/contexto/servicios",
            timeout=TIMEOUT, headers=headers, json=s,
        )
        assert r.status_code == 201, f"esperaba 201 creando {s['nombre_servicio']}, llegó {r.status_code}"
        ids.append(r.json()["id_servicio"])
    return ids


@paso("Guardar reglas e instrucciones del negocio")
def guardar_contexto(headers, id_negocio):
    r = requests.put(
        f"{BASE_URL}/negocio/{id_negocio}/contexto", timeout=TIMEOUT, headers=headers,
        json={
            "reglas_negocio": "No se agenda fuera del horario de atención. Máximo 1 hora por cliente al día.",
            "instrucciones": "Responde de forma breve, cercana y profesional. Usa el nombre del cliente si lo conoces.",
        },
    )
    assert r.status_code == 200, f"esperaba 200, llegó {r.status_code}: {r.text}"
    assert len(r.json()["servicios"]) == 3, "el contexto debería traer los 3 servicios cargados"


@paso("Conectar el número de WhatsApp (sandbox)")
def conectar_whatsapp(headers, id_negocio):
    r = requests.put(
        f"{BASE_URL}/negocio/{id_negocio}/whatsapp", timeout=TIMEOUT, headers=headers,
        json={"phone_number_id": "888777666555444", "access_token": "EAAsandboxTestToken123456"},
    )
    assert r.status_code == 200, f"esperaba 200, llegó {r.status_code}: {r.text}"
    assert r.json()["conectado"] is True, "debería quedar conectado"


@paso("Encender el bot")
def encender_bot(headers, id_negocio):
    r = requests.patch(
        f"{BASE_URL}/negocio/{id_negocio}/estado-bot?activo=true", timeout=TIMEOUT, headers=headers,
    )
    assert r.status_code == 200, f"esperaba 200, llegó {r.status_code}"
    assert r.json()["estado_bot"] is True, "el bot debería quedar encendido"


@paso("Simular un mensaje entrante real de un cliente por WhatsApp")
def simular_mensaje_entrante():
    payload = {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "888777666555444"},
            "messages": [{"from": "56999887766", "text": {"body": "Hola, cuanto cuesta un corte con barba?"}}],
        }}]}]
    }
    r = requests.post(f"{BASE_URL}/webhook", timeout=TIMEOUT, json=payload)
    assert r.status_code == 200, f"esperaba 200, llegó {r.status_code}: {r.text}"
    assert r.json()["status"] == "procesado", f"esperaba 'procesado', llegó {r.json()}"


@paso("El mensaje quedó guardado en el historial (RF-05)")
def verificar_historial(headers, id_negocio):
    r = requests.get(f"{BASE_URL}/negocio/{id_negocio}/conversaciones", timeout=TIMEOUT, headers=headers)
    assert r.status_code == 200
    conversaciones = r.json()
    assert len(conversaciones) == 1, f"esperaba 1 conversación, hay {len(conversaciones)}"
    assert conversaciones[0]["total_mensajes"] == 2, "debería haber mensaje del cliente + respuesta del bot"

    id_conv = conversaciones[0]["id_conversacion"]
    r2 = requests.get(
        f"{BASE_URL}/negocio/{id_negocio}/conversaciones/{id_conv}/mensajes",
        timeout=TIMEOUT, headers=headers,
    )
    mensajes = r2.json()
    assert mensajes[0]["emisor"] == "cliente"
    assert mensajes[1]["emisor"] == "bot"


@paso("Agendar una cita manualmente desde el Dashboard")
def agendar_cita(headers, id_negocio, id_servicio):
    r = requests.post(
        f"{BASE_URL}/negocio/{id_negocio}/agenda", timeout=TIMEOUT, headers=headers,
        json={
            "telefono_cliente": "+56988776655", "nombre_cliente": "Cliente de prueba",
            "id_servicio": id_servicio, "fecha_cita": "2026-09-15", "hora_cita": "16:00:00",
        },
    )
    assert r.status_code == 201, f"esperaba 201, llegó {r.status_code}: {r.text}"
    return r.json()["id_agenda"]


@paso("Confirmar la cita agendada")
def confirmar_cita(headers, id_negocio, id_agenda):
    r = requests.patch(
        f"{BASE_URL}/negocio/{id_negocio}/agenda/{id_agenda}/estado",
        timeout=TIMEOUT, headers=headers, json={"estado_cita": "confirmada"},
    )
    assert r.status_code == 200
    assert r.json()["estado_cita"] == "confirmada"


@paso("Otro administrador NO puede ver el negocio de Silvabarber (multitenant)")
def verificar_aislamiento(id_negocio, correo_otro, contrasena_otro):
    requests.post(
        f"{BASE_URL}/auth/registro", timeout=TIMEOUT,
        json={"nombre": "Otro Admin", "correo": correo_otro, "contrasena": contrasena_otro},
    )
    r = requests.post(
        f"{BASE_URL}/auth/login", timeout=TIMEOUT,
        json={"correo": correo_otro, "contrasena": contrasena_otro},
    )
    otro_token = r.json()["access_token"]
    r2 = requests.get(
        f"{BASE_URL}/negocio/{id_negocio}/contexto", timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {otro_token}"},
    )
    assert r2.status_code == 403, f"esperaba 403, llegó {r2.status_code}"


def main():
    sufijo = str(int(time.time()))
    correo = f"silva{sufijo}@barberia.cl"
    contrasena = "claveSegura123"

    print("TrepAI — pruebas end-to-end con datos de Silvabarber\n")

    verificar_backend()
    registrar_administrador(correo, contrasena)
    token = login(correo, contrasena)
    headers = {"Authorization": f"Bearer {token}"}

    id_negocio = crear_negocio(headers)
    ids_servicios = cargar_catalogo(headers, id_negocio)
    guardar_contexto(headers, id_negocio)
    conectar_whatsapp(headers, id_negocio)
    encender_bot(headers, id_negocio)

    simular_mensaje_entrante()
    verificar_historial(headers, id_negocio)

    if ids_servicios:
        id_agenda = agendar_cita(headers, id_negocio, ids_servicios[0])
        if id_agenda:
            confirmar_cita(headers, id_negocio, id_agenda)

    verificar_aislamiento(id_negocio, f"otro{sufijo}@test.cl", "otraClave123")

    print()
    if fallos:
        print(f"❌ {len(fallos)} prueba(s) fallaron: {', '.join(fallos)}")
        sys.exit(1)
    else:
        print("✅ Todas las pruebas end-to-end pasaron")


if __name__ == "__main__":
    main()
