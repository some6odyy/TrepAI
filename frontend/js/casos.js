/* ==========================================================================
   TrepAI — Pestaña "Casos de uso"
   Demostración en vivo de cada requerimiento: un simulador de WhatsApp que
   usa el mismo flujo que un mensaje real (webhook -> prompt -> IA -> agenda)
   y una tarjeta por RF/RNF con su evidencia y un botón para probarlo.
   Depende de app.js (estado, api, $, mostrarToast, escapeHtml, ...).
   ========================================================================== */

const casos = {
  telefono: '56900000001',
  evidencia: null,
  grupo: '',
  enviando: false,
  ultimoTexto: '',
};

const TELEFONOS_DEMO = {
  '56900000001': { n: '1', nombre: 'Cliente de prueba 1' },
  '56900000002': { n: '2', nombre: 'Cliente de prueba 2' },
};

/* ------------------------------ fechas del guion ------------------------------ */

function _proximoDiaHabil(desde, dias) {
  const d = new Date(desde);
  d.setDate(d.getDate() + dias);
  while (d.getDay() === 0) d.setDate(d.getDate() + 1); // sin domingos
  return d;
}

const _ddmm = (d) => `${String(d.getDate()).padStart(2, '0')}-${String(d.getMonth() + 1).padStart(2, '0')}`;

function guionDemo() {
  const hoy = new Date();
  const d1 = _proximoDiaHabil(hoy, 3);
  const d2 = _proximoDiaHabil(d1, 1);
  const d3 = _proximoDiaHabil(d2, 1);
  const servicios = estado.servicios || [];
  const s1 = servicios[0]?.nombre_servicio || 'Corte';
  const s2 = servicios[1]?.nombre_servicio || s1;
  const enUnaHora = new Date(hoy.getTime() + 60 * 60 * 1000);
  const t1 = '56900000001';
  const t2 = '56900000002';
  return [
    { etiqueta: 'Consultar precios', cu: 'RF-03', tel: t1, texto: 'Hola! ¿Qué servicios tienen y cuánto cuestan?' },
    { etiqueta: 'Agendar', cu: 'AG-01', tel: t1,
      texto: `Quiero agendar ${s1} el ${_ddmm(d1)} a las 15:00. Soy Cliente Demo, demo@trepai.cl, +56 9 0000 0001` },
    { etiqueta: 'Agendar otra cita', cu: 'AG-02', tel: t1,
      texto: `También quiero agendar otra hora: ${s2} el ${_ddmm(d2)} a las 11:00` },
    { etiqueta: 'Choque con su otra cita', cu: 'AG-03', tel: t1,
      texto: `Agéndame además ${s2} el ${_ddmm(d1)} a las 15:00` },
    { etiqueta: 'Cambiar una cita', cu: 'AG-04', tel: t1, texto: `Cambia el ${s1} al ${_ddmm(d3)} a las 16:00` },
    { etiqueta: 'Hora ocupada (cliente 2)', cu: 'AG-03', tel: t2,
      texto: `Quiero ${s1} el ${_ddmm(d3)} a las 16:00. Soy Otra Persona, otra@trepai.cl, +56 9 0000 0002` },
    { etiqueta: 'Cambiar sin decir cuál', cu: 'AG-04', tel: t1, texto: 'Quiero cambiar mi hora a las 12:00' },
    { etiqueta: 'Responder "2"', cu: 'AG-04', tel: t1, texto: '2' },
    { etiqueta: 'Menos de 2 h', cu: 'AG-01', tel: t2,
      texto: `${s1} hoy a las ${String(enUnaHora.getHours()).padStart(2, '0')}:00. Soy Otra Persona, otra@trepai.cl, +56 9 0000 0002` },
    { etiqueta: 'Cancelar una', cu: 'AG-05', tel: t1, texto: `Cancela el ${s2}` },
  ];
}

/* --------------------------------- simulador --------------------------------- */

