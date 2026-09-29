/* Funciones compartidas entre el dashboard (index.html) y la vista de
 * detalle por titular (titular.html). Nada de estado global de página
 * acá — cada página mantiene su propio `estado`/`cargar()`. */

async function api(path, opciones = {}) {
  const r = await fetch(path, { credentials: "include", ...opciones });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
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

function badgeLead(row) {
  if (row.es_lead === true) return '<span class="badge lead">LEAD</span>';
  if (row.es_lead === false) return `<span class="badge no-lead">${row.caracter || "con agente"}</span>`;
  return '<span class="badge sin-dato">sin verificar</span>';
}

// Cache de filas con oposición de tercero, clave = acta — el popup
// (abrirModalOposicion) lee de acá en vez de recibir el objeto entero por
// el onclick (que solo puede llevar strings simples). Se va completando
// cada vez que se renderiza una fila con badgeOposicion.
window._filasOposicion = window._filasOposicion || {};

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
    return `<button type="button" class="badge-oposicion" onclick="abrirModalOposicion('${row.acta}')">⚠ Ver oposición</button>`;
  }
  return `<span class="tooltip badge-vista">👁 VISTA DE INPI<span class="globo">${detalle || "observación de oficio de INPI detectada en Grilla Digital"}</span></span>`;
}

function _escapeHtml(s) {
  return (s ?? "").toString()
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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

function abrirModalOposicion(acta) {
  const row = window._filasOposicion[acta];
  if (!row) return;
  _asegurarModalOposicion();

  const doc = row.oponente_tipo_doc && row.oponente_numero_doc
    ? `${row.oponente_tipo_doc} ${row.oponente_numero_doc}` : "";
  const cuit = row.oponente_cuit ? `CUIT ${row.oponente_cuit}` : "";
  const identificacion = [doc, cuit].filter(Boolean).join(" · ");

  const contenido = document.getElementById("modal-oposicion-contenido");
  contenido.innerHTML = `
    <h3>⚠ Oposición — acta ${_escapeHtml(row.acta)}</h3>
    ${row.oponente_nombre ? `<p class="mo-oponente"><strong>${_escapeHtml(row.oponente_nombre)}</strong>${identificacion ? ` <span class="mo-doc">(${_escapeHtml(identificacion)})</span>` : ""}</p>` : ""}
    ${row.fundamento_oposicion
      ? `<p class="mo-fundamento">${_escapeHtml(row.fundamento_oposicion)}</p>`
      : `<p class="mo-fundamento">${_escapeHtml(row.detalle_oposicion) || "Sin más detalle disponible."}</p>`}
    <div id="mo-marca-oponente">Buscando la marca del oponente…</div>
  `;
  document.getElementById("modal-oposicion").classList.add("abierto");
  _renderMarcaOponente(row);
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
      actas.map(a => `<a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${a}')">Ver ficha del acta ${a} ↗</a>`).join(" ");
    return;
  }

  // Solo tenemos denominación + número de registro citados en el
  // fundamento (sin ACTA propia) — hay que buscarla. Mejor esfuerzo: no
  // siempre encuentra el registro exacto (el nombre en el texto legal
  // puede tener espaciado distinto al cargado en INPI), así que mostramos
  // la lista completa de resultados y que la persona elija.
  if (row.marca_oponente_denominacion) {
    cont.innerHTML = `<button type="button" onclick="_buscarMarcaOponente('${_escapeHtml(row.marca_oponente_denominacion)}', '${_escapeHtml(row.marca_oponente_numero_registro || "")}')">
      🔍 Buscar "${_escapeHtml(row.marca_oponente_denominacion)}" en INPI
    </button>`;
    return;
  }

  cont.innerHTML = "";
}

async function _buscarMarcaOponente(denominacion, numeroBuscado) {
  const cont = document.getElementById("mo-marca-oponente");
  cont.innerHTML = "Buscando…";
  try {
    const data = await api(`/api/marcas/buscar-marca?denominacion=${encodeURIComponent(denominacion)}`);
    const filas = data.resultados || [];
    if (!filas.length) {
      cont.innerHTML = `No se encontró ninguna marca con "${_escapeHtml(denominacion)}" en INPI (puede que esté cargada con otro espaciado o redacción).`;
      return;
    }
    cont.innerHTML = `<div class="mo-titulo">Resultados para "${_escapeHtml(denominacion)}":</div>` +
      filas.map(f => {
        const coincide = numeroBuscado && f.numero_resolucion && f.numero_resolucion.replace(/\D/g, "") === numeroBuscado.replace(/\D/g, "");
        return `<div class="mo-resultado ${coincide ? "mo-coincide" : ""}">
          ${_escapeHtml(f.denominacion)} — clase ${_escapeHtml(f.clase)}${f.numero_resolucion ? ` · Reg. ${_escapeHtml(f.numero_resolucion)}` : ""}
          <a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${f.acta}')">Ver ficha ↗</a>
        </div>`;
      }).join("");
  } catch (e) {
    cont.innerHTML = `No se pudo buscar (${e.message}).`;
  }
}

function fmtFecha(f) {
  if (!f) return "";
  return f.split("T")[0].split(" ")[0];
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
    const detalle = `Concedida el ${fmtFecha(row.fecha_concesion) || "?"}`
      + (row.fecha_vencimiento_marca ? ` · vence ${fmtFecha(row.fecha_vencimiento_marca)}` : "");
    return `<span class="tooltip badge-concedida">✓ CONCEDIDA<span class="globo">${detalle}</span></span>`;
  }
  if (row.estado_tramite === "Denegada") {
    return '<span class="badge no-lead">DENEGADA</span>';
  }
  return `<span class="badge sin-dato">${row.estado_tramite}</span>`;
}

function fechaPublicacionOFallback(r) {
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
  if (!clave) return r.titular || "";
  return `<a class="link-titular" href="/titular/${encodeURIComponent(clave)}">${r.titular || clave}</a>`;
}

function celdaEmail(r) {
  if (r.email) {
    return `<a href="mailto:${r.email}">${r.email}</a>`;
  }
  if (r.es_lead !== true) {
    return ""; // no aplica (tiene apoderado) o todavía no se verificó
  }
  const motivo = r.motivo_sin_email || "no se pudo determinar el motivo";
  return `
    <span class="sin-email">
      <span class="tooltip">sin email<span class="globo">${motivo}</span></span>
      <button class="reintentar" onclick="reintentarEmail('${r.acta}', this)">Reintentar</button>
      <a class="link-acta" href="javascript:void(0)" onclick="abrirActa('${r.acta}')" title="Ver expediente completo en INPI">Ver ficha ↗</a>
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
      <span class="icono-email no tooltip">&#10007;<span class="globo">${motivo}</span></span>
      <button class="reintentar" onclick="reintentarEmail('${r.acta}', this)">Reintentar</button>
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

async function marcarContactado(acta, valorActual) {
  const nuevo = !valorActual;
  await api(`/api/marcas/${encodeURIComponent(acta)}/contactado?valor=${nuevo}`, { method: "POST" });
  if (typeof window.alRecargar === "function") await window.alRecargar();
}
