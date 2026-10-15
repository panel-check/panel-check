/* Ficha de una marca (Clientes → Marcas → «Abrir ficha», o desde la ficha del cliente).
 * Una «marca» es la carpeta que junta los expedientes (actas) que son la misma marca: distintas clases,
 * denominativa y mixta, etc. (la agrupación la arma clientes.js: gruposDeMarcas).
 * Pestañas: Información general · Expedientes · Clases NCL · Plazos · Vigilancia · Historial.
 * Depende de clientes.js (esc, $, est, api, apiJson, htmlAlerta, conectarAlertas, abrirCliente, cargarResumen,
 * estadoMarca, tagEstadoMarca, logoMarca) y de niza.js. Todo texto de la base pasa por esc(). */

const FM_TABS = [
  ["info", "Información general"], ["expedientes", "Expedientes"], ["clases", "Clases NCL"],
  ["plazos", "Plazos"], ["vigilancia", "Vigilancia"], ["historial", "Historial"],
];
const fm = { nombre: "", actas: [], d: null, tab: "info", sel: new Set(), tipo: "", cambios: false };

const fmPesos = n => "$" + Math.round(n).toLocaleString("es-AR");
const fmNiza = n => NIZA.find(c => c[0] === n);
const fmAbierto = id => ["nueva", "monitorear", "oponer"].includes(id);

function fmCerrar() {
  $("modal-marca").classList.remove("abierto");
  if (fm.cambios) {
    fm.cambios = false; cargarResumen(); cargarVista();
    if (est.clienteAbierto) abrirCliente(est.clienteAbierto);   // la ficha del cliente, si estaba abierta debajo
  }
}
$("modal-marca").addEventListener("click", fmCerrar);
document.querySelector("#modal-marca .modal-cerrar").addEventListener("click", fmCerrar);
// Escape cierra primero la ficha de marca (queda encima de la del cliente, si estaba abierta).
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape" && $("modal-marca").classList.contains("abierto")) { ev.stopImmediatePropagation(); fmCerrar(); }
}, true);

/* Ciclo de vida de la marca como un todo (el estado real es de cada expediente). */
function fmEstado(ms) {
  const abiertas = ms.filter(m => !["Concedida", "Denegada"].includes(m.estado_tramite));
  if (ms.some(m => m.estado_tramite === "Concedida")) return { texto: "Registrada", clase: "ok" };
  if (abiertas.some(m => m.tuvo_oposicion)) return { texto: "En oposición", clase: "aviso" };
  if (!abiertas.length) return { texto: "Denegada", clase: "mal" };
  if (abiertas.some(m => !m.consultado_en)) return { texto: "Pendiente de consultar", clase: "aviso" };
  if (abiertas.some(m => m.fecha_publicacion)) return { texto: "Publicada", clase: "azul" };
  return { texto: "En trámite", clase: "" };
}

/* Entrada: nombre de la marca y las actas que la componen. */
async function abrirFichaMarca(nombre, actas, tab = "info") {
  Object.assign(fm, { nombre, actas: [...actas], d: null, tab, sel: new Set(), tipo: "" });
  $("modal-marca").classList.add("abierto");
  fmPintar();
  await fmCargar();
}

async function fmCargar() {
  try {
    fm.d = await api(`/api/cartera/ficha-marca?actas=${encodeURIComponent(fm.actas.join(","))}`);
    est.estadosAlerta = fm.d.estados_alerta;
    if (!est.resumen) est.resumen = await api("/api/cartera/resumen");
    if (!fm.tipo) fm.tipo = tipoLegible(fm.d.marcas[0]?.tipo) || "Denominativa";
    fmPintar();
  } catch (e) {
    $("modal-marca-contenido").innerHTML = `<p class="crm-error">No se pudo cargar la marca (${esc(e.message)}).</p>`;
  }
}

