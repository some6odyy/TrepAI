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

  if (respuesta.status === 401) {
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
          : data.detail.map((d) => d.msg).join(', ');
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

  listarNegocios: () => apiFetch('/negocio'),
  crearNegocio: (datos) => apiFetch('/negocio', { method: 'POST', body: datos }),
  actualizarPerfil: (id, datos) => apiFetch(`/negocio/${id}/perfil`, { method: 'PUT', body: datos }),
  cambiarEstadoBot: (id, activo) => apiFetch(`/negocio/${id}/estado-bot?activo=${activo}`, { method: 'PATCH' }),
  estadoWhatsapp: (id) => apiFetch(`/negocio/${id}/whatsapp`),
  conectarWhatsapp: (id, datos) => apiFetch(`/negocio/${id}/whatsapp`, { method: 'PUT', body: datos }),

  obtenerContexto: (id) => apiFetch(`/negocio/${id}/contexto`),
  actualizarContexto: (id, datos) => apiFetch(`/negocio/${id}/contexto`, { method: 'PUT', body: datos }),
  agregarServicio: (id, datos) => apiFetch(`/negocio/${id}/contexto/servicios`, { method: 'POST', body: datos }),
  actualizarServicio: (id, idServicio, datos) =>
    apiFetch(`/negocio/${id}/contexto/servicios/${idServicio}`, { method: 'PUT', body: datos }),
  eliminarServicio: (id, idServicio) =>
    apiFetch(`/negocio/${id}/contexto/servicios/${idServicio}`, { method: 'DELETE' }),

  listarCitas: (id, fecha) => apiFetch(`/negocio/${id}/agenda${fecha ? `?fecha=${fecha}` : ''}`),
  crearCita: (id, datos) => apiFetch(`/negocio/${id}/agenda`, { method: 'POST', body: datos }),
  actualizarEstadoCita: (id, idAgenda, estado) =>
    apiFetch(`/negocio/${id}/agenda/${idAgenda}/estado`, { method: 'PATCH', body: { estado_cita: estado } }),
  eliminarCita: (id, idAgenda) => apiFetch(`/negocio/${id}/agenda/${idAgenda}`, { method: 'DELETE' }),

  listarConversaciones: (id) => apiFetch(`/negocio/${id}/conversaciones`),
  listarMensajes: (id, idConversacion) => apiFetch(`/negocio/${id}/conversaciones/${idConversacion}/mensajes`),
};
