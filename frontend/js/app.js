/* ==========================================================================
   TrepAI — Dashboard
   ========================================================================== */

const estado = {
  administrador: null,
  negocio: null,
  servicios: [],
  citas: [],
  filtroEstadoCita: '',
  conversaciones: [],
  conversacionActiva: null,
  // Actualización en vivo
  firmaAgenda: null,
  firmaConversaciones: null,
  avisos: [],
  ultimoAvisoVisto: 0,
  idsCitasConocidas: new Set(),
  mensajesActivos: 0,
  citaEditando: null,
  bloqueos: [],
  clientes: [],
  fichaActiva: null,
};

/* ---------------------------- Utilidades UI ---------------------------- */

function mostrarToast(mensaje, tipo = 'info') {
  const stack = document.getElementById('toastStack');
  if (!stack) return;
  while (stack.children.length >= 2) {
    stack.firstElementChild.remove();
  }
  const toast = document.createElement('div');
  toast.className = `toast ${tipo === 'error' ? 'error' : ''}`;
  toast.textContent = mensaje;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

function formatoCLP(valor) {
  return new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(valor);
}

// El backend guarda las fechas técnicas en UTC sin zona ("2026-11-18T17:46:59");
// sin la "Z" el navegador las leería como hora local y mostraría 3 h de diferencia.
function fechaUTC(iso) {
  if (!iso) return new Date(NaN);
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function formatoFechaHora(iso) {
  return fechaUTC(iso).toLocaleString('es-CL', { dateStyle: 'short', timeStyle: 'short' });
}

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Hoy -> "15:47"; ayer -> "Ayer"; otro día -> "17-11"
function formatoCorto(iso) {
  const d = fechaUTC(iso);
  const hoy = new Date();
  if (d.toDateString() === hoy.toDateString()) return d.toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit', hour12: false });
  const ayer = new Date(hoy); ayer.setDate(hoy.getDate() - 1);
  if (d.toDateString() === ayer.toDateString()) return 'Ayer';
  return d.toLocaleDateString('es-CL', { day: '2-digit', month: '2-digit' });
}

function formatoHora(iso) {
  return fechaUTC(iso).toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit' });
}

function fechaLocalISO(d = new Date()) {
  const z = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
}

// Formato de WhatsApp en las burbujas: *negrita*, _cursiva_, ~tachado~
function formatoWhatsapp(texto) {
  return escapeHtml(texto)
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, '$1<b>$2</b>')
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?:;]|$)/g, '$1<i>$2</i>')
    .replace(/(^|[\s(])~([^~\n]+)~(?=[\s).,!?:;]|$)/g, '$1<s>$2</s>');
}

// "56988335580" -> "+56 9 8833 5580" (otros países: "+" y los dígitos)
function formatoTelefono(tel) {
  const d = String(tel || '').replace(/\D/g, '');
  if (d.length === 11 && d.startsWith('569')) return `+56 9 ${d.slice(3, 7)} ${d.slice(7)}`;
  return d ? `+${d}` : '';
}

// Nombre para mostrar: el del cliente o, si aún no lo sabemos, su número
function nombreVisible(c) {
  return c.nombre_cliente || formatoTelefono(c.telefono_cliente);
}

function iniciales(texto) {
  const partes = String(texto || '?').replace(/[^\p{L}\s]/gu, '').trim().split(/\s+/).filter(Boolean);
  if (partes.length === 0) return '#';
  return (partes[0][0] + (partes[1]?.[0] || '')).toUpperCase();
}

const icono = (id) => `<svg class="i"><use href="#i-${id}"/></svg>`;

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
    // Si la acción cambió el texto del botón (ej. "Verificar y conectar"), se respeta
    if (boton.textContent === 'Guardando…') boton.textContent = textoOriginal;
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

const TITULOS_VISTA = {
  resumen: 'Resumen', contexto: 'Contexto IA', agenda: 'Agenda',
  historial: 'Historial', clientes: 'Clientes', casos: 'Casos de uso', cuenta: 'Mi cuenta',
};

function cambiarVista(nombreVista) {
  document.querySelectorAll('.nav-item').forEach((el) => {
    const activo = el.dataset.view === nombreVista;
    el.classList.toggle('active', activo);
    if (activo) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
  const titulo = document.getElementById('topbarVista');
  if (titulo) titulo.textContent = TITULOS_VISTA[nombreVista] || '';
  document.title = `${TITULOS_VISTA[nombreVista] || 'Dashboard'} · TrepAI`;
  cerrarMenu();
  if (esPantallaAngosta()) window.scrollTo({ top: 0 });
  document.querySelectorAll('.view').forEach((el) => {
    el.classList.toggle('active', el.id === `view${capitalizar(nombreVista)}`);
  });

  if (nombreVista === 'resumen') cargarEstadisticasResumen();
  if (nombreVista === 'contexto') cargarContexto();
  if (nombreVista === 'agenda') cargarAgenda();
  if (nombreVista === 'historial') cargarConversaciones();
  if (nombreVista === 'clientes') cargarClientes();
  if (nombreVista === 'cuenta') rellenarCuenta();
  if (nombreVista === 'casos' && typeof cargarCasosDeUso === 'function') cargarCasosDeUso();
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
  detenerEnVivo();
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
  iniciarEnVivo();
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
    console.warn('No se pudieron cargar las estadísticas del resumen:', error.message);
  }
  cargarTiempoRespuesta();
}

// RNF-01: cuánto tarda el bot en responder (últimos 7 días)
async function cargarTiempoRespuesta() {
  try {
    const t = await api.tiempoRespuesta(estado.negocio.id_negocio, 7);
    const segundos = (v) => `${String(v).replace('.', ',')} s`;
    if (!t.total) {
      $('statLatencia').textContent = '—';
      $('statLatenciaSub').textContent = 'Sin respuestas en 7 días';
      return;
    }
    $('statLatencia').textContent = segundos(t.promedio_s);
    $('statLatenciaSub').textContent = `${String(t.dentro_del_limite_pct).replace('.', ',')} % bajo ${t.limite_s} s · ${t.total} resp.`;
    $('statLatencia').closest('.stat-card').title =
      `Promedio ${segundos(t.promedio_s)} · 95 % en menos de ${segundos(t.p95_s)} · máximo ${segundos(t.maximo_s)} (últimos ${t.dias} días)`;
  } catch (_) { /* el resto del resumen funciona igual */ }
}

/* ------------------------------ Navegación ------------------------------- */

document.querySelectorAll('.nav-item').forEach((item) => {
  item.addEventListener('click', () => cambiarVista(item.dataset.view));
});

/* Menú deslizable en pantallas angostas (≤ 900 px): botón ☰ de la barra superior */
function esPantallaAngosta() { return window.matchMedia('(max-width: 900px)').matches; }

function bloquearScroll() {
  const algoAbierto = document.getElementById('sidebar')?.classList.contains('open')
    || document.querySelector('.drawer-backdrop.open, .modal-backdrop.open');
  document.body.classList.toggle('sin-scroll', Boolean(algoAbierto));
}

function abrirMenu() {
  const sidebar = document.getElementById('sidebar');
  const fondo = document.getElementById('menuBackdrop');
  if (!sidebar || !esPantallaAngosta()) return;
  sidebar.classList.add('open');
  fondo.hidden = false;
  requestAnimationFrame(() => fondo.classList.add('open'));
  document.getElementById('btnMenu').setAttribute('aria-expanded', 'true');
  document.getElementById('btnMenu').setAttribute('aria-label', 'Cerrar menú');
  bloquearScroll();
  (sidebar.querySelector('.nav-item.active') || sidebar.querySelector('.nav-item'))?.focus();
}

function cerrarMenu({ devolverFoco = false } = {}) {
  const sidebar = document.getElementById('sidebar');
  const fondo = document.getElementById('menuBackdrop');
  if (!sidebar || !sidebar.classList.contains('open')) return;
  sidebar.classList.remove('open');
  fondo.classList.remove('open');
  setTimeout(() => { if (!sidebar.classList.contains('open')) fondo.hidden = true; }, 250);
  const boton = document.getElementById('btnMenu');
  boton.setAttribute('aria-expanded', 'false');
  boton.setAttribute('aria-label', 'Abrir menú');
  bloquearScroll();
  if (devolverFoco) boton.focus();
}

document.getElementById('btnMenu')?.addEventListener('click', () => {
  if (document.getElementById('sidebar').classList.contains('open')) cerrarMenu({ devolverFoco: true });
  else abrirMenu();
});
document.getElementById('menuBackdrop')?.addEventListener('click', () => cerrarMenu({ devolverFoco: true }));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') cerrarMenu({ devolverFoco: true });
  // Mantiene el foco dentro del menú abierto (Tab y Shift+Tab)
  const sidebar = document.getElementById('sidebar');
  if (e.key === 'Tab' && sidebar?.classList.contains('open')) {
    const focusables = [...sidebar.querySelectorAll('button, a[href], input, select, textarea')].filter((el) => !el.disabled);
    const primero = focusables[0];
    const ultimo = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === primero) { e.preventDefault(); ultimo.focus(); }
    else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primero.focus(); }
  }
});
// Si la pantalla se agranda con el menú abierto (girar el teléfono), se cierra
window.matchMedia('(max-width: 900px)').addEventListener?.('change', () => { if (!esPantallaAngosta()) cerrarMenu(); });

