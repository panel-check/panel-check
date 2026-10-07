/* Piezas compartidas del calendario: la página /calendario y el bloque «Agenda» de la
 * ficha del titular. Depende de comun.js (api, apiJson, _escapeHtml, mostrarAviso). */

const CAL_TZ = "America/Argentina/Buenos_Aires";
const CAL_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const CAL_DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

// ── Fechas (todo con cadenas AAAA-MM-DD; sin depender de la zona del navegador) ──
function calHoy() {
  return new Date().toLocaleDateString("sv-SE", { timeZone: CAL_TZ });
}
function calUTC(iso) {
  const [a, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(a, m - 1, d, 12));
}
function calIso(d) {
  return d.toISOString().slice(0, 10);
}
function calSumarDias(iso, n) {
  const d = calUTC(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return calIso(d);
}
function calDiffDias(a, b) {
  return Math.round((calUTC(b) - calUTC(a)) / 86400000);
}
// 0 = lunes … 6 = domingo
function calDiaSemanaLunes(iso) {
  return (calUTC(iso).getUTCDay() + 6) % 7;
}
function calFechaLarga(iso) {
  const d = calUTC(iso);
  return `${CAL_DIAS[d.getUTCDay()]} ${d.getUTCDate()} de ${CAL_MESES[d.getUTCMonth()]}`;
}
function calMinutos(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// ── Vínculos con actas ───────────────────────────────────────────────
// Qué tipo de color lleva un evento: cliente > lead > con acta que no está en la base > sin acta.
function calClaseEvento(e) {
  const v = e.vinculos || [];
  if (v.some(x => x.tipo === "cliente")) return "cliente";
  if (v.some(x => x.tipo === "lead")) return "lead";
  if (v.some(x => x.tipo === "desconocida")) return "sin-base";
  return "";
}

function calVinculoHtml(v) {
  const acta = _escapeHtml(v.acta);
  const marca = v.denominacion ? ` · ${_escapeHtml(v.denominacion)}` : "";
  if (v.tipo === "cliente") {
    const href = `/clientes?cliente=${encodeURIComponent(v.cliente_id)}`;
    return `<a class="cal-vinculo cliente" href="${href}" title="Abrir la ficha del cliente"><b>Cliente</b> ${_escapeHtml(v.cliente)} · acta ${acta}${marca}</a>`;
  }
  if (v.tipo === "lead") {
    const nombre = _escapeHtml(v.titular || v.clave || "");
    if (v.clave) {
      return `<a class="cal-vinculo lead" href="/titular/${encodeURIComponent(v.clave)}" title="Abrir la ficha del lead"><b>Lead</b> ${nombre} · acta ${acta}${marca}</a>`;
    }
    return `<span class="cal-vinculo lead"><b>Lead</b> ${nombre} · acta ${acta}${marca}</span>`;
  }
  if (v.tipo === "tercero") {
    return `<span class="cal-vinculo" title="Solicitud presentada con agente: no es lead ni cliente"><b>Acta ${acta}</b>${marca} · de ${_escapeHtml(v.titular || "un tercero")}</span>`;
  }
  return `<span class="cal-vinculo sin-base" title="Ese número no está en la base del panel (todavía)"><b>Acta ${acta}</b> · no está en la base</span>`;
}

function calVinculosHtml(e) {
  const v = e.vinculos || [];
  return v.length ? `<div class="cal-vinculos">${v.map(calVinculoHtml).join("")}</div>` : "";
}

// ── Tarjeta de un evento ─────────────────────────────────────────────
function calHoraTexto(e) {
  if (e.todo_el_dia) return e.fecha_fin !== e.fecha ? `Todo el día (hasta el ${calFechaLarga(e.fecha_fin)})` : "Todo el día";
  if (e.fecha_fin !== e.fecha) return `${e.hora} → ${calFechaLarga(e.fecha_fin)} ${e.hora_fin}`;
  return `${e.hora} – ${e.hora_fin}`;
}

// «Cómo» es el evento: videollamada de Meet (con link), llamada o presencial.
function calComoHtml(e) {
  const lugar = e.lugar ? _escapeHtml(e.lugar) : "";
  if (e.meet_url) return `🎥 Videollamada · <a href="${_escapeHtml(e.meet_url)}" target="_blank" rel="noopener">Unirse a Meet</a>`;
  if (e.modalidad === "llamada") return `📞 Llamada${lugar ? " · " + lugar : ""}`;
  if (e.modalidad === "presencial") return `📍 ${lugar || "Presencial"}`;
  return lugar ? `📍 ${lugar}` : "";
}

function calTarjetaHtml(e, { fechaCorta = false } = {}) {
  const clase = calClaseEvento(e);
  const donde = calComoHtml(e);
  const quien = e.origen === "panel" && e.creado_por ? `Agendado desde el panel por ${_escapeHtml(e.creado_por)}` : "";
  const meta = [donde, quien].filter(Boolean).join(" · ");
  const dia = fechaCorta ? `${_escapeHtml(calFechaLarga(e.fecha))} · ` : "";
  return `<div class="cal-evento ${clase}" data-evento="${e.id}">
    <div class="cal-evento-cab"><span class="cal-evento-hora">${dia}${_escapeHtml(calHoraTexto(e))}</span><span class="cal-evento-titulo">${_escapeHtml(e.titulo)}</span></div>
    ${meta ? `<p class="cal-evento-meta">${meta}</p>` : ""}
    ${calVinculosHtml(e)}
    ${e.descripcion ? `<p class="cal-evento-notas">${_escapeHtml(e.descripcion)}</p>` : ""}
    <div class="cal-evento-acciones">
      <button type="button" class="cal-btn chico" data-cal-editar="${e.id}">Editar</button>
      ${e.link ? `<a class="cal-btn chico" href="${_escapeHtml(e.link)}" target="_blank" rel="noopener">Abrir en Google Calendar</a>` : ""}
      <button type="button" class="cal-btn chico peligro" data-cal-borrar="${e.id}">Borrar</button>
    </div>
  </div>`;
}

// Engancha los botones Editar / Borrar de las tarjetas dentro de `cont`.
//   porId: {id: evento}; alCambiar: se llama después de guardar o borrar; opciones: se pasa al modal.
function calEnlazarTarjetas(cont, porId, alCambiar, opciones = {}) {
  cont.querySelectorAll("[data-cal-editar]").forEach(b => b.addEventListener("click", () => {
    const e = porId[b.dataset.calEditar];
    if (e) calAbrirModal({ ...opciones, evento: e, alGuardar: alCambiar });
  }));
  cont.querySelectorAll("[data-cal-borrar]").forEach(b => b.addEventListener("click", async () => {
    const e = porId[b.dataset.calBorrar];
    if (e) calBorrar(e, alCambiar);
  }));
}

async function calBorrar(e, alBorrar) {
  if (!confirm(`¿Borrar «${e.titulo}»?\n\nTambién se borra de Google Calendar.`)) return;
  try {
    await apiJson(`/api/calendario/eventos/${e.id}`, "DELETE");
    mostrarAviso("Evento borrado");
    if (alBorrar) alBorrar();
  } catch (err) {
    mostrarAviso(`No se pudo borrar: ${err.message}`, "aviso");
  }
}

// ── Modal para crear / editar ────────────────────────────────────────
let _calModalCtx = null;

function _calAsegurarModal() {
  if (document.getElementById("modal-cal")) return;
  const div = document.createElement("div");
  div.id = "modal-cal";
  div.className = "modal-fondo";
  div.innerHTML = `
    <div class="modal-caja modal-cal" onclick="event.stopPropagation()">
      <button type="button" class="modal-cerrar" id="mc-x" aria-label="Cerrar">&times;</button>
      <h3 id="mc-titulo-modal">Nuevo evento</h3>
      <form id="mc-form" autocomplete="off">
        <label class="campo">Título
          <input type="text" id="mc-titulo" maxlength="200" placeholder="Ej: Reunión con Pérez" required />
        </label>
        <div class="mc-fila">
          <label class="campo">Fecha <input type="date" id="mc-fecha" required /></label>
          <label class="campo" id="mc-l-hora">Hora <input type="time" id="mc-hora" /></label>
          <label class="campo" id="mc-l-dur">Duración
            <select id="mc-dur">
              <option value="15">15 min</option><option value="30">30 min</option><option value="45">45 min</option>
              <option value="60" selected>1 hora</option><option value="90">1 h 30</option><option value="120">2 horas</option><option value="180">3 horas</option>
            </select>
          </label>
          <label class="campo" id="mc-l-hasta" hidden>Hasta (inclusive) <input type="date" id="mc-hasta" /></label>
        </div>
        <label class="check"><input type="checkbox" id="mc-todo" /> Todo el día</label>
        <fieldset class="mc-como" id="mc-como">
          <legend>Cómo es</legend>
          <label><input type="radio" name="mc-mod" value="meet" /> 🎥 Videollamada (Meet)</label>
          <label><input type="radio" name="mc-mod" value="llamada" /> 📞 Llamada</label>
          <label><input type="radio" name="mc-mod" value="presencial" /> 📍 Presencial</label>
        </fieldset>
        <p class="mc-ayuda" id="mc-meet-nota" hidden></p>
        <label class="campo" id="mc-l-lugar">Lugar <input type="text" id="mc-lugar" maxlength="300" /></label>
        <label class="campo">Actas vinculadas (opcional)
          <input type="text" id="mc-actas" placeholder="Ej: 4797001, 4797002" />
        </label>
        <div class="mc-marcas" id="mc-marcas" hidden></div>
        <div class="mc-vista-actas" id="mc-vista-actas" hidden></div>
        <p class="mc-ayuda">Con el número de acta, el evento queda vinculado al lead o cliente dueño de esa marca. También sirve escribir «Acta 4797001» en las notas, incluso si lo agendás directo en Google Calendar.</p>
        <label class="campo">Notas (opcional) <textarea id="mc-notas" rows="4" placeholder="Qué hay que hacer, qué traer…"></textarea></label>
        <div class="mc-aviso" id="mc-aviso-caja">
          <label class="check"><input type="checkbox" id="mc-avisar" /> <span id="mc-avisar-txt">Avisar por mail</span></label>
          <label class="campo" id="mc-l-email">Mail de la persona (si lo dejás vacío, el aviso va solo al equipo)
            <input type="text" id="mc-email" placeholder="persona@mail.com" />
          </label>
          <p class="mc-ayuda" id="mc-aviso-ayuda"></p>
        </div>
        <p class="mc-error" id="mc-error" hidden></p>
        <div class="acciones-modal">
          <button type="button" class="cal-btn peligro" id="mc-borrar" hidden>Borrar</button>
          <span style="flex:1"></span>
          <button type="button" class="cal-btn" id="mc-cancelar">Cancelar</button>
          <button type="submit" class="cal-btn principal" id="mc-guardar">Guardar</button>
        </div>
      </form>
    </div>`;
  div.addEventListener("click", calCerrarModal);
  document.body.appendChild(div);
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") calCerrarModal(); });
  document.getElementById("mc-x").addEventListener("click", calCerrarModal);
  document.getElementById("mc-cancelar").addEventListener("click", calCerrarModal);
  document.getElementById("mc-todo").addEventListener("change", _calPintarTodoElDia);
  document.querySelectorAll('input[name="mc-mod"]').forEach(r => r.addEventListener("change", _calPintarModalidad));
  document.getElementById("mc-avisar").addEventListener("change", _calPintarAviso);
  document.getElementById("mc-email").addEventListener("input", (ev) => { ev.target.dataset.manual = "1"; });
  document.getElementById("mc-fecha").addEventListener("change", () => {
    const h = document.getElementById("mc-hasta");
    if (!h.value || h.value < document.getElementById("mc-fecha").value) h.value = document.getElementById("mc-fecha").value;
  });
  document.getElementById("mc-form").addEventListener("submit", _calGuardar);
  document.getElementById("mc-borrar").addEventListener("click", () => {
    const ctx = _calModalCtx;
    if (!ctx || !ctx.evento) return;
    calCerrarModal();
    calBorrar(ctx.evento, ctx.alGuardar);
  });
  let temporizador = null;
  document.getElementById("mc-actas").addEventListener("input", () => {
    _calPintarChipsMarcas();
    clearTimeout(temporizador);
    temporizador = setTimeout(_calVistaPreviaActas, 350);
  });
}

function calCerrarModal() {
  const m = document.getElementById("modal-cal");
  if (m) m.classList.remove("abierto");
  _calModalCtx = null;
}

function _calPintarTodoElDia() {
  const todo = document.getElementById("mc-todo").checked;
  document.getElementById("mc-l-hora").hidden = todo;
  document.getElementById("mc-l-dur").hidden = todo;
  document.getElementById("mc-l-hasta").hidden = !todo;
  if (todo && !document.getElementById("mc-hasta").value) document.getElementById("mc-hasta").value = document.getElementById("mc-fecha").value;
}

function _calModalidad() {
  const r = document.querySelector('input[name="mc-mod"]:checked');
  return r ? r.value : null;
}

// Cambia el rótulo del campo «lugar» y muestra lo que corresponde según cómo sea el evento.
function _calPintarModalidad() {
  const $ = (id) => document.getElementById(id);
  const mod = _calModalidad();
  const ctx = _calModalCtx || {};
  const lugar = $("mc-lugar");
  const nota = $("mc-meet-nota");
  $("mc-l-lugar").hidden = mod === "meet";
  nota.hidden = mod !== "meet";
  if (mod === "meet") {
    nota.textContent = ctx.evento && ctx.evento.meet_url
      ? "Este evento ya tiene su videollamada de Meet; el link no cambia al guardar."
      : "Al guardar, Google genera la videollamada y el link de Meet queda en el evento y en el mail de aviso.";
  }
  const rotulo = { llamada: "Teléfono al que se llama", presencial: "Lugar" }[mod] || "Lugar o link (opcional)";
  $("mc-l-lugar").firstChild.textContent = rotulo + " ";
  lugar.placeholder = mod === "llamada" ? "Ej: 11 5555-1234" : mod === "presencial" ? "Ej: Estudio, Av. Corrientes 1234" : "Estudio, teléfono…";
}

function _calPintarAviso() {
  const $ = (id) => document.getElementById(id);
  const on = $("mc-avisar").checked;
  $("mc-l-email").hidden = !on;
  const equipo = (_calEstado && _calEstado.aviso_equipo) || "";
  $("mc-aviso-ayuda").hidden = !on;
  $("mc-aviso-ayuda").textContent = equipo
    ? `Se le manda el aviso a esa persona y una copia a ${equipo}.`
    : "No hay mail del equipo configurado: el aviso va solo a la persona.";
}

let _calEstado = null;
async function _calCargarEstado() {
  try { _calEstado = await api("/api/calendario/estado"); } catch (_) { _calEstado = _calEstado || null; }
}

function _calActasDelCampo() {
  return document.getElementById("mc-actas").value.split(/[\s,;]+/).filter(Boolean);
}

function _calPintarChipsMarcas() {
  const cont = document.getElementById("mc-marcas");
  const marcas = (_calModalCtx && _calModalCtx.marcas) || [];
  cont.hidden = !marcas.length;
  if (!marcas.length) return;
  const puestas = new Set(_calActasDelCampo());
  cont.innerHTML = marcas.map(m =>
    `<button type="button" data-acta="${_escapeHtml(m.acta)}" class="${puestas.has(m.acta) ? "activo" : ""}" title="Vincular o desvincular esta marca">${_escapeHtml(m.acta)}${m.denominacion ? " · " + _escapeHtml(m.denominacion) : ""}</button>`
  ).join("");
  cont.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    const actual = _calActasDelCampo();
    const i = actual.indexOf(b.dataset.acta);
    if (i >= 0) actual.splice(i, 1); else actual.push(b.dataset.acta);
    document.getElementById("mc-actas").value = actual.join(", ");
    _calPintarChipsMarcas();
    _calVistaPreviaActas();
  }));
}

