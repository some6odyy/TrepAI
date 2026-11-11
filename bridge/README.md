# Puente de demo — WhatsApp con whatsapp-web.js

Conecta un número normal de WhatsApp al backend de TrepAI escaneando un QR,
igual que WhatsApp Web. Sirve para hacer la demo sin la Cloud API de Meta.

> ⚠️ whatsapp-web.js **no es oficial** y va contra los términos de WhatsApp.
> Para una demo corta el riesgo es bajo, pero si puedes usa un chip de repuesto.
> El diseño oficial del sistema sigue siendo la **WhatsApp Business Cloud API**.

## Requisitos

- Node.js 18 o superior (`node -v`)
- El backend de TrepAI corriendo (`uvicorn app.main:app --reload`)

## 1. Backend: activar el puente

En el `.env` de la raíz del proyecto (no el de `bridge/`):

```
PUENTE_TOKEN=una-clave-larga-cualquiera
PUENTE_ID_NEGOCIO=        # vacío = el primer negocio con el bot encendido
```

Reinicia uvicorn. Sin `PUENTE_TOKEN` el endpoint `/puente` queda apagado.

En el Dashboard: el negocio debe tener catálogo, contexto y **el bot encendido**.
Y en el `.env` del backend tiene que estar `AI_API_KEY`, si no el bot
responde siempre el mensaje de respaldo ("Disculpa, en este momento...").

## 2. Bridge: configurar e instalar

```bash
cd bridge
cp .env.example .env        # en Windows: copy .env.example .env
npm install                 # descarga Chromium, tarda un par de minutos
```

Edita `bridge/.env`:

```
TREPAI_URL=http://127.0.0.1:8000
PUENTE_TOKEN=una-clave-larga-cualquiera     # el MISMO del backend
NUMEROS_PERMITIDOS=56912345678,56987654321  # quiénes pueden hablar con el bot
PALABRA_CLAVE=                              # opcional, ej. barberia
```

## 3. Correr

```bash
npm start
```

1. Aparece un QR en la terminal.
2. En el celular: WhatsApp → Dispositivos vinculados → Vincular un dispositivo → escanear.
3. Debe decir `✅ Backend OK — atendiendo a "Silvabarber"`.
4. Desde **otro** celular (uno de `NUMEROS_PERMITIDOS`) escríbele al número vinculado.
   El bot responde y la conversación aparece en el Historial del Dashboard.

La sesión queda guardada en `bridge/.wwebjs_auth/`, así que la próxima vez no
pide QR. Para desvincular: borra esa carpeta y cierra la sesión desde el celular.

## Notas

- Los mensajes que **tú** mandas desde el número vinculado no activan el bot.
  Por eso el cliente de prueba tiene que ser otro teléfono.
- Ignora grupos, estados, audios e imágenes; solo responde texto.
- Si apagas el bot en el Dashboard (RF-04), el puente deja de responder.
- `TREPAI_URL` también puede ser tu URL de Vercel, siempre que hayas puesto
  `PUENTE_TOKEN` en las variables de entorno de Vercel.
