/* Página /calendario: vista mensual y agenda de los eventos de Google Calendar,
 * con el detalle del día, vínculos por número de acta y alta/edición de eventos.
 * Las piezas compartidas con la ficha del titular están en calendario_comun.js. */

const $ = (id) => document.getElementById(id);
const esc = _escapeHtml;

const AGENDA_PASO = 30;       // días que se suman cada vez que se pide «ver más» en la agenda
const AGENDA_MAXIMO = 120;    // tope del servidor para un pedido

const EMBEBIDO = document.documentElement.classList.contains("embebido");   // panel lateral de 📞 (pestaña Disponibilidad)
// /disponibilidad abre directo la vista Disponibilidad (y en el panel lateral, siempre).
const VISTA_INICIAL = (EMBEBIDO || location.pathname.replace(/\/+$/, "") === "/disponibilidad") ? "disponibilidad" : "agenda";

const est = {
  cerrados: {},            // días sin reuniones del rango visible: {fecha: {id, motivo}}
  vista: VISTA_INICIAL,         // "agenda" (por defecto: desde hoy hacia adelante) | "mes" | "disponibilidad"
  seguimientos: [],        // recordatorios pendientes (y los hechos hace poco), para la agenda
  disp: null,              // disponibilidad cargada: {dias, duracion, margen}
  propuesta: null,         // lo que entendió el servidor de un texto de disponibilidad, antes de guardarlo
  agendaDias: AGENDA_PASO,
  mes: calHoy().slice(0, 7) + "-01",
  elegido: calHoy(),
  eventos: [],
  estado: null,
  cargando: false,
  abiertos: new Set(),     // eventos de hoy ya pasados que se desplegaron en la agenda
};