function _burbuja(clase, html, meta = '') {
  const thread = $('simThread');
  thread.querySelector('.chat-empty')?.remove();
  const div = document.createElement('div');
  div.className = `bubble ${clase}`;
  div.innerHTML = html + (meta ? `<span class="meta">${meta}</span>` : '');
  thread.appendChild(div);
  thread.scrollTop = thread.scrollHeight;
  return div;
}

const ORIGENES = { ia: 'IA', sin_ia: 'sin IA (respaldo)', confirmacion: 'confirmación SÍ/NO', no_texto: 'no texto' };

function _metaRespuesta(origen, latenciaMs) {
  const partes = [];
  if (origen) partes.push(ORIGENES[origen] || origen);
  if (latenciaMs != null) partes.push(`${(latenciaMs / 1000).toFixed(2)} s`);
  return partes.join(' · ');
}

function seleccionarClienteDemo(tel) {
  casos.telefono = tel;
  const info = TELEFONOS_DEMO[tel];
  $('simAvatar').textContent = info.n;
  $('simSub').textContent = `${info.nombre} · ${formatoTelefono(tel)}`;
  $$('[data-sim-tel]').forEach((b) => b.classList.toggle('active', b.dataset.simTel === tel));
  cargarConversacionDemo();
}

async function cargarConversacionDemo() {
  const thread = $('simThread');
  try {
    const [mensajes, citas] = await Promise.all([
      api.demoConversacion(estado.negocio.id_negocio, casos.telefono),
      api.demoCitas(estado.negocio.id_negocio, casos.telefono),
    ]);
    thread.innerHTML = '';
    if (!mensajes.length) {
      thread.innerHTML = `<div class="chat-empty">${icono('chat')}<p>Escribe como si fueras un cliente, o usa el guion de abajo.</p></div>`;
    }
    mensajes.forEach((m) => {
      const clase = m.emisor === 'cliente' ? 'cliente' : m.emisor === 'negocio' ? 'negocio' : 'bot';
      _burbuja(clase, formatoWhatsapp(m.contenido), m.emisor === 'bot' ? _metaRespuesta(m.origen, m.latencia_ms) : '');
    });
    pintarCitasDemo(citas);
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

function pintarCitasDemo(citas) {
  const caja = $('simCitas');
  if (!citas.length) {
    caja.innerHTML = '<p class="text-muted">Sin citas activas.</p>';
    return;
  }
  caja.innerHTML = citas.map((c) => {
    const fecha = new Date(`${c.fecha_cita}T00:00:00`).toLocaleDateString('es-CL', { weekday: 'short', day: '2-digit', month: '2-digit' });
    return `<div class="casos-cita ${c.estado_cita}">
      <b>${escapeHtml(c.servicio)}</b>
      ${escapeHtml(fecha)} · ${escapeHtml(c.hora_cita)} (${c.duracion} min)
      <small>#${c.id_agenda} · ${escapeHtml(c.estado_cita)}</small>
    </div>`;
  }).join('') + `<p class="text-muted" style="font-size:12px;margin:4px 0 0">${citas.length} cita(s) activa(s)</p>`;
}

async function enviarMensajeDemo(texto, tel = casos.telefono) {
  texto = (texto || '').trim();
  if (!texto || casos.enviando) return;
  if (tel !== casos.telefono) {
    casos.telefono = tel;
    seleccionarClienteDemo(tel);
    await new Promise((r) => setTimeout(r, 250));
  }
  casos.enviando = true;
  casos.ultimoTexto = texto;
  $('simEnviar').disabled = true;
  _burbuja('cliente', formatoWhatsapp(texto));
  const escribiendo = _burbuja('bot escribiendo', 'escribiendo…');
  try {
    const r = await api.demoMensaje(estado.negocio.id_negocio, tel, texto);
    escribiendo.remove();
    if (r.estado === 'bot_inactivo') {
      _burbuja('sistema', '🔴 Bot apagado (RF-04): el mensaje quedó guardado en Historial, pero nadie respondió.');
    } else if (r.estado === 'conversacion_en_pausa') {
      _burbuja('sistema', '⏸ Conversación en pausa: el dueño la está atendiendo a mano desde Historial.');
    } else if (r.respuesta) {
      _burbuja('bot', formatoWhatsapp(r.respuesta), _metaRespuesta(r.origen, r.latencia_ms));
    }
    $('simDetalle').innerHTML = `
      <dt>Origen</dt><dd>${escapeHtml(ORIGENES[r.origen] || r.estado)}</dd>
      <dt>Latencia</dt><dd>${(r.latencia_ms / 1000).toFixed(2)} s ${r.latencia_ms <= r.limite_ms ? '✅' : '⚠️'} (límite ${r.limite_ms / 1000} s)</dd>
      <dt>Citas</dt><dd>${r.citas_activas.length} activa(s)</dd>`;
    pintarCitasDemo(r.citas_activas);
  } catch (error) {
    escribiendo.remove();
    _burbuja('sistema', `Error: ${escapeHtml(error.message)}`);
  } finally {
    casos.enviando = false;
    $('simEnviar').disabled = false;
  }
}

function pintarGuion() {
  $('simGuion').innerHTML = guionDemo().map((paso, i) => `
    <button class="chip" type="button" data-paso="${i}" title="${escapeHtml(paso.texto)}">
      <span class="n">${i + 1}</span> ${escapeHtml(paso.etiqueta)}
    </button>`).join('');
}

async function verPromptDemo() {
  const texto = $('simTexto').value.trim() || casos.ultimoTexto || 'Hola, ¿qué horas tengo reservadas?';
  try {
    const r = await api.demoPrompt(estado.negocio.id_negocio, casos.telefono, texto);
    abrirModalCasos(
      `Prompt dinámico (RF-07) · ${r.proveedor || '—'} / ${r.modelo || '—'} · ${r.caracteres} caracteres`,
      `<pre>${escapeHtml(r.prompt)}</pre>`,
    );
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

/* ------------------------------ modal genérico ------------------------------ */

function abrirModalCasos(titulo, html) {
  $('modalCasosTitulo').textContent = titulo;
  $('modalCasosCuerpo').innerHTML = html;
  $('modalCasos').classList.add('open');
}

function cerrarModalCasos() {
  $('modalCasos').classList.remove('open');
  $('modalCasosCuerpo').innerHTML = '';
}

/* ------------------------------ tarjetas de CU ------------------------------ */

const _ok = (cond, si = 'Funcionando', no = 'Falta configurar') =>
  cond ? `<span class="pill pill-success">✓ ${si}</span>` : `<span class="pill pill-warning">! ${no}</span>`;

function definicionCasos(e) {
  const p = e.perfil;
  const perfilCompleto = Boolean(p.nombre_negocio && p.direccion && p.telefono && p.horario);
  const lat = e.latencia;
  return [
    {
      codigo: 'RF-01', grupo: 'rf', titulo: 'Autenticación', actor: 'Administrador',
      desc: 'Registro, inicio de sesión con token JWT y recuperación de contraseña.',
      estado: _ok(true, 'Sesión activa'),
      evidencia: `Sesión iniciada: ${e.administrador.nombre}\n${e.administrador.correo}\nToken Bearer en cada petición`,
      acciones: [{ t: 'Ir a Mi cuenta', fn: () => cambiarVista('cuenta') }],
    },
    {
      codigo: 'RF-02', grupo: 'rf', titulo: 'Gestión del perfil', actor: 'Administrador',
      desc: 'Formulario con nombre, dirección, teléfono y horario del local.',
      estado: _ok(perfilCompleto, 'Completo', 'Incompleto'),
      evidencia: `${p.nombre_negocio}\n${p.direccion || '(sin dirección)'} · ${p.telefono || '(sin teléfono)'}\nHorario: ${p.horario || '(sin horario)'}`,
      acciones: [{ t: 'Editar perfil', fn: () => cambiarVista('resumen') }],
    },
    {
      codigo: 'RF-03', grupo: 'rf', titulo: 'Inyección de contexto', actor: 'Administrador',
      desc: 'Catálogo de servicios, precios y reglas de negocio que instruyen a la IA.',
      estado: _ok(e.contexto.servicios > 0, `${e.contexto.servicios} servicios`, 'Sin catálogo'),
      evidencia: `Servicios: ${e.contexto.servicios}\nReglas: ${e.contexto.reglas ? 'sí' : 'no'} · Instrucciones: ${e.contexto.instrucciones ? 'sí' : 'no'}`,
      acciones: [
        { t: 'Ir a Contexto IA', fn: () => cambiarVista('contexto') },
        { t: 'Preguntar precios', fn: () => enviarMensajeDemo(guionDemo()[0].texto, '56900000001') },
      ],
    },
    {
      codigo: 'RF-04', grupo: 'rf', titulo: 'Control de estado del bot', actor: 'Administrador',
      desc: 'Interruptor On/Off que pausa al instante las respuestas automáticas.',
      estado: e.bot_activo ? '<span class="pill pill-success">● Encendido</span>' : '<span class="pill pill-danger">● Apagado</span>',
      evidencia: 'Con el bot apagado el mensaje se guarda en Historial pero no se responde.',
      acciones: [
        { t: e.bot_activo ? 'Apagar bot' : 'Encender bot', primario: true, fn: alternarBotDesdeCasos },
        { t: 'Enviar "Hola"', fn: () => enviarMensajeDemo('Hola') },
      ],
    },
    {
      codigo: 'RF-05', grupo: 'rf', titulo: 'Historial de conversaciones', actor: 'Administrador',
      desc: 'Registro auditable de lo que conversan la IA, el dueño y los clientes.',
      estado: _ok(e.historial.conversaciones > 0, `${e.historial.conversaciones} conversaciones`, 'Sin conversaciones'),
      evidencia: `Mensajes de clientes: ${e.historial.mensajes_cliente}\nRespuestas del bot: ${e.historial.respuestas_bot}`,
      acciones: [{ t: 'Ir a Historial', fn: () => cambiarVista('historial') }],
    },
    {
      codigo: 'RF-06', grupo: 'rf', titulo: 'Recepción de webhooks', actor: 'Usuario final',
      desc: 'El servidor recibe en tiempo real los mensajes que Meta envía al webhook.',
      estado: _ok(e.whatsapp_conectado, 'WhatsApp conectado', 'WhatsApp sin conectar'),
      evidencia: `Último mensaje recibido: ${e.historial.ultimo_mensaje ? formatoFechaHora(e.historial.ultimo_mensaje) : '—'}\nEndpoint: POST /webhook (firma X-Hub-Signature-256)`,
      acciones: [{ t: 'Simular mensaje', fn: () => $('simTexto').focus() }],
    },
    {
      codigo: 'RF-07', grupo: 'rf', titulo: 'Prompt dinámico', actor: 'Sistema',
      desc: 'Une el mensaje del cliente con el contexto, catálogo, horario y citas del negocio.',
      estado: _ok(true, 'Listo'),
      evidencia: 'Muestra el prompt exacto que recibe la IA para el último mensaje del simulador.',
      acciones: [{ t: 'Ver prompt', primario: true, fn: verPromptDemo }],
    },
    {
      codigo: 'RF-08', grupo: 'rf', titulo: 'Procesamiento NLP', actor: 'Sistema',
      desc: 'Consulta el modelo de IA; si falla, prueba respaldos y luego responde sin IA.',
      estado: _ok(Boolean(e.contexto.modelo), e.contexto.modelo || ''),
      evidencia: `Motor: ${e.contexto.proveedor || '—'} / ${e.contexto.modelo || '—'}\nRespuestas IA: ${e.historial.respuestas_ia} · sin IA: ${e.historial.respuestas_sin_ia}`,
      acciones: [{ t: 'Probar IA', fn: probarIaDesdeCasos }],
    },
    {
      codigo: 'RF-09', grupo: 'rf', titulo: 'Envío de respuesta', actor: 'Usuario final',
      desc: 'La respuesta vuelve al cliente por la API de WhatsApp (en el simulador, a la burbuja).',
      estado: _ok(e.historial.respuestas_bot > 0, `${e.historial.respuestas_bot} enviadas`, 'Sin respuestas aún'),
      evidencia: 'Envío real: Graph API /messages con el token del negocio.',
      acciones: [{ t: 'Enviar mensaje', fn: () => enviarMensajeDemo('¿Dónde están ubicados y a qué hora abren?') }],
    },
    {
      codigo: 'AG-01', grupo: 'ag', titulo: 'Agendar por WhatsApp', actor: 'Usuario final',
      desc: 'Pide servicio, día, hora, nombre, correo y teléfono; respeta horario, 2 h de anticipación y bloquea el espacio.',
      estado: _ok(e.contexto.agendar_automatico, 'Agendado automático ON', 'Agendado automático OFF'),
      evidencia: `Citas activas en el negocio: ${e.agenda.citas_activas}\nTotal histórico: ${e.agenda.total_citas}`,
      acciones: [
        { t: 'Agendar', primario: true, fn: () => correrPaso(1) },
        { t: 'Con < 2 h', fn: () => correrPaso(8) },
      ],
    },
    {
      codigo: 'AG-02', grupo: 'ag', titulo: 'Varias citas por cliente', actor: 'Usuario final',
      desc: 'Un mismo cliente puede reservar más de una hora (ej. corte el viernes y barba el sábado).',
      estado: _ok(e.contexto.agendar_automatico, 'Disponible'),
      evidencia: `Clientes con 2+ citas activas: ${e.agenda.clientes_con_varias_citas}\nSi no repite sus datos, se usan los de su otra hora.`,
      acciones: [{ t: 'Agendar otra', primario: true, fn: () => correrPaso(2) }],
    },
    {
      codigo: 'AG-03', grupo: 'ag', titulo: 'Sin choques de horario', actor: 'Sistema',
      desc: 'Ninguna cita nueva o cambiada puede cruzarse con otra reserva, sea de otro cliente o del mismo.',
      estado: _ok(true, 'Validado en backend'),
      evidencia: 'Ocupado por otro → sugiere horas libres.\nChoque con su otra cita → lo avisa.',
      acciones: [
        { t: 'Choque propio', fn: () => correrPaso(3) },
        { t: 'Ocupada (cliente 2)', fn: () => correrPaso(5) },
      ],
    },
    {
      codigo: 'AG-04', grupo: 'ag', titulo: 'Cambiar una cita', actor: 'Usuario final',
      desc: 'Mueve solo la cita indicada; las otras quedan igual. Si no queda claro cuál, el bot pregunta.',
      estado: _ok(e.contexto.agendar_automatico, 'Disponible'),
      evidencia: 'Por servicio ("cambia el degradado…"), por número de la lista o por #id (IA).',
      acciones: [
        { t: 'Cambiar una', primario: true, fn: () => correrPaso(4) },
        { t: '¿Cuál?', fn: () => correrPaso(6) },
        { t: 'Responder "2"', fn: () => correrPaso(7) },
      ],
    },
    {
      codigo: 'AG-05', grupo: 'ag', titulo: 'Cancelar y recordatorios', actor: 'Usuario final',
      desc: 'Cancela una cita específica. Recordatorio al agendar y 24 h antes; confirmación 1 h antes.',
      estado: _ok(true, 'Disponible'),
      evidencia: 'Los recordatorios salen por WhatsApp y correo a los datos de contacto de cada cita.',
      acciones: [
        { t: 'Cancelar una', primario: true, fn: () => correrPaso(9) },
        { t: 'Ir a Agenda', fn: () => cambiarVista('agenda') },
      ],
    },
    {
      codigo: 'RNF-01', grupo: 'rnf', titulo: 'Latencia máxima', actor: 'Sistema',
      desc: `Del mensaje entrante a la respuesta enviada: menos de ${lat.limite_s} s.`,
      estado: lat.total ? _ok(lat.dentro_del_limite_pct >= 95, `${lat.dentro_del_limite_pct}% bajo ${lat.limite_s} s`, `${lat.dentro_del_limite_pct}% bajo ${lat.limite_s} s`) : _ok(false, '', 'Sin datos'),
      evidencia: lat.total ? `Últimos 7 días: ${lat.total} respuestas\nPromedio ${lat.promedio_s} s · máximo ${lat.maximo_s} s` : 'Envía mensajes en el simulador para medir.',
      acciones: [{ t: 'Medir ahora', fn: () => enviarMensajeDemo('Hola, ¿atienden hoy?') }],
    },
    {
      codigo: 'RNF-02', grupo: 'rnf', titulo: 'Arquitectura multitenant', actor: 'Sistema',
      desc: 'Cada consulta valida que el negocio pertenezca al administrador del token.',
      estado: _ok(true, 'Aislado'),
      evidencia: e.id_otro_negocio ? `Prueba: leer el negocio #${e.id_otro_negocio} (de otro dueño) debe responder 403.` : 'No hay otro negocio registrado para probar.',
      acciones: e.id_otro_negocio ? [{ t: 'Intentar acceder', primario: true, fn: probarMultitenant }] : [],
    },
    {
      codigo: 'RNF-03', grupo: 'rnf', titulo: 'Diseño responsivo', actor: 'Administrador',
      desc: 'El Dashboard se adapta a escritorio y a celulares.',
      estado: _ok(true, 'Responsivo'),
      evidencia: `Ancho actual de la ventana: ${window.innerWidth}px`,
      acciones: [{ t: 'Vista de celular', primario: true, fn: abrirVistaMovil }],
    },
    {
      codigo: 'RNF-04', grupo: 'rnf', titulo: 'Disponibilidad 24/7', actor: 'Sistema',
      desc: 'Backend en la nube, sin depender del computador del barbero.',
      estado: '<span class="pill pill-muted" id="casoUptime">Sin comprobar</span>',
      evidencia: `Servidor: ${window.location.host}\nHora del servidor (UTC): ${e.servidor_hora_utc}`,
      acciones: [{ t: 'Comprobar /status', primario: true, fn: comprobarServicio }],
    },
  ];
}

function pintarCasos() {
  const e = casos.evidencia;
  if (!e) return;
  const lista = definicionCasos(e).filter((c) => !casos.grupo || c.grupo === casos.grupo);
  casos._acciones = [];
  $('casosGrid').innerHTML = lista.map((c) => `
    <article class="caso">
      <div class="caso-top"><span class="caso-codigo">${c.codigo}</span>${c.estado}</div>
      <h3>${escapeHtml(c.titulo)}</h3>
      <div class="caso-actor">Actor: ${escapeHtml(c.actor)}</div>
      <p>${escapeHtml(c.desc)}</p>
      <div class="caso-evidencia">${escapeHtml(c.evidencia)}</div>
      <div class="caso-acciones">${c.acciones.map((a) => {
        casos._acciones.push(a.fn);
        return `<button class="btn btn-sm ${a.primario ? 'btn-primary' : ''}" data-accion="${casos._acciones.length - 1}">${escapeHtml(a.t)}</button>`;
      }).join('')}</div>
    </article>`).join('');
}

async function actualizarEvidencia() {
  try {
    casos.evidencia = await api.demoEstado(estado.negocio.id_negocio);
    pintarCasos();
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

async function correrPaso(i) {
  const paso = guionDemo()[i];
  if (!paso) return;
  $('simThread').scrollIntoView({ behavior: 'smooth', block: 'center' });
  await enviarMensajeDemo(paso.texto, paso.tel);
  actualizarEvidencia();
}

/* -------------------------------- acciones -------------------------------- */

async function alternarBotDesdeCasos() {
  await alternarBot(); // misma función que el interruptor del Resumen
  actualizarEvidencia();
}

async function probarIaDesdeCasos() {
  abrirModalCasos('Probar conexión con la IA (RF-08)', '<p class="text-muted">Probando… (puede tardar hasta 15 s)</p>');
  try {
    const r = await api.probarIa(estado.negocio.id_negocio);
    $('modalCasosCuerpo').innerHTML = `
      <p>${r.funciona ? '✅ La IA está funcionando.' : '❌ La IA no respondió: el bot contesta en modo sin IA con los datos del negocio.'}</p>
      <pre>${r.intentos.map((i) => `${i.ok ? '✓' : '✗'} ${i.proveedor} / ${i.modelo} — ${i.detalle}${i.segundos != null ? ` (${i.segundos} s)` : ''}`).map(escapeHtml).join('\n')}</pre>`;
  } catch (error) {
    $('modalCasosCuerpo').innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
}

async function probarMultitenant() {
  const otro = casos.evidencia?.id_otro_negocio;
  try {
    await apiFetch(`/negocio/${otro}/contexto`);
    abrirModalCasos('RNF-02 · Multitenant', '<p>⚠️ Se pudo leer otro negocio: revisar permisos.</p>');
  } catch (error) {
    abrirModalCasos('RNF-02 · Multitenant',
      `<p>✅ Acceso bloqueado. Se pidió <code>GET /negocio/${otro}/contexto</code> con tu sesión y el servidor respondió:</p>
       <pre>${escapeHtml(error.message)}</pre>`);
  }
}

function abrirVistaMovil() {
  abrirModalCasos('RNF-03 · Dashboard en un celular (390 px)',
    `<div class="casos-movil"><iframe src="${window.location.pathname}" title="Dashboard en ancho de celular"></iframe></div>`);
}

async function comprobarServicio() {
  const inicio = performance.now();
  try {
    const r = await api.estadoServicio();
    const ms = Math.round(performance.now() - inicio);
    const pill = $('casoUptime');
    if (pill) {
      pill.className = 'pill pill-success';
      pill.textContent = `✓ ${r.estado} (${ms} ms)`;
    }
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

/* ---------------------------------- carga ---------------------------------- */

async function cargarCasosDeUso() {
  if (!estado.servicios?.length) {
    try {
      const ctx = await api.obtenerContexto(estado.negocio.id_negocio);
      estado.servicios = ctx.servicios || [];
    } catch (_) { /* el guion usa nombres genéricos */ }
  }
  pintarGuion();
  seleccionarClienteDemo(casos.telefono);
  actualizarEvidencia();
}

$('simForm').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const texto = $('simTexto').value;
  $('simTexto').value = '';
  enviarMensajeDemo(texto).then(actualizarEvidencia);
});
$('simTexto').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault();
    $('simForm').requestSubmit();
  }
});
$('simGuion').addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-paso]');
  if (btn) correrPaso(Number(btn.dataset.paso));
});
$$('[data-sim-tel]').forEach((b) => b.addEventListener('click', () => seleccionarClienteDemo(b.dataset.simTel)));
$('casosGrid').addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-accion]');
  if (btn) casos._acciones[Number(btn.dataset.accion)]?.();
});
$('casosFiltros').addEventListener('click', (ev) => {
  const chip = ev.target.closest('[data-grupo]');
  if (!chip) return;
  casos.grupo = chip.dataset.grupo;
  $$('#casosFiltros .chip').forEach((c) => c.classList.toggle('active', c === chip));
  pintarCasos();
});
$('btnCasosActualizar').addEventListener('click', actualizarEvidencia);
$('btnSimPrompt').addEventListener('click', verPromptDemo);
$('btnSimReiniciar').addEventListener('click', async () => {
  const ok = await confirmar({
    titulo: '¿Reiniciar la demo?',
    texto: `Se cancelan las citas del ${TELEFONOS_DEMO[casos.telefono].nombre} y se cierra su conversación.`,
    boton: 'Reiniciar',
  });
  if (!ok) return;
  try {
    const r = await api.demoReiniciar(estado.negocio.id_negocio, casos.telefono);
    mostrarToast(`Listo: ${r.citas_canceladas} cita(s) cancelada(s)`);
    cargarConversacionDemo();
    actualizarEvidencia();
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
});
$('btnCerrarModalCasos').addEventListener('click', cerrarModalCasos);
$('modalCasos').addEventListener('click', (ev) => { if (ev.target === $('modalCasos')) cerrarModalCasos(); });
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && $('modalCasos').classList.contains('open')) cerrarModalCasos();
});
