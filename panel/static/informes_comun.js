/* Informes, compartido entre Más → Informes (informes.js), la solapa Informes de la ficha del lead
 * (titular.html) y la del cliente (clientes.js): la lista agrupada por marca y el diálogo «Nuevo
 * informe», que carga el JSON a un cliente o a un lead que todavía no es cliente. Tipos: el informe
 * preliminar (al cliente en PDF + IA interno), el Informe Oposición IA (skill revisador-de-oposiciones)
 * y el Informe Vista IA (skill respondervistas, vistas de fondo del INPI).
 * Depende de comun.js (api, apiJson). Estilos en informes_comun.css.
 * Todo texto que viene de la base pasa por ixEsc() antes de ir al HTML. */

const ixEsc = (s) => (s ?? "").toString()
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const IX_ICO = {
  pdf: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
  ia: '<svg viewBox="0 0 24 24"><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 10h4v4h-4z"/></svg>',
  mas: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  bajar: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
  buscar: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  opo: '<svg viewBox="0 0 24 24"><path d="M12 3v18M5 21h14M5 7h14"/><path d="M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z"/></svg>',
  vista: '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
};

/* Chances del informe de oposición → color del puntito. */
const ixColorChances = (c) => /^(muy bajas|bajas)$/.test(c || "") ? "mal" : /^(bajas a medias|medias)$/.test(c || "") ? "aviso" : c ? "ok" : "";

function ixFechaIso(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}
function ixDiasHasta(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  if (!m) return null;
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  return Math.round((new Date(+m[1], +m[2] - 1, +m[3]) - hoy) / 86400000);
}