function calHace(iso) {
  if (!iso) return "nunca";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} días`;
}

// ── Rangos ───────────────────────────────────────────────────────────
function ultimoDiaDelMes(iso) {
  const d = calUTC(iso);
  return calIso(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0, 12)));
}

function rangoVisible() {
  const primero = est.mes;
  // La agenda arranca siempre hoy (los días pasados no se muestran) y avanza de a AGENDA_PASO días.
  if (est.vista === "agenda") return { desde: calHoy(), hasta: calSumarDias(calHoy(), est.agendaDias - 1) };
  const desde = calSumarDias(primero, -calDiaSemanaLunes(primero));
  return { desde, hasta: calSumarDias(desde, 41) };
}

// ── Carga ────────────────────────────────────────────────────────────
async function cargarEstado() {
  try { est.estado = await api("/api/calendario/estado"); } catch (_) { est.estado = null; }
  pintarEstado();
}

async function cargar({ silencioso = false } = {}) {
  if (est.cargando) return;
  est.cargando = true;
  try {
    await cargarEstado();
    const habilitado = est.estado && est.estado.configurado;
    $("cal-barra").hidden = !habilitado;
    if (!habilitado) { $("cal-vista").innerHTML = ""; $("cal-detalle").innerHTML = ""; $("cal-leyenda").hidden = true; return; }
    if (est.vista === "disponibilidad") {
      est.disp = await api("/api/calendario/disponibilidad");
      pintar();
      return;
    }
    const { desde, hasta } = rangoVisible();
    est.eventos = (await api(`/api/calendario/eventos?desde=${desde}&hasta=${hasta}`)).eventos;
    try {
      est.cerrados = Object.fromEntries((await api(`/api/calendario/dias-cerrados?desde=${desde}&hasta=${hasta}`)).dias.map(d => [d.fecha, d]));
    } catch (_) { est.cerrados = {}; }
    if (est.vista === "agenda") {
      try { est.seguimientos = (await api("/api/calendario/seguimientos")).seguimientos; } catch (_) { est.seguimientos = []; }
    }
    pintar();
  } catch (e) {
    if (!silencioso) $("cal-vista").innerHTML = `<p class="cal-vacio">No se pudo cargar el calendario (${esc(e.message)}).</p>`;
  } finally {
    est.cargando = false;
  }
}

// ── Estado de la conexión con Google ─────────────────────────────────
function pintarEstado() {
  const e = est.estado;
  const aviso = $("cal-aviso");
  const linea = $("cal-estado");
  if (!e) { linea.innerHTML = ""; aviso.innerHTML = '<div class="cal-aviso error">No se pudo consultar el estado del calendario.</div>'; return; }

  if (!e.configurado) {
    linea.innerHTML = '<span><span class="cal-punto espera"></span>Sin conectar con Google Calendar</span>';
    aviso.innerHTML = `<div class="cal-aviso"><b>Falta conectar el calendario con Google.</b> Esto se hace una sola vez:
      <ol>
        <li>En <b>Google Cloud</b>: activar la <i>Google Calendar API</i> y crear una <b>cuenta de servicio</b> con una clave JSON.</li>
        <li>En <b>Google Calendar</b>: compartir el calendario del estudio con el mail de esa cuenta de servicio, con permiso <i>«Hacer cambios en eventos»</i>.</li>
        <li>En <b>Railway</b> (servicio del panel), cargar dos variables:
          <code>GOOGLE_SERVICE_ACCOUNT_JSON</code> (el contenido completo del archivo JSON) y
          <code>GOOGLE_CALENDAR_ID</code> (el ID del calendario; en el principal de una cuenta de Gmail es el mismo mail).</li>
      </ol>
      Cuando el panel se reinicie con las variables, esta pantalla se conecta sola. Más detalle en <a href="/ayuda#calendario">Ayuda → Calendario</a>.
      ${e.cuenta_servicio ? `<br>Compartí el calendario con: <code>${esc(e.cuenta_servicio)}</code>` : ""}</div>`;
    return;
  }

  const mal = !!e.ultimo_error;
  const ultima = e.ultimo_ok_en ? `última sincronización ${calHace(e.ultimo_ok_en)}` : "todavía no sincronizó";
  linea.innerHTML = `
    <span><span class="cal-punto ${mal ? "mal" : (e.ultimo_ok_en ? "" : "espera")}"></span>Sincronizado con Google Calendar${e.calendario ? ` (${esc(e.calendario)})` : ""} · ${ultima}</span>
    <span>Se actualiza solo cada ${Math.round((e.sondeo_segundos || 120) / 60)} min</span>
    <a href="https://calendar.google.com/calendar/u/0/r" target="_blank" rel="noopener">Abrir Google Calendar ↗</a>
    <a href="#" id="cal-resync" title="Vuelve a leer todo el calendario desde cero">Resincronizar todo</a>`;
  let html = mal
    ? `<div class="cal-aviso error"><b>La última sincronización con Google falló:</b> ${esc(e.ultimo_error)}
        ${e.cuenta_servicio && !e.oauth_conectado ? `<br>Cuenta de servicio: <code>${esc(e.cuenta_servicio)}</code> (el calendario tiene que estar compartido con ese mail).` : ""}</div>`
    : "";
  if (!e.puede_meet) html += pasosMeet(e);
  aviso.innerHTML = html;
  const r = $("cal-resync");
  if (r) r.addEventListener("click", (ev) => { ev.preventDefault(); sincronizar(true); });
}

// Guía para poder generar links de Meet: hace falta conectar la cuenta de Google del estudio.
function pasosMeet(e) {
  if (e.oauth_pendiente) {
    return `<div class="cal-aviso"><b>Último paso para generar links de Meet:</b> conectar la cuenta de Google del estudio.
      <a class="cal-btn principal chico" href="/api/calendario/google/conectar">Conectar con Google</a>
      Entrá con <code>${esc(e.calendario || "la cuenta del estudio")}</code>, aceptá el permiso y copiá el token que aparece a Railway
      (variable <code>GOOGLE_OAUTH_REFRESH_TOKEN</code>). Lo tiene que hacer un administrador.</div>`;
  }
  return `<div class="cal-aviso"><b>Para generar links de Meet</b> el panel tiene que usar la cuenta de Google del estudio (la cuenta de servicio no puede). Se hace una sola vez:
    <ol>
      <li>En <b>Google Cloud</b> → <i>APIs y servicios</i> → <i>Credenciales</i> → <i>Crear credenciales</i> → <b>ID de cliente de OAuth</b>, tipo <i>Aplicación web</i>.
        En <i>URI de redireccionamiento autorizados</i> poné: <code>${esc(e.redirect_uri || "")}</code></li>
      <li>En la <i>Pantalla de consentimiento de OAuth</i>: tipo <i>Externo</i>, agregá el permiso de Calendar y <b>publicá la app («En producción»)</b> para que la conexión no venza a los 7 días. Google va a avisar que la app «no está verificada»: es la nuestra, se acepta.</li>
      <li>En <b>Railway</b> (servicio del panel): <code>GOOGLE_OAUTH_CLIENT_ID</code> y <code>GOOGLE_OAUTH_CLIENT_SECRET</code>.</li>
      <li>Volvé acá y tocá <b>Conectar con Google</b>.</li>
    </ol>
    Mientras tanto se puede agendar como llamada o presencial. Más detalle en <a href="/ayuda#calendario">Ayuda → Calendario</a>.</div>`;
}

async function sincronizar(completa = false) {
  const b = $("cal-sync");
  b.disabled = true;
  const txt = b.textContent;
  b.textContent = "Sincronizando…";
  try {
    const r = await apiJson(`/api/calendario/sincronizar?completa=${completa ? "true" : "false"}`, "POST");
    if (r.omitida) mostrarAviso("Ya hay una sincronización en curso; probá de nuevo en un momento.", "aviso");
    else mostrarAviso(r.cambios ? `Sincronizado: ${r.cambios} cambio${r.cambios === 1 ? "" : "s"} desde Google Calendar` : "Ya estaba al día");
  } catch (e) {
    mostrarAviso(`No se pudo sincronizar: ${e.message}`, "aviso");
  } finally {
    b.disabled = false;
    b.textContent = txt;
    cargar();
  }
}

// ── Pintado ──────────────────────────────────────────────────────────
function eventosPorDia() {
  const { desde, hasta } = rangoVisible();
  const porDia = {};
  // En la agenda los recordatorios van en su propio bloque (arriba), no repartidos por día.
  const lista0 = est.vista === "agenda" ? est.eventos.filter(e => e.tipo !== "seguimiento") : est.eventos;
  for (const e of lista0) {
    let d = e.fecha < desde ? desde : e.fecha;
    const fin = e.fecha_fin > hasta ? hasta : e.fecha_fin;
    for (let i = 0; d <= fin && i < 400; i++, d = calSumarDias(d, 1)) (porDia[d] ||= []).push(e);
  }
  for (const lista of Object.values(porDia)) {
    lista.sort((a, b) => (a.todo_el_dia ? "" : a.hora || "").localeCompare(b.todo_el_dia ? "" : b.hora || "") || a.id - b.id);
  }
  return porDia;
}

function pintar() {
  const d = calUTC(est.mes);
  const nombre = CAL_MESES[d.getUTCMonth()];
  const enAgenda = est.vista === "agenda";
  const enDisp = est.vista === "disponibilidad";
  $("cal-titulo").textContent = enDisp ? "Disponibilidad" : enAgenda ? "Próximos eventos" : `${nombre.charAt(0).toUpperCase()}${nombre.slice(1)} ${d.getUTCFullYear()}`;
  $("cal-ant").parentElement.hidden = enAgenda || enDisp;   // ‹ Hoy › solo tiene sentido en el mes
  $("cal-v-mes").classList.toggle("activo", est.vista === "mes");
  $("cal-v-agenda").classList.toggle("activo", enAgenda);
  $("cal-v-disp").classList.toggle("activo", enDisp);
  if (enDisp) {
    $("cal-leyenda").hidden = true;
    $("cal-detalle").innerHTML = "";
    pintarDisponibilidad();
    return;
  }
  const porDia = eventosPorDia();
  if (est.vista === "mes") {
    $("cal-leyenda").hidden = false;
    pintarMes(porDia);
    pintarDetalle(porDia);
  } else {
    $("cal-leyenda").hidden = true;
    $("cal-detalle").innerHTML = "";
    pintarAgenda(porDia);
  }
}

function chipHtml(e) {
  const hora = e.todo_el_dia ? "" : `<span class="cal-hora">${esc(e.hora)}</span>`;
  const icono = e.tipo === "seguimiento" ? "🔔 " : e.meet_url ? "🎥 " : e.modalidad === "llamada" ? "📞 " : "";
  return `<button type="button" class="cal-chip ${calClaseEvento(e)}" data-chip="${e.id}" title="${esc(e.titulo)}">${hora}${icono}${esc(e.titulo)}</button>`;
}

function pintarMes(porDia) {
  const { desde } = rangoVisible();
  const hoy = calHoy();
  const mesActual = est.mes.slice(0, 7);
  const cab = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"].map(n => `<div>${n}</div>`).join("");
  let semanas = "";
  for (let s = 0; s < 6; s++) {
    let celdas = "";
    for (let c = 0; c < 7; c++) {
      const dia = calSumarDias(desde, s * 7 + c);
      const lista = porDia[dia] || [];
      const cerrado = est.cerrados[dia];
      const clases = ["cal-dia", dia.slice(0, 7) !== mesActual ? "otro-mes" : "", dia === hoy ? "hoy" : "", dia === est.elegido ? "elegido" : "", cerrado ? "sin-reuniones" : ""].filter(Boolean).join(" ");
      const marca = cerrado ? `<span class="cal-sin-reu" title="Sin reuniones${cerrado.motivo ? `: ${esc(cerrado.motivo)}` : ""}">🚫 Sin reuniones${cerrado.motivo ? ` · ${esc(cerrado.motivo)}` : ""}</span>` : "";
      const visibles = lista.slice(0, 3).map(chipHtml).join("");
      const mas = lista.length > 3 ? `<button type="button" class="cal-mas" data-mas="${dia}">+${lista.length - 3} más</button>` : "";
      celdas += `<div class="${clases}" data-dia="${dia}"><span class="cal-num">${+dia.slice(8)}</span>${marca}${visibles}${mas}</div>`;
    }
    semanas += `<div class="cal-semana">${celdas}</div>`;
  }
  $("cal-vista").innerHTML = `<div class="cal-mes"><div class="cal-semana-cab">${cab}</div>${semanas}</div>`;
}

function sinReunionesHtml(dia) {
  const c = est.cerrados[dia];
  if (!c) return "";
  return `<p class="cal-sin-reu-aviso">🚫 <b>Sin reuniones este día</b>${c.motivo ? ` · ${esc(c.motivo)}` : ""}${(est.eventos.some(e => e.tipo !== "seguimiento" && !e.todo_el_dia && e.fecha <= dia && dia <= e.fecha_fin)) ? " (ya hay algo agendado, revisalo)" : ""}</p>`;
}

function pintarDetalle(porDia) {
  const lista = porDia[est.elegido] || [];
  $("cal-detalle").innerHTML = `
    <div class="cal-detalle-cab">
      <h3>${esc(calFechaLarga(est.elegido))}</h3>
      <button type="button" class="cal-btn chico" id="cal-nuevo-dia">＋ Agendar este día</button>
    </div>
    ${sinReunionesHtml(est.elegido)}
    ${lista.length ? lista.map(e => calTarjetaHtml(e)).join("") : '<p class="cal-vacio">No hay nada agendado este día.</p>'}`;
  const porId = Object.fromEntries(est.eventos.map(e => [String(e.id), e]));
  calEnlazarTarjetas($("cal-detalle"), porId, () => cargar());
  $("cal-nuevo-dia").addEventListener("click", () => calAbrirModal({ fecha: est.elegido, alGuardar: () => cargar() }));
}

function pintarAgenda(porDia) {
  const hoy = calHoy();
  const dias = Object.keys(porDia).filter(d => d >= hoy);
  for (const d of Object.keys(est.cerrados)) if (d >= hoy && !dias.includes(d)) dias.push(d);   // los días sin reuniones también figuran
  if (!dias.includes(hoy)) dias.unshift(hoy);   // hoy siempre figura, aunque no haya nada
  dias.sort();
  // Hoy: primero lo que falta (completo) y debajo lo que ya pasó, cerrado en una línea (se despliega al tocarlo).
  const cuerpoHoy = (evs) => {
    const proximos = evs.filter(e => !calEventoPasado(e));
    const pasados = evs.filter(e => calEventoPasado(e));
    let h = proximos.length ? proximos.map(e => calTarjetaHtml(e)).join("")
                            : (pasados.length ? '<p class="cal-vacio">No queda nada más por hoy.</p>' : '<p class="cal-vacio">No hay nada agendado hoy.</p>');
    if (pasados.length) {
      h += `<p class="cal-pasaron">Ya pasaron (${pasados.length})</p>` +
           pasados.map(e => calTarjetaHtml(e, { plegado: true, abierto: est.abiertos.has(String(e.id)) })).join("");
    }
    return h;
  };
  const dia = (d) => `
    <div class="cal-agenda-dia ${d === hoy ? "hoy" : ""}">
      <h3>${esc(calFechaLarga(d))}${d === hoy ? " · hoy" : ""}</h3>
      ${sinReunionesHtml(d)}
      ${d === hoy ? cuerpoHoy(porDia[d] || [])
        : (porDia[d] || []).map(e => calTarjetaHtml(e)).join("")}
    </div>`;
  const hayMas = est.agendaDias < AGENDA_MAXIMO;
  const pie = hayMas
    ? `<button type="button" class="cal-btn" id="cal-mas-dias">Ver ${AGENDA_PASO} días más</button>`
    : `<p class="cal-vacio">Se muestran los próximos ${est.agendaDias} días.</p>`;
  const sinNada = dias.length === 1 && !(porDia[hoy] || []).length
    ? `<p class="cal-vacio">No hay eventos en los próximos ${est.agendaDias} días.</p>` : "";
  $("cal-vista").innerHTML = htmlSeguimientos() + dias.map(dia).join("") + sinNada + `<div class="cal-agenda-pie">${pie}</div>`;
  const porId = Object.fromEntries([...est.eventos, ...est.seguimientos].map(e => [String(e.id), e]));
  calEnlazarTarjetas($("cal-vista"), porId, () => cargar());
  const nuevoSeg = $("cal-seg-nuevo");
  if (nuevoSeg) nuevoSeg.addEventListener("click", abrirRecordatorio);
  // los desplegados siguen abiertos cuando la agenda se vuelve a pintar (sincronización, guardar…)
  $("cal-vista").querySelectorAll("details.plegado").forEach(d => d.addEventListener("toggle", () => {
    if (d.open) est.abiertos.add(d.dataset.evento); else est.abiertos.delete(d.dataset.evento);
  }));
  const mas = $("cal-mas-dias");
  if (mas) mas.addEventListener("click", () => { est.agendaDias = Math.min(AGENDA_MAXIMO, est.agendaDias + AGENDA_PASO); cargar(); });
}

// ── Recordatorios (seguimientos) ─────────────────────────────────────
function abrirRecordatorio() {
  calAbrirModal({ recordatorio: true, fecha: calSumarDias(calHoy(), 1), alGuardar: () => cargar() });
}

function htmlSeguimientos() {
  const hoy = calHoy();
  const pendientes = est.seguimientos.filter(e => !e.hecho);
  const hechos = est.seguimientos.filter(e => e.hecho).reverse();
  const semana = calSumarDias(hoy, 7);
  const grupos = [
    ["atrasados", "⚠ Atrasados", pendientes.filter(e => e.fecha < hoy)],
    ["hoy", "Para hoy", pendientes.filter(e => e.fecha === hoy)],
    ["semana", "Próximos 7 días", pendientes.filter(e => e.fecha > hoy && e.fecha <= semana)],
    ["luego", "Más adelante", pendientes.filter(e => e.fecha > semana)],
  ].filter(g => g[2].length);
  const cuerpo = grupos.length
    ? grupos.map(([clase, titulo, lista]) => `<h4 class="cal-seg-grupo ${clase}">${titulo} (${lista.length})</h4>${lista.map(e => calTarjetaHtml(e, { fechaCorta: true })).join("")}`).join("")
    : '<p class="cal-vacio">No hay recordatorios pendientes. Se crean con «🔔 Recordatorio», o desde una reunión, o desde la ficha de cada lead.</p>';
  return `<details class="cal-seg-panel" open>
    <summary><span>🔔 Recordatorios <span class="cal-seg-cant ${grupos.length && grupos[0][0] === "atrasados" ? "alerta" : ""}">${pendientes.length}</span></span></summary>
    ${cuerpo}
    <div class="cal-seg-pie"><button type="button" class="cal-btn chico principal" id="cal-seg-nuevo">＋ Recordatorio</button></div>
    ${hechos.length ? `<details class="cal-seg-hechos"><summary>Hechos hace poco (${hechos.length})</summary>${hechos.map(e => calTarjetaHtml(e, { fechaCorta: true })).join("")}</details>` : ""}
  </details>`;
}

// ── Disponibilidad ───────────────────────────────────────────────────
const dispMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const dispHora = (min) => `${Math.floor(min / 60)}:${String(min % 60).padStart(2, "0")}`;

function dispBarra(d) {
  const ini = Math.min(...d.ventanas.map(v => dispMin(v.desde)));
  const fin = Math.max(...d.ventanas.map(v => dispMin(v.hasta)));
  const span = fin - ini || 1;
  const pos = (a, b) => `left:${((a - ini) / span * 100).toFixed(2)}%;width:${(Math.max(b - a, 0) / span * 100).toFixed(2)}%`;
  const vent = d.ventanas.map(v => `<span class="seg-vent" style="${pos(dispMin(v.desde), dispMin(v.hasta))}"></span>`).join("");
  const libres = d.tramos.map(t => `<span class="seg-libre" style="${pos(dispMin(t.desde), dispMin(t.hasta))}"></span>`).join("");
  const reun = d.reuniones.map(r => {
    const a = Math.max(dispMin(r.hora), ini), b = Math.min(dispMin(r.hora_fin), fin);
    return b > a ? `<span class="seg-reunion" style="${pos(a, b)}" title="${esc(r.hora)}–${esc(r.hora_fin)} · ${esc(r.titulo)}"></span>` : "";
  }).join("");
  return `<div class="cal-disp-barra" aria-hidden="true">${vent}${libres}${reun}</div>
          <div class="cal-disp-ejes"><span>${dispHora(ini)}</span><span>${dispHora(fin)}</span></div>`;
}

function dispDiaCerradoHtml(d) {
  const hoy = calHoy();
  const c = d.cerrado;
  const aviso = d.reuniones.length
    ? `<p class="cal-disp-reuniones cal-disp-ojo">⚠ Ese día ya hay ${d.reuniones.length === 1 ? "una reunión agendada" : `${d.reuniones.length} reuniones agendadas`}: ${d.reuniones.map(r => `<b>${esc(r.hora)}–${esc(r.hora_fin)}</b> ${esc(r.titulo)}`).join(" · ")}</p>` : "";
  return `<article class="cal-disp-dia cerrado ${d.fecha === hoy ? "hoy" : ""}">
    <header>
      <h3>${esc(calFechaLarga(d.fecha))}${d.fecha === hoy ? " · hoy" : ""}</h3>
      <span class="cal-disp-resumen"><b>🚫 Sin reuniones${c.motivo ? ` · ${esc(c.motivo)}` : ""}</b></span>
    </header>
    ${aviso}
    <div class="cal-disp-chips"><button type="button" class="cal-btn chico" data-borrar-cierre="${c.id}" title="Vuelve a ofrecer este día">Habilitar de nuevo este día</button></div>
  </article>`;
}

function dispDiaHtml(d, duracion) {
  if (d.cerrado) return dispDiaCerradoHtml(d);
  const hoy = calHoy();
  const turnos = d.libres.length
    ? d.libres.map(h => `<button type="button" class="cal-turno" data-turno="${esc(h)}" data-dia="${esc(d.fecha)}" title="Agendar una reunión de ${duracion} min a las ${esc(h)}">${esc(h)}</button>`).join("")
    : '<span class="cal-vacio">No queda ningún horario libre este día.</span>';
  const reuniones = d.reuniones.length
    ? `<p class="cal-disp-reuniones">Ya agendado: ${d.reuniones.map(r => `<b>${esc(r.hora)}–${esc(r.hora_fin)}</b> ${esc(r.titulo)}`).join(" · ")}</p>` : "";
  const ventanas = d.ventanas.map(v => `<span class="cal-disp-chip">${esc(dispHora(dispMin(v.desde)))} a ${esc(dispHora(dispMin(v.hasta)))}<button type="button" data-borrar-ventana="${v.id}" title="Quitar este horario" aria-label="Quitar este horario">✕</button></span>`).join("");
  return `<article class="cal-disp-dia ${d.fecha === hoy ? "hoy" : ""}">
    <header>
      <h3>${esc(calFechaLarga(d.fecha))}${d.fecha === hoy ? " · hoy" : ""}</h3>
      <span class="cal-disp-resumen">${d.capacidad} turno${d.capacidad === 1 ? "" : "s"} en total · ${d.agendadas} agendado${d.agendadas === 1 ? "" : "s"} · <b>${d.libres.length} libre${d.libres.length === 1 ? "" : "s"}</b></span>
    </header>
    <div class="cal-disp-chips">Libre de ${ventanas}</div>
    ${dispBarra(d)}
    ${reuniones}
    <div class="cal-turnos">${turnos}</div>
    ${d.libres.length ? `<button type="button" class="cal-btn chico" data-copiar-dia="${esc(d.fecha)}" title="Copia «${esc(d.texto)}» para pegarlo en un mensaje">📋 Copiar horarios libres</button>` : ""}
  </article>`;
}

function dispPintarLista() {
  const cont = $("disp-lista");
  if (!cont || !est.disp) return;
  const { dias, duracion, margen } = est.disp;
  if (!dias.length) {
    cont.innerHTML = '<p class="cal-vacio">Todavía no hay disponibilidad cargada. Escribila arriba, por ejemplo: «próximo martes libre de 8 a 15».</p>';
    return;
  }
  cont.innerHTML = `<div class="cal-disp-cab">
      <p class="cal-nota cal-nota-disp">Reuniones de ${duracion} min con ${margen} de margen. Tocá un horario para agendar; lo agendado se descuenta solo.</p>
      <button type="button" class="cal-btn chico" id="disp-copiar-todo">📋 Copiar todos los días</button>
    </div>${dias.map(d => dispDiaHtml(d, duracion)).join("")}`;
}

function dispPintarPrevia() {
  const cont = $("disp-previa");
  if (!cont) return;
  const p = est.propuesta;
  if (!p) { cont.innerHTML = ""; return; }
  const avisos = p.advertencias.length ? `<ul class="cal-disp-avisos">${p.advertencias.map(a => `<li>${esc(a)}</li>`).join("")}</ul>` : "";
  const cierres = p.cierres || [];
  if (!p.ventanas.length && !cierres.length) { cont.innerHTML = `<div class="cal-disp-previa">${avisos}</div>`; return; }
  cont.innerHTML = `<div class="cal-disp-previa">
    <p><b>Entendí:</b></p>
    <ul class="cal-disp-entendido">${p.ventanas.map((v, i) => `<li><span>${esc(v.texto)}</span><button type="button" class="cal-btn chico" data-quitar="${i}" title="No cargar este">✕</button></li>`).join("")}${
      cierres.map((c, i) => `<li><span>🚫 ${esc(c.texto)}</span><button type="button" class="cal-btn chico" data-quitar-cierre="${i}" title="No cargar este">✕</button></li>`).join("")}</ul>
    ${avisos}
    <div class="cal-disp-acciones">
      <button type="button" class="cal-btn principal" id="disp-guardar">Guardar</button>
      <span class="cal-disp-rep">
        <button type="button" class="cal-btn" id="disp-guardar-rep" title="Carga lo mismo para las próximas semanas (cada una se puede borrar por separado)">↻ Guardar y repetir cada semana</button>
        <label>durante <input type="number" id="disp-rep" min="1" max="12" value="4"> semanas más</label>
      </span>
      <button type="button" class="cal-btn" id="disp-cancelar">Cancelar</button>
    </div>
  </div>`;
}

async function dispInterpretar() {
  const texto = $("disp-texto").value.trim();
  if (!texto) { mostrarAviso("Escribí la disponibilidad, por ejemplo: próximo martes libre de 8 a 15.", "aviso"); return; }
  const b = $("disp-ir"), txt = b.textContent;
  b.disabled = true; b.textContent = "Leyendo…";
  try {
    est.propuesta = { ...(await apiJson("/api/calendario/disponibilidad/interpretar", "POST", { texto })), texto };
    dispPintarPrevia();
    const g = $("disp-guardar");
    if (g) g.focus();
  } catch (e) {
    mostrarAviso(`No se pudo interpretar: ${e.message}`, "aviso");
  } finally { b.disabled = false; b.textContent = txt; }
}

async function dispGuardar(repetir) {
  const p = est.propuesta;
  if (!p || !(p.ventanas.length || (p.cierres || []).length)) return;
  const semanas = repetir ? Math.max(1, Math.min(12, +$("disp-rep").value || 4)) : 0;
  for (const id of ["disp-guardar", "disp-guardar-rep"]) { const b = $(id); if (b) b.disabled = true; }
  try {
    const r = await apiJson("/api/calendario/disponibilidad", "POST", {
      ventanas: p.ventanas.map(({ fecha, desde, hasta }) => ({ fecha, desde, hasta })),
      cierres: (p.cierres || []).map(({ fecha, motivo }) => ({ fecha, motivo })), repetir_semanas: semanas,
    });
    const partes = [];
    if (p.ventanas.length) partes.push(`${r.creadas} horario${r.creadas === 1 ? "" : "s"}${r.repetidas ? `, ${r.repetidas} ya estaba${r.repetidas === 1 ? "" : "n"} cargado${r.repetidas === 1 ? "" : "s"}` : ""}`);
    if ((p.cierres || []).length) partes.push(`${r.cerrados + r.cerrados_repetidos} día${r.cerrados + r.cerrados_repetidos === 1 ? "" : "s"} sin reuniones`);
    mostrarAviso(`Disponibilidad guardada (${partes.join("; ")})`);
    est.propuesta = null;
    $("disp-texto").value = "";
    dispPintarPrevia();
    cargar();
  } catch (e) {
    for (const id of ["disp-guardar", "disp-guardar-rep"]) { const b = $(id); if (b) b.disabled = false; }
    mostrarAviso(`No se pudo guardar: ${e.message}`, "aviso");
  }
}

async function dispCopiar(texto, boton) {
  try { await navigator.clipboard.writeText(texto); }
  catch (_) {
    const t = document.createElement("textarea"); t.value = texto; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
  }
  if (boton) { const antes = boton.textContent; boton.textContent = "¡Copiado!"; setTimeout(() => { boton.textContent = antes; }, 1600); }
}

function pintarDisponibilidad() {
  // El cuadro de arriba se arma una sola vez: si el calendario se refresca solo mientras se escribe, no se pierde lo escrito.
  if (!$("disp-lista")) {
    $("cal-vista").innerHTML = `
      <section class="cal-disp-carga">
        <h3>Cargar disponibilidad</h3>
        <p class="cal-nota">Escribilo como lo dirías, con horarios o con días sin reuniones.
          <details class="cal-ejemplos"><summary>Ver ejemplos</summary>
            <i>próximo martes libre de 8 a 15</i><i>lunes y jueves de 9 a 13</i><i>mañana de 10 a 12 y de 15 a 17</i><i>el 14 de octubre de 8:30 a 12</i>
            <i>lunes 12/10 feriado</i><i>miércoles 14 sin reuniones, cumple de Pame</i>
          </details></p>
        <div class="cal-disp-fila">
          <input type="text" id="disp-texto" maxlength="1000" placeholder="próximo martes libre de 8 a 15" autocomplete="off">
          <button type="button" class="cal-btn principal" id="disp-ir">Interpretar</button>
        </div>
        <div id="disp-previa"></div>
      </section>
      <div id="disp-lista"></div>`;
    dispPintarPrevia();
    $("disp-ir").addEventListener("click", dispInterpretar);
    $("disp-texto").addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      // Enter una vez interpreta; con lo entendido a la vista y el texto sin cambios, Enter otra vez guarda.
      if (est.propuesta && (est.propuesta.ventanas.length || (est.propuesta.cierres || []).length) && est.propuesta.texto === $("disp-texto").value.trim()) dispGuardar(false);
      else dispInterpretar();
    });
    $("disp-texto").addEventListener("input", () => { if (est.propuesta && est.propuesta.texto !== $("disp-texto").value.trim()) { est.propuesta = null; dispPintarPrevia(); } });
    $("disp-previa").addEventListener("click", (ev) => {
      const q = ev.target.closest("[data-quitar]");
      if (q) { est.propuesta.ventanas.splice(+q.dataset.quitar, 1); dispPintarPrevia(); return; }
      const qc = ev.target.closest("[data-quitar-cierre]");
      if (qc) { est.propuesta.cierres.splice(+qc.dataset.quitarCierre, 1); dispPintarPrevia(); return; }
      if (ev.target.closest("#disp-guardar")) dispGuardar(false);
      else if (ev.target.closest("#disp-guardar-rep")) dispGuardar(true);
      else if (ev.target.closest("#disp-cancelar")) { est.propuesta = null; dispPintarPrevia(); }
    });
    $("disp-lista").addEventListener("click", async (ev) => {
      const turno = ev.target.closest("[data-turno]");
      if (turno && EMBEBIDO) {
        // En el panel lateral el formulario es la pestaña «Agendar»: se le pasa el día y la hora.
        parent.postMessage({ tipo: "agendar-turno", fecha: turno.dataset.dia, hora: turno.dataset.turno.padStart(5, "0") }, location.origin);
        return;
      }
      if (turno) {
        calAbrirModal({ fecha: turno.dataset.dia, hora: turno.dataset.turno.padStart(5, "0"), duracion: est.disp.duracion, alGuardar: () => cargar() });
        return;
      }
      const borrar = ev.target.closest("[data-borrar-ventana]");
      if (borrar) {
        if (!confirm("¿Quitar este horario de disponibilidad?")) return;
        try { await apiJson(`/api/calendario/disponibilidad/${borrar.dataset.borrarVentana}`, "DELETE"); cargar(); }
        catch (e) { mostrarAviso(`No se pudo borrar: ${e.message}`, "aviso"); }
        return;
      }
      const habilitar = ev.target.closest("[data-borrar-cierre]");
      if (habilitar) {
        try { await apiJson(`/api/calendario/disponibilidad/cierre/${habilitar.dataset.borrarCierre}`, "DELETE"); cargar(); }
        catch (e) { mostrarAviso(`No se pudo habilitar: ${e.message}`, "aviso"); }
        return;
      }
      const copiarDia = ev.target.closest("[data-copiar-dia]");
      if (copiarDia) {
        const d = est.disp.dias.find(x => x.fecha === copiarDia.dataset.copiarDia);
        if (d) dispCopiar(`Te puedo ofrecer estos horarios:\n${d.texto}`, copiarDia);
        return;
      }
      const todo = ev.target.closest("#disp-copiar-todo");
      if (todo) dispCopiar(`Te puedo ofrecer estos horarios:\n${est.disp.dias.filter(d => d.libres.length).map(d => d.texto).join("\n")}`, todo);
    });
  }
  dispPintarLista();
}

// ── Interacción ──────────────────────────────────────────────────────
function irAlMes(mes, dia = null) {
  est.mes = mes;
  est.elegido = dia || (mes.slice(0, 7) === calHoy().slice(0, 7) ? calHoy() : mes);
  cargar();
}

function moverMes(n) {
  const d = calUTC(est.mes);
  irAlMes(calIso(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1, 12))));
}

$("cal-ant").addEventListener("click", () => moverMes(-1));
$("cal-sig").addEventListener("click", () => moverMes(1));
$("cal-hoy").addEventListener("click", () => irAlMes(calHoy().slice(0, 7) + "-01", calHoy()));
$("cal-sync").addEventListener("click", () => sincronizar(false));
$("cal-agenda-mail").addEventListener("click", async () => {
  if (!confirm("¿Mandar ahora el mail AGENDA con las reuniones y llamadas de mañana?\n\nEs el mismo que sale solo a las 20 hs.")) return;
  const b = $("cal-agenda-mail"), txt = b.textContent;
  b.disabled = true; b.textContent = "Mandando…";
  try {
    const r = await apiJson("/api/calendario/agenda/enviar", "POST");
    if (r.estado === "sin_eventos") mostrarAviso("Mañana no hay reuniones ni llamadas: no se mandó nada.", "aviso");
    else mostrarAviso(`Agenda enviada a ${r.destinatarios} (${r.cantidad} ${r.cantidad === 1 ? "reunión o llamada" : "reuniones y llamadas"}).`);
  } catch (e) {
    mostrarAviso(`No se pudo mandar la agenda: ${e.message}`, "aviso");
  } finally { b.disabled = false; b.textContent = txt; }
});
$("cal-nuevo").addEventListener("click", () => calAbrirModal({ fecha: est.elegido, alGuardar: () => cargar() }));
$("cal-nuevo-seg").addEventListener("click", abrirRecordatorio);
for (const [id, vista] of [["cal-v-mes", "mes"], ["cal-v-agenda", "agenda"], ["cal-v-disp", "disponibilidad"]]) {
  $(id).addEventListener("click", () => {
    est.vista = vista;
    if (vista === "agenda") est.agendaDias = AGENDA_PASO;
    cargar();
  });
}

$("cal-vista").addEventListener("click", (ev) => {
  const chip = ev.target.closest("[data-chip]");
  if (chip) {
    ev.stopPropagation();
    const e = est.eventos.find(x => String(x.id) === chip.dataset.chip);
    if (e) calAbrirModal({ evento: e, alGuardar: () => cargar() });
    return;
  }
  const mas = ev.target.closest("[data-mas]");
  const celda = ev.target.closest("[data-dia]");
  const dia = mas ? mas.dataset.mas : celda && celda.dataset.dia;
  if (!dia) return;
  est.elegido = dia;
  if (dia.slice(0, 7) !== est.mes.slice(0, 7)) irAlMes(dia.slice(0, 7) + "-01", dia);
  else pintar();
  $("cal-detalle").scrollIntoView({ behavior: "smooth", block: "nearest" });
});

$("cal-vista").addEventListener("dblclick", (ev) => {
  const celda = ev.target.closest("[data-dia]");
  if (celda && !ev.target.closest("[data-chip]")) calAbrirModal({ fecha: celda.dataset.dia, alGuardar: () => cargar() });
});

// Se refresca solo mientras la pestaña está a la vista (el panel ya sincroniza con Google cada pocos minutos).
setInterval(() => { if (document.visibilityState === "visible" && !document.getElementById("modal-cal")?.classList.contains("abierto")) cargar({ silencioso: true }); }, 60000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") cargar({ silencioso: true }); });

cargar();
