/* Capa de acceso a la API de TrepAI. Todo el resto del frontend pasa
 * por acá para no repetir el manejo del token ni de errores. */

const API_BASE = window.TREPAI_API_BASE || 'http://127.0.0.1:8000';

const Auth = {
  getToken: () => localStorage.getItem('trepai_token'),
  setToken: (t) => localStorage.setItem('trepai_token', t),
  clearToken: () => localStorage.removeItem('trepai_token'),
};

/**
 * @param {string} path - ej. "/negocio"
 * @param {object} options - { method, body } — body ya es un objeto JS, no un string.
 */
async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = Auth.getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let body = options.body;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }

  let respuesta;
  try {
    respuesta = await fetch(`${API_BASE}${path}`, { ...options, headers, body });
  } catch (error) {
    throw new Error('No se pudo conectar con el servidor. ¿Está corriendo el backend?');
  }

  // 401 con sesión abierta = la sesión venció. Sin token (ej. contraseña
  // incorrecta al iniciar sesión) se muestra el mensaje del backend.
  if (respuesta.status === 401 && token && !path.startsWith('/auth/login')) {
    Auth.clearToken();
    mostrarPantallaLogin();
    throw new Error('Tu sesión expiró, inicia sesión de nuevo');
  }

  if (!respuesta.ok) {
    let detalle = `Error ${respuesta.status}`;
    try {
      const data = await respuesta.json();
      if (data.detail) {
        detalle = typeof data.detail === 'string'
          ? data.detail
          : data.detail.map((d) => String(d.msg).replace(/^Value error, /, '')).join(', ');
      }
    } catch (_) { /* el cuerpo no era JSON */ }
    throw new Error(detalle);
  }

  if (respuesta.status === 204) return null;
  return respuesta.json();
}