async function _calVistaPreviaActas() {
  const caja = document.getElementById("mc-vista-actas");
  if (!caja) return;
  const validas = [...new Set(_calActasDelCampo().filter(a => /^\d{4,9}$/.test(a)))];
  if (!validas.length) { caja.hidden = true; return; }
  try {
    const r = await api(`/api/calendario/actas?actas=${validas.join(",")}`);
    caja.hidden = false;
    const correo = document.getElementById("mc-email");
    const conMail = r.actas.find(a => a.email);
    if (correo && !correo.dataset.manual && conMail && !correo.value) correo.value = conMail.email;
    caja.innerHTML = r.actas.map(a => {
      if (a.tipo === "cliente") return `✔ <b>${_escapeHtml(a.acta)}</b>: cliente <b>${_escapeHtml(a.cliente)}</b>${a.denominacion ? " · " + _escapeHtml(a.denominacion) : ""}`;
      if (a.tipo === "lead") return `✔ <b>${_escapeHtml(a.acta)}</b>: lead <b>${_escapeHtml(a.titular || "")}</b>${a.denominacion ? " · " + _escapeHtml(a.denominacion) : ""}`;
      if (a.tipo === "tercero") return `• <b>${_escapeHtml(a.acta)}</b>: solicitud de ${_escapeHtml(a.titular || "un tercero")} (con agente, no es lead ni cliente)`;
      return `<span class="mal">⚠ <b>${_escapeHtml(a.acta)}</b>: no está en la base (el evento se guarda igual y se vincula si después se carga)</span>`;
    }).join("<br>");
  } catch (_) { caja.hidden = true; }
}

