/* Informes preliminares, compartido entre Más → Informes (informes.js) y la solapa Informes de la
 * ficha del lead (titular.html): la lista agrupada por marca y el diálogo «Nuevo informe», que
 * carga el JSON (el del cliente o el interno: se reconoce solo) a un cliente o a un lead que
 * todavía no es cliente. Depende de comun.js (api, apiJson). Estilos en informes_comun.css.
 * Todo texto que viene de la base pasa por ixEsc() antes de ir al HTML. */

const ixEsc = (s) => (s ?? "").toString()
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const IX_ICO = {
  pdf: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
  ia: '<svg viewBox="0 0 24 24"><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 10h4v4h-4z"/></svg>',
  mas: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  bajar: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
  buscar: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
};

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

function ixVersiones(lista) {
  if (lista.length < 2) return "";
  const resto = lista.slice(1);
  return `<details class="ix-versiones"><summary>+ ${ixPlural(resto.length, "versión anterior", "versiones anteriores")}</summary>
    ${resto.map((v) => `<a href="/informes/${v.id}" target="_blank" rel="noopener">${ixEsc(ixFechaCorta(v.generado_en))}${v.generado_por ? " · " + ixEsc(v.generado_por) : ""} ↗</a>`).join("")}</details>`;
}

function ixDocPdf(lista, g) {
  if (!lista.length) return `<button type="button" class="ix-doc falta" data-ix-cargar data-tipo="cliente">
      <span class="ix-doc-ico">${IX_ICO.mas}</span><span class="ix-doc-txt"><span class="ix-doc-titulo">Informe al cliente</span>
      <span class="ix-doc-meta">Falta · cargarlo</span></span></button>`;
  const u = lista[0];
  const fecha = u.fecha_informe || ixFechaCorta(u.generado_en);
  return `<div><div class="ix-doc pdf">
      <a class="ix-doc-abrir" href="/informes/${u.id}" target="_blank" rel="noopener" title="Abrir el PDF">
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
      <a class="ix-doc-abrir" href="/informes/${u.id}" target="_blank" rel="noopener" title="Abrir el informe interno">
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

/* La lista. `mostrarDueno` en false para la ficha del lead (ya se sabe de quién es). */
function ixListaHtml(grupos, { mostrarDueno = true } = {}) {
  return `<div class="ix-lista">${grupos.map((g, i) => {
    const ref = /^INFPR-\d+$/i.test(g.referencia || "") ? `<span class="ix-ref">${ixEsc(g.referencia.toUpperCase())}</span>` : "";
    return `<div class="ix-fila" data-ix="${i}">
      <div class="ix-marca"><div><span class="ix-marca-nombre">${ixEsc(g.marca || "—")}</span>${ref}</div>
        ${mostrarDueno ? `<div class="ix-dueno">${ixDueno(g)}</div>` : ""}</div>
      ${ixDocPdf(g.cliente, g)}
      ${ixDocIa(g.interno, g)}
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
 * Dos cuadros separados: el JSON del informe al cliente (arma el PDF) y el del informe IA interno.
 * Se puede cargar uno solo o los dos; si van los dos, primero el del cliente (el interno toma su INFPR).
 * destino: {cliente_id, nombre, tipo:"cliente"} | {lead_clave, nombre, tipo:"lead"} | null (se elige buscando)
 * fijo: el destino no se puede cambiar (ficha del lead). solo: "cliente" | "interno" muestra un solo cuadro. */
const IX_CAJAS = {
  cliente: { titulo: "Informe al cliente (PDF)", ico: "pdf",
    ayuda: "Pegá el <strong>…_cliente.json</strong>. El panel arma el PDF del informe preliminar que se le manda a la persona.",
    placeholder: '{ "tipo": "informe_viabilidad_cliente", "encabezado": { … }, "secciones": [ … ] }',
    otro: "Ese es el JSON interno: pegalo en el cuadro «Informe IA interno»." },
  interno: { titulo: "Informe IA interno", ico: "ia",
    ayuda: "Pegá el <strong>…_interno.json</strong>. Queda guardado para el estudio (búsquedas, antecedentes, revisión legal); no se manda.",
    placeholder: '{ "tipo": "informe_viabilidad_interno", "caso": { … }, "busqueda_inpi": { … } }',
    otro: "Ese es el JSON del informe al cliente: pegalo en el cuadro «Informe al cliente (PDF)»." },
};

function ixNuevoInforme({ destino = null, fijo = false, marca = "", solo = "", esperado = "", alTerminar = null } = {}) {
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
  const tipos = solo ? [solo] : ["cliente", "interno"];
  let elegido = destino;
  const est = { cliente: { previa: { ok: false }, hecho: null }, interno: { previa: { ok: false }, hecho: null } };
  const cuerpo = $m("ix-cuerpo"), estado = $m("ix-estado"), guardar = $m("ix-guardar");
  $m("ix-titulo").textContent = solo === "cliente" ? "Cargar informe al cliente" : solo === "interno" ? "Cargar informe IA interno" : "Nuevo informe";
  cuerpo.innerHTML = `
    <p class="ix-paso">Para quién</p>
    <div class="ix-bloque" id="ix-destino"></div>
    ${tipos.map((t) => `
    <div class="ix-caja" id="ix-caja-${t}">
      <div class="ix-caja-cab"><span class="ix-doc-ico ${IX_CAJAS[t].ico}">${IX_ICO[IX_CAJAS[t].ico]}</span>
        <div><div class="ix-caja-titulo">${IX_CAJAS[t].titulo}${solo ? "" : ' <span class="ix-opcional">opcional</span>'}</div>
          <div class="ix-caja-ayuda">${IX_CAJAS[t].ayuda}</div></div></div>
      <textarea id="ix-texto-${t}" spellcheck="false" placeholder='${IX_CAJAS[t].placeholder}'></textarea>
      <div class="ix-previa" id="ix-previa-${t}"></div>
    </div>`).join("")}
    ${solo ? "" : '<p class="ix-nota">Podés pegar uno solo o los dos. Si pegás los dos, el interno toma la misma referencia INFPR que el PDF.</p>'}`;
  estado.textContent = ""; estado.className = "ix-estado";
  $m("ix-cancelar").textContent = "Cancelar";

  const listos = () => tipos.filter((t) => !est[t].hecho && est[t].previa.ok);
  const conTexto = () => tipos.filter((t) => !est[t].hecho && $m("ix-texto-" + t).value.trim());
  const actualizarBoton = () => {
    const l = listos(), malos = conTexto().filter((t) => !est[t].previa.ok);
    guardar.disabled = !elegido || !l.length || malos.length > 0;
    guardar.textContent = l.includes("cliente") && l.includes("interno") ? "Generar PDF y guardar los dos"
      : l.includes("cliente") ? "Generar PDF" : l.includes("interno") ? "Guardar informe interno" : "Guardar";
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
    q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(buscar, 300); });
    if (q.value) buscar();
    setTimeout(() => q.focus(), 30);
  }
  pintarDestino();

  tipos.forEach((t) => {
    const ta = $m("ix-texto-" + t);
    ta.addEventListener("input", () => {
      let p = ixPrevia(ta.value);
      if (p.tipo && p.tipo !== t) p = { ok: false, html: `<span class="mal">${IX_CAJAS[t].otro}</span>` };
      est[t].previa = p;
      $m("ix-previa-" + t).innerHTML = p.html;
      estado.textContent = ""; estado.className = "ix-estado";
      actualizarBoton();
    });
  });

  function marcarHecho(t, r) {
    est[t].hecho = r;
    const caja = $m("ix-caja-" + t);
    caja.classList.add("hecho");
    $m("ix-texto-" + t).disabled = true;
    $m("ix-previa-" + t).innerHTML = `<span class="bien">✓ Guardado${r.referencia ? " · " + ixEsc(r.referencia) : ""}</span>
      <a class="ix-boton chico" href="/informes/${r.id}" target="_blank" rel="noopener">${t === "cliente" ? "Ver el PDF ↗" : "Ver el informe ↗"}</a>
      ${t === "cliente" ? `<a class="ix-boton sec chico" href="/api/informes/${r.id}/pdf">Descargar PDF</a>` : ""}`;
  }

  guardar.onclick = async () => {
    if (guardar.disabled) return;
    guardar.disabled = true; estado.className = "ix-estado";
    const orden = ["cliente", "interno"].filter((t) => listos().includes(t));  // el del cliente primero: el interno hereda su INFPR
    let alguno = false;
    for (const t of orden) {
      estado.textContent = t === "cliente" ? "Generando el PDF…" : "Guardando el informe interno…";
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
  actualizarBoton();
  modal.classList.add("abierto");
  if (elegido) setTimeout(() => $m("ix-texto-" + tipos[0]).focus(), 30);
}