const api = {
  registrar: (datos) => apiFetch('/auth/registro', { method: 'POST', body: datos }),
  login: (datos) => apiFetch('/auth/login', { method: 'POST', body: datos }),
  yo: () => apiFetch('/auth/yo'),
  actualizarCuenta: (datos) => apiFetch('/auth/yo', { method: 'PUT', body: datos }),
  cambiarContrasena: (datos) => apiFetch('/auth/cambiar-contrasena', { method: 'POST', body: datos }),

  listarNegocios: () => apiFetch('/negocio'),
  crearNegocio: (datos) => apiFetch('/negocio', { method: 'POST', body: datos }),
  actualizarPerfil: (id, datos) => apiFetch(`/negocio/${id}/perfil`, { method: 'PUT', body: datos }),
  cambiarEstadoBot: (id, activo) => apiFetch(`/negocio/${id}/estado-bot?activo=${activo}`, { method: 'PATCH' }),
  estadoWhatsapp: (id) => apiFetch(`/negocio/${id}/whatsapp`),
  conectarWhatsapp: (id, datos) => apiFetch(`/negocio/${id}/whatsapp`, { method: 'PUT', body: datos }),
  solicitarCodigoWhatsapp: (idNegocio, datos) =>
    apiFetch(`/negocio/${idNegocio}/whatsapp/solicitar-codigo`, { method: 'POST', body: datos }),

  verificarCodigoWhatsapp: (idNegocio, datos) =>
    apiFetch(`/negocio/${idNegocio}/whatsapp/verificar-codigo`, { method: 'POST', body: datos }),
  desvincularWhatsapp: (id) => apiFetch(`/negocio/${id}/whatsapp`, { method: 'DELETE' }),
  listarNumerosWhatsapp: (id) => apiFetch(`/negocio/${id}/whatsapp/numeros`),

  obtenerContexto: (id) => apiFetch(`/negocio/${id}/contexto`),
  actualizarContexto: (id, datos) => apiFetch(`/negocio/${id}/contexto`, { method: 'PUT', body: datos }),
  agregarServicio: (id, datos) => apiFetch(`/negocio/${id}/contexto/servicios`, { method: 'POST', body: datos }),
  actualizarServicio: (id, idServicio, datos) =>
    apiFetch(`/negocio/${id}/contexto/servicios/${idServicio}`, { method: 'PUT', body: datos }),
  eliminarServicio: (id, idServicio) =>
    apiFetch(`/negocio/${id}/contexto/servicios/${idServicio}`, { method: 'DELETE' }),

  listarCitas: (id, fecha) => apiFetch(`/negocio/${id}/agenda${fecha ? `?fecha=${fecha}` : ''}`),
  crearCita: (id, datos) => apiFetch(`/negocio/${id}/agenda`, { method: 'POST', body: datos }),
  actualizarEstadoCita: (id, idAgenda, estado, extra = {}) =>
    apiFetch(`/negocio/${id}/agenda/${idAgenda}/estado`, { method: 'PATCH', body: { estado_cita: estado, ...extra } }),
  eliminarCita: (id, idAgenda) => apiFetch(`/negocio/${id}/agenda/${idAgenda}`, { method: 'DELETE' }),
  editarCita: (id, idAgenda, datos) => apiFetch(`/negocio/${id}/agenda/${idAgenda}`, { method: 'PUT', body: datos }),

  listarBloqueos: (id) => apiFetch(`/negocio/${id}/bloqueos`),
  crearBloqueo: (id, datos) => apiFetch(`/negocio/${id}/bloqueos`, { method: 'POST', body: datos }),
  eliminarBloqueo: (id, idBloqueo) => apiFetch(`/negocio/${id}/bloqueos/${idBloqueo}`, { method: 'DELETE' }),

  listarClientes: (id) => apiFetch(`/negocio/${id}/clientes`),
  obtenerCliente: (id, idCliente) => apiFetch(`/negocio/${id}/clientes/${idCliente}`),
  guardarNotasCliente: (id, idCliente, notas) =>
    apiFetch(`/negocio/${id}/clientes/${idCliente}/notas`, { method: 'PUT', body: { notas } }),

  tiempoRespuesta: (id, dias = 7) => apiFetch(`/negocio/${id}/tiempo-respuesta?dias=${dias}`),

  listarConversaciones: (id) => apiFetch(`/negocio/${id}/conversaciones`),
  listarMensajes: (id, idConversacion) => apiFetch(`/negocio/${id}/conversaciones/${idConversacion}/mensajes`),
  responderConversacion: (id, idConversacion, texto) =>
    apiFetch(`/negocio/${id}/conversaciones/${idConversacion}/mensajes`, { method: 'POST', body: { texto } }),
  pausarBotConversacion: (id, idConversacion, pausado, horas = null) =>
    apiFetch(`/negocio/${id}/conversaciones/${idConversacion}/bot`, { method: 'PUT', body: { pausado, horas } }),

  // Pestaña "Casos de uso"
  demoEstado: (id) => apiFetch(`/negocio/${id}/demo/estado`),
  demoMensaje: (id, telefono, texto) =>
    apiFetch(`/negocio/${id}/demo/mensaje`, { method: 'POST', body: { telefono, texto } }),
  demoPrompt: (id, telefono, texto) =>
    apiFetch(`/negocio/${id}/demo/prompt`, { method: 'POST', body: { telefono, texto } }),
  demoCitas: (id, telefono) => apiFetch(`/negocio/${id}/demo/citas?telefono=${telefono}`),
  demoConversacion: (id, telefono) => apiFetch(`/negocio/${id}/demo/conversacion?telefono=${telefono}`),
  demoReiniciar: (id, telefono) => apiFetch(`/negocio/${id}/demo/reiniciar`, { method: 'POST', body: { telefono } }),
  estadoServicio: () => apiFetch('/status'),

  probarIa: (id) => apiFetch(`/negocio/${id}/contexto/probar-ia`, { method: 'POST' }),
  novedades: (id) => apiFetch(`/negocio/${id}/novedades`),
  leerAvisos: (id, ids = null) => apiFetch(`/negocio/${id}/avisos/leer`, { method: 'POST', body: { ids } }),
};