/* Abre el formulario.
 *   evento      → si viene, se edita; si no, se crea.
 *   fecha       → fecha inicial (AAAA-MM-DD) para uno nuevo.
 *   actas       → actas con las que arranca uno nuevo.
 *   marcas      → [{acta, denominacion}] para tildar rápido (ficha del titular).
 *   titulo      → título sugerido para uno nuevo.
 *   alGuardar   → se llama (sin argumentos) después de guardar o borrar. */
async function calAbrirModal({ evento = null, fecha = null, actas = [], marcas = [], titulo = "", alGuardar = null } = {}) {
  _calAsegurarModal();
  _calModalCtx = { evento, marcas, alGuardar };
  await _calCargarEstado();
  if (!_calModalCtx) return;
  const $ = (id) => document.getElementById(id);
  $("mc-titulo-modal").textContent = evento ? "Editar evento" : "Nuevo evento";
  $("mc-error").hidden = true;
  $("mc-vista-actas").hidden = true;
  $("mc-borrar").hidden = !evento;
  $("mc-guardar").disabled = false;
  $("mc-guardar").textContent = "Guardar";

  const durSel = $("mc-dur");
  [...durSel.querySelectorAll("option[data-extra]")].forEach(o => o.remove());
  if (evento) {
    $("mc-titulo").value = evento.titulo;
    $("mc-fecha").value = evento.fecha;
    $("mc-todo").checked = !!evento.todo_el_dia;
    $("mc-hora").value = evento.hora || "";
    $("mc-hasta").value = evento.fecha_fin || evento.fecha;
    $("mc-lugar").value = evento.lugar || "";
    $("mc-actas").value = (evento.actas || []).join(", ");
    $("mc-notas").value = evento.descripcion || "";
    if (!evento.todo_el_dia && evento.hora && evento.hora_fin) {
      const min = calDiffDias(evento.fecha, evento.fecha_fin) * 1440 + calMinutos(evento.hora_fin) - calMinutos(evento.hora);
      if (min >= 5 && ![...durSel.options].some(o => +o.value === min)) {
        const o = document.createElement("option");
        o.value = min; o.dataset.extra = "1";
        o.textContent = min % 60 === 0 ? `${min / 60} h` : `${Math.floor(min / 60)} h ${min % 60} min`;
        durSel.appendChild(o);
      }
      durSel.value = String(min);
    }
  } else {
    $("mc-titulo").value = titulo || "";
    $("mc-fecha").value = fecha || calHoy();
    $("mc-todo").checked = false;
    $("mc-hora").value = "";
    $("mc-hasta").value = $("mc-fecha").value;
    $("mc-lugar").value = "";
    $("mc-actas").value = (actas || []).join(", ");
    $("mc-notas").value = "";
    durSel.value = "60";
  }
  // Cómo es: Meet solo se ofrece si el panel está conectado con la cuenta de Google del estudio
  // (o si el evento ya tiene su Meet).
  const puedeMeet = !!(_calEstado && _calEstado.puede_meet) || !!(evento && evento.meet_url);
  const radioMeet = document.querySelector('input[name="mc-mod"][value="meet"]');
  radioMeet.disabled = !puedeMeet;
  radioMeet.parentElement.title = puedeMeet ? "" : "Para generar el link de Meet hay que conectar el panel con la cuenta de Google (Calendario → Conectar con Google)";
  radioMeet.parentElement.classList.toggle("deshabilitado", !puedeMeet);
  const modInicial = evento ? (evento.meet_url ? "meet" : evento.modalidad) : (puedeMeet ? "meet" : "llamada");
  document.querySelectorAll('input[name="mc-mod"]').forEach(r => { r.checked = r.value === modInicial; });
  // Aviso por mail: tildado al agendar uno nuevo; al editar, a elección.
  const correo = $("mc-email");
  correo.value = "";
  delete correo.dataset.manual;
  $("mc-avisar").checked = !evento;
  $("mc-avisar-txt").textContent = evento ? "Avisar del cambio por mail" : "Avisar por mail (a la persona y al equipo)";
  _calPintarModalidad();
  _calPintarAviso();
  _calPintarTodoElDia();
  _calPintarChipsMarcas();
  if ($("mc-actas").value) _calVistaPreviaActas();
  document.getElementById("modal-cal").classList.add("abierto");
  setTimeout(() => $("mc-titulo").focus(), 50);
}