function fmPintar() {
  const cont = $("modal-marca-contenido");
  const d = fm.d;
  // Mientras carga se reserva el alto de la ficha para que el contenido no «salte».
  if (!d) { cont.innerHTML = `<div class="fm-ficha"><h3>🛡 ${esc(fm.nombre)}</h3><div class="fm-cuerpo"><p class="crm-cargando">Cargando…</p></div></div>`; return; }
  const ms = d.marcas, c = d.cliente || {};
  const est_ = fmEstado(ms);
  const abiertas = d.alertas.filter(a => fmAbierto(a.estado)).length;
  const cuenta = { expedientes: ms.length, plazos: d.plazos.length, vigilancia: abiertas, historial: d.novedades.length };
  const vigiladas = ms.filter(m => m.vigilar).length;
  cont.innerHTML = `
    <div class="fm-ficha">
      <div class="fm-cab">
        <h3>🛡 ${esc(fm.nombre)} <span class="cl-tag ${est_.clase}">${est_.texto}</span>
          ${vigiladas ? '<span class="cl-tag ok" title="Se compara contra las solicitudes nuevas de INPI">👁 Vigilada</span>' : '<span class="cl-tag">Sin vigilar</span>'}
          ${abiertas ? `<span class="cl-tag mal">🔔 ${abiertas}</span>` : ""}</h3>
        <div class="crm-gris">Cliente: <a href="javascript:void(0)" id="fm-cliente"><strong>${esc(c.nombre || "—")}</strong></a>${c.referido ? " " + tagReferido(c.referido) : ""}
          · ${ms.length} expediente${ms.length === 1 ? "" : "s"}</div>
      </div>
      <div class="fm-tabs" role="tablist">${FM_TABS.map(([k, t]) => `<button type="button" role="tab" data-tab="${k}" class="${fm.tab === k ? "activa" : ""}">${t}${cuenta[k] ? ` <span class="fm-n">${cuenta[k]}</span>` : ""}</button>`).join("")}</div>
      <div class="fm-cuerpo" id="fm-cuerpo">${fmTab()}</div>
    </div>`;
  cont.querySelectorAll("[data-tab]").forEach(b => b.addEventListener("click", () => { fm.tab = b.dataset.tab; fmPintar(); }));
  $("fm-cliente").addEventListener("click", () => { if (c.id) { fmCerrar(); abrirCliente(c.id); } });
  fmConectar(cont);
}

function fmTab() {
  return ({ info: fmInfo, expedientes: fmExpedientes, clases: fmClases, plazos: fmPlazos, vigilancia: fmVigilancia, historial: fmHistorial })[fm.tab]();
}

/* ── Información general ── */
function fmTarjeta(m) {
  return `<div class="fm-exp">
    <div class="fm-exp-cab">${logoMarca(m)}<a class="link-acta" href="javascript:void(0)" data-abrir="${esc(m.acta)}" title="Abrir el expediente completo en INPI">Acta ${esc(m.acta)} ↗</a>
      ${tagEstadoMarca(m)}${m.tuvo_oposicion && !["Concedida", "Denegada"].includes(m.estado_tramite) ? ' <span class="cl-tag aviso">⚖ oposición / vista</span>' : ""}
      ${m.alertas_abiertas ? `<span class="cl-tag mal">🔔 ${m.alertas_abiertas}</span>` : ""}</div>
    <div class="fm-exp-datos">
      <span><small>Denominación</small>${esc(m.denominacion || (m.tipo === "F" ? "(figurativa, sin texto)" : "(pendiente)"))}</span>
      <span><small>Clase</small>${esc(m.clase ?? "—")}</span>
      <span><small>Tipo</small>${esc(tipoLegible(m.tipo) || "—")}</span>
      <span><small>Titular</small>${esc(m.titular || "—")}</span>
      <span><small>Presentada</small>${m.fecha_presentacion ? crmFecha(m.fecha_presentacion) : "—"}</span>
      <span><small>Concedida</small>${m.fecha_concesion ? crmFecha(m.fecha_concesion) : "—"}</span>
      <span><small>Vence</small>${m.fecha_vencimiento_marca ? crmFecha(m.fecha_vencimiento_marca) : "—"}</span>
    </div>
    ${m.ultimo_movimiento ? `<div class="crm-gris" style="font-size:12px;margin-top:6px">Próximo/último movimiento del boletín: ${esc(m.ultimo_movimiento)}${m.ultimo_movimiento_fecha ? " (" + crmFecha(m.ultimo_movimiento_fecha) + ")" : ""}</div>` : ""}
    ${m.error_consulta ? `<div class="crm-error" style="padding:0;font-size:12px">${esc(m.error_consulta)}</div>` : ""}
    <div class="fm-exp-acc"><button type="button" class="cl-btn sec mini" data-actualizar="${esc(m.acta)}" title="Volver a consultar el expediente en INPI">↻ Actualizar</button>
      <button type="button" class="cl-btn sec mini" data-abrir="${esc(m.acta)}">Ver en INPI ↗</button></div>
  </div>`;
}

