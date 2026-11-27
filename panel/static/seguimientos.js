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
  // Con un dictamen o resolución del INPI (27/11/2026) la «próxima etapa» deja de tener sentido: manda el aviso.
  const sigue = t.estado !== "cerrado" && t.proxima_etapa && !t.resultado_inpi
    ? `<div class="sg-sigue" title="Próxima etapa"><span class="prox">Prox.</span>${esc(t.proxima_etapa)}</div>` : "";
  const extra = (t.actas_extra || []).length;
  const acta = t.acta_efectiva ? `<span class="acta" ${extra ? `title="También: ${esc(t.actas_extra.join(", "))}"` : ""}>Acta ${esc(t.acta_efectiva)}${extra ? ` +${extra}` : ""}</span>` : `<span class="acta">Sin acta</span>`;
  return `<button type="button" class="sg-card ${t.estado === "cerrado" ? "cerrado" : ""}" data-id="${t.id}" style="--sg-color:${s.color}">
    <div class="marca">${esc(t.marca)}</div>
    <div class="cliente">${esc(t.cliente)}</div>
    ${t.nos_oponemos ? `<div class="sg-contra" title="Oposición que presentamos nosotros (sale de una alerta de vigilancia)">Nos oponemos a <b>${esc(t.contraparte || "marca nueva")}</b></div>` : ""}
    ${t.resultado_inpi ? `<div class="sg-resultado ${esc(t.resultado_inpi.nivel)}" title="${esc(t.resultado_inpi.explicacion)} (acta ${esc(t.resultado_inpi.acta)})">${t.resultado_inpi.icono} ${esc(t.resultado_inpi.titulo)}${t.resultado_inpi.fecha ? ` · ${esc(t.resultado_inpi.fecha.slice(0, 5))}` : ""}</div>` : ""}
    <div class="fila">${t.alerta?.nivel === "ninguno" ? `<span class="contratado">Contratado el ${fecha(t.contratacion)}</span>` : chipAlerta(t.alerta) + venc}${inf}</div>
    <div class="sg-avance"><div class="barra">${barra}</div>
      <div class="txt"><span><b>${esc(t.etapa_nombre)}</b>${t.etapas_total ? ` · ${t.etapa}/${t.etapas_total}` : ""}</span>${acta}</div></div>
    ${sigue}
  </button>`;
}

// Última columna: los clientes que tienen la vigilancia marcaria contratada de verdad (Clientes → «Vigilancia
// contratada»), más los trabajos de vigilancia que haya cargados. Un clic abre la ficha del cliente encima.
// Cartel al pasar el mouse: las marcas vigiladas, una por renglón con sus clases (MARCA · clases 9, 35).
function tituloVigilancia(c) {
  const porMarca = new Map();
  for (const m of c.marcas_lista || []) {
    const d = (m.d || "(sin denominación)").trim();
    if (!porMarca.has(d)) porMarca.set(d, []);
    if (m.c != null && !porMarca.get(d).includes(m.c)) porMarca.get(d).push(m.c);
  }
  const lineas = [...porMarca].map(([d, cl]) => `• ${d}${cl.length ? ` · clase${cl.length > 1 ? "s" : ""} ${cl.join(", ")}` : ""}`);
  return lineas.length ? `Marcas vigiladas:\n${lineas.join("\n")}\n\nClic para ver las alertas` : `Ver las alertas de ${c.nombre}`;
}
function tarjetaVigilancia(c) {
  const alertas = c.alertas_nuevas
    ? `<span class="sg-alerta rojo"><span class="dot"></span>${c.alertas_nuevas} alerta${c.alertas_nuevas > 1 ? "s" : ""} sin revisar</span>`
    : `<span class="sg-alerta verde"><span class="dot"></span>Sin alertas nuevas</span>`;
  const curso = c.alertas_en_curso ? `<span class="sg-vig-dato">${c.alertas_en_curso} en seguimiento</span>` : "";
  return `<button type="button" class="sg-card sg-vig" data-vig-cliente="${c.id}" title="${esc(tituloVigilancia(c))}">
    <div class="marca">${esc(c.nombre)}</div>
    <div class="cliente">${c.marcas} marca${c.marcas === 1 ? "" : "s"} vigilada${c.marcas === 1 ? "" : "s"}${c.vigilancia_desde ? ` · desde el ${fecha(c.vigilancia_desde)}` : ""}</div>
    <div class="fila">${alertas}${curso}</div>
  </button>`;
}
function columnaVigilancia(s, trabajos) {
  if (sg.filtro || sg.estado === "cerrado") return trabajos.length ? columnaSimple(s, trabajos.map(tarjeta).join(""), trabajos.length) : "";
  const conTrabajo = new Set(trabajos.map((t) => t.cliente_id));
  const clientes = (sg.datos.vigilancia || []).filter((c) => !conTrabajo.has(c.id));
  if (!trabajos.length && !clientes.length && sg.q) return "";
  return columnaSimple(s, trabajos.map(tarjeta).join("") + clientes.map(tarjetaVigilancia).join(""), trabajos.length + clientes.length,
    "Clientes que la tienen contratada · alertas nuevas primero");
}
function columnaSimple(s, html, n, sub = "Más nuevos arriba") {
  return `<section class="sg-col" data-col="${s.id}" data-n="${n}" style="--sg-color:${s.color}">
      <div class="sg-col-cab"><span class="punto" style="background:${s.color}"></span>${esc(s.nombre)}<span class="cant">${n}</span></div>
      <div class="sg-col-sub">${sub}</div>
      <div class="sg-cards">${html || '<div class="sg-col-vacia">Ningún cliente tiene la vigilancia contratada</div>'}</div>
    </section>`;
}

// En el celular las columnas no se apilan: arriba hay una pestaña por servicio y se ve una columna a la vez.
function pestanasMovil(cols) {
  const tmp = document.createElement("div");
  tmp.innerHTML = cols;
  const lista = [...tmp.querySelectorAll(".sg-col")].map((c) => ({ id: c.dataset.col, n: Number(c.dataset.n || 0) }));
  if (!lista.some((c) => c.id === sg.colMovil)) sg.colMovil = (lista.find((c) => c.n > 0) || lista[0])?.id;
  $("tablero").dataset.colMovil = sg.colMovil || "";
  return `<nav class="sg-tabs-movil" aria-label="Servicios">${lista.map((c) => {
    const s = servicioDe(c.id);
    return `<button type="button" data-col-movil="${c.id}" class="${c.id === sg.colMovil ? "activa" : ""}" style="--sg-color:${s.color}">
      <span class="punto"></span>${esc(s.corto || s.nombre)}<b>${c.n}</b></button>`;
  }).join("")}</nav>`;
}
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-col-movil]");
  if (!b) return;
  sg.colMovil = b.dataset.colMovil;
  $("tablero").dataset.colMovil = sg.colMovil;
  document.querySelectorAll("[data-col-movil]").forEach((x) => x.classList.toggle("activa", x === b));
  window.scrollTo({ top: 0, behavior: "smooth" });
});

