/* Página /informes/{id}: abre un informe de la ficha del cliente (solapa Informes)
 * en su propia dirección. El del cliente se ve como PDF; el interno de la IA, como página. */

(function () {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const cont = $("informe");
  const iid = (location.pathname.match(/\/informes\/(\d+)/) || [])[1];

  const texto = v => v == null ? "" : Array.isArray(v) ? v.map(texto).filter(Boolean).join(" · ")
    : typeof v === "object" ? Object.entries(v).map(([k, x]) => `${k}: ${texto(x)}`).join(" · ")
    : typeof v === "boolean" ? (v ? "sí" : "no") : String(v);
  const etiqueta = k => k.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
  const lista = arr => arr && arr.length ? `<ul>${arr.map(x => `<li>${esc(texto(x))}</li>`).join("")}</ul>` : '<p class="crm-gris" style="margin:0">—</p>';
  const caja = (titulo, html) => html ? `<div class="inf-caja"><h3>${esc(titulo)}</h3>${html}</div>` : "";
  const dl = obj => {
    const filas = Object.entries(obj || {}).filter(([, v]) => v !== "" && v != null && !(Array.isArray(v) && !v.length));
    return filas.length ? `<dl class="inf-grid">${filas.map(([k, v]) => `<dt>${esc(etiqueta(k))}</dt><dd>${linkSi(v)}</dd>`).join("")}</dl>` : "";
  };
  const linkSi = v => (typeof v === "string" && /^https?:\/\//.test(v)) ? `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a>` : esc(texto(v));

  function tabla(filas, { filtro = false, id = "" } = {}) {
    if (!Array.isArray(filas) || !filas.length) return "";
    const cols = [...new Set(filas.flatMap(f => (f && typeof f === "object") ? Object.keys(f) : []))];
    const celda = (k, v) => k === "acta" && v ? `<a class="link-acta" href="javascript:void(0)" data-acta="${esc(v)}">${esc(v)} ↗</a>` : esc(texto(v));
    return `${filtro ? `<input class="inf-filtro" data-filtrar="${id}" placeholder="Filtrar (marca, titular, clase, acta…)">` : ""}
      <div style="overflow-x:auto"><table class="cl-tabla" id="${id}"><thead><tr>${cols.map(c => `<th>${esc(etiqueta(c))}</th>`).join("")}</tr></thead>
      <tbody>${filas.map(f => `<tr>${cols.map(c => `<td>${celda(c, f[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  // Antecedentes como tarjetas (no tabla): los textos largos se leen enteros sin deslizar.
  function fichas(filas) {
    if (!Array.isArray(filas) || !filas.length) return "";
    const cabecera = new Set(["acta", "denominacion", "clase", "estado", "tipo"]);
    const largos = ["por_que_es_relevante", "productos_servicios", "limitacion"];
    const varias = filas.length > 1;
    return `<div class="inf-plegar"><button type="button" class="cl-btn sec mini" data-plegar="abrir">${varias ? "Abrir todos" : "Abrir"}</button>
      <button type="button" class="cl-btn sec mini" data-plegar="cerrar">${varias ? "Cerrar todos" : "Cerrar"}</button></div>
      <div class="inf-fichas">${filas.map(f => {
      if (!f || typeof f !== "object") return `<div class="inf-ficha">${esc(texto(f))}</div>`;
      const chips = [f.clase != null && f.clase !== "" ? `Clase ${f.clase}` : "", f.estado, f.tipo].filter(Boolean);
      const cortos = Object.entries(f).filter(([k, v]) => !cabecera.has(k) && !largos.includes(k) && v !== "" && v != null
        && !(typeof v === "string" && v.length > 90));
      const extensos = [...largos.filter(k => f[k]).map(k => [k, f[k]]),
        ...Object.entries(f).filter(([k, v]) => !cabecera.has(k) && !largos.includes(k) && typeof v === "string" && v.length > 90)];
      // Cerrada por defecto: en la línea se ve la marca, la clase, el estado y el titular; al hacer click se abre el detalle.
      return `<details class="inf-ficha">
        <summary class="inf-ficha-cab"><span class="inf-flecha">▸</span>${f.acta ? `<a class="link-acta" href="javascript:void(0)" data-acta="${esc(f.acta)}">Acta ${esc(f.acta)} ↗</a>` : ""}
          <strong>${esc(f.denominacion || "")}</strong>${chips.map(c => `<span class="cl-tag">${esc(c)}</span>`).join("")}
          ${f.titular ? `<span class="crm-gris inf-titular">${esc(texto(f.titular))}</span>` : ""}
          <span class="cl-btn sec mini inf-ver" aria-hidden="true">Ver detalle ▾</span></summary>
        <div class="inf-ficha-cuerpo">
        ${cortos.length ? `<dl class="inf-grid">${cortos.map(([k, v]) => `<dt>${esc(etiqueta(k))}</dt><dd>${esc(texto(v))}</dd>`).join("")}</dl>` : ""}
        ${extensos.map(([k, v]) => `<div class="inf-largo${k === "por_que_es_relevante" ? " inf-destacado" : ""}"><span>${esc(k === "por_que_es_relevante" ? "Por qué es relevante" : etiqueta(k))}</span>${esc(texto(v))}</div>`).join("")}
        </div>
      </details>`;
    }).join("")}</div>`;
  }

  function barra(r, extra) {
    const titulo = r.tipo === "cliente" ? "Informe preliminar de viabilidad" : "Informe IA interno";
    document.title = `${titulo} — ${r.marca || ""}`;
    return `<div class="inf-barra">
      <div><h2>${esc(titulo)} · ${esc(r.marca || "")}</h2>
        <div class="crm-gris">${r.cliente_id ? `Cliente: <a href="/clientes?cliente=${r.cliente_id}">${esc(r.cliente_nombre || "#" + r.cliente_id)}</a> · ` : ""}cargado ${esc(fmtFechaHora(r.generado_en))}${r.generado_por ? " por " + esc(r.generado_por) : ""}</div></div>
      <div class="der">${extra}<a class="cl-btn sec" href="/api/informes/${r.id}/json">Descargar JSON</a></div></div>`;
  }

  function interno(r) {
    const d = r.datos || {}, b = d.busqueda_inpi || {};
    const listas = Object.entries(b).filter(([k, v]) => k.startsWith("antecedentes_") && k !== "antecedentes_relevantes" && Array.isArray(v));
    const totalListados = listas.reduce((n, [, v]) => n + v.length, 0);
    const kpis = [[(b.identicas || []).length, "marcas idénticas"], [(b.antecedentes_relevantes || []).length, "antecedentes relevantes"],
      [totalListados, "antecedentes listados"], [(d.caso || {}).fecha_consulta_inpi || "—", "consulta a INPI"]];
    const conocidas = new Set(["tipo", "version", "estado", "caso", "cliente", "actividad", "clases", "busqueda_inpi", "limitaciones",
      "datos_faltantes", "observaciones_para_pame"]);
    const otras = Object.entries(d).filter(([k]) => !conocidas.has(k));
    const cli = d.cliente || {};
    const clases = d.clases || {};
    const filasClases = [...(clases.principal || []).map(c => ({ tipo: "principal", ...c })), ...(clases.complementarias || []).map(c => ({ tipo: "complementaria", ...c }))];
    const porClase = Object.entries((b.resumen || {}).por_clase || {}).map(([clase, v]) => ({ clase, ...v }));
    const restoResumen = Object.fromEntries(Object.entries(b.resumen || {}).filter(([k]) => k !== "por_clase"));
    cont.innerHTML = barra(r, "") + `
      <div class="inf-kpis">${kpis.map(([n, t]) => `<div class="inf-kpi"><b>${esc(n)}</b><span>${esc(t)}</span></div>`).join("")}</div>
      ${caja("Para revisar primero", d.observaciones_para_pame ? `<p class="inf-obs">${esc(d.observaciones_para_pame)}</p>` : "")}
      ${caja("Antecedentes relevantes", fichas(b.antecedentes_relevantes))}
      ${caja("Marcas idénticas", (b.identicas || []).length ? fichas(b.identicas) : '<p class="crm-gris" style="margin:0">No se encontraron marcas idénticas vigentes o en trámite.</p>')}
      ${caja("Caso", dl(d.caso))}
      ${caja("Cliente", dl(Object.fromEntries(Object.entries(cli).filter(([k]) => k !== "datos_de_la_web"))) + (cli.datos_de_la_web ? `<h3 style="margin-top:12px">Datos de la web</h3>${dl(cli.datos_de_la_web)}` : ""))}
      ${caja("Actividad", d.actividad ? `${d.actividad.descripcion_formulario ? `<p style="margin:0 0 8px;font-size:13px">Formulario: «${esc(d.actividad.descripcion_formulario)}»</p>` : ""}
        <h3>Actuales</h3>${lista(d.actividad.actuales)}<h3 style="margin-top:10px">Proyectadas</h3>${lista(d.actividad.proyectadas)}
        ${(d.actividad.categorias_catalogo || []).length ? `<h3 style="margin-top:10px">Catálogo</h3>${lista(d.actividad.categorias_catalogo)}` : ""}
        ${(d.actividad.productos_complementarios || []).length ? `<h3 style="margin-top:10px">Productos complementarios</h3>${lista(d.actividad.productos_complementarios)}` : ""}` : "")}
      ${caja("Clases (uso interno)", tabla(filasClases, { id: "t-clases" }))}
      ${caja("Búsqueda en INPI", `${b.fuente ? `<p class="crm-gris" style="margin:0 0 8px">${esc(b.fuente)}</p>` : ""}${b.criterio_activas ? `<p style="margin:0 0 8px;font-size:13px">${esc(b.criterio_activas)}</p>` : ""}
        ${tabla(b.terminos, { id: "t-term" })}${dl(restoResumen) ? `<div style="margin-top:10px">${dl(restoResumen)}</div>` : ""}${porClase.length ? `<div style="margin-top:10px">${tabla(porClase, { id: "t-porclase" })}</div>` : ""}`)}
      ${listas.map(([k, v], i) => caja(`${etiqueta(k)} (${v.length})`, tabla(v, { filtro: v.length > 10, id: "t-lista" + i }))).join("")}
      ${caja("Descartados (inactivos)", (b.descartados_inactivos || []).length ? lista(b.descartados_inactivos) : "")}
      ${caja("Limitaciones de la búsqueda", (d.limitaciones || []).length ? lista(d.limitaciones) : "")}
      ${caja("Datos faltantes", (d.datos_faltantes || []).length ? lista(d.datos_faltantes) : "")}
      ${otras.map(([k, v]) => caja(etiqueta(k), Array.isArray(v) && v.length && typeof v[0] === "object" ? tabla(v) : typeof v === "object" ? dl(v) : `<p style="margin:0">${esc(texto(v))}</p>`)).join("")}`;
    // El link del acta está dentro del renglón que abre/cierra la ficha: que abra INPI sin plegar la ficha.
    cont.querySelectorAll("[data-acta]").forEach(a => a.addEventListener("click", ev => { ev.preventDefault(); ev.stopPropagation(); abrirActa(a.dataset.acta); }));
    cont.querySelectorAll("details.inf-ficha").forEach(d => d.addEventListener("toggle", () => {
      const v = d.querySelector(".inf-ver"); if (v) v.textContent = d.open ? "Ocultar detalle ▴" : "Ver detalle ▾";
    }));
    cont.querySelectorAll("[data-plegar]").forEach(b => b.addEventListener("click", () => {
      b.closest(".inf-caja").querySelectorAll("details.inf-ficha").forEach(d => { d.open = b.dataset.plegar === "abrir"; });
    }));
    cont.querySelectorAll("[data-filtrar]").forEach(inp => inp.addEventListener("input", () => {
      const q = inp.value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
      $(inp.dataset.filtrar).querySelectorAll("tbody tr").forEach(tr => {
        tr.hidden = q && !tr.textContent.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().includes(q);
      });
    }));
  }

  (async function () {
    if (!iid) { cont.innerHTML = '<p class="crm-error">Falta el número de informe.</p>'; return; }
    let r;
    try { r = await api(`/api/informes/${iid}`); }
    catch (e) { cont.innerHTML = `<p class="crm-error">No se pudo abrir el informe (${esc(e.message)}).</p>`; return; }
    if (r.tipo === "cliente") {
      cont.innerHTML = barra(r, `<a class="cl-btn" href="/api/informes/${r.id}/pdf">Descargar PDF</a>`) +
        `<iframe class="inf-pdf" src="/api/informes/${r.id}/pdf?ver=1" title="Informe en PDF"></iframe>`;
    } else {
      interno(r);
    }
  })();
})();