function fmInfo() {
  const d = fm.d, ms = d.marcas;
  const clases = [...new Set(ms.map(m => m.clase).filter(x => x != null))].sort((a, b) => a - b);
  const proximo = d.plazos[0];
  return `
    <div class="fm-resumen">
      <div><small>Clases cubiertas</small><strong>${clases.length ? clases.join(", ") : "—"}</strong></div>
      <div><small>Expedientes</small><strong>${ms.length}</strong></div>
      <div><small>Próximo plazo</small><strong>${proximo ? `${crmFecha(proximo.fecha)} · ${esc(crmUrgencia(proximo))}` : "—"}</strong></div>
    </div>
    <h4>Expedientes INPI vinculados</h4>
    <div class="fm-exps">${ms.map(fmTarjeta).join("")}</div>
    <p class="crm-nota">Una marca puede tener varios expedientes (clases, logos, variantes). El estado, las fechas y la vigilancia son de cada expediente.</p>`;
}

/* ── Expedientes ── */
function fmExpedientes() {
  const ms = fm.d.marcas;
  return `
    <div style="overflow-x:auto"><table class="cl-tabla"><thead><tr><th>Acta</th><th>Denominación</th><th>Clase</th><th>Tipo</th><th>Estado</th><th>Presentación</th><th>Vencimiento</th><th>Agente</th></tr></thead><tbody>
    ${ms.map(m => `<tr><td><a class="link-acta mk-acta" href="javascript:void(0)" data-abrir="${esc(m.acta)}">${esc(m.acta)} ↗</a></td>
      <td>${esc(m.denominacion || (m.tipo === "F" ? "(figurativa, sin texto)" : "(pendiente)"))}</td><td><strong>${esc(m.clase ?? "")}</strong></td>
      <td>${esc(tipoLegible(m.tipo))}</td><td>${tagEstadoMarca(m)}</td>
      <td>${m.fecha_presentacion ? crmFecha(m.fecha_presentacion) : "—"}</td><td>${m.fecha_vencimiento_marca ? crmFecha(m.fecha_vencimiento_marca) : "—"}</td>
      <td><small>${esc(m.agente || (m.matricula_agente ? "matrícula " + m.matricula_agente : "—"))}</small></td></tr>`).join("")}</tbody></table></div>
    <h4>Agregar un expediente a esta marca</h4>
    <div class="cl-caja"><label class="crm-gris" style="display:block;margin-bottom:4px">Número de acta. Se consulta en INPI, se carga con todos sus datos y queda en la cartera del mismo cliente.</label>
      <div class="cl-barra" style="padding:0"><input id="fm-acta-nueva" placeholder="ej. 3456789" style="min-width:200px">
        <button type="button" class="cl-btn" id="fm-sumar">Agregar expediente</button><span class="cl-estado" id="fm-sumar-estado"></span></div></div>`;
}

/* ── Clases NCL: selector + costo estimado + resumen para presentar ── */
function fmClasesCubiertas() {
  const mapa = new Map();
  fm.d.marcas.forEach(m => { if (m.clase != null && !mapa.has(+m.clase)) mapa.set(+m.clase, m); });
  return mapa;
}