// Los paneles laterales y diálogos también bloquean el desplazamiento del fondo
new MutationObserver(bloquearScroll).observe(document.body, {
  subtree: true, attributes: true, attributeFilter: ['class'],
});

/* Tablas que en el celular se muestran como tarjetas: cada celda lleva el
   nombre de su columna (data-label), tomado del encabezado de la tabla. */
function etiquetarTabla(tabla) {
  const nombres = [...tabla.querySelectorAll('thead th')].map((th) => th.dataset.etiqueta ?? th.textContent.trim());
  tabla.querySelectorAll('tbody tr').forEach((tr) => {
    [...tr.children].forEach((td, i) => {
      if (!td.hasAttribute('colspan') && td.dataset.label === undefined) td.dataset.label = nombres[i] || '';
    });
  });
}
document.querySelectorAll('table.tabla-resp').forEach((tabla) => {
  etiquetarTabla(tabla);
  new MutationObserver(() => etiquetarTabla(tabla)).observe(tabla.querySelector('tbody'), { childList: true });
});

/* Bloques de Contexto IA: también se abren con el teclado (Enter o espacio) */
document.querySelectorAll('.block-card').forEach((card) => {
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); }
  });
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

/* ------------------------- WhatsApp (2 pasos SMS) ------------------------ */

let pasoVerificacionSms = false;

function reiniciarFormularioWhatsapp() {
  pasoVerificacionSms = false;
  const pasoCodigo = document.getElementById('pasoCodigoSms');
  const btnSubmit = document.querySelector('#formWhatsapp button[type="submit"]');
  if (pasoCodigo) pasoCodigo.classList.add('hidden');
  if (btnSubmit) btnSubmit.textContent = 'Enviar código SMS';
  const inputCodigo = document.getElementById('waCodigoSms');
  if (inputCodigo) inputCodigo.value = '';
}

async function refrescarEstadoWhatsapp() {
  const info = await api.estadoWhatsapp(estado.negocio.id_negocio);
  const badge = document.getElementById('whatsappBadge');
  if (badge) {
    badge.textContent = info.conectado ? `Conectado (${info.phone_number_id})` : 'Sin conectar';
    badge.className = `badge ${info.conectado ? 'badge-success' : 'badge-muted'}`;
  }
  const btnDesvincular = document.getElementById('btnDesvincularWhatsapp');
  if (btnDesvincular) {
    btnDesvincular.hidden = !info.conectado;
  }
  const formWa = document.getElementById('formWhatsapp');
  if (formWa) {
    formWa.hidden = info.conectado;
    if (!info.conectado) {
      const inputNombre = document.getElementById('waNombreVisible');
      if (inputNombre && !inputNombre.value) {
        inputNombre.value = estado.negocio.nombre_negocio || '';
      }
    }
  }
  cargarNumerosWhatsapp();
}

/* Números dados de alta en el WABA de Meta (cupo de 2 sin verificar) */

const ESTADOS_NUMERO_WA = {
  CONNECTED: 'Conectado',
  PENDING: 'Pendiente',
  UNVERIFIED: 'Sin verificar',
  DISCONNECTED: 'Desconectado',
  FLAGGED: 'Marcado',
  RESTRICTED: 'Restringido',
  BANNED: 'Bloqueado',
};