async function _calGuardar(ev) {
  ev.preventDefault();
  const $ = (id) => document.getElementById(id);
  const error = (t) => { $("mc-error").textContent = t; $("mc-error").hidden = false; };
  $("mc-error").hidden = true;

  const titulo = $("mc-titulo").value.trim();
  const todo = $("mc-todo").checked;
  const hora = $("mc-hora").value;
  if (!titulo) return error("Poné un título.");
  if (!$("mc-fecha").value) return error("Elegí la fecha.");
  if (!todo && !hora) return error("Poné la hora, o tildá «Todo el día».");
  if (todo && $("mc-hasta").value && $("mc-hasta").value < $("mc-fecha").value) return error("«Hasta» no puede ser anterior a la fecha de inicio.");
  const modalidad = _calModalidad();
  const avisar = $("mc-avisar").checked;
  const email = $("mc-email").value.trim();
  if (avisar && email && !email.split(/[,;\s]+/).filter(Boolean).every(m => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(m))) return error("El mail de la persona no es válido.");
  const actas = _calActasDelCampo();
  const mala = actas.find(a => !/^\d{4,9}$/.test(a));
  if (mala) return error(`«${mala}» no es un número de acta (son de 4 a 9 cifras, separados por coma).`);

  const cuerpo = {
    titulo,
    fecha: $("mc-fecha").value,
    hora: todo ? null : hora,
    duracion_min: +$("mc-dur").value,
    todo_el_dia: todo,
    fecha_fin: todo && $("mc-hasta").value && $("mc-hasta").value !== $("mc-fecha").value ? $("mc-hasta").value : null,
    descripcion: $("mc-notas").value,
    lugar: modalidad === "meet" ? "" : $("mc-lugar").value,
    actas: [...new Set(actas)],
    modalidad,
    avisar,
    email_aviso: avisar ? email : null,
  };
  const ctx = _calModalCtx || {};
  $("mc-guardar").disabled = true;
  $("mc-guardar").textContent = "Guardando…";
  try {
    const r = ctx.evento ? await apiJson(`/api/calendario/eventos/${ctx.evento.id}`, "PUT", cuerpo)
                         : await apiJson("/api/calendario/eventos", "POST", cuerpo);
    calCerrarModal();
    const partes = [ctx.evento ? "Evento actualizado (también en Google Calendar)" : "Evento agendado (también en Google Calendar)"];
    if (r && r.evento && r.evento.meet_url && modalidad === "meet") partes.push("videollamada de Meet creada");
    let hayError = false;
    const av = r && r.aviso;
    if (av) {
      for (const [quien, x] of [["a la persona", av.persona], ["al equipo", av.equipo]]) {
        if (!x) continue;
        if (x.enviado) partes.push(`aviso enviado ${quien} (${x.email})`);
        else { hayError = true; partes.push(`no se pudo avisar ${quien}: ${x.error}`); }
      }
    }
    mostrarAviso(partes.join(" · "), hayError ? "aviso" : undefined);
    if (ctx.alGuardar) ctx.alGuardar();
  } catch (e) {
    $("mc-guardar").disabled = false;
    $("mc-guardar").textContent = "Guardar";
    error(e.message);
  }
}

