/**
 * TrepAI — Puente de DEMOSTRACIÓN con whatsapp-web.js
 *
 * Vincula un número normal de WhatsApp (escaneando un QR, igual que
 * WhatsApp Web), escucha los mensajes entrantes y los reenvía al backend
 * (POST /puente/mensaje). Además expone POST /enviar en localhost para
 * que el backend envíe notificaciones salientes (ej. cita confirmada).
 *
 * OJO: whatsapp-web.js NO es oficial. Úsalo solo para demos cortas y, si
 * puedes, con un chip de repuesto. El diseño real sigue siendo la
 * WhatsApp Business Cloud API (RF-06 / RF-09).
 */
require("dotenv").config({ path: require("path").join(__dirname, ".env") });

const http = require("http");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");

const TREPAI_URL = (process.env.TREPAI_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const PUENTE_TOKEN = process.env.PUENTE_TOKEN || "";
const PUENTE_PORT = Number(process.env.PUENTE_PORT || 3001);
const NUMEROS_PERMITIDOS = new Set(
  (process.env.NUMEROS_PERMITIDOS || "")
    .split(",")
    .map((n) => n.replace(/\D/g, ""))
    .filter(Boolean)
);
const PALABRA_CLAVE = (process.env.PALABRA_CLAVE || "").trim().toLowerCase();
const MINUTOS_SESION = 30;
const TIMEOUT_MS = 30000;

if (!PUENTE_TOKEN) {
  console.error("❌ Falta PUENTE_TOKEN en bridge/.env (debe ser igual al del backend).");
  process.exit(1);
}
if (NUMEROS_PERMITIDOS.size === 0 && !PALABRA_CLAVE) {
  console.error("❌ Define NUMEROS_PERMITIDOS o PALABRA_CLAVE en bridge/.env,");
  console.error("   si no el bot le respondería a TODOS tus contactos.");
  process.exit(1);
}

// Chats que activaron el bot con la palabra clave -> timestamp del último mensaje
const sesionesActivas = new Map();
// Teléfono normalizado -> chatId real (@c.us o @lid) para responder sin fallos
const chatsPorNumero = new Map();

function debeResponder(numero, texto) {
  if (NUMEROS_PERMITIDOS.has(numero)) return true;
  if (!PALABRA_CLAVE) return false;

  const ahora = Date.now();
  if (texto.trim().toLowerCase().startsWith(PALABRA_CLAVE)) {
    sesionesActivas.set(numero, ahora);
    return true;
  }
  const ultimo = sesionesActivas.get(numero);
  if (ultimo && ahora - ultimo < MINUTOS_SESION * 60 * 1000) {
    sesionesActivas.set(numero, ahora);
    return true;
  }
  return false;
}

async function llamarBackend(ruta, opciones = {}) {
  const res = await fetch(`${TREPAI_URL}${ruta}`, {
    ...opciones,
    headers: {
      "Content-Type": "application/json",
      "X-Puente-Token": PUENTE_TOKEN,
      ...(opciones.headers || {}),
    },
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const cuerpo = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${cuerpo.detail || JSON.stringify(cuerpo)}`);
  }
  return cuerpo;
}

async function verificarBackend() {
  try {
    const estado = await llamarBackend("/puente/estado");
    if (estado.ok) {
      console.log(`✅ Backend OK — atendiendo a "${estado.nombre_negocio}" (id ${estado.id_negocio})`);
    } else {
      console.warn(`⚠️  Backend responde, pero: ${estado.motivo}`);
      console.warn("   Enciende el bot del negocio en el Dashboard.");
    }
  } catch (err) {
    console.warn(`⚠️  No pude hablar con el backend en ${TREPAI_URL}: ${err.message}`);
    console.warn("   Revisa que uvicorn esté corriendo y que PUENTE_TOKEN sea igual en ambos .env");
  }
}

function crearCliente() {
  const c = new Client({
    authStrategy: new LocalAuth({ dataPath: require("path").join(__dirname, ".wwebjs_auth") }),
    puppeteer: {
      headless: true,
      // PCs lentos (ej. salas de la U): WhatsApp Web tarda en cargar y el
      // timeout por defecto de 30 s no alcanza. 0 = sin límite.
      protocolTimeout: 0,
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    },
  });
  registrarEventos(c);
  return c;
}

let client = null;

function registrarEventos(client) {
  client.on("qr", (qr) => {
    console.log("\n📱 Escanea este QR desde WhatsApp > Dispositivos vinculados > Vincular un dispositivo:\n");
    qrcode.generate(qr, { small: true });
  });

  client.on("authenticated", () => console.log("🔐 Sesión autenticada (queda guardada en bridge/.wwebjs_auth)"));
  client.on("auth_failure", (m) => console.error("❌ Falló la autenticación:", m));
  client.on("disconnected", (motivo) => {
    console.warn("🔌 WhatsApp desconectado:", motivo);
    process.exit(1);
  });

  client.on("ready", async () => {
    console.log(`\n🤖 TrepAI puente listo como +${client.info.wid.user}`);
    if (NUMEROS_PERMITIDOS.size) console.log(`   Responde a: ${[...NUMEROS_PERMITIDOS].join(", ")}`);
    if (PALABRA_CLAVE) console.log(`   O a quien empiece con: "${PALABRA_CLAVE}"`);
    await verificarBackend();
  });

  // Las cuentas nuevas de WhatsApp llegan como "@lid" (un id interno, no el
  // teléfono). Intentamos obtener el número real; si no se puede, usamos el id.
  async function resolverNumero(msg) {
    const idCrudo = msg.from.split("@")[0];
    if (msg.from.endsWith("@lid")) {
      try {
        const [info] = await client.getContactLidAndPhone([msg.from]);
        if (info && info.pn) return info.pn.split("@")[0];
      } catch (err) {
        console.warn("   (no pude convertir @lid a número:", err.message || err, ")");
      }
    }
    try {
      const contacto = await msg.getContact();
      if (contacto && contacto.number) return contacto.number.replace(/\D/g, "");
    } catch (_) {
      /* sin contacto, usamos el id */
    }
    return idCrudo;
  }

  async function enviarRespuesta(msg, texto) {
    // 1° intento: responder al mismo chat de donde vino el mensaje
    try {
      await client.sendMessage(msg.from, texto);
      return;
    } catch (err) {
      console.warn("   envío al chat original falló:", err.message || err);
    }
    // 2° intento: citar el mensaje original
    await msg.reply(texto);
  }

  // "message" solo dispara con mensajes RECIBIDOS (no los que tú envías)
  client.on("message", async (msg) => {
    let paso = "leer mensaje";
    try {
      // Solo chats individuales y texto (sin grupos, estados, audios, etc.)
      if (msg.from.endsWith("@g.us") || msg.from === "status@broadcast" || msg.isStatus) return;
      if (msg.type !== "chat" || !msg.body) return;

      paso = "obtener número";
      const numero = await resolverNumero(msg);
      chatsPorNumero.set(numero, msg.from);
      if (!debeResponder(numero, msg.body)) return;

      console.log(`💬 ${numero}: ${msg.body}`);

      // "Escribiendo..." es solo cosmético: si falla, seguimos igual
      let chat = null;
      try {
        chat = await msg.getChat();
        await chat.sendStateTyping();
      } catch (_) {
        chat = null;
      }

      paso = "llamar al backend";
      const datos = await llamarBackend("/puente/mensaje", {
        method: "POST",
        body: JSON.stringify({ telefono: numero, mensaje: msg.body }),
      });

      if (chat) await chat.clearState().catch(() => { });
      if (!datos.respuesta) {
        console.log(`   (sin respuesta: ${datos.status})`);
        return;
      }

      paso = "enviar respuesta por WhatsApp";
      await enviarRespuesta(msg, datos.respuesta);
      console.log(`🤖 -> ${datos.respuesta}`);
    } catch (err) {
      console.error(`❌ Error al ${paso}:`, err && (err.message || err));
    }
  });
}

// Micro-servidor local para notificaciones salientes desde FastAPI (ej. cita confirmada)
const servidorEnvios = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/enviar") {
    res.writeHead(404, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ detail: "Not Found" }));
  }

  if (req.headers["x-puente-token"] !== PUENTE_TOKEN) {
    res.writeHead(401, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ detail: "Token inválido" }));
  }

  if (!client || !client.info) {
    res.writeHead(503, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ detail: "WhatsApp Web aún no está listo" }));
  }

  let body = "";
  req.on("data", (chunk) => {
    body += chunk;
  });

  req.on("end", async () => {
    try {
      const { telefono, texto } = JSON.parse(body || "{}");
      const digitos = String(telefono || "").replace(/\D/g, "");
      if (!digitos || !texto) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ detail: "Falta telefono o texto" }));
      }

      let chatId = chatsPorNumero.get(digitos);
      if (!chatId) {
        const numberId = await client.getNumberId(digitos).catch(() => null);
        chatId = numberId ? numberId._serialized : `${digitos}@c.us`;
      }

      await client.sendMessage(chatId, texto);
      console.log(`📤 Notificación enviada a ${digitos}`);

      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      console.error("❌ Error enviando notificación saliente:", err && (err.message || err));
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: (err && err.message) || "Error interno" }));
    }
  });
});

servidorEnvios.listen(PUENTE_PORT, "127.0.0.1", () => {
  console.log(`📡 Endpoint de envíos activo en http://127.0.0.1:${PUENTE_PORT}/enviar`);
});

// whatsapp-web.js tiene listeners internos que a veces fallan mientras
// WhatsApp Web se recarga ("Could not load response body", etc.). Esos
// errores no son graves, así que los mostramos y seguimos en vez de caernos.
process.on("unhandledRejection", (err) => {
  console.warn("⚠️  (error interno ignorado):", (err && err.message) || err);
});

process.on("SIGINT", async () => {
  console.log("\nCerrando puente...");
  servidorEnvios.close();
  if (client) await client.destroy().catch(() => { });
  process.exit(0);
});

// WhatsApp Web a veces se recarga solo mientras conectamos ("Execution
// context was destroyed"). En vez de caernos, reintentamos unas veces.
async function iniciar(intento = 1) {
  const MAX_INTENTOS = 5;
  console.log(`Iniciando WhatsApp Web (intento ${intento}/${MAX_INTENTOS}, puede tardar)...`);
  client = crearCliente();
  try {
    await client.initialize();
  } catch (err) {
    console.warn(`⚠️  Falló la conexión: ${err.message || err}`);
    await client.destroy().catch(() => { });
    if (intento >= MAX_INTENTOS) {
      console.error("❌ No se pudo conectar. Cierra los 'Google Chrome for Testing' en el Administrador de tareas y prueba de nuevo.");
      process.exit(1);
    }
    await new Promise((r) => setTimeout(r, 5000));
    return iniciar(intento + 1);
  }
}

iniciar();