/* Seguimientos (/seguimientos): tablero de trabajos puntuales por servicio + ficha lateral.
 * La lógica de plazos y orden viene calculada del servidor (trabajos_core.py). */

const sg = {
  estado: "activo",       // activo | cerrado | todos
  filtro: "",             // filtro rápido del resumen: urgentes | vencidos | falta | ""
  q: "",
  datos: null,            // respuesta de /api/trabajos
  abierto: null,          // id del trabajo con la ficha abierta
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function pedir(path, opciones = {}) {
  const o = { credentials: "include", ...opciones };
  if (o.body && typeof o.body !== "string") {
    o.body = JSON.stringify(o.body);
    o.headers = { "Content-Type": "application/json", ...(o.headers || {}) };
  }
  const r = await fetch(path, o);
  if (r.status === 401 && typeof irAlLogin === "function") return irAlLogin();
  let j = null;
  try { j = await r.json(); } catch (_) { /* sin cuerpo */ }
  if (!r.ok) throw new Error((j && j.detail) || `Error ${r.status}`);
  return j;
}

function fecha(iso, larga) {
  if (!iso) return "";
  const [a, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  const f = new Date(a, m - 1, d);
  return larga
    ? f.toLocaleDateString("es-AR", { weekday: "short", day: "numeric", month: "long", year: "numeric" })
    : f.toLocaleDateString("es-AR", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

function servicioDe(id) {
  return (sg.datos?.servicios || []).find((s) => s.id === id) || { nombre: id, color: "#9aa1ab", etapas: [] };
}

function chipAlerta(a, chico) {
  if (!a) return "";
  return `<span class="sg-alerta ${esc(a.nivel)}"><span class="dot"></span>${esc(a.texto)}</span>`;
}

// ── Tablero ─────────────────────────────────────────────────────

async function cargar() {
  const p = new URLSearchParams({ estado: sg.estado });
  if (sg.q) p.set("q", sg.q);
  try {
    sg.datos = await pedir(`/api/trabajos?${p}`);
  } catch (e) {
    $("tablero").innerHTML = `<p class="sg-vacio-total">No se pudo cargar: ${esc(e.message)}</p>`;
    return;
  }
  $("link-procesos").href = sg.datos.notion_procesos;
  pintar();
}

function pasaFiltro(t) {
  const n = t.alerta?.nivel;
  if (sg.filtro === "urgentes") return n === "rojo" || n === "amarillo";
  if (sg.filtro === "vencidos") return n === "vencido";
  if (sg.filtro === "falta") return n === "falta";
  return true;
}

function pintarResumen(trabajos) {
  const cuenta = (f) => trabajos.filter(f).length;
  const stats = [
    { id: "", txt: sg.estado === "cerrado" ? "cerrados" : "trabajos", n: trabajos.length, cls: "" },
    { id: "urgentes", txt: "vencen en 7 días", n: cuenta((t) => ["rojo", "amarillo"].includes(t.alerta?.nivel)), cls: "rojo" },
    { id: "vencidos", txt: "vencidos", n: cuenta((t) => t.alerta?.nivel === "vencido"), cls: "negro" },
    { id: "falta", txt: "sin notificación", n: cuenta((t) => t.alerta?.nivel === "falta"), cls: "ambar" },
  ];
  $("resumen").innerHTML = stats
    .filter((s) => s.id === "" || s.n > 0 || sg.filtro === s.id)
    .map((s) => `<button type="button" class="sg-stat ${s.cls} ${sg.filtro === s.id ? "activa" : ""}" data-filtro="${s.id}" title="${s.id ? "Ver solo estos" : "Ver todos"}"><b>${s.n}</b>${esc(s.txt)}</button>`)
    .join("");
}

function tarjeta(t) {
  const s = servicioDe(t.servicio);
  const barra = s.etapas.map((_, i) => `<span class="${i < t.etapa ? "hecha" : ""}"></span>`).join("");
  const venc = t.vencimiento && t.alerta?.nivel !== "cumplido" ? `<span class="venc-fecha">${fecha(t.vencimiento)}</span>` : "";
  const inf = t.informes ? `<span class="sg-inf" title="${t.informes} informe${t.informes > 1 ? "s" : ""} de esta marca">📄 ${t.informes}</span>` : "";
  const sigue = t.estado !== "cerrado" && t.proxima_etapa
    ? `<div class="sg-sigue" title="Próxima etapa"><span class="flecha" aria-hidden="true">→</span>${esc(t.proxima_etapa)}</div>` : "";
  const acta = t.acta_efectiva ? `<span class="acta">Acta ${esc(t.acta_efectiva)}</span>` : `<span class="acta">Sin acta</span>`;
  return `<button type="button" class="sg-card ${t.estado === "cerrado" ? "cerrado" : ""}" data-id="${t.id}" style="--sg-color:${s.color}">
    <div class="marca">${esc(t.marca)}</div>
    <div class="cliente">${esc(t.cliente)}</div>
    <div class="fila">${t.alerta?.nivel === "ninguno" ? `<span class="contratado">Contratado el ${fecha(t.contratacion)}</span>` : chipAlerta(t.alerta) + venc}${inf}</div>
    <div class="sg-avance"><div class="barra">${barra}</div>
      <div class="txt"><span><b>${esc(t.etapa_nombre)}</b>${t.etapas_total ? ` · ${t.etapa}/${t.etapas_total}` : ""}</span>${acta}</div></div>
    ${sigue}
  </button>`;
}

function pintar() {
  const todos = sg.datos.trabajos;
  pintarResumen(todos);
  const visibles = todos.filter(pasaFiltro);
  const cols = sg.datos.servicios.map((s) => {
    const suyos = visibles.filter((t) => t.servicio === s.id);
    // Las columnas de servicios que no se usan no ocupan lugar (salvo que no haya nada cargado).
    if (!suyos.length && (sg.filtro || sg.q || !todos.some((t) => t.servicio === s.id))) return "";
    const sub = s.con_vencimiento ? `Ordenado por vencimiento · ${esc(s.plazo_texto)} desde la notificación` : "Más nuevos arriba";
    return `<section class="sg-col" style="--sg-color:${s.color}">
      <div class="sg-col-cab"><span class="punto" style="background:${s.color}"></span>${esc(s.nombre)}<span class="cant">${suyos.length}</span></div>
      <div class="sg-col-sub">${sub}</div>
      <div class="sg-cards">${suyos.map(tarjeta).join("") || '<div class="sg-col-vacia">Nada por acá</div>'}</div>
    </section>`;
  }).join("");
  $("tablero").innerHTML = cols || `<p class="sg-vacio-total">${sg.q || sg.filtro ? "No hay trabajos con ese filtro." : "Todavía no hay trabajos. Creá el primero con «＋ Nuevo trabajo»."}</p>`;
}

// ── Ficha ───────────────────────────────────────────────────────

// Campo de link (Drive, comprobante): siempre cerrado. Con URL muestra «✓ cargada · Cambiar»
// (el botón para abrirlo está arriba, en la cabecera); vacío, «Sin cargar · ＋ Agregar».
function campoLink(campo, nombre, valor, placeholder, cargado) {
  const input = `<input type="url" data-campo="${campo}" value="${esc(valor || "")}" placeholder="${placeholder}" />`;
  const boton = valor
    ? `<button type="button" class="sg-link-cambiar" data-cambiar-link title="Editar el link">✓ ${cargado} · <u>Cambiar</u></button>`
    : `<button type="button" class="sg-link-cambiar vacio" data-cambiar-link title="Cargar el link">Sin cargar · <u>＋ Agregar</u></button>`;
  return `<div class="ancho sg-link plegado"><div class="sg-link-cab"><span>${nombre}</span>${boton}</div>${input}</div>`;
}

async function abrirFicha(id) {
  const f = $("ficha");
  // Si la ficha ya está abierta con este trabajo, se refresca sin vaciarla
  // (así no parpadea en blanco al cambiar una etapa o un dato).
  const refresco = sg.abierto === id && f.classList.contains("abierta") && !f.querySelector(".sg-cargando");
  sg.abierto = id;
  if (!refresco) f.innerHTML = '<div class="sg-f-cab"><p class="sg-cargando">Cargando…</p></div>';
  f.classList.add("abierta"); f.setAttribute("aria-hidden", "false");
  $("velo").hidden = false;
  history.replaceState(null, "", `/seguimientos?trabajo=${id}`);
  try {
    const d = await pedir(`/api/trabajos/${id}`);
    if (sg.abierto !== id) return;
    const scroll = f.scrollTop;
    pintarFicha(d);
    if (refresco) f.scrollTop = scroll;
  } catch (e) {
    f.innerHTML = `<div class="sg-f-cab"><button class="cerrar" type="button" data-cerrar aria-label="Cerrar">×</button><p>No se pudo abrir: ${esc(e.message)}</p></div>`;
  }
}

function cerrarFicha() {
  sg.abierto = null;
  $("ficha").classList.remove("abierta");
  $("ficha").setAttribute("aria-hidden", "true");
  $("velo").hidden = true;
  history.replaceState(null, "", "/seguimientos");
}

function bloqueCuenta(t, s) {
  const a = t.alerta;
  if (a.nivel === "ninguno") return "";
  if (a.nivel === "cerrado") return `<div class="sg-cuenta cerrado"><div class="det"><b>Trabajo cerrado</b><br>Ya no cuenta para los vencimientos.</div></div>`;
  if (a.nivel === "cumplido")
    return `<div class="sg-cuenta cumplido"><div class="num">✓</div><div class="det"><b>Plazo cumplido</b><br>${esc(a.texto.replace("✓ ", ""))}${t.vencimiento ? ` · el plazo vencía el ${fecha(t.vencimiento)}` : ""}</div></div>`;
  if (a.nivel === "falta")
    return `<div class="sg-cuenta falta"><div class="num">?</div><div class="det"><b>Falta la fecha de notificación</b><br>Cargala abajo y el vencimiento se calcula solo (${esc(s.plazo_texto)}).</div></div>`;
  const dias = Math.abs(a.dias);
  return `<div class="sg-cuenta ${a.nivel}"><div class="num">${dias}<small>${dias === 1 ? "día" : "días"}</small></div>
    <div class="det"><b>${esc(a.texto)}</b><br>Vence el ${fecha(t.vencimiento, true)}<br>Notificado el ${fecha(t.notificacion)}</div></div>`;
}

function lineaTiempo(d) {
  const t = d.trabajo, ex = d.expediente;
  const items = [];
  (d.bitacora || []).forEach((b) => items.push({ fecha: b.fecha, orden: b.creado_en, tipo: "Nota", color: "#2563eb", texto: b.texto, quien: b.usuario, id: b.id }));
  (ex?.novedades || []).forEach((n) => items.push({ fecha: n.detectado_en, orden: n.detectado_en, tipo: "INPI", color: "#7c3aed", texto: n.texto }));
  if (ex?.marca?.fecha_publicacion) items.push({ fecha: ex.marca.fecha_publicacion, orden: ex.marca.fecha_publicacion, tipo: "INPI", color: "#7c3aed", texto: "Publicada en el boletín" });
  if (ex?.marca?.fecha_presentacion) items.push({ fecha: ex.marca.fecha_presentacion, orden: ex.marca.fecha_presentacion, tipo: "INPI", color: "#7c3aed", texto: "Solicitud presentada" });
  if (t.notificacion) items.push({ fecha: t.notificacion, orden: t.notificacion, tipo: "Plazo", color: "#ea580c", texto: `Notificación (${esc(t.servicio_nombre)})` });
  if (t.contratacion) items.push({ fecha: t.contratacion, orden: t.contratacion, tipo: "Contratación", color: "#16a34a", texto: "Nos contrató" });
  items.sort((a, b) => String(b.fecha).localeCompare(String(a.fecha)) || String(b.orden).localeCompare(String(a.orden)));
  if (!items.length) return '<p class="sg-exp-aviso">Todavía no hay movimientos.</p>';
  return `<ul class="sg-linea">${items.map((i) => `<li style="--c:${i.color}">
      <div class="cuando"><span class="tag" style="color:${i.color}">${esc(i.tipo)}</span>${fecha(i.fecha)}${i.quien ? ` · ${esc(i.quien)}` : ""}
        ${i.id ? `<button type="button" class="borrar" data-borrar-nota="${i.id}" title="Borrar esta nota">✕</button>` : ""}</div>
      <div>${esc(i.texto)}</div></li>`).join("")}</ul>`;
}

function bloqueExpediente(d) {
  const t = d.trabajo, ex = d.expediente;
  let datos = "";
  if (!ex) {
    datos = `<div class="sg-exp-aviso">Sin número de acta todavía. El panel la busca sola todos los días en las solicitudes nuevas del INPI (también las que presentamos con agente) y la carga cuando es seguro que es esta marca.
      <div class="sg-buscar-acta"><button type="button" class="sg-btn sg-btn-mini" data-buscar-acta>🔎 Buscar el acta en el INPI</button></div>
      <div id="resultado-acta"></div></div>`;
  } else if (!ex.en_cartera) {
    datos = `<p class="sg-exp-aviso">Acta <b>${esc(ex.acta)}</b>. Esta marca todavía no está en Clientes → Marcas, por eso no hay datos del INPI. Agregala ahí (en la ficha del cliente) y el expediente se completa solo.</p>`;
  } else {
    const m = ex.marca;
    const celda = (k, v) => (v ? `<div><span>${k}</span><b>${esc(v)}</b></div>` : "");
    datos = `<div class="sg-exp-datos">
      ${celda("Acta", m.acta + (ex.automatica ? " (vinculada sola por el nombre)" : ""))}
      ${celda("Estado del trámite", m.estado_tramite)}
      ${celda("Último movimiento", [m.ultimo_movimiento, m.ultimo_movimiento_fecha && fecha(m.ultimo_movimiento_fecha)].filter(Boolean).join(" · "))}
      ${celda("Clase", m.clase)}${celda("Titular", m.titular)}
      ${celda("Presentada", m.fecha_presentacion && fecha(m.fecha_presentacion))}
      ${celda("Publicada", m.fecha_publicacion && fecha(m.fecha_publicacion))}
      ${celda("Concedida", m.fecha_concesion && fecha(m.fecha_concesion))}
      ${m.tuvo_oposicion ? celda("Oposición", m.detalle_oposicion || "Sí") : ""}
    </div>`;
  }
  return `<div class="sg-bloque"><h3>📂 Expediente y bitácora</h3>${datos}
    <form class="sg-nota-nueva" id="form-nota"><input type="text" id="nota-texto" placeholder="Agregar una nota: «Le mandé el escrito para firmar»…" maxlength="4000" />
      <button type="submit" class="sg-btn sg-btn-mini">Agregar</button></form>
    ${lineaTiempo(d)}</div>`;
}

function bloqueInformes(d) {
  const item = (i) => `<li><a href="/informes/${i.id}" target="_blank" rel="noopener"><b>${esc(i.tipo_nombre)}</b>${i.marca ? ` · ${esc(i.marca)}` : ""}</a>
      <small>${fecha(i.generado_en)}${i.generado_por ? ` · ${esc(i.generado_por)}` : ""}</small></li>`;
  const suyos = d.informes || [], otros = d.otros_informes || [];
  if (!suyos.length && !otros.length) return "";
  return `<div class="sg-bloque"><h3>📄 Informes <span class="der"><a href="/clientes?cliente=${d.trabajo.cliente_id}&ctab=informes" data-cliente-popup="${d.trabajo.cliente_id}" data-ctab="informes">Ver en el cliente →</a></span></h3>
    ${suyos.length ? `<ul class="sg-informes">${suyos.map(item).join("")}</ul>` : '<p class="sg-exp-aviso">No hay informes de esta marca.</p>'}
    ${otros.length ? `<details class="sg-otros"><summary>Otros informes del cliente (${otros.length})</summary><ul class="sg-informes">${otros.map(item).join("")}</ul></details>` : ""}
  </div>`;
}

function pintarFicha(d) {
  const t = d.trabajo;
  const s = servicioDe(t.servicio);
  const f = $("ficha");
  f.style.setProperty("--sg-color", s.color);
  const pasos = s.etapas.map((e, i) => {
    const n = i + 1;
    const cls = n <= t.etapa ? "hecho" : "";
    return `<li class="sg-paso ${cls} ${n === t.etapa ? "actual" : ""}" data-etapa="${n}" title="Marcar «${esc(e)}» como etapa actual">
      <span class="bola">${n <= t.etapa ? "✓" : ""}</span><span class="nombre">${esc(e)}</span></li>`;
  }).join("");
  const opServ = d.servicios.map((x) => `<option value="${x.id}" ${x.id === t.servicio ? "selected" : ""}>${esc(x.nombre)}</option>`).join("");
  const enlace = (url, txt) => (url ? `<a class="sg-btn sg-btn-suave sg-btn-mini" href="${esc(url)}" target="_blank" rel="noopener">${txt}</a>` : "");
  f.innerHTML = `
    <div class="sg-f-cab">
      <button class="cerrar" type="button" data-cerrar aria-label="Cerrar">×</button>
      <span class="sg-f-serv">● ${esc(s.nombre)}</span>
      <div class="sg-f-marca"><span class="nombre" data-editar-marca title="Clic para editar el nombre de la marca">${esc(t.marca)}</span><button type="button" class="sg-f-lapiz" data-editar-marca title="Editar el nombre de la marca" aria-label="Editar el nombre de la marca">✎</button></div>
      <div class="sg-f-cliente"><a href="/clientes?cliente=${t.cliente_id}" data-cliente-popup="${t.cliente_id}" title="Ver la ficha del cliente">${esc(t.cliente)}</a>${t.cliente_telefono ? ` · ${esc(t.cliente_telefono)}` : ""}${t.cliente_email ? ` · ${esc(t.cliente_email)}` : ""}</div>
      <div class="sg-f-links">
        ${enlace(t.carpeta_drive, "📁 Carpeta Drive")}
        ${enlace(t.comprobante, "🧾 Comprobante")}
        ${enlace(d.notion_procesos, "📋 Procesos y mails")}
      </div>
    </div>
    <div class="sg-f-cuerpo">
      ${bloqueCuenta(t, s)}
      <div class="sg-bloque"><h3>Etapas <span class="der">${t.proxima_etapa ? `Sigue: <b>${esc(t.proxima_etapa)}</b>` : "✓ Todas completas"}</span></h3>
        <ol class="sg-pasos">${pasos}</ol></div>
      <div class="sg-bloque"><h3>Datos <span class="der sg-guardado" id="guardado">✓ Guardado</span></h3>
        <div class="sg-datos">
          <label>Servicio <select data-campo="servicio">${opServ}</select></label>
          <label>Acta <input type="text" inputmode="numeric" data-campo="acta" value="${esc(t.acta || "")}" placeholder="${t.acta_auto ? esc(t.acta_efectiva) + " (sola)" : "Sin presentar"}" /></label>
          <label>Contratación <input type="date" data-campo="contratacion" value="${esc(t.contratacion || "")}" /></label>
          ${s.con_vencimiento ? `<label>Notificación <input type="date" data-campo="notificacion" value="${esc(t.notificacion || "")}" /></label>
          <label>Vencimiento <span class="calc">${t.vencimiento ? fecha(t.vencimiento, true) : "—"}</span></label>` : ""}
          ${campoLink("carpeta_drive", "Carpeta de Drive", t.carpeta_drive, "https://drive.google.com/…", "cargada")}
          ${campoLink("comprobante", "Comprobante", t.comprobante, "Link al comprobante de pago", "cargado")}
          <label class="ancho">Notas <textarea data-campo="notas" rows="3">${esc(t.notas || "")}</textarea></label>
        </div></div>
      ${bloqueInformes(d)}
      ${bloqueExpediente(d)}
      <div class="sg-pie">
        <button type="button" class="sg-btn sg-btn-suave" data-estado-trabajo="${t.estado === "cerrado" ? "activo" : "cerrado"}">${t.estado === "cerrado" ? "↩ Reabrir trabajo" : "✓ Cerrar trabajo"}</button>
        <button type="button" class="sg-btn sg-btn-peligro" data-borrar-trabajo>🗑 Borrar</button>
      </div>
    </div>`;
}

function pintarCandidatas(r) {
  const cont = $("resultado-acta");
  if (!cont) return;
  const filas = (r.candidatas || []).map((c) => `<li class="sg-cand ${c.segura ? "segura" : ""}">
      <div><b>Acta ${esc(c.acta)}</b> · ${esc(c.denominacion || "")}${c.clase ? ` · clase ${esc(c.clase)}` : ""}${c.fecha ? ` · ${fecha(c.fecha)}` : ""}
        <br><small>${esc(c.titular || "Titular sin dato")}${c.estado ? ` · ${esc(c.estado)}` : ""}${c.motivos && c.motivos.length ? ` · ✓ ${esc(c.motivos.join(", "))}` : ""}</small></div>
      <button type="button" class="sg-btn sg-btn-suave sg-btn-mini" data-usar-acta="${esc(c.acta)}">Usar esta</button></li>`).join("");
  cont.innerHTML = `<p class="sg-cand-txt">${esc(r.texto || "")}</p>${filas ? `<ul class="sg-cands">${filas}</ul>` : ""}`;
}

async function guardar(cambios, repintar = true) {
  const id = sg.abierto;
  try {
    await pedir(`/api/trabajos/${id}`, { method: "PUT", body: cambios });
    const g = $("guardado");
    if (g) { g.classList.add("ver"); setTimeout(() => g.classList.remove("ver"), 1500); }
    if (repintar) await abrirFicha(id);
    cargar();
  } catch (e) {
    mostrarAviso(`No se guardó: ${e.message}`, "aviso");
  }
}

// ── Nuevo trabajo ───────────────────────────────────────────────

let clientesSugeridos = [];
async function buscarClientes(q) {
  try {
    const d = await pedir(`/api/clientes?q=${encodeURIComponent(q)}`);
    clientesSugeridos = d.clientes.slice(0, 30);
    $("n-clientes").innerHTML = clientesSugeridos.map((c) => `<option value="${esc(c.nombre)} · #${c.id}"></option>`).join("");
  } catch (_) { /* nada */ }
}

function clienteElegido() {
  const m = $("n-cliente").value.match(/#(\d+)\s*$/);
  return m ? Number(m[1]) : null;
}

function abrirNuevo() {
  const f = $("form-nuevo");
  f.reset();
  $("n-error").hidden = true;
  $("n-servicio").innerHTML = sg.datos.servicios.map((s) => `<option value="${s.id}">${esc(s.nombre)}</option>`).join("");
  $("n-contratacion").value = sg.datos.hoy;
  actualizarNotif();
  $("n-cliente-ayuda").textContent = "Tiene que ser un cliente de Clientes. Si no existe, crealo primero ahí.";
  $("n-cliente-ayuda").classList.remove("ok");
  buscarClientes("");
  $("modal-nuevo").showModal();
  $("n-cliente").focus();
}

function actualizarNotif() {
  const s = servicioDe($("n-servicio").value);
  $("n-notif-label").hidden = !s.con_vencimiento;
}

async function crearTrabajo(ev) {
  ev.preventDefault();
  const cliente_id = clienteElegido();
  const err = $("n-error");
  if (!cliente_id) { err.textContent = "Elegí el cliente de la lista (tiene que aparecer con su #número)."; err.hidden = false; return; }
  const body = {
    cliente_id, marca: $("n-marca").value, servicio: $("n-servicio").value,
    contratacion: $("n-contratacion").value || null,
    notificacion: servicioDe($("n-servicio").value).con_vencimiento ? ($("n-notificacion").value || null) : null,
    acta: $("n-acta").value || null, carpeta_drive: $("n-drive").value || null, notas: $("n-notas").value || null,
  };
  try {
    $("n-guardar").disabled = true;
    const r = await pedir("/api/trabajos", { method: "POST", body });
    $("modal-nuevo").close();
    await cargar();
    abrirFicha(r.trabajo.id);
  } catch (e) {
    err.textContent = e.message; err.hidden = false;
  } finally {
    $("n-guardar").disabled = false;
  }
}

// ── Eventos ─────────────────────────────────────────────────────

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-id], [data-filtro], [data-estado], [data-cerrar], [data-etapa], [data-estado-trabajo], [data-borrar-trabajo], [data-borrar-nota], [data-buscar-acta], [data-usar-acta]");
  if (!el) return;
  if (el.matches(".sg-card[data-id]")) return abrirFicha(Number(el.dataset.id));
  if (el.matches("[data-filtro]")) { sg.filtro = sg.filtro === el.dataset.filtro ? "" : el.dataset.filtro; return pintar(); }
  if (el.matches(".sg-estado [data-estado]")) {
    sg.estado = el.dataset.estado; sg.filtro = "";
    document.querySelectorAll(".sg-estado button").forEach((b) => b.classList.toggle("activa", b === el));
    return cargar();
  }
  if (el.matches("[data-cerrar]")) return cerrarFicha();
  if (el.matches("[data-etapa]")) {
    const n = Number(el.dataset.etapa);
    const actual = Number($("ficha").querySelector(".sg-paso.actual")?.dataset.etapa || 0);
    const nueva = n === actual ? n - 1 : n;
    // Se marca al instante; el guardado y el refresco van por detrás.
    $("ficha").querySelectorAll(".sg-paso").forEach((li) => {
      const k = Number(li.dataset.etapa);
      li.classList.toggle("hecho", k <= nueva);
      li.classList.toggle("actual", k === nueva);
      li.querySelector(".bola").textContent = k <= nueva ? "✓" : "";
    });
    return guardar({ etapa: nueva });   // tocar la etapa actual la desmarca
  }
  if (el.matches("[data-estado-trabajo]")) return guardar({ estado: el.dataset.estadoTrabajo });
  if (el.matches("[data-usar-acta]")) {
    await guardar({ acta: el.dataset.usarActa });
    return mostrarAviso(`Acta ${el.dataset.usarActa} cargada`);
  }
  if (el.matches("[data-buscar-acta]")) {
    const id = sg.abierto;
    el.disabled = true; el.textContent = "Buscando en el INPI…";
    try {
      const r = await pedir(`/api/trabajos/${id}/buscar-acta`, { method: "POST" });
      if (r.asignada) {
        mostrarAviso(`Acta ${r.asignada} encontrada y cargada`);
        await abrirFicha(id); cargar();
      } else {
        pintarCandidatas(r);
        el.disabled = false; el.textContent = "🔎 Buscar de nuevo";
      }
    } catch (e) {
      el.disabled = false; el.textContent = "🔎 Buscar el acta en el INPI";
      mostrarAviso(`No se pudo buscar: ${e.message}`, "aviso");
    }
    return;
  }
  if (el.matches("[data-borrar-trabajo]")) {
    if (!confirm("¿Borrar este trabajo y su bitácora? No se puede deshacer. (Si terminó, mejor «Cerrar trabajo»)")) return;
    try {
      await pedir(`/api/trabajos/${sg.abierto}`, { method: "DELETE" });
      cerrarFicha(); cargar(); mostrarAviso("Trabajo borrado");
    } catch (e) { mostrarAviso(`No se pudo borrar: ${e.message}`, "aviso"); }
    return;
  }
  if (el.matches("[data-borrar-nota]")) {
    if (!confirm("¿Borrar esta nota?")) return;
    try {
      await pedir(`/api/trabajos/${sg.abierto}/bitacora/${el.dataset.borrarNota}`, { method: "DELETE" });
      abrirFicha(sg.abierto);
    } catch (e) { mostrarAviso(`No se pudo borrar: ${e.message}`, "aviso"); }
  }
});

// Datos de la ficha: se guardan solos al salir del campo (o al elegir en un select / fecha).
document.addEventListener("change", (ev) => {
  const c = ev.target.closest("#ficha [data-campo]");
  if (!c) return;
  const campo = c.dataset.campo;
  const valor = c.value.trim() || null;
  // Los campos que cambian cálculos o el diseño de la ficha la redibujan; el resto no (así no se pierde el foco).
  guardar({ [campo]: valor }, ["servicio", "notificacion", "acta", "contratacion", "carpeta_drive", "comprobante"].includes(campo));
});

document.addEventListener("submit", async (ev) => {
  if (ev.target.id === "form-nota") {
    ev.preventDefault();
    const texto = $("nota-texto").value.trim();
    if (!texto) return;
    try {
      await pedir(`/api/trabajos/${sg.abierto}/bitacora`, { method: "POST", body: { texto } });
      abrirFicha(sg.abierto);
    } catch (e) { mostrarAviso(`No se guardó la nota: ${e.message}`, "aviso"); }
  }
});

$("velo").addEventListener("click", cerrarFicha);
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && sg.abierto && !popupClienteAbierto()) cerrarFicha(); });

