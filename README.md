# TrepAI — Backend + Dashboard

Plataforma SaaS que le da a las pymes un asistente con IA para atender a
sus clientes por WhatsApp de forma automática — la idea detrás del
nombre es justo esa: que un negocio chico pueda **trepar** y crecer sin
tener que contratar a alguien solo para contestar mensajes. Estructura
por capas (FastAPI + SQLAlchemy + SQLite), fiel al MER y a los RF/RNF
del informe del Grupo 7.

## Estructura

```
app/
  core/        -> configuración, seguridad (JWT/bcrypt), dependencias de auth
  db/          -> conexión a la base de datos (SQLAlchemy)
  models/      -> tablas: Administrador, Negocio, Servicio, Cliente,
                  Conversacion, Mensaje, ContextoIA, Agenda
  schemas/     -> validación Pydantic de entrada/salida por módulo
  routers/     -> endpoints agrupados por requerimiento funcional
  services/    -> integración con WhatsApp Cloud API y el proveedor de IA
  main.py      -> arma la app, CORS, monta el Dashboard, registra routers

frontend/      -> Dashboard (HTML/CSS/JS vanilla + fetch a la API)
tests/         -> smoke_test.py: prueba end-to-end con datos de Silvabarber
deploy/        -> systemd + nginx para el VPS (alternativa al deploy en Vercel)

pyproject.toml -> le dice a Vercel dónde está la app FastAPI
vercel.json    -> duración máxima de la función serverless
.vercelignore  -> qué queda fuera del despliegue en Vercel
```

## Cómo correrlo localmente

> **Versión de Python:** el proyecto funciona con Python 3.11 a 3.14. Las
> dependencias en `requirements.txt` usan versiones mínimas (`>=`) elegidas
> específicamente porque ya publican wheels precompilados para 3.14 — no
> deberías necesitar compilar nada ni instalar Rust/Visual C++ Build Tools
> en ningún equipo, incluidas las salas de la universidad.
>
> Si en un equipo puntual `pip install` igual intenta compilar algo desde
> el código fuente, asegúrate de que `pip` esté actualizado primero
> (`python -m pip install --upgrade pip`) — versiones viejas de pip a
> veces no saben pedir el wheel correcto aunque exista.

```bash
python -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # y completa tus credenciales

uvicorn app.main:app --reload
```

Al levantar, SQLAlchemy crea automáticamente `trepai.db` con las 8 tablas
del diccionario de datos.

- API y documentación interactiva: `http://127.0.0.1:8000/docs`
- Dashboard: `http://127.0.0.1:8000/dashboard/`

La primera vez que inicias sesión en el Dashboard, si tu cuenta no tiene
ningún negocio creado, se crea uno automáticamente ("Mi negocio") para que
puedas seguir configurando desde ahí — no queda una pantalla vacía.

## Pruebas end-to-end

Con el backend corriendo en otra terminal:

```bash
python tests/smoke_test.py
```

Recorre el journey completo con datos realistas de Silvabarber: registro,
negocio, catálogo, contexto, conexión WhatsApp, bot encendido, mensaje
entrante simulado, verificación del historial, agenda, y aislamiento
multitenant entre negocios. Es buena práctica correrlo antes de cada
despliegue y después de cualquier cambio grande en los routers.

## Mapeo requerimiento -> archivo

| RF/RNF | Dónde vive |
|---|---|
| RF-01 Autenticación | `routers/auth.py`, `core/security.py` (JWT + bcrypt) |
| RF-02 Perfil del negocio | `routers/negocio.py` |
| RF-03 Inyección de contexto | `routers/contexto.py`, `models/contexto_ia.py`, `models/servicio.py` — en el Dashboard: bloques "Personificación" / "Catálogo & Precios" / "Reglas del Local" |
| RF-04 Control on/off del bot | `routers/negocio.py` (`estado_bot`) |
| RF-05 Historial de logs | `routers/conversaciones.py` |
| RF-06 Recepción de webhooks | `routers/webhook.py`, `services/whatsapp_service.py` — token cifrado con `core/security.py` (`cifrar_texto`/`descifrar_texto`) |
| RF-07 Prompt dinámico | `services/ai_service.py` (`armar_prompt`) |
| RF-08 Procesamiento NLP | `services/ai_service.py` (`generar_respuesta`, Gemini/OpenAI) — en el Dashboard: bloque "Motor de IA" (proveedor + modelo por negocio) |
| RF-09 Envío de respuesta | `services/whatsapp_service.py` (`enviar_mensaje`) |
| RNF-01 Latencia < 15s | `core/config.py` (`MAX_RESPONSE_LATENCY_SECONDS`), timeout en `ai_service.py` |
| RNF-02 Multitenant | `id_negocio` como FK + `core/deps.py` (`obtener_negocio_propio`) |

