/* Pantalla /buscador-inpi: buscador de marcas de INPI dentro del panel, consultando en vivo
 * (rutas /api/inpi/..., ver buscador_inpi_api.py).
 * Entradas por URL: ?q=texto (lo que se escribió en la lupa con «INPI»: acta, CUIT o denominación),
 * o ?acta=N para abrir directo la ficha de un acta.
 * Usa de comun.js: _escapeHtml, abrirActa, abrirGrilla, mostrarAviso. */

const esc = s => _escapeHtml(s == null ? "" : String(s));
const $ = id => document.getElementById(id);
const est = { ultima: null, offset: 0, limit: 50, buscando: false };

// ── Utilidades ──────────────────────────────────────────────────────────
async function pedir(url, opciones = {}) {
  const r = await fetch(url, { credentials: "include", ...opciones });
  if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
  let datos = null;
  try { datos = await r.json(); } catch (_) { /* sin cuerpo */ }
  if (!r.ok) {
    if (datos && datos.detail) throw new Error(datos.detail);
    // 502/503/504/524: el pedido a INPI tardó más de lo que espera el servidor (Cloudflare corta a los 100 s).
    if ([502, 503, 504, 524].includes(r.status)) throw new Error("INPI no respondió a tiempo. Puede estar caído o lento: probá de nuevo en unos minutos o abrilo directo en la web de INPI.");
    throw new Error(`Error ${r.status}`);
  }
  return datos;
}