async function cargarNumerosWhatsapp() {
  const tbody = document.getElementById('tablaNumerosWa');
  const cupo = document.getElementById('numerosWaCupo');
  if (!tbody || !estado.negocio) return;

  try {
    const { limite, numeros, url_administrador: urlMeta } = await api.listarNumerosWhatsapp(estado.negocio.id_negocio);
    if (cupo) {
      cupo.textContent = `${numeros.length}/${limite}`;
      cupo.className = `badge ${numeros.length >= limite ? 'badge-danger' : 'badge-muted'}`;
    }
    if (!numeros.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="table-empty">No hay números en la cuenta de Meta</td></tr>';
      return;
    }
    tbody.innerHTML = numeros.map((n) => {
      const estadoTxt = ESTADOS_NUMERO_WA[n.estado] || n.estado || (n.verificacion === 'VERIFIED' ? 'Verificado' : 'Sin verificar');
      const etiquetas = {
        este: '<span class="badge badge-success">Este negocio</span>',
        este_pendiente: '<span class="badge badge-muted">Este negocio · sin verificar</span>',
        otro: '<span class="badge badge-warning">Otro negocio</span>',
      };
      const etiqueta = etiquetas[n.vinculado_a] ? ` ${etiquetas[n.vinculado_a]}` : '';
      let boton = '';
      if (n.se_puede_eliminar) {
        boton = `<a class="btn btn-sm btn-danger" href="${escapeHtml(urlMeta)}" target="_blank" rel="noopener"
             title="Meta solo permite borrar números desde el Administrador de WhatsApp">Eliminar en Meta ↗</a>`;
      } else if (n.vinculado_a === 'este') {
        boton = '<span class="text-muted" style="font-size:12px">Desvincula primero para eliminarlo</span>';
      }
      return `<tr>
        <td>${escapeHtml(n.numero || '—')}${etiqueta}</td>
        <td>${escapeHtml(n.nombre || '—')}</td>
        <td>${escapeHtml(estadoTxt)}</td>
        <td style="text-align:right">${boton}</td>
      </tr>`;
    }).join('');
  } catch (error) {
    if (cupo) cupo.textContent = '';
    tbody.innerHTML = `<tr><td colspan="4" class="table-empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

document.getElementById('btnRefrescarNumerosWa')?.addEventListener('click', cargarNumerosWhatsapp);

document.getElementById('btnDesvincularWhatsapp')?.addEventListener('click', async (e) => {
  const confirmado = await confirmar({
    titulo: '¿Desvincular WhatsApp?',
    texto: 'TrepAI dejará de responder mensajes en este número y el bot quedará apagado.',
    boton: 'Desvincular',
  });
  if (!confirmado) return;

  const boton = e.currentTarget;
  boton.disabled = true;
  try {
    await api.desvincularWhatsapp(estado.negocio.id_negocio);
    estado.negocio.estado_bot = false;
    actualizarInterruptorBot(false);
    document.getElementById('formWhatsapp')?.reset();
    reiniciarFormularioWhatsapp();
    await refrescarEstadoWhatsapp();
    mostrarToast('WhatsApp desvinculado');
  } catch (error) {
    mostrarToast(error.message, 'error');
  } finally {
    boton.disabled = false;
  }
});

document.getElementById('formWhatsapp')?.addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    const idNegocio = estado.negocio.id_negocio;

    if (!pasoVerificacionSms) {
      const telefono = document.getElementById('waTelefono').value.trim();
      const nombreVisible = document.getElementById('waNombreVisible').value.trim() || estado.negocio.nombre_negocio;

      await api.solicitarCodigoWhatsapp(idNegocio, {
        telefono,
        nombre_visible: nombreVisible,
      });

      pasoVerificacionSms = true;
      document.getElementById('pasoCodigoSms').classList.remove('hidden');
      e.target.querySelector('button[type="submit"]').textContent = 'Verificar y conectar';
      document.getElementById('waCodigoSms').focus();
      mostrarToast('Te enviamos un SMS con un código de 6 dígitos');
      cargarNumerosWhatsapp();
      return;
    }

    const codigo = document.getElementById('waCodigoSms').value.trim();
    await api.verificarCodigoWhatsapp(idNegocio, { codigo });

    estado.negocio.estado_bot = true;
    actualizarInterruptorBot(true);
    reiniciarFormularioWhatsapp();
    await refrescarEstadoWhatsapp();
    mostrarToast('¡WhatsApp conectado y bot encendido!');
  });
});

document.getElementById('btnReenviarCodigo')?.addEventListener('click', () => {
  reiniciarFormularioWhatsapp();
  document.getElementById('formWhatsapp')?.requestSubmit();
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

const PANEL_POR_BLOQUE = {
  personificacion: 'panelPersonificacion',
  catalogo: 'panelCatalogo',
  reglas: 'panelReglas',
  motorIa: 'panelMotorIa',
};

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
  if (!el) return;

  el.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (el.dataset.guardando === '1') return;

    const estadoAnterior = el.classList.contains('on');
    const nuevoValor = !estadoAnterior;

    el.dataset.guardando = '1';
    el.style.pointerEvents = 'none';
    el.style.opacity = '0.6';
    el.classList.toggle('on', nuevoValor);

    try {
      await api.actualizarContexto(estado.negocio.id_negocio, { [campo]: nuevoValor });
      mostrarToast(`${etiqueta} ${nuevoValor ? 'activado' : 'desactivado'}`);
    } catch (error) {
      el.classList.toggle('on', estadoAnterior);
      mostrarToast(error.message, 'error');
    } finally {
      delete el.dataset.guardando;
      el.style.pointerEvents = '';
      el.style.opacity = '';
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
    opt.classList.add('selected');

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

/* ---- Probar IA: muestra qué modelo responde y por qué falla cada uno ---- */

$('btnProbarIa').addEventListener('click', async () => {
  const btn = $('btnProbarIa');
  btn.disabled = true;
  $('probarIaEstado').textContent = 'Probando… (puede tardar hasta 15 s)';
  $('probarIaLista').classList.add('hidden');
  try {
    const r = await api.probarIa(estado.negocio.id_negocio);
    $('probarIaEstado').innerHTML = r.funciona
      ? '<b class="ok">✅ La IA está funcionando.</b>'
      : '<b class="no">❌ La IA no respondió.</b> Mientras tanto el bot contesta con los datos de tu negocio.';
    $('probarIaLista').innerHTML = r.intentos.map((i) => `
      <li class="${i.ok ? 'ok' : 'no'}">
        <span class="mono">${escapeHtml(i.proveedor)} / ${escapeHtml(i.modelo)}</span>
        <span>${escapeHtml(i.detalle)}${i.segundos != null ? ` <small>(${i.segundos} s)</small>` : ''}</span>
      </li>`).join('');
    $('probarIaLista').classList.remove('hidden');
  } catch (error) {
    $('probarIaEstado').textContent = error.message;
  } finally {
    btn.disabled = false;
  }
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

const ESTADOS_CITA = ['pendiente', 'confirmada', 'completada', 'no_asistio', 'cancelada', 'expirada'];
const ETIQUETA_ESTADO = {
  pendiente: 'Pendiente', confirmada: 'Confirmada', completada: 'Completada',
  no_asistio: 'No asistió', cancelada: 'Cancelada', expirada: 'Expirada',
};

function badgesNotificacion(c) {
  const b = [];
  if (c.respuesta_cliente === 'si') b.push('<span class="notif ok">Cliente confirmó</span>');
  else if (c.respuesta_cliente === 'no') b.push('<span class="notif no">Cliente canceló</span>');
  else if (c.confirmacion_1h_enviada) b.push('<span class="notif">Esperando confirmación</span>');
  else if (c.recordatorio_24h_enviado && ['pendiente', 'confirmada'].includes(c.estado_cita)) b.push('<span class="notif">Recordatorio enviado</span>');
  return b.length ? `<span class="cita-notif">${b.join('')}</span>` : '';
}

async function cargarAgenda() {
  if (estado.servicios.length === 0) {
    try { await cargarContexto(); } catch (_) { }
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
  cargarBloqueos();
}

function renderizarAgenda() {
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

  // Las citas que aparecen por primera vez (ej. agendadas por el bot) se resaltan
  const citasNuevas = new Set(
    estado.idsCitasConocidas.size ? citas.map((c) => c.id_agenda).filter((id) => !estado.idsCitasConocidas.has(id)) : []
  );
  estado.citas.forEach((c) => estado.idsCitasConocidas.add(c.id_agenda));

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
            <article class="cita st-${c.estado_cita} ${c.estado_cita} ${citasNuevas.has(c.id_agenda) ? 'flash' : ''}">
              <div class="cita-hora">${c.hora_cita.slice(0, 5)}</div>
              <div class="cita-who">
                <b>${escapeHtml(c.nombre_cliente || c.telefono_cliente)}</b>
                <small>${escapeHtml(c.nombre_servicio)} · <span class="mono">${escapeHtml(c.telefono_contacto || c.telefono_cliente)}</span>${c.correo_cliente ? ` · ${escapeHtml(c.correo_cliente)}` : ''}</small>
                ${badgesNotificacion(c)}
              </div>
              <select class="status-select st-${c.estado_cita}" data-cambiar-estado="${c.id_agenda}" aria-label="Estado de la cita">
                ${ESTADOS_CITA.map((e) => `<option value="${e}" ${e === c.estado_cita ? 'selected' : ''}>${ETIQUETA_ESTADO[e]}</option>`).join('')}
              </select>
              <span class="acciones">
                <button class="icon-btn" data-editar-cita="${c.id_agenda}" title="Editar" aria-label="Editar cita">${icono('edit')}</button>
                <button class="icon-btn danger" data-eliminar-cita="${c.id_agenda}" title="Eliminar" aria-label="Eliminar cita">${icono('trash')}</button>
              </span>
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

  $$('[data-editar-cita]', cont).forEach((btn) => {
    btn.addEventListener('click', () => {
      const cita = estado.citas.find((c) => String(c.id_agenda) === btn.dataset.editarCita);
      if (cita) abrirDrawer(cita);
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

// El mismo panel sirve para agendar (cita = null) y para editar una cita
function abrirDrawer(cita = null) {
  estado.citaEditando = cita;
  $('formCita').reset();
  actualizarSelectServicios();
  $('drawerTitulo').textContent = cita ? 'Editar cita' : 'Agendar manualmente';
  $('btnGuardarCita').textContent = cita ? 'Guardar cambios' : 'Agendar cita';
  $('citaHint').textContent = cita
    ? 'Si cambias el día o la hora, el cliente recibe un aviso con la hora nueva y los recordatorios se reprograman.'
    : 'Se agenda con al menos 2 horas de anticipación. El cliente recibirá un aviso al agendar, un recordatorio el día antes y un mensaje para confirmar 1 hora antes.';
  if (cita) {
    $('citaNombre').value = cita.nombre_cliente || '';
    $('citaCorreo').value = cita.correo_cliente || '';
    $('citaTelefono').value = formatoTelefono(cita.telefono_contacto || cita.telefono_cliente);
    $('citaServicio').value = String(cita.id_servicio);
    $('citaFecha').value = cita.fecha_cita;
    $('citaHora').value = cita.hora_cita.slice(0, 5);
  } else {
    $('citaFecha').value = $('filtroFecha').value || fechaLocalISO();
  }
  $('drawerCita').classList.add('open');
  setTimeout(() => $('citaNombre').focus(), 200);
}

function cerrarDrawer(id = null) {
  $$(id ? `#${id}` : '.drawer-backdrop').forEach((d) => d.classList.remove('open'));
}

$('btnNuevaCita').addEventListener('click', () => abrirDrawer());
$$('[data-close-drawer]').forEach((b) => b.addEventListener('click', () => cerrarDrawer(b.closest('.drawer-backdrop').id)));
$$('.drawer-backdrop').forEach((d) => d.addEventListener('click', (e) => { if (e.target === d) cerrarDrawer(d.id); }));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrarDrawer(); });

