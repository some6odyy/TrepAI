/* ==========================================================================
   TrepAI — Dashboard
   Sin framework: el stack del proyecto es HTML/CSS/JS + Bootstrap según el
   informe, y aquí usamos vanilla JS porque el Dashboard es un solo negocio
   por sesión — no justifica el peso de un framework de componentes.
   ========================================================================== */

const estado = {
  administrador: null,
  negocio: null,
  servicios: [],
  citas: [],
  filtroEstadoCita: '',
  conversaciones: [],
  conversacionActiva: null,
};

/* ---------------------------- Utilidades UI ---------------------------- */

function mostrarToast(mensaje, tipo = 'info') {
  const stack = document.getElementById('toastStack');
  const toast = document.createElement('div');
  toast.className = `toast ${tipo === 'error' ? 'error' : ''}`;
  toast.textContent = mensaje;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 4500);
}

function formatoCLP(valor) {
  return new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(valor);
}

function formatoFechaHora(iso) {
  return new Date(iso).toLocaleString('es-CL', { dateStyle: 'short', timeStyle: 'short' });
}

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function formatoHora(iso) {
  return new Date(iso).toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit' });
}

function fechaLocalISO(d = new Date()) {
  const z = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
}

function iniciales(texto) {
  const partes = String(texto || '?').replace(/[^\p{L}\s]/gu, '').trim().split(/\s+/).filter(Boolean);
  if (partes.length === 0) return '#';
  return (partes[0][0] + (partes[1]?.[0] || '')).toUpperCase();
}

const icono = (id) => `<svg class="i"><use href="#i-${id}"/></svg>`;

/* Diálogo de confirmación propio (evita el confirm() nativo) */
function confirmar({ titulo = '¿Eliminar?', texto = 'Esta acción no se puede deshacer.', boton = 'Eliminar' } = {}) {
  return new Promise((resolve) => {
    const modal = $('modalConfirmar');
    $('confirmTitulo').textContent = titulo;
    $('confirmTexto').textContent = texto;
    $('confirmSi').textContent = boton;
    modal.classList.add('open');
    $('confirmSi').focus();
    const cerrar = (valor) => {
      modal.classList.remove('open');
      $('confirmSi').onclick = $('confirmNo').onclick = modal.onclick = null;
      document.removeEventListener('keydown', onKey);
      resolve(valor);
    };
    const onKey = (e) => { if (e.key === 'Escape') cerrar(false); };
    $('confirmSi').onclick = () => cerrar(true);
    $('confirmNo').onclick = () => cerrar(false);
    modal.onclick = (e) => { if (e.target === modal) cerrar(false); };
    document.addEventListener('keydown', onKey);
  });
}

async function manejarEnvio(formulario, accion) {
  const boton = formulario.querySelector('button[type="submit"]');
  const textoOriginal = boton.textContent;
  boton.disabled = true;
  boton.textContent = 'Guardando…';
  try {
    await accion();
  } catch (error) {
    mostrarToast(error.message, 'error');
  } finally {
    boton.disabled = false;
    boton.textContent = textoOriginal;
  }
}

/* ------------------------------ Pantallas ------------------------------- */

function mostrarPantallaLogin() {
  document.getElementById('authScreen').style.display = 'grid';
  document.getElementById('appShell').classList.remove('visible');
}

function mostrarPantallaDashboard() {
  document.getElementById('authScreen').style.display = 'none';
  document.getElementById('appShell').classList.add('visible');
}

function cambiarVista(nombreVista) {
  document.querySelectorAll('.nav-item').forEach((el) => {
    el.classList.toggle('active', el.dataset.view === nombreVista);
  });
  document.querySelectorAll('.view').forEach((el) => {
    el.classList.toggle('active', el.id === `view${capitalizar(nombreVista)}`);
  });

  if (nombreVista === 'resumen') cargarEstadisticasResumen();
  if (nombreVista === 'contexto') cargarContexto();
  if (nombreVista === 'agenda') cargarAgenda();
  if (nombreVista === 'historial') cargarConversaciones();
}

