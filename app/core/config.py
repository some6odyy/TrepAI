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

# --- Motor de IA (RF-08) ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")  # gemini | openai
AI_API_KEY = os.getenv("AI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

# --- Cifrado en reposo del token de WhatsApp de cada negocio ---
# Clave de ejemplo solo para desarrollo local — en producción SIEMPRE se
# debe definir una propia. Generarla con:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "6Tx_gPZKphFL3YldOKPEOnYR78mPEoLSzFZed6FzhBg=")

# --- Requerimiento no funcional RNF-01: latencia máxima ---
MAX_RESPONSE_LATENCY_SECONDS = 15

# --- CORS: qué orígenes pueden llamar a esta API desde el navegador ---
# En desarrollo dejamos "*" para no pelear con el puerto del frontend.
# En producción, reemplazar por el dominio real del Dashboard.
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")