function pintar() {
  const todos = sg.datos.trabajos;
  pintarResumen(todos);
  const visibles = todos.filter(pasaFiltro);
  const cols = sg.datos.servicios.map((s) => {
    const suyos = visibles.filter((t) => t.servicio === s.id);
    if (s.id === "vigilancia") return columnaVigilancia(s, suyos);
    // Las columnas de servicios que no se usan no ocupan lugar (salvo que no haya nada cargado).
    if (!suyos.length && (sg.filtro || sg.q || !todos.some((t) => t.servicio === s.id))) return "";
    const sub = s.con_vencimiento ? `Ordenado por vencimiento · ${esc(s.plazo_texto)} desde la notificación` : "Más nuevos arriba";
    return `<section class="sg-col" data-col="${s.id}" data-n="${suyos.length}" style="--sg-color:${s.color}">
      <div class="sg-col-cab"><span class="punto" style="background:${s.color}"></span>${esc(s.nombre)}<span class="cant">${suyos.length}</span></div>
      <div class="sg-col-sub">${sub}</div>
      <div class="sg-cards">${suyos.map(tarjeta).join("") || '<div class="sg-col-vacia">Nada por acá</div>'}</div>
    </section>`;
  }).join("");
  $("tablero").innerHTML = cols ? pestanasMovil(cols) + cols : `<p class="sg-vacio-total">${sg.q || sg.filtro ? "No hay trabajos con ese filtro." : "Todavía no hay trabajos. Creá el primero con «＋ Nuevo trabajo»."}</p>`;
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
  if (typeof id === "string" && id.startsWith("vig:")) return abrirVigilancia(Number(id.slice(4)));
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

// ── Ficha de Vigilancia marcaria (panel lateral, como los trabajos) ─────────────
// Muestra las alertas del cliente con los mismos botones que en Clientes (POST /api/cartera/alertas/{id}).
// El nombre del cliente abre su ficha completa encima.
const VIG = {
  estados: { nueva: "Nueva", monitorear: "Monitorear", oponer: "Oponer", opuesta: "Oposición presentada", descartada: "Descartada", descartada_ia: "Descartada por IA" },
  veredictos: { identica: ["rojo", "Idéntica"], confundible: ["rojo", "Confundible"], cercana: ["amarillo", "Cercana"], no_confundible: ["", "No confundible"] },
  relacion: { misma: ["rojo", "Misma clase"], relacionada: ["amarillo", "Clase conexa"], distinta: ["", "Clase distinta"] },
  tipos: { D: "Denominativa", M: "Mixta", F: "Figurativa", T: "Tridimensional" },
};
const ABIERTAS = ["nueva", "monitorear", "oponer"];

async function abrirVigilancia(cid) {
  const f = $("ficha");
  const clave = `vig:${cid}`;
  const refresco = sg.abierto === clave && f.classList.contains("abierta") && !f.querySelector(".sg-cargando");
  sg.abierto = clave;
  if (!refresco) f.innerHTML = '<div class="sg-f-cab"><p class="sg-cargando">Cargando…</p></div>';
  f.classList.add("abierta"); f.setAttribute("aria-hidden", "false");
  $("velo").hidden = false;
  history.replaceState(null, "", `/seguimientos?vigilancia=${cid}`);
  try {
    const d = await pedir(`/api/clientes/${cid}`);
    if (sg.abierto !== clave) return;
    const scroll = f.scrollTop;
    pintarVigilancia(d);
    if (refresco) f.scrollTop = scroll;
  } catch (e) {
    f.innerHTML = `<div class="sg-f-cab"><button class="cerrar" type="button" data-cerrar aria-label="Cerrar">×</button><p>No se pudo abrir: ${esc(e.message)}</p></div>`;
  }
}

function alertaVig(a) {
  const abierta = ABIERTAS.includes(a.estado);
  const otro = a.tipo === "otro_agente";
  const nivel = a.nivel === "alta" ? "rojo" : a.nivel === "media" ? "amarillo" : "";
  const rel = VIG.relacion[a.relacion_clases];
  const ver = VIG.veredictos[a.ia_veredicto];
  const actasCli = (Array.isArray(a.actas_cliente) && a.actas_cliente.length ? a.actas_cliente : [{ acta: a.acta_cliente, clase: a.clase_cliente }])
    .filter((x) => x.acta);
  // «Hay que oponer» crea el trabajo en Oposición + negociación (o lleva a él si ya existe); «Descartar» la cierra.
  const trabajo = (sg.datos?.trabajos || []).find((t) => t.alerta_id === a.id);
  const botones = a.estado === "descartada_ia"
    ? [["nueva", "No estoy de acuerdo"]]
    : (otro ? [["descartada", "Descartar"]]
      : [["oponer", trabajo ? "Ver el trabajo de oposición →" : "Hay que oponer"], ["descartada", "Descartar"]])
      .filter(([e]) => e !== a.estado || e === "oponer").concat(!abierta ? [["nueva", "Reabrir"]] : []);
  const plazo = !otro && a.plazo_oposicion
    ? `<div class="sg-vig-plazo ${a.dias_oposicion !== null && a.dias_oposicion <= 7 ? "urgente" : ""}">⚠ ${a.dias_oposicion >= 0 ? "Plazo para oponerse hasta el" : "El plazo para oponerse cerró el"} <b>${fecha(a.plazo_oposicion)}</b>${a.dias_oposicion >= 0 ? ` (${a.dias_oposicion === 0 ? "hoy" : `${a.dias_oposicion} día${a.dias_oposicion === 1 ? "" : "s"}`})` : ""}</div>`
    : (!otro && abierta ? `<div class="sg-vig-plazo temprana">Todavía no salió en el boletín: el plazo de 30 días para oponerse empieza cuando se publique.</div>` : "");
  return `<div class="sg-vig-alerta ${abierta ? "" : "cerrada"}" data-alerta-vig="${a.id}">
    <div class="cab">
      ${otro ? '<span class="sg-tag azul">🔀 Otro agente</span>' : `<span class="sg-tag ${nivel}">${esc((a.nivel || "").toUpperCase())}</span><b class="pct">${a.puntaje ?? ""}%</b>${rel ? `<span class="sg-tag ${rel[0]}">${rel[1]}</span>` : ""}`}
      <span class="sg-tag ${abierta ? "azul" : ""}">${esc(VIG.estados[a.estado] || a.estado)}</span>
      <span class="cuando">${fecha(a.creada_en)}</span>
    </div>
    ${otro ? `<div class="txt"><b>${esc(a.titular_nuevo || "El cliente")}</b> presentó <b>${esc(a.denominacion_nueva || "una marca")}</b> (clase ${esc(a.clase_nueva ?? "?")}, acta ${linkActa(a.acta_nueva)})${a.agente_nuevo ? ` · ${esc(a.agente_nuevo)}` : ""}.</div>`
      : `<div class="cols">
      <div><small>Marca del cliente</small><b>${esc(a.denominacion_cliente || "")}</b>
        <span class="dato">${actasCli.map((x) => `cl. ${esc(x.clase ?? "?")} · ${linkActa(x.acta)}`).join("<br>")}</span></div>
      <div><small>Solicitud nueva</small><b>${esc(a.denominacion_nueva || "(sin nombre)")}</b>
        <span class="dato">cl. ${esc(a.clase_nueva ?? "?")} · ${linkActa(a.acta_nueva)}${a.tipo_nuevo ? ` · ${esc(VIG.tipos[a.tipo_nuevo] || a.tipo_nuevo)}` : ""}<br>
        ${esc(a.titular_nuevo || "")}${a.agente_nuevo ? ` · ${esc(a.agente_nuevo)}` : ""}${a.fecha_presentacion_nueva ? `<br>presentada ${fecha(a.fecha_presentacion_nueva)}` : ""}</span></div>
    </div>
    ${a.ia_veredicto ? `<div class="ia"><span class="sg-tag ${ver ? ver[0] : ""}">🤖 ${esc(ver ? ver[1] : a.ia_veredicto)}</span> ${textoNota(a.ia_razon || "")}</div>` : ""}`}
    ${plazo}
    <div class="acciones">${botones.map(([e, txt]) => `<button type="button" class="sg-btn sg-btn-mini ${e === "descartada" ? "sg-btn-peligro" : "sg-btn-suave"}" data-alerta-estado="${e}">${txt}</button>`).join("")}
      <input class="nota" data-alerta-nota placeholder="nota (se guarda al salir del campo)" value="${esc(a.nota || "")}"></div>
  </div>`;
}

function pintarVigilancia(d) {
  sg.ficha = { vigilancia: d };
  const c = d.cliente;
  const s = servicioDe("vigilancia");
  const f = $("ficha");
  f.style.setProperty("--sg-color", s.color);
  const alertas = (d.alertas || []).filter((a) => a.estado !== "pendiente_ia");
  const abiertas = alertas.filter((a) => ABIERTAS.includes(a.estado));
  const nuevas = abiertas.filter((a) => a.estado === "nueva").length;
  const revisadas = alertas.filter((a) => !ABIERTAS.includes(a.estado));
  const vigiladas = (d.marcas || []).filter((m) => m.vigilar !== false);
  const verRev = sg.vigRevisadas === c.id;
  f.innerHTML = `
    <div class="sg-f-cab">
      <button class="cerrar" type="button" data-cerrar aria-label="Cerrar">×</button>
      <span class="sg-f-serv">● ${esc(s.nombre)}</span>
      <div class="sg-f-marca"><span class="nombre">${esc(c.nombre)}</span></div>
      <div class="sg-f-cliente"><a href="/clientes?cliente=${c.id}" data-cliente-popup="${c.id}" title="Ver la ficha completa del cliente">Ver la ficha del cliente</a>${c.telefono ? ` · ${esc(c.telefono)}` : ""}${c.email ? ` · ${esc(c.email)}` : ""}</div>
      <div class="sg-f-links"><button type="button" class="sg-btn sg-btn-mini" data-armar-mail title="Armar un mail o WhatsApp con una plantilla y los datos del cliente">✉️ Mail</button></div>
    </div>
    <div class="sg-f-cuerpo">
      <div class="sg-cuenta ${nuevas ? "rojo" : "verde"}"><div class="det"><b>${nuevas ? `${nuevas} alerta${nuevas > 1 ? "s" : ""} sin revisar` : "Sin alertas nuevas"}</b><br>
        ${vigiladas.length} marca${vigiladas.length === 1 ? "" : "s"} vigilada${vigiladas.length === 1 ? "" : "s"}${c.vigilancia_desde ? ` · contratada el ${fecha(c.vigilancia_desde)}` : ""}${abiertas.length > nuevas ? ` · ${abiertas.length - nuevas} en seguimiento` : ""}</div></div>
      <div class="sg-bloque"><h3>🛡️ Alertas abiertas <span class="der">${abiertas.length}</span></h3>
        ${abiertas.length ? abiertas.map(alertaVig).join("") : '<p class="sg-vacio-chico">No hay alertas abiertas.</p>'}</div>
      ${revisadas.length ? `<div class="sg-bloque"><h3 class="plegable" data-vig-revisadas>Revisadas <span class="der">${revisadas.length} <span class="mas">${verRev ? "−" : "+"}</span></span></h3>
        ${verRev ? revisadas.slice(0, 40).map(alertaVig).join("") : ""}</div>` : ""}
      <div class="sg-bloque"><h3>Marcas vigiladas <span class="der">${vigiladas.length}</span></h3>
        <ul class="sg-vig-marcas">${vigiladas.map((m) => `<li><b>${esc(m.denominacion || "(sin nombre)")}</b><span>cl. ${esc(m.clase ?? "?")} · ${linkActa(m.acta)}</span></li>`).join("") || '<li class="sg-vacio-chico">Sin marcas vigiladas.</li>'}</ul></div>
    </div>`;
}

document.addEventListener("click", async (ev) => {
  const card = ev.target.closest("[data-vig-cliente]");
  if (card) return abrirVigilancia(Number(card.dataset.vigCliente));
  if (ev.target.closest("[data-vig-revisadas]")) {
    const cid = Number(String(sg.abierto).slice(4));
    sg.vigRevisadas = sg.vigRevisadas === cid ? null : cid;
    return abrirVigilancia(cid);
  }
  const b = ev.target.closest("[data-alerta-estado]");
  if (!b) return;
  const id = b.closest("[data-alerta-vig]").dataset.alertaVig;
  b.disabled = true;
  try {
    if (b.dataset.alertaEstado === "oponer") {
      const r = await pedir(`/api/trabajos/desde-alerta/${id}`, { method: "POST" });
      if (r.creado) mostrarAviso("Se creó el trabajo en Oposición + negociación", "ok");
      await cargar();
      return abrirFicha(r.id);
    }
    await pedir(`/api/cartera/alertas/${id}`, { method: "POST", body: { estado: b.dataset.alertaEstado } });
    abrirVigilancia(Number(String(sg.abierto).slice(4)));
    cargar();
  } catch (e) { b.disabled = false; mostrarAviso(`No se pudo cambiar la alerta: ${e.message}`, "aviso"); }
});
document.addEventListener("change", async (ev) => {
  const n = ev.target.closest("[data-alerta-nota]");
  if (!n) return;
  const id = n.closest("[data-alerta-vig]").dataset.alertaVig;
  try {
    await pedir(`/api/cartera/alertas/${id}`, { method: "POST", body: { nota: n.value } });
    mostrarAviso("Nota guardada", "ok");
  } catch (e) { mostrarAviso(`No se guardó la nota: ${e.message}`, "aviso"); }
});

function cerrarFicha() {
  sg.abierto = null;
  $("ficha").classList.remove("abierta");
  $("ficha").setAttribute("aria-hidden", "true");
  $("velo").hidden = true;
  history.replaceState(null, "", "/seguimientos");
}

// Vencimiento en dos renglones, sin el número grande: «Vence en 15 días (4 diciembre 2026)» / «Notificado el 04/11/26».
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
function fechaLarga(iso) {
  const [a, m, d] = String(iso || "").slice(0, 10).split("-").map(Number);
  return a ? `${d} ${MESES.at(m - 1)} ${a}` : "";
}
function bloqueCuenta(t, s) {
  const a = t.alerta;
  if (a.nivel === "ninguno") return "";
  const caja = (nivel, l1, l2) => `<div class="sg-cuenta ${nivel}"><div class="det"><b>${l1}</b>${l2 ? `<br>${l2}` : ""}</div></div>`;
  if (a.nivel === "cerrado") return caja("cerrado", "Trabajo cerrado", "Ya no cuenta para los vencimientos.");
  if (a.nivel === "cumplido")
    return caja("cumplido", `✓ ${esc(a.texto.replace("✓ ", ""))}`, t.vencimiento ? `El plazo vencía el ${fechaLarga(t.vencimiento)}` : "");
  if (a.nivel === "falta" && t.nos_oponemos)
    return caja("falta", `«${esc(t.contraparte || "La marca")}» todavía no salió publicada`, "Los 30 días corridos para presentar la oposición empiezan con la publicación: el panel los carga solo.");
  if (a.nivel === "falta")
    return caja("falta", "Falta la fecha de notificación", `Cargala en Datos y el vencimiento se calcula solo (${esc(s.plazo_texto)}).`);
  if (t.vence_presentar)
    return caja(a.nivel, `${esc(a.texto)} <span class="fecha">(${fechaLarga(t.vencimiento)})</span>`, "Plazo para presentar la oposición · 30 días corridos desde la publicación. Con la notificación cargada pasa a los 3 meses de negociación.");
  return caja(a.nivel, `${esc(a.texto)} <span class="fecha">(${fechaLarga(t.vencimiento)})</span>`, `Notificado el ${fecha(t.notificacion)}`);
}

// Texto de una nota: respeta los saltos de línea, pone en negrita lo que va entre *asteriscos* y, si es largo, se muestra plegado con «Ver más».
function textoNota(texto) {
  const t = String(texto || "");
  const largo = t.length > 260 || t.split("\n").length > 4;
  // *texto* → negrita (como en WhatsApp). Se aplica después de escapar, así no se puede inyectar HTML.
  let html = esc(t).replace(/\*([^*\n]+?)\*/g, "<b>$1</b>");
  // «@pame»: las menciones a usuarios del panel se resaltan (26/11/2026)
  const todos = [...(menciones.usuarios || []), menciones.yo].filter(Boolean);
  if (todos.length) {
    const alt = todos.sort((a, b) => b.length - a.length).map((u) => u.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
    html = html.replace(new RegExp(`(^|[^\\w@.])@(${alt})(?![\\w@]|[.\\-]\\w)`, "gi"), '$1<span class="sg-mencion">@$2</span>');
  }
  return `<div class="sg-nota-txt${largo ? " plegada" : ""}">${html}</div>${largo ? '<button type="button" class="sg-ver-mas" data-ver-mas>Ver más</button>' : ""}`;
}

// «/Date(1793715300000)/ - Vista de Marcas - None» (así viene del INPI) → «04/11/26 · Vista de Marcas».
function detalleOposicion(v) {
  if (!v) return "Sí";
  return String(v).replace(/\/Date\((\d+)\)\//g, (_, ms) => fecha(new Date(Number(ms)).toLocaleDateString("sv-SE", { timeZone: "America/Argentina/Buenos_Aires" })))
    .split(/\s+-\s+/).map((x) => x.trim()).filter((x) => x && x !== "None" && x !== "null").join(" · ") || "Sí";
}

function lineaTiempo(d) {
  const t = d.trabajo, ex = d.expediente;
  const items = [];
  (d.bitacora || []).forEach((b) => items.push({ fecha: b.fecha, orden: b.creado_en, tipo: "Nota", color: "#2563eb", texto: b.texto, quien: b.usuario, id: b.id }));
  (ex?.novedades || []).forEach((n) => items.push({
    fecha: n.detectado_en, orden: n.detectado_en, color: n.importante?.nivel === "rojo" ? "#dc2626" : n.importante?.nivel === "verde" ? "#16a34a" : "#7c3aed",
    tipo: n.importante ? `${n.importante.icono} ${n.importante.titulo}` : "INPI",
    texto: (n.mostrar_acta ? `Acta ${n.acta} · ` : "") + n.texto + (n.importante ? `\n${n.importante.explicacion}` : ""),
    importante: !!n.importante }));
  if (ex?.marca?.fecha_publicacion) items.push({ fecha: ex.marca.fecha_publicacion, orden: ex.marca.fecha_publicacion, tipo: "INPI", color: "#7c3aed", texto: "Publicada en el boletín" });
  if (ex?.marca?.fecha_presentacion) items.push({ fecha: ex.marca.fecha_presentacion, orden: ex.marca.fecha_presentacion, tipo: "INPI", color: "#7c3aed", texto: "Solicitud presentada" });
  if (t.notificacion) items.push({ fecha: t.notificacion, orden: t.notificacion, tipo: "Plazo", color: "#ea580c", texto: `Notificación (${t.servicio_nombre})` });
  if (t.contratacion) items.push({ fecha: t.contratacion, orden: t.contratacion, tipo: "Contratación", color: "#16a34a", texto: "Nos contrató" });
  items.sort((a, b) => String(b.fecha).localeCompare(String(a.fecha)) || String(b.orden).localeCompare(String(a.orden)));
  if (!items.length) return '<p class="sg-exp-aviso">Todavía no hay movimientos.</p>';
  return `<ul class="sg-linea">${items.map((i) => `<li style="--c:${i.color}"${i.importante ? ' class="importante"' : ""}>
      <div class="cuando"><span class="tag" style="color:${i.color}">${esc(i.tipo)}</span>${fecha(i.fecha)}${i.quien ? ` · ${esc(i.quien)}` : ""}
        ${i.id ? `<button type="button" class="borrar" data-borrar-nota="${i.id}" title="Borrar esta nota">✕</button>` : ""}</div>
      ${textoNota(i.texto)}</li>`).join("")}</ul>`;
}

// Número de acta que abre la ficha del expediente en el portal del INPI (abrirActa de comun.js: es un
// POST, porque por GET el INPI muestra la ficha recortada).
function linkActa(acta) {
  return `<a href="#" class="sg-link-acta" data-acta-inpi="${esc(acta)}" title="Abrir el acta en el INPI">${esc(acta)} ↗</a>`;
}

// Texto para pegar en el mail: «MÁS (clase 36, acta 4790681, MIXTA)». Si el trabajo tiene varias actas,
// todas juntas («… y …»); si todavía no hay datos del INPI, la marca con el acta que haya.
function textoParaCopiar(d) {
  const t = d.trabajo, sols = d.solicitudes || [];
  const propias = new Set([t.acta_efectiva, ...(t.actas_extra || [])].filter(Boolean));
  let elegidas = sols.filter((m) => propias.has(m.acta));
  if (!elegidas.length) elegidas = sols.filter((m) => m.con_problema);
  if (!elegidas.length && !propias.size) elegidas = sols;
  const textos = elegidas.map((m) => m.para_copiar).filter(Boolean);
  if (!textos.length && d.expediente?.para_copiar) return d.expediente.para_copiar;
  if (!textos.length) return t.acta_efectiva ? `${t.marca} (acta ${t.acta_efectiva})` : t.marca;
  return textos.length === 1 ? textos[0] : `${textos.slice(0, -1).join(", ")} y ${textos[textos.length - 1]}`;
}

function botonCopiar(texto) {
  return `<button type="button" class="sg-copiar" data-copiar="${esc(texto)}" title="Copiar «${esc(texto)}»" aria-label="Copiar para el mail">⧉</button>`;
}

// Todas las solicitudes de la marca (una por clase). Primero la que tiene la vista u oposición por la
// que contrataron; la principal (la del expediente de arriba) va marcada.
function listaSolicitudes(d) {
  const sols = d.solicitudes || [];
  const t = d.trabajo;
  if (!sols.length || (sols.length === 1 && sols[0].acta === t.acta_efectiva)) return "";
  const que = t.servicio === "vista" ? "Vista" : t.servicio === "oposicion" ? "Oposición" : "";
  const fila = (m) => {
    const notif = t.servicio === "vista" ? m.vista_fecha_notificacion : m.oposicion_fecha_notificacion;
    const estado = m.con_problema && que
      ? `<span class="sg-sol-tag">${que}${notif ? ` · notif. ${fecha(notif)}` : ""}</span>`
      : `<span class="sg-sol-estado">${esc(m.estado_tramite || m.ultimo_movimiento || (m.tuvo_vista ? "Con vista" : m.tuvo_oposicion ? "Con oposición" : "Sin vista ni oposición"))}</span>`;
    return `<li class="${m.con_problema ? "con-problema" : ""}">
      <div class="izq"><span>${linkActa(m.acta)}${m.acta === t.acta_efectiva ? ' <small class="sg-sol-ppal">principal</small>' : ""}
        ${botonCopiar(m.para_copiar)}</span>
        <small>${[m.clase ? `cl. ${m.clase}` : "", m.tipo_texto ? m.tipo_texto.toLowerCase() : "", m.fecha_presentacion ? `presentada ${fecha(m.fecha_presentacion)}` : ""].filter(Boolean).join(" · ")}</small></div>
      ${estado}</li>`;
  };
  const abierta = sg.solsAbiertas === t.id;
  return `<div class="sg-sols ${abierta ? "abierta" : ""}">
    <button type="button" class="sg-sols-tit" data-sols-toggle aria-expanded="${abierta}">
      <span>Solicitudes de ${esc(t.marca)} (${sols.length})</span><span class="mas" aria-hidden="true">${abierta ? "−" : "+"}</span></button>
    <ul>${sols.map(fila).join("")}</ul></div>`;
}

function avisoResultado(d) {
  const r = d.trabajo.resultado_inpi;
  if (!r) return "";
  const varias = [d.trabajo.acta_efectiva, ...(d.trabajo.actas_extra || [])].filter(Boolean).length > 1;
  return `<div class="sg-resultado-ficha ${esc(r.nivel)}"><b>${r.icono} ${esc(r.titulo)}</b>${r.fecha ? ` · ${esc(r.fecha)}` : ""}${varias ? ` · acta ${esc(r.acta)}` : ""}<br><span class="dato">INPI: «${esc(r.texto)}»</span><br>${esc(r.explicacion)}</div>`;
}

function bloqueExpediente(d) {
  const t = d.trabajo, ex = d.expediente;
  let datos = "";
  if (!ex) {
    const conflicto = t.servicio === "vista" || t.servicio === "oposicion";
    datos = `<div class="sg-exp-aviso">${conflicto
      ? `Sin número de acta todavía. El panel busca solo las actas de este cliente con ${t.servicio === "vista" ? "vista" : "oposición"} (por su CUIT, su nombre como titular y sus marcas de Clientes) y carga las que tienen el mismo nombre de marca, con la fecha de notificación si ya la tiene.`
      : `Sin número de acta todavía. El panel la busca sola todos los días en las solicitudes nuevas del INPI (también las que presentamos con agente) y la carga cuando es seguro que es esta marca.`}
      <div class="sg-buscar-acta"><button type="button" class="sg-btn sg-btn-mini" data-buscar-acta>${conflicto ? `🔎 Buscar las actas con ${t.servicio === "vista" ? "vista" : "oposición"}` : "🔎 Buscar el acta en el INPI"}</button></div>
      <div id="resultado-acta"></div></div>`;
  } else if (!ex.en_cartera) {
    datos = `<p class="sg-exp-aviso">Acta <b>${linkActa(ex.acta)}</b>. Esta marca todavía no está en Clientes → Marcas, por eso no hay datos del INPI. Agregala ahí (en la ficha del cliente) y el expediente se completa solo.</p>`;
  } else {
    const m = ex.marca;
    const celda = (k, v) => (v ? `<div><span>${k}</span><b>${esc(v)}</b></div>` : "");
    datos = `<div class="sg-exp-datos">
      <div><span>Acta</span><b>${linkActa(m.acta)}${ex.para_copiar ? botonCopiar(ex.para_copiar) : ""}${ex.automatica ? ` <small class="sg-quieto">(vinculada sola por el nombre)</small>` : ""}</b>
        <small class="sg-acta-links"><a href="#" data-grilla-inpi="${esc(m.acta)}">Grilla digital ↗</a></small></div>
      ${celda("Estado del trámite", m.estado_tramite)}
      ${celda("Último movimiento", [m.ultimo_movimiento, m.ultimo_movimiento_fecha && fecha(m.ultimo_movimiento_fecha)].filter(Boolean).join(" · "))}
      ${celda("Clase", m.clase)}${celda("Titular", m.titular)}
      ${celda("Presentada", m.fecha_presentacion && fecha(m.fecha_presentacion))}
      ${celda("Publicada", m.fecha_publicacion && fecha(m.fecha_publicacion))}
      ${celda("Concedida", m.fecha_concesion && fecha(m.fecha_concesion))}
      ${m.tuvo_oposicion ? celda("Oposición", detalleOposicion(m.detalle_oposicion)) : ""}
    </div>`;
  }
  return `<div class="sg-bloque"><h3>📂 Expediente y bitácora</h3>${avisoResultado(d)}${datos}${listaSolicitudes(d)}
    <form class="sg-nota-nueva" id="form-nota"><textarea id="nota-texto" rows="1" placeholder="Agregar una nota… (@ para avisarle a alguien)" title="Enter: salto de línea · Cmd/Ctrl + Enter: guardar · *texto* en negrita · @usuario le avisa en su campanita" maxlength="50000"></textarea>
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
  sg.ficha = d;
  const t = d.trabajo;
  const s = servicioDe(t.servicio);
  const f = $("ficha");
  f.style.setProperty("--sg-color", s.color);
  const r = t.resultado_inpi;
  const filaEstado = r ? `<li class="sg-paso sg-paso-estado ${esc(r.nivel)}" title="${esc(r.explicacion)}">
      <span class="bola">${r.icono}</span><span class="nombre"><b>${esc(r.titulo)}</b>${r.fecha ? ` · ${esc(r.fecha)}` : ""}<small>${esc(r.explicacion)}</small></span><span class="sg-auto">INPI</span></li>` : "";
  const pasos = s.etapas.map((e, i) => {
    const n = i + 1;
    const cls = n <= t.etapa ? "hecho" : "";
    const auto = (s.auto_desde && n >= s.auto_desde) || (s.auto_etapas || []).includes(n);
    return `<li class="sg-paso ${cls} ${n === t.etapa ? "actual" : ""}" data-etapa="${n}" title="${auto ? `Se marca sola cuando el INPI lo muestra (también se puede marcar a mano)` : `Marcar «${esc(e)}» como etapa actual`}">
      <span class="bola">${n <= t.etapa ? "✓" : ""}</span><span class="nombre">${esc(e)}</span>${auto ? '<span class="sg-auto">INPI</span>' : ""}</li>`
      + (n === Math.max(t.etapa, 1) ? filaEstado : "");
  }).join("");
  const inpi = d.inpi;
  const bloqueInpi = !s.auto_desde ? "" : !inpi || !inpi.actas.length
    ? `<p class="sg-inpi-nota">Desde «${esc(s.etapas[s.auto_desde - 1])}» la etapa avanza sola con lo que el panel lee del INPI. Para eso el trabajo necesita su acta: se busca sola o se carga en Datos.</p>`
    : `<div class="sg-inpi"><div class="sg-inpi-tit">Según el INPI${inpi.actas.length > 1 ? ` · ${inpi.actas.length} actas (cuenta la más atrasada)` : ""}</div>
        <ul>${inpi.actas.map((a) => `<li><span class="acta">Acta ${esc(a.acta)}${a.clase ? ` · cl. ${a.clase}` : ""}</span>
          <span class="est ${a.etapa ? "" : "den"}">${a.pendiente ? "Pendiente de leer en el INPI" : esc(a.nombre)}</span></li>`).join("")}</ul>
        ${inpi.actas.length === 1 ? `<p class="sg-inpi-nota">¿La marca tiene más clases? Las otras actas se suman solas si están en Clientes → Marcas con el mismo nombre.</p>` : ""}</div>`;
  const opServ = d.servicios.map((x) => `<option value="${x.id}" ${x.id === t.servicio ? "selected" : ""}>${esc(x.nombre)}</option>`).join("");
  const enlace = (url, txt) => (url ? `<a class="sg-btn sg-btn-suave sg-btn-mini" href="${esc(url)}" target="_blank" rel="noopener">${txt}</a>` : "");
  f.innerHTML = `
    <div class="sg-f-cab">
      <button class="cerrar" type="button" data-cerrar aria-label="Cerrar">×</button>
      <span class="sg-f-serv">● ${esc(s.nombre)}</span>
      <div class="sg-f-marca"><span class="nombre" data-editar-marca title="Clic para editar el nombre de la marca">${esc(t.marca)}</span><button type="button" class="sg-f-lapiz" data-editar-marca title="Editar el nombre de la marca" aria-label="Editar el nombre de la marca">✎</button></div>
      <div class="sg-f-cliente"><a href="/clientes?cliente=${t.cliente_id}" data-cliente-popup="${t.cliente_id}" title="Ver la ficha del cliente">${esc(t.cliente)}</a>${t.cliente_telefono ? ` · ${esc(t.cliente_telefono)}` : ""}${t.cliente_email ? ` · ${esc(t.cliente_email)}` : ""}</div>
      ${t.nos_oponemos ? `<div class="sg-f-contra">Nos oponemos a <b>${esc(t.contraparte || "la marca nueva")}</b>${t.acta_contraria ? ` · acta ${linkActa(t.acta_contraria)}` : ""} <small>(desde una alerta de vigilancia)</small></div>` : ""}
      <div class="sg-f-links">
        ${enlace(t.carpeta_drive, "📁 Carpeta Drive")}
        ${enlace(t.comprobante, "🧾 Comprobante")}
        <button type="button" class="sg-btn sg-btn-mini" data-armar-mail title="Armar un mail o WhatsApp con una plantilla y los datos de este trabajo ya completados">✉️ Mail</button>
        ${t.acta_efectiva ? `<button type="button" class="sg-btn sg-btn-suave sg-btn-mini" data-actualizar-inpi title="Volver a leer ahora en el INPI el expediente de ${[t.acta_efectiva, ...(t.actas_extra || [])].length > 1 ? "las actas" : "el acta"} de este trabajo (${esc([t.acta_efectiva, ...(t.actas_extra || [])].join(", "))})">↻ Actualizar INPI</button>` : ""}
        <button type="button" class="sg-btn sg-btn-suave sg-btn-mini" data-copiar="${esc(textoParaCopiar(d))}" data-etiqueta="⧉ Copiar marca" title="Copiar «${esc(textoParaCopiar(d))}» para pegar en un mail">⧉ Copiar marca</button>
        <a class="sg-btn sg-btn-suave sg-btn-mini" href="/mails?tab=clientes" target="_blank" rel="noopener" title="Plantillas para clientes (en Mails)">📋 Plantillas</a>
      </div>
    </div>
    <div class="sg-f-cuerpo">
      ${bloqueCuenta(t, s)}
      <div class="sg-bloque"><h3>Etapas <span class="der">${r ? `Estado: <b class="sg-estado-${esc(r.nivel)}">${r.icono} ${esc(r.titulo)}</b>` : t.proxima_etapa ? `Sigue: <b>${esc(t.proxima_etapa)}</b>` : "✓ Todas completas"}</span></h3>
        <ol class="sg-pasos">${pasos}</ol>${bloqueInpi}</div>
      ${bloqueInformes(d)}
      ${bloqueExpediente(d)}
      <div class="sg-bloque"><h3>Datos <span class="der sg-guardado" id="guardado">✓ Guardado</span></h3>
        <div class="sg-datos">
          <label>Servicio <select data-campo="servicio">${opServ}</select></label>
          <label>Acta <input type="text" inputmode="numeric" data-campo="acta" value="${esc(t.acta || "")}" placeholder="${t.acta_auto ? esc(t.acta_efectiva) + " (sola)" : "Sin presentar"}" />${(t.actas_extra || []).length ? `<small class="sg-actas-extra">También: ${t.actas_extra.map(linkActa).join(", ")}</small>` : ""}</label>
          <label>Contratación <input type="date" data-campo="contratacion" value="${esc(t.contratacion || "")}" /></label>
          ${s.con_vencimiento ? `<label>Notificación <input type="date" data-campo="notificacion" value="${esc(t.notificacion || "")}" /></label>
          <label>Vencimiento <span class="calc">${t.vencimiento ? fecha(t.vencimiento, true) : "—"}</span></label>` : ""}
          ${campoLink("carpeta_drive", "Carpeta de Drive", t.carpeta_drive, "https://drive.google.com/…", "cargada")}
          ${campoLink("comprobante", "Comprobante", t.comprobante, "Link al comprobante de pago", "cargado")}
          <label class="ancho">Notas <textarea data-campo="notas" rows="3">${esc(t.notas || "")}</textarea></label>
        </div></div>
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

// ── ↻ Actualizar INPI desde la ficha del trabajo (27/11/2026): lo mismo que el botón de la ficha de la marca en
// Clientes, para todas las actas del trabajo, sin salir de Seguimientos. ──────────────────────────────────
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-actualizar-inpi]");
  if (!b || b.disabled) return;
  const t = sg.ficha?.trabajo;
  const actas = t ? [t.acta_efectiva, ...(t.actas_extra || [])].filter(Boolean) : [];
  if (!actas.length) return;
  b.disabled = true;
  const txt = b.textContent;
  let ok = 0, nuevas = 0, fuera = 0, fallas = 0;
  for (const [i, acta] of actas.entries()) {
    b.textContent = `↻ Leyendo ${actas.length > 1 ? `${i + 1}/${actas.length}` : "el INPI"}…`;
    try {
      const r = await pedir(`/api/cartera/marcas/${encodeURIComponent(acta)}/actualizar`, { method: "POST" });
      if (r.ok) { ok++; nuevas += (r.novedades || []).length; } else fallas++;
    } catch (e) {
      if (/no está en la cartera/i.test(e.message)) fuera++; else fallas++;
    }
  }
  b.disabled = false;
  b.textContent = txt;
  const partes = [];
  if (ok) partes.push(`${ok === actas.length ? (actas.length > 1 ? "Actas actualizadas" : "Acta actualizada") : `${ok} de ${actas.length} actas actualizadas`}: ${nuevas ? `${nuevas} novedad${nuevas > 1 ? "es" : ""}` : "sin novedades"}`);
  if (fuera) partes.push(`${fuera} no ${fuera > 1 ? "están" : "está"} en Clientes → Marcas (agregala ahí para leerla)`);
  if (fallas) partes.push(`el INPI no respondió en ${fallas}: probá de nuevo en unos minutos`);
  mostrarAviso(partes.join(" · ") || "Sin cambios", fallas && !ok ? "error" : "ok");
  if (ok) {
    if (typeof cargar === "function") cargar();
    if (sg.abierto) abrirFicha(sg.abierto);
  }
});

// ── Menciones en las notas (26/11/2026): «@» + usuario le avisa en su campanita 🔔 ──────────────
const menciones = { usuarios: [], yo: null, lista: [], sel: 0, cargado: false, pidiendo: null };
function cargarUsuariosMencion() {
  if (!menciones.pidiendo) {
    menciones.pidiendo = fetch("/api/trabajos-usuarios", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) { menciones.usuarios = d.usuarios || []; menciones.yo = d.yo; menciones.cargado = true; } })
      .catch(() => {})
      .finally(() => { menciones.pidiendo = null; });
  }
  return menciones.pidiendo;
}
cargarUsuariosMencion();

