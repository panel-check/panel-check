/* Página /calendario: vista mensual y agenda de los eventos de Google Calendar,
 * con el detalle del día, vínculos por número de acta y alta/edición de eventos.
 * Las piezas compartidas con la ficha del titular están en calendario_comun.js. */

const $ = (id) => document.getElementById(id);
const esc = _escapeHtml;

const est = {
  vista: "mes",            // "mes" | "agenda"
  mes: calHoy().slice(0, 7) + "-01",
  elegido: calHoy(),
  eventos: [],
  estado: null,
  cargando: false,
};

try {
  const guardada = localStorage.getItem("calVista");
  est.vista = guardada === "mes" || guardada === "agenda" ? guardada : (window.innerWidth < 760 ? "agenda" : "mes");
} catch (_) { est.vista = window.innerWidth < 760 ? "agenda" : "mes"; }

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
  if (est.vista === "agenda") return { desde: primero, hasta: ultimoDiaDelMes(primero) };
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
    const { desde, hasta } = rangoVisible();
    est.eventos = (await api(`/api/calendario/eventos?desde=${desde}&hasta=${hasta}`)).eventos;
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
  aviso.innerHTML = mal
    ? `<div class="cal-aviso error"><b>La última sincronización con Google falló:</b> ${esc(e.ultimo_error)}
        ${e.cuenta_servicio ? `<br>Cuenta de servicio: <code>${esc(e.cuenta_servicio)}</code> (el calendario tiene que estar compartido con ese mail).` : ""}</div>`
    : "";
  const r = $("cal-resync");
  if (r) r.addEventListener("click", (ev) => { ev.preventDefault(); sincronizar(true); });
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
  for (const e of est.eventos) {
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
  $("cal-titulo").textContent = `${nombre.charAt(0).toUpperCase()}${nombre.slice(1)} ${d.getUTCFullYear()}`;
  $("cal-v-mes").classList.toggle("activo", est.vista === "mes");
  $("cal-v-agenda").classList.toggle("activo", est.vista === "agenda");
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
  return `<button type="button" class="cal-chip ${calClaseEvento(e)}" data-chip="${e.id}" title="${esc(e.titulo)}">${hora}${esc(e.titulo)}</button>`;
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
      const clases = ["cal-dia", dia.slice(0, 7) !== mesActual ? "otro-mes" : "", dia === hoy ? "hoy" : "", dia === est.elegido ? "elegido" : ""].filter(Boolean).join(" ");
      const visibles = lista.slice(0, 3).map(chipHtml).join("");
      const mas = lista.length > 3 ? `<button type="button" class="cal-mas" data-mas="${dia}">+${lista.length - 3} más</button>` : "";
      celdas += `<div class="${clases}" data-dia="${dia}"><span class="cal-num">${+dia.slice(8)}</span>${visibles}${mas}</div>`;
    }
    semanas += `<div class="cal-semana">${celdas}</div>`;
  }
  $("cal-vista").innerHTML = `<div class="cal-mes"><div class="cal-semana-cab">${cab}</div>${semanas}</div>`;
}

function pintarDetalle(porDia) {
  const lista = porDia[est.elegido] || [];
  $("cal-detalle").innerHTML = `
    <div class="cal-detalle-cab">
      <h3>${esc(calFechaLarga(est.elegido))}</h3>
      <button type="button" class="cal-btn chico" id="cal-nuevo-dia">＋ Agendar este día</button>
    </div>
    ${lista.length ? lista.map(e => calTarjetaHtml(e)).join("") : '<p class="cal-vacio">No hay nada agendado este día.</p>'}`;
  const porId = Object.fromEntries(est.eventos.map(e => [String(e.id), e]));
  calEnlazarTarjetas($("cal-detalle"), porId, () => cargar());
  $("cal-nuevo-dia").addEventListener("click", () => calAbrirModal({ fecha: est.elegido, alGuardar: () => cargar() }));
}

function pintarAgenda(porDia) {
  const hoy = calHoy();
  const dias = Object.keys(porDia).sort();
  if (!dias.length) { $("cal-vista").innerHTML = '<p class="cal-vacio">No hay eventos este mes.</p>'; return; }
  $("cal-vista").innerHTML = dias.map(d => `
    <div class="cal-agenda-dia ${d === hoy ? "hoy" : ""} ${d < hoy ? "pasado" : ""}">
      <h3>${esc(calFechaLarga(d))}${d === hoy ? " · hoy" : ""}</h3>
      ${porDia[d].map(e => calTarjetaHtml(e)).join("")}
    </div>`).join("");
  const porId = Object.fromEntries(est.eventos.map(e => [String(e.id), e]));
  calEnlazarTarjetas($("cal-vista"), porId, () => cargar());
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
$("cal-nuevo").addEventListener("click", () => calAbrirModal({ fecha: est.elegido, alGuardar: () => cargar() }));
for (const [id, vista] of [["cal-v-mes", "mes"], ["cal-v-agenda", "agenda"]]) {
  $(id).addEventListener("click", () => {
    est.vista = vista;
    try { localStorage.setItem("calVista", vista); } catch (_) {}
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
