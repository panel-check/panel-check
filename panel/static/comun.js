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
  if (row.tuvo_oposicion === true) {
    const detalle = row.detalle_oposicion || "se detectó una oposición/vista en Grilla Digital";
    return `<span class="tooltip badge-oposicion">⚠ OPOSICIÓN<span class="globo">${detalle}</span></span>`;
  }
  return "";
}

function fmtFecha(f) {
  if (!f) return "";
  return f.split("T")[0].split(" ")[0];
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