function capitalizar(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function actualizarSaludo() {
  const hora = new Date().getHours();
  const saludo = hora < 12 ? 'Buen día' : hora < 20 ? 'Buenas tardes' : 'Buenas noches';
  document.getElementById('saludoTitulo').textContent = saludo;

  const fecha = new Date().toLocaleDateString('es-CL', { weekday: 'long', day: 'numeric', month: 'long' });
  const fechaCapitalizada = fecha.charAt(0).toUpperCase() + fecha.slice(1);
  document.getElementById('saludoFecha').textContent = `${estado.negocio.nombre_negocio} · ${fechaCapitalizada}`;
}

document.querySelectorAll('[data-view-link]').forEach((btn) => {
  btn.addEventListener('click', () => cambiarVista(btn.dataset.viewLink));
});

/* -------------------------------- Auth ---------------------------------- */

document.querySelectorAll('.auth-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.auth-tab').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    const esLogin = tab.dataset.tab === 'login';
    document.getElementById('formLogin').classList.toggle('hidden', !esLogin);
    document.getElementById('formRegistro').classList.toggle('hidden', esLogin);
    ocultarErrorAuth();
  });
});

function ocultarErrorAuth() {
  document.getElementById('authError').classList.add('hidden');
}
function mostrarErrorAuth(mensaje) {
  const el = document.getElementById('authError');
  el.textContent = mensaje;
  el.classList.remove('hidden');
}

document.getElementById('formLogin').addEventListener('submit', async (e) => {
  e.preventDefault();
  ocultarErrorAuth();
  try {
    const { access_token } = await api.login({
      correo: document.getElementById('loginCorreo').value,
      contrasena: document.getElementById('loginContrasena').value,
    });
    Auth.setToken(access_token);
    await iniciarSesionExitosa();
  } catch (error) {
    mostrarErrorAuth(error.message);
  }
});

document.getElementById('formRegistro').addEventListener('submit', async (e) => {
  e.preventDefault();
  ocultarErrorAuth();
  try {
    await api.registrar({
      nombre: document.getElementById('regNombre').value,
      correo: document.getElementById('regCorreo').value,
      contrasena: document.getElementById('regContrasena').value,
    });
    const { access_token } = await api.login({
      correo: document.getElementById('regCorreo').value,
      contrasena: document.getElementById('regContrasena').value,
    });
    Auth.setToken(access_token);
    await iniciarSesionExitosa();
  } catch (error) {
    mostrarErrorAuth(error.message);
  }
});

document.getElementById('btnLogout').addEventListener('click', () => {
  Auth.clearToken();
  estado.administrador = null;
  estado.negocio = null;
  mostrarPantallaLogin();
});

async function iniciarSesionExitosa() {
  estado.administrador = await api.yo();
  document.getElementById('sidebarCorreo').textContent = estado.administrador.correo;

  const negocios = await api.listarNegocios();
  if (negocios.length === 0) {
    // Primer uso: creamos el negocio con datos mínimos para poder seguir.
    estado.negocio = await api.crearNegocio({ nombre_negocio: 'Mi negocio' });
    mostrarToast('Creamos tu negocio — completa el perfil en "Resumen".');
  } else {
    estado.negocio = negocios[0];
  }

  document.getElementById('sidebarNegocio').textContent = estado.negocio.nombre_negocio;
  document.getElementById('topbarNegocio').textContent = estado.negocio.nombre_negocio;
  actualizarSaludo();
  rellenarFormularioPerfil();
  await refrescarEstadoWhatsapp();
  cargarEstadisticasResumen();
  mostrarPantallaDashboard();
}

async function cargarEstadisticasResumen() {
  try {
    const hoy = new Date().toISOString().slice(0, 10);
    const [citasHoy, todasLasCitas, conversaciones, contexto] = await Promise.all([
      api.listarCitas(estado.negocio.id_negocio, hoy),
      api.listarCitas(estado.negocio.id_negocio),
      api.listarConversaciones(estado.negocio.id_negocio),
      api.obtenerContexto(estado.negocio.id_negocio),
    ]);
    estado.servicios = contexto.servicios || [];

    document.getElementById('statCitasHoy').textContent = citasHoy.length;
    document.getElementById('statCitasPendientes').textContent =
      todasLasCitas.filter((c) => c.estado_cita === 'pendiente').length;
    document.getElementById('statConversaciones').textContent = conversaciones.length;
    document.getElementById('statServicios').textContent = estado.servicios.length;
  } catch (error) {
    // Las estadísticas son un plus visual — si fallan, no interrumpimos el resto del Dashboard.
    console.warn('No se pudieron cargar las estadísticas del resumen:', error.message);
  }
}