// ── Nombre de la marca editable ─────────────────────────────────
document.addEventListener("click", (ev) => {
  if (!ev.target.closest("#ficha [data-editar-marca]")) return;
  const caja = $("ficha").querySelector(".sg-f-marca");
  if (!caja || caja.querySelector("input")) return;
  const actual = caja.querySelector(".nombre").textContent;
  caja.innerHTML = `<input type="text" class="sg-f-marca-input" maxlength="200" value="${esc(actual)}" aria-label="Nombre de la marca" />`;
  const inp = caja.querySelector("input");
  inp.focus(); inp.select();
  let listo = false;
  const terminar = (guardarlo) => {
    if (listo) return; listo = true;
    const nuevo = inp.value.trim();
    if (guardarlo && nuevo && nuevo.toUpperCase() !== actual.toUpperCase()) return guardar({ marca: nuevo });
    abrirFicha(sg.abierto);   // sin cambios: vuelve a mostrar el nombre
  };
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); terminar(true); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); terminar(false); }
  });
  inp.addEventListener("blur", () => terminar(true));
});

// ── Ficha del cliente en un popup (la misma de Clientes, embebida) ─
function popupClienteAbierto() { const c = $("popcli"); return !!(c && !c.hidden); }
function abrirClientePopup(id, ctab) {
  let c = $("popcli");
  if (!c) {
    c = document.createElement("div");
    c.id = "popcli"; c.className = "sg-popcli";
    c.innerHTML = '<p class="sg-popcli-carga">Cargando la ficha del cliente…</p><iframe title="Ficha del cliente"></iframe>';
    document.body.appendChild(c);
  }
  const fr = c.querySelector("iframe");
  c.classList.remove("listo");
  fr.onload = () => { if (fr.src !== "about:blank") c.classList.add("listo"); };
  fr.src = `/clientes?cliente=${id}&embebido=1${ctab ? `&ctab=${ctab}` : ""}`;
  c.hidden = false;
}
function cerrarClientePopup() {
  const c = $("popcli");
  if (!c || c.hidden) return;
  c.hidden = true;
  c.querySelector("iframe").src = "about:blank";
  if (sg.abierto) abrirFicha(sg.abierto);   // por si se cambió el teléfono, el mail o un informe
  cargar();
}
document.addEventListener("click", (ev) => {
  const a = ev.target.closest("[data-cliente-popup]");
  if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;   // con Cmd/Ctrl se sigue abriendo en otra pestaña
  ev.preventDefault();
  abrirClientePopup(a.dataset.clientePopup, a.dataset.ctab);
});
window.addEventListener("message", (ev) => {
  if (ev.origin === location.origin && ev.data && ev.data.tipo === "panel-cerrar-cliente") cerrarClientePopup();
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && popupClienteAbierto()) cerrarClientePopup(); });

