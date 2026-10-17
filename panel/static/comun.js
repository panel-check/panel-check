/* Funciones compartidas entre el dashboard (index.html) y la vista de
 * detalle por titular (titular.html). Nada de estado global de página
 * acá — cada página mantiene su propio `estado`/`cargar()`. */

async function api(path, opciones = {}) {
  const r = await fetch(path, { credentials: "include", ...opciones });
  if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// Aviso corto abajo de la pantalla ("12 marcas marcadas como contactadas").
// tipo: "ok" (oscuro) o "aviso" (ámbar, cuando algo no se pudo hacer del todo).
let _timerAviso = null;
function mostrarAviso(texto, tipo = "ok") {
  let el = document.getElementById("aviso-panel");
  if (!el) {
    el = document.createElement("div");
    el.id = "aviso-panel";
    el.className = "aviso-panel";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = texto;
  el.classList.toggle("aviso", tipo === "aviso");
  void el.offsetWidth; // para que la animación arranque aunque ya estuviera visible
  el.classList.add("visible");
  clearTimeout(_timerAviso);
  _timerAviso = setTimeout(() => el.classList.remove("visible"), tipo === "aviso" ? 7000 : 4500);
}

const TIPOS_MARCA = {
  D: "Denominativa",
  M: "Mixta",
  F: "Figurativa",
  T: "Tridimensional",
};

function tipoLegible(tipo) {
  if (!tipo) return "";
  const letra = tipo.trim().toUpperCase();
  return TIPOS_MARCA[letra] || tipo;
}

// Texto a mostrar cuando una marca todavía no tiene nombre. Figurativa pura
// (logo sin texto) no tiene nombre en ningún lado; el resto está pendiente
// de backfill_denominacion.py (corre solo después de cada escaneo de actas).
function sinNombre(tipo) {
  const t = (tipo || "").trim().toUpperCase();
  if (t === "F") return "(figurativa, sin texto)";
  return "(nombre pendiente)";
}

function badgeLead(row) {
  if (row.es_lead === true) return '<span class="badge lead">LEAD</span>';
  if (row.es_lead === false) return `<span class="badge no-lead">${_escapeHtml(row.caracter || "con agente")}</span>`;
  return '<span class="badge sin-dato">sin verificar</span>';
}

// El número de boletín queda NULL mientras el acta se detectó por escaneo
// directo (scripts/escanear_actas_nuevas.py) y todavía no la publicó el
// boletín real -- antes esto se mostraba como el string literal "null".
function badgePreBoletin(row) {
  if (row.boletin || row.fuente !== "escaneo_directo") return "";
  return '<span class="tooltip badge-vista">🆕 pre-boletín<span class="globo">'
    + 'Detectado directamente por número de acta, antes de que INPI lo publique '
    + 'en un boletín. Todavía no arrancó el plazo de oposición.</span></span>';
}

// Para la columna "Boletín" de la ficha de titular (titular.html), donde sí
// se muestra el número -- acá si no hay número, mostramos el mismo badge.
function badgeBoletin(row) {
  if (row.boletin) return row.boletin;
  return badgePreBoletin(row) || "—";
}

// Cache de filas con oposición de tercero, clave = acta — el popup
// (abrirModalOposicion) lee de acá en vez de recibir el objeto entero por
// el onclick (que solo puede llevar strings simples). Se va completando
// cada vez que se renderiza una fila con badgeOposicion.
window._filasOposicion = window._filasOposicion || {};

// Cuándo se revisó por última vez la oposición de este lead. Desde el
// 15/10/2026 scripts/revisar_oposiciones.py revisa el boletín COMPLETO por
// rotación nocturna (5 boletines por noche, los que estén más cerca de
// cerrar el plazo), no por hitos de 10/23/33 días. Chip gris chico, solo
// para leads ya revisados al menos una vez.
function badgeRevisionOposicion(row) {
  if (!row.es_lead || !row.opo_ultimo_chequeo_en) return "";
  const d = new Date(row.opo_ultimo_chequeo_en);
  if (isNaN(d)) return "";
  const opciones = { timeZone: "America/Argentina/Buenos_Aires", day: "2-digit", month: "2-digit" };
  const corta = d.toLocaleDateString("es-AR", opciones);
  const larga = d.toLocaleDateString("es-AR", { ...opciones, year: "numeric" });
  const detalle = `Oposición revisada por última vez el ${larga} · el boletín se revisa entero por rotación nocturna (mientras el plazo de oposición siga abierto o pendiente de notificación)`
    + (row.tuvo_oposicion === true ? " · con oposición: se sigue mirando" : "");
  return `<span class="tooltip badge sin-dato">🔎 ${corta}<span class="globo">${detalle}</span></span>`;
}

// Estado de la oposición leído del expediente de INPI (scripts/oposiciones_expediente.py).
// "sirve" = nadie la está trabajando todavía, así que vale la pena ofrecer ayuda.
const ESTADOS_OPOSICION = {
  sin_notificar:         { texto: "sin notificar",            clase: "lead",    sirve: true  },
  notificada_en_plazo:   { texto: "plazo corriendo",          clase: "lead",    sirve: true  },
  plazo_vencido:         { texto: "plazo vencido",            clase: "lead",    sirve: true  },
  vista_pendiente:       { texto: "vista sin contestar",      clase: "lead",    sirve: true  },
  oposicion_sin_detalle: { texto: "recién ingresada",         clase: "lead",    sirve: true  },
  contestada:            { texto: "ya contestada",            clase: "sin-dato", sirve: false },
  levantada:             { texto: "levantada",                clase: "sin-dato", sirve: false },
  con_apoderado:         { texto: "ya tiene apoderado",       clase: "sin-dato", sirve: false },
};

function chipEstadoOposicion(row) {
  const e = ESTADOS_OPOSICION[row.estado_oposicion];
  if (!e) return "";
  let texto = e.texto;
  if (row.estado_oposicion === "notificada_en_plazo" && row.oposicion_fecha_vencimiento) {
    const [a, m, d] = String(row.oposicion_fecha_vencimiento).slice(0, 10).split("-");
    texto += ` hasta ${d}/${m}`;
  }
  const detalle = _escapeHtml(row.estado_oposicion_detalle || "")
    + (e.sirve ? " · Nadie la está trabajando: sirve para ofrecer ayuda." : " · Ya se está trabajando o no hay nada que hacer: no sirve.");
  return ` <span class="tooltip badge ${e.clase}">${texto}<span class="globo">${detalle}</span></span>`;
}

function badgeOposicion(row) {
  // tuvo_oposicion se completa recién ~33 días después de la publicación
  // (scripts/revisar_oposiciones.py), y solo para leads reales. Antes de eso
  // queda en null ("todavía no se revisó", no "no tiene").
  if (row.tuvo_oposicion !== true) return "";
  const detalle = row.detalle_oposicion || "";
  // detalle_oposicion trae "fecha - Indice - Referencia" tal cual vino de
  // Grilla Digital (ver validar_leads.detectar_oposicion). "OPO"/"OPOSICION"
  // es una oposición real de un tercero (alguien se está oponiendo a la
  // marca) — distinto de una "VISTA" propia de INPI (una observación de
  // oficio, sin que nadie se oponga). Se muestran con badges distintos para
  // no confundir urgencia: una oposición de tercero es más urgente que una
  // vista de INPI.
  const esOposicionDeTercero = /OPO/i.test(detalle);
  if (esOposicionDeTercero) {
    // El detalle rico (oponente/fundamento) puede ser largo — no entra
    // legible en un tooltip de hover (ver corrección del 29/09/2026), así
    // que ahora es un botón que abre un popup con el texto completo.
    window._filasOposicion[row.acta] = row;
    return `<button type="button" class="badge-oposicion" onclick="abrirModalOposicion('${_idSeguro(row.acta)}')">⚠ Ver oposición</button>${chipEstadoOposicion(row)}`;
  }
  return `<span class="tooltip badge-vista">👁 VISTA DE INPI<span class="globo">${_escapeHtml(detalle) || "observación de oficio de INPI detectada en Grilla Digital"}</span></span>${chipEstadoOposicion(row)}`;
}

// Vista de INPI en una marca PRE-BOLETÍN (scripts/probar_vistas_preboletin.py).
// Es una alerta aparte de la oposición de un tercero: INPI observó la solicitud
// antes de publicarla y la publicación queda frenada hasta que el titular contesta.
const ESTADOS_VISTA_PRE = {
  sin_notificar:       { texto: "sin notificar",       clase: "lead",     sirve: true  },
  notificada_en_plazo: { texto: "plazo corriendo",     clase: "lead",     sirve: true  },
  plazo_vencido:       { texto: "plazo vencido",       clase: "lead",     sirve: true  },
  resuelta:            { texto: "ya resuelta",         clase: "sin-dato", sirve: false },
  con_apoderado:       { texto: "ya tiene apoderado",  clase: "sin-dato", sirve: false },
};

// Tipo de vista según el PDF: de fondo (observa el signo, 30 días corridos) o formal (pide logotipos, 10 días hábiles).
const TIPOS_VISTA_PRE = { fondo: "de fondo", formal: "formal (logos)", fondo_y_formal: "de fondo y formal" };

// Cache de filas con vista antes de publicar, clave = acta: el popup (abrirModalVistaPre) lee de acá.
window._filasVistaPre = window._filasVistaPre || {};

function _vencimientoVistaPre(row) {
  // Vence lo más cercano entre lo que dice el texto de la vista y lo que marca INPI.
  const fechas = [row.vista_vencimiento_calculado, row.vista_fecha_vencimiento].filter(Boolean).map(f => String(f).slice(0, 10));
  return fechas.sort()[0] || "";
}

function badgeVistaPre(row) {
  if (row.tuvo_vista !== true) return "";
  const e = ESTADOS_VISTA_PRE[row.vista_estado];
  const tipo = TIPOS_VISTA_PRE[row.vista_tipo] || "";
  const venc = _vencimientoVistaPre(row);
  window._filasVistaPre[row.acta] = row;
  let chip = "";
  if (e) {
    let texto = e.texto;
    if (row.vista_estado === "notificada_en_plazo" && venc) {
      const [a, m, d] = venc.split("-");
      texto += ` hasta ${d}/${m}`;
    }
    const detalle = _escapeHtml(row.vista_detalle || "")
      + (e.sirve ? " · Nadie la contestó: sirve para ofrecer ayuda." : " · Ya se resolvió o se está trabajando: no sirve.");
    chip = ` <span class="tooltip badge ${e.clase}">${texto}<span class="globo">${detalle}</span></span>`;
  }
  return `<button type="button" class="badge-vista badge-vista-btn" title="Ver el detalle de la vista" onclick="abrirModalVistaPre('${_idSeguro(row.acta)}')">👁 VISTA ANTES DE PUBLICAR${tipo ? " · " + tipo : ""}</button>${chip}`;
}

// Qué significa cada artículo de la Ley 22.362 que cita una vista de fondo (resumen orientativo).
const ARTICULOS_VISTA = {
  "Art. 2 inc. a)": "El signo sería la designación necesaria o habitual del producto o servicio, o descriptivo de sus características (no distingue).",
  "Art. 2 inc. b)": "El signo habría pasado al uso general antes de la solicitud (no distingue).",
  "Art. 3 inc. a)": "El signo sería idéntico o parecido a una marca ya registrada o solicitada antes para productos o servicios iguales o parecidos.",
};

function abrirModalVistaPre(acta) {
  const row = window._filasVistaPre[acta];
  if (!row) return;
  _asegurarModalOposicion();
  const fmtF = (f) => { if (!f) return ""; const [a, m, d] = String(f).slice(0, 10).split("-"); return `${d}/${m}/${a}`; };
  const e = ESTADOS_VISTA_PRE[row.vista_estado];
  const tipo = row.vista_tipo;
  const esFondo = tipo === "fondo" || tipo === "fondo_y_formal";
  const esFormal = tipo === "formal" || tipo === "fondo_y_formal";
  const venc = _vencimientoVistaPre(row);

  const fechas = [
    row.vista_fecha ? `Vista: ${fmtF(row.vista_fecha)}` : "",
    row.vista_fecha_notificacion ? `Notificada al titular: ${fmtF(row.vista_fecha_notificacion)}` : "",
    row.vista_vencimiento_calculado ? `Vence según el texto de la vista (${_escapeHtml(row.vista_plazo || "")}): ${fmtF(row.vista_vencimiento_calculado)}` : "",
    row.vista_fecha_vencimiento ? `INPI marca en el expediente: ${fmtF(row.vista_fecha_vencimiento)}` : "",
  ].filter(Boolean).join(" · ");
  const estadoHtml = e
    ? `<p class="mo-estado"><strong>${e.sirve ? "✔ Sirve" : "✖ No sirve"} — ${_escapeHtml(e.texto)}${e.sirve && venc ? ` (vence ${_escapeHtml(fmtF(venc))})` : ""}.</strong> ` +
      `${e.sirve ? "Nadie la contestó todavía: la publicación de la marca está frenada hasta que se conteste." : "Ya se resolvió o el titular se está ocupando."}` +
      `${fechas ? `<br><span class="mo-doc">${fechas}</span>` : ""}</p>`
    : "";

  const articulos = (row.vista_articulos || "").split(",").map(a => a.trim()).filter(Boolean);
  const bloqueFondo = esFondo ? `
    <p class="mo-oponente"><strong>Vista de fondo</strong> — INPI observa el signo${articulos.length ? "" : ""}:</p>
    ${articulos.map(a => `<p class="mo-fundamento"><strong>${_escapeHtml(a)} de la Ley 22.362.</strong> ${_escapeHtml(ARTICULOS_VISTA[a] || "")}</p>`).join("")}
    <p class="mo-doc">Plazo: 30 días corridos (Res. INPI P 123/2019), ampliable con las extensiones automáticas pagando el arancel (cód. 770.001). Es la que se puede defender: el servicio es contestar la vista con argumentos.</p>` : "";
  const bloqueFormal = esFormal ? `
    <p class="mo-oponente"><strong>Vista formal</strong> — INPI pide subir los logotipos de nuevo:</p>
    <p class="mo-fundamento">Archivo JPG o BMP de 200 a 300 DPI y hasta 2 MB; trazos de al menos 0,3 mm y tipografías de al menos 2 mm; si se reivindican colores, el logo en esos colores, y si no, en negro o escala de grises. Se contesta pagando el arancel de contestación de vista formal (cód. 750001): sin pagarlo la contestación es nula.</p>
    <p class="mo-doc">Plazo: 10 días hábiles (art. 12 del decreto 242/19) bajo apercibimiento de resolución denegatoria. Es un trámite más simple y barato; muchos titulares lo resuelven solos.</p>` : "";
  const bloqueTipo = (esFondo || esFormal) ? "" : `<p class="mo-fundamento">No se pudo leer el tipo de esta vista. ${row.vista_detalle ? _escapeHtml(row.vista_detalle) : ""}</p>`;

  const antecedentes = (row.vista_antecedente || "").split(";").map(a => a.trim()).filter(Boolean);
  const bloqueAntecedente = antecedentes.length ? `
    <div class="mo-titulo">Marca anterior con la que se parece:</div>
    ${antecedentes.map(a => { const n = (a.match(/^\d+/) || [""])[0];
      return `<p class="mo-fundamento">${_escapeHtml(a)} ${n ? `<a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(n)}')">Ver ficha del acta ${_escapeHtml(n)} ↗</a>` : ""}</p>`; }).join("")}` : "";

  document.getElementById("modal-oposicion-contenido").innerHTML = `
    <h3>👁 Vista antes de publicar — acta ${_escapeHtml(row.acta)}</h3>
    <p class="mo-doc">No es una oposición de un tercero: es una observación de INPI sobre la solicitud antes de publicarla.</p>
    ${estadoHtml}${bloqueFondo}${bloqueFormal}${bloqueTipo}${bloqueAntecedente}
    <p><a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(row.acta)}')">Ver expediente completo en INPI ↗</a></p>`;
  document.getElementById("modal-oposicion").classList.add("abierto");
}

function _escapeHtml(s) {
  return (s ?? "").toString()
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Para valores que van dentro de un onclick="...('X')" o de un selector: solo
// deja letras, números, punto, guion y guion bajo (un acta o un id normal no
// cambia). Escapar con _escapeHtml no alcanza ahí: el navegador decodifica
// &#39; antes de ejecutar el JavaScript del atributo.
function _idSeguro(s) {
  return (s ?? "").toString().replace(/[^A-Za-z0-9._-]/g, "");
}

function _asegurarModalOposicion() {
  if (document.getElementById("modal-oposicion")) return;
  const div = document.createElement("div");
  div.id = "modal-oposicion";
  div.className = "modal-fondo";
  div.innerHTML = `
    <div class="modal-caja" onclick="event.stopPropagation()">
      <button type="button" class="modal-cerrar" onclick="cerrarModalOposicion()">&times;</button>
      <div id="modal-oposicion-contenido"></div>
    </div>
  `;
  div.addEventListener("click", cerrarModalOposicion); // click en el fondo, fuera de la caja
  document.body.appendChild(div);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") cerrarModalOposicion();
  });
}

function cerrarModalOposicion() {
  const modal = document.getElementById("modal-oposicion");
  if (modal) modal.classList.remove("abierto");
}

function _fechaDeDetalleCrudo(detalle) {
  // detalle_oposicion viene como "fecha - Indice - Referencia", con la
  // fecha en formato .NET /Date(ms)/ tal cual la devuelve Grilla Digital.
  const m = /Date\((-?\d+)/.exec(detalle || "");
  if (!m) return "";
  const d = new Date(parseInt(m[1], 10));
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString("es-AR");
}

// Oposiciones completas ya verificadas en INPI desde la pestaña «Análisis de
// marca» (ver _completar_oposiciones_verificadas en app.py) — cuando están,
// el popup de acá se arma con ellas en vez de con la única oposición que
// guarda la tabla `marcas`, para que las dos vistas no queden desincronizadas.
function _oposicionesVerificadas(row) {
  return Array.isArray(row.oposiciones_verificadas) && row.oposiciones_verificadas.length
    ? row.oposiciones_verificadas : null;
}

function _fmtFechaCorta(f) {
  if (!f) return "";
  const [a, m, d] = String(f).slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

function _bloqueMarcaOpuestaVerificada(mo) {
  const acta = _escapeHtml(mo.acta || "");
  const linkFicha = mo.acta
    ? `<a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(mo.acta)}')">Ver ficha del acta ${acta} ↗</a>`
    : "";
  if (mo.estado === "no_existe") {
    return `<div class="mo-marca-opuesta">Marca del oponente: acta ${acta} (no se encontró el expediente en INPI)</div>`;
  }
  const datos = [
    mo.denominacion ? `<strong>${_escapeHtml(mo.denominacion)}</strong>` : "",
    mo.clase ? `clase ${_escapeHtml(mo.clase)}` : "",
    mo.tipo_marca ? _escapeHtml(mo.tipo_marca) : "",
  ].filter(Boolean).join(" · ");
  if (!datos && !linkFicha) return "";
  return `<div class="mo-marca-opuesta">Marca del oponente: ${datos}${linkFicha ? ` — ${linkFicha}` : ""}</div>`;
}

function _bloqueOposicionVerificada(i, n) {
  const fechas = [
    i.presentacion ? `Presentada: ${_fmtFechaCorta(i.presentacion)}` : "",
    i.notificacion ? `Notificada: ${_fmtFechaCorta(i.notificacion)}` : "",
    i.vencimiento ? `Vence: ${_fmtFechaCorta(i.vencimiento)}` : "",
    i.levantamiento ? `Levantada: ${_fmtFechaCorta(i.levantamiento)}` : "",
  ].filter(Boolean).join(" · ");
  const opuestas = (i.marcas_opuestas || []).map(_bloqueMarcaOpuestaVerificada).filter(Boolean).join("");
  return `
    <div class="mo-oposicion-item">
      <p class="mo-oponente"><strong>${n}. ${_escapeHtml(i.oponente || "oponente no informado")}</strong></p>
      ${fechas || i.agente_oponente
        ? `<p class="mo-doc">${_escapeHtml(fechas)}${i.agente_oponente ? ` · Agente del oponente: ${_escapeHtml(i.agente_oponente)}` : ""}</p>`
        : ""}
      ${i.fundamento ? `<p class="mo-fundamento">${_escapeHtml(i.fundamento)}</p>` : ""}
      ${opuestas}
    </div>`;
}

function abrirModalOposicion(acta) {
  const row = window._filasOposicion[acta];
  if (!row) return;
  _asegurarModalOposicion();

  const doc = row.oponente_tipo_doc && row.oponente_numero_doc
    ? `${row.oponente_tipo_doc} ${row.oponente_numero_doc}` : "";
  const cuit = row.oponente_cuit ? `CUIT ${row.oponente_cuit}` : "";
  const identificacion = [doc, cuit].filter(Boolean).join(" · ");

  // Si después de la oposición apareció alguien sumándose como
  // apoderado/gestor ("Acompaña Poder"/"Ratifica" — ver
  // revisar_oposiciones.py), es señal de que el titular ya está trabajando
  // con alguien para responderla: se muestra un badge bien visible arriba
  // del todo, para descartarlo de un vistazo como lead frío prioritario.
  const badgeRepresentacion = row.representacion_posterior_oposicion
    ? `<p class="mo-representacion-posterior">⚖ Ya se sumó un apoderado/gestor después de la oposición` +
      `${row.detalle_representacion_posterior ? ` <span class="mo-doc">(${_escapeHtml(row.detalle_representacion_posterior)})</span>` : ""}` +
      `</p>`
    : "";

  // Estado leído del expediente: en qué punto está y si todavía sirve para ofrecer ayuda.
  const fmtF = (f) => { if (!f) return ""; const [a, m, d] = String(f).slice(0, 10).split("-"); return `${d}/${m}/${a}`; };
  const est = ESTADOS_OPOSICION[row.estado_oposicion];
  const fechas = [
    row.oposicion_fecha_presentacion ? `Presentada: ${fmtF(row.oposicion_fecha_presentacion)}` : "",
    row.oposicion_fecha_notificacion ? `Notificada al titular: ${fmtF(row.oposicion_fecha_notificacion)}` : "",
    row.oposicion_fecha_vencimiento ? `Vence el plazo para contestar: ${fmtF(row.oposicion_fecha_vencimiento)}` : "",
    row.oposicion_fecha_levantamiento ? `Levantada: ${fmtF(row.oposicion_fecha_levantamiento)}` : "",
  ].filter(Boolean).join(" · ");
  const estadoHtml = est
    ? `<p class="mo-estado"><strong>${est.sirve ? "✔ Sirve" : "✖ No sirve"} — ${_escapeHtml(est.texto)}.</strong> ${_escapeHtml(row.estado_oposicion_detalle || "")}` +
      `${fechas ? `<br><span class="mo-doc">${_escapeHtml(fechas)}</span>` : ""}</p>`
    : "";

  const contenido = document.getElementById("modal-oposicion-contenido");
  let cuerpo;
  const verificadas = _oposicionesVerificadas(row);
  if (verificadas) {
    // Ya se verificaron TODAS las oposiciones del expediente desde «Análisis
    // de marca»: se muestran todas acá (sin buscar nada más), en vez de la
    // única que guarda `marcas`.
    const cuando = row.oposiciones_verificadas_en
      ? new Date(row.oposiciones_verificadas_en).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" })
      : "";
    cuerpo = `
      ${estadoHtml}${badgeRepresentacion}
      <p class="mo-verificado">✔ ${verificadas.length} oposición${verificadas.length === 1 ? "" : "es"} verificada${verificadas.length === 1 ? "" : "s"} en INPI${cuando ? ` el ${cuando}` : ""}.</p>
      ${verificadas.map((i, idx) => _bloqueOposicionVerificada(i, idx + 1)).join("")}
    `;
    contenido.innerHTML = `<h3>⚠ Oposición — acta ${_escapeHtml(row.acta)}</h3>${cuerpo}`;
    document.getElementById("modal-oposicion").classList.add("abierto");
    return;
  }
  if (row.oponente_nombre || row.fundamento_oposicion) {
    cuerpo = `
      ${estadoHtml}${badgeRepresentacion}
      ${row.oponente_nombre ? `<p class="mo-oponente"><strong>${_escapeHtml(row.oponente_nombre)}</strong>${identificacion ? ` <span class="mo-doc">(${_escapeHtml(identificacion)})</span>` : ""}</p>` : ""}
      ${row.fundamento_oposicion ? `<p class="mo-fundamento">${_escapeHtml(row.fundamento_oposicion)}</p>` : ""}
      <div id="mo-marca-oponente">Buscando la marca del oponente…</div>
    `;
  } else {
    // No se pudo bajar/parsear el Formulario de esta oposición (pasa en
    // algunos trámites que no lo listan aparte en Grilla Digital — ver
    // manual). Mostramos lo poco que sabemos, en texto legible, y un link
    // para que la persona lo confirme a mano en INPI en vez de dejar el
    // popup vacío.
    const fecha = _fechaDeDetalleCrudo(row.detalle_oposicion);
    cuerpo = `
      ${estadoHtml}${badgeRepresentacion}
      <p class="mo-fundamento">No se pudo obtener el detalle completo (oponente y fundamento) de esta oposición —
      el trámite no tiene un Formulario propio listado en Grilla Digital, o no se pudo descargar.
      ${fecha ? `Se detectó un ingreso de "Opo. de Marcas" el ${fecha}.` : ""}</p>
      <a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(row.acta)}')">Ver expediente completo en INPI ↗</a>
    `;
  }

  contenido.innerHTML = `<h3>⚠ Oposición — acta ${_escapeHtml(row.acta)}</h3>${cuerpo}`;
  document.getElementById("modal-oposicion").classList.add("abierto");
  if (row.oponente_nombre || row.fundamento_oposicion) _renderMarcaOponente(row);
}

function _renderMarcaOponente(row) {
  const cont = document.getElementById("mo-marca-oponente");
  if (!cont) return;

  // Caso más confiable: el fundamento citaba una o más ACTA concretas del
  // oponente — mismo botón "Ver ficha" que ya usa el resto del panel, sin
  // depender de ninguna búsqueda.
  if (row.actas_marca_oponente) {
    const actas = row.actas_marca_oponente.split(",").filter(Boolean);
    cont.innerHTML = `<div class="mo-titulo">Marca(s) que invoca el oponente:</div>` +
      actas.map(a => `<a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(a)}')">Ver ficha del acta ${_escapeHtml(a)} ↗</a>`).join(" ");
    return;
  }

  // Solo tenemos denominación + número de registro citados en el
  // fundamento (sin ACTA propia) — hay que buscarla. Mejor esfuerzo: no
  // siempre encuentra el registro exacto (el nombre en el texto legal
  // puede tener espaciado distinto al cargado en INPI), así que mostramos
  // la lista completa de resultados y que la persona elija. Se dispara
  // sola al abrir el popup (antes había que apretar un botón "Buscar" —
  // reportado como confuso/no funcional el 29/09/2026, la persona
  // esperaba el mismo comportamiento directo que con actas_marca_oponente);
  // el botón de reintentar queda como respaldo si la búsqueda falla.
  if (row.marca_oponente_denominacion) {
    _buscarMarcaOponente(row.marca_oponente_denominacion, row.marca_oponente_numero_registro || "");
    return;
  }

  cont.innerHTML = "";
}

async function _buscarMarcaOponente(denominacion, numeroBuscado) {
  window._ultimaBusquedaMO = { d: denominacion, n: numeroBuscado };
  const cont = document.getElementById("mo-marca-oponente");
  cont.innerHTML = "Buscando…";
  try {
    const data = await api(`/api/marcas/buscar-marca?denominacion=${encodeURIComponent(denominacion)}`);
    const filas = data.resultados || [];
    const usada = data.denominacion_usada || denominacion;
    const nota = usada !== denominacion ? ` (probado también sin espacios: "${_escapeHtml(usada)}")` : "";
    if (!filas.length) {
      cont.innerHTML = `No se encontró ninguna marca con "${_escapeHtml(denominacion)}" en INPI, ni sin espacios ("${_escapeHtml(usada)}") — puede que esté cargada con otra redacción.`;
      return;
    }
    cont.innerHTML = `<div class="mo-titulo">Resultados para "${_escapeHtml(usada)}"${nota}:</div>` +
      filas.map(f => {
        const coincide = numeroBuscado && f.numero_resolucion && f.numero_resolucion.replace(/\D/g, "") === numeroBuscado.replace(/\D/g, "");
        return `<div class="mo-resultado ${coincide ? "mo-coincide" : ""}">
          ${_escapeHtml(f.denominacion)} — clase ${_escapeHtml(f.clase)}${f.numero_resolucion ? ` · Reg. ${_escapeHtml(f.numero_resolucion)}` : ""}
          <a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(f.acta)}')">Ver ficha ↗</a>
        </div>`;
      }).join("");
  } catch (e) {
    cont.innerHTML = `No se pudo buscar (${e.message}). <button type="button" onclick="_buscarMarcaOponente(window._ultimaBusquedaMO.d, window._ultimaBusquedaMO.n)">Reintentar</button>`;
  }
}

function fmtFecha(f) {
  if (!f) return "";
  return f.split("T")[0].split(" ")[0];
}

// Igual que fmtFecha, pero con guion no separable (U+2011) en vez de "-":
// evita que el navegador corte una fecha tipo "2036-02-03" justo a la mitad
// al hacer wrap dentro de un globo de tooltip angosto (se ve horrible: "2036-"
// en una línea y "02-03" en la siguiente).
function fmtFechaSinCorte(f) {
  return fmtFecha(f).replace(/-/g, "‑");
}

// estado_tramite lo completa scripts/revisar_estado.py leyendo la sección
// RESOLUCIÓN del expediente — null mientras el trámite sigue en curso
// (examen de forma/fondo, publicación, oposición), no es un error. En el
// dashboard compacto (index.html) no mostramos nada mientras está en
// trámite (sería un badge gris en casi todas las filas, sin aportar nada);
// en el detalle por titular sí conviene aclararlo (ver mostrarSinDato).
function badgeEstadoTramite(row, mostrarSinDato = false) {
  if (!row.estado_tramite) {
    return mostrarSinDato ? '<span class="badge sin-dato">en trámite</span>' : "";
  }
  if (row.estado_tramite === "Concedida") {
    const detalle = `Concedida el ${fmtFechaSinCorte(row.fecha_concesion) || "?"}`
      + (row.fecha_vencimiento_marca ? ` · vence ${fmtFechaSinCorte(row.fecha_vencimiento_marca)}` : "");
    return `<span class="tooltip badge-concedida">✓ CONCEDIDA<span class="globo">${detalle}</span></span>`;
  }
  if (row.estado_tramite === "Denegada") {
    return '<span class="badge no-lead">DENEGADA</span>';
  }
  return `<span class="badge sin-dato">${_escapeHtml(row.estado_tramite)}</span>`;
}

function fechaPublicacionOFallback(r) {
  // Pre-boletín (todavía sin número de boletín): la marca no se publicó, así
  // que "Fecha Boletín" queda vacía. La fecha recién aparece cuando la marca
  // sale en un boletín y la fila se actualiza.
  if (!r.boletin) return "";
  // Preferimos fecha_publicacion (la real, de "Hoja Publicacion" en Grilla
  // Digital) — solo la tienen los leads ya verificados. Para el resto (sin
  // verificar, o con agente) no se scrapea Grilla Digital, así que mostramos
  // fecha_presentacion como respaldo.
  return fmtFecha(r.fecha_publicacion) || fmtFecha(r.fecha_presentacion);
}

function normalizarTitular(titular) {
  return (titular || "").trim().toUpperCase().replace(/\s+/g, " ");
}

function claveTitular(r) {
  // El CUIT es la clave real (se lee de TITULARIDAD en el expediente, para
  // cualquier tipo de marca). Si todavía no se pudo capturar (bloqueo del
  // WAF, falta reintentar, etc.) usamos el nombre normalizado como respaldo.
  const cuit = (r.cuit || "").trim();
  return cuit || normalizarTitular(r.titular);
}

function linkTitular(r) {
  const clave = claveTitular(r);
  if (!clave) return _escapeHtml(r.titular || "");
  return `<a class="link-titular" href="/titular/${encodeURIComponent(clave)}">${_escapeHtml(r.titular || clave)}</a>`;
}

function celdaEmail(r) {
  if (r.email) {
    return `<a href="mailto:${_escapeHtml(r.email)}">${_escapeHtml(r.email)}</a>`;
  }
  if (r.es_lead !== true) {
    return ""; // no aplica (tiene apoderado) o todavía no se verificó
  }
  const motivo = r.motivo_sin_email || "no se pudo determinar el motivo";
  return `
    <span class="sin-email">
      <span class="tooltip">sin email<span class="globo">${_escapeHtml(motivo)}</span></span>
      <button class="reintentar" onclick="reintentarEmail('${_idSeguro(r.acta)}', this)">Reintentar</button>
      <a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(r.acta)}')" title="Ver expediente completo en INPI">Ver ficha ↗</a>
    </span>
  `;
}

function celdaEmailIcono(r) {
  // Versión resumida para el dashboard principal: no expone la dirección,
  // solo si se pudo recuperar o no (el detalle por titular sí muestra el
  // email completo).
  if (r.email) {
    return '<span class="icono-email ok" title="Tiene email recuperado">&#10003;</span>';
  }
  if (r.es_lead !== true) {
    return ""; // no aplica (tiene apoderado) o todavía no se verificó
  }
  const motivo = r.motivo_sin_email || "no se pudo determinar el motivo";
  return `
    <span class="sin-email">
      <span class="icono-email no tooltip">&#10007;<span class="globo">${_escapeHtml(motivo)}</span></span>
      <button class="reintentar" onclick="reintentarEmail('${_idSeguro(r.acta)}', this)">Reintentar</button>
    </span>
  `;
}

async function reintentarEmail(acta, boton) {
  boton.disabled = true;
  const textoOriginal = boton.textContent;
  boton.textContent = "Consultando...";
  try {
    await api(`/api/marcas/${encodeURIComponent(acta)}/reintentar-email`, { method: "POST" });
    if (typeof window.alRecargar === "function") await window.alRecargar();
  } catch (e) {
    boton.disabled = false;
    boton.textContent = textoOriginal;
    alert(`No se pudo reintentar el acta ${acta}: ${e.message}`);
  }
}

function abrirActa(acta) {
  // Un <a href> normal solo puede hacer GET, y esa versión de la página de
  // INPI viene recortada (solo "PROTECCION"). La ficha completa (titularidad,
  // datos generales, botón GRILLA DIGITAL) solo aparece cuando la consulta
  // se hace por POST con el acta en el body — lo mismo que hace el pipeline
  // en validar_leads.py. Por eso armamos un form y lo mandamos por POST en
  // una pestaña nueva en vez de usar un link directo.
  const form = document.createElement("form");
  form.method = "POST";
  form.action = "https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado";
  form.target = "_blank";
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "acta";
  input.value = acta;
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
  form.remove();
}

// Va directo a la Grilla Digital del acta (la tabla de archivos del expediente: formularios,
// oposiciones, hoja de publicación...), sin pasar por la ficha del expediente. Es el mismo POST
// que hace el botón GRILLA DIGITAL del portal ("fname" = "1-<acta>", ver validar_leads.py).
function abrirGrilla(acta) {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = "https://portaltramites.inpi.gob.ar/Home/GrillaDigital";
  form.target = "_blank";
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "fname";
  input.value = `1-${acta}`;
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
  form.remove();
}

async function marcarContactado(acta, valorActual) {
  const nuevo = !valorActual;
  await api(`/api/marcas/${encodeURIComponent(acta)}/contactado?valor=${nuevo}`, { method: "POST" });
  if (typeof window.alRecargar === "function") await window.alRecargar();
}

/* ── Comentarios internos ──────────────────────────────────────────────
 * Notas entre las personas del equipo (ver /comentarios). Cada comentario
 * puede ir vinculado a un acta y/o dirigido a una persona puntual. Acá está
 * lo compartido: el globito 💬 de cada fila, el popup de comentarios de un
 * acta y el contador de "nuevos" del encabezado. */

window._comentariosPorActa = window._comentariosPorActa || {};
let _yo = null; // {usuario, usuarios}

async function quienSoy() {
  if (!_yo) _yo = await api("/api/yo");
  return _yo;
}

async function apiJson(path, metodo, body) {
  const r = await fetch(path, {
    method: metodo,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
  if (!r.ok) {
    let detalle = `${r.status}`;
    try { detalle = (await r.json()).detail || detalle; } catch (_) {}
    throw new Error(detalle);
  }
  return r.json();
}

function fmtFechaHora(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-AR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Carga los contadores de comentarios de todas las actas visibles y pinta
// los globitos (los botones ya están en el HTML con data-acta-coment).
async function cargarContadoresComentarios(actas) {
  const unicas = [...new Set(actas.filter(Boolean))];
  if (!unicas.length) return;
  try {
    const data = await api(`/api/comentarios/por-acta?actas=${encodeURIComponent(unicas.join(","))}`);
    Object.assign(window._comentariosPorActa, data);
  } catch (_) { return; }
  document.querySelectorAll("[data-acta-coment]").forEach(pintarBotonComentarios);
}

function pintarBotonComentarios(boton) {
  const c = window._comentariosPorActa[boton.dataset.actaComent];
  boton.classList.toggle("con-abiertos", !!(c && c.abiertos));
  boton.classList.toggle("con-comentarios", !!(c && c.total));
  boton.innerHTML = c && c.total ? `💬 ${c.abiertos || c.total}` : "💬";
  boton.title = c && c.total
    ? `${c.total} comentario(s), ${c.abiertos} sin resolver — clic para ver o agregar`
    : "Agregar un comentario interno sobre esta marca";
}

function botonComentarios(acta) {
  if (!acta) return "";
  return `<button type="button" class="btn-coment" data-acta-coment="${_escapeHtml(acta)}" onclick="abrirComentariosActa('${_idSeguro(acta)}')">💬</button>`;
}

function htmlComentario(c, { mostrarActa = true } = {}) {
  const yo = _yo ? _yo.usuario : null;
  const para = c.destinatario ? `→ <strong>${_escapeHtml(c.destinatario)}</strong>` : '→ <span class="com-todos">todo el equipo</span>';
  const marca = c.denominacion_inpi || c.denominacion;
  const claveTit = (c.cuit || "").trim() || normalizarTitular(c.titular);
  const vinculo = mostrarActa && c.acta
    ? `<div class="com-acta">📎 Acta <a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${_idSeguro(c.acta)}')" title="Ver expediente en INPI">${_escapeHtml(c.acta)} ↗</a>`
      + (marca ? ` · ${_escapeHtml(marca)}` : "")
      + (c.titular && claveTit ? ` · <a class="link-titular" href="/titular/${encodeURIComponent(claveTit)}">${_escapeHtml(c.titular)}</a>` : "")
      + (!c.titular && !marca ? ' <span class="com-todos">(no está cargada en el panel)</span>' : "")
      + `</div>`
    : "";
  const resuelto = c.resuelto
    ? `<span class="com-resuelto">✓ Resuelto por ${_escapeHtml(c.resuelto_por || "?")} · ${fmtFechaHora(c.resuelto_en)}</span>`
    : "";
  const acciones = `
    <button type="button" onclick="responderComentario(${c.id})">Responder</button>
    <button type="button" onclick="resolverComentario(${c.id}, ${!c.resuelto})">${c.resuelto ? "Reabrir" : "✓ Resolver"}</button>
    ${c.autor === yo ? `<button type="button" class="com-borrar" onclick="borrarComentario(${c.id})">Borrar</button>` : ""}
  `;
  window._comentariosCache = window._comentariosCache || {};
  window._comentariosCache[c.id] = c;
  return `
    <div class="comentario ${c.resuelto ? "resuelto" : ""} ${c.destinatario && c.destinatario === yo ? "para-mi" : ""}">
      <div class="com-cabecera"><strong>${_escapeHtml(c.autor)}</strong> ${para} <span class="com-fecha">${fmtFechaHora(c.creado_en)}</span></div>
      ${vinculo}
      <div class="com-texto">${_escapeHtml(c.texto).replace(/\n/g, "<br>")}</div>
      <div class="com-pie">${resuelto}<span class="com-acciones">${acciones}</span></div>
    </div>
  `;
}

function htmlFormComentario({ acta = "", conActa = true, idPrefijo = "fc" } = {}) {
  const opciones = (_yo ? _yo.usuarios : [])
    .filter(u => !_yo || u !== _yo.usuario)
    .map(u => `<option value="${_escapeHtml(u)}">${_escapeHtml(u)}</option>`).join("");
  return `
    <form class="form-coment" onsubmit="enviarComentario(event, '${idPrefijo}')">
      <textarea id="${idPrefijo}-texto" rows="3" placeholder="Escribí un comentario para el equipo..." required></textarea>
      <div class="form-coment-fila">
        <label>Para
          <select id="${idPrefijo}-para"><option value="">Todo el equipo</option>${opciones}</select>
        </label>
        ${conActa
          ? `<label>Acta (opcional)<input id="${idPrefijo}-acta" type="text" inputmode="numeric" placeholder="ej. 4797001" value="${_escapeHtml(acta)}" /></label>`
          : `<input id="${idPrefijo}-acta" type="hidden" value="${_escapeHtml(acta)}" />`}
        <button type="submit">Enviar</button>
      </div>
    </form>
  `;
}

async function enviarComentario(ev, idPrefijo) {
  ev.preventDefault();
  const texto = document.getElementById(`${idPrefijo}-texto`);
  const boton = ev.target.querySelector("button[type=submit]");
  boton.disabled = true;
  try {
    await apiJson("/api/comentarios", "POST", {
      texto: texto.value,
      acta: document.getElementById(`${idPrefijo}-acta`).value || null,
      destinatario: document.getElementById(`${idPrefijo}-para`).value || null,
    });
    texto.value = "";
    await _refrescarComentarios();
  } catch (e) {
    alert(`No se pudo guardar el comentario: ${e.message}`);
  } finally {
    boton.disabled = false;
  }
}

// Cada página define window.alCambiarComentarios para recargar lo suyo;
// si el popup de un acta está abierto, también se recarga.
async function _refrescarComentarios() {
  const modal = document.getElementById("modal-comentarios");
  if (modal && modal.classList.contains("abierto") && modal.dataset.acta) {
    await _cargarModalComentarios(modal.dataset.acta);
  }
  if (typeof window.alCambiarComentarios === "function") await window.alCambiarComentarios();
}

async function resolverComentario(id, valor) {
  try {
    await apiJson(`/api/comentarios/${id}/resolver?valor=${valor}`, "POST");
    await _refrescarComentarios();
  } catch (e) { alert(`No se pudo actualizar: ${e.message}`); }
}

async function borrarComentario(id) {
  if (!confirm("¿Borrar este comentario? No se puede deshacer.")) return;
  try {
    await apiJson(`/api/comentarios/${id}`, "DELETE");
    await _refrescarComentarios();
  } catch (e) { alert(`No se pudo borrar: ${e.message}`); }
}

// "Responder": precarga el formulario visible con el autor como destinatario
// y la misma acta.
function responderComentario(id) {
  const c = (window._comentariosCache || {})[id];
  if (!c) return;
  const modal = document.getElementById("modal-comentarios");
  const prefijo = modal && modal.classList.contains("abierto") ? "mc" : "fc";
  const para = document.getElementById(`${prefijo}-para`);
  const acta = document.getElementById(`${prefijo}-acta`);
  const texto = document.getElementById(`${prefijo}-texto`);
  if (!texto) return;
  if (para && _yo && c.autor !== _yo.usuario) para.value = c.autor;
  if (acta && c.acta && acta.type !== "hidden") acta.value = c.acta;
  texto.focus();
  texto.scrollIntoView({ behavior: "smooth", block: "center" });
}

function _asegurarModalComentarios() {
  if (document.getElementById("modal-comentarios")) return;
  const div = document.createElement("div");
  div.id = "modal-comentarios";
  div.className = "modal-fondo";
  div.innerHTML = `
    <div class="modal-caja modal-coment" onclick="event.stopPropagation()">
      <button type="button" class="modal-cerrar" onclick="cerrarModalComentarios()">&times;</button>
      <div id="modal-comentarios-contenido"></div>
    </div>
  `;
  div.addEventListener("click", cerrarModalComentarios);
  document.body.appendChild(div);
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") cerrarModalComentarios(); });
}

function cerrarModalComentarios() {
  const modal = document.getElementById("modal-comentarios");
  if (modal) modal.classList.remove("abierto");
}

async function abrirComentariosActa(acta) {
  _asegurarModalComentarios();
  const modal = document.getElementById("modal-comentarios");
  modal.dataset.acta = acta;
  document.getElementById("modal-comentarios-contenido").innerHTML = "Cargando…";
  modal.classList.add("abierto");
  await quienSoy();
  await _cargarModalComentarios(acta, true);
}

async function _cargarModalComentarios(acta, conForm = false) {
  const cont = document.getElementById("modal-comentarios-contenido");
  let lista;
  try {
    lista = await api(`/api/comentarios?acta=${encodeURIComponent(acta)}&estado=todos`);
  } catch (e) {
    cont.innerHTML = `No se pudieron cargar los comentarios (${e.message}).`;
    return;
  }
  // Actualiza el globito de la fila sin recargar la tabla entera.
  window._comentariosPorActa[acta] = { total: lista.length, abiertos: lista.filter(c => !c.resuelto).length };
  document.querySelectorAll(`[data-acta-coment="${_idSeguro(acta)}"]`).forEach(pintarBotonComentarios);

  const htmlLista = lista.length
    ? lista.map(c => htmlComentario(c, { mostrarActa: false })).join("")
    : '<p class="com-vacio">Todavía no hay comentarios sobre esta marca.</p>';
  if (conForm || !document.getElementById("mc-lista")) {
    const marca = lista.find(c => c.denominacion_inpi || c.denominacion);
    cont.innerHTML = `
      <h3 class="titulo-coment">💬 Chat — acta ${_escapeHtml(acta)}${marca ? ` · ${_escapeHtml(marca.denominacion_inpi || marca.denominacion)}` : ""}</h3>
      ${htmlFormComentario({ acta, conActa: false, idPrefijo: "mc" })}
      <div id="mc-lista">${htmlLista}</div>
    `;
  } else {
    document.getElementById("mc-lista").innerHTML = htmlLista;
  }
}

// Contador de comentarios nuevos en el ícono de chat del encabezado
// (cualquier elemento con id="link-comentarios").
async function actualizarContadorComentarios() {
  // Si está menu.js (todas las pantallas del panel), él maneja el ícono, el número y el cartelito.
  if (typeof window.refrescarChat === "function") return window.refrescarChat();
  const link = document.getElementById("link-comentarios");
  if (!link) return;
  try {
    const r = await api("/api/comentarios/resumen");
    // El link del encabezado trae su propio globito (.nav-badge); se llena
    // solo con el número, para no pisar el ícono ni el texto del link.
    const badge = link.querySelector(".nav-badge");
    if (!badge) return;
    badge.textContent = r.nuevos ? String(r.nuevos) : "";
    badge.title = r.nuevos ? `${r.nuevos} nuevo(s) desde la última vez que entraste${r.abiertos_para_mi ? ` · ${r.abiertos_para_mi} sin resolver dirigido(s) a vos` : ""}` : "";
  } catch (_) {}
}
document.addEventListener("DOMContentLoaded", actualizarContadorComentarios);

/* Al abrir la ficha de un titular desde una lista (panel de leads), se guarda el
 * orden de los titulares que se veían para que la ficha ofrezca «Anterior /
 * Siguiente» y se pueda recorrer la cola sin volver a la lista (ver titular.html).
 * Vive en sessionStorage: solo en esta pestaña del navegador. */
document.addEventListener("click", (ev) => {
  const a = ev.target.closest && ev.target.closest('a.link-titular[href^="/titular/"]');
  if (!a || location.pathname.startsWith("/titular/")) return;
  const links = [...new Set([...document.querySelectorAll('a.link-titular[href^="/titular/"]')].map(x => x.getAttribute("href")))];
  try { sessionStorage.setItem("colaTitulares", JSON.stringify({ links, desde: location.pathname + location.search })); } catch (_) { /* sin cola: no es crítico */ }
});

// Vista previa del PDF de un presupuesto (plantillas de Mails con PDF adjunto): se pide al
// servidor con los datos que haya en pantalla y se abre en una pestaña nueva. La pestaña se
// abre antes de esperar la respuesta para que el navegador no la bloquee como ventana emergente.
async function abrirPdfPresupuesto(clave, presupuesto) {
  const w = window.open("about:blank", "_blank");
  try {
    const r = await fetch(`/api/mails/${encodeURIComponent(clave)}/presupuesto/pdf`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ presupuesto: presupuesto || {} }),
    });
    if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
    if (!r.ok) {
      let detalle = `${r.status}`;
      try { detalle = (await r.json()).detail || detalle; } catch (_) {}
      throw new Error(detalle);
    }
    const url = URL.createObjectURL(await r.blob());
    if (w) w.location.href = url; else window.open(url, "_blank");
  } catch (e) {
    if (w) w.close();
    throw e;
  }
}