function cajaMenciones() {
  const form = $("form-nota");
  if (!form) return null;
  let c = form.querySelector(".sg-menciones");
  if (!c) {
    c = document.createElement("ul");
    c.className = "sg-menciones";
    c.setAttribute("role", "listbox");
    form.appendChild(c);
  }
  return c;
}
function cerrarMenciones() { const c = $("form-nota")?.querySelector(".sg-menciones"); if (c) c.hidden = true; menciones.lista = []; }
function abiertoMenciones() { return menciones.lista.length > 0; }
function visibleMenciones() { const c = $("form-nota")?.querySelector(".sg-menciones"); return !!(c && !c.hidden); }
function buscarMencion(caja) {
  const antes = caja.value.slice(0, caja.selectionStart);
  const m = antes.match(/(^|[\s(¿¡,;:])@([\w.\-]*)$/);
  if (!m) return cerrarMenciones();
  const q = m[2].toLowerCase();
  // Si la lista de usuarios todavía no llegó (o falló), se pide de nuevo y se vuelve a buscar.
  if (!menciones.cargado) { cargarUsuariosMencion().then(() => { if (menciones.cargado && document.activeElement === caja) buscarMencion(caja); }); }
  // Primero los que empiezan con lo escrito («@pa» → pame), después los que lo contienen.
  const empiezan = menciones.usuarios.filter((u) => u.toLowerCase().startsWith(q));
  const contienen = menciones.usuarios.filter((u) => !u.toLowerCase().startsWith(q) && u.toLowerCase().includes(q));
  menciones.lista = [...empiezan, ...contienen];
  const c = cajaMenciones();
  if (!menciones.lista.length) {
    c.innerHTML = `<li class="ayuda">${menciones.cargado ? (q ? `Ningún usuario coincide con «${esc(q)}»` : "No hay otros usuarios en el panel") : "Buscando usuarios…"}</li>`;
    c.hidden = false;
    return;
  }
  menciones.sel = Math.min(menciones.sel, menciones.lista.length - 1);
  c.innerHTML = menciones.lista.map((u, i) => `<li role="option" data-mencion="${esc(u)}" class="${i === menciones.sel ? "sel" : ""}"><span class="ini">${esc(u[0].toUpperCase())}</span>@${esc(u)}</li>`).join("")
    + '<li class="ayuda">Le llega en su campanita 🔔</li>';
  c.hidden = false;
}
function elegirMencion(u) {
  const caja = $("nota-texto");
  const pos = caja.selectionStart;
  const antes = caja.value.slice(0, pos).replace(/@([\w.\-]*)$/, `@${u} `);
  caja.value = antes + caja.value.slice(pos);
  caja.focus();
  caja.setSelectionRange(antes.length, antes.length);
  cerrarMenciones();
}
document.addEventListener("input", (ev) => { if (ev.target.id === "nota-texto") buscarMencion(ev.target); });
document.addEventListener("keydown", (ev) => {
  if (ev.target.id !== "nota-texto" || !visibleMenciones()) return;
  if (!abiertoMenciones()) {   // solo el cartel de ayuda: Escape lo cierra, lo demás sigue normal
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); cerrarMenciones(); }
    return;
  }
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    const n = menciones.lista.length;
    menciones.sel = (menciones.sel + (ev.key === "ArrowDown" ? 1 : n - 1)) % n;
    buscarMencion(ev.target);
  } else if ((ev.key === "Enter" && !ev.metaKey && !ev.ctrlKey) || ev.key === "Tab") {
    ev.preventDefault();
    ev.stopImmediatePropagation();
    elegirMencion(menciones.lista[menciones.sel]);
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    ev.stopPropagation();
    cerrarMenciones();
  }
}, true);
document.addEventListener("mousedown", (ev) => {
  const li = ev.target.closest("[data-mencion]");
  if (li) { ev.preventDefault(); elegirMencion(li.dataset.mencion); }
  else if (!ev.target.closest(".sg-menciones") && ev.target.id !== "nota-texto") cerrarMenciones();
});