/* ------------------------------ Navegación ------------------------------- */

document.querySelectorAll('.nav-item').forEach((item) => {
  item.addEventListener('click', () => cambiarVista(item.dataset.view));
});

/* ------------------------- Resumen: perfil + bot ------------------------- */

function rellenarFormularioPerfil() {
  document.getElementById('perfilNombre').value = estado.negocio.nombre_negocio || '';
  document.getElementById('perfilTelefono').value = estado.negocio.telefono || '';
  document.getElementById('perfilDireccion').value = estado.negocio.direccion || '';
  document.getElementById('perfilHorario').value = estado.negocio.horario || '';
  actualizarInterruptorBot(estado.negocio.estado_bot);
}

function actualizarInterruptorBot(activo) {
  const switchEl = document.getElementById('botSwitch');
  const titulo = document.getElementById('signTitle');
  const card = document.getElementById('botCard');
  switchEl.classList.toggle('on', activo);
  switchEl.setAttribute('aria-checked', String(activo));
  card.classList.toggle('off', !activo);
  titulo.textContent = activo ? 'Bot encendido' : 'Bot apagado';
}

async function alternarBot() {
  const nuevoEstado = !estado.negocio.estado_bot;
  try {
    const resultado = await api.cambiarEstadoBot(estado.negocio.id_negocio, nuevoEstado);
    estado.negocio.estado_bot = resultado.estado_bot;
    actualizarInterruptorBot(resultado.estado_bot);
    mostrarToast(resultado.estado_bot ? 'Bot encendido' : 'Bot apagado');
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

document.getElementById('botSwitch').addEventListener('click', alternarBot);
document.getElementById('botSwitch').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); alternarBot(); }
});

document.getElementById('formPerfil').addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    estado.negocio = await api.actualizarPerfil(estado.negocio.id_negocio, {
      nombre_negocio: document.getElementById('perfilNombre').value,
      telefono: document.getElementById('perfilTelefono').value || null,
      direccion: document.getElementById('perfilDireccion').value || null,
      horario: document.getElementById('perfilHorario').value || null,
    });
    document.getElementById('sidebarNegocio').textContent = estado.negocio.nombre_negocio;
    mostrarToast('Perfil actualizado');
  });
});

async function refrescarEstadoWhatsapp() {
  const info = await api.estadoWhatsapp(estado.negocio.id_negocio);
  const badge = document.getElementById('whatsappBadge');
  badge.textContent = info.conectado ? `Conectado (${info.phone_number_id})` : 'Sin conectar';
  badge.className = `badge ${info.conectado ? 'badge-success' : 'badge-muted'}`;
  // El botón de desvincular solo tiene sentido si hay un número conectado.
  document.getElementById('btnDesvincularWhatsapp').hidden = !info.conectado;
}

document.getElementById('btnDesvincularWhatsapp').addEventListener('click', async (e) => {
  const confirmado = confirm(
    '¿Desvincular el número de WhatsApp?\n\n'
    + 'TrepAI dejará de responder mensajes en ese número y el bot quedará apagado. '
    + 'Puedes volver a conectarlo cuando quieras con el Phone Number ID y el token.'
  );
  if (!confirmado) return;

  const boton = e.currentTarget;
  boton.disabled = true;
  try {
    await api.desvincularWhatsapp(estado.negocio.id_negocio);
    // El backend también apaga el bot: reflejarlo en el interruptor del Resumen.
    estado.negocio.estado_bot = false;
    actualizarInterruptorBot(false);
    document.getElementById('waPhoneId').value = '';
    document.getElementById('waToken').value = '';
    await refrescarEstadoWhatsapp();
    mostrarToast('WhatsApp desvinculado');
  } catch (error) {
    mostrarToast(error.message, 'error');
  } finally {
    boton.disabled = false;
  }
});

document.getElementById('formWhatsapp').addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    await api.conectarWhatsapp(estado.negocio.id_negocio, {
      phone_number_id: document.getElementById('waPhoneId').value,
      access_token: document.getElementById('waToken').value,
    });
    document.getElementById('waToken').value = '';
    await refrescarEstadoWhatsapp();
    mostrarToast('WhatsApp conectado');
  });
});

/* --------------------------- Contexto IA --------------------------------- */