function ixFechaCorta(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const hoy = new Date();
  const dias = Math.floor((new Date(hoy.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (dias === 0) return "hoy";
  if (dias === 1) return "ayer";
  const o = { day: "numeric", month: "short", timeZone: "America/Argentina/Buenos_Aires" };
  if (d.getFullYear() !== hoy.getFullYear()) o.year = "numeric";
  return d.toLocaleDateString("es-AR", o).replace(".", "");
}

const ixPlural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
// Link de un informe con su código de compartir (?c=): pegado en WhatsApp muestra de qué informe se trata.
const ixLink = (i) => `/informes/${i.id}${i.c || i.compartir ? `?c=${encodeURIComponent(i.c || i.compartir)}` : ""}`;

function ixVersiones(lista) {
  if (lista.length < 2) return "";
  const resto = lista.slice(1);
  return `<details class="ix-versiones"><summary>+ ${ixPlural(resto.length, "versión anterior", "versiones anteriores")}</summary>
    ${resto.map((v) => `<a href="${ixLink(v)}" target="_blank" rel="noopener">${ixEsc(ixFechaCorta(v.generado_en))}${v.generado_por ? " · " + ixEsc(v.generado_por) : ""} ↗</a>`).join("")}</details>`;
}

function ixDocPdf(lista, g) {
  if (!lista.length) return `<button type="button" class="ix-doc falta" data-ix-cargar data-tipo="cliente">
      <span class="ix-doc-ico">${IX_ICO.mas}</span><span class="ix-doc-txt"><span class="ix-doc-titulo">Informe al cliente</span>
      <span class="ix-doc-meta">Falta · cargarlo</span></span></button>`;
  const u = lista[0];
  const fecha = u.fecha_informe || ixFechaCorta(u.generado_en);
  return `<div><div class="ix-doc pdf">
      <a class="ix-doc-abrir" href="${ixLink(u)}" target="_blank" rel="noopener" title="Abrir el PDF">
        <span class="ix-doc-ico">${IX_ICO.pdf}</span>
        <span class="ix-doc-txt"><span class="ix-doc-titulo">Informe al cliente</span>
          <span class="ix-doc-meta">PDF · ${ixEsc(fecha)}${u.formato === 1 ? " · formato anterior" : ""}</span></span></a>
      <a class="ix-bajar" href="/api/informes/${u.id}/pdf" title="Descargar el PDF" aria-label="Descargar el PDF">${IX_ICO.bajar}</a>
    </div>${ixVersiones(lista)}</div>`;
}

function ixDocIa(lista, g) {
  if (!lista.length) return `<button type="button" class="ix-doc falta" data-ix-cargar data-tipo="interno">
      <span class="ix-doc-ico">${IX_ICO.mas}</span><span class="ix-doc-txt"><span class="ix-doc-titulo">Informe IA interno</span>
      <span class="ix-doc-meta">Falta · cargarlo</span></span></button>`;
  const u = lista[0];
  const exactas = u.identicas
    ? `<span class="ix-punto mal"></span>${ixPlural(u.identicas, "coincidencia exacta", "coincidencias exactas")}`
    : '<span class="ix-punto ok"></span>Sin coincidencias exactas';
  return `<div><div class="ix-doc ia">
      <a class="ix-doc-abrir" href="${ixLink(u)}" target="_blank" rel="noopener" title="Abrir el informe interno">
        <span class="ix-doc-ico">${IX_ICO.ia}</span>
        <span class="ix-doc-txt"><span class="ix-doc-titulo">Informe IA interno</span>
          <span class="ix-doc-meta">${exactas}${u.relevantes ? ` · ${ixPlural(u.relevantes, "relevante", "relevantes")}` : ""}</span></span></a>
    </div>${ixVersiones(lista)}</div>`;
}

function ixDueno(g) {
  if (g.cliente_id) return `<span class="ix-chip-tipo cliente">Cliente</span>
    <a href="/clientes?cliente=${g.cliente_id}&ctab=informes" title="Abrir la ficha del cliente">${ixEsc(g.cliente_nombre || "#" + g.cliente_id)}</a>`;
  if (g.lead_clave) return `<span class="ix-chip-tipo lead">Lead</span>
    <a href="/titular/${encodeURIComponent(g.lead_clave)}#informes" title="Abrir la ficha del lead">${ixEsc(g.lead_nombre || g.lead_clave)}</a>`;
  return '<span class="ix-chip-tipo">Sin dueño</span>';
}

/* Informe Oposición IA: una tarjeta ancha con las chances, el oponente y el vencimiento de los 3 meses. */
function ixDocOposicion(lista) {
  const u = lista[0];
  const dias = ixDiasHasta(u.vencimiento);
  const vto = u.vencimiento ? `vence ${ixFechaIso(u.vencimiento)}${dias != null ? (dias < 0 ? " (vencido)" : dias <= 30 ? ` (faltan ${dias} días)` : "") : ""}` : "";
  return `<div class="ix-opo-celda"><div class="ix-doc opo">
      <a class="ix-doc-abrir" href="${ixLink(u)}" target="_blank" rel="noopener" title="Abrir el informe de oposición">
        <span class="ix-doc-ico">${IX_ICO.opo}</span>
        <span class="ix-doc-txt"><span class="ix-doc-titulo">Informe Oposición IA${u.oponentes && u.oponentes.length ? ` · vs. ${ixEsc(u.oponentes.join(", "))}` : ""}</span>
          <span class="ix-doc-meta">${u.chances ? `<span class="ix-punto ${ixColorChances(u.chances)}"></span>Chances ${ixEsc(u.chances)}` : "Sin chances"}${u.oposiciones ? ` · ${ixPlural(u.oposiciones, "oposición", "oposiciones")}` : ""}${vto ? ` · <span class="${dias != null && dias <= 30 ? "ix-urgente" : ""}">${ixEsc(vto)}</span>` : ""}</span>
          ${u.camino ? `<span class="ix-doc-meta ix-camino">→ ${ixEsc(u.camino)}</span>` : ""}</span></a>
    </div>${ixVersiones(lista)}</div>`;
}

/* Informe Vista IA: una tarjeta ancha con las chances, el artículo invocado y el vencimiento de la vista. */
function ixDocVista(lista) {
  const u = lista[0];
  const dias = ixDiasHasta(u.vencimiento);
  const vto = u.vencimiento ? `vence ${ixFechaIso(u.vencimiento)}${dias != null ? (dias < 0 ? " (vencida)" : dias <= 30 ? ` (faltan ${dias} días)` : "") : ""}` : "";
  return `<div class="ix-opo-celda"><div class="ix-doc vista">
      <a class="ix-doc-abrir" href="${ixLink(u)}" target="_blank" rel="noopener" title="Abrir el informe de vista">
        <span class="ix-doc-ico">${IX_ICO.vista}</span>
        <span class="ix-doc-txt"><span class="ix-doc-titulo">Informe Vista IA${u.articulos && u.articulos.length ? ` · ${ixEsc(u.articulos.join(", "))}` : ""}</span>
          <span class="ix-doc-meta">${u.chances ? `<span class="ix-punto ${ixColorChances(u.chances)}"></span>Chances ${ixEsc(u.chances)}` : "Sin chances"}${u.vistas ? ` · ${ixPlural(u.vistas, "vista", "vistas")}` : ""}${u.actas && u.actas.length ? ` · acta${u.actas.length > 1 ? "s" : ""} ${ixEsc(u.actas.join(", "))}` : ""}${vto ? ` · <span class="${dias != null && dias <= 30 ? "ix-urgente" : ""}">${ixEsc(vto)}</span>` : ""}</span>
          ${u.camino ? `<span class="ix-doc-meta ix-camino">→ ${ixEsc(u.camino)}</span>` : ""}</span></a>
    </div>${ixVersiones(lista)}</div>`;
}

/* La lista. `mostrarDueno` en false para la ficha del lead (ya se sabe de quién es). */
function ixListaHtml(grupos, { mostrarDueno = true } = {}) {
  return `<div class="ix-lista">${grupos.map((g, i) => {
    const ref = /^INFPR-\d+$/i.test(g.referencia || "") ? `<span class="ix-ref">${ixEsc(g.referencia.toUpperCase())}</span>` : "";
    const opo = g.familia === "oposicion", vis = g.familia === "vista";
    return `<div class="ix-fila${opo || vis ? " es-opo" : ""}" data-ix="${i}">
      <div class="ix-marca"><div><span class="ix-marca-nombre">${ixEsc(g.marca || "—")}</span>${ref}${opo ? '<span class="ix-ref opo">OPOSICIÓN</span>' : ""}${vis ? '<span class="ix-ref vista">VISTA</span>' : ""}</div>
        ${mostrarDueno ? `<div class="ix-dueno">${ixDueno(g)}</div>` : ""}</div>
      ${opo ? ixDocOposicion(g.oposicion) : vis ? ixDocVista(g.vista || []) : ixDocPdf(g.cliente, g) + ixDocIa(g.interno, g)}
      <div class="ix-fecha" title="Último informe cargado"><strong>${ixEsc(ixFechaCorta(g.ultimo))}</strong>último</div>
    </div>`;
  }).join("")}</div>`;
}

/* Botones «Falta · pegar el JSON» de la lista: abren el diálogo con el dueño ya elegido. */
function ixConectarLista(cont, grupos, alTerminar) {
  cont.querySelectorAll("[data-ix-cargar]").forEach((b) => b.addEventListener("click", () => {
    const g = grupos[+b.closest("[data-ix]").dataset.ix];
    const destino = g.cliente_id ? { cliente_id: g.cliente_id, nombre: g.cliente_nombre, tipo: "cliente" }
      : { lead_clave: g.lead_clave, nombre: g.lead_nombre, tipo: "lead" };
    ixNuevoInforme({ destino, marca: g.marca, solo: b.dataset.tipo, alTerminar });
  }));
}

/* ── Vista previa del JSON pegado (el panel lo vuelve a validar al guardar) ── */
function ixPrevia(texto) {
  const t = texto.trim().replace(/^```[a-zA-Z]*\s*|\s*```$/g, "");
  if (!t) return { ok: false, html: "" };
  let d;
  try { d = JSON.parse(t); } catch (e) { return { ok: false, html: `<span class="mal">No es un JSON válido: ${ixEsc(e.message)}</span>` }; }
  if (!d || typeof d !== "object" || Array.isArray(d)) return { ok: false, html: '<span class="mal">No es un informe.</span>' };
  if (d.tipo === "informe_oposicion_ia" || (!d.tipo && d.oposiciones && d.marca_cliente)) {
    const marca = (d.marca_cliente || {}).denominacion || "";
    const ops = Array.isArray(d.oposiciones) ? d.oposiciones : [];
    const oponentes = [...new Set(ops.map((o) => o && o.oponente).filter(Boolean))];
    const ch = typeof d.chances === "string" ? d.chances : (d.chances || {}).nivel || "";
    const rx = d.resumen_express && typeof d.resumen_express === "object" && Object.values(d.resumen_express).some((v) => String(v || "").trim());
    const faltas = [!marca && "marca_cliente.denominacion", !rx && "resumen_express", !ops.length && "oposiciones"].filter(Boolean);
    return { ok: !faltas.length, tipo: "oposicion", marca, html: `<span class="tipo opo">Informe Oposición IA</span><strong>${ixEsc(marca || "— falta la marca —")}</strong>
      · ${ixPlural(ops.length, "oposición", "oposiciones")}${oponentes.length ? " de " + ixEsc(oponentes.join(", ")) : ""}${ch ? " · chances " + ixEsc(ch) : ""}` +
      (faltas.length ? `<br><span class="mal">Falta ${ixEsc(faltas.join(", "))}.</span>` : "") };
  }
  if (d.tipo === "informe_vista_ia" || (!d.tipo && d.vistas && d.marca_cliente && !d.oposiciones)) {
    const marca = (d.marca_cliente || {}).denominacion || "";
    const vs = Array.isArray(d.vistas) ? d.vistas : [];
    const arts = [...new Set(vs.flatMap((v) => (v && Array.isArray(v.articulos)) ? v.articulos : v && v.articulos ? [v.articulos] : []).filter(Boolean))];
    const ch = typeof d.chances === "string" ? d.chances : (d.chances || {}).nivel || "";
    const rx = d.resumen_express && typeof d.resumen_express === "object" && Object.values(d.resumen_express).some((v) => String(v || "").trim());
    const sinActa = vs.some((v) => !v || !String(v.acta_cliente || "").trim());
    const faltas = [!marca && "marca_cliente.denominacion", !rx && "resumen_express", !vs.length && "vistas", vs.length && sinActa && "vistas[].acta_cliente"].filter(Boolean);
    return { ok: !faltas.length, tipo: "vista", marca, html: `<span class="tipo vista">Informe Vista IA</span><strong>${ixEsc(marca || "— falta la marca —")}</strong>
      · ${ixPlural(vs.length, "vista", "vistas")}${arts.length ? " · " + ixEsc(arts.join(", ")) : ""}${ch ? " · chances " + ixEsc(ch) : ""}` +
      (faltas.length ? `<br><span class="mal">Falta ${ixEsc(faltas.join(", "))}.</span>` : "") };
  }
  const interno = d.tipo === "informe_viabilidad_interno" || (!(d.secciones && d.encabezado) && d.tipo !== "informe_viabilidad_cliente" && ("busqueda_inpi" in d || "caso" in d));
  if (interno) {
    const marca = (d.caso || {}).marca_pretendida || d.marca_pretendida || "";
    const b = d.busqueda_inpi || {};
    const exactas = (b.coincidencias_exactas || b.identicas || []).length;
    return { ok: !!marca, tipo: "interno", marca, html: `<span class="tipo ia">Informe IA interno</span><strong>${ixEsc(marca || "— falta caso.marca_pretendida —")}</strong>
      · ${ixPlural(exactas, "coincidencia exacta", "coincidencias exactas")} · ${ixPlural((b.antecedentes_relevantes || []).length, "antecedente relevante", "antecedentes relevantes")}` };
  }
  const e = d.encabezado || {}, sec = Array.isArray(d.secciones) ? d.secciones : [];
  const v2 = Number(d.version || 0) >= 2 || "titulo" in d || "referencia" in e || typeof e.web === "object";
  const textos = JSON.stringify(v2 ? [d.titulo, sec] : [e.actividad, e.antecedentes, e.logo, d.introduccion, sec]);
  const clase = textos.match(/\bclases?\s*(n[°ºo.]*\s*)?\d+/i);
  const corchete = v2 && textos.match(/\[[A-ZÁÉÍÓÚÑ ]{3,}\]/);
  const sinTitulo = v2 && sec.some((s) => s && typeof s === "object" && !String(s.titulo || "").trim());
  const ref = e.referencia && !/^INFPR-?$/i.test(String(e.referencia).trim()) ? ixEsc(e.referencia) : "se asigna sola";
  return { ok: !!e.marca_pretendida && sec.length > 0 && !clase && !corchete && !sinTitulo, tipo: "cliente", marca: e.marca_pretendida || "",
    html: `<span class="tipo pdf">Informe al cliente</span><strong>${ixEsc(e.marca_pretendida || "— falta la marca —")}</strong>
      · ${ixEsc(e.fecha || e.fecha_informe || "fecha de hoy")} · referencia ${ref} · ${ixPlural(sec.length, "sección", "secciones")}${v2 ? "" : " · formato anterior"}` +
      (clase ? `<br><span class="mal">Menciona «${ixEsc(clase[0])}»: el informe al cliente no puede mostrar números de clase.</span>` : "") +
      (corchete ? `<br><span class="mal">Quedó «${ixEsc(corchete[0])}» sin reemplazar.</span>` : "") +
      (sinTitulo ? '<br><span class="mal">Hay una sección sin título.</span>' : "") };
}

/* ── Diálogo «Nuevo informe» ──
 * Arriba se elige qué se carga: el informe preliminar (dos cuadros separados: el JSON del informe al
 * cliente, que arma el PDF, y el del IA interno; uno o los dos, y si van los dos primero el del cliente
 * para que el interno tome su INFPR), el Informe Oposición IA (un cuadro, el JSON de la skill
 * revisador-de-oposiciones) o el Informe Vista IA (un cuadro, el JSON de la skill respondervistas).
 * destino: {cliente_id, nombre, tipo:"cliente"} | {lead_clave, nombre, tipo:"lead"} | null (se elige buscando)
 * fijo: el destino no se puede cambiar (ficha del lead o del cliente).
 * solo: "cliente" | "interno" | "oposicion" | "vista" muestra un solo cuadro y no deja cambiar de tipo.
 * modo: "preliminar" | "oposicion" | "vista": con qué tipo abre (se puede cambiar). */
const IX_CAJAS = {
  cliente: { titulo: "Informe al cliente (PDF)", ico: "pdf",
    ayuda: "Pegá el <strong>…_cliente.json</strong>. El panel arma el PDF del informe preliminar que se le manda a la persona.",
    placeholder: '{ "tipo": "informe_viabilidad_cliente", "encabezado": { … }, "secciones": [ … ] }',
    otro: "Ese es el JSON interno: pegalo en el cuadro «Informe IA interno»." },
  interno: { titulo: "Informe IA interno", ico: "ia",
    ayuda: "Pegá el <strong>…_interno.json</strong>. Queda guardado para el estudio (búsquedas, antecedentes, revisión legal); no se manda.",
    placeholder: '{ "tipo": "informe_viabilidad_interno", "caso": { … }, "busqueda_inpi": { … } }',
    otro: "Ese es el JSON del informe al cliente: pegalo en el cuadro «Informe al cliente (PDF)»." },
  oposicion: { titulo: "Informe Oposición IA", ico: "opo",
    ayuda: "Pegá el JSON que entrega la skill <strong>revisador-de-oposiciones</strong> junto con el análisis. Es de uso interno: queda guardado para el estudio, no se manda al cliente.",
    placeholder: '{ "tipo": "informe_oposicion_ia", "resumen_express": { … }, "oposiciones": [ … ] }',
    otro: "Ese no es el JSON de oposición: es el del informe preliminar (elegí «Informe preliminar» arriba)." },
  vista: { titulo: "Informe Vista IA", ico: "vista",
    ayuda: "Pegá el JSON que entrega la skill <strong>respondervistas</strong> con el análisis de una vista de fondo del INPI. Es de uso interno: queda guardado para el estudio, no se manda al cliente.",
    placeholder: '{ "tipo": "informe_vista_ia", "resumen_express": { … }, "vistas": [ … ] }',
    otro: "Ese no es el JSON de vista: es el de otro informe (elegí el tipo que corresponde arriba)." },
};
const IX_NOMBRE = { cliente: "Informe al cliente (PDF)", interno: "Informe IA interno", oposicion: "Informe Oposición IA", vista: "Informe Vista IA" };
const IX_MODOS = ["preliminar", "oposicion", "vista"];
const ixModoDe = (tipo) => tipo === "oposicion" || tipo === "vista" ? tipo : "preliminar";

/* Link «abrir en el panel» que arman las skills de Claude: /informes#cargar=<JSON comprimido>.
 * Formato: «z.» + base64url(deflate-raw(JSON en UTF-8)), o «j.» + base64url(JSON) sin comprimir.
 * Va después del «#»: no viaja al servidor, solo lo lee el navegador. Devuelve el texto del JSON. */
async function ixLeerLinkCarga(hash) {
  const m = /[#&]cargar=([zj])\.([A-Za-z0-9_-]+)/.exec(hash || "");
  if (!m) return null;
  const b64 = m[2].replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64 + "===".slice((b64.length + 3) % 4));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  if (m[1] === "j") return new TextDecoder().decode(bytes);
  const flujo = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return await new Response(flujo).text();
}

/* Lo del link pegado en el diálogo: elige el tipo según el JSON y lo deja listo para elegir el dueño y guardar. */
async function ixAbrirLinkCarga({ alTerminar = null } = {}) {
  let texto;
  try { texto = await ixLeerLinkCarga(location.hash); }
  catch (e) { alert("No se pudo leer el informe del link (¿se cortó al copiarlo?): " + e.message); }
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  if (!texto) return false;
  const p = ixPrevia(texto);
  ixNuevoInforme({ modo: ixModoDe(p.tipo), texto, alTerminar });
  return true;
}

function ixNuevoInforme({ destino = null, fijo = false, marca = "", solo = "", esperado = "", modo = "", texto = "", alTerminar = null } = {}) {
  solo = solo || esperado;
  let modal = document.getElementById("ix-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "ix-modal"; modal.className = "ix-modal";
    modal.innerHTML = `<div class="ix-dialogo" role="dialog" aria-modal="true" aria-labelledby="ix-titulo">
      <div class="ix-dialogo-cab"><h3 id="ix-titulo">Nuevo informe</h3><button type="button" class="ix-cerrar" aria-label="Cerrar">&times;</button></div>
      <div class="ix-dialogo-cuerpo" id="ix-cuerpo"></div>
      <div class="ix-dialogo-pie"><span class="ix-estado" id="ix-estado"></span>
        <button type="button" class="ix-boton sec" id="ix-cancelar">Cancelar</button>
        <button type="button" class="ix-boton" id="ix-guardar" disabled>Guardar</button></div></div>`;
    document.body.appendChild(modal);
    const cerrar = () => modal.classList.remove("abierto");
    modal.addEventListener("click", (ev) => { if (ev.target === modal) cerrar(); });
    modal.querySelector(".ix-cerrar").addEventListener("click", cerrar);
    modal.querySelector("#ix-cancelar").addEventListener("click", cerrar);
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && modal.classList.contains("abierto")) cerrar(); });
  }
  const $m = (id) => modal.querySelector("#" + id);
  modo = solo === "oposicion" || solo === "vista" ? solo : IX_MODOS.includes(modo) ? modo : "preliminar";
  let tipos = [];
  let elegido = destino;
  const nuevoEst = () => ({ previa: { ok: false }, hecho: null });
  const est = { cliente: nuevoEst(), interno: nuevoEst(), oposicion: nuevoEst(), vista: nuevoEst() };
  const cuerpo = $m("ix-cuerpo"), estado = $m("ix-estado"), guardar = $m("ix-guardar");
  $m("ix-titulo").textContent = solo ? `Cargar ${IX_NOMBRE[solo].replace(" (PDF)", "")}` : "Nuevo informe";
  cuerpo.innerHTML = `
    <p class="ix-paso">Para quién</p>
    <div class="ix-bloque" id="ix-destino"></div>
    ${solo ? "" : `<p class="ix-paso">Qué informe</p>
    <div class="ix-segmentos ix-modo" id="ix-modo" role="group" aria-label="Tipo de informe">
      <button type="button" data-modo="preliminar">Informe preliminar <span class="ix-n">PDF al cliente + IA interno</span></button>
      <button type="button" data-modo="oposicion">Informe Oposición IA</button>
      <button type="button" data-modo="vista">Informe Vista IA</button>
    </div>`}
    <div id="ix-cajas"></div>`;
  estado.textContent = ""; estado.className = "ix-estado";
  $m("ix-cancelar").textContent = "Cancelar";

  function pintarCajas() {
    tipos = solo ? [solo] : modo === "preliminar" ? ["cliente", "interno"] : [modo];
    tipos.forEach((t) => { est[t] = nuevoEst(); });
    if (!solo) cuerpo.querySelectorAll("#ix-modo [data-modo]").forEach((b) => b.classList.toggle("activo", b.dataset.modo === modo));
    const varias = tipos.length > 1;
    $m("ix-cajas").innerHTML = tipos.map((t) => `
    <div class="ix-caja" id="ix-caja-${t}">
      <div class="ix-caja-cab"><span class="ix-doc-ico ${IX_CAJAS[t].ico}">${IX_ICO[IX_CAJAS[t].ico]}</span>
        <div><div class="ix-caja-titulo">${IX_CAJAS[t].titulo}${varias ? ' <span class="ix-opcional">opcional</span>' : ""}</div>
          <div class="ix-caja-ayuda">${IX_CAJAS[t].ayuda}</div></div></div>
      <textarea id="ix-texto-${t}" spellcheck="false" placeholder='${IX_CAJAS[t].placeholder}'></textarea>
      <div class="ix-previa" id="ix-previa-${t}"></div>
    </div>`).join("") +
      (varias ? '<p class="ix-nota">Podés pegar uno solo o los dos. Si pegás los dos, el interno toma la misma referencia INFPR que el PDF.</p>' : "");
    tipos.forEach((t) => {
      const ta = $m("ix-texto-" + t);
      ta.addEventListener("input", () => {
        let p = ixPrevia(ta.value);
        if (p.tipo && p.tipo !== t) {
          const msg = p.tipo === "oposicion" || p.tipo === "vista"
            ? `Ese es el JSON del ${IX_NOMBRE[p.tipo]}: ${solo ? "este cuadro es para otro informe" : `elegí «${IX_NOMBRE[p.tipo]}» arriba`}.`
            : IX_CAJAS[t].otro;
          p = { ok: false, html: `<span class="mal">${msg}</span>` };
        }
        est[t].previa = p;
        $m("ix-previa-" + t).innerHTML = p.html;
        if (p.marca) autocompletarMarca(p.marca);
        estado.textContent = ""; estado.className = "ix-estado";
        actualizarBoton();
      });
    });
    estado.textContent = ""; estado.className = "ix-estado";
    actualizarBoton();
  }

  const listos = () => tipos.filter((t) => !est[t].hecho && est[t].previa.ok);
  const conTexto = () => tipos.filter((t) => !est[t].hecho && $m("ix-texto-" + t).value.trim());
  const actualizarBoton = () => {
    const l = listos(), malos = conTexto().filter((t) => !est[t].previa.ok);
    guardar.disabled = !elegido || !l.length || malos.length > 0;
    guardar.textContent = l.includes("cliente") && l.includes("interno") ? "Generar PDF y guardar los dos"
      : l.includes("cliente") ? "Generar PDF" : l.includes("interno") ? "Guardar informe interno"
      : l.includes("oposicion") ? "Guardar informe de oposición" : l.includes("vista") ? "Guardar informe de vista" : "Guardar";
  };

  function pintarDestino() {
    const caja = $m("ix-destino");
    if (elegido) {
      const quien = elegido.tipo === "cliente" ? '<span class="ix-chip-tipo cliente">Cliente</span>' : '<span class="ix-chip-tipo lead">Lead</span>';
      caja.innerHTML = `<div class="ix-destino">${quien}<div class="ix-destino-txt"><strong>${ixEsc(elegido.nombre || "")}</strong>
          <span>${elegido.tipo === "lead" ? "No hace falta que sea cliente: queda en su ficha de lead y, si después se convierte, pasa a la del cliente." : "Queda en la ficha del cliente, solapa Informes."}</span></div>
        ${fijo ? "" : '<button type="button" class="ix-link" id="ix-cambiar">Cambiar</button>'}</div>`;
      if (!fijo) $m("ix-cambiar").addEventListener("click", () => { elegido = null; pintarDestino(); actualizarBoton(); });
      return;
    }
    caja.innerHTML = `<div class="ix-buscar">${IX_ICO.buscar}<input type="search" id="ix-q" placeholder="Buscá la marca o el titular: clientes y leads de los boletines" autocomplete="off" value="${ixEsc(marca)}"></div>
      <div class="ix-resultados" id="ix-res"></div>`;
    const q = $m("ix-q"), res = $m("ix-res");
    let t = null, pedido = 0;
    const buscar = async () => {
      const v = q.value.trim();
      if (v.length < 2) { res.innerHTML = ""; return; }
      const n = ++pedido;
      res.innerHTML = '<div class="ix-nada">Buscando…</div>';
      try {
        const r = await api(`/api/informes/destinos?q=${encodeURIComponent(v)}`);
        if (n !== pedido) return;
        const op = [];
        if (r.clientes.length) {
          op.push('<div class="ix-grupo">Clientes</div>');
          r.clientes.forEach((c) => op.push(`<button type="button" class="ix-opcion" data-tipo="cliente" data-id="${c.id}" data-nombre="${ixEsc(c.nombre)}">
            <span class="ix-chip-tipo cliente">Cliente</span><span class="ix-opcion-txt"><strong>${ixEsc(c.nombre)}</strong>
            <span>${c.marcas && c.marcas.length ? ixEsc(c.marcas.join(" · ")) : "coincide el nombre"}</span></span></button>`));
        }
        if (r.leads.length) {
          op.push('<div class="ix-grupo">Leads de los boletines</div>');
          r.leads.forEach((l) => {
            const marcas = l.marcas.map((m) => `${m.denominacion || "(sin denominación)"}${m.clase ? " · cl. " + m.clase : ""}`).join("  ·  ");
            op.push(l.cliente
              ? `<button type="button" class="ix-opcion" data-tipo="cliente" data-id="${l.cliente.id}" data-nombre="${ixEsc(l.cliente.nombre)}">
                  <span class="ix-chip-tipo cliente">Cliente</span><span class="ix-opcion-txt"><strong>${ixEsc(l.titular)}</strong><span>Ya es cliente · ${ixEsc(marcas)}</span></span></button>`
              : `<button type="button" class="ix-opcion" data-tipo="lead" data-clave="${ixEsc(l.clave)}" data-nombre="${ixEsc(l.titular)}">
                  <span class="ix-chip-tipo lead">Lead</span><span class="ix-opcion-txt"><strong>${ixEsc(l.titular || l.clave)}</strong><span>${ixEsc(marcas)}</span></span></button>`);
          });
        }
        res.innerHTML = op.length ? op.join("") : '<div class="ix-nada">No hay clientes ni leads con esa marca o ese titular. Probá con otra parte del nombre.</div>';
        res.querySelectorAll(".ix-opcion").forEach((b) => b.addEventListener("click", () => {
          elegido = b.dataset.tipo === "cliente" ? { tipo: "cliente", cliente_id: +b.dataset.id, nombre: b.dataset.nombre }
            : { tipo: "lead", lead_clave: b.dataset.clave, nombre: b.dataset.nombre };
          pintarDestino(); actualizarBoton(); $m("ix-texto-" + tipos[0]).focus();
        }));
      } catch (e) { if (n === pedido) res.innerHTML = `<div class="ix-nada">No se pudo buscar (${ixEsc(e.message)}).</div>`; }
    };
    qAMano = false;
    q.addEventListener("input", () => { qAMano = true; clearTimeout(t); t = setTimeout(buscar, 300); });
    q.addEventListener("ix-auto", () => { clearTimeout(t); buscar(); });
    if (q.value) buscar();
    setTimeout(() => q.focus(), 30);
  }
  /* Al pegar el JSON: si todavía no se eligió a quién va y no se escribió nada a mano en la búsqueda,
   * se busca la marca del informe (varias marcas juntas, «A / B», se buscan cada una). */
  let qAMano = false;
  function autocompletarMarca(m) {
    marca = m;
    const q = $m("ix-q");
    if (elegido || !q || (qAMano && q.value.trim()) || q.value === m) return;
    q.value = m;
    q.dispatchEvent(new CustomEvent("ix-auto"));
  }
  pintarDestino();
  pintarCajas();
  if (!solo) $m("ix-modo").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-modo]");
    if (!b || b.dataset.modo === modo) return;
    if (tipos.some((t) => est[t].hecho)) return;  // ya se guardó algo: no se cambia de tipo
    modo = b.dataset.modo;
    pintarCajas();
    $m("ix-texto-" + tipos[0]).focus();
  });

  function marcarHecho(t, r) {
    est[t].hecho = r;
    const caja = $m("ix-caja-" + t);
    caja.classList.add("hecho");
    $m("ix-texto-" + t).disabled = true;
    $m("ix-previa-" + t).innerHTML = `<span class="bien">✓ Guardado${r.referencia ? " · " + ixEsc(r.referencia) : ""}</span>
      <a class="ix-boton chico" href="${ixLink(r)}" target="_blank" rel="noopener">${t === "cliente" ? "Ver el PDF ↗" : "Ver el informe ↗"}</a>
      ${t === "cliente" ? `<a class="ix-boton sec chico" href="/api/informes/${r.id}/pdf">Descargar PDF</a>` : ""}`;
  }

  guardar.onclick = async () => {
    if (guardar.disabled) return;
    guardar.disabled = true; estado.className = "ix-estado";
    const orden = ["cliente", "interno", "oposicion", "vista"].filter((t) => listos().includes(t));  // el del cliente primero: el interno hereda su INFPR
    let alguno = false;
    for (const t of orden) {
      estado.textContent = t === "cliente" ? "Generando el PDF…" : t === "interno" ? "Guardando el informe interno…"
        : t === "vista" ? "Guardando el informe de vista…" : "Guardando el informe de oposición…";
      try {
        const body = { texto: $m("ix-texto-" + t).value, tipo: t,
          ...(elegido.cliente_id ? { cliente_id: elegido.cliente_id } : { lead_clave: elegido.lead_clave }) };
        const r = await apiJson("/api/informes/cargar", "POST", body);
        marcarHecho(t, r); alguno = true;
        if (r.cliente_id && elegido.tipo === "lead") elegido = { tipo: "cliente", cliente_id: r.cliente_id, nombre: elegido.nombre };
      } catch (e) {
        estado.className = "ix-estado mal";
        estado.textContent = `${IX_CAJAS[t].titulo}: ${e.message}`;
        actualizarBoton();
        if (alguno && alTerminar) alTerminar();
        return;
      }
    }
    estado.className = "ix-estado ok"; estado.textContent = "Listo";
    if (fijo || elegido) { const c = $m("ix-cambiar"); if (c) c.remove(); }
    guardar.textContent = "Guardado";
    guardar.disabled = true;
    $m("ix-cancelar").textContent = "Cerrar";
    if (alTerminar) alTerminar();
  };
  if (texto) {  // vino por link: el JSON va al cuadro que corresponde (el preliminar: cliente o interno)
    const p = ixPrevia(texto);
    const caja = $m("ix-texto-" + (tipos.includes(p.tipo) ? p.tipo : tipos[0]));
    caja.value = texto.trim();
    caja.dispatchEvent(new Event("input"));
  }
  actualizarBoton();
  modal.classList.add("abierto");
  if (elegido) setTimeout(() => $m("ix-texto-" + tipos[0]).focus(), 30);
}
