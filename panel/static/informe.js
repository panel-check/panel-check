/* Página /informes/{id}: abre un informe en su propia dirección. El del cliente se ve como PDF;
 * el interno de la IA, el Informe Oposición IA (skill revisador-de-oposiciones) y el Informe Vista IA
 * (skill respondervistas), como página. */

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
    const fmt2 = r.tipo === "cliente" && r.datos && r.datos.formato === 2;
    const ref = fmt2 ? (r.datos.encabezado || {}).referencia : ((r.datos || {}).caso || {}).referencia;
    const titulo = r.tipo === "cliente" ? "Informe preliminar de viabilidad"
      : r.tipo === "oposicion" ? "Informe Oposición IA" : r.tipo === "vista" ? "Informe Vista IA" : "Informe IA interno";
    document.title = `${titulo} — ${r.marca || ""}`;
    return `<div class="inf-barra">
      <div><h2>${esc(titulo)} · ${esc(r.marca || "")}${ref && /^INFPR-\d+$/i.test(ref) ? ` · <span class="cl-tag">${esc(ref)}</span>` : ""}</h2>
        <div class="crm-gris">${r.cliente_id ? `Cliente: <a href="/clientes?cliente=${r.cliente_id}&ctab=informes">${esc(r.cliente_nombre || "#" + r.cliente_id)}</a> · `
          : r.lead_clave ? `Lead: <a href="/titular/${encodeURIComponent(r.lead_clave)}#informes">${esc(r.lead_nombre || r.lead_clave)}</a> · ` : ""}cargado ${esc(fmtFechaHora(r.generado_en))}${r.generado_por ? " por " + esc(r.generado_por) : ""}</div></div>
      <div class="der">${r.compartir ? '<button type="button" class="cl-btn sec" id="inf-compartir" title="Copia el link con la vista previa para WhatsApp (marca y de quién es); el informe sigue pidiendo iniciar sesión">🔗 Copiar link para compartir</button>' : ""}${extra}<a class="cl-btn sec" href="/api/informes/${r.id}/json">Descargar JSON</a></div></div>`;
  }

  function interno(r) {
    const d = r.datos || {}, b = d.busqueda_inpi || {};
    // Formato 2: coincidencias exactas aparte, las tres consultas del procedimiento y la búsqueda amplia agrupada.
    const amplia = b.busqueda_amplia && typeof b.busqueda_amplia === "object" ? b.busqueda_amplia : {};
    const exactas = b.coincidencias_exactas || b.identicas || [];
    const listas = [...Object.entries(b), ...Object.entries(amplia)].filter(([k, v]) => k.startsWith("antecedentes_") && k !== "antecedentes_relevantes" && Array.isArray(v));
    const consultas = (b.consultas_procedimiento || []).map(c => { const { filas, ...resto } = c || {}; return { ...resto, filas: Array.isArray(filas) ? filas.length : filas }; });
    const legal = d.analisis_legal_ley_22362 || null;
    const resumenB = amplia.resumen || b.resumen || {};
    const terminos = amplia.terminos || b.terminos;
    const totalListados = listas.reduce((n, [, v]) => n + v.length, 0);
    const kpis = [[exactas.length, b.coincidencias_exactas ? "coincidencias exactas" : "marcas idénticas"], [(b.antecedentes_relevantes || []).length, "antecedentes relevantes"],
      [totalListados, "antecedentes listados"], [(d.caso || {}).fecha_consulta_inpi || "—", "consulta a INPI"]];
    const conocidas = new Set(["tipo", "version", "estado", "caso", "cliente", "actividad", "clases", "busqueda_inpi", "limitaciones",
      "datos_faltantes", "observaciones_para_pame", "analisis_legal_ley_22362"]);
    const otras = Object.entries(d).filter(([k]) => !conocidas.has(k));
    const cli = d.cliente || {};
    const clases = d.clases || {};
    const filasClases = [...(clases.principal || []).map(c => ({ tipo: "principal", ...c })), ...(clases.complementarias || []).map(c => ({ tipo: "complementaria", ...c }))];
    const porClase = Object.entries(resumenB.por_clase || {}).map(([clase, v]) => ({ clase, ...v }));
    const restoResumen = Object.fromEntries(Object.entries(resumenB).filter(([k]) => k !== "por_clase"));
    cont.innerHTML = barra(r, "") + `
      <div class="inf-kpis">${kpis.map(([n, t]) => `<div class="inf-kpi"><b>${esc(n)}</b><span>${esc(t)}</span></div>`).join("")}</div>
      ${caja("Para revisar primero", d.observaciones_para_pame ? `<p class="inf-obs">${esc(d.observaciones_para_pame)}</p>` : "")}
      ${caja("Antecedentes relevantes", fichas(b.antecedentes_relevantes))}
      ${caja(b.coincidencias_exactas ? "Coincidencias exactas" : "Marcas idénticas", exactas.length ? fichas(exactas) : '<p class="crm-gris" style="margin:0">No se encontraron marcas idénticas vigentes o en trámite.</p>')}
      ${caja("Consultas del procedimiento (INPI)", consultas.length ? tabla(consultas, { id: "t-consultas" }) + (b.control_solo_vigentes ? `<div style="margin-top:10px"><h3>Control de «Solo vigentes»</h3>${dl(b.control_solo_vigentes)}</div>` : "") : "")}
      ${caja("Revisión legal (Ley 22.362)", legal ? `${dl(Object.fromEntries(Object.entries(legal).filter(([k]) => !["evaluacion_por_articulo", "recomendaciones_para_evitar_vistas_y_oposiciones"].includes(k))))}
        ${tabla(legal.evaluacion_por_articulo, { id: "t-legal" })}
        ${(legal.recomendaciones_para_evitar_vistas_y_oposiciones || []).length ? `<h3 style="margin-top:10px">Recomendaciones</h3>${lista(legal.recomendaciones_para_evitar_vistas_y_oposiciones)}` : ""}` : "")}
      ${caja("Caso", dl(d.caso))}
      ${caja("Cliente", dl(Object.fromEntries(Object.entries(cli).filter(([k]) => k !== "datos_de_la_web"))) + (cli.datos_de_la_web ? `<h3 style="margin-top:12px">Datos de la web</h3>${dl(cli.datos_de_la_web)}` : ""))}
      ${caja("Actividad", d.actividad ? `${d.actividad.descripcion_formulario ? `<p style="margin:0 0 8px;font-size:13px">Formulario: «${esc(d.actividad.descripcion_formulario)}»</p>` : ""}
        <h3>Actuales</h3>${lista(d.actividad.actuales)}<h3 style="margin-top:10px">Proyectadas</h3>${lista(d.actividad.proyectadas)}
        ${(d.actividad.categorias_catalogo || []).length ? `<h3 style="margin-top:10px">Catálogo</h3>${lista(d.actividad.categorias_catalogo)}` : ""}
        ${(d.actividad.productos_complementarios || []).length ? `<h3 style="margin-top:10px">Productos complementarios</h3>${lista(d.actividad.productos_complementarios)}` : ""}
        ${(d.actividad.productos_de_terceros || []).length ? `<h3 style="margin-top:10px">Productos de terceros</h3>${lista(d.actividad.productos_de_terceros)}` : ""}
        ${(d.actividad.productos_marca_propia || []).length ? `<h3 style="margin-top:10px">Productos con marca propia</h3>${lista(d.actividad.productos_marca_propia)}` : ""}` : "")}
      ${caja("Clases (uso interno)", tabla(filasClases, { id: "t-clases" }))}
      ${caja("Búsqueda en INPI", `${b.fuente ? `<p class="crm-gris" style="margin:0 0 8px">${esc(b.fuente)}</p>` : ""}${b.criterio_activas ? `<p style="margin:0 0 8px;font-size:13px">${esc(b.criterio_activas)}</p>` : ""}
        ${tabla(terminos, { id: "t-term" })}${dl(restoResumen) ? `<div style="margin-top:10px">${dl(restoResumen)}</div>` : ""}${porClase.length ? `<div style="margin-top:10px">${tabla(porClase, { id: "t-porclase" })}</div>` : ""}`)}
      ${listas.map(([k, v], i) => caja(`${etiqueta(k)} (${v.length})`, tabla(v, { filtro: v.length > 10, id: "t-lista" + i }))).join("")}
      ${caja(`Coincidencias parciales (${(b.coincidencias_parciales || []).length})`, (b.coincidencias_parciales || []).length ? tabla(b.coincidencias_parciales, { filtro: b.coincidencias_parciales.length > 10, id: "t-parciales" }) : "")}
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

  // Fechas clave de cada acta opuesta: solicitud, publicación en el boletín, cierre del plazo para recibir
  // oposiciones (publicación + 30 días corridos) y fin de los 3 meses para negociar. Usa «fechas_clave» si el
  // JSON lo trae; si no (informes cargados antes), las arma con marca_cliente.actas_opuestas y oposiciones.
  const aFecha = s => { const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(String(s || "").trim()); return m ? new Date(+m[3], +m[2] - 1, +m[1]) : null; };
  const deFecha = f => `${String(f.getDate()).padStart(2, "0")}/${String(f.getMonth() + 1).padStart(2, "0")}/${f.getFullYear()}`;
  const diasHasta = f => { const h = new Date(); return Math.round((f - new Date(h.getFullYear(), h.getMonth(), h.getDate())) / 864e5); };
  function fechasClave(d) {
    const mc = d.marca_cliente || {}, ops = (Array.isArray(d.oposiciones) ? d.oposiciones : []).filter(o => o && typeof o === "object");
    const actas = (Array.isArray(mc.actas_opuestas) ? mc.actas_opuestas : []).filter(a => a && typeof a === "object");
    const propias = (Array.isArray(d.fechas_clave) ? d.fechas_clave : []).filter(x => x && typeof x === "object");
    let claves = [...new Set([...propias.map(x => x.acta), ...actas.map(a => a.acta), ...ops.map(o => o.acta_cliente)]
      .map(a => String(a || "").trim()).filter(Boolean))];
    const unica = claves.length <= 1;
    if (!claves.length) claves = [""];
    const buscar = (lista, campo, acta) => lista.find(x => String(x[campo] || "").trim() === acta) || (unica ? lista[0] || {} : {});
    return claves.map(acta => {
      const p = buscar(propias, "acta", acta), a = buscar(actas, "acta", acta), o = buscar(ops, "acta_cliente", acta);
      const publicacion = p.fecha_publicacion || a.fecha_publicacion || "";
      let cierre = p.cierre_oposiciones || "";
      if (!cierre && aFecha(publicacion)) { const f = aFecha(publicacion); f.setDate(f.getDate() + 30); cierre = deFecha(f); }
      return {
        acta, clase: p.clase ?? a.clase ?? o.clase ?? "",
        solicitud: p.fecha_solicitud || a.fecha_presentacion || "", publicacion, boletin: p.boletin || a.boletin || "", cierre,
        vence: p.vencimiento_negociacion || o.vencimiento_negociacion || "", notificacion: p.fecha_notificacion || o.fecha_notificacion || "",
        estimado: p.vencimiento_estimado === true || (p.notificacion || o.notificacion) === "esperada",
      };
    }).filter(f => f.solicitud || f.publicacion || f.cierre || f.vence);
  }
  function cajaFechas(d) {
    const filas = fechasClave(d);
    if (!filas.length) return "";
    const tarjeta = (titulo, valor, nota, cls = "") => `<div class="op-fecha${cls ? " " + cls : ""}"><span>${esc(titulo)}</span><b>${esc(valor || "—")}</b>${nota ? `<small>${esc(nota)}</small>` : ""}</div>`;
    const falta = (txt, pasado) => {
      const f = aFecha(txt);
      if (!f) return { nota: "", cls: "" };
      const n = diasHasta(f);
      return n < 0 ? { nota: pasado, cls: "pasada" } : n === 0 ? { nota: "es hoy", cls: "urgente" }
        : { nota: `faltan ${n} ${n === 1 ? "día" : "días"}`, cls: n <= 15 ? "urgente" : "" };
    };
    return caja("📅 Fechas clave", filas.map(f => {
      const c = falta(f.cierre, "ya cerró"), v = falta(f.vence, "ya venció");
      const notaVence = f.vence
        ? [f.estimado ? "estimado: falta la notificación" : f.notificacion ? `notificada el ${f.notificacion}` : "", v.nota].filter(Boolean).join(" · ")
        : "falta la notificación del INPI";
      return `${filas.length > 1 ? `<div class="op-fechas-cab">Acta ${f.acta ? `<a class="link-acta" href="javascript:void(0)" data-acta="${esc(f.acta)}">${esc(f.acta)} ↗</a>` : "—"}${f.clase !== "" ? ` <span class="cl-tag">Clase ${esc(f.clase)}</span>` : ""}</div>` : ""}
        <div class="op-fechas">
          ${tarjeta("Se solicitó la marca", f.solicitud, "")}
          ${tarjeta("Se publicó en el boletín", f.publicacion, f.boletin ? `Boletín ${f.boletin}` : "")}
          ${tarjeta("Cierra el plazo de oposiciones", f.cierre, ["30 días corridos desde la publicación", c.nota].filter(Boolean).join(" · "), c.cls)}
          ${tarjeta("Vencen los 3 meses para negociar", f.vence, notaVence, f.vence ? (v.cls || "clave") : "")}
        </div>`;
    }).join(""));
  }

  // Informe Oposición IA: ficha ⚡ arriba, después oposiciones, marcas, comparación, palancas y caminos.
  function oposicion(r) {
    const d = r.datos || {}, rx = d.resumen_express || {}, mc = d.marca_cliente || {}, cmp = d.comparacion || {};
    const ops = Array.isArray(d.oposiciones) ? d.oposiciones : [];
    const ch = typeof d.chances === "string" ? { nivel: d.chances } : (d.chances || {});
    const colorCh = /^(muy bajas|bajas)$/.test(ch.nivel || "") ? "mal" : /^(bajas a medias|medias)$/.test(ch.nivel || "") ? "aviso" : ch.nivel ? "ok" : "";
    const vtos = ops.map(o => o.vencimiento_negociacion).filter(Boolean);
    const oponentes = [...new Set(ops.map(o => o.oponente).filter(Boolean))];
    const RX = [["cliente_marca", "Cliente / marca"], ["oponente", "Oponente"], ["que_cita", "Qué cita"], ["plazos", "Plazos"],
      ["chances", "Chances"], ["ojo", "Ojo"], ["camino_sugerido", "Camino sugerido"], ["falta", "Falta"]];
    const fichaRx = RX.filter(([k]) => rx[k]).map(([k, t]) => `<div class="op-rx-fila${k === "ojo" ? " ojo" : ""}"><dt>${esc(t)}</dt><dd>${esc(texto(rx[k]))}</dd></div>`).join("")
      + Object.entries(rx).filter(([k, v]) => !RX.some(([c]) => c === k) && v).map(([k, v]) => `<div class="op-rx-fila"><dt>${esc(etiqueta(k))}</dt><dd>${esc(texto(v))}</dd></div>`).join("");
    const actaLink = a => a ? `<a class="link-acta" href="javascript:void(0)" data-acta="${esc(a)}">${esc(a)} ↗</a>` : "";
    const sit = v => { const t = String(v || "").toLowerCase(); return t.startsWith("id") ? "mal" : t.startsWith("inc") ? "aviso" : t.startsWith("af") ? "ok" : ""; };
    const sino = v => v === true ? "sí" : v === false ? "no" : esc(texto(v));
    const conocidas = new Set(["tipo", "version", "fecha_analisis", "fechas_clave", "resumen_express", "resumen", "marca_cliente", "oposiciones", "marcas_oponente",
      "comparacion", "palancas", "chances", "caminos", "preguntas_cliente", "a_confirmar_pame", "limitaciones"]);
    const otras = Object.entries(d).filter(([k]) => !conocidas.has(k));
    const signos = cmp.signos && typeof cmp.signos === "object" ? cmp.signos : null;
    const conv = cmp.convivencia && typeof cmp.convivencia === "object" ? cmp.convivencia : (cmp.convivencia ? { resumen: cmp.convivencia } : null);
    cont.innerHTML = barra(r, "") + `
      <div class="inf-kpis">
        <div class="inf-kpi"><b class="op-ch ${colorCh}">${esc(ch.nivel || "—")}</b><span>chances</span></div>
        <div class="inf-kpi"><b>${ops.length}</b><span>${ops.length === 1 ? "oposición" : "oposiciones"}</span></div>
        <div class="inf-kpi"><b>${esc(vtos.length ? vtos.sort((a, b) => (a.split("/").reverse().join("")).localeCompare(b.split("/").reverse().join("")))[0] : "—")}</b><span>vencen los 3 meses para negociar</span></div>
        <div class="inf-kpi" style="flex:1"><b style="font-size:15px">${esc(oponentes.join(", ") || "—")}</b><span>oponente</span></div>
      </div>
      ${cajaFechas(d)}
      ${caja("⚡ Resumen express", fichaRx ? `<dl class="op-rx">${fichaRx}</dl>` : "")}
      ${caja("Resumen", d.resumen ? `<p class="inf-obs">${esc(d.resumen)}</p>` : "")}
      ${caja("Marca del cliente", dl(Object.fromEntries(Object.entries(mc).filter(([k]) => !["actas_opuestas", "otras_actas"].includes(k))))
        + ((mc.actas_opuestas || []).length ? `<h3 style="margin-top:12px">Actas opuestas</h3>${tabla(mc.actas_opuestas, { id: "t-actas" })}` : "")
        + ((mc.otras_actas || []).length ? `<h3 style="margin-top:12px">Otras actas del titular</h3>${tabla(mc.otras_actas, { id: "t-otras" })}` : ""))}
      ${caja(ops.length === 1 ? "La oposición" : "Las oposiciones", ops.length ? `<div class="op-lista">${ops.map(o => `<div class="op-item">
          <div class="op-item-cab">Acta ${actaLink(o.acta_cliente) || "—"}${o.clase != null && o.clase !== "" ? ` <span class="cl-tag">Clase ${esc(o.clase)}</span>` : ""}
            · <strong>${esc(o.oponente || "")}</strong>${o.en_termino === false ? ' <span class="cl-tag mal">fuera de término</span>' : ""}</div>
          <dl class="inf-grid">
            ${o.fecha_oposicion ? `<dt>Fecha de la oposición</dt><dd>${esc(o.fecha_oposicion)}${o.en_termino === true ? " (en término)" : ""}</dd>` : ""}
            ${o.agente_oponente ? `<dt>Agente del oponente</dt><dd>${esc(o.agente_oponente)}</dd>` : ""}
            ${(o.marcas_citadas || []).length ? `<dt>Marcas citadas</dt><dd>${o.marcas_citadas.map(a => /^\d+$/.test(String(a)) ? actaLink(a) : esc(texto(a))).join(", ")}</dd>` : ""}
            ${(o.articulos || []).length ? `<dt>Artículos</dt><dd>${esc(texto(o.articulos))}</dd>` : ""}
            ${o.fundamento ? `<dt>Fundamento</dt><dd>${esc(o.fundamento)}</dd>` : ""}
            ${o.fecha_notificacion || o.notificacion ? `<dt>Notificación</dt><dd>${esc(o.fecha_notificacion || "—")}${o.notificacion ? ` (${esc(o.notificacion)})` : ""}</dd>` : ""}
            ${o.vencimiento_negociacion ? `<dt>Vencen los 3 meses</dt><dd><strong>${esc(o.vencimiento_negociacion)}</strong></dd>` : ""}
            ${Object.entries(o).filter(([k, v]) => !["acta_cliente", "clase", "oponente", "en_termino", "fecha_oposicion", "agente_oponente", "marcas_citadas", "articulos", "fundamento", "fecha_notificacion", "notificacion", "vencimiento_negociacion"].includes(k) && v !== "" && v != null).map(([k, v]) => `<dt>${esc(etiqueta(k))}</dt><dd>${esc(texto(v))}</dd>`).join("")}
          </dl></div>`).join("")}</div>` : "")}
      ${caja("Marcas del oponente (verificadas en el INPI)", (d.marcas_oponente || []).length ? `<div class="inf-fichas">${d.marcas_oponente.map(m => {
          const avisos = [m.es_del_oponente === false ? '<span class="cl-tag mal">no es del oponente</span>' : "",
            m.anterior_al_cliente === false ? '<span class="cl-tag ok">posterior al cliente</span>' : "",
            /renov/i.test(m.origen || "") ? '<span class="cl-tag aviso">renovación</span>' : "",
            m.falta_de_uso_posible === true ? '<span class="cl-tag aviso">posible falta de uso</span>' : ""].join("");
          const resto = Object.fromEntries(Object.entries(m).filter(([k]) => !["acta", "denominacion", "clase", "es_del_oponente", "anterior_al_cliente", "falta_de_uso_posible", "nota"].includes(k)));
          return `<div class="inf-ficha"><div class="inf-ficha-cab" style="cursor:default">${m.acta ? `<a class="link-acta" href="javascript:void(0)" data-acta="${esc(m.acta)}">Acta ${esc(m.acta)} ↗</a>` : ""}
            <strong>${esc(m.denominacion || "")}</strong>${m.clase != null && m.clase !== "" ? `<span class="cl-tag">Clase ${esc(m.clase)}</span>` : ""}${avisos}</div>
            <div class="inf-ficha-cuerpo">${dl(resto)}${m.nota ? `<div class="inf-largo inf-destacado">${esc(m.nota)}</div>` : ""}</div></div>`;
        }).join("")}</div>` : "")}
      ${caja("Comparación", (signos ? `<h3>Signos</h3>${dl(signos)}` : "")
        + ((cmp.productos || []).length ? `<h3 style="margin-top:12px">Productos y servicios</h3><div style="overflow-x:auto"><table class="cl-tabla"><thead><tr><th>Ítem</th><th>Situación</th><th>Detalle</th></tr></thead><tbody>
            ${cmp.productos.map(p => `<tr><td>${esc(p.item || "")}</td><td>${p.situacion ? `<span class="cl-tag ${sit(p.situacion)}">${esc(p.situacion)}</span>` : ""}</td><td>${esc(p.detalle || "")}</td></tr>`).join("")}</tbody></table></div>` : "")
        + (conv ? `<h3 style="margin-top:12px">Convivencia</h3>${conv.resumen ? `<p style="margin:0 0 8px;font-size:13px">${esc(conv.resumen)}</p>` : ""}${tabla(conv.marcas, { id: "t-conv", filtro: (conv.marcas || []).length > 10 })}` : "")
        + Object.entries(cmp).filter(([k]) => !["signos", "productos", "convivencia"].includes(k)).map(([k, v]) => `<h3 style="margin-top:12px">${esc(etiqueta(k))}</h3><p style="margin:0;font-size:13px">${esc(texto(v))}</p>`).join(""))}
      ${caja("Palancas", (d.palancas || []).length ? `<ul>${d.palancas.map(p => typeof p === "object" ? `<li><strong>${esc(p.palanca || "")}</strong>${p.detalle ? ": " + esc(p.detalle) : ""}</li>` : `<li>${esc(p)}</li>`).join("")}</ul>` : "")}
      ${caja("Chances y caminos", `${ch.nivel || ch.motivo ? `<p style="margin:0 0 10px;font-size:13.5px"><span class="cl-tag ${colorCh}">Chances ${esc(ch.nivel || "")}</span> ${esc(ch.motivo || "")}</p>` : ""}
        ${(d.caminos || []).length ? `<div class="op-caminos">${d.caminos.map((c, i) => typeof c === "object" ? `<div class="op-camino${c.recomendado ? " reco" : ""}">
            <div class="op-camino-cab"><span class="op-num">${i + 1}</span><strong>${esc(c.titulo || "")}</strong>${c.recomendado ? '<span class="cl-tag ok">sugerido</span>' : ""}${c.costo ? `<span class="op-costo">${esc(c.costo)}</span>` : ""}</div>
            ${c.detalle ? `<p>${esc(c.detalle)}</p>` : ""}</div>` : `<div class="op-camino"><p>${esc(c)}</p></div>`).join("")}</div>` : ""}`)}
      ${caja("Qué preguntarle al cliente", (d.preguntas_cliente || []).length ? lista(d.preguntas_cliente) : "")}
      ${caja("A confirmar con Pame", (d.a_confirmar_pame || []).length ? lista(d.a_confirmar_pame) : "")}
      ${caja("Limitaciones", (d.limitaciones || []).length ? lista(d.limitaciones) : "")}
      ${otras.map(([k, v]) => caja(etiqueta(k), Array.isArray(v) && v.length && typeof v[0] === "object" ? tabla(v) : typeof v === "object" ? dl(v) : `<p style="margin:0">${esc(texto(v))}</p>`)).join("")}`;
    cont.querySelectorAll("[data-acta]").forEach(a => a.addEventListener("click", ev => { ev.preventDefault(); ev.stopPropagation(); abrirActa(a.dataset.acta); }));
    cont.querySelectorAll("[data-filtrar]").forEach(inp => inp.addEventListener("input", () => {
      const q = inp.value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
      $(inp.dataset.filtrar).querySelectorAll("tbody tr").forEach(tr => {
        tr.hidden = q && !tr.textContent.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().includes(q);
      });
    }));
  }

  // Informe Vista IA: ficha ⚡ arriba, después las vistas (con su vencimiento), antecedentes, palancas y caminos.
  function vista(r) {
    const d = r.datos || {}, rx = d.resumen_express || {}, mc = d.marca_cliente || {};
    const ant = d.antecedentes && typeof d.antecedentes === "object" ? d.antecedentes : {};
    const vs = Array.isArray(d.vistas) ? d.vistas : [];
    const ch = typeof d.chances === "string" ? { nivel: d.chances } : (d.chances || {});
    const colorCh = /^(muy bajas|bajas)$/.test(ch.nivel || "") ? "mal" : /^(bajas a medias|medias)$/.test(ch.nivel || "") ? "aviso" : ch.nivel ? "ok" : "";
    const aOrden = f => String(f || "").split("/").reverse().join("");
    const vtos = vs.map(v => v.vencimiento).filter(Boolean).sort((a, b) => aOrden(a).localeCompare(aOrden(b)));
    const arts = [...new Set(vs.flatMap(v => Array.isArray(v.articulos) ? v.articulos : v.articulos ? [v.articulos] : []))];
    const RX = [["cliente_marca", "Cliente / marca"], ["vista", "Vista"], ["que_objeta", "Qué objeta"], ["plazos", "Plazos"],
      ["chances", "Chances"], ["ojo", "Ojo"], ["camino_sugerido", "Camino sugerido"], ["falta", "Falta"]];
    const fichaRx = RX.filter(([k]) => rx[k]).map(([k, t]) => `<div class="op-rx-fila${k === "ojo" ? " ojo" : ""}"><dt>${esc(t)}</dt><dd>${esc(texto(rx[k]))}</dd></div>`).join("")
      + Object.entries(rx).filter(([k, v]) => !RX.some(([c]) => c === k) && v).map(([k, v]) => `<div class="op-rx-fila"><dt>${esc(etiqueta(k))}</dt><dd>${esc(texto(v))}</dd></div>`).join("");
    const actaLink = a => a ? `<a class="link-acta" href="javascript:void(0)" data-acta="${esc(a)}">${esc(a)} ↗</a>` : "";
    const conocidas = new Set(["tipo", "version", "fecha_analisis", "resumen_express", "resumen", "marca_cliente", "vistas", "antecedentes",
      "palancas", "chances", "caminos", "preguntas_cliente", "a_confirmar_pame", "limitaciones"]);
    const otras = Object.entries(d).filter(([k]) => !conocidas.has(k));
    const VCAMPOS = ["acta_cliente", "clase", "tipo_vista", "articulos", "fundamento", "fecha_vista", "if_vista", "fecha_notificacion", "if_cedula", "vencimiento", "vencimiento_inpi", "prorroga"];
    // Antecedentes: tarjetas con acta, marca, clase y estado; verde los favorables, rojo los en contra.
    const fichasAnt = (filas, tono) => (filas || []).length ? `<div class="inf-fichas">${filas.map(m => {
        if (!m || typeof m !== "object") return `<div class="inf-ficha">${esc(texto(m))}</div>`;
        const resto = Object.fromEntries(Object.entries(m).filter(([k]) => !["acta", "denominacion", "clase", "tipo", "nota"].includes(k)));
        return `<div class="inf-ficha"><div class="inf-ficha-cab" style="cursor:default">${m.acta ? `<a class="link-acta" href="javascript:void(0)" data-acta="${esc(m.acta)}">Acta ${esc(m.acta)} ↗</a>` : ""}
          <strong>${esc(m.denominacion || "")}</strong>${m.clase != null && m.clase !== "" ? `<span class="cl-tag">Clase ${esc(m.clase)}</span>` : ""}${m.tipo ? `<span class="cl-tag">${esc(m.tipo)}</span>` : ""}<span class="cl-tag ${tono}">${tono === "ok" ? "a favor" : "en contra"}</span></div>
          <div class="inf-ficha-cuerpo">${dl(resto)}${m.nota ? `<div class="inf-largo inf-destacado">${esc(m.nota)}</div>` : ""}</div></div>`;
      }).join("")}</div>` : "";
    cont.innerHTML = barra(r, "") + `
      <div class="inf-kpis">
        <div class="inf-kpi"><b class="op-ch ${colorCh}">${esc(ch.nivel || "—")}</b><span>chances</span></div>
        <div class="inf-kpi"><b>${vs.length}</b><span>${vs.length === 1 ? "vista" : "vistas"}</span></div>
        <div class="inf-kpi"><b>${esc(vtos[0] || "—")}</b><span>vence la vista</span></div>
        <div class="inf-kpi" style="flex:1"><b style="font-size:15px">${esc(arts.join(", ") || "—")}</b><span>artículo invocado</span></div>
      </div>
      ${caja("⚡ Resumen express", fichaRx ? `<dl class="op-rx">${fichaRx}</dl>` : "")}
      ${caja("Resumen", d.resumen ? `<p class="inf-obs">${esc(d.resumen)}</p>` : "")}
      ${caja("Marca del cliente", dl(Object.fromEntries(Object.entries(mc).filter(([k]) => !["actas_observadas", "otras_actas"].includes(k))))
        + ((mc.actas_observadas || []).length ? `<h3 style="margin-top:12px">Actas observadas</h3>${tabla(mc.actas_observadas, { id: "t-actas" })}` : "")
        + ((mc.otras_actas || []).length ? `<h3 style="margin-top:12px">Otras actas del titular</h3>${tabla(mc.otras_actas, { id: "t-otras" })}` : ""))}
      ${caja(vs.length === 1 ? "La vista" : "Las vistas", vs.length ? `<div class="op-lista">${vs.map(v => `<div class="op-item">
          <div class="op-item-cab">Acta ${actaLink(v.acta_cliente) || "—"}${v.clase != null && v.clase !== "" ? ` <span class="cl-tag">Clase ${esc(v.clase)}</span>` : ""}${v.tipo_vista ? ` · <strong>${esc(v.tipo_vista)}</strong>` : ""}</div>
          <dl class="inf-grid">
            ${(Array.isArray(v.articulos) ? v.articulos.length : v.articulos) ? `<dt>Artículos</dt><dd>${esc(texto(v.articulos))}</dd>` : ""}
            ${v.fundamento ? `<dt>Fundamento</dt><dd>${esc(v.fundamento)}</dd>` : ""}
            ${v.fecha_vista ? `<dt>Vista</dt><dd>${esc(v.fecha_vista)}${v.if_vista ? ` · ${esc(v.if_vista)}` : ""}</dd>` : ""}
            ${v.fecha_notificacion ? `<dt>Notificación</dt><dd>${esc(v.fecha_notificacion)}${v.if_cedula ? ` · ${esc(v.if_cedula)}` : ""}</dd>` : ""}
            ${v.vencimiento ? `<dt>Vence</dt><dd><strong>${esc(v.vencimiento)}</strong>${v.vencimiento_inpi && v.vencimiento_inpi !== v.vencimiento ? ` <span class="crm-gris">(el INPI muestra ${esc(v.vencimiento_inpi)})</span>` : ""}</dd>` : ""}
            ${v.prorroga ? `<dt>Prórroga</dt><dd>${esc(v.prorroga)}</dd>` : ""}
            ${Object.entries(v).filter(([k, x]) => !VCAMPOS.includes(k) && x !== "" && x != null).map(([k, x]) => `<dt>${esc(etiqueta(k))}</dt><dd>${esc(texto(x))}</dd>`).join("")}
          </dl></div>`).join("")}</div>` : "")}
      ${caja("Antecedentes", ((ant.busquedas || []).length ? `<h3>Búsquedas en el INPI</h3>${tabla(ant.busquedas, { id: "t-busq" })}` : "")
        + ((ant.favorables || []).length ? `<h3 style="margin-top:12px">A favor (concedidas)</h3>${fichasAnt(ant.favorables, "ok")}` : "")
        + ((ant.en_contra || []).length ? `<h3 style="margin-top:12px">En contra (denegadas u observadas)</h3>${fichasAnt(ant.en_contra, "mal")}` : "")
        + (ant.conclusion ? `<div class="inf-largo inf-destacado" style="margin-top:12px">${esc(texto(ant.conclusion))}</div>` : "")
        + Object.entries(ant).filter(([k]) => !["busquedas", "favorables", "en_contra", "conclusion"].includes(k)).map(([k, x]) => `<h3 style="margin-top:12px">${esc(etiqueta(k))}</h3>${Array.isArray(x) && x.length && typeof x[0] === "object" ? tabla(x) : `<p style="margin:0;font-size:13px">${esc(texto(x))}</p>`}`).join(""))}
      ${caja("Palancas", (d.palancas || []).length ? `<ul>${d.palancas.map(p => typeof p === "object" ? `<li><strong>${esc(p.palanca || "")}</strong>${p.detalle ? ": " + esc(p.detalle) : ""}</li>` : `<li>${esc(p)}</li>`).join("")}</ul>` : "")}
      ${caja("Chances y caminos", `${ch.nivel || ch.motivo ? `<p style="margin:0 0 10px;font-size:13.5px"><span class="cl-tag ${colorCh}">Chances ${esc(ch.nivel || "")}</span> ${esc(ch.motivo || "")}</p>` : ""}
        ${(d.caminos || []).length ? `<div class="op-caminos">${d.caminos.map((c, i) => typeof c === "object" ? `<div class="op-camino${c.recomendado ? " reco" : ""}">
            <div class="op-camino-cab"><span class="op-num">${i + 1}</span><strong>${esc(c.titulo || "")}</strong>${c.recomendado ? '<span class="cl-tag ok">sugerido</span>' : ""}${c.costo ? `<span class="op-costo">${esc(c.costo)}</span>` : ""}</div>
            ${c.detalle ? `<p>${esc(c.detalle)}</p>` : ""}</div>` : `<div class="op-camino"><p>${esc(c)}</p></div>`).join("")}</div>` : ""}`)}
      ${caja("Qué preguntarle al cliente", (d.preguntas_cliente || []).length ? lista(d.preguntas_cliente) : "")}
      ${caja("A confirmar con Pame", (d.a_confirmar_pame || []).length ? lista(d.a_confirmar_pame) : "")}
      ${caja("Limitaciones", (d.limitaciones || []).length ? lista(d.limitaciones) : "")}
      ${otras.map(([k, v]) => caja(etiqueta(k), Array.isArray(v) && v.length && typeof v[0] === "object" ? tabla(v) : typeof v === "object" ? dl(v) : `<p style="margin:0">${esc(texto(v))}</p>`)).join("")}`;
    cont.querySelectorAll("[data-acta]").forEach(a => a.addEventListener("click", ev => { ev.preventDefault(); ev.stopPropagation(); abrirActa(a.dataset.acta); }));
  }

  (async function () {
    if (!iid) { cont.innerHTML = '<p class="crm-error">Falta el número de informe.</p>'; return; }
    let r;
    try { r = await api(`/api/informes/${iid}`); }
    catch (e) { cont.innerHTML = `<p class="crm-error">No se pudo abrir el informe (${esc(e.message)}).</p>`; return; }
    // La dirección queda con el código de compartir (?c=): si se copia de la barra y se manda por
    // WhatsApp, la tarjeta muestra el tipo, la marca y de quién es en vez de la pantalla de ingreso.
    const linkCompartir = r.compartir ? `${location.origin}/informes/${r.id}?c=${encodeURIComponent(r.compartir)}` : "";
    if (linkCompartir && new URLSearchParams(location.search).get("c") !== r.compartir) {
      try { history.replaceState(null, "", `/informes/${r.id}?c=${encodeURIComponent(r.compartir)}`); } catch (e) { /* sin cambio de dirección */ }
    }
    const conectarCompartir = () => {
      const b = $("inf-compartir");
      if (!b) return;
      b.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(linkCompartir); b.textContent = "✓ Link copiado"; }
        catch (e) { prompt("Copiá el link:", linkCompartir); }
        setTimeout(() => { b.textContent = "🔗 Copiar link para compartir"; }, 2500);
      });
    };
    if (r.tipo === "cliente") {
      cont.innerHTML = barra(r, `<a class="cl-btn" href="/api/informes/${r.id}/pdf">Descargar PDF</a>`) +
        `<iframe class="inf-pdf" src="/api/informes/${r.id}/pdf?ver=1" title="Informe en PDF"></iframe>`;
    } else if (r.tipo === "oposicion") {
      oposicion(r);
    } else if (r.tipo === "vista") {
      vista(r);
    } else {
      interno(r);
    }
    conectarCompartir();
  })();
})();