async function cargarContexto() {
  try {
    const contexto = await api.obtenerContexto(estado.negocio.id_negocio);
    document.getElementById('ctxReglas').value = contexto.reglas_negocio || '';
    document.getElementById('ctxInstrucciones').value = contexto.instrucciones || '';
    estado.servicios = contexto.servicios || [];
    renderizarServicios();
    actualizarToggleSistema('toggleCatalogo', contexto.consultar_catalogo);
    actualizarToggleSistema('toggleAgendaAuto', contexto.agendar_automatico);
    seleccionarMotorIa(contexto.ai_provider, contexto.ai_model);
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

function renderizarServicios() {
  const tbody = document.getElementById('tablaServicios');
  if (estado.servicios.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="table-empty">Todavía no agregas servicios al catálogo.</td></tr>`;
    return;
  }
  tbody.innerHTML = estado.servicios.map((s) => `
    <tr>
      <td>${escapeHtml(s.nombre_servicio)}${s.descripcion ? `<br><span class="text-muted" style="font-size:12px">${escapeHtml(s.descripcion)}</span>` : ''}</td>
      <td class="mono">${formatoCLP(s.precio)}</td>
      <td class="mono">${s.duracion_estimada ?? '—'} min</td>
      <td><button class="btn btn-sm btn-danger" data-eliminar-servicio="${s.id_servicio}">Eliminar</button></td>
    </tr>
  `).join('');

  tbody.querySelectorAll('[data-eliminar-servicio]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        await api.eliminarServicio(estado.negocio.id_negocio, btn.dataset.eliminarServicio);
        await cargarContexto();
        actualizarSelectServicios();
        mostrarToast('Servicio eliminado');
      } catch (error) {
        mostrarToast(error.message, 'error');
      }
    });
  });

  actualizarSelectServicios();
}

function actualizarSelectServicios() {
  const select = document.getElementById('citaServicio');
  select.innerHTML = estado.servicios.map((s) =>
    `<option value="${s.id_servicio}">${escapeHtml(s.nombre_servicio)} — ${formatoCLP(s.precio)}</option>`
  ).join('') || '<option disabled selected>Agrega un servicio primero</option>';
}

document.getElementById('formPersonificacion').addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    await api.actualizarContexto(estado.negocio.id_negocio, {
      instrucciones: document.getElementById('ctxInstrucciones').value,
    });
    mostrarToast('Personificación guardada');
  });
});

document.getElementById('formReglas').addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    await api.actualizarContexto(estado.negocio.id_negocio, {
      reglas_negocio: document.getElementById('ctxReglas').value,
    });
    mostrarToast('Reglas del local guardadas');
  });
});

/* ---- Arquitectura del Asistente: bloques (acordeón) ---- */

const PANEL_POR_BLOQUE = { personificacion: 'panelPersonificacion', catalogo: 'panelCatalogo', reglas: 'panelReglas', motorIa: 'panelMotorIa' };

document.querySelectorAll('.block-card[data-block]').forEach((card) => {
  card.addEventListener('click', () => {
    const panelId = PANEL_POR_BLOQUE[card.dataset.block];
    const yaAbierto = card.classList.contains('open');

    document.querySelectorAll('.block-card[data-block]').forEach((c) => c.classList.remove('open'));
    document.querySelectorAll('.accordion-panel').forEach((p) => p.classList.remove('open'));

    if (!yaAbierto) {
      card.classList.add('open');
      document.getElementById(panelId).classList.add('open');
      document.getElementById(panelId).scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  });
});

document.getElementById('btnAnadirBloque').addEventListener('click', () => {
  mostrarToast('Los bloques personalizados llegan en una próxima versión');
});

/* ---- Funciones de sistema (webhooks) ---- */

function actualizarToggleSistema(id, activo) {
  document.getElementById(id).classList.toggle('on', !!activo);
}

function conectarToggleSistema(id, campo, etiqueta) {
  const el = document.getElementById(id);
  el.addEventListener('click', async () => {
    const nuevoValor = !el.classList.contains('on');
    el.classList.toggle('on', nuevoValor); // respuesta visual inmediata
    try {
      await api.actualizarContexto(estado.negocio.id_negocio, { [campo]: nuevoValor });
      mostrarToast(`${etiqueta} ${nuevoValor ? 'activado' : 'desactivado'}`);
    } catch (error) {
      el.classList.toggle('on', !nuevoValor); // revertir si falló
      mostrarToast(error.message, 'error');
    }
  });
}

conectarToggleSistema('toggleCatalogo', 'consultar_catalogo', 'Consultar catálogo');
conectarToggleSistema('toggleAgendaAuto', 'agendar_automatico', 'Agendar cita automática');

/* ---- Motor de IA: proveedor + modelo ---- */

document.querySelectorAll('.provider-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.provider-tab').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('listaModelosGemini').classList.toggle('hidden', tab.dataset.provider !== 'gemini');
    document.getElementById('listaModelosOpenai').classList.toggle('hidden', tab.dataset.provider !== 'openai');
  });
});

function seleccionarMotorIa(provider, model) {
  document.querySelectorAll('.provider-tab').forEach((t) => t.classList.toggle('active', t.dataset.provider === provider));
  document.getElementById('listaModelosGemini').classList.toggle('hidden', provider !== 'gemini');
  document.getElementById('listaModelosOpenai').classList.toggle('hidden', provider !== 'openai');
  document.querySelectorAll('.model-option').forEach((opt) => {
    opt.classList.toggle('selected', opt.dataset.provider === provider && opt.dataset.model === model);
  });
}

document.querySelectorAll('.model-option').forEach((opt) => {
  opt.addEventListener('click', async () => {
    if (opt.classList.contains('selected')) return;

    const anterior = document.querySelector('.model-option.selected');
    document.querySelectorAll('.model-option').forEach((o) => o.classList.remove('selected'));
    opt.classList.add('selected'); // respuesta visual inmediata

    try {
      await api.actualizarContexto(estado.negocio.id_negocio, {
        ai_provider: opt.dataset.provider,
        ai_model: opt.dataset.model,
      });
      mostrarToast(`Motor de IA actualizado a ${opt.querySelector('.model-name').firstChild.textContent.trim()}`);
    } catch (error) {
      opt.classList.remove('selected');
      if (anterior) anterior.classList.add('selected');
      mostrarToast(error.message, 'error');
    }
  });
});

document.getElementById('formServicio').addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    await api.agregarServicio(estado.negocio.id_negocio, {
      nombre_servicio: document.getElementById('servNombre').value,
      precio: Number(document.getElementById('servPrecio').value),
      duracion_estimada: Number(document.getElementById('servDuracion').value),
      descripcion: document.getElementById('servDescripcion').value || null,
    });
    e.target.reset();
    await cargarContexto();
    mostrarToast('Servicio agregado');
    cargarEstadisticasResumen();
  });
});

/* ---------------------------------- Agenda ---------------------------------- */

const ESTADOS_CITA = ['pendiente', 'confirmada', 'completada', 'cancelada', 'expirada'];

async function cargarAgenda() {
  if (estado.servicios.length === 0) {
    // Necesitamos el catálogo para el <select> de "agendar manualmente".
    try { await cargarContexto(); } catch (_) { /* se maneja abajo igual */ }
  } else {
    actualizarSelectServicios();
  }
  try {
    const fecha = $('filtroFecha').value;
    $('btnLimpiarFecha').classList.toggle('hidden', !fecha);
    estado.citas = await api.listarCitas(estado.negocio.id_negocio, fecha || null);
    renderizarAgenda();
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

function renderizarAgenda() {
  // Contadores por estado en los chips de filtro
  $$('#filtroEstados [data-count]').forEach((b) => {
    const k = b.dataset.count;
    b.textContent = k ? estado.citas.filter((c) => c.estado_cita === k).length || '' : estado.citas.length || '';
  });

  const cont = $('tablaAgenda');
  const citas = estado.citas
    .filter((c) => !estado.filtroEstadoCita || c.estado_cita === estado.filtroEstadoCita)
    .sort((a, b) => `${a.fecha_cita}${a.hora_cita}`.localeCompare(`${b.fecha_cita}${b.hora_cita}`));

  if (citas.length === 0) {
    cont.innerHTML = `
      <div class="empty">
        ${icono('cal')}
        <b>Sin citas por aquí</b>
        <span>No hay citas para mostrar.</span>
      </div>`;
    return;
  }

  // Agrupar por fecha
  const grupos = citas.reduce((acc, c) => ((acc[c.fecha_cita] ||= []).push(c), acc), {});
  const hoy = fechaLocalISO();

  cont.innerHTML = Object.entries(grupos).map(([fecha, lista]) => {
    const d = new Date(`${fecha}T12:00:00`);
    return `
      <div class="day">
        <div class="day-label ${fecha === hoy ? 'today' : ''}">
          <div class="day-wd">${fecha === hoy ? 'Hoy' : d.toLocaleDateString('es-CL', { weekday: 'short' })}</div>
          <div class="day-num">${d.getDate()}</div>
          <div class="day-mo">${d.toLocaleDateString('es-CL', { month: 'short' })}</div>
        </div>
        <div class="day-items">
          ${lista.map((c) => `
            <article class="cita st-${c.estado_cita} ${c.estado_cita}">
              <div class="cita-hora">${c.hora_cita.slice(0, 5)}</div>
              <div class="cita-who">
                <b>${escapeHtml(c.nombre_cliente || c.telefono_cliente)}</b>
                <small>${escapeHtml(c.nombre_servicio)}${c.nombre_cliente ? ` · <span class="mono">${escapeHtml(c.telefono_cliente)}</span>` : ''}</small>
              </div>
              <select class="status-select st-${c.estado_cita}" data-cambiar-estado="${c.id_agenda}" aria-label="Estado de la cita">
                ${ESTADOS_CITA.map((e) => `<option value="${e}" ${e === c.estado_cita ? 'selected' : ''}>${e}</option>`).join('')}
              </select>
              <button class="icon-btn danger" data-eliminar-cita="${c.id_agenda}" title="Eliminar">${icono('trash')}</button>
            </article>`).join('')}
        </div>
      </div>`;
  }).join('');

  $$('[data-cambiar-estado]', cont).forEach((select) => {
    select.addEventListener('change', async () => {
      try {
        await api.actualizarEstadoCita(estado.negocio.id_negocio, select.dataset.cambiarEstado, select.value);
        mostrarToast('Estado actualizado');
        cargarAgenda();
      } catch (error) {
        mostrarToast(error.message, 'error');
        cargarAgenda();
      }
    });
  });

  $$('[data-eliminar-cita]', cont).forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!(await confirmar({ titulo: '¿Eliminar esta cita?' }))) return;
      try {
        await api.eliminarCita(estado.negocio.id_negocio, btn.dataset.eliminarCita);
        cargarAgenda();
        mostrarToast('Cita eliminada');
        cargarEstadisticasResumen();
      } catch (error) {
        mostrarToast(error.message, 'error');
      }
    });
  });
}

$$('#filtroEstados .chip').forEach((chip) => {
  chip.addEventListener('click', () => {
    estado.filtroEstadoCita = chip.dataset.estado;
    $$('#filtroEstados .chip').forEach((c) => c.classList.toggle('active', c === chip));
    renderizarAgenda();
  });
});

$('filtroFecha').addEventListener('change', cargarAgenda);
$('btnHoy').addEventListener('click', () => { $('filtroFecha').value = fechaLocalISO(); cargarAgenda(); });
$('btnLimpiarFecha').addEventListener('click', () => { $('filtroFecha').value = ''; cargarAgenda(); });

/* Drawer "Agendar manualmente" */
function abrirDrawer() {
  $('drawerCita').classList.add('open');
  if (!$('citaFecha').value) $('citaFecha').value = $('filtroFecha').value || fechaLocalISO();
  setTimeout(() => $('citaTelefono').focus(), 200);
}
function cerrarDrawer() { $('drawerCita').classList.remove('open'); }

$('btnNuevaCita').addEventListener('click', abrirDrawer);
$$('[data-close-drawer]').forEach((b) => b.addEventListener('click', cerrarDrawer));
$('drawerCita').addEventListener('click', (e) => { if (e.target.id === 'drawerCita') cerrarDrawer(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrarDrawer(); });

$('formCita').addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    await api.crearCita(estado.negocio.id_negocio, {
      telefono_cliente: $('citaTelefono').value,
      nombre_cliente: $('citaNombre').value || null,
      id_servicio: Number($('citaServicio').value),
      fecha_cita: $('citaFecha').value,
      hora_cita: $('citaHora').value,
    });
    e.target.reset();
    cerrarDrawer();
    await cargarAgenda();
    mostrarToast('Cita agendada');
    cargarEstadisticasResumen();
  });
});

/* -------------------------------- Historial -------------------------------- */

async function cargarConversaciones() {
  try {
    estado.conversaciones = await api.listarConversaciones(estado.negocio.id_negocio);
    renderizarConversaciones();
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

function renderizarConversaciones() {
  const cont = $('tablaConversaciones');
  const q = $('buscarConversacion').value.trim().toLowerCase();
  const lista = estado.conversaciones.filter((c) =>
    !q || (c.nombre_cliente || '').toLowerCase().includes(q) || c.telefono_cliente.includes(q));

  if (estado.conversaciones.length === 0) {
    cont.innerHTML = `<div class="conv-empty">Todavía no hay conversaciones registradas.</div>`;
    return;
  }
  if (lista.length === 0) {
    cont.innerHTML = `<div class="conv-empty">Sin resultados para "${escapeHtml(q)}".</div>`;
    return;
  }

  cont.innerHTML = lista.map((c) => {
    const nombre = c.nombre_cliente || c.telefono_cliente;
    return `
      <button class="conv ${estado.conversacionActiva === c.id_conversacion ? 'active' : ''}" data-ver-conversacion="${c.id_conversacion}">
        <span class="avatar avatar-sm">${escapeHtml(iniciales(c.nombre_cliente))}</span>
        <span class="conv-body">
          <span class="conv-top"><b>${escapeHtml(c.nombre_cliente || '(sin nombre)')}</b><time>${formatoFechaHora(c.fecha_inicio)}</time></span>
          <span class="conv-bottom">
            <small>${escapeHtml(c.telefono_cliente)}</small>
            <span style="display:flex;gap:6px;align-items:center">
              <span class="pill ${c.estado === 'abierta' ? 'pill-success' : 'pill-muted'}">${escapeHtml(c.estado)}</span>
              <span class="count" title="Mensajes">${c.total_mensajes}</span>
            </span>
          </span>
        </span>
      </button>`;
  }).join('');

  $$('[data-ver-conversacion]', cont).forEach((fila) => {
    fila.addEventListener('click', () => {
      const conv = estado.conversaciones.find((c) => String(c.id_conversacion) === fila.dataset.verConversacion);
      abrirMensajes(conv);
    });
  });
}

$('buscarConversacion').addEventListener('input', renderizarConversaciones);

async function abrirMensajes(conv) {
  try {
    const mensajes = await api.listarMensajes(estado.negocio.id_negocio, conv.id_conversacion);
    estado.conversacionActiva = conv.id_conversacion;
    const nombre = conv.nombre_cliente || conv.telefono_cliente;
    $('mensajesTitulo').textContent = `Conversación con ${nombre}`;
    $('chatSub').textContent = `${conv.telefono_cliente} · ${conv.total_mensajes} mensajes`;
    $('chatAvatar').textContent = iniciales(conv.nombre_cliente);

    let diaAnterior = '';
    $('chatThread').innerHTML = mensajes.length === 0
      ? `<div class="chat-empty">${icono('chat')}<p>Esta conversación no tiene mensajes.</p></div>`
      : mensajes.map((m, i) => {
        const dia = new Date(m.fecha_hora).toLocaleDateString('es-CL', { weekday: 'long', day: 'numeric', month: 'long' });
        const separador = dia !== diaAnterior ? `<div class="chat-day">${capitalizar(dia)}</div>` : '';
        diaAnterior = dia;
        return `${separador}
          <div class="bubble ${m.emisor === 'bot' ? 'bot' : 'cliente'}" style="animation-delay:${Math.min(i, 12) * 25}ms">${escapeHtml(m.contenido)}<time>${formatoHora(m.fecha_hora)}</time></div>`;
      }).join('');

    $('inbox').classList.add('reading');
    renderizarConversaciones();
    const hilo = $('chatThread');
    hilo.scrollTop = hilo.scrollHeight;
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

$('btnCerrarMensajes').addEventListener('click', () => $('inbox').classList.remove('reading'));

/* -------------------------------- Helpers ---------------------------------- */

function escapeHtml(texto) {
  const div = document.createElement('div');
  div.textContent = texto ?? '';
  return div.innerHTML;
}

/* -------------------------------- Arranque ---------------------------------- */

(async function iniciar() {
  if (Auth.getToken()) {
    try {
      await iniciarSesionExitosa();
      return;
    } catch (_) {
      // El token guardado ya no sirve — apiFetch ya limpió el token y
      // llamó a mostrarPantallaLogin() por el manejo del 401.
    }
  }
  mostrarPantallaLogin();
})();
