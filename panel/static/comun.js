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
    return `<span class="tooltip badge-oposicion">⚠ OPOSICIÓN<span class="globo">${detalle || "oposición de un tercero detectada en Grilla Digital"}</span></span>`;
  }
  return `<span class="tooltip badge-vista">👁 VISTA DE INPI<span class="globo">${detalle || "observación de oficio de INPI detectada en Grilla Digital"}</span></span>`;
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