function fmClases() {
  const cubiertas = fmClasesCubiertas();
  return `
    <h4>1 · Elegí dónde proteger la marca</h4>
    <p class="crm-gris" style="margin:0 0 8px">Las clases ya cubiertas por los expedientes aparecen tildadas. Tildá las que quieras cotizar para <strong>ampliar</strong> la protección.</p>
    <div class="cl-chips" id="fm-chips">${[...cubiertas.keys()].sort((a, b) => a - b).map(n => `<span class="cl-chip" style="padding-right:10px">✓ Clase ${n} · ${esc(fmNiza(n)?.[1] || "")} <span class="cl-tag ${cubiertas.get(n).estado_tramite === "Concedida" ? "ok" : ""}">${esc(cubiertas.get(n).estado_tramite === "Concedida" ? "registrada" : "en trámite")}</span></span>`).join("") || '<span class="crm-gris">Todavía no hay clases cargadas.</span>'}
      ${[...fm.sel].sort((a, b) => a - b).map(n => `<span class="cl-chip">＋ Clase ${n} · ${esc(fmNiza(n)?.[1] || "")}<button type="button" data-quitar="${n}" title="Sacar">×</button></span>`).join("")}</div>
    <input type="search" id="fm-buscar-clase" placeholder="Buscar clases por descripción o número…" style="width:100%;max-width:420px;margin:6px 0">
    <div class="fm-niza" id="fm-niza">${NIZA.map(([n, t, desc]) => {
      const ya = cubiertas.has(n);
      return `<label class="fm-niza-fila${ya ? " ya" : ""}" data-n="${n}" data-texto="${esc((n + " " + t + " " + desc).toLowerCase())}">
        <input type="checkbox" data-clase="${n}" ${ya || fm.sel.has(n) ? "checked" : ""} ${ya ? "disabled" : ""}>
        <span class="fm-niza-n">${n}</span><span><strong>${esc(t)}</strong>${ya ? ' <span class="cl-tag ok">ya cubierta</span>' : ""}<br><small class="crm-gris">${esc(desc)}</small></span></label>`;
    }).join("")}</div>
    <h4>2 · Costo estimado de registro en INPI</h4>
    <div id="fm-costos">${fmCostos()}</div>
    <h4>3 · Resumen para presentar</h4>
    <div id="fm-resumen">${fmResumen()}</div>`;
}

function fmCostos() {
  const tasa = fm.d.arancel.tasa_clase, nuevas = [...fm.sel].sort((a, b) => a - b);
  if (!nuevas.length) return '<p class="crm-gris">Tildá una o más clases arriba para ver cuánto cuesta presentar cada una.</p>';
  return `<div class="fm-costos">${nuevas.map(n => `<div class="fm-costo"><span><strong>Clase ${n}</strong><br><small class="crm-gris">${esc(fmNiza(n)?.[1] || "")}</small></span><span class="fm-monto">${fmPesos(tasa)}</span></div>`).join("")}
      <div class="fm-costo fm-total"><span>TOTAL (${nuevas.length} trámite${nuevas.length === 1 ? "" : "s"})</span><span class="fm-monto">${fmPesos(tasa * nuevas.length)}</span></div></div>
    <p class="crm-nota">Cada clase es un trámite independiente con su propia tasa base: ${fmPesos(tasa)}${fm.d.arancel.mes ? ` (valores de ${esc(fm.d.arancel.mes)})` : ""}. Es la tasa de INPI que se carga en <em>Mails → Presupuesto de registro de marca</em>; no incluye honorarios.</p>`;
}

function fmTextoResumen() {
  const nuevas = [...fm.sel].sort((a, b) => a - b), tasa = fm.d.arancel.tasa_clase;
  const hoy = new Date().toLocaleDateString("es-AR");
  return [
    "RESUMEN PARA PRESENTAR EN INPI", "",
    `Marca: ${fm.nombre}`, `Tipo: ${fm.tipo}`, `Titular: ${fm.d.cliente?.nombre || "—"}`, `Fecha: ${hoy}`, "",
    "Clases a solicitar:",
    ...nuevas.map(n => `  • Clase ${n} — ${fmNiza(n)?.[1] || ""}: ${fmNiza(n)?.[2] || ""} (tasa ${fmPesos(tasa)})`), "",
    `TOTAL (${nuevas.length} trámite${nuevas.length === 1 ? "" : "s"}): ${fmPesos(tasa * nuevas.length)}`, "",
    "Cada clase es un trámite independiente con su propia tasa base. Valores orientativos: confirmar al presentar.",
  ].join("\n");
}