document.addEventListener("submit", async (ev) => {
  if (ev.target.id === "form-nota") {
    ev.preventDefault();
    const caja = $("nota-texto");
    const texto = caja.value.trim();
    if (!texto) return;
    const boton = ev.target.querySelector("button[type=submit]");
    boton.disabled = true;
    try {
      const r = await pedir(`/api/trabajos/${sg.abierto}/bitacora`, { method: "POST", body: { texto } });
      caja.value = "";
      cerrarMenciones();
      if (r && (r.mencionados || []).length) mostrarAviso(`Le avisamos en la campanita a ${r.mencionados.map((u) => "@" + u).join(", ")}.`, "ok");
      abrirFicha(sg.abierto);
    } catch (e) {
      boton.disabled = false;   // el texto queda en la caja para reintentar
      mostrarAviso(`No se guardó la nota: ${e.message}`, "aviso");
    }
  }
});

// Caja de la nota: crece al escribir (hasta un tope, después scrollea). Enter hace salto de línea;
// Cmd/Ctrl + Enter la agrega.
document.addEventListener("input", (ev) => {
  if (ev.target.id !== "nota-texto") return;
  const c = ev.target;
  c.style.height = "auto";
  c.style.height = Math.min(c.scrollHeight + 2, 320) + "px";
});
document.addEventListener("keydown", (ev) => {
  if (ev.target.id === "nota-texto" && ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    ev.target.form.requestSubmit();
  }
});
// Actas: abren el expediente o la Grilla Digital en el portal del INPI (pestaña nueva)
document.addEventListener("click", (ev) => {
  const a = ev.target.closest("[data-acta-inpi], [data-grilla-inpi]");
  if (!a) return;
  ev.preventDefault();
  if (a.dataset.actaInpi) abrirActa(a.dataset.actaInpi);
  else abrirGrilla(a.dataset.grillaInpi);
});

