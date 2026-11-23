"""
Configuración central de TrepAI.
Lee variables de entorno para no dejar credenciales hardcodeadas en el código.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

# --- Base de datos ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/trepai.db")

# --- Autenticación (RF-01) ---
SECRET_KEY = os.getenv("SECRET_KEY", "CAMBIAR_ESTA_CLAVE_EN_PRODUCCION")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8 horas

# --- WhatsApp Business Cloud API (RF-06 / RF-09) ---
# El phone_number_id y el token viven por negocio (tabla negocio), no acá,
# porque cada pyme conecta su propio número de WhatsApp Business.
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "trepai_verify")

# --- Puente de demo con whatsapp-web.js (bridge/) ---
# Si PUENTE_TOKEN queda vacío, el endpoint /puente no funciona (desactivado).
PUENTE_TOKEN = os.getenv("PUENTE_TOKEN", "")
# Negocio que atiende el puente. Vacío = el primer negocio con el bot encendido.
_puente_id = os.getenv("PUENTE_ID_NEGOCIO", "").strip()
PUENTE_ID_NEGOCIO = int(_puente_id) if _puente_id.isdigit() else None

# --- Motor de IA (RF-08) ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")  # gemini | openai
AI_API_KEY = os.getenv("AI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
# Claves por proveedor (opcionales). Si no se definen, se usa AI_API_KEY para
# el proveedor de AI_PROVIDER. Con las dos claves, si un proveedor falla se
# intenta con el otro.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or (AI_API_KEY if AI_PROVIDER == "gemini" else "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() or (AI_API_KEY if AI_PROVIDER == "openai" else "")
# Modelos de respaldo, en orden, si el modelo elegido por el negocio falla
# (límite de uso 429, servidor saturado 503, demora...). Separados por coma.
GEMINI_MODELOS_RESPALDO = [
    m.strip() for m in os.getenv("GEMINI_MODELOS_RESPALDO", "gemini-3.5-flash-lite,gemini-3.6-flash").split(",") if m.strip()
]
OPENAI_MODELOS_RESPALDO = [
    m.strip() for m in os.getenv("OPENAI_MODELOS_RESPALDO", "gpt-5.4-mini,gpt-4o").split(",") if m.strip()
]

# --- Cifrado en reposo del token de WhatsApp de cada negocio ---
# Clave de ejemplo solo para desarrollo local — en producción SIEMPRE se
# debe definir una propia. Generarla con:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "6Tx_gPZKphFL3YldOKPEOnYR78mPEoLSzFZed6FzhBg=")

# --- Recordatorios y confirmaciones de citas ---
# Clave que el cron externo (GitHub Actions, cron-job.org, Vercel Cron) manda
# para ejecutar /tareas/recordatorios. Vacía = endpoint desactivado.
CRON_SECRET = os.getenv("CRON_SECRET", "").strip()
# URL pública del Dashboard, para los enlaces de confirmar/cancelar del correo.
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8000").rstrip("/")
# Plantillas aprobadas por Meta para escribirle al cliente FUERA de la ventana
# de 24 h (ver README). Vacías = fuera de la ventana solo se envía correo.
WHATSAPP_PLANTILLA_RECORDATORIO = os.getenv("WHATSAPP_PLANTILLA_RECORDATORIO", "").strip()
WHATSAPP_PLANTILLA_CONFIRMACION = os.getenv("WHATSAPP_PLANTILLA_CONFIRMACION", "").strip()
WHATSAPP_PLANTILLA_IDIOMA = os.getenv("WHATSAPP_PLANTILLA_IDIOMA", "es").strip() or "es"

# --- Seguridad del webhook ---
# Clave secreta de la app de Meta (developers.facebook.com > App settings >
# Basic > App secret). Con ella se verifica la firma X-Hub-Signature-256 de
# cada evento. Vacía = no se verifica (solo para desarrollo local).
META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()

# Cuando el dueño responde a mano desde Conversaciones, el bot se calla en
# esa conversación por estas horas (o hasta que lo reactive).
PAUSA_BOT_MANUAL_HORAS = float(os.getenv("PAUSA_BOT_MANUAL_HORAS", "2") or 2)

# --- Requerimiento no funcional RNF-01: latencia máxima ---
MAX_RESPONSE_LATENCY_SECONDS = 15

# --- CORS: qué orígenes pueden llamar a esta API desde el navegador ---
# En desarrollo dejamos "*" para no pelear con el puerto del frontend.
# En producción, reemplazar por el dominio real del Dashboard.
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")