## Despliegue en Vercel (recomendado para partir)

Vercel soporta FastAPI de forma prácticamente nativa (detecta la app y la
sirve como una única Vercel Function). Ya está todo preparado en el repo
(`pyproject.toml`, `vercel.json`, `.vercelignore`) — solo falta la parte
de credenciales.

> ⚠️ **Importante sobre el plan gratuito (Hobby):** los Términos de
> Servicio de Vercel restringen el plan Hobby a uso personal/no comercial.
> Sirve perfecto para probar TrepAI, hacer la demo y validar el producto
> — pero si van a cobrarle a clientes reales, van a necesitar el plan
> **Pro** (USD 20/mes) antes de lanzarlo de verdad.

### Por qué no alcanza con lo que ya tenían

Vercel es **serverless**: cada request puede correr en una instancia
distinta y el disco no persiste entre invocaciones. Eso significa que
`trepai.db` (SQLite) **se perdería** — no sirve para producción acá.
Por eso el proyecto ahora soporta Postgres a través de la misma
`DATABASE_URL` (no hay que tocar código, solo cambiar la variable).

### 1. Crear una base de datos Postgres gratuita

La forma más simple es [Neon](https://neon.tech) (tiene un free tier
generoso y lo usan un montón de proyectos que corren en Vercel):

1. Crea una cuenta en neon.tech y un proyecto nuevo.
2. En el dashboard de Neon, copia el **Connection string** — viene como
   `postgresql://usuario:clave@ep-xxxx.neon.tech/nombre_bd?sslmode=require`.
3. Cámbiale el prefijo a `postgresql+psycopg://` (SQLAlchemy necesita
   saber qué driver usar):
   ```
   postgresql+psycopg://usuario:clave@ep-xxxx.neon.tech/nombre_bd?sslmode=require
   ```
   Esa es tu `DATABASE_URL` de producción.

*(Alternativa: Vercel también ofrece Postgres directo desde la pestaña
"Storage" de tu proyecto, sin salir del dashboard — mismo resultado.)*

### 2. Generar las claves propias (nunca uses las de ejemplo del repo)

En tu terminal, con el venv activado:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# -> esta es tu SECRET_KEY

python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# -> esta es tu ENCRYPTION_KEY
```

Guarda ambos valores, los vas a necesitar en el paso 4.

### 3. Conectar el repo a Vercel

1. Sube el proyecto a GitHub (si aún no lo hiciste).
2. Entra a [vercel.com](https://vercel.com), crea una cuenta e importa
   el repositorio ("Add New… → Project → Import Git Repository").
3. En "Framework Preset" deja **Other** (FastAPI no está en la lista de
   frameworks, Vercel lo maneja igual gracias al `pyproject.toml`).
4. **No** le des a "Deploy" todavía — primero hay que cargar las
   variables de entorno (siguiente paso), o el primer deploy va a fallar
   por falta de credenciales.

### 4. Cargar las variables de entorno en Vercel

En el proyecto dentro de Vercel: **Settings → Environment Variables**.
Agrega estas, una por una (nombre exacto a la izquierda, valor a la
derecha):

| Variable | Valor |
|---|---|
| `DATABASE_URL` | El connection string de Neon del paso 1, con el prefijo `postgresql+psycopg://` |
| `SECRET_KEY` | La que generaste en el paso 2 |
| `ENCRYPTION_KEY` | La que generaste en el paso 2 |
| `WHATSAPP_VERIFY_TOKEN` | Cualquier string que inventes (ej. `trepai_verify_2026`) — lo vas a volver a usar en el paso 6 |
| `AI_PROVIDER` | `gemini` u `openai` |
| `AI_API_KEY` | Tu clave de la API de IA (ver paso 5) |
| `GEMINI_MODEL` | `gemini-3.6-flash` (si usas Gemini) |
| `OPENAI_MODEL` | `gpt-5.4-mini` (si usas OpenAI) |
| `CORS_ALLOWED_ORIGINS` | `*` para partir; después de desplegar, cámbialo a tu dominio real de Vercel |

Dale **Deploy**. La primera vez que la función arranque, SQLAlchemy va a
crear las 8 tablas automáticamente en tu base Neon (mismo mecanismo que
en local, solo que ahora apunta a Postgres).

### 5. Conseguir la API key del motor de IA