// Solicitudes de la marca: se abren con el «+» (y quedan abiertas mientras se mira ese trabajo)
document.addEventListener("click", async (ev) => {
  const cp = ev.target.closest("[data-copiar]");
  if (cp) {
    ev.preventDefault();
    try {
      await navigator.clipboard.writeText(cp.dataset.copiar);
      const etiqueta = cp.dataset.etiqueta || "⧉";
      cp.textContent = cp.dataset.etiqueta ? "✓ Copiado" : "✓"; cp.classList.add("ok");
      mostrarAviso(`Copiado: ${cp.dataset.copiar}`, "ok");
      setTimeout(() => { cp.textContent = etiqueta; cp.classList.remove("ok"); }, 1500);
    } catch { mostrarAviso("No se pudo copiar (el navegador no dio permiso).", "aviso"); }
    return;
  }
  const tg = ev.target.closest("[data-sols-toggle]");
  if (!tg) return;
  const caja = tg.closest(".sg-sols");
  const abierta = caja.classList.toggle("abierta");
  tg.setAttribute("aria-expanded", abierta);
  tg.querySelector(".mas").textContent = abierta ? "−" : "+";
  sg.solsAbiertas = abierta ? sg.abierto : null;
});

// ✉️ Mail: arma el mail con una plantilla (plantillas.js) y los datos del trabajo o del cliente ya completados
function contextoMail() {
  const d = sg.ficha || {};
  if (d.vigilancia) {
    const c = d.vigilancia.cliente;
    const marcas = (d.vigilancia.marcas || []).filter((m) => m.vigilar !== false);
    const nombres = [...new Set(marcas.map((m) => m.denominacion).filter(Boolean))];
    return { titulo: `Vigilancia · ${c.nombre}`, servicio: "vigilancia", nombre: c.contacto || c.nombre, email: c.email, telefono: c.telefono,
      marca: nombres.length === 1 ? nombres[0] : "", clases: [...new Set(marcas.map((m) => m.clase).filter((x) => x != null))].join(", ") };
  }
  const t = d.trabajo;
  const sols = d.solicitudes || [];
  const ppal = sols.find((m) => m.acta === t.acta_efectiva) || sols[0] || {};
  const ex = d.expediente?.marca || {};
  return {
    titulo: `${t.marca} · ${t.cliente}`, servicio: t.servicio, etapa: t.etapa,
    nombre: t.cliente_contacto || t.cliente, email: t.cliente_email, telefono: t.cliente_telefono,
    marca: t.marca, acta: t.acta_efectiva, clase: ppal.clase ?? ex.clase, tipo: ppal.tipo_texto || ex.tipo,
    clases: [...new Set(sols.map((m) => m.clase).filter((x) => x != null))].join(", ") || (ppal.clase ?? ex.clase),
    fecha_publicacion: ex.fecha_publicacion, fecha_concesion: ex.fecha_concesion, fecha_vencimiento: ex.fecha_vencimiento_marca,
  };
}
document.addEventListener("click", (ev) => {
  if (!ev.target.closest("[data-armar-mail]")) return;
  if (window.Plantillas) Plantillas.abrir(contextoMail());
});

// «Ver más / Ver menos» de las notas largas
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-ver-mas]");
  if (!b) return;
  const txt = b.previousElementSibling;
  const abierta = txt.classList.toggle("plegada") === false;
  b.textContent = abierta ? "Ver menos" : "Ver más";
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
  const p = new URLSearchParams(location.search);
  const id = Number(p.get("trabajo"));
  if (id) abrirFicha(id);
  else if (Number(p.get("vigilancia"))) abrirVigilancia(Number(p.get("vigilancia")));
})();