$('formCita').addEventListener('submit', (e) => {
  e.preventDefault();
  const editando = estado.citaEditando;
  const horaForm = $('citaHora').value;
  const cambiaMomento = !editando || editando.fecha_cita !== $('citaFecha').value || editando.hora_cita.slice(0, 5) !== horaForm;
  // Misma regla que el backend: al menos 2 horas de anticipación
  const inicio = new Date(`${$('citaFecha').value}T${horaForm}`);
  if (cambiaMomento && inicio.getTime() < Date.now() + 2 * 60 * 60 * 1000) {
    mostrarToast('Las citas se agendan con al menos 2 horas de anticipación', 'error');
    return;
  }
  if (editando) {
    manejarEnvio(e.target, async () => {
      await api.editarCita(estado.negocio.id_negocio, editando.id_agenda, {
        nombre_cliente: $('citaNombre').value.trim(),
        correo_cliente: $('citaCorreo').value.trim(),
        telefono_contacto: $('citaTelefono').value.trim(),
        id_servicio: Number($('citaServicio').value),
        fecha_cita: $('citaFecha').value,
        hora_cita: horaForm,
      });
      cerrarDrawer('drawerCita');
      await cargarAgenda();
      mostrarToast(cambiaMomento ? 'Cita actualizada: se le avisó al cliente la hora nueva' : 'Cita actualizada');
    });
    return;
  }
  manejarEnvio(e.target, async () => {
    await api.crearCita(estado.negocio.id_negocio, {
      telefono_cliente: $('citaTelefono').value.trim(),
      nombre_cliente: $('citaNombre').value.trim(),
      correo_cliente: $('citaCorreo').value.trim(),
      id_servicio: Number($('citaServicio').value),
      fecha_cita: $('citaFecha').value,
      hora_cita: $('citaHora').value,
    });
    e.target.reset();
    cerrarDrawer('drawerCita');
    await cargarAgenda();
    mostrarToast('Cita agendada');
    cargarEstadisticasResumen();
  });
});

/* ------------------------- Agenda: bloquear horarios ------------------------- */

async function cargarBloqueos() {
  try {
    estado.bloqueos = await api.listarBloqueos(estado.negocio.id_negocio);
  } catch (error) {
    estado.bloqueos = [];
  }
  renderizarBloqueos();
}