function fecha(iso) {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}))?/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}${m[4] ? " " + m[4] : ""}` : iso;
}

function tagEstado(e) {
  if (!e) return '<span class="bi-sub">—</span>';
  const cls = /concedida/i.test(e) ? "ok" : /(denegada|nula|caduca|abandonada|desistida)/i.test(e) ? "mal" : "aviso";
  return `<span class="bi-tag ${cls}">${esc(e)}</span>`;
}

function tagPanel(p) {
  if (!p) return '<span class="bi-sub">—</span>';
  const cls = p.tipo === "cliente" ? "cliente" : p.tipo === "lead" ? "lead" : "";
  return `<a class="bi-tag ${cls}" href="${esc(p.url)}" title="Abrir en el panel">${esc(p.etiqueta)}</a>`;
}

// ── Pestañas y formularios ──────────────────────────────────────────────
function elegirPestana(tab) {
  document.querySelectorAll(".bi-tab").forEach(b => {
    const activa = b.dataset.tab === tab;
    b.classList.toggle("activa", activa);
    b.setAttribute("aria-selected", activa ? "true" : "false");
  });
  document.querySelectorAll(".bi-form").forEach(f => { f.hidden = f.dataset.panel !== tab; });
}
document.querySelectorAll(".bi-tab").forEach(b => b.addEventListener("click", () => {
  elegirPestana(b.dataset.tab);
  const primero = document.querySelector(`.bi-form[data-panel="${b.dataset.tab}"] input`);
  if (primero) primero.focus();
}));

(function cargarClases() {
  const sel = $("sel-clase");
  for (let i = 1; i <= 45; i++) sel.insertAdjacentHTML("beforeend", `<option value="${i}">${i}</option>`);
})();

function datosForm(form) {
  const d = {};
  new FormData(form).forEach((v, k) => { d[k] = typeof v === "string" ? v.trim() : v; });
  form.querySelectorAll("input[type=checkbox]").forEach(c => { d[c.name] = c.checked; });
  return d;
}

$("f-marcas").addEventListener("submit", e => {
  e.preventDefault();
  const d = datosForm(e.target);
  buscar({
    modo: "avanzada", tipo_resolucion: d.tipo_resolucion, clase: d.clase,
    denominacion: d.denominacion, denominacion_empieza: d.denominacion_modo === "empieza",
    titular: d.titular, titular_empieza: d.titular_modo === "empieza",
    ingreso_desde: d.ingreso_desde, ingreso_hasta: d.ingreso_hasta,
    resolucion_desde: d.resolucion_desde, resolucion_hasta: d.resolucion_hasta, vigentes: d.vigentes,
  });
});
$("f-marcas").querySelector("[data-limpiar]").addEventListener("click", () => { $("f-marcas").reset(); });
$("f-puntual").addEventListener("submit", e => {
  e.preventDefault();
  const d = datosForm(e.target);
  buscar({ modo: "puntual", acta: d.acta, resolucion: d.resolucion });
});
$("f-cuit").addEventListener("submit", e => {
  e.preventDefault();
  buscar({ modo: "cuit", cuit: datosForm(e.target).cuit });
});
$("f-renovaciones").addEventListener("submit", e => {
  e.preventDefault();
  const d = datosForm(e.target);
  buscar({ modo: "renovaciones", presentacion_desde: d.presentacion_desde, presentacion_hasta: d.presentacion_hasta });
});

// ── Búsqueda ────────────────────────────────────────────────────────────
async function buscar(consulta, offset = 0) {
  if (est.buscando) return;
  est.buscando = true;
  est.ultima = consulta;
  est.offset = offset;
  const res = $("bi-resultado");
  res.innerHTML = '<p class="bi-estado">Consultando a INPI…</p>';
  document.querySelectorAll(".bi-form button[type=submit]").forEach(b => { b.disabled = true; });
  try {
    const r = await pedir("/api/inpi/buscar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...consulta, limit: est.limit, offset }),
    });
    pintarResultado(r);
  } catch (e) {
    res.innerHTML = `<p class="bi-estado error">${esc(e.message)}</p>`;
  } finally {
    est.buscando = false;
    document.querySelectorAll(".bi-form button[type=submit]").forEach(b => { b.disabled = false; });
  }
}

function pintarResultado(r) {
  const res = $("bi-resultado");
  const filas = r.filas || [];
  const avisos = [];
  if (r.sin_tildes) avisos.push("INPI no encontró nada tal cual se escribió: se buscó sin tener en cuenta tildes.");
  if (r.recortado) avisos.push("Hay muchas coincidencias: se revisaron las primeras 200. Achicá la búsqueda (clase, fechas o más letras).");
  if (r.por_resolucion) avisos.push("No hay un acta con ese número: se buscó como número de resolución.");
  if (!filas.length) {
    res.innerHTML = `${avisos.map(a => `<p class="bi-aviso">${esc(a)}</p>`).join("")}<p class="bi-estado">INPI no devolvió resultados.</p>`;
    return;
  }
  const total = r.total || filas.length;
  const desde = est.offset + 1, hasta = est.offset + filas.length;
  const resumen = `<div class="bi-resumen"><span><strong>${total.toLocaleString("es-AR")}</strong> resultado${total === 1 ? "" : "s"} en INPI${total > filas.length ? ` · mostrando ${desde}–${hasta}` : ""}</span>
    ${avisos.map(a => `<span class="bi-aviso">${esc(a)}</span>`).join("")}</div>`;
  const tabla = r.modo === "renovaciones" ? tablaRenovaciones(filas) : tablaMarcas(filas, r.modo);
  res.innerHTML = resumen + `<div class="bi-tabla-caja">${tabla}</div>` + paginas(total, filas.length, r.modo);
  res.querySelectorAll("[data-ver]").forEach(b => b.addEventListener("click", () => abrirFicha(b.dataset.ver)));
  res.querySelectorAll("[data-inpi]").forEach(b => b.addEventListener("click", () => abrirActa(b.dataset.inpi)));
  const ant = res.querySelector("[data-pag=ant]"), sig = res.querySelector("[data-pag=sig]");
  if (ant) ant.addEventListener("click", () => buscar(est.ultima, Math.max(0, est.offset - est.limit)));
  if (sig) sig.addEventListener("click", () => buscar(est.ultima, est.offset + est.limit));
  const tam = res.querySelector("[data-tam]");
  if (tam) tam.addEventListener("change", () => { est.limit = +tam.value; buscar(est.ultima, 0); });
}

function tablaMarcas(filas, modo) {
  const conEstado = modo !== "cuit";
  return `<table class="bi-tabla"><thead><tr>
      <th>Acta</th><th>Denominación</th><th>Clase</th><th>Titulares</th><th>Ingreso</th>
      ${conEstado ? "<th>Resolución</th><th>Estado</th><th>Vence</th>" : ""}<th>En el panel</th><th></th>
    </tr></thead><tbody>${filas.map(f => `<tr>
      <td class="bi-num"><button type="button" class="bi-acta" data-ver="${esc(f.acta)}" title="Ver el acta y su Grilla Digital">${esc(f.acta)}</button></td>
      <td><div class="bi-den">${esc(f.denominacion || "(sin denominación)")}</div><div class="bi-sub">${esc(f.tipo_marca || "")}</div></td>
      <td class="bi-num">${esc(f.clase ?? "—")}</td>
      <td>${esc(f.titulares || "—")}${f.cuit ? `<div class="bi-sub">CUIT ${esc(f.cuit)}</div>` : ""}</td>
      <td class="bi-num">${fecha(f.fecha_ingreso)}</td>
      ${conEstado ? `<td class="bi-num">${esc(f.numero_resolucion || "—")}</td><td>${tagEstado(f.estado)}</td><td class="bi-num">${fecha(f.vencimiento)}</td>` : ""}
      <td>${tagPanel(f.panel)}</td>
      <td><div class="bi-fila-acc">
        <button type="button" class="bi-btn chico" data-ver="${esc(f.acta)}">Ver acta</button>
        <button type="button" class="bi-btn chico" data-inpi="${esc(f.acta)}" title="Abrir el expediente en la web de INPI">INPI ↗</button>
      </div></td>
    </tr>`).join("")}</tbody></table>`;
}

function tablaRenovaciones(filas) {
  return `<table class="bi-tabla"><thead><tr>
      <th>Acta</th><th>Presentación</th><th>Clase</th><th>Resolución</th><th>Renovadora</th><th>En el panel</th><th></th>
    </tr></thead><tbody>${filas.map(f => `<tr>
      <td class="bi-num"><button type="button" class="bi-acta" data-ver="${esc(f.acta)}">${esc(f.acta)}</button></td>
      <td class="bi-num">${fecha(f.fecha_presentacion)}</td>
      <td class="bi-num">${esc(f.clase ?? "—")}</td>
      <td class="bi-num">${esc(f.numero_resolucion || "—")}</td>
      <td class="bi-num">${f.renovadora ? `<button type="button" class="bi-acta" data-ver="${esc(f.renovadora)}" title="Acta de la renovación">${esc(f.renovadora)}</button>` : "—"}</td>
      <td>${tagPanel(f.panel)}</td>
      <td><div class="bi-fila-acc"><button type="button" class="bi-btn chico" data-inpi="${esc(f.acta)}">INPI ↗</button></div></td>
    </tr>`).join("")}</tbody></table>`;
}

function paginas(total, cantidad, modo) {
  if (modo === "puntual" || modo === "cuit" || (est.ultima && est.ultima.modo === "rapido")) return "";
  const hayAnt = est.offset > 0, haySig = est.offset + cantidad < total;
  return `<div class="bi-paginas">
    <label>Por página <select data-tam>${[20, 50, 100, 200].map(n => `<option ${n === est.limit ? "selected" : ""}>${n}</option>`).join("")}</select></label>
    <button type="button" class="bi-btn chico" data-pag="ant" ${hayAnt ? "" : "disabled"}>← Anterior</button>
    <button type="button" class="bi-btn chico" data-pag="sig" ${haySig ? "" : "disabled"}>Siguiente →</button>
  </div>`;
}

// ── Ficha del acta (modal) ──────────────────────────────────────────────
function cerrarFicha() {
  $("bi-modal").hidden = true;
  document.body.style.overflow = "";
  const u = new URL(location.href);
  if (u.searchParams.has("acta")) { u.searchParams.delete("acta"); history.replaceState(null, "", u); }
}
document.querySelectorAll("#bi-modal [data-cerrar]").forEach(b => b.addEventListener("click", cerrarFicha));
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("bi-modal").hidden) cerrarFicha(); });

async function abrirFicha(acta) {
  const cont = $("bi-modal-contenido");
  cont.innerHTML = `<h2 id="bi-modal-titulo">Acta ${esc(acta)}</h2><p class="bi-estado">Consultando el expediente y la Grilla Digital en INPI…</p>`;
  $("bi-modal").hidden = false;
  document.body.style.overflow = "hidden";
  try {
    const d = await pedir(`/api/inpi/acta/${encodeURIComponent(acta)}`);
    if ($("bi-modal").hidden) return;
    cont.innerHTML = fichaHtml(d);
    cont.querySelector("[data-abrir-inpi]").addEventListener("click", () => abrirActa(d.acta));
    cont.querySelector("[data-abrir-grilla]").addEventListener("click", () => abrirGrilla(d.acta));
    cont.querySelectorAll("[data-ver]").forEach(b => b.addEventListener("click", () => abrirFicha(b.dataset.ver)));
  } catch (e) {
    cont.innerHTML = `<h2 id="bi-modal-titulo">Acta ${esc(acta)}</h2><p class="bi-estado error">${esc(e.message)}</p>
      <div class="bi-ficha-acc"><button type="button" class="bi-btn" id="bi-reint">Reintentar</button>
      <button type="button" class="bi-btn" id="bi-en-inpi">Abrir en INPI ↗</button></div>`;
    $("bi-reint").addEventListener("click", () => abrirFicha(acta));
    $("bi-en-inpi").addEventListener("click", () => abrirActa(acta));
  }
}

function dato(etiqueta, valor) {
  return `<div><span>${esc(etiqueta)}</span>${valor || "—"}</div>`;
}

function fichaHtml(d) {
  const agente = d.particular ? "Particular (sin agente)" : esc([d.agente, d.caracter].filter(Boolean).join(" · "));
  const enPanel = d.panel
    ? `<a class="btn-prim" href="${esc(d.panel.url)}">Abrir en el panel · ${esc(d.panel.etiqueta)}</a>`
    : '<span class="bi-tag">No está en el panel</span>';
  const opos = d.oposiciones.length ? `<div class="bi-tabla-caja"><table class="bi-tabla"><thead><tr>
      <th>Oponente</th><th>Presentada</th><th>Notificada</th><th>Vence</th><th>Levantada</th><th>Agente</th></tr></thead><tbody>
      ${d.oposiciones.map(o => `<tr><td>${esc(o.oponente || "—")}${o.fundamento ? `<details class="bi-fundamento"><summary>Ver fundamento</summary><div>${esc(o.fundamento)}</div></details>` : ""}</td>
        <td class="bi-num">${fecha(o.presentacion)}</td><td class="bi-num">${fecha(o.notificacion)}</td><td class="bi-num">${fecha(o.vencimiento)}</td>
        <td class="bi-num">${fecha(o.levantamiento)}</td><td>${esc(o.agente_oponente || "Particular")}</td></tr>`).join("")}
      </tbody></table></div>`
    : `<p class="bi-vacio">${d.error_oposiciones ? "INPI tiene oposiciones cargadas pero no se pudieron leer: miralas en INPI." : "Sin oposiciones."}</p>`;
  const vistas = d.vistas.length ? `<div class="bi-tabla-caja"><table class="bi-tabla"><thead><tr>
      <th>Tipo</th><th>Fecha</th><th>Notificada</th><th>Vence</th><th>Contestada</th></tr></thead><tbody>
      ${d.vistas.map(v => `<tr><td>${esc(v.tipo || "—")}</td><td class="bi-num">${fecha(v.fecha)}</td><td class="bi-num">${fecha(v.notificacion)}</td>
        <td class="bi-num">${fecha(v.vencimiento)}</td><td class="bi-num">${fecha(v.contestacion)}</td></tr>`).join("")}
      </tbody></table></div>`
    : `<p class="bi-vacio">${d.error_vistas ? "INPI tiene vistas cargadas pero no se pudieron leer: miralas en INPI." : "Sin vistas ni notificaciones."}</p>`;
  const grilla = d.grilla.length ? `<div class="bi-tabla-caja"><table class="bi-tabla"><thead><tr>
      <th>Fecha</th><th>Índice</th><th>Referencia</th><th></th></tr></thead><tbody>
      ${d.grilla.map(a => `<tr><td class="bi-num">${fecha(a.fecha)}</td><td>${esc(a.indice || "—")}${a.gde ? `<div class="bi-sub">${esc(a.gde)}</div>` : ""}</td><td>${esc(a.referencia || "—")}</td>
        <td>${a.id ? `<a class="bi-btn chico" target="_blank" rel="noopener" href="/api/inpi/acta/${encodeURIComponent(d.acta)}/archivo?id=${encodeURIComponent(a.id)}">📄 Ver</a>` : ""}</td></tr>`).join("")}
      </tbody></table></div>`
    : `<p class="bi-vacio">${esc(d.error_grilla || "La Grilla Digital no tiene archivos.")}</p>`;
  const pubs = d.publicaciones.length
    ? d.publicaciones.map(p => `${fecha(p.fecha)} · ${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">Boletín ${esc(p.numero || "")}</a>` : `Boletín ${esc(p.numero || "—")}`}${p.tipo ? ` (${esc(p.tipo)})` : ""}`).join("<br>")
    : "—";
  return `
    <div class="bi-ficha-cab">
      ${d.logo ? `<img class="bi-logo" src="${esc(d.logo)}" alt="Logo de la marca" />` : ""}
      <div>
        <h2 id="bi-modal-titulo">${esc(d.denominacion || "(sin denominación)")}</h2>
        <div class="bi-sub">Acta ${esc(d.acta)} · Clase ${esc(d.clase ?? "—")} · ${esc(d.tipo_marca || "—")} ${d.estado_tramite ? tagEstado(d.estado_tramite) : '<span class="bi-tag aviso">En trámite</span>'}</div>
      </div>
    </div>
    <div class="bi-ficha-acc">
      ${enPanel}
      <button type="button" class="bi-btn" data-abrir-inpi>Abrir en INPI ↗</button>
      <button type="button" class="bi-btn" data-abrir-grilla>Grilla Digital en INPI ↗</button>
    </div>
    <div class="bi-sec"><h3>Datos</h3><div class="bi-datos">
      ${dato("Titulares", esc(d.titulares))}
      ${dato("CUIT", esc(d.cuit))}
      ${dato("Presentación", fecha(d.fecha_presentacion))}
      ${dato("Gestión del trámite", agente)}
      ${dato("Resolución", esc([d.estado_tramite, d.numero_disposicion].filter(Boolean).join(" · ")))}
      ${dato("Concesión / vencimiento", `${fecha(d.fecha_concesion)} · ${fecha(d.vencimiento)}`)}
      ${dato("Publicación", pubs)}
    </div></div>
    ${d.proteccion || d.limitacion ? `<div class="bi-sec"><h3>Protección y limitación</h3>
      ${d.proteccion ? `<p class="bi-texto"><strong>Protección:</strong> ${esc(d.proteccion)}</p>` : ""}
      ${d.limitacion ? `<p class="bi-texto"><strong>Limitación:</strong> ${esc(d.limitacion)}</p>` : ""}</div>` : ""}
    <div class="bi-sec"><h3>Oposiciones (${d.oposiciones.length})</h3>${opos}</div>
    <div class="bi-sec"><h3>Vistas y notificaciones (${d.vistas.length})</h3>${vistas}</div>
    <div class="bi-sec"><h3>Grilla Digital (${d.grilla.length})</h3>${grilla}</div>`;
}

// ── Entrada por URL ─────────────────────────────────────────────────────
(function desdeUrl() {
  const p = new URLSearchParams(location.search);
  const q = (p.get("q") || "").trim();
  const acta = (p.get("acta") || "").replace(/\D/g, "");
  if (q) {
    const digitos = q.replace(/\D/g, "");
    if (digitos && /^[\d.\-\s/]+$/.test(q)) {
      if (digitos.length === 11) { elegirPestana("cuit"); $("f-cuit").cuit.value = q; buscar({ modo: "cuit", cuit: digitos }); }
      // Acta (y, si no existe, el mismo número como resolución).
      else { elegirPestana("puntual"); $("f-puntual").acta.value = digitos; buscar({ modo: "rapido", q: digitos }); }
    } else {
      elegirPestana("marcas"); $("f-marcas").denominacion.value = q;
      buscar({ modo: "avanzada", denominacion: q });
    }
  }
  if (acta) abrirFicha(acta);
  if (!q && !acta) $("f-marcas").denominacion.focus();
})();