- **Gemini (gratis para empezar):** entra a
  [aistudio.google.com/apikey](https://aistudio.google.com/apikey), crea
  una API key con tu cuenta de Google, y pégala en `AI_API_KEY`.
- **OpenAI:** entra a
  [platform.openai.com/api-keys](https://platform.openai.com/api-keys),
  crea una key (vas a necesitar tener saldo cargado en la cuenta), y
  pégala en `AI_API_KEY`.

Recuerda: el proveedor elegido acá en el `.env`/Vercel es el *default*
del sistema — cada negocio puede después elegir su propio proveedor y
modelo desde el bloque "Motor de IA" del Dashboard, sin tocar esta
variable.

### 6. Conectar el webhook real de WhatsApp

Una vez desplegado, Vercel te da una URL propia con HTTPS automático
(algo como `https://trepai.vercel.app`) — ya cumple el requisito de
Meta sin necesitar nginx ni certbot como en un VPS.

1. Entra a [developers.facebook.com](https://developers.facebook.com),
   crea una app de tipo "Business", y agrégale el producto **WhatsApp**.
2. En WhatsApp → Configuration:
   - **Callback URL:** `https://tu-proyecto.vercel.app/webhook`
   - **Verify Token:** el mismo valor exacto que pusiste en
     `WHATSAPP_VERIFY_TOKEN` en el paso 4.
3. Dale "Verify and Save" — Meta le va a pegar un GET a tu webhook; si
   el token coincide, queda verificado (esto es exactamente lo que hace
   `verificar_webhook()` en `app/routers/webhook.py`).
4. Desde el Dashboard de TrepAI (`https://tu-proyecto.vercel.app/dashboard/`),
   en el negocio correspondiente, conecta el **Phone Number ID** y el
   **token de acceso** que Meta te da en esa misma pantalla de
   configuración (bloque "Conexión con WhatsApp" → queda cifrado en la
   BD automáticamente).

### 7. Verificar que quedó todo funcionando

```bash
python tests/smoke_test.py
```

Pero antes cambia `BASE_URL` al inicio del archivo de
`http://127.0.0.1:8000` a tu dominio real de Vercel, para que las 13
pruebas corran contra producción en vez de local.

### Cada vez que hagan push a GitHub

Vercel redespliega solo. No hay paso manual — a diferencia del VPS, acá
no hay que hacer `ssh` ni reiniciar ningún servicio.

---

## Despliegue en el VPS (alternativa — más control, sin límite de tiempo por request)

Pensado para un VPS chico (2 vCPU / 2GB RAM), con Nginx como proxy reverso
y gunicorn+uvicorn corriendo la app como servicio del sistema.

1. **Preparar el servidor**
   ```bash
   sudo apt update && sudo apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx
   sudo adduser --system --group trepai
   ```

2. **Subir el código y crear el entorno**
   ```bash
   git clone <tu-repo> /home/trepai/trepai-backend
   cd /home/trepai/trepai-backend
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env   # completar con las credenciales reales de producción
   ```

3. **Servicio systemd** — copia `deploy/trepai.service` a
   `/etc/systemd/system/trepai.service`, ajusta usuario/rutas si difieren, y:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now trepai-ai
   sudo systemctl status trepai-ai
   ```

4. **Nginx + HTTPS** — copia `deploy/nginx.conf` a
   `/etc/nginx/sites-available/trepai-ai`, ajusta `server_name` a tu dominio:
   ```bash
   sudo ln -s /etc/nginx/sites-available/trepai-ai /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d tu-dominio.cl
   ```
   HTTPS no es opcional acá: Meta **rechaza** webhooks de WhatsApp que no
   sean HTTPS.

5. **Configurar el webhook en Meta for Developers** — en la sección
   WhatsApp > Configuration de tu app, el Callback URL es
   `https://tu-dominio.cl/webhook` y el Verify Token es el mismo valor que
   pusiste en `WHATSAPP_VERIFY_TOKEN` del `.env`.

6. **Verificar que quedó arriba**
   ```bash
   curl https://tu-dominio.cl/
   python tests/smoke_test.py   # apuntando BASE_URL a tu dominio
   ```

### Después de cada actualización de código

```bash
cd /home/trepai/trepai-backend
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart trepai-ai
python tests/smoke_test.py
```

## Notas de seguridad para producción

- Genera un `SECRET_KEY` **y** un `ENCRYPTION_KEY` reales y únicos por
  entorno (nunca los placeholders del `.env.example`) — comandos exactos
  en la sección de despliegue en Vercel más arriba.
- El `whatsapp_token` de cada negocio ya se cifra en reposo con
  `cryptography.fernet` (`core/security.py`: `cifrar_texto`/`descifrar_texto`)
  antes de guardarse en la BD — verificado que lo que queda en la tabla
  `negocio` no es el texto plano original.
- Si en algún momento cambias `ENCRYPTION_KEY`, los tokens de WhatsApp ya
  guardados van a dejar de poder descifrarse (van a quedar como
  "no conectado" hasta que el administrador los vuelva a cargar) — no
  hay drama, pero avisa a los negocios activos antes de rotarla.
- Cambia `CORS_ALLOWED_ORIGINS` de `*` al dominio real del Dashboard una
  vez que esté desplegado.