function renderizarBloqueos() {
  const lista = estado.bloqueos || [];
  const strip = $('bloqueosStrip');
  strip.classList.toggle('hidden', lista.length === 0);
  strip.innerHTML = lista.length === 0 ? '' : `<span>Bloqueado:</span>` + lista.map((b) => `
      <button class="bloqueo-chip" type="button" data-abrir-bloqueos title="${escapeHtml(b.descripcion)}">
        ${icono('block')}${escapeHtml(b.motivo || 'Sin motivo')} · ${escapeHtml(b.descripcion)}
      </button>`).join('');
  $$('[data-abrir-bloqueos]', strip).forEach((b) => b.addEventListener('click', abrirBloqueos));

  $('listaBloqueos').innerHTML = lista.length === 0
    ? '<div class="vacio-chico">No hay horarios bloqueados.</div>'
    : lista.map((b) => `
      <div class="bloqueo-item">
        <div>
          <b>${escapeHtml(b.motivo || 'Sin motivo')}</b>
          <small>${escapeHtml(b.descripcion)}</small>
          ${b.citas_afectadas ? `<small class="aviso-citas">${b.citas_afectadas} cita${b.citas_afectadas === 1 ? '' : 's'} agendada${b.citas_afectadas === 1 ? '' : 's'} en este horario: muévela${b.citas_afectadas === 1 ? '' : 's'} o cancéla${b.citas_afectadas === 1 ? 'la' : 'las'}.</small>` : ''}
        </div>
        <button class="icon-btn danger" type="button" data-quitar-bloqueo="${b.id_bloqueo}" title="Quitar bloqueo" aria-label="Quitar bloqueo">${icono('trash')}</button>
      </div>`).join('');
  $$('[data-quitar-bloqueo]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!(await confirmar({ titulo: '¿Quitar este bloqueo?', texto: 'Esos horarios vuelven a quedar disponibles para agendar.', boton: 'Quitar' }))) return;
    try {
      await api.eliminarBloqueo(estado.negocio.id_negocio, btn.dataset.quitarBloqueo);
      mostrarToast('Bloqueo quitado');
      cargarBloqueos();
    } catch (error) {
      mostrarToast(error.message, 'error');
    }
  }));
}

function actualizarFormBloqueo() {
  const tipo = $('bloqueoTipo').value;
  $('bloqueoFechas').classList.toggle('hidden', tipo === 'semanal');
  $('bloqueoHastaCampo').classList.toggle('hidden', tipo === 'tramo');
  $('bloqueoDias').classList.toggle('hidden', tipo !== 'semanal');
  $('bloqueoHoras').classList.toggle('hidden', tipo === 'dias');
}

function abrirBloqueos() {
  if (!$('bloqueoDesde').value) $('bloqueoDesde').value = fechaLocalISO();
  actualizarFormBloqueo();
  renderizarBloqueos();
  $('drawerBloqueo').classList.add('open');
}

$('btnBloqueos').addEventListener('click', abrirBloqueos);
$('bloqueoTipo').addEventListener('change', actualizarFormBloqueo);