// ── Bloque «Agenda» dentro de la ficha del titular ───────────────────
let _calFichaUltimasMarcas = null;

async function calAgendaFicha(rows, titular) {
  const cont = document.getElementById("lead-agenda");
  if (!cont) return;
  _calFichaUltimasMarcas = rows;
  const marcas = rows.map(r => ({ acta: r.acta, denominacion: r.denominacion_inpi || r.denominacion || "" }));
  const actas = marcas.map(m => m.acta);
  const recargar = () => calAgendaFicha(rows, titular);
  let eventos = [], estado = null;
  try {
    estado = await api("/api/calendario/estado");
    if (estado.configurado) eventos = (await api(`/api/calendario/por-actas?actas=${actas.join(",")}`)).eventos;
  } catch (_) { /* si falla, se muestra vacío */ }

  const hoy = calHoy();
  const proximos = eventos.filter(e => e.fecha_fin >= hoy);
  const pasados = eventos.filter(e => e.fecha_fin < hoy).reverse();
  const porId = Object.fromEntries(eventos.map(e => [String(e.id), e]));
  const habilitado = estado && estado.configurado;
  cont.innerHTML = `<div class="cal-ficha">
    <div class="cal-ficha-cab">
      <h4>📅 Agenda de este titular</h4>
      ${habilitado ? '<button type="button" class="cal-btn principal chico" id="cal-ficha-nuevo">＋ Agendar</button>' : ""}
      <a class="cal-btn chico" href="/calendario" style="text-decoration:none;display:inline-flex;align-items:center">Ver calendario</a>
    </div>
    ${!habilitado ? '<p class="cal-nota">El calendario todavía no está conectado con Google Calendar. Ver «Calendario» en el menú.</p>'
      : proximos.length ? proximos.map(e => calTarjetaHtml(e, { fechaCorta: true })).join("") : '<p class="cal-nota">No hay nada agendado para este titular.</p>'}
    ${pasados.length ? `<details style="margin-top:8px"><summary class="cal-nota" style="cursor:pointer">Anteriores (${pasados.length})</summary>${pasados.map(e => calTarjetaHtml(e, { fechaCorta: true })).join("")}</details>` : ""}
    ${habilitado ? '<p class="cal-nota">Lo que agendás acá se crea también en Google Calendar. Si en Google escribís el número de acta de alguna de estas marcas en la nota del evento, aparece acá solo.</p>' : ""}
  </div>`;
  const nuevo = document.getElementById("cal-ficha-nuevo");
  if (nuevo) nuevo.addEventListener("click", () => calAbrirModal({
    fecha: hoy,
    actas: actas.length ? [actas[0]] : [],
    marcas: marcas.slice(0, 12),
    titulo: titular ? `Reunión ${titular}` : "",
    alGuardar: recargar,
  }));
  calEnlazarTarjetas(cont, porId, recargar, { marcas: marcas.slice(0, 12) });
}