let timerBuscar = null;
$("buscar").addEventListener("input", (ev) => {
  clearTimeout(timerBuscar);
  timerBuscar = setTimeout(() => { sg.q = ev.target.value.trim(); cargar(); }, 250);
});

$("btn-nuevo").addEventListener("click", abrirNuevo);
$("n-cancelar").addEventListener("click", () => $("modal-nuevo").close());
$("n-servicio").addEventListener("change", actualizarNotif);
$("form-nuevo").addEventListener("submit", crearTrabajo);
let timerClientes = null;
$("n-cliente").addEventListener("input", (ev) => {
  const id = clienteElegido();
  const ayuda = $("n-cliente-ayuda");
  if (id) { ayuda.textContent = "✓ Cliente elegido"; ayuda.classList.add("ok"); return; }
  ayuda.classList.remove("ok");
  ayuda.textContent = "Tiene que ser un cliente de Clientes. Si no existe, crealo primero ahí.";
  clearTimeout(timerClientes);
  timerClientes = setTimeout(() => buscarClientes(ev.target.value), 250);
});

document.addEventListener("click", (ev) => {
  const b = ev.target.closest("#ficha [data-cambiar-link]");
  if (!b) return;
  const caja = b.closest(".sg-link");
  caja.classList.remove("plegado");
  const inp = caja.querySelector("input");
  inp.focus(); inp.select();
  inp.addEventListener("blur", () => { if (!inp.value.trim() && inp.defaultValue === "") caja.classList.add("plegado"); }, { once: true });
});

(async function iniciar() {
  await cargar();
  const id = Number(new URLSearchParams(location.search).get("trabajo"));
  if (id) abrirFicha(id);
})();