$('formBloqueo').addEventListener('submit', (e) => {
  e.preventDefault();
  const tipo = $('bloqueoTipo').value;
  const datos = { motivo: $('bloqueoMotivo').value.trim() || null };
  if (tipo === 'semanal') {
    Object.assign(datos, {
      tipo: 'semanal',
      dias_semana: $$('#bloqueoDias input:checked').map((i) => Number(i.value)),
    });
  } else {
    Object.assign(datos, {
      tipo: 'fechas',
      fecha_desde: $('bloqueoDesde').value || null,
      fecha_hasta: tipo === 'dias' ? ($('bloqueoHasta').value || $('bloqueoDesde').value || null) : $('bloqueoDesde').value || null,
    });
  }
  if (tipo !== 'dias') {
    datos.hora_desde = $('bloqueoHoraDesde').value || null;
    datos.hora_hasta = $('bloqueoHoraHasta').value || null;
  }
  manejarEnvio(e.target, async () => {
    const creado = await api.crearBloqueo(estado.negocio.id_negocio, datos);
    $('bloqueoMotivo').value = '';
    await cargarBloqueos();
    mostrarToast(creado.citas_afectadas
      ? `Horario bloqueado. Ojo: hay ${creado.citas_afectadas} cita(s) agendada(s) ahí.`
      : 'Horario bloqueado');
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

  cont.innerHTML = lista.map((c) => `
      <button class="conv ${estado.conversacionActiva === c.id_conversacion ? 'active' : ''}" data-ver-conversacion="${c.id_conversacion}">
        <span class="avatar avatar-sm">${escapeHtml(iniciales(c.nombre_cliente))}</span>
        <span class="conv-body">
          <span class="conv-top"><b class="${c.nombre_cliente ? '' : 'mono'}">${escapeHtml(nombreVisible(c))}</b><time title="${formatoFechaHora(c.ultima_actividad || c.fecha_inicio)}">${formatoCorto(c.ultima_actividad || c.fecha_inicio)}</time></span>
          <span class="conv-bottom">
            <small>${c.nombre_cliente ? escapeHtml(formatoTelefono(c.telefono_cliente)) : 'Sin nombre aún'}</small>
            <span style="display:flex;gap:6px;align-items:center">
              ${botEnPausa(c)
                ? '<span class="pill pill-warning" title="Bot en pausa: la estás atendiendo tú">en pausa</span>'
                : `<span class="pill ${c.estado === 'abierta' ? 'pill-success' : 'pill-muted'}">${escapeHtml(c.estado)}</span>`}
              <span class="count" title="Mensajes">${c.total_mensajes}</span>
            </span>
          </span>
        </span>
      </button>`
  ).join('');

  $$('[data-ver-conversacion]', cont).forEach((fila) => {
    fila.addEventListener('click', () => {
      const conv = estado.conversaciones.find((c) => String(c.id_conversacion) === fila.dataset.verConversacion);
      abrirMensajes(conv);
    });
  });
}

$('buscarConversacion').addEventListener('input', renderizarConversaciones);

const ETIQUETA_EMISOR = { bot: 'bot', negocio: 'negocio', cliente: 'cliente' };

async function abrirMensajes(conv, { silencioso = false } = {}) {
  try {
    const mensajes = await api.listarMensajes(estado.negocio.id_negocio, conv.id_conversacion);
    // Si es un refresco en vivo y no llegó nada nuevo, no se toca el DOM
    if (silencioso && estado.conversacionActiva === conv.id_conversacion && mensajes.length === estado.mensajesActivos) {
      actualizarComposer(conv);
      return;
    }
    const hilo = $('chatThread');
    const estabaAbajo = hilo.scrollHeight - hilo.scrollTop - hilo.clientHeight < 80;
    const cambioDeConversacion = estado.conversacionActiva !== conv.id_conversacion;
    const previos = cambioDeConversacion ? 0 : estado.mensajesActivos;

    estado.conversacionActiva = conv.id_conversacion;
    estado.mensajesActivos = mensajes.length;
    const titulo = $('mensajesTitulo');
    titulo.textContent = '';
    const prefijo = document.createElement('span');
    prefijo.className = 'solo-escritorio';
    prefijo.textContent = 'Conversación con ';
    titulo.append(prefijo, nombreVisible(conv));
    $('chatSub').textContent = `${formatoTelefono(conv.telefono_cliente)} · ${mensajes.length} ${mensajes.length === 1 ? 'mensaje' : 'mensajes'}`;
    $('chatAvatar').textContent = iniciales(conv.nombre_cliente);

    let diaAnterior = '';
    hilo.innerHTML = mensajes.length === 0
      ? `<div class="chat-empty">${icono('chat')}<p>Esta conversación no tiene mensajes.</p></div>`
      : mensajes.map((m, i) => {
        const dia = fechaUTC(m.fecha_hora).toLocaleDateString('es-CL', { weekday: 'long', day: 'numeric', month: 'long' });
        const separador = dia !== diaAnterior ? `<div class="chat-day">${capitalizar(dia)}</div>` : '';
        diaAnterior = dia;
        // Solo se animan los mensajes que recién llegan
        const anim = i >= previos ? `animation-delay:${Math.min(i - previos, 12) * 25}ms` : 'animation:none';
        return `${separador}
          <div class="bubble ${ETIQUETA_EMISOR[m.emisor] || 'cliente'}" style="${anim}">${formatoWhatsapp(m.contenido)}<time>${formatoHora(m.fecha_hora)}</time></div>`;
      }).join('');

    actualizarComposer(conv);
    $('inbox').classList.add('reading');
    if (!silencioso) renderizarConversaciones();
    if (!silencioso || estabaAbajo || cambioDeConversacion) hilo.scrollTop = hilo.scrollHeight;
  } catch (error) {
    if (!silencioso) mostrarToast(error.message, 'error');
  }
}

function botEnPausa(conv) {
  return Boolean(conv && conv.bot_pausado_hasta && fechaUTC(conv.bot_pausado_hasta) > new Date());
}

function actualizarComposer(conv) {
  const puede = Boolean(conv && conv.puede_responder);
  $('formResponder').classList.toggle('hidden', !conv || !puede);
  $('chatCerrado').classList.toggle('hidden', !conv || puede);
  actualizarControlBot(conv);
}

// Tomar el control: el bot se calla en esta conversación mientras el dueño atiende
function actualizarControlBot(conv) {
  $('chatBot').classList.toggle('hidden', !conv);
  if (!conv) return;
  const pausado = botEnPausa(conv);
  const hasta = pausado ? fechaUTC(conv.bot_pausado_hasta) : null;
  const mismoDia = hasta && hasta.toDateString() === new Date().toDateString();
  $('chatBotPausa').classList.toggle('hidden', !pausado);
  if (pausado) {
    $('chatBotPausa').textContent = `Bot en pausa hasta ${mismoDia ? 'las ' : ''}${mismoDia ? formatoHora(conv.bot_pausado_hasta) : formatoFechaHora(conv.bot_pausado_hasta)}`;
  }
  $('btnBotConversacion').innerHTML = pausado ? `${icono('play')}Reactivar bot` : `${icono('pause')}Pausar bot`;
  $('btnBotConversacion').title = pausado
    ? 'El bot vuelve a responder en esta conversación'
    : 'El bot deja de responder en esta conversación para que la atiendas tú';
  $('chatNotaPausa').classList.toggle('hidden', pausado || !conv.puede_responder);
}

$('btnBotConversacion').addEventListener('click', async () => {
  const conv = estado.conversaciones.find((c) => c.id_conversacion === estado.conversacionActiva);
  if (!conv) return;
  const pausar = !botEnPausa(conv);
  try {
    const actualizada = await api.pausarBotConversacion(estado.negocio.id_negocio, conv.id_conversacion, pausar);
    Object.assign(conv, actualizada);
    actualizarComposer(conv);
    renderizarConversaciones();
    mostrarToast(pausar ? 'Bot en pausa en esta conversación' : 'El bot vuelve a responder en esta conversación');
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
});

$('formResponder').addEventListener('submit', (e) => {
  e.preventDefault();
  const texto = $('respuestaTexto').value.trim();
  if (!texto || !estado.conversacionActiva) return;
  manejarEnvio(e.target, async () => {
    await api.responderConversacion(estado.negocio.id_negocio, estado.conversacionActiva, texto);
    $('respuestaTexto').value = '';
    $('respuestaTexto').style.height = '';
    await cargarConversaciones();
    const conv = estado.conversaciones.find((c) => c.id_conversacion === estado.conversacionActiva);
    if (conv) await abrirMensajes(conv, { silencioso: true });
    if (conv) actualizarComposer(conv);
    $('chatThread').scrollTop = $('chatThread').scrollHeight;
    if (botEnPausa(conv)) mostrarToast('Enviado. El bot queda en pausa en esta conversación mientras la atiendes.');
  });
});

// Enter envía, Shift+Enter hace salto de línea; el cuadro crece con el texto
$('respuestaTexto').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $('formResponder').requestSubmit();
  }
});
$('respuestaTexto').addEventListener('input', (e) => {
  e.target.style.height = 'auto';
  e.target.style.height = `${Math.min(e.target.scrollHeight, 140)}px`;
});

$('btnCerrarMensajes').addEventListener('click', () => $('inbox').classList.remove('reading'));

/* -------------------------------- Clientes -------------------------------- */

async function cargarClientes() {
  try {
    estado.clientes = await api.listarClientes(estado.negocio.id_negocio);
    renderizarClientes();
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

function fechaCorta(iso) {
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString('es-CL', { weekday: 'short', day: 'numeric', month: 'short' });
}

function renderizarClientes() {
  const cont = $('listaClientes');
  const q = $('buscarCliente').value.trim().toLowerCase();
  const digitos = q.replace(/\D/g, '');
  const lista = (estado.clientes || []).filter((c) => !q
    || (c.nombre || '').toLowerCase().includes(q)
    || (c.correo || '').toLowerCase().includes(q)
    || (digitos && c.telefono.includes(digitos)));

  if (!estado.clientes || estado.clientes.length === 0) {
    cont.innerHTML = `<div class="empty">${icono('chat')}<b>Todavía no hay clientes</b><span>Aparecen aquí cuando te escriben o agendan.</span></div>`;
    return;
  }
  if (lista.length === 0) {
    cont.innerHTML = `<div class="empty"><b>Sin resultados para "${escapeHtml(q)}"</b></div>`;
    return;
  }
  cont.innerHTML = lista.map((c) => `
    <button class="cliente-card" type="button" data-ver-cliente="${c.id_cliente}">
      <span class="cliente-top">
        <span class="avatar avatar-sm">${escapeHtml(iniciales(c.nombre))}</span>
        <span style="min-width:0">
          <b>${escapeHtml(c.nombre || formatoTelefono(c.telefono))}</b>
          <small class="mono">${escapeHtml(formatoTelefono(c.telefono))}</small>
        </span>
      </span>
      <span class="cliente-nums">
        <span><b>${c.total_citas}</b>citas</span>
        <span><b>${c.completadas}</b>atendidas</span>
        <span class="${c.inasistencias ? 'malo' : ''}"><b>${c.inasistencias}</b>no asistió</span>
      </span>
      ${c.proxima_cita
        ? `<span class="cliente-prox">Próxima: ${fechaCorta(c.proxima_cita.fecha_cita)}, ${c.proxima_cita.hora_cita.slice(0, 5)} · ${escapeHtml(c.proxima_cita.nombre_servicio)}</span>`
        : `<span class="cliente-prox sin">${c.ultima_visita ? `Última visita: ${fechaCorta(c.ultima_visita)}` : 'Sin citas próximas'}</span>`}
      ${c.notas ? `<span class="cliente-nota">${escapeHtml(c.notas)}</span>` : ''}
    </button>`).join('');
  $$('[data-ver-cliente]', cont).forEach((b) => b.addEventListener('click', () => abrirFicha(Number(b.dataset.verCliente))));
}

$('buscarCliente').addEventListener('input', renderizarClientes);

async function abrirFicha(idCliente) {
  try {
    const c = await api.obtenerCliente(estado.negocio.id_negocio, idCliente);
    estado.fichaActiva = c;
    $('fichaAvatar').textContent = iniciales(c.nombre);
    $('fichaNombre').textContent = c.nombre || formatoTelefono(c.telefono);
    $('fichaContacto').textContent = [formatoTelefono(c.telefono), c.correo].filter(Boolean).join(' · ');
    $('fichaStats').innerHTML = `
      <div><b>${c.total_citas}</b>citas</div>
      <div><b>${c.completadas}</b>atendidas</div>
      <div class="${c.inasistencias ? 'malo' : ''}"><b>${c.inasistencias}</b>no asistió</div>
      <div><b>${c.canceladas}</b>canceladas</div>`;
    $('fichaNotas').value = c.notas || '';
    $('fichaCitas').innerHTML = c.citas.length === 0
      ? '<div class="vacio-chico">Todavía no tiene citas.</div>'
      : c.citas.map((x) => `
        <div class="ficha-cita">
          <span><b>${fechaCorta(x.fecha_cita)}, ${x.hora_cita.slice(0, 5)}</b> · ${escapeHtml(x.nombre_servicio)}</span>
          <span class="estado-cita st-${x.estado_cita}">${ETIQUETA_ESTADO[x.estado_cita] || x.estado_cita}</span>
        </div>`).join('');
    $('btnFichaConversacion').classList.toggle('hidden', !c.id_conversacion);
    $('drawerCliente').classList.add('open');
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
}

$('formNotas').addEventListener('submit', (e) => {
  e.preventDefault();
  if (!estado.fichaActiva) return;
  manejarEnvio(e.target, async () => {
    const c = await api.guardarNotasCliente(estado.negocio.id_negocio, estado.fichaActiva.id_cliente, $('fichaNotas').value);
    estado.fichaActiva = c;
    const enLista = (estado.clientes || []).find((x) => x.id_cliente === c.id_cliente);
    if (enLista) enLista.notas = c.notas;
    renderizarClientes();
    mostrarToast('Notas guardadas');
  });
});

$('btnFichaConversacion').addEventListener('click', async () => {
  const id = estado.fichaActiva?.id_conversacion;
  if (!id) return;
  cerrarDrawer('drawerCliente');
  cambiarVista('historial');
  await cargarConversaciones();
  const conv = estado.conversaciones.find((c) => c.id_conversacion === id);
  if (conv) abrirMensajes(conv);
});

/* -------------------------------- Mi cuenta -------------------------------- */

function rellenarCuenta() {
  const a = estado.administrador;
  if (!a) return;
  $('cuentaNombre').value = a.nombre || '';
  $('cuentaCorreo').value = a.correo || '';
  $('cuentaClaveActual').value = '';
  $('campoClaveCorreo').classList.add('hidden');
  $('cuentaDesde').textContent = a.fecha_registro
    ? `Cuenta creada el ${fechaUTC(a.fecha_registro).toLocaleDateString('es-CL', { day: 'numeric', month: 'long', year: 'numeric' })}.`
    : '';
}

// Para cambiar el correo se pide la contraseña actual
$('cuentaCorreo').addEventListener('input', () => {
  const cambia = $('cuentaCorreo').value.trim().toLowerCase() !== (estado.administrador?.correo || '').toLowerCase();
  $('campoClaveCorreo').classList.toggle('hidden', !cambia);
  $('cuentaClaveActual').required = cambia;
});

$('formCuenta').addEventListener('submit', (e) => {
  e.preventDefault();
  manejarEnvio(e.target, async () => {
    const correo = $('cuentaCorreo').value.trim();
    const datos = { nombre: $('cuentaNombre').value.trim() };
    if (correo.toLowerCase() !== estado.administrador.correo.toLowerCase()) {
      datos.correo = correo;
      datos.contrasena_actual = $('cuentaClaveActual').value;
    }
    estado.administrador = await api.actualizarCuenta(datos);
    $('sidebarCorreo').textContent = estado.administrador.correo;
    rellenarCuenta();
    mostrarToast('Datos de la cuenta actualizados');
  });
});

$('formClave').addEventListener('submit', (e) => {
  e.preventDefault();
  if ($('claveNueva').value !== $('claveRepetir').value) {
    mostrarToast('Las contraseñas nuevas no coinciden', 'error');
    return;
  }
  manejarEnvio(e.target, async () => {
    await api.cambiarContrasena({ contrasena_actual: $('claveActual').value, nueva_contrasena: $('claveNueva').value });
    e.target.reset();
    mostrarToast('Contraseña actualizada');
  });
});

/* ------------------------- Actualización en vivo --------------------------- */

const INTERVALO_EN_VIVO_MS = 7000;
let temporizadorEnVivo = null;
let consultandoNovedades = false;

function vistaActiva() {
  return document.querySelector('.view.active')?.id || '';
}

async function revisarNovedades() {
  if (!estado.negocio || consultandoNovedades || document.hidden) return;
  consultandoNovedades = true;
  try {
    const n = await api.novedades(estado.negocio.id_negocio);
    $('liveDot').classList.remove('off');

    if (estado.firmaAgenda !== null && n.firma_agenda !== estado.firmaAgenda) {
      if (vistaActiva() === 'viewAgenda') await cargarAgendaSilenciosa();
      cargarEstadisticasResumen();
    }
    if (estado.firmaConversaciones !== null && n.firma_conversaciones !== estado.firmaConversaciones) {
      if (vistaActiva() === 'viewHistorial') {
        await cargarConversaciones();
        const conv = estado.conversaciones.find((c) => c.id_conversacion === estado.conversacionActiva);
        if (conv) await abrirMensajes(conv, { silencioso: true });
      }
      cargarEstadisticasResumen();
    }
    const huboCambios = (estado.firmaAgenda !== null && n.firma_agenda !== estado.firmaAgenda)
      || (estado.firmaConversaciones !== null && n.firma_conversaciones !== estado.firmaConversaciones);
    if (huboCambios && vistaActiva() === 'viewClientes') cargarClientes();
    estado.firmaAgenda = n.firma_agenda;
    estado.firmaConversaciones = n.firma_conversaciones;
    actualizarAvisos(n.avisos);
  } catch (error) {
    $('liveDot').classList.add('off');
    console.warn('Sin conexión para actualizar en vivo:', error.message);
  } finally {
    consultandoNovedades = false;
  }
}

async function cargarAgendaSilenciosa() {
  try {
    const fecha = $('filtroFecha').value;
    estado.citas = await api.listarCitas(estado.negocio.id_negocio, fecha || null);
    // No re-renderizar si el usuario está cambiando un estado en este momento
    if (document.activeElement?.matches?.('[data-cambiar-estado]')) return;
    renderizarAgenda();
  } catch (_) { /* se reintenta en la próxima vuelta */ }
}

function actualizarAvisos(avisos) {
  estado.avisos = avisos || [];
  const total = estado.avisos.length;
  $('avisosCount').textContent = total > 9 ? '9+' : String(total);
  $('avisosCount').classList.toggle('hidden', total === 0);
  $('btnAvisos').setAttribute('aria-label', total ? `Avisos (${total > 9 ? '9+' : total} sin leer)` : 'Avisos');

  // Los avisos nuevos (desde la última consulta) también salen como toast
  const nuevos = estado.avisos.filter((a) => a.id_aviso > estado.ultimoAvisoVisto);
  if (estado.ultimoAvisoVisto > 0) nuevos.slice(0, 2).reverse().forEach((a) => mostrarToast(a.texto));
  if (total) estado.ultimoAvisoVisto = Math.max(estado.ultimoAvisoVisto, ...estado.avisos.map((a) => a.id_aviso));
  else if (estado.ultimoAvisoVisto === 0) estado.ultimoAvisoVisto = 1;

  $('avisosLista').innerHTML = total === 0
    ? '<div class="avisos-vacio">Sin avisos nuevos.</div>'
    : estado.avisos.map((a) => `
        <div class="aviso ${escapeHtml(a.tipo)}">
          <span class="aviso-icono"></span>
          <div>${escapeHtml(a.texto)}<time>${formatoFechaHora(a.fecha)}</time></div>
        </div>`).join('');
}

function iniciarEnVivo() {
  detenerEnVivo();
  estado.firmaAgenda = null;
  estado.firmaConversaciones = null;
  estado.ultimoAvisoVisto = 0;
  revisarNovedades();
  temporizadorEnVivo = setInterval(revisarNovedades, INTERVALO_EN_VIVO_MS);
}

function detenerEnVivo() {
  if (temporizadorEnVivo) clearInterval(temporizadorEnVivo);
  temporizadorEnVivo = null;
}

// Al volver a la pestaña, se actualiza de inmediato
document.addEventListener('visibilitychange', () => { if (!document.hidden) revisarNovedades(); });

$('btnAvisos').addEventListener('click', (e) => {
  e.stopPropagation();
  const abierto = $('avisosPanel').classList.toggle('open');
  $('btnAvisos').setAttribute('aria-expanded', String(abierto));
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.avisos-wrap')) {
    $('avisosPanel').classList.remove('open');
    $('btnAvisos').setAttribute('aria-expanded', 'false');
  }
});
$('btnLeerAvisos').addEventListener('click', async () => {
  try {
    await api.leerAvisos(estado.negocio.id_negocio);
    actualizarAvisos([]);
  } catch (error) {
    mostrarToast(error.message, 'error');
  }
});

/* -------------------------------- Helpers ---------------------------------- */

function escapeHtml(texto) {
  const div = document.createElement('div');
  div.textContent = texto ?? '';
  return div.innerHTML;
}

/* -------------------------------- Arranque ---------------------------------- */

(async function iniciar() {
  const params = new URLSearchParams(window.location.search);
  const resetToken = params.get('reset_token');

  if (resetToken) {
    mostrarPantallaLogin();
    initRecuperacionPassword(resetToken);
    return;
  }

  initRecuperacionPassword(null);

  if (Auth.getToken()) {
    try {
      await iniciarSesionExitosa();
      return;
    } catch (_) { }
  }
  mostrarPantallaLogin();
})();

function initRecuperacionPassword(resetToken) {
  const authTabs = document.getElementById('authTabs');
  const authError = document.getElementById('authError');
  const formLogin = document.getElementById('formLogin');
  const formRegistro = document.getElementById('formRegistro');
  const formForgot = document.getElementById('formForgot');
  const formReset = document.getElementById('formReset');

  function limpiarEstilosAviso() {
    if (!authError) return;
    authError.style.background = '';
    authError.style.color = '';
    authError.style.borderColor = '';
  }

  function mostrarAviso(texto, esExito = false) {
    if (!authError) return mostrarToast(texto, esExito ? 'info' : 'error');
    authError.textContent = texto;
    authError.classList.remove('hidden');
    if (esExito) {
      authError.style.background = '#e6f4ea';
      authError.style.color = '#1e7e34';
      authError.style.borderColor = '#b7dfb9';
    } else {
      limpiarEstilosAviso();
    }
  }

  function ocultarAviso() {
    limpiarEstilosAviso();
    if (authError) authError.classList.add('hidden');
  }

  if (resetToken && formReset) {
    authTabs?.classList.add('hidden');
    formLogin?.classList.add('hidden');
    formRegistro?.classList.add('hidden');
    formForgot?.classList.add('hidden');
    formReset.classList.remove('hidden');
  }

  document.querySelectorAll('.auth-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      ocultarAviso();
      formForgot?.classList.add('hidden');
      formReset?.classList.add('hidden');
    });
  });

  document.getElementById('linkOlvideClave')?.addEventListener('click', (e) => {
    e.preventDefault();
    ocultarAviso();
    formLogin?.classList.add('hidden');
    formRegistro?.classList.add('hidden');
    formForgot?.classList.remove('hidden');
  });

  document.getElementById('linkVolverLogin')?.addEventListener('click', (e) => {
    e.preventDefault();
    ocultarAviso();
    formForgot?.classList.add('hidden');
    formLogin?.classList.remove('hidden');
  });

  formForgot?.addEventListener('submit', async (e) => {
    e.preventDefault();
    ocultarAviso();
    const correo = document.getElementById('forgotCorreo').value.trim();
    const btn = formForgot.querySelector("button[type='submit']");
    if (btn) btn.disabled = true;

    try {
      const res = await fetch('/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ correo }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        mostrarAviso(data.detail || 'No se pudo procesar la solicitud.');
      } else {
        mostrarAviso(data.detail || 'Revisa tu correo para continuar.', true);
        formForgot.reset();
      }
    } catch (_) {
      mostrarAviso('Error de conexión con el servidor.');
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  formReset?.addEventListener('submit', async (e) => {
    e.preventDefault();
    ocultarAviso();
    const p1 = document.getElementById('resetPass').value;
    const p2 = document.getElementById('resetPassConfirm').value;
    if (p1 !== p2) return mostrarAviso('Las contraseñas no coinciden.');

    const btn = formReset.querySelector("button[type='submit']");
    if (btn) btn.disabled = true;

    try {
      const res = await fetch('/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, nueva_contrasena: p1 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        mostrarAviso(data.detail || 'El enlace es inválido o ya expiró.');
      } else {
        mostrarAviso(data.detail || 'Contraseña actualizada.', true);
        setTimeout(() => {
          window.location.href = '/dashboard/';
        }, 1500);
      }
    } catch (_) {
      mostrarAviso('Error de conexión con el servidor.');
    } finally {
      if (btn) btn.disabled = false;
    }
  });
}