function fmResumen() {
  if (!fm.sel.size) return '<p class="crm-gris">Se arma solo cuando elegís clases.</p>';
  return `<div class="cl-barra" style="padding:0 0 8px"><label>Tipo de marca<select id="fm-tipo">${["Denominativa", "Figurativa", "Mixta", "Tridimensional"].map(t => `<option ${t === fm.tipo ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <button type="button" class="cl-btn" id="fm-copiar">Copiar</button><button type="button" class="cl-btn sec" id="fm-imprimir">Imprimir / PDF</button><span class="cl-estado" id="fm-res-estado"></span></div>
    <pre class="fm-pre" id="fm-texto">${esc(fmTextoResumen())}</pre>`;
}

function fmImprimir() {
  const w = window.open("", "_blank");
  if (!w) { alert("El navegador bloqueó la ventana: permití los pop-ups para imprimir."); return; }
  w.document.write(`<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Resumen — ${esc(fm.nombre)}</title>
    <style>body{font:14px/1.5 Arial,sans-serif;margin:32px;color:#1f2430}h1{font-size:20px;margin:0 0 12px}pre{white-space:pre-wrap;font:inherit}.pie{margin-top:24px;color:#6b7280;font-size:12px}</style></head>
    <body><h1>Resumen para INPI</h1><pre>${esc(fmTextoResumen().replace(/^RESUMEN PARA PRESENTAR EN INPI\n\n/, ""))}</pre><div class="pie">Armado en el panel de Smarties Consultora</div></body></html>`);
  w.document.close(); w.focus(); setTimeout(() => w.print(), 250);
}

/* ── Plazos ── */
function fmPlazos() {
  const p = fm.d.plazos;
  return (p.length ? `<ul class="crm-plazos">${p.map(x => `
      <li class="crm-plazo crm-urg-${x.urgencia}"><span class="crm-plazo-fecha">${crmFecha(x.fecha)}<small>${crmUrgencia(x)}</small></span>
        <span class="crm-plazo-texto"><strong>${esc(x.titulo)}</strong>${x.acta ? ` · acta ${esc(x.acta)}, clase ${esc(x.clase)}` : ""}${x.detalle ? `<br><small class="crm-gris">${esc(x.detalle)}</small>` : ""}</span></li>`).join("")}</ul>`
    : '<p class="crm-gris">Sin plazos a la vista.</p>') + '<p class="crm-nota">Fechas orientativas calculadas por el sistema: confirmar siempre en el expediente.</p>';
}

/* ── Vigilancia: ajustes por expediente + alertas de la marca ── */
function fmVigilancia() {
  const d = fm.d, ms = d.marcas;
  const ult = est.resumen && est.resumen.ultima_vigilancia;
  return `
    <p class="crm-gris" style="margin:0 0 8px">Cada expediente se compara contra las solicitudes nuevas de INPI (boletines y escaneo de actas) mientras corre el plazo de oposición.
      ${ult ? `Última corrida: <strong>${fmtFechaHora(ult)}</strong>.` : ""}</p>
    <div style="overflow-x:auto"><table class="cl-tabla cl-marcas"><thead><tr><th>Acta</th><th>Clase</th><th>Vigilar</th><th>Umbral de parecido</th><th>Otros nombres a vigilar</th></tr></thead><tbody>
    ${ms.map(m => `<tr data-acta="${esc(m.acta)}"><td>${esc(m.acta)}</td><td>${esc(m.clase ?? "")}</td>
      <td><label><input type="checkbox" data-campo="vigilar" ${m.vigilar ? "checked" : ""}> Vigilar</label><br>
        <label title="Comparar también contra todas las clases, no solo la propia y las relacionadas"><input type="checkbox" data-campo="vigilar_todas_clases" ${m.vigilar_todas_clases ? "checked" : ""}> En todas las clases</label></td>
      <td><input type="number" min="40" max="100" data-campo="umbral_vigilancia" value="${m.umbral_vigilancia ?? ""}" placeholder="${d.umbral_general}" style="width:80px" title="Puntaje mínimo (40-100) para alertar sobre esta marca. Vacío = el general de Ajustes (${d.umbral_general})"> %<br><small class="crm-gris">${m.umbral_vigilancia ? "propio" : "general"}</small></td>
      <td><input type="text" data-campo="terminos_vigilancia" value="${esc(m.terminos_vigilancia || "")}" placeholder="variantes, abreviaturas… (coma)" style="width:100%;min-width:180px"></td></tr>`).join("")}</tbody></table></div>
    <p class="crm-nota">Un umbral más bajo avisa más parecidos (más ruido); uno más alto, solo los muy parecidos. El cambio se aplica en la próxima corrida de la vigilancia.</p>
    <h4>Alertas de esta marca (${d.alertas.filter(a => fmAbierto(a.estado)).length} abiertas)</h4>
    ${d.alertas.length ? d.alertas.map(a => htmlAlerta(a, { sinCliente: true })).join("") : '<p class="crm-gris">Sin alertas.</p>'}`;
}

/* ── Historial ── */
function fmHistorial() {
  const n = fm.d.novedades;
  return n.length ? `<ul class="cl-lista-simple">${n.map(x => `<li><span class="crm-gris">${fmtFechaHora(x.detectado_en)}</span> · acta ${esc(x.acta)} · ${esc(x.texto)}</li>`).join("")}</ul>`
    : '<p class="crm-gris">Sin movimientos detectados todavía.</p>';
}

/* ── Eventos de cada pestaña ── */
function fmConectar(cont) {
  cont.querySelectorAll("[data-abrir]").forEach(a => a.addEventListener("click", () => abrirActa(a.dataset.abrir)));
  cont.querySelectorAll("[data-actualizar]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await apiJson(`/api/cartera/marcas/${encodeURIComponent(b.dataset.actualizar)}/actualizar`, "POST");
      fm.cambios = true;
      if (r.aviso) alert(r.aviso); else if (r.novedades.length) alert("Novedades:\n• " + r.novedades.join("\n• "));
      await fmCargar();
    } catch (e) { alert(e.message); b.disabled = false; }
  }));

  if (fm.tab === "expedientes") {
    $("fm-sumar").addEventListener("click", async ev => {
      const acta = $("fm-acta-nueva").value.replace(/\D/g, ""), estado = $("fm-sumar-estado");
      if (!acta) return;
      ev.currentTarget.disabled = true; estado.textContent = "Consultando INPI…"; estado.className = "cl-estado";
      try {
        const r = await apiJson(`/api/clientes/${fm.d.cliente.id}/marcas`, "POST", { acta });
        fm.cambios = true; fm.actas.push(acta); await fmCargar();
        if (r.aviso) alert(r.aviso);
      } catch (e) { estado.textContent = e.message; estado.className = "cl-estado mal"; ev.currentTarget.disabled = false; }
    });
  }

  if (fm.tab === "clases") {
    cont.querySelectorAll("[data-clase]").forEach(i => i.addEventListener("change", () => {
      const n = +i.dataset.clase; if (i.checked) fm.sel.add(n); else fm.sel.delete(n);
      const pos = $("fm-niza").scrollTop, q = $("fm-buscar-clase").value;
      fmPintar(); $("fm-niza").scrollTop = pos; $("fm-buscar-clase").value = q; $("fm-buscar-clase").dispatchEvent(new Event("input"));
    }));
    cont.querySelectorAll("[data-quitar]").forEach(b => b.addEventListener("click", () => { fm.sel.delete(+b.dataset.quitar); fmPintar(); }));
    $("fm-buscar-clase").addEventListener("input", ev => {
      const q = ev.target.value.trim().toLowerCase();
      cont.querySelectorAll(".fm-niza-fila").forEach(f => { f.hidden = !!q && !f.dataset.texto.includes(q); });
    });
    fmConectarResumen();
  }

  if (fm.tab === "vigilancia") {
    cont.querySelectorAll(".cl-marcas tr[data-acta]").forEach(tr => {
      const acta = tr.dataset.acta;
      tr.querySelectorAll("[data-campo]").forEach(inp => inp.addEventListener("change", async () => {
        const campo = inp.dataset.campo;
        let valor = inp.type === "checkbox" ? inp.checked : inp.value.trim();
        if (campo === "umbral_vigilancia") valor = valor === "" ? null : parseInt(valor, 10);
        try {
          await apiJson(`/api/cartera/marcas/${encodeURIComponent(acta)}`, "PUT", { [campo]: valor });
          fm.cambios = true;
          const m = fm.d.marcas.find(x => x.acta === acta); if (m) m[campo] = valor;
          inp.style.outline = "2px solid var(--verde)"; setTimeout(() => { inp.style.outline = ""; }, 900);
          if (campo === "umbral_vigilancia") { const t = tr.querySelector("small.crm-gris"); if (t) t.textContent = valor ? "propio" : "general"; }
        } catch (e) { alert(e.message); }
      }));
    });
    conectarAlertas(cont, async () => { fm.cambios = true; await fmCargar(); });
  }
}

function fmConectarResumen() {
  const tipo = $("fm-tipo");
  if (!tipo) return;
  tipo.addEventListener("change", () => { fm.tipo = tipo.value; $("fm-texto").textContent = fmTextoResumen(); });
  $("fm-copiar").addEventListener("click", async () => {
    const estado = $("fm-res-estado");
    try { await navigator.clipboard.writeText(fmTextoResumen()); estado.textContent = "Copiado ✓"; estado.className = "cl-estado"; }
    catch (e) { estado.textContent = "No se pudo copiar: seleccioná el texto a mano."; estado.className = "cl-estado mal"; }
  });
  $("fm-imprimir").addEventListener("click", fmImprimir);
}
