/* Clientes y vigilancia marcaria (/clientes).
 * Depende de comun.js (api, apiJson, abrirActa, fmtFechaHora, tipoLegible) y
 * de crm.js (crmEsc, crmFecha, crmUrgencia). Todo texto que viene de la base
 * pasa por esc() antes de ir al HTML; los datos viajan en atributos data-*. */

const esc = crmEsc;
const VISTAS = ["marcas", "clientes", "formularios", "vigilancia", "vencimientos", "matricula", "ajustes"];
const est = {
  vista: "marcas",
  clientes: [], origenes: {},
  alertas: { estado: "abiertas", nivel: "", tipo: "", cliente_id: "", sin_contratar: false },
  estadosAlerta: {},
  clienteAbierto: null,
  clienteTab: "datos", clienteTabId: null,
  tiposPersona: { fisica: "Persona física", juridica: "Persona jurídica" },
};

const $ = id => document.getElementById(id);
const vista = id => $(`vista-${id}`);

function fmtNum(n) { return (n ?? 0).toLocaleString("es-AR"); }

function tagNivel(n) {
  const clase = n === "alta" ? "mal" : n === "media" ? "aviso" : "";
  return `<span class="cl-tag ${clase}">${esc((n || "").toUpperCase())}</span>`;
}

function llenarSelectReferidos(sel, lista, actual) {
  sel.innerHTML = '<option value="">Todos</option><option value="__sin__">Sin referido</option>' +
    lista.map(r => `<option value="${esc(r)}">${esc(r)}</option>`).join("");
  sel.value = [...sel.options].some(o => o.value === actual) ? actual : "";
}

function tagReferido(r) {
  return r ? `<span class="cl-tag ref" title="Referido por ${esc(r)}">🤝 ${esc(r)}</span>` : "";
}

// Logo de la marca (el que figura en el expediente de INPI y se guarda al cargar la marca).
// Se ve en miniatura; un click lo abre grande en una pestaña nueva. Las marcas sin logo
// (denominativas) no muestran nada.
function logoMarca(m) {
  if (!m.tiene_logo) return "";
  const url = `/api/analisis-marca/${encodeURIComponent(m.acta)}/logo`;
  return `<a href="${url}" target="_blank" rel="noopener" class="cl-logo-link" title="Ver el logo de la marca" onclick="event.stopPropagation()">` +
    `<img class="cl-logo" loading="lazy" alt="Logo de ${esc(m.denominacion || "la marca")}" src="${url}"></a>`;
}

// Estado de una marca, con una sola regla para la etiqueta y para el filtro «Estado»:
// concedida / denegada (resolución firme) → pendiente de consultar (todavía no se leyó en INPI)
// → publicada (ya salió en el boletín, sin resolución) → en trámite.
function estadoMarca(m) {
  if (m.estado_tramite === "Concedida") return { clave: "concedida", texto: "Concedida", clase: "ok" };
  if (m.estado_tramite === "Denegada") return { clave: "denegada", texto: "Denegada", clase: "mal" };
  if (!m.consultado_en) return { clave: "pendiente", texto: "Pendiente de consultar", clase: "aviso", ayuda: "Todavía no se consultó en INPI: se completa sola en la próxima revisión" };
  if (m.fecha_publicacion) return { clave: "publicada", texto: "Publicada", clase: "azul", ayuda: "Publicada en el boletín el " + crmFecha(m.fecha_publicacion) };
  return { clave: "en_tramite", texto: "En trámite", clase: "" };
}

function tagEstadoMarca(m) {
  const e = estadoMarca(m);
  return `<span class="cl-tag${e.clase ? " " + e.clase : ""}"${e.ayuda ? ` title="${esc(e.ayuda)}"` : ""}>${e.texto}</span>`;
}

function pedir(fn) {
  return fn().catch(e => { alert(e.message || e); throw e; });
}

/* ── Navegación ─────────────────────────────────────────────────── */
function cambiarVista(v, { sinURL = false } = {}) {
  est.vista = v;
  document.querySelectorAll("#pestanas button").forEach(b => b.classList.toggle("activa", b.dataset.vista === v));
  VISTAS.forEach(x => { vista(x).hidden = x !== v; });
  if (!sinURL) history.replaceState(null, "", v === "marcas" ? "/clientes" : `/clientes?vista=${v}`);
  cargarVista();
}

function cargarVista() {
  ({ clientes: pintarClientes, marcas: pintarMarcas, formularios: pintarFormularios, vigilancia: pintarVigilancia, vencimientos: pintarVencimientos,
     matricula: pintarMatricula, ajustes: pintarAjustes })[est.vista]();
}

async function cargarResumen() {
  try {
    const r = await api("/api/cartera/resumen");
    est.resumen = r;
    const chips = [
      `<span class="crm-chip"><strong>${fmtNum(r.clientes)}</strong> clientes</span>`,
      `<span class="crm-chip"><strong>${fmtNum(r.marcas)}</strong> marcas en cartera</span>`,
      `<span class="crm-chip crm-chip-plata"><strong>${fmtNum(r.con_vigilancia)}</strong> con vigilancia contratada</span>`,
      `<span class="crm-chip"><strong>${fmtNum(r.alertas_abiertas)}</strong> alertas abiertas</span>`,
    ];
    if (r.alertas_sin_contratar) chips.push(`<span class="crm-chip" title="Alertas de clientes que no pagan la vigilancia: oportunidad para ofrecerla">💡 <strong>${fmtNum(r.alertas_sin_contratar)}</strong> alertas de clientes sin vigilancia contratada</span>`);
    if (r.otro_agente_nuevas) chips.push(`<span class="crm-chip">🔀 <strong>${fmtNum(r.otro_agente_nuevas)}</strong> clientes presentaron marcas con otro agente</span>`);
    if (r.sin_consultar) chips.push(`<span class="crm-chip">⏳ <strong>${fmtNum(r.sin_consultar)}</strong> marcas pendientes de consultar en INPI</span>`);
    chips.push(`<span class="crm-chip crm-gris">Última vigilancia: ${r.ultima_vigilancia ? fmtFechaHora(r.ultima_vigilancia) : "todavía no corrió"}</span>`);
    $("resumen").innerHTML = chips.join("");
    $("badge-vigilancia").textContent = r.alertas_altas_nuevas ? String(r.alertas_altas_nuevas) : "";
    if ($("mk-kpis")) pintarKpisMarcas();
  } catch (e) {
    $("resumen").innerHTML = `<span class="crm-error">No se pudo cargar el resumen (${esc(e.message)}).</span>`;
  }
}

/* ── Clientes ───────────────────────────────────────────────────── */
async function pintarClientes() {
  const cont = vista("clientes");
  if (!cont.dataset.armado) {
    cont.dataset.armado = "1";
    cont.innerHTML = `
      <div class="cl-vista">
        <div class="cl-barra">
          <label>Buscar<input type="search" id="cl-q" placeholder="cliente, CUIT, email, marca o acta…" style="min-width:280px"></label>
          <label>Referido<select id="cl-referido"><option value="">Todos</option></select></label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="cl-inactivos"> Incluir inactivos</label>
          <button type="button" class="cl-btn" id="cl-nuevo">+ Nuevo cliente</button>
        </div>
        <div id="cl-tabla"></div>
      </div>`;
    let t;
    $("cl-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(pintarClientes, 250); });
    $("cl-inactivos").addEventListener("change", pintarClientes);
    $("cl-referido").addEventListener("change", pintarClientes);
    $("cl-nuevo").addEventListener("click", () => abrirModalNuevoCliente());
  }
  const q = $("cl-q").value.trim();
  const tabla = $("cl-tabla");
  try {
    const ref = $("cl-referido").value;
    const r = await api(`/api/clientes?q=${encodeURIComponent(q)}&activos=${$("cl-inactivos").checked ? "false" : "true"}&referido=${encodeURIComponent(ref)}`);
    est.clientes = r.clientes; est.origenes = r.origenes; est.referidos = r.referidos || [];
    llenarSelectReferidos($("cl-referido"), est.referidos, ref);
    if (!r.clientes.length) {
      tabla.innerHTML = `<p class="vacio">${q ? "Ningún cliente coincide con la búsqueda." : "Todavía no cargaste clientes. Usá «+ Nuevo cliente», o convertí un lead desde su ficha del CRM."}</p>`;
      return;
    }
    tabla.innerHTML = `<table class="cl-tabla"><thead><tr>
        <th>Cliente</th><th>CUIT</th><th>Marcas</th><th>Vigilancia</th><th>Alertas</th><th>Próx. vencimiento</th><th>Origen</th></tr></thead><tbody>
      ${r.clientes.map(c => `<tr class="cl-click ${c.activo ? "" : "cl-inactivo"}" data-id="${c.id}">
        <td><strong>${esc(c.nombre)}</strong>${c.activo ? "" : ' <span class="cl-tag">inactivo</span>'}${c.referido ? " " + tagReferido(c.referido) : ""}<br><small class="crm-gris">${esc(c.email || "")}</small></td>
        <td>${esc(c.cuit || "—")}</td>
        <td>${c.marcas}${c.con_oposicion ? ` <span class="cl-tag aviso" title="Marcas con oposición o vista">⚖ ${c.con_oposicion}</span>` : ""}${c.sin_consultar ? ` <span class="cl-tag" title="Pendientes de consultar en INPI">⏳ ${c.sin_consultar}</span>` : ""}</td>
        <td>${c.vigilancia_contratada ? '<span class="cl-tag ok">Contratada</span>' : '<span class="cl-tag">Interna</span>'}</td>
        <td>${c.alertas_abiertas ? `<span class="cl-tag mal">${c.alertas_abiertas}</span>` : "—"}</td>
        <td>${c.proximo_vencimiento ? crmFecha(c.proximo_vencimiento) : "—"}</td>
        <td>${esc(est.origenes[c.origen] || c.origen)}</td></tr>`).join("")}
      </tbody></table><p class="cl-pie">${r.clientes.length} cliente(s). La vigilancia interna corre igual para todos: «Contratada» solo marca a quién se le cobra y se le manda el informe.</p>`;
    tabla.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => abrirCliente(+tr.dataset.id)));
  } catch (e) {
    tabla.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`;
  }
}

/* ── Marcas (vista plana de toda la cartera) ────────────────────── */
async function pintarMarcas() {
  const cont = vista("marcas");
  if (!cont.dataset.armado) {
    cont.dataset.armado = "1";
    if (!est.clientes.length) { const r = await api("/api/clientes?activos=true"); est.clientes = r.clientes; est.origenes = r.origenes; }
    cont.innerHTML = `
      <div class="cl-vista">
        <div class="kpis" id="mk-kpis"></div>
        <div class="cl-barra">
          <label>Buscar<input type="search" id="mk-q" placeholder="marca, acta, titular o cliente…" style="min-width:240px"></label>
          <label>Cliente<select id="mk-cliente"><option value="">Todos</option>${est.clientes.map(c => `<option value="${c.id}">${esc(c.nombre)}</option>`).join("")}</select></label>
          <label>Referido<select id="mk-referido"><option value="">Todos</option></select></label>
          <label>Estado<select id="mk-estado"><option value="">Todos</option><option value="en_tramite">En trámite</option><option value="publicada">Publicada</option><option value="concedida">Concedida</option><option value="denegada">Denegada</option><option value="pendiente">Pendiente de consultar</option></select></label>
          <label>Clase<input type="number" id="mk-clase" min="1" max="45" style="width:80px"></label>
          <label>Vence en<select id="mk-vence"><option value="">—</option><option value="90">90 días</option><option value="180">6 meses</option><option value="365">1 año</option></select></label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-opo"> Con oposición</label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-alertas"> Con alertas</label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-sinvig"> Sin vigilar</label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-vigiladas"> Solo vigiladas</label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-agrupar" checked> Agrupar por marca</label>
        </div>
        <div id="mk-tabla"></div>
      </div>`;
    let t;
    $("mk-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(pintarMarcas, 250); });
    ["mk-cliente", "mk-referido", "mk-estado", "mk-clase", "mk-vence", "mk-opo", "mk-alertas", "mk-sinvig", "mk-vigiladas", "mk-agrupar"].forEach(id => $(id).addEventListener("change", pintarMarcas));
  }
  const p = new URLSearchParams();
  if ($("mk-q").value.trim()) p.set("q", $("mk-q").value.trim());
  if ($("mk-cliente").value) p.set("cliente_id", $("mk-cliente").value);
  if ($("mk-referido").value) p.set("referido", $("mk-referido").value);
  if ($("mk-estado").value) p.set("estado", $("mk-estado").value);
  if ($("mk-clase").value) p.set("clase", $("mk-clase").value);
  if ($("mk-vence").value) p.set("vence_dias", $("mk-vence").value);
  if ($("mk-opo").checked) p.set("con_oposicion", "true");
  if ($("mk-alertas").checked) p.set("alertas", "true");
  if ($("mk-sinvig").checked) p.set("sin_vigilar", "true");
  if ($("mk-vigiladas").checked) p.set("vigiladas", "true");
  const tabla = $("mk-tabla");
  try {
    if (!est.resumen) est.resumen = await api("/api/cartera/resumen");
    pintarKpisMarcas();
    const r = await api(`/api/cartera/marcas?${p}`);
    llenarSelectReferidos($("mk-referido"), r.referidos || [], $("mk-referido").value);
    if (!r.marcas.length) { tabla.innerHTML = '<p class="vacio">No hay marcas con estos filtros.</p>'; return; }
    if ($("mk-agrupar").checked) {
      tabla.innerHTML = marcasAgrupadasHtml(r.marcas);
      enlazarMarcasAgrupadas(tabla);
    } else {
      tabla.innerHTML = marcasPlanasHtml(r.marcas);
    }
    tabla.querySelectorAll("[data-abrir]").forEach(a => a.addEventListener("click", ev => { ev.stopPropagation(); abrirActa(a.dataset.abrir); }));
    tabla.querySelectorAll("[data-agrupar]").forEach(a => a.addEventListener("click", async ev => {
      ev.stopPropagation();
      const n = prompt(`Nombre del grupo para el acta ${a.dataset.agrupar}.\nLas actas con el mismo nombre de grupo (del mismo cliente) se muestran juntas. Dejalo vacío para sacarla del grupo manual.`, a.dataset.nombre || "");
      if (n === null) return;
      try { await apiJson(`/api/cartera/marcas/${encodeURIComponent(a.dataset.agrupar)}`, "PUT", { grupo_marca: n.trim() }); pintarMarcas(); }
      catch (e) { alert(e.message || e); }
    }));
    tabla.querySelectorAll("tr[data-cliente], [data-ver-cliente]").forEach(el => el.addEventListener("click", ev => {
      ev.stopPropagation(); abrirCliente(+(el.dataset.cliente || el.dataset.verCliente));
    }));
  } catch (e) { tabla.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`; }
}

/* Tarjetas de arriba de la lista de marcas: cada una es un atajo que aplica su filtro (la primera los limpia). */
function pintarKpisMarcas() {
  const r = est.resumen || {};
  const v = id => $(id) && ($(id).type === "checkbox" ? $(id).checked : $(id).value);
  const activa = v("mk-estado") === "concedida" ? "concedida" : v("mk-vence") === "180" ? "vencen" : v("mk-vigiladas") ? "vigiladas"
    : (!v("mk-q") && !v("mk-cliente") && !v("mk-estado") && !v("mk-clase") && !v("mk-vence") && !v("mk-opo") && !v("mk-alertas") && !v("mk-sinvig") ? "total" : "");
  const kpis = [
    ["total", "🛡", r.marcas, "Expedientes en cartera", "Todos los expedientes de clientes activos (sin filtros)"],
    ["concedida", "✅", r.marcas_concedidas, "Concedidas", "Expedientes con el registro concedido: ver solo esos"],
    ["vencen", "⏰", r.marcas_vencen, "Vencen en 6 meses", "Registros que vencen en los próximos 6 meses: ver solo esos"],
    ["vigiladas", "👁", r.marcas_vigiladas, "Vigiladas", "Expedientes que se comparan contra las solicitudes nuevas de INPI: ver solo esos"],
  ];
  $("mk-kpis").innerHTML = kpis.map(([k, ico, n, t, ayuda]) => `<button type="button" class="kpi kpi-${k}${activa === k ? " activa" : ""}" data-kpi="${k}" title="${esc(ayuda)}">
    <span class="kpi-ico" aria-hidden="true">${ico}</span><span class="kpi-n">${n == null ? "…" : fmtNum(n)}</span><span class="kpi-t">${t}</span></button>`).join("");
  $("mk-kpis").querySelectorAll("[data-kpi]").forEach(b => b.addEventListener("click", () => {
    ["mk-q", "mk-cliente", "mk-estado", "mk-clase", "mk-vence"].forEach(id => { $(id).value = ""; });
    ["mk-opo", "mk-alertas", "mk-sinvig", "mk-vigiladas"].forEach(id => { $(id).checked = false; });
    if (b.dataset.kpi === "concedida") $("mk-estado").value = "concedida";
    if (b.dataset.kpi === "vencen") $("mk-vence").value = "180";
    if (b.dataset.kpi === "vigiladas") $("mk-vigiladas").checked = true;
    pintarMarcas();
  }));
}

/* Marcas en lista plana (una fila por acta), para quien prefiera no agrupar. */
function marcasPlanasHtml(marcas) {
  const r = { marcas };
  return `<div style="overflow-x:auto"><table class="cl-tabla"><thead><tr>
        <th>Marca</th><th>Clase</th><th>Acta</th><th>Cliente</th><th>Estado</th><th>Vence</th><th>Agente</th><th>Vigilancia</th></tr></thead><tbody>
      ${r.marcas.map(m => `<tr class="cl-click" data-cliente="${m.cliente_id}">
        <td><strong>${esc(m.denominacion || (m.tipo === "F" ? "(figurativa, sin texto)" : "(pendiente)"))}</strong>
          <br><small class="crm-gris">${esc(tipoLegible(m.tipo))}${m.fecha_presentacion ? " · presentada " + crmFecha(m.fecha_presentacion) : ""}</small>
          ${m.tuvo_oposicion && !["Concedida", "Denegada"].includes(m.estado_tramite) ? '<br><span class="cl-tag aviso">⚖ oposición / vista</span>' : ""}</td>
        <td>${esc(m.clase ?? "")}</td>
        <td><a class="link-acta" href="javascript:void(0)" data-abrir="${esc(m.acta)}">${esc(m.acta)} ↗</a></td>
        <td>${esc(m.cliente_nombre)}${m.cliente_referido ? "<br>" + tagReferido(m.cliente_referido) : ""}</td>
        <td>${tagEstadoMarca(m)}</td>
        <td>${m.fecha_vencimiento_marca ? crmFecha(m.fecha_vencimiento_marca) : "—"}</td>
        <td><small>${esc(m.agente || (m.matricula_agente ? "matrícula " + m.matricula_agente : "—"))}</small></td>
        <td>${m.vigilar ? '<span class="cl-tag ok">Vigilada</span>' : '<span class="cl-tag">No</span>'}${m.vigilar_todas_clases ? ' <span class="cl-tag azul">todas las clases</span>' : ""}${m.alertas_abiertas ? ` <span class="cl-tag mal">${m.alertas_abiertas} alerta(s)</span>` : ""}</td></tr>`).join("")}
      </tbody></table></div><p class="cl-pie">${marcas.length} marca(s). Un click en la fila abre la ficha del cliente.</p>`;
}

/* Marcas agrupadas (una fila por marca y cliente, que se expande y muestra sus actas).
   Se juntan solas las que tienen el mismo nombre (sin importar mayúsculas, acentos, comas o
   espacios), las que repiten el nombre ("LBV BONVIELA LBV BONVIELA…") y las que contienen
   entero el nombre de otra de dos o más palabras ("BS AS TECHNOLOGY" y "BA BS AS TECHNOLOGY").
   Lo que no se junta solo se puede agrupar a mano con «Agrupar…» (queda en el campo grupo_marca).
   Las marcas sin texto (figurativas) no se agrupan: cada acta es su propia fila. */
function palabrasMarca(s) {
  let t = String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim();
  t = t ? t.split(" ") : [];
  for (let p = 1; p <= t.length / 2; p++) {           // "A B A B A B" → "A B"
    if (t.length % p) continue;
    if (t.every((w, i) => w === t[i % p])) return t.slice(0, p);
  }
  return t;
}

function contieneSecuencia(grande, chica) {
  for (let i = 0; i + chica.length <= grande.length; i++) {
    if (chica.every((w, j) => grande[i + j] === w)) return true;
  }
  return false;
}

function gruposDeMarcas(marcas) {
  const porCliente = new Map();
  marcas.forEach(m => { if (!porCliente.has(m.cliente_id)) porCliente.set(m.cliente_id, []); porCliente.get(m.cliente_id).push(m); });
  const claveDe = new Map(), nombreDe = new Map();
  porCliente.forEach((ms, cid) => {
    const reps = [];
    const buscar = pal => reps.find(r => r.pal.join(" ") === pal.join(" ") || (r.pal.length >= 2 && contieneSecuencia(pal, r.pal)));
    // 1) los grupos puestos a mano
    ms.filter(m => (m.grupo_marca || "").trim()).forEach(m => {
      const pal = palabrasMarca(m.grupo_marca);
      let r = reps.find(x => x.pal.join(" ") === pal.join(" "));
      if (!r) { r = { pal, nombre: m.grupo_marca.trim(), clave: `${cid}|${pal.join(" ")}` }; reps.push(r); }
      claveDe.set(m.acta, r.clave); nombreDe.set(r.clave, r.nombre);
    });
    // 2) el resto, de la denominación más corta a la más larga
    ms.filter(m => !(m.grupo_marca || "").trim()).map(m => ({ m, pal: palabrasMarca(m.denominacion) }))
      .sort((a, b) => a.pal.length - b.pal.length)
      .forEach(({ m, pal }) => {
        if (!pal.length) { const k = `${cid}|acta:${m.acta}`; claveDe.set(m.acta, k); nombreDe.set(k, m.denominacion || (m.tipo === "F" ? "(figurativa, sin texto)" : "(pendiente)")); return; }
        let r = buscar(pal);
        if (!r) { r = { pal, nombre: pal.length < palabrasMarca(m.denominacion).length ? pal.join(" ") : m.denominacion.trim(), clave: `${cid}|${pal.join(" ")}` }; reps.push(r); }
        claveDe.set(m.acta, r.clave); if (!nombreDe.has(r.clave)) nombreDe.set(r.clave, r.nombre);
      });
  });
  const grupos = new Map();
  marcas.forEach(m => { const k = claveDe.get(m.acta); if (!grupos.has(k)) grupos.set(k, []); grupos.get(k).push(m); });
  return { grupos, nombreDe };
}

function marcasAgrupadasHtml(marcas) {
  if (!est.gruposAbiertos) est.gruposAbiertos = new Set();
  const { grupos, nombreDe } = gruposDeMarcas(marcas);
  est.clavesGrupos = [...grupos.keys()];
  est.fichasGrupos = [...grupos].map(([k, ms]) => ({ nombre: nombreDe.get(k), actas: ms.map(m => m.acta) }));
  const filas = [...grupos].map(([k, ms], i) => {
    const abierto = est.gruposAbiertos.has(k);
    const nombre = nombreDe.get(k);
    const clases = [...new Set(ms.map(m => m.clase).filter(c => c != null))].sort((a, b) => a - b).join(", ");
    const ultimo = ms.filter(m => m.ultimo_movimiento_fecha).sort((a, b) => String(b.ultimo_movimiento_fecha).localeCompare(String(a.ultimo_movimiento_fecha)))[0];
    const conOpo = ms.filter(m => m.tuvo_oposicion && !["Concedida", "Denegada"].includes(m.estado_tramite)).length;
    const alertas = ms.reduce((t, m) => t + (Number(m.alertas_abiertas) || 0), 0);
    const cab = `<tr class="mk-grupo${abierto ? " abierto" : ""}" data-grupo="${i}">
      <td class="mk-chev-td"><span class="mk-chev" aria-hidden="true">›</span></td>
      <td><span class="mk-nombre">${esc(nombre)}</span>
        ${conOpo ? ` <span class="cl-tag aviso" title="Tiene oposición o vista sin resolver">⚖ ${conOpo}</span>` : ""}${alertas ? ` <span class="cl-tag mal" title="Alertas de vigilancia abiertas">${alertas} alerta(s)</span>` : ""}
        <button type="button" class="cl-btn sec mini" data-ficha="${i}" title="Abrir la ficha de la marca: expedientes, clases, plazos, vigilancia e historial">Abrir ficha</button></td>
      <td class="mk-exp">📄 <strong>${ms.length}</strong> expediente${ms.length === 1 ? "" : "s"}</td>
      <td>${esc(clases || "—")}</td>
      <td>${esc(ms[0].cliente_nombre)}${ms[0].cliente_referido ? "<br>" + tagReferido(ms[0].cliente_referido) : ""}</td>
      <td>${ultimo ? `${crmFecha(ultimo.ultimo_movimiento_fecha)}<br><small class="crm-gris">${esc(ultimo.ultimo_movimiento || "")}</small>` : '<span class="crm-gris">—</span>'}</td></tr>`;
    const filasActas = ms.map(m => `<tr>
        <td><a class="link-acta mk-acta" href="javascript:void(0)" data-abrir="${esc(m.acta)}" title="Abrir el expediente completo en INPI">${esc(m.acta)} ↗</a></td>
        <td class="mk-den">${logoMarca(m)}${esc(m.denominacion || (m.tipo === "F" ? "(figurativa, sin texto)" : "(pendiente)"))}</td>
        <td><strong>${esc(m.clase ?? "")}</strong></td>
        <td>${esc(tipoLegible(m.tipo))}</td>
        <td>${tagEstadoMarca(m)}${m.tuvo_oposicion && !["Concedida", "Denegada"].includes(m.estado_tramite) ? ' <span class="cl-tag aviso">⚖ oposición / vista</span>' : ""}</td>
        <td>${m.fecha_presentacion ? crmFecha(m.fecha_presentacion) : "—"}</td>
        <td>${m.fecha_vencimiento_marca ? crmFecha(m.fecha_vencimiento_marca) : "—"}</td>
        <td><small>${esc(m.agente || (m.matricula_agente ? "matrícula " + m.matricula_agente : "—"))}</small></td>
        <td>${m.vigilar ? '<span class="cl-tag ok">Vigilada</span>' : '<span class="cl-tag">No</span>'}</td>
        <td><a class="link-acta" href="javascript:void(0)" data-ver-cliente="${m.cliente_id}">Ver cliente →</a><br><a class="link-acta" href="javascript:void(0)" data-agrupar="${esc(m.acta)}" data-nombre="${esc(m.grupo_marca || nombre)}" title="Elegir a qué grupo de marcas pertenece esta acta">Agrupar…</a></td></tr>`).join("");
    const det = `<tr class="mk-detalle" ${abierto ? "" : "hidden"}><td colspan="6">
      <div class="mk-sub-wrap"><table class="mk-sub"><colgroup><col style="width:104px"><col><col style="width:50px"><col style="width:104px"><col style="width:150px"><col style="width:96px"><col style="width:96px"><col style="width:120px"><col style="width:84px"><col style="width:96px"></colgroup><thead><tr><th>Acta</th><th>Denominación</th><th>Clase</th><th>Tipo</th><th>Estado</th><th>Presentación</th><th>Vencimiento</th><th>Agente</th><th>Vigilancia</th><th></th></tr></thead>
      <tbody>${filasActas}</tbody></table></div></td></tr>`;
    return cab + det;
  }).join("");
  return `<div class="mk-barra"><span class="cl-pie" style="margin:0">${grupos.size} marca(s) · ${marcas.length} expediente(s)</span>
      <span><button type="button" class="cl-btn sec mini" id="mk-expandir">Expandir todo</button> <button type="button" class="cl-btn sec mini" id="mk-contraer">Contraer todo</button></span></div>
    <div class="mk-tabla-wrap"><table class="mk-tabla"><colgroup><col style="width:28px"><col><col style="width:112px"><col style="width:92px"><col style="width:17%"><col style="width:17%"></colgroup><thead><tr><th></th><th>Marca</th><th>Expedientes</th><th>Clases</th><th>Cliente</th><th>Último movimiento INPI</th></tr></thead>
    <tbody>${filas}</tbody></table></div>
    <p class="cl-pie">Un click en una marca muestra sus actas. El número de acta abre el expediente en INPI y «Ver cliente» abre la ficha del cliente.</p>`;
}

function enlazarMarcasAgrupadas(tabla) {
  const fijar = (tr, abrir) => {
    tr.classList.toggle("abierto", abrir);
    tr.nextElementSibling.hidden = !abrir;
    const k = est.clavesGrupos[+tr.dataset.grupo];
    if (abrir) est.gruposAbiertos.add(k); else est.gruposAbiertos.delete(k);
  };
  tabla.querySelectorAll("tr.mk-grupo").forEach(tr => tr.addEventListener("click", () => fijar(tr, !tr.classList.contains("abierto"))));
  tabla.querySelectorAll("[data-ficha]").forEach(b => b.addEventListener("click", ev => {
    ev.stopPropagation();
    const f = est.fichasGrupos[+b.dataset.ficha];
    abrirFichaMarca(f.nombre, f.actas);
  }));
  $("mk-expandir").addEventListener("click", () => tabla.querySelectorAll("tr.mk-grupo").forEach(tr => fijar(tr, true)));
  $("mk-contraer").addEventListener("click", () => tabla.querySelectorAll("tr.mk-grupo").forEach(tr => fijar(tr, false)));
}

/* ── Modal ──────────────────────────────────────────────────────── */
// URL interna de la ficha: /clientes?cliente=ID (con la vista actual si no es "marcas")
function urlCliente(id) {
  const p = new URLSearchParams();
  if (est.vista && est.vista !== "marcas") p.set("vista", est.vista);
  if (id) p.set("cliente", id);
  const q = p.toString();
  return q ? `/clientes?${q}` : "/clientes";
}
function cerrarModal() {
  $("modal-cliente").classList.remove("abierto"); est.clienteAbierto = null;
  history.replaceState(null, "", urlCliente(null));
}
$("modal-cliente").addEventListener("click", cerrarModal);
document.querySelector("#modal-cliente .modal-cerrar").addEventListener("click", cerrarModal);
document.addEventListener("keydown", ev => { if (ev.key === "Escape") cerrarModal(); });

function camposClienteHtml(c = {}) {
  const origenes = Object.entries(est.origenes).map(([k, v]) => `<option value="${k}" ${k === (c.origen || "cartera") ? "selected" : ""}>${esc(v)}</option>`).join("");
  return `
    <label>Nombre o razón social<input name="nombre" value="${esc(c.nombre || "")}" required></label>
    <label>CUIT<input name="cuit" value="${esc(c.cuit || "")}" placeholder="11 dígitos"></label>
    <label>Email<input name="email" type="email" value="${esc(c.email || "")}"></label>
    <label>Teléfono<input name="telefono" value="${esc(c.telefono || "")}"></label>
    <label>Persona de contacto<input name="contacto" value="${esc(c.contacto || "")}"></label>
    <label>Origen<select name="origen">${origenes}</select></label>
    <label class="cl-referido">¿Quién es el referido? (se muestra como tag)<input name="referido" list="lista-referidos" value="${esc(c.referido || "")}" placeholder="ej.: KOM, agencia de marketing, diseñador…" maxlength="120" autocomplete="off"></label>
    <datalist id="lista-referidos">${(est.referidos || []).map(r => `<option value="${esc(r)}">`).join("")}</datalist>
    <label>Tipo de persona<select name="tipo_persona"><option value="">—</option>${Object.entries(est.tiposPersona).map(([k, v]) => `<option value="${k}" ${k === c.tipo_persona ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
    <label class="check"><input type="checkbox" name="vigilancia_contratada" ${c.vigilancia_contratada ? "checked" : ""}> Tiene contratada la vigilancia (se le cobra y se le manda el informe)</label>
    <details class="cl-mas ancho" ${CAMPOS_EXTRA.some(([k]) => c[k]) ? "open" : ""}><summary>Datos para el trámite (DNI, domicilio, estado civil…)</summary>
      <div class="cl-form">${CAMPOS_EXTRA.map(([k, t]) => `<label>${t}<input name="${k}" value="${esc(c[k] || "")}"></label>`).join("")}</div></details>
    <label class="ancho">Notas internas<textarea name="notas" rows="2">${esc(c.notas || "")}</textarea></label>`;
}

// Datos que completa el cliente en el formulario web (también editables a mano).
const CAMPOS_EXTRA = [
  ["dni", "DNI"], ["nacionalidad", "Nacionalidad"], ["domicilio", "Domicilio"], ["localidad", "Localidad"],
  ["provincia", "Provincia"], ["codigo_postal", "Código postal"], ["domicilio_comercial", "Domicilio comercial"],
  ["estado_civil", "Estado civil"], ["conyuge_nombre", "Cónyuge (nombre y apellido)"], ["conyuge_dni", "DNI del cónyuge"],
  ["firmante_nombre", "Firmante (persona jurídica)"], ["firmante_cargo", "Cargo del firmante"],
];

// Al elegir Origen «Referido» se pide quién es: lleva el cursor al campo del nombre.
document.addEventListener("change", ev => {
  const sel = ev.target;
  if (!sel || sel.name !== "origen" || !sel.form) return;
  const campo = sel.form.elements.referido;
  if (!campo) return;
  campo.closest("label").classList.toggle("destacado", sel.value === "referido" && !campo.value.trim());
  if (sel.value === "referido" && !campo.value.trim()) campo.focus();
});

function leerCampos(form, conActivo = false) {
  const f = form.elements;
  const v = {
    nombre: f.nombre.value.trim(), cuit: f.cuit.value.trim() || null, email: f.email.value.trim() || null,
    telefono: f.telefono.value.trim() || null, contacto: f.contacto.value.trim() || null,
    origen: f.origen.value, notas: f.notas.value.trim() || null,
    referido: f.referido.value.trim() || null,
    vigilancia_contratada: f.vigilancia_contratada.checked,
    tipo_persona: f.tipo_persona.value || null,
  };
  CAMPOS_EXTRA.forEach(([k]) => { v[k] = f[k].value.trim() || null; });
  if (conActivo && f.activo) v.activo = f.activo.checked;
  return v;
}

async function asegurarOrigenes() {
  if (!Object.keys(est.origenes).length || !est.referidos) {
    const r = await api("/api/clientes?activos=true");
    est.origenes = r.origenes; est.referidos = r.referidos || [];
  }
}

async function abrirModalNuevoCliente(precarga = {}, alCrear = null) {
  await asegurarOrigenes();
  $("modal-cliente").classList.add("abierto");
  $("modal-cliente-contenido").innerHTML = `
    <div class="cl-ficha"><h3>Nuevo cliente</h3>
      <form class="cl-form" id="form-nuevo">${camposClienteHtml(precarga)}</form>
      <div class="cl-acciones"><button type="button" class="cl-btn" id="nuevo-guardar">Crear cliente</button>
        <span class="cl-estado" id="nuevo-estado"></span></div>
      <p class="crm-nota">Después de crearlo vas a poder sumarle marcas por número de acta o buscándolas por CUIT.</p></div>`;
  $("nuevo-guardar").addEventListener("click", async () => {
    $("nuevo-estado").textContent = "Guardando…";
    try {
      const r = await apiJson("/api/clientes", "POST", leerCampos($("form-nuevo")));
      cargarResumen();
      if (alCrear) return alCrear(r.cliente);
      pintarClientes();
      abrirCliente(r.cliente.id);
    } catch (e) { $("nuevo-estado").textContent = e.message; $("nuevo-estado").className = "cl-estado mal"; }
  });
}

async function abrirCliente(id) {
  est.clienteAbierto = id;
  history.replaceState(null, "", urlCliente(id));
  $("modal-cliente").classList.add("abierto");
  const cont = $("modal-cliente-contenido");
  if (!cont.querySelector(".cl-ficha") || cont.dataset.id !== String(id)) cont.innerHTML = '<p class="crm-cargando">Cargando…</p>';
  cont.dataset.id = id;
  let d;
  try {
    d = await api(`/api/clientes/${id}`);
  } catch (e) { cont.innerHTML = `<p class="crm-error">No se pudo cargar el cliente (${esc(e.message)}).</p>`; return; }
  est.origenes = d.origenes; est.estadosAlerta = d.estados_alerta; est.referidos = d.referidos || [];
  if (d.tipos_persona) est.tiposPersona = d.tipos_persona;
  const c = d.cliente;
  const scroll = cont.parentElement.scrollTop;
  if (est.clienteTabId !== id) { est.clienteTab = "datos"; est.clienteTabId = id; }

  const marcasHtml = d.marcas.length ? `
    <div style="overflow-x:auto"><table class="cl-tabla cl-marcas"><thead><tr><th>Acta</th><th>Marca</th><th>Clase</th><th>Estado</th><th>Agente</th><th>Vigilancia</th><th></th></tr></thead><tbody>
    ${d.marcas.map(m => `<tr data-acta="${esc(m.acta)}">
      <td><a class="link-acta" href="javascript:void(0)" data-abrir="${esc(m.acta)}">${esc(m.acta)} ↗</a></td>
      <td>${logoMarca(m)}<strong>${esc(m.denominacion || (m.tipo === "F" ? "(figurativa, sin texto)" : "(pendiente)"))}</strong><br><small class="crm-gris">${esc(tipoLegible(m.tipo))}${m.fecha_presentacion ? " · presentada " + crmFecha(m.fecha_presentacion) : ""}${m.fecha_vencimiento_marca ? " · vence " + crmFecha(m.fecha_vencimiento_marca) : ""}</small>
        ${c.referido ? "<br>" + tagReferido(c.referido) : ""}${m.tuvo_oposicion ? '<br><span class="cl-tag aviso">⚖ oposición / vista</span>' : ""}${m.ultimo_movimiento ? `<br><small class="crm-gris">Últ. mov.: ${esc(m.ultimo_movimiento)}${m.ultimo_movimiento_fecha ? " (" + crmFecha(m.ultimo_movimiento_fecha) + ")" : ""}</small>` : ""}
        ${m.error_consulta ? `<br><small class="crm-error" style="padding:0">${esc(m.error_consulta)}</small>` : ""}</td>
      <td>${esc(m.clase ?? "")}</td>
      <td>${tagEstadoMarca(m)}</td>
      <td><small>${esc(m.agente || (m.matricula_agente ? "matrícula " + m.matricula_agente : m.caracter || "—"))}</small></td>
      <td><div class="cl-vig">
        <label><input type="checkbox" data-campo="vigilar" ${m.vigilar ? "checked" : ""}> Vigilar ${m.alertas_abiertas ? `<span class="cl-tag mal">${m.alertas_abiertas}</span>` : ""}</label>
        <label title="Comparar también contra todas las clases, no solo la propia y las relacionadas"><input type="checkbox" data-campo="vigilar_todas_clases" ${m.vigilar_todas_clases ? "checked" : ""}> En todas las clases</label>
        <input type="text" data-campo="terminos_vigilancia" value="${esc(m.terminos_vigilancia || "")}" placeholder="otros nombres a vigilar (coma)" title="Nombres extra para vigilar: variantes, abreviaturas, el nombre comercial…">
      </div></td>
      <td style="white-space:nowrap"><button type="button" class="cl-btn sec mini" data-accion="ficha" title="Abrir la ficha de la marca (con todos sus expedientes)">Ficha</button>
        <button type="button" class="cl-btn sec mini" data-accion="actualizar" title="Volver a consultar el expediente en INPI">↻</button>
        <button type="button" class="cl-btn sec mini" data-accion="mover">Mover</button>
        <button type="button" class="cl-btn peligro mini" data-accion="sacar">Sacar</button></td>
    </tr>`).join("")}</tbody></table></div>` : '<p class="crm-gris">Todavía no hay marcas cargadas.</p>';

  const plazosHtml = d.plazos.length ? `<ul class="crm-plazos">${d.plazos.map(p => `
      <li class="crm-plazo crm-urg-${p.urgencia}"><span class="crm-plazo-fecha">${crmFecha(p.fecha)}<small>${crmUrgencia(p)}</small></span>
        <span class="crm-plazo-texto"><strong>${esc(p.titulo)}</strong>${p.acta ? ` · ${esc(p.marca)} (acta ${esc(p.acta)}, clase ${esc(p.clase)})` : ""}
        ${p.detalle ? `<br><small class="crm-gris">${esc(p.detalle)}</small>` : ""}</span></li>`).join("")}</ul>
      <p class="crm-nota">Fechas orientativas calculadas por el sistema: confirmar siempre en el expediente.</p>` : '<p class="crm-gris">Sin plazos a la vista.</p>';

  const novedadesHtml = d.novedades.length ? `<ul class="cl-lista-simple">${d.novedades.map(n => `
      <li><span class="crm-gris">${fmtFechaHora(n.detectado_en)}</span> · acta ${esc(n.acta)} · ${esc(n.texto)}</li>`).join("")}</ul>` : '<p class="crm-gris">Sin novedades detectadas todavía.</p>';

  const alertasAbiertas = d.alertas.filter(a => ["nueva", "monitorear", "oponer"].includes(a.estado)).length;
  const tabsCli = [["datos", "Datos"], ["poderes", "Poderes", (d.poderes || []).length], ["informes", "Informes", (d.informes || []).length], ["formularios", "Formularios", (d.formularios || []).length],
    ["marcas", "Marcas", d.marcas.length], ["plazos", "Plazos", d.plazos.length], ["alertas", "Alertas", alertasAbiertas],
    ["novedades", "Novedades", d.novedades.length]];

  cont.innerHTML = `
    <div class="cl-ficha">
      <h3>${esc(c.nombre)} ${c.vigilancia_contratada ? '<span class="cl-tag ok">Vigilancia contratada</span>' : '<span class="cl-tag">Vigilancia interna</span>'}${c.activo ? "" : ' <span class="cl-tag">inactivo</span>'}${c.referido ? " " + tagReferido(c.referido) : ""}</h3>
      <div class="crm-gris">Cargado el ${crmFecha(c.alta_en)}${c.alta_por ? " por " + esc(c.alta_por) : ""}${c.clave_crm ? ` · <a href="/titular/${encodeURIComponent(c.clave_crm)}" target="_blank">ver como lead ↗</a>` : ""}</div>

      <div class="fm-tabs" role="tablist">${tabsCli.map(([k, t, n]) => `<button type="button" role="tab" data-ctab="${k}" class="${est.clienteTab === k ? "activa" : ""}">${t}${n ? ` <span class="fm-n">${n}</span>` : ""}</button>`).join("")}</div>

      <div data-cpanel="datos" ${est.clienteTab === "datos" ? "" : "hidden"}>
      <form class="cl-form" id="form-cliente">${camposClienteHtml(c)}
        <label class="check"><input type="checkbox" name="activo" ${c.activo ? "checked" : ""}> Cliente activo (los inactivos no se vigilan)</label></form>
      <div class="cl-acciones">
        <button type="button" class="cl-btn" id="cli-guardar">Guardar datos</button>
        <button type="button" class="cl-btn sec" id="cli-poder-btn">📄 Generar poder para firmar</button>
        <button type="button" class="cl-btn sec" id="cli-unir" title="Pasa las marcas y todo lo demás de otro cliente duplicado a este, y borra el duplicado">🔗 Unir otro cliente a este</button>
        <button type="button" class="cl-btn peligro" id="cli-borrar">Borrar cliente</button>
        <span class="cl-estado" id="cli-estado"></span></div>
      </div>

      <div data-cpanel="poderes" ${est.clienteTab === "poderes" ? "" : "hidden"}>
        <div class="cl-acciones" style="margin-top:12px"><button type="button" class="cl-btn" id="cli-poder-btn2">📄 Generar poder para firmar</button>
          <button type="button" class="cl-btn" id="cli-toma-btn">📑 Generar toma de intervención</button></div>
        <div class="cl-caja" id="cli-poder" hidden style="margin-top:10px"></div>
        <div class="cl-caja" id="cli-toma" hidden style="margin-top:10px"></div>
        <div id="cli-poderes-lista">${d.poderes && d.poderes.length ? "<h4>Poderes generados</h4>" + htmlPoderes(d.poderes) : '<p class="crm-gris">Todavía no se generó ningún poder.</p>'}</div>
      </div>

      <div data-cpanel="informes" ${est.clienteTab === "informes" ? "" : "hidden"}>
        <div class="cl-acciones" style="margin-top:12px"><button type="button" class="cl-btn" id="cli-inf-btn">📄 Generar informe de disponibilidad marcaria</button>
          <button type="button" class="cl-btn sec" id="cli-iai-btn">🧠 Adjuntar informe IA interno</button></div>
        <div class="cl-caja" id="cli-inf" hidden style="margin-top:10px"></div>
        <div class="cl-caja" id="cli-iai" hidden style="margin-top:10px"></div>
        <div id="cli-informes-lista">${htmlInformes(d.informes)}</div>
      </div>

      <div data-cpanel="formularios" ${est.clienteTab === "formularios" ? "" : "hidden"}>
        ${d.formularios && d.formularios.length ? d.formularios.map(f => htmlRespuesta(f, { enFicha: true })).join("") : '<p class="crm-gris">Este cliente no completó ningún formulario.</p>'}
      </div>

      <div data-cpanel="marcas" ${est.clienteTab === "marcas" ? "" : "hidden"}>
      <h4>Sumar marcas</h4>
      <div class="cl-caja">
        <label class="crm-gris" style="display:block;margin-bottom:4px">Números de acta (uno por línea o separados por coma). Se consulta cada uno en INPI y se cargan todos los datos.</label>
        <textarea id="cli-actas" rows="2" placeholder="ej. 3456789, 3456790"></textarea>
        <div class="cl-acciones"><button type="button" class="cl-btn" id="cli-sumar">Sumar actas</button>
          <button type="button" class="cl-btn sec" id="cli-buscar-toggle">Buscar por CUIT o titular en INPI</button>
          <span class="cl-estado" id="cli-sumar-estado"></span></div>
        <div id="cli-buscar" hidden style="margin-top:10px">
          <div class="cl-barra" style="padding:0"><label>CUIT<input id="bt-cuit" value="${esc(c.cuit || "")}"></label>
            <label>o nombre del titular<input id="bt-titular" placeholder="ej. komunikacion"></label>
            <button type="button" class="cl-btn" id="bt-ir">Buscar</button></div>
          <div id="bt-res" class="cl-res-ws"></div></div>
      </div>

      <h4>Marcas en cartera (${d.marcas.length})</h4>
      ${marcasHtml}
      </div>

      <div data-cpanel="plazos" ${est.clienteTab === "plazos" ? "" : "hidden"}><div style="margin-top:12px">${plazosHtml}</div></div>

      <div data-cpanel="alertas" ${est.clienteTab === "alertas" ? "" : "hidden"}><div style="margin-top:12px">
        ${d.alertas.length ? d.alertas.slice(0, 30).map(a => htmlAlerta(a, { sinCliente: true })).join("") : '<p class="crm-gris">Sin alertas.</p>'}</div></div>

      <div data-cpanel="novedades" ${est.clienteTab === "novedades" ? "" : "hidden"}><div style="margin-top:12px">${novedadesHtml}</div></div>
    </div>`;
  cont.parentElement.scrollTop = scroll;
  conectarRespuestas(cont, () => abrirCliente(id));
  const irTab = k => {
    est.clienteTab = k;
    cont.querySelectorAll("[data-ctab]").forEach(b => b.classList.toggle("activa", b.dataset.ctab === k));
    cont.querySelectorAll("[data-cpanel]").forEach(p => { p.hidden = p.dataset.cpanel !== k; });
  };
  cont.querySelectorAll("[data-ctab]").forEach(b => b.addEventListener("click", () => irTab(b.dataset.ctab)));
  const abrirPoder = () => { irTab("poderes"); if ($("cli-poder").hidden) abrirGeneradorPoder(id); };
  $("cli-poder-btn").addEventListener("click", abrirPoder);
  $("cli-poder-btn2").addEventListener("click", abrirPoder);
  $("cli-toma-btn").addEventListener("click", () => abrirGeneradorToma(id));
  $("cli-inf-btn").addEventListener("click", () => abrirCajaInforme(id, "cliente"));
  $("cli-iai-btn").addEventListener("click", () => abrirCajaInforme(id, "interno"));
  conectarPoderes(id);
  conectarInformes(id);
  conectarFichaCliente(cont, d);
}

// Une un cliente duplicado a este: sus marcas, poderes, formularios y alertas pasan acá y el duplicado se borra.
async function unirOtroCliente(c, d) {
  const norm = t => String(t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim();
  const todos = (await api("/api/clientes?activos=false")).clientes;
  const q = prompt(`¿Qué cliente querés unir a «${c.nombre}»? Escribí una parte de su nombre (ese cliente se borra y sus marcas pasan a este).`);
  if (!q || !norm(q)) return;
  const hallados = todos.filter(x => x.id !== c.id && norm(x.nombre).includes(norm(q)));
  if (!hallados.length) { alert("No encontré ningún otro cliente con ese nombre."); return; }
  let otro = hallados[0];
  if (hallados.length > 1) {
    const n = prompt("Hay varios, elegí el número:\n" + hallados.map((x, i) => `${i + 1}) ${x.nombre} — ${x.marcas} marca(s)`).join("\n"), "1");
    otro = hallados[(parseInt(n, 10) || 0) - 1];
    if (!otro) return;
  }
  if (!confirm(`Vas a pasar las ${otro.marcas} marca(s) y los demás datos de «${otro.nombre}» a «${c.nombre}», y «${otro.nombre}» se borra. No se puede deshacer. ¿Seguimos?`)) return;
  const estado = $("cli-estado");
  estado.textContent = "Uniendo…"; estado.className = "cl-estado";
  try {
    let r;
    try { r = await apiJson(`/api/clientes/${c.id}/unir`, "POST", { origen_id: otro.id, forzar: false }); }
    catch (e) {
      if (!String(e.message).includes("CUIT distinto")) throw e;
      if (!confirm(`Los dos clientes tienen CUIT distinto. ¿Seguro que son la misma persona o empresa? Si seguís, se unen igual.`)) { estado.textContent = ""; return; }
      r = await apiJson(`/api/clientes/${c.id}/unir`, "POST", { origen_id: otro.id, forzar: true });
    }
    est.clientes = [];
    const mk = vista("marcas"); if (mk) delete mk.dataset.armado;   // que la lista de marcas se arme de nuevo sin el cliente borrado
    cargarResumen(); pintarClientes();
    await abrirCliente(c.id);
    $("cli-estado").textContent = `Listo: se unieron ${r.marcas} marca(s) ✓`;
  } catch (e) { estado.textContent = e.message; estado.className = "cl-estado mal"; }
}

function conectarFichaCliente(cont, d) {
  const c = d.cliente;
  const estado = $("cli-estado");
  $("cli-guardar").addEventListener("click", async ev => {
    ev.currentTarget.disabled = true; estado.textContent = "Guardando…"; estado.className = "cl-estado";
    try {
      await apiJson(`/api/clientes/${c.id}`, "PUT", leerCampos($("form-cliente"), true));
      cargarResumen(); pintarClientes(); await abrirCliente(c.id);
      $("cli-estado").textContent = "Guardado ✓";
    } catch (e) { ev.currentTarget.disabled = false; estado.textContent = e.message; estado.className = "cl-estado mal"; }
  });
  $("cli-unir").addEventListener("click", () => unirOtroCliente(c, d));
  $("cli-borrar").addEventListener("click", async () => {
    if (!confirm(`¿Borrar a ${c.nombre} y sus ${d.marcas.length} marca(s) de la cartera? No se puede deshacer (para dejar de vigilarlo sin borrar nada, desmarcá «Cliente activo»).`)) return;
    try { await apiJson(`/api/clientes/${c.id}`, "DELETE"); cerrarModal(); cargarResumen(); pintarClientes(); }
    catch (e) { alert(e.message); }
  });

  $("cli-sumar").addEventListener("click", async ev => {
    const actas = [...new Set($("cli-actas").value.split(/[\s,;]+/).map(a => a.replace(/\D/g, "")).filter(Boolean))];
    if (!actas.length) return;
    const boton = ev.currentTarget; boton.disabled = true;
    const log = []; let ok = 0;
    for (let i = 0; i < actas.length; i++) {
      $("cli-sumar-estado").textContent = `Consultando INPI… ${i + 1}/${actas.length}`;
      try {
        const r = await apiJson(`/api/clientes/${c.id}/marcas`, "POST", { acta: actas[i] });
        ok++; if (r.aviso) log.push(`${actas[i]}: ${r.aviso}`);
      } catch (e) { log.push(`${actas[i]}: ${e.message}`); }
    }
    cargarResumen(); pintarClientes();
    await abrirCliente(c.id);
    $("cli-sumar-estado").innerHTML = `${ok} de ${actas.length} sumada(s).${log.length ? "<br>" + log.map(esc).join("<br>") : ""}`;
    if (log.length) $("cli-sumar-estado").className = "cl-estado mal";
  });

  $("cli-buscar-toggle").addEventListener("click", () => { $("cli-buscar").hidden = !$("cli-buscar").hidden; });
  $("bt-ir").addEventListener("click", async ev => {
    const res = $("bt-res"); ev.currentTarget.disabled = true; res.innerHTML = '<p class="crm-cargando">Consultando INPI…</p>';
    try {
      const r = await apiJson(`/api/clientes/${c.id}/buscar-titular`, "POST", { cuit: $("bt-cuit").value, titular: $("bt-titular").value });
      if (!r.resultados.length) res.innerHTML = '<p class="crm-gris">INPI no devolvió marcas para esa búsqueda.</p>';
      else {
        res.innerHTML = `<table class="cl-tabla"><thead><tr><th></th><th>Acta</th><th>Marca</th><th>Clase</th><th>Titular</th><th></th></tr></thead><tbody>
          ${r.resultados.map(x => `<tr><td>${x.en_cartera ? "" : `<input type="checkbox" data-sumar="${esc(x.acta)}" checked>`}</td>
            <td>${esc(x.acta)}</td><td>${esc(x.denominacion || "")}</td><td>${esc(x.clase ?? "")}</td><td>${esc(x.titulares || "")}</td>
            <td>${x.en_cartera ? `<span class="cl-tag">ya está en ${esc(x.cliente)}</span>` : ""}</td></tr>`).join("")}</tbody></table>
          <div class="cl-acciones"><button type="button" class="cl-btn" id="bt-sumar">Sumar las marcadas</button></div>`;
        $("bt-sumar").addEventListener("click", () => {
          $("cli-actas").value = [...res.querySelectorAll("[data-sumar]:checked")].map(i => i.dataset.sumar).join(", ");
          $("cli-sumar").click();
        });
      }
    } catch (e) { res.innerHTML = `<p class="crm-error">${esc(e.message)}</p>`; }
    ev.currentTarget.disabled = false;
  });

  cont.querySelectorAll("[data-abrir]").forEach(a => a.addEventListener("click", () => abrirActa(a.dataset.abrir)));

  cont.querySelectorAll(".cl-marcas tr[data-acta]").forEach(tr => {
    const acta = tr.dataset.acta;
    tr.querySelectorAll("[data-campo]").forEach(inp => inp.addEventListener("change", async () => {
      const campo = inp.dataset.campo;
      const valor = inp.type === "checkbox" ? inp.checked : inp.value.trim();
      try { await apiJson(`/api/cartera/marcas/${encodeURIComponent(acta)}`, "PUT", { [campo]: valor }); inp.style.outline = "2px solid var(--verde)"; setTimeout(() => { inp.style.outline = ""; }, 900); }
      catch (e) { alert(e.message); }
    }));
    tr.querySelector('[data-accion="ficha"]').addEventListener("click", () => {
      const { grupos, nombreDe } = gruposDeMarcas(d.marcas);
      const clave = [...grupos].find(([, ms]) => ms.some(m => m.acta === acta));
      abrirFichaMarca(clave ? nombreDe.get(clave[0]) : acta, clave ? clave[1].map(m => m.acta) : [acta]);
    });
    tr.querySelector('[data-accion="actualizar"]').addEventListener("click", async ev => {
      ev.currentTarget.disabled = true;
      try {
        const r = await apiJson(`/api/cartera/marcas/${encodeURIComponent(acta)}/actualizar`, "POST");
        if (r.aviso) alert(r.aviso);
        else if (r.novedades.length) alert("Novedades:\n• " + r.novedades.join("\n• "));
        await abrirCliente(c.id);
      } catch (e) { alert(e.message); ev.currentTarget.disabled = false; }
    });
    tr.querySelector('[data-accion="mover"]').addEventListener("click", async () => {
      const r = await api("/api/clientes?activos=true");
      const otros = r.clientes.filter(x => x.id !== c.id);
      if (!otros.length) return alert("No hay otros clientes.");
      const lista = otros.map((x, i) => `${i + 1}) ${x.nombre}`).join("\n");
      const n = parseInt(prompt(`¿A qué cliente se mueve el acta ${acta}?\n\n${lista}\n\nEscribí el número:`), 10);
      if (!n || !otros[n - 1]) return;
      try { await apiJson(`/api/cartera/marcas/${encodeURIComponent(acta)}/mover`, "POST", { cliente_id: otros[n - 1].id }); cargarResumen(); pintarClientes(); await abrirCliente(c.id); }
      catch (e) { alert(e.message); }
    });
    tr.querySelector('[data-accion="sacar"]').addEventListener("click", async () => {
      if (!confirm(`¿Sacar el acta ${acta} de la cartera de ${c.nombre}?`)) return;
      try { await apiJson(`/api/cartera/marcas/${encodeURIComponent(acta)}`, "DELETE"); cargarResumen(); pintarClientes(); await abrirCliente(c.id); }
      catch (e) { alert(e.message); }
    });
  });
  conectarAlertas(cont, () => abrirCliente(c.id));
}

/* ── Alertas de vigilancia ──────────────────────────────────────── */
function htmlAlerta(a, { sinCliente = false } = {}) {
  const abierta = ["nueva", "monitorear", "oponer"].includes(a.estado);
  const otro = a.tipo === "otro_agente";
  const agente = a.agente_nuevo ? ` · ${esc(a.agente_nuevo)}` : "";
  const plazo = !otro && a.plazo_oposicion
    ? `<div class="vg-plazo ${a.dias_oposicion !== null && a.dias_oposicion <= 7 ? "urgente" : ""}">⚠ ${a.dias_oposicion >= 0 ? "Posible oposición: tenés 30 días corridos desde la publicación (Art. 13, Ley 22.362), hasta el" : "El plazo de oposición (30 días corridos desde la publicación, Art. 13, Ley 22.362) cerró el"} <strong>${crmFecha(a.plazo_oposicion)}</strong> (${crmUrgencia({ dias: a.dias_oposicion })})</div>`
    : (!otro && abierta ? '<div class="vg-plazo vg-temprana">ℹ Detección temprana: todavía no fue publicada en el boletín, no corre plazo. Los 30 días corridos para oponerse (Art. 13, Ley 22.362) empiezan cuando se publique.</div>' : "");
  const REL = { misma: ["mal", "Misma clase", "La marca nueva está en la misma clase que la del cliente"],
    relacionada: ["aviso", "Clase conexa", "Clase distinta pero relacionada (tabla de Ajustes → Clases relacionadas)"],
    distinta: ["", "Clase distinta", "Clases sin relación: alerta porque el nombre es casi idéntico o la marca se vigila en todas las clases"] };
  const rel = !otro && REL[a.relacion_clases] ? `<span class="cl-tag ${REL[a.relacion_clases][0]}" title="${esc(REL[a.relacion_clases][2])}">${REL[a.relacion_clases][1]}</span>` : "";
  const botones = otro
    ? [["monitorear", "Lo sigo"], ["opuesta", "Ya lo hablé"], ["descartada", "Descartar"]]
    : [["monitorear", "Monitorear"], ["oponer", "Hay que oponer"], ["opuesta", "Oposición presentada"], ["descartada", "Descartar"]];
  return `
    <div class="vg-card ${otro ? "vg-otro" : "vg-" + esc(a.nivel)} ${abierta ? "" : "vg-cerrada"}" data-alerta="${a.id}">
      <div class="vg-cab">
        ${otro ? '<span class="cl-tag azul">🔀 OTRO AGENTE</span>' : `${tagNivel(a.nivel)} <span class="vg-puntaje">${a.puntaje}%</span> ${rel}${a.clase_nueva != null ? ` <span class="cl-tag" title="Clase de la solicitud nueva">Clase ${esc(a.clase_nueva)}</span>` : ""}`}
        <span class="cl-tag ${abierta ? "azul" : ""}">${esc(est.estadosAlerta[a.estado] || a.estado)}</span>
        ${sinCliente ? "" : `<a href="javascript:void(0)" data-cliente="${a.cliente_id}"><strong>${esc(a.cliente_nombre || "(cliente borrado)")}</strong></a>
          ${a.vigilancia_contratada ? '<span class="cl-tag ok">Contratada</span>' : '<span class="cl-tag" title="No paga la vigilancia: oportunidad para ofrecerla">Sin contratar</span>'}`}
        <span class="crm-gris" style="margin-left:auto">${fmtFechaHora(a.creada_en)}</span>
      </div>
      ${otro ? `<div><strong>${esc(a.titular_nuevo || "El cliente")}</strong> presentó <strong>${esc(a.denominacion_nueva || "una marca")}</strong> (clase ${esc(a.clase_nueva ?? "?")}, acta
            <a class="link-acta" href="javascript:void(0)" data-abrir="${esc(a.acta_nueva)}">${esc(a.acta_nueva)} ↗</a>)${agente}.
          <div class="vg-motivos">Un cliente nuestro presentó una marca nueva por otro lado: es el momento de hablar con él (¿la gestiona otro estudio? ¿la presentó solo?).</div></div>`
      : `<div class="vg-cols">
        <div class="vg-col"><small>Marca del cliente</small><strong>${esc(a.denominacion_cliente || "")}</strong>
          <div class="crm-gris">clase ${esc(a.clase_cliente ?? "?")} · acta <a class="link-acta" href="javascript:void(0)" data-abrir="${esc(a.acta_cliente)}">${esc(a.acta_cliente)} ↗</a></div></div>
        <div class="vg-col"><small>Solicitud nueva</small><strong>${esc(a.denominacion_nueva || "(sin nombre)")}</strong>
          <div class="crm-gris">clase ${esc(a.clase_nueva ?? "?")} · acta <a class="link-acta" href="javascript:void(0)" data-abrir="${esc(a.acta_nueva)}">${esc(a.acta_nueva)} ↗</a>
            · ${esc(tipoLegible(a.tipo_nuevo))}<br>Titular: ${esc(a.titular_nuevo || "?")}${agente}<br>${esc(a.fuente_nueva || "")}${a.fecha_presentacion_nueva ? " · presentada " + crmFecha(a.fecha_presentacion_nueva) : ""}</div></div>
      </div><div class="vg-motivos">${esc(a.motivos || "")}</div>`}
      ${plazo}
      <div class="vg-acciones">
        ${botones.filter(([e]) => e !== a.estado).map(([e, t]) => `<button type="button" class="cl-btn ${e === "descartada" ? "peligro" : "sec"} mini" data-estado="${e}">${t}</button>`).join("")}
        ${a.estado !== "nueva" && abierta === false ? `<button type="button" class="cl-btn sec mini" data-estado="nueva">Reabrir</button>` : ""}
        <input class="cl-inline-input" data-nota placeholder="nota (se guarda al salir del campo)" value="${esc(a.nota || "")}">
      </div>
      ${a.decidido_por ? `<div class="crm-gris" style="font-size:11px;margin-top:4px">Último cambio: ${esc(a.decidido_por)} ${fmtFechaHora(a.decidido_en)}</div>` : ""}
    </div>`;
}

function conectarAlertas(cont, recargar) {
  cont.querySelectorAll(".vg-card").forEach(card => {
    const id = card.dataset.alerta;
    card.querySelectorAll("[data-estado]").forEach(b => b.addEventListener("click", async () => {
      try { await apiJson(`/api/cartera/alertas/${id}`, "POST", { estado: b.dataset.estado }); cargarResumen(); await recargar(); }
      catch (e) { alert(e.message); }
    }));
    const nota = card.querySelector("[data-nota]");
    nota.addEventListener("change", async () => {
      try { await apiJson(`/api/cartera/alertas/${id}`, "POST", { nota: nota.value }); nota.style.outline = "2px solid var(--verde)"; setTimeout(() => { nota.style.outline = ""; }, 900); }
      catch (e) { alert(e.message); }
    });
    card.querySelectorAll("[data-abrir]").forEach(a => a.addEventListener("click", () => abrirActa(a.dataset.abrir)));
    card.querySelectorAll("[data-cliente]").forEach(a => a.addEventListener("click", () => a.dataset.cliente && abrirCliente(+a.dataset.cliente)));
  });
}

async function pintarVigilancia() {
  const cont = vista("vigilancia");
  if (!cont.dataset.armado) {
    cont.dataset.armado = "1";
    cont.innerHTML = `
      <div class="cl-vista">
        <div class="cl-caja" style="margin-top:12px">
          <strong>Probar un nombre</strong> <span class="crm-gris">— qué solicitudes conocidas se parecen a una marca (para chequear una marca nueva antes de presentarla)</span>
          <div class="cl-barra" style="padding:8px 0 0"><label>Nombre<input id="pr-nombre" style="min-width:220px"></label>
            <label>Clase (opcional)<input id="pr-clase" type="number" min="1" max="45" style="width:90px"></label>
            <button type="button" class="cl-btn" id="pr-ir">Buscar parecidas</button></div>
          <div id="pr-res"></div></div>
        <details class="cl-caja" id="vg-marcas-det" style="margin-top:12px">
          <summary><strong>Qué se vigila</strong> <span class="crm-gris">— marcas vigiladas, umbral de parecido de cada una y alertas abiertas</span></summary>
          <div id="vg-marcas" style="margin-top:8px"></div></details>
        <div class="cl-barra">
          <label>Estado<select id="vg-estado"></select></label>
          <label>Nivel<select id="vg-nivel"><option value="">Todos</option><option value="alta">Alta</option><option value="media">Media</option><option value="baja">Baja</option></select></label>
          <label>Tipo<select id="vg-tipo"><option value="">Todos</option><option value="similitud">Parecidos</option><option value="otro_agente">Otro agente</option></select></label>
          <label>Cliente<select id="vg-cliente"><option value="">Todos</option></select></label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="vg-sc"> Solo clientes sin vigilancia contratada</label>
        </div>
        <div id="vg-lista"></div>
      </div>`;
    const r = await api("/api/clientes?activos=true"); est.clientes = r.clientes; est.origenes = r.origenes;
    $("vg-cliente").innerHTML += r.clientes.map(c => `<option value="${c.id}">${esc(c.nombre)}</option>`).join("");
    const aj = await api("/api/cartera/alertas?estado=abiertas&cliente_id=-1"); est.estadosAlerta = aj.estados;
    $("vg-estado").innerHTML = `<option value="abiertas">Abiertas</option>` + Object.entries(aj.estados).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("") + `<option value="">Todas</option>`;
    ["vg-estado", "vg-nivel", "vg-tipo", "vg-cliente", "vg-sc"].forEach(id => $(id).addEventListener("change", pintarVigilancia));
    $("vg-marcas-det").addEventListener("toggle", () => { if ($("vg-marcas-det").open) pintarVigilanciaMarcas(); });
    $("pr-ir").addEventListener("click", probarNombre);
    $("pr-nombre").addEventListener("keydown", ev => { if (ev.key === "Enter") probarNombre(); });
  }
  const p = new URLSearchParams();
  p.set("estado", $("vg-estado").value);
  if ($("vg-nivel").value) p.set("nivel", $("vg-nivel").value);
  if ($("vg-tipo").value) p.set("tipo", $("vg-tipo").value);
  if ($("vg-cliente").value) p.set("cliente_id", $("vg-cliente").value);
  if ($("vg-sc").checked) p.set("sin_contratar", "true");
  const lista = $("vg-lista");
  try {
    const r = await api(`/api/cartera/alertas?${p}`);
    est.estadosAlerta = r.estados;
    lista.innerHTML = r.alertas.length ? r.alertas.map(a => htmlAlerta(a)).join("") + `<p class="cl-pie">${r.alertas.length} alerta(s).</p>`
      : '<p class="vacio">No hay alertas con estos filtros. La vigilancia corre todos los días: cuando aparezca una solicitud parecida a una marca de la cartera, va a figurar acá.</p>';
    conectarAlertas(lista, pintarVigilancia);
    if ($("vg-marcas-det").open) pintarVigilanciaMarcas();
  } catch (e) { lista.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`; }
}

/* Tabla «Qué se vigila»: una fila por marca vigilada, con su umbral (editable) y sus alertas abiertas. */
async function pintarVigilanciaMarcas() {
  const cont = $("vg-marcas");
  if (!est.vgMarcas) cont.innerHTML = '<p class="crm-cargando">Cargando…</p>';
  try {
    const r = await api("/api/cartera/vigilancia/marcas");
    est.vgMarcas = r;
    const o = est.vgOrden || (est.vgOrden = { col: "denominacion", dir: 1 });
    const valor = m => o.col === "alertas_abiertas" ? Number(m.alertas_abiertas) : o.col === "umbral" ? (m.umbral_vigilancia || r.umbral_general)
      : String(m[o.col] ?? "").toLowerCase();
    const marcas = [...r.marcas].sort((a, b) => (valor(a) > valor(b) ? 1 : valor(a) < valor(b) ? -1 : 0) * o.dir);
    const th = (col, t) => `<th class="vg-orden" data-orden="${col}">${t}${o.col === col ? (o.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
    cont.innerHTML = `<p class="crm-gris" style="margin:0 0 6px">Umbral general (Ajustes): <strong>${r.umbral_general}%</strong> · Última corrida: <strong>${r.ultima_vigilancia ? fmtFechaHora(r.ultima_vigilancia) : "todavía no corrió"}</strong> (la vigilancia corre todos los días).${r.conceptos_pendientes ? ` · <strong>${r.conceptos_pendientes}</strong> marca(s) todavía sin conceptos${r.ia_disponible ? " (la IA los arma sola, de a tandas cada 20 minutos)" : " (la IA no está configurada: falta IA_API_KEY)"}.` : ""}</p>
      ${marcas.length ? `<div style="overflow-x:auto"><table class="cl-tabla"><thead><tr>${th("denominacion", "Marca")}${th("clase", "Clase")}${th("cliente_nombre", "Cliente")}${th("umbral", "Umbral")}<th>Otros nombres</th><th title="Traducciones y sinónimos: si una solicitud nueva nombra uno, se alerta como «mismo concepto». Se corrigen en la ficha de la marca.">Conceptos</th>${th("alertas_abiertas", "Alertas")}</tr></thead><tbody>
      ${marcas.map(m => `<tr data-acta="${esc(m.acta)}"><td><a href="javascript:void(0)" data-ficha-acta="${esc(m.acta)}" data-nombre="${esc(m.denominacion || m.acta)}"><strong>${esc(m.denominacion || "(sin nombre)")}</strong></a><br><small class="crm-gris">acta ${esc(m.acta)}</small></td>
        <td>${esc(m.clase ?? "")}${m.vigilar_todas_clases ? ' <span class="cl-tag azul" title="Se compara contra todas las clases">todas</span>' : ""}</td>
        <td><a href="javascript:void(0)" data-cliente="${m.cliente_id}">${esc(m.cliente_nombre)}</a></td>
        <td><input type="number" min="40" max="100" data-umbral value="${m.umbral_vigilancia ?? ""}" placeholder="${r.umbral_general}" style="width:72px" title="Vacío = el general (${r.umbral_general}%)"> %${m.umbral_vigilancia ? "" : ' <small class="crm-gris">general</small>'}</td>
        <td><small>${esc(m.terminos_vigilancia || "—")}</small></td>
        <td><small>${esc(m.conceptos_vigilancia || "—")}</small></td>
        <td>${m.alertas_abiertas ? `<span class="cl-tag mal">${m.alertas_abiertas}</span>` : "—"}</td></tr>`).join("")}</tbody></table></div>
      <p class="cl-pie">${marcas.length} marca(s) vigilada(s). El umbral propio de una marca reemplaza al general; se aplica en la próxima corrida.</p>` : '<p class="vacio">No hay marcas vigiladas.</p>'}`;
    cont.querySelectorAll("[data-orden]").forEach(h => h.addEventListener("click", () => {
      o.dir = o.col === h.dataset.orden ? -o.dir : 1; o.col = h.dataset.orden; pintarVigilanciaMarcas();
    }));
    cont.querySelectorAll("[data-cliente]").forEach(a => a.addEventListener("click", () => abrirCliente(+a.dataset.cliente)));
    cont.querySelectorAll("[data-ficha-acta]").forEach(a => a.addEventListener("click", () => abrirFichaMarca(a.dataset.nombre, [a.dataset.fichaActa], "vigilancia")));
    cont.querySelectorAll("tr[data-acta] [data-umbral]").forEach(inp => inp.addEventListener("change", async () => {
      const valor = inp.value.trim() === "" ? null : parseInt(inp.value, 10);
      try {
        await apiJson(`/api/cartera/marcas/${encodeURIComponent(inp.closest("tr").dataset.acta)}`, "PUT", { umbral_vigilancia: valor });
        pintarVigilanciaMarcas();
      } catch (e) { alert(e.message); }
    }));
  } catch (e) { cont.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`; }
}

async function probarNombre() {
  const res = $("pr-res"); const nombre = $("pr-nombre").value.trim();
  if (nombre.length < 2) return;
  res.innerHTML = '<p class="crm-cargando">Buscando…</p>';
  try {
    const r = await apiJson("/api/cartera/probar", "POST", { nombre, clase: $("pr-clase").value ? +$("pr-clase").value : null });
    res.innerHTML = r.resultados.length ? `<div style="overflow-x:auto"><table class="cl-tabla" style="margin-top:8px"><thead><tr><th>Parecido</th><th>Marca</th><th>Clase</th><th>Acta</th><th>Titular</th><th>Dónde está</th></tr></thead><tbody>
      ${r.resultados.map(x => `<tr><td>${tagNivel(x.nivel)} ${x.puntaje}%<br><small class="crm-gris">${esc(x.motivos || "")}</small></td><td><strong>${esc(x.nombre)}</strong></td><td>${esc(x.clase ?? "")}</td>
        <td><a class="link-acta" href="javascript:void(0)" data-abrir="${esc(x.acta)}">${esc(x.acta)} ↗</a></td><td>${esc(x.titular || "")}</td>
        <td>${x.boletin ? "boletín " + esc(x.boletin) : "pre-boletín"}${x.fecha_publicacion ? " · publicada " + crmFecha(x.fecha_publicacion) : ""}</td></tr>`).join("")}</tbody></table></div>
      <p class="crm-nota">Solo ve lo que pasó por los boletines procesados y el escaneo de actas: no es una búsqueda de anterioridades en toda la base de INPI.</p>`
      : '<p class="crm-gris" style="margin-top:8px">No hay nada parecido entre las solicitudes que conocemos.</p>';
    res.querySelectorAll("[data-abrir]").forEach(a => a.addEventListener("click", () => abrirActa(a.dataset.abrir)));
  } catch (e) { res.innerHTML = `<p class="crm-error">${esc(e.message)}</p>`; }
}

/* ── Vencimientos ───────────────────────────────────────────────── */
async function pintarVencimientos() {
  const cont = vista("vencimientos");
  cont.innerHTML = '<div class="cl-vista"><p class="crm-cargando">Cargando…</p></div>';
  try {
    const r = await api("/api/cartera/agenda");
    const grupos = [
      ["Vencidos o de hoy", p => p.dias <= 0], ["Próximos 7 días", p => p.dias > 0 && p.dias <= 7],
      ["Próximos 30 días", p => p.dias > 7 && p.dias <= 30], ["Próximos 90 días", p => p.dias > 30 && p.dias <= 90],
      ["Más adelante", p => p.dias > 90],
    ];
    const hechos = new Set();
    let html = "";
    grupos.forEach(([titulo, f]) => {
      const items = r.plazos.filter(f);
      if (!items.length) return;
      html += `<div class="cl-venc-sec"><h4>${titulo} (${items.length})</h4>${items.map(p => `
        <div class="cl-venc u-${p.urgencia}" data-cliente="${p.cliente_id}">
          <span class="f">${crmFecha(p.fecha)}<small>${crmUrgencia(p)}</small></span>
          <span><strong>${esc(p.titulo)}</strong><br><span class="crm-gris">${esc(p.cliente || "")}${p.marca ? " · " + esc(p.marca) : ""}${p.clase ? " (clase " + esc(p.clase) + ")" : ""}${p.acta ? " · acta " + esc(p.acta) : ""}</span>
          ${p.detalle ? `<br><small class="crm-gris">${esc(p.detalle)}</small>` : ""}</span></div>`).join("")}</div>`;
    });
    cont.innerHTML = `<div class="cl-vista">${html || '<p class="vacio">No hay plazos a la vista en la cartera.</p>'}
      <p class="crm-nota">Incluye renovaciones, DJ de uso de medio término, cierre de oposiciones de terceros, oposiciones recibidas y los plazos para oponerse a marcas parecidas detectadas por la vigilancia. Fechas orientativas: confirmar siempre en el expediente.</p></div>`;
    cont.querySelectorAll("[data-cliente]").forEach(el => el.addEventListener("click", () => el.dataset.cliente && el.dataset.cliente !== "null" && abrirCliente(+el.dataset.cliente)));
  } catch (e) { cont.innerHTML = `<div class="cl-vista"><p class="crm-error">No se pudo cargar (${esc(e.message)}).</p></div>`; }
}

/* ── Por matrícula ──────────────────────────────────────────────── */
async function pintarMatricula() {
  const cont = vista("matricula");
  cont.innerHTML = '<div class="cl-vista"><p class="crm-cargando">Cargando…</p></div>';
  try {
    const [r, cl] = await Promise.all([api("/api/cartera/por-matricula"), api("/api/clientes?activos=true")]);
    est.clientes = cl.clientes; est.origenes = cl.origenes;
    $("badge-matricula").textContent = r.grupos.length ? String(r.grupos.length) : "";
    const chips = r.matriculas.map(m => `<span class="cl-chip">${esc(m)}<button type="button" data-borrar-mat="${esc(m)}" title="Quitar">×</button></span>`).join("");
    const opcionesClientes = cl.clientes.map(c => `<option value="${c.id}">${esc(c.nombre)}</option>`).join("");
    cont.innerHTML = `<div class="cl-vista">
      <div class="cl-caja" style="margin-top:12px"><strong>Matrículas del estudio</strong>
        <p class="crm-gris" style="margin:4px 0">Todo lo que se presente con estas matrículas aparece acá. Si el CUIT es de un cliente, entra solo a su cartera; si no, queda como propuesta para que decidas.</p>
        <div class="cl-chips">${chips || '<span class="crm-gris">Todavía no cargaste ninguna.</span>'}</div>
        <div class="cl-barra" style="padding:0"><label>Matrícula<input id="mat-num" style="width:130px" placeholder="número"></label>
          <label>Nombre (opcional)<input id="mat-nom" placeholder="ej. Socio"></label>
          <button type="button" class="cl-btn" id="mat-add">Agregar</button></div></div>
      <h4 style="margin:18px 0 6px">Propuestas para sumar a la cartera (${r.total_marcas} marca(s) de ${r.grupos.length} titular(es))</h4>
      ${r.grupos.length ? "" : '<p class="vacio">No hay propuestas pendientes.</p>'}
      ${r.grupos.map((g, i) => `<div class="cl-grupo" data-g="${i}">
        <h4>${esc(g.titular || "(sin titular)")} <span class="crm-gris" style="font-weight:400">${g.cuit ? "CUIT " + esc(g.cuit) : "sin CUIT"}</span>
          ${g.clientes_posibles.length ? g.clientes_posibles.map(c => `<span class="cl-tag ok">cliente: ${esc(c.nombre)}</span>`).join(" ") : ""}</h4>
        <div style="overflow-x:auto"><table class="cl-tabla"><tbody>${g.marcas.map(m => `<tr><td style="width:24px"><input type="checkbox" checked data-acta="${esc(m.acta)}"></td>
          <td>${esc(m.acta)}</td><td><strong>${esc(m.denominacion_inpi || "(sin nombre)")}</strong></td><td>clase ${esc(m.clase ?? "?")}</td>
          <td>mat. ${esc(m.matricula_agente)}</td><td>${m.fecha_presentacion ? crmFecha(m.fecha_presentacion) : ""}</td><td>${m.boletin ? "boletín " + esc(m.boletin) : "pre-boletín"}</td></tr>`).join("")}</tbody></table></div>
        <div class="cl-acciones"><select data-cliente-sel><option value="">Sumar a un cliente existente…</option>${opcionesClientes}</select>
          <button type="button" class="cl-btn sec" data-ir-existente>Sumar</button>
          <button type="button" class="cl-btn" data-ir-nuevo>Crear cliente nuevo con estos datos</button>
          <button type="button" class="cl-btn peligro" data-descartar>No es de cartera (descartar)</button></div></div>`).join("")}</div>`;
    $("mat-add").addEventListener("click", async () => {
      try { await apiJson("/api/cartera/matriculas", "POST", { matricula: $("mat-num").value, nombre: $("mat-nom").value }); pintarMatricula(); cargarResumen(); }
      catch (e) { alert(e.message); }
    });
    cont.querySelectorAll("[data-borrar-mat]").forEach(b => b.addEventListener("click", async () => {
      if (!confirm(`¿Quitar la matrícula ${b.dataset.borrarMat}?`)) return;
      await pedir(() => apiJson(`/api/cartera/matriculas/${encodeURIComponent(b.dataset.borrarMat)}`, "DELETE")); pintarMatricula();
    }));
    cont.querySelectorAll(".cl-grupo").forEach(div => {
      const g = r.grupos[+div.dataset.g];
      const actas = () => [...div.querySelectorAll("[data-acta]:checked")].map(i => i.dataset.acta);
      const despues = () => { cargarResumen(); pintarMatricula(); pintarClientes(); };
      div.querySelector("[data-ir-existente]").addEventListener("click", async () => {
        const id = div.querySelector("[data-cliente-sel]").value;
        if (!id) return alert("Elegí el cliente al que se suman.");
        if (!actas().length) return;
        try { const x = await apiJson("/api/cartera/por-matricula/asignar", "POST", { actas: actas(), cliente_id: +id }); despues(); alert(`${x.sumadas} marca(s) sumada(s).`); }
        catch (e) { alert(e.message); }
      });
      div.querySelector("[data-ir-nuevo]").addEventListener("click", async () => {
        if (!actas().length) return;
        await abrirModalNuevoCliente({ nombre: g.titular || "", cuit: g.cuit || "", origen: "cartera" }, async cliente => {
          try { await apiJson("/api/cartera/por-matricula/asignar", "POST", { actas: actas(), cliente_id: cliente.id }); cerrarModal(); despues(); abrirCliente(cliente.id); }
          catch (e) { alert(e.message); }
        });
      });
      div.querySelector("[data-descartar]").addEventListener("click", async () => {
        if (!actas().length || !confirm(`¿Descartar ${actas().length} marca(s)? No vuelven a proponerse.`)) return;
        await pedir(() => apiJson("/api/cartera/por-matricula/descartar", "POST", { actas: actas() })); despues();
      });
    });
  } catch (e) { cont.innerHTML = `<div class="cl-vista"><p class="crm-error">No se pudo cargar (${esc(e.message)}).</p></div>`; }
}

/* ── Ajustes ────────────────────────────────────────────────────── */
async function pintarAjustes() {
  const cont = vista("ajustes");
  cont.innerHTML = '<div class="cl-vista"><p class="crm-cargando">Cargando…</p></div>';
  try {
    const r = await api("/api/cartera/ajustes");
    const c = r.config;
    cont.innerHTML = `<div class="cl-vista">
      <h4 style="margin:16px 0 6px">Sensibilidad de la vigilancia</h4>
      <div class="cl-caja"><form class="cl-form" id="aj-form">
        <label>Puntaje mínimo para crear una alerta<input type="number" name="umbral" min="40" max="100" value="${esc(c.umbral)}"></label>
        <label>Nivel «media» desde<input type="number" name="nivel_media" min="40" max="100" value="${esc(c.nivel_media)}"></label>
        <label>Nivel «alta» desde<input type="number" name="nivel_alta" min="40" max="100" value="${esc(c.nivel_alta)}"></label>
        <label>Comparar contra solicitudes de los últimos (meses)<input type="number" name="meses_universo" min="1" max="60" value="${esc(c.meses_universo)}"></label></form>
        <div class="cl-acciones"><button type="button" class="cl-btn" id="aj-guardar">Guardar</button><span class="cl-estado" id="aj-estado"></span></div>
        <p class="crm-nota">Bajar el puntaje mínimo genera más alertas (más falsos positivos). Un cambio se aplica desde la próxima corrida de la vigilancia; las alertas ya creadas no se tocan.</p></div>

      <h4 style="margin:18px 0 6px">Poder: datos del apoderado</h4>
      <div class="cl-caja"><p class="crm-gris" style="margin:0 0 6px">Va en el poder después de «Por el presente otorgo a…». Dejalo vacío y guardá para volver al texto original.</p>
        <textarea id="aj-apoderado" rows="3"></textarea>
        <div class="cl-acciones"><button type="button" class="cl-btn" id="aj-apoderado-guardar">Guardar</button><span class="cl-estado" id="aj-apoderado-estado"></span></div></div>

      <h4 style="margin:18px 0 6px">Clases relacionadas</h4>
      <p class="crm-gris" style="margin:0 0 4px">Clases de Niza que suelen chocar entre sí (por ejemplo indumentaria 25 y su venta, 35). Una marca parecida en una clase relacionada alerta con un descuento chico; en una clase sin relación solo alerta si es casi idéntica (o si la marca se vigila «en todas las clases»).</p>
      <div class="cl-chips">${r.clases_relacionadas.map(p => `<span class="cl-chip">${p.clase_a} ↔ ${p.clase_b}<button type="button" data-quitar-par="${p.clase_a},${p.clase_b}" title="Quitar">×</button></span>`).join("")}</div>
      <div class="cl-barra" style="padding:0"><label>Clase<input id="par-a" type="number" min="1" max="45" style="width:80px"></label>
        <label>Clase<input id="par-b" type="number" min="1" max="45" style="width:80px"></label>
        <button type="button" class="cl-btn sec" id="par-add">Agregar par</button></div>

      <h4 style="margin:18px 0 6px">Palabras genéricas</h4>
      <p class="crm-gris" style="margin:0 0 4px">Palabras que no distinguen una marca (GRUPO, SERVICIOS, ARGENTINA…): el sistema las ignora al comparar, para no alertar por «GRUPO ALFA» vs «GRUPO BETA».</p>
      <div class="cl-chips">${r.palabras_genericas.map(p => `<span class="cl-chip">${esc(p)}<button type="button" data-quitar-pal="${esc(p)}" title="Quitar">×</button></span>`).join("")}</div>
      <div class="cl-barra" style="padding:0"><label>Palabra<input id="pal-new" style="width:180px"></label>
        <button type="button" class="cl-btn sec" id="pal-add">Agregar</button></div></div>`;
    api("/api/poderes-apoderado").then(a => { $("aj-apoderado").value = a.texto; }).catch(() => {});
    $("aj-apoderado-guardar").addEventListener("click", async () => {
      const e = $("aj-apoderado-estado");
      try { await apiJson("/api/poderes-apoderado", "PUT", { texto: $("aj-apoderado").value }); e.textContent = "Guardado."; e.className = "cl-estado";
            const a = await api("/api/poderes-apoderado"); $("aj-apoderado").value = a.texto; }
      catch (err) { e.textContent = err.message; e.className = "cl-estado mal"; }
    });
    $("aj-guardar").addEventListener("click", async () => {
      const f = $("aj-form").elements; const e = $("aj-estado");
      try {
        await apiJson("/api/cartera/ajustes", "PUT", { umbral: +f.umbral.value, nivel_media: +f.nivel_media.value, nivel_alta: +f.nivel_alta.value, meses_universo: +f.meses_universo.value });
        e.textContent = "Guardado ✓"; e.className = "cl-estado";
      } catch (x) { e.textContent = x.message; e.className = "cl-estado mal"; }
    });
    $("par-add").addEventListener("click", async () => {
      try { await apiJson("/api/cartera/ajustes/clases", "POST", { clase_a: +$("par-a").value, clase_b: +$("par-b").value }); pintarAjustes(); } catch (e) { alert(e.message); }
    });
    cont.querySelectorAll("[data-quitar-par]").forEach(b => b.addEventListener("click", async () => {
      const [a, bb] = b.dataset.quitarPar.split(",");
      await pedir(() => apiJson(`/api/cartera/ajustes/clases?clase_a=${a}&clase_b=${bb}`, "DELETE")); pintarAjustes();
    }));
    $("pal-add").addEventListener("click", async () => {
      try { await apiJson("/api/cartera/ajustes/palabras", "POST", { palabra: $("pal-new").value }); pintarAjustes(); } catch (e) { alert(e.message); }
    });
    cont.querySelectorAll("[data-quitar-pal]").forEach(b => b.addEventListener("click", async () => {
      await pedir(() => apiJson(`/api/cartera/ajustes/palabras?palabra=${encodeURIComponent(b.dataset.quitarPal)}`, "DELETE")); pintarAjustes();
    }));
  } catch (e) { cont.innerHTML = `<div class="cl-vista"><p class="crm-error">No se pudo cargar (${esc(e.message)}).</p></div>`; }
}


/* ── Formularios para clientes ─────────────────────────────────── */
function tamanoLegible(b) { return b >= 1024 * 1024 ? (b / 1024 / 1024).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; }

/* ── Poder para firmar ─────────────────────────────────────────── */
async function bajarArchivo(url, opciones, nombreDefecto) {
  const r = await fetch(url, { credentials: "include", ...opciones });
  if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
  if (!r.ok) {
    let d = `${r.status}`; try { d = (await r.json()).detail || d; } catch (_) {}
    throw new Error(d);
  }
  const cd = r.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)/);
  const nombre = m ? decodeURIComponent(m[1]) : nombreDefecto;
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = nombre;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

function htmlPoderes(lista) {
  if (!lista || !lista.length) return "";
  return `<ul class="cl-lista-simple">${lista.map(p => `<li data-poder="${p.id}">
      <span class="crm-gris">${fmtFechaHora(p.generado_en)}${p.generado_por ? " · " + esc(p.generado_por) : ""}</span> ·
      ${esc((p.datos.otorgante || "").slice(0, 60))} · lugar y fecha: ${esc(p.datos.lugar)}, ${crmFecha(p.datos.fecha)}
      <button type="button" class="cl-btn sec mini" data-bajar-poder="pdf">PDF</button>
      <button type="button" class="cl-btn sec mini" data-bajar-poder="docx">Word</button>
      <button type="button" class="cl-btn peligro mini" data-borrar-poder title="Saca este poder de la lista">×</button></li>`).join("")}</ul>`;
}

async function abrirGeneradorPoder(clienteId) {
  const caja = $("cli-poder");
  if (!caja.hidden) { caja.hidden = true; return; }
  caja.hidden = false;
  caja.innerHTML = '<p class="crm-cargando">Preparando…</p>';
  let r;
  try { r = await api(`/api/clientes/${clienteId}/poder`); }
  catch (e) { caja.innerHTML = `<p class="crm-error">No se pudo preparar (${esc(e.message)}).</p>`; return; }
  const p = r.propuesta;
  const faltan = [];
  if (!p.otorgante.match(/domicili/)) faltan.push("domicilio");
  if (p.tipo_persona === "fisica" && !p.otorgante.match(/DNI/)) faltan.push("DNI");
  caja.innerHTML = `
    <p class="crm-gris" style="margin:0 0 8px">Se arma con el modelo del estudio y los datos del cliente. Revisá el texto (por ejemplo «domiciliado» / «domiciliada») y descargalo para mandárselo a firmar. La aclaración (y el cargo, si es empresa) salen en blanco: los completa a mano quien firma.</p>
    ${faltan.length ? `<p class="crm-error" style="padding:0 0 8px">Ojo: falta ${faltan.join(", ")} en la ficha. Podés completarlo acá abajo o en «Datos para el trámite».</p>` : ""}
    <form class="cl-form" id="poder-form" style="margin-top:0">
      <label>Tipo<select name="tipo_persona">${Object.entries(est.tiposPersona).map(([k, v]) => `<option value="${k}" ${k === p.tipo_persona ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      <label>Lugar<input name="lugar" value="${esc(p.lugar)}"></label>
      <label>Fecha<input type="date" name="fecha" value="${esc(p.fecha)}"></label>
      <label class="ancho">El abajo firmante…<textarea name="otorgante" rows="3">${esc(p.otorgante)}</textarea></label>
    </form>
    <p class="crm-nota">Apoderado: ${esc(r.apoderado)} <a href="/clientes?vista=ajustes" title="Se cambia en Ajustes">(cambiar)</a></p>
    <div class="cl-acciones"><button type="button" class="cl-btn" data-generar="pdf">Descargar PDF</button>
      <button type="button" class="cl-btn sec" data-generar="docx">Descargar Word</button>
      <span class="cl-estado" id="poder-estado"></span></div>`;
  const form = $("poder-form");
  caja.querySelectorAll("[data-generar]").forEach(b => b.addEventListener("click", async () => {
    const f = form.elements;
    const datos = { tipo_persona: f.tipo_persona.value, otorgante: f.otorgante.value, lugar: f.lugar.value,
                    fecha: f.fecha.value };
    const e = $("poder-estado"); e.className = "cl-estado"; e.textContent = "Generando…"; b.disabled = true;
    try {
      await bajarArchivo(`/api/clientes/${clienteId}/poder?formato=${b.dataset.generar}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos) }, `Poder.${b.dataset.generar}`);
      e.textContent = "Listo: se descargó y quedó en «Poderes generados».";
      const d = await api(`/api/clientes/${clienteId}`);
      $("cli-poderes-lista").innerHTML = "<h4>Poderes generados</h4>" + htmlPoderes(d.poderes);
      conectarPoderes(clienteId);
    } catch (err) { e.textContent = err.message; e.className = "cl-estado mal"; }
    b.disabled = false;
  }));
}

// Toma de intervención: un documento por marca. Las marcas con oposición abierta vienen tildadas.
async function abrirGeneradorToma(clienteId) {
  const caja = $("cli-toma");
  if (!caja.hidden) { caja.hidden = true; return; }
  caja.hidden = false;
  caja.innerHTML = '<p class="crm-cargando">Preparando…</p>';
  let r;
  try { r = await api(`/api/clientes/${clienteId}/toma-intervencion`); }
  catch (e) { caja.innerHTML = `<p class="crm-error">No se pudo preparar (${esc(e.message)}).</p>`; return; }
  if (!r.marcas.length) { caja.innerHTML = '<p class="crm-gris">Este cliente no tiene marcas cargadas.</p>'; return; }
  const hayOp = r.marcas.some(m => m.con_oposicion);
  caja.innerHTML = `
    <p class="crm-gris" style="margin:0 0 8px">Se genera <strong>un documento por marca</strong> con el modelo del estudio. ${hayOp ? "Vienen tildadas las marcas con oposición abierta." : "Ninguna marca tiene oposición abierta: tildá las que necesites."} Podés corregir los datos antes de bajarlos; si elegís varias, se baja un ZIP.</p>
    <div style="overflow-x:auto"><table class="cl-tabla"><thead><tr><th></th><th>Acta</th><th>Marca</th><th>Clase</th><th>Tipo</th><th>Solicitante</th><th>Fecha</th></tr></thead><tbody>
    ${r.marcas.map((m, i) => `<tr data-i="${i}">
      <td><input type="checkbox" data-sel ${m.con_oposicion ? "checked" : ""}></td>
      <td><input data-k="acta" value="${esc(m.acta)}" size="9"></td>
      <td><input data-k="marca" value="${esc(m.marca)}">${m.con_oposicion ? '<br><span class="cl-tag aviso">⚖ oposición / vista</span>' : ""}</td>
      <td><input data-k="clase" value="${esc(m.clase)}" size="3"></td>
      <td><input data-k="tipo" value="${esc(m.tipo)}" size="12"></td>
      <td><input data-k="solicitante" value="${esc(m.solicitante)}"></td>
      <td><input type="date" data-k="fecha" value="${esc(m.fecha)}"></td></tr>`).join("")}</tbody></table></div>
    <div class="cl-acciones"><button type="button" class="cl-btn" data-toma="docx">Descargar Word</button>
      <button type="button" class="cl-btn sec" data-toma="pdf">Descargar PDF</button>
      <span class="cl-estado" id="toma-estado"></span></div>`;
  caja.querySelectorAll("[data-toma]").forEach(b => b.addEventListener("click", async () => {
    const items = [...caja.querySelectorAll("tr[data-i]")].filter(tr => tr.querySelector("[data-sel]").checked).map(tr => {
      const o = {}; tr.querySelectorAll("[data-k]").forEach(i => { o[i.dataset.k] = i.value; }); return o; });
    const e = $("toma-estado"); e.className = "cl-estado";
    if (!items.length) { e.textContent = "Tildá al menos una marca."; e.className = "cl-estado mal"; return; }
    e.textContent = "Generando…"; b.disabled = true;
    try {
      await bajarArchivo(`/api/clientes/${clienteId}/toma-intervencion?formato=${b.dataset.toma}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) }, "Toma_de_intervencion");
      e.textContent = items.length > 1 ? `Listo: se descargó un ZIP con ${items.length} documentos.` : "Listo: se descargó.";
    } catch (err) { e.textContent = err.message; e.className = "cl-estado mal"; }
    b.disabled = false;
  }));
}

function conectarPoderes(clienteId) {
  const cont = $("cli-poderes-lista");
  if (!cont) return;
  cont.querySelectorAll("[data-bajar-poder]").forEach(b => b.addEventListener("click", () =>
    pedir(() => bajarArchivo(`/api/poderes/${b.closest("[data-poder]").dataset.poder}?formato=${b.dataset.bajarPoder}`, {}, "Poder." + b.dataset.bajarPoder))));
  cont.querySelectorAll("[data-borrar-poder]").forEach(b => b.addEventListener("click", async () => {
    await pedir(() => apiJson(`/api/poderes/${b.closest("[data-poder]").dataset.poder}`, "DELETE"));
    b.closest("li").remove();
  }));
}

/* ── Informes (viabilidad marcaria para el cliente + informe IA interno) ── */
function htmlInformes(lista) {
  if (!lista || !lista.length) return '<p class="crm-gris" style="margin-top:12px">Todavía no hay informes. Pegá el JSON que generó la IA con alguno de los dos botones.</p>';
  const cli = lista.filter(i => i.tipo === "cliente"), inte = lista.filter(i => i.tipo === "interno");
  const quien = i => `<span class="crm-gris">${fmtFechaHora(i.generado_en)}${i.generado_por ? " · " + esc(i.generado_por) : ""}</span>`;
  let h = "";
  if (cli.length) h += `<h4>Informes preliminares (para el cliente)</h4><ul class="cl-lista-simple">${cli.map(i => `<li data-informe="${i.id}">
      ${quien(i)} · <strong>${esc(i.marca || "")}</strong>${i.referencia ? ` · <span class="cl-tag">${esc(i.referencia)}</span>` : ""}${i.fecha_informe ? " · fecha del informe " + esc(i.fecha_informe) : ""}${i.formato === 1 ? ' <span class="crm-gris">(formato anterior)</span>' : ""}
      <a class="cl-btn mini" style="text-decoration:none;display:inline-block" href="/informes/${i.id}" target="_blank" rel="noopener" title="Abre el PDF en una pestaña nueva, con su propio link">Abrir link ↗</a>
      <button type="button" class="cl-btn sec mini" data-inf-pdf>PDF</button>
      <button type="button" class="cl-btn sec mini" data-inf-json title="Bajar el JSON con el que se armó">JSON</button>
      <button type="button" class="cl-btn peligro mini" data-inf-borrar title="Borrar este informe">×</button></li>`).join("")}</ul>`;
  if (inte.length) h += `<h4>Informes IA internos</h4><ul class="cl-lista-simple">${inte.map(i => {
    const r = i.resumen || {};
    return `<li data-informe="${i.id}">
      ${quien(i)} · <strong>${esc(i.marca || "")}</strong>${r.referencia ? ` · <span class="cl-tag">${esc(r.referencia)}</span>` : ""} · ${r.identicas ? `<span class="cl-tag mal">${r.identicas} coincidencia(s) exacta(s)</span>` : '<span class="cl-tag ok">sin coincidencias exactas</span>'}
      · ${(r.relevantes || []).length} antecedente(s) relevante(s)${r.antecedentes_listados ? ` · ${r.antecedentes_listados} listados` : ""}
      <a class="cl-btn mini" style="text-decoration:none;display:inline-block" href="/informes/${i.id}" target="_blank" rel="noopener" title="Abre el informe completo en una pestaña nueva, con su propio link">Abrir link ↗</a>
      <button type="button" class="cl-btn sec mini" data-inf-ver>Ver resumen</button>
      <button type="button" class="cl-btn sec mini" data-inf-json title="Bajar el JSON completo">JSON</button>
      <button type="button" class="cl-btn peligro mini" data-inf-borrar title="Borrar este informe">×</button>
      <div class="cl-caja" data-inf-detalle hidden style="margin-top:8px">
        ${r.fecha_consulta ? `<p class="crm-gris" style="margin:0 0 6px">Consulta a INPI: ${esc(r.fecha_consulta)}</p>` : ""}
        ${r.observaciones ? `<p style="margin:0 0 8px"><strong>Para revisar:</strong> ${esc(r.observaciones)}</p>` : ""}
        ${(r.relevantes || []).length ? `<div style="overflow-x:auto"><table class="cl-tabla"><thead><tr><th>Acta</th><th>Marca</th><th>Clase</th><th>Titular</th><th>Vigencia</th><th>Por qué importa</th></tr></thead><tbody>
          ${r.relevantes.map(a => `<tr><td>${a.acta ? `<a class="link-acta" href="javascript:void(0)" data-inf-acta="${esc(a.acta)}" data-nombre="${esc(a.denominacion)}">${esc(a.acta)} ↗</a>` : ""}</td><td>${esc(a.denominacion)}</td><td>${esc(a.clase)}</td><td>${esc(a.titular)}</td><td>${esc(a.vigencia)}</td><td><small>${esc(a.por_que_es_relevante)}</small></td></tr>`).join("")}
          </tbody></table></div>` : ""}
        ${(r.limitaciones || []).length ? `<p class="crm-nota" style="margin-top:8px">Limitaciones: ${r.limitaciones.map(esc).join(" · ")}</p>` : ""}
      </div></li>`; }).join("")}</ul>`;
  return h;
}

function conectarInformes(clienteId) {
  const cont = $("cli-informes-lista");
  if (!cont) return;
  const iid = b => b.closest("[data-informe]").dataset.informe;
  cont.querySelectorAll("[data-inf-pdf]").forEach(b => b.addEventListener("click", () =>
    pedir(() => bajarArchivo(`/api/informes/${iid(b)}/pdf`, {}, "Informe_Preliminar.pdf"))));
  cont.querySelectorAll("[data-inf-json]").forEach(b => b.addEventListener("click", () =>
    pedir(() => bajarArchivo(`/api/informes/${iid(b)}/json`, {}, "Informe.json"))));
  cont.querySelectorAll("[data-inf-ver]").forEach(b => b.addEventListener("click", () => {
    const det = b.closest("li").querySelector("[data-inf-detalle]");
    det.hidden = !det.hidden; b.textContent = det.hidden ? "Ver resumen" : "Ocultar";
  }));
  cont.querySelectorAll("[data-inf-borrar]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("¿Borrar este informe? No se puede deshacer.")) return;
    await pedir(() => apiJson(`/api/informes/${iid(b)}`, "DELETE"));
    recargarInformes(clienteId);
  }));
  cont.querySelectorAll("[data-inf-acta]").forEach(a => a.addEventListener("click", () => {
    abrirActa(a.dataset.infActa);
  }));
}

async function recargarInformes(clienteId) {
  const r = await api(`/api/clientes/${clienteId}/informes`);
  $("cli-informes-lista").innerHTML = htmlInformes(r.informes);
  const n = document.querySelector('[data-ctab="informes"]');
  if (n) n.innerHTML = `Informes${r.informes.length ? ` <span class="fm-n">${r.informes.length}</span>` : ""}`;
  conectarInformes(clienteId);
}

// Vista previa local del JSON pegado (el panel lo vuelve a validar al guardar).
function previaInforme(texto, tipo) {
  const t = texto.trim().replace(/^```[a-zA-Z]*\s*|\s*```$/g, "");
  if (!t) return { ok: false, html: "" };
  let d;
  try { d = JSON.parse(t); } catch (e) { return { ok: false, html: `<span class="crm-error" style="padding:0">No es un JSON válido: ${esc(e.message)}</span>` }; }
  if (tipo === "cliente") {
    if (d.tipo === "informe_viabilidad_interno") return { ok: false, html: '<span class="crm-error" style="padding:0">Ese es el JSON interno: va en «Adjuntar informe IA interno».</span>' };
    const e = d.encabezado || {}, sec = Array.isArray(d.secciones) ? d.secciones : [];
    // Formato 2 (disponibilidad, desde el 28/10/2026): trae título, referencia INFPR y firma en líneas.
    const v2 = Number(d.version || 0) >= 2 || "titulo" in d || "referencia" in e || typeof e.web === "object";
    const textos = JSON.stringify(v2 ? [d.titulo, sec] : [e.actividad, e.antecedentes, e.logo, d.introduccion, sec]);
    const clase = textos.match(/\bclases?\s*(n[°ºo.]*\s*)?\d+/i);
    const corchete = v2 && textos.match(/\[[A-ZÁÉÍÓÚÑ ]{3,}\]/);
    const sinTitulo = v2 && sec.some(s => s && typeof s === "object" && !String(s.titulo || "").trim());
    const ref = e.referencia && !/^INFPR-?$/i.test(String(e.referencia).trim()) ? esc(e.referencia) : "se asigna sola al generar";
    return { ok: !!e.marca_pretendida && sec.length > 0 && !clase && !corchete && !sinTitulo, html:
      `Marca: <strong>${esc(e.marca_pretendida || "— falta —")}</strong> · fecha ${esc(e.fecha || e.fecha_informe || (v2 ? "hoy" : "—"))}` +
      (v2 ? ` · referencia ${ref}` : ' · <span class="crm-gris">formato anterior</span>') + ` · ${sec.length} sección(es)` +
      (clase ? `<br><span class="crm-error" style="padding:0">Menciona «${esc(clase[0])}»: el informe al cliente no puede mostrar números de clase.</span>` : "") +
      (corchete ? `<br><span class="crm-error" style="padding:0">Quedó «${esc(corchete[0])}» sin reemplazar.</span>` : "") +
      (sinTitulo ? '<br><span class="crm-error" style="padding:0">Hay una sección sin título: en este formato todas llevan título.</span>' : "") };
  }
  if (d.tipo === "informe_viabilidad_cliente" || (d.secciones && d.encabezado)) return { ok: false, html: '<span class="crm-error" style="padding:0">Ese es el JSON para el cliente: va en «Generar informe de disponibilidad marcaria».</span>' };
  const marca = (d.caso || {}).marca_pretendida || d.marca_pretendida || "";
  const b = d.busqueda_inpi || {};
  const exactas = (b.coincidencias_exactas || b.identicas || []).length;
  return { ok: !!marca, html: `Marca: <strong>${esc(marca || "— falta caso.marca_pretendida —")}</strong> · ${exactas} coincidencia(s) exacta(s) · ${(b.antecedentes_relevantes || []).length} antecedente(s) relevante(s)` };
}

function abrirCajaInforme(clienteId, tipo) {
  const caja = $(tipo === "cliente" ? "cli-inf" : "cli-iai");
  const otra = $(tipo === "cliente" ? "cli-iai" : "cli-inf");
  otra.hidden = true;
  if (!caja.hidden) { caja.hidden = true; return; }
  caja.hidden = false;
  caja.innerHTML = tipo === "cliente" ? `
    <p class="crm-gris" style="margin:0 0 8px">Pegá el JSON <strong>para el cliente</strong> que generó la IA (skill «informe-viabilidad-marcaria», archivo <em>…_cliente.json</em>). El panel arma el PDF «Informe preliminar de disponibilidad marcaria» con el formato aprobado (modelo ATELIER ASTRANITY) y lo guarda acá. Si el JSON no trae la <strong>referencia INFPR</strong>, el panel asigna la siguiente; si es una corrección de la misma marca, conserva la que ya tenía. No deja generarlo si el texto menciona un número de clase o usa expresiones que los criterios no permiten.</p>
    <textarea id="inf-texto" rows="8" spellcheck="false" placeholder='{ "tipo": "informe_viabilidad_cliente", "encabezado": { … }, "secciones": [ … ] }' style="font-family:ui-monospace,monospace;font-size:12px"></textarea>
    <div class="crm-gris" id="inf-previa" style="margin-top:6px"></div>
    <div class="cl-acciones"><button type="button" class="cl-btn" id="inf-ir" disabled>Generar PDF</button>
      <span class="cl-estado" id="inf-estado"></span></div>` : `
    <p class="crm-gris" style="margin:0 0 8px">Pegá el JSON <strong>interno</strong> que generó la IA (archivo <em>…_interno.json</em>): búsquedas, antecedentes y observaciones. Queda guardado en la ficha para el análisis completo; no se manda al cliente.</p>
    <textarea id="inf-texto-i" rows="8" spellcheck="false" placeholder='{ "tipo": "informe_viabilidad_interno", "caso": { … }, "busqueda_inpi": { … } }' style="font-family:ui-monospace,monospace;font-size:12px"></textarea>
    <div class="crm-gris" id="inf-previa-i" style="margin-top:6px"></div>
    <div class="cl-acciones"><button type="button" class="cl-btn" id="inf-ir-i" disabled>Guardar informe interno</button>
      <span class="cl-estado" id="inf-estado-i"></span></div>`;
  const suf = tipo === "cliente" ? "" : "-i";
  const ta = $("inf-texto" + suf), previa = $("inf-previa" + suf), ir = $("inf-ir" + suf), estado = $("inf-estado" + suf);
  ta.addEventListener("input", () => { const p = previaInforme(ta.value, tipo); previa.innerHTML = p.html; ir.disabled = !p.ok; });
  ta.focus();
  ir.addEventListener("click", async () => {
    estado.className = "cl-estado"; estado.textContent = tipo === "cliente" ? "Generando…" : "Guardando…"; ir.disabled = true;
    try {
      const r = await apiJson(`/api/clientes/${clienteId}/informes/${tipo}`, "POST", { texto: ta.value });
      if (tipo === "cliente") await bajarArchivo(`/api/informes/${r.id}/pdf`, {}, r.archivo || "Informe_Preliminar.pdf");
      estado.textContent = tipo === "cliente" ? `Listo${r.referencia ? " (" + r.referencia + ")" : ""}: se descargó el PDF y quedó guardado abajo.` : "Listo: quedó guardado abajo.";
      ta.value = ""; previa.innerHTML = "";
      await recargarInformes(clienteId);
    } catch (e) { estado.textContent = e.message; estado.className = "cl-estado mal"; ir.disabled = false; }
  });
}

function htmlRespuesta(f, { enFicha = false } = {}) {
  const archivos = f.archivos.length ? `<div class="fm-archivos">${f.archivos.map(a => `
      <span class="fm-archivo"><span class="crm-gris">${esc(a.etiqueta)}:</span>
        <a href="/api/formularios/archivos/${a.id}?ver=1" target="_blank" rel="noopener">${esc(a.nombre)}</a>
        <small class="crm-gris">(${tamanoLegible(a.tamano)})</small>
        <a href="/api/formularios/archivos/${a.id}" title="Descargar">⬇</a></span>`).join("")}</div>` : "";
  const filas = f.respuestas.map(r => `<tr><td>${esc(r.etiqueta)}</td><td>${esc(r.valor).replace(/\n/g, "<br>")}</td></tr>`).join("");
  const aviso = f.aviso_enviado_en ? `mail enviado ${fmtFechaHora(f.aviso_enviado_en)}`
    : f.aviso_error ? `<span class="crm-error" style="padding:0" title="${esc(f.aviso_error)}">mail pendiente (se reintenta solo)</span>` : "mail pendiente (se envía en minutos)";
  return `<div class="fm-resp cl-caja" data-resp="${f.id}">
    <div class="fm-cab"><strong>${esc(f.tipo_legible)}</strong> · recibido ${fmtFechaHora(f.recibido_en)}
      ${f.estado === "nueva" ? '<span class="cl-tag azul">Nueva</span>' : f.estado === "archivada" ? '<span class="cl-tag">Archivada</span>' : `<span class="cl-tag ok" title="${esc(f.revisado_por || "")}">Revisada</span>`}
      ${f.cliente_nuevo ? '<span class="cl-tag">creó el cliente</span>' : '<span class="cl-tag">actualizó datos del cliente</span>'}
      <small class="crm-gris">· ${aviso}</small></div>
    <details ${enFicha ? "" : "open"}><summary>Ver respuestas${f.archivos.length ? ` y ${f.archivos.length} archivo(s)` : ""}</summary>
      <table class="fm-tabla">${filas}</table>${archivos}</details>
    <div class="cl-acciones">
      ${f.estado === "nueva" ? '<button type="button" class="cl-btn sec mini" data-fm="revisada">✓ Marcar revisada</button>' : f.estado === "revisada" ? '<button type="button" class="cl-btn sec mini" data-fm="nueva">Volver a «nueva»</button>' : ""}
      ${f.estado === "archivada" ? '<button type="button" class="cl-btn sec mini" data-fm="revisada">Desarchivar</button>' : '<button type="button" class="cl-btn sec mini" data-fm="archivada" title="La saca de la lista; se ve en «Ver archivados».">Archivar</button>'}
      <button type="button" class="cl-btn peligro mini" data-fm="borrar" title="Borra la respuesta y sus archivos. El cliente queda.">Borrar respuesta</button></div>
  </div>`;
}

function conectarRespuestas(cont, recargar) {
  cont.querySelectorAll("[data-fm]").forEach(b => b.addEventListener("click", async () => {
    const id = b.closest("[data-resp]").dataset.resp;
    const acc = b.dataset.fm;
    if (acc === "borrar" && !confirm("¿Borrar esta respuesta y sus archivos? El cliente y sus datos quedan como están.")) return;
    b.disabled = true;
    try {
      if (acc === "borrar") await apiJson(`/api/formularios/${id}`, "DELETE");
      else await apiJson(`/api/formularios/${id}/estado`, "POST", { estado: acc });
      cargarBadgeFormularios();
      recargar();
    } catch (e) { alert(e.message); b.disabled = false; }
  }));
}

async function cargarBadgeFormularios() {
  try { const r = await api("/api/formularios?estado=nueva"); $("badge-formularios").textContent = r.nuevas ? String(r.nuevas) : ""; } catch (_) {}
}

async function pintarFormularios() {
  const cont = vista("formularios");
  if (!cont.dataset.armado) {
    cont.dataset.armado = "1";
    cont.innerHTML = `<div class="cl-vista">
      <div id="fm-links"></div>
      <div class="cl-barra" style="margin-top:14px"><label>Mostrar<select id="fm-estado">
        <option value="">Todas</option><option value="nueva" selected>Nuevas (sin revisar)</option><option value="revisada">Revisadas</option><option value="archivada">Archivadas</option></select></label>
        <button type="button" class="cl-btn sec mini" id="fm-ver-arch"></button></div>
      <div id="fm-lista"></div></div>`;
    $("fm-estado").addEventListener("change", pintarFormularios);
    $("fm-ver-arch").addEventListener("click", () => {
      $("fm-estado").value = $("fm-estado").value === "archivada" ? "nueva" : "archivada";
      pintarFormularios();
    });
  }
  const lista = $("fm-lista");
  try {
    const r = await api(`/api/formularios?estado=${$("fm-estado").value}`);
    $("badge-formularios").textContent = r.nuevas ? String(r.nuevas) : "";
    const verArch = $("fm-estado").value === "archivada";
    $("fm-ver-arch").textContent = verArch ? "← Volver a los formularios" : `🗄 Ver archivados (${r.archivadas})`;
    $("fm-links").innerHTML = `<div class="fm-links">${r.formularios.map(f => {
      const url = location.origin + f.ruta;
      return `<div class="cl-caja fm-link"><div><strong>${esc(f.tipo)}</strong><br><small class="crm-gris">${esc(f.titulo)}</small></div>
        <input readonly value="${esc(url)}" onclick="this.select()">
        <div class="cl-acciones" style="margin-top:0"><button type="button" class="cl-btn mini" data-copiar="${esc(url)}">Copiar link</button>
          <a class="cl-btn sec mini" href="${esc(url)}" target="_blank" rel="noopener">Abrir</a>
          <a class="cl-btn sec mini" href="https://wa.me/?text=${encodeURIComponent("Hola! Te paso el formulario para iniciar el registro de tu marca: " + url)}" target="_blank" rel="noopener">WhatsApp</a></div></div>`;
    }).join("")}</div>
      <p class="cl-pie">Estos links son públicos: el cliente los abre sin usuario ni clave. Al enviarlo se crea el cliente con todos sus datos (o, si ya existía uno con ese CUIT, se le actualizan) y llega un mail a marcas@komunikacion.com.ar con las respuestas y los archivos.</p>`;
    $("fm-links").querySelectorAll("[data-copiar]").forEach(b => b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copiar); b.textContent = "¡Copiado!"; }
      catch (_) { b.closest(".fm-link").querySelector("input").select(); document.execCommand("copy"); b.textContent = "¡Copiado!"; }
      setTimeout(() => { b.textContent = "Copiar link"; }, 1800);
    }));
    if (!r.respuestas.length) {
      lista.innerHTML = `<p class="vacio">${verArch ? "No hay formularios archivados." : $("fm-estado").value === "nueva" ? "No hay formularios nuevos sin revisar." : "Todavía no llegó ningún formulario."}</p>`;
      return;
    }
    lista.innerHTML = `<div style="overflow-x:auto"><table class="cl-tabla"><thead><tr><th>Recibido</th><th>Tipo</th><th>Titular</th><th>CUIT</th><th>Marca</th><th>Archivos</th><th>Estado</th><th></th></tr></thead><tbody>
      ${r.respuestas.map(f => `<tr data-resp="${f.id}">
        <td>${fmtFechaHora(f.recibido_en)}</td>
        <td>${esc(est.tiposPersona[f.tipo_persona] || f.tipo_persona)}</td>
        <td><strong>${esc(f.nombre || "")}</strong><br><small class="crm-gris">${esc(f.email || "")}</small></td>
        <td>${esc(f.cuit || "")}</td>
        <td>${esc(f.marca || "—")}</td>
        <td>${f.archivos ? "📎 " + f.archivos : "—"}</td>
        <td>${f.estado === "nueva" ? '<span class="cl-tag azul">Nueva</span>' : f.estado === "archivada" ? '<span class="cl-tag">Archivada</span>' : '<span class="cl-tag ok">Revisada</span>'}
          ${f.cliente_nuevo ? "" : '<br><small class="crm-gris">ya era cliente</small>'}</td>
        <td style="white-space:nowrap">${f.cliente_id ? `<button type="button" class="cl-btn mini" data-ver-cliente="${f.cliente_id}">Ver cliente</button>` : '<small class="crm-gris">cliente borrado</small>'}
          <button type="button" class="cl-btn sec mini" data-ver-resp="${f.id}">Respuestas</button>
          <button type="button" class="cl-btn sec mini" data-fm-fila="${f.estado === "archivada" ? "revisada" : "archivada"}">${f.estado === "archivada" ? "Desarchivar" : "Archivar"}</button>
          <button type="button" class="cl-btn peligro mini" data-fm-fila="borrar" title="Borra la respuesta y sus archivos. El cliente queda.">Borrar</button></td></tr>`).join("")}
      </tbody></table></div><p class="cl-pie">${r.respuestas.length} formulario(s).</p>`;
    lista.querySelectorAll("[data-ver-cliente]").forEach(b => b.addEventListener("click", () => abrirCliente(+b.dataset.verCliente)));
    lista.querySelectorAll("[data-ver-resp]").forEach(b => b.addEventListener("click", () => abrirRespuesta(+b.dataset.verResp)));
    lista.querySelectorAll("[data-fm-fila]").forEach(b => b.addEventListener("click", async () => {
      const id = b.closest("[data-resp]").dataset.resp, acc = b.dataset.fmFila;
      if (acc === "borrar" && !confirm("¿Borrar esta respuesta y sus archivos? El cliente y sus datos quedan como están.")) return;
      b.disabled = true;
      try {
        if (acc === "borrar") await apiJson(`/api/formularios/${id}`, "DELETE");
        else await apiJson(`/api/formularios/${id}/estado`, "POST", { estado: acc });
        pintarFormularios();
      } catch (e) { alert(e.message); b.disabled = false; }
    }));
  } catch (e) {
    lista.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`;
  }
}

async function abrirRespuesta(id) {
  $("modal-cliente").classList.add("abierto");
  const cont = $("modal-cliente-contenido");
  cont.dataset.id = "";
  cont.innerHTML = '<p class="crm-cargando">Cargando…</p>';
  try {
    const f = await api(`/api/formularios/${id}`);
    cont.innerHTML = `<div class="cl-ficha"><h3>${esc(f.datos.nombre || "Formulario")}</h3>
      ${f.cliente_id ? `<div class="cl-acciones" style="margin-top:0"><button type="button" class="cl-btn sec" id="fm-ir-cliente">Abrir ficha del cliente</button></div>` : ""}
      ${htmlRespuesta(f)}</div>`;
    if (f.cliente_id) $("fm-ir-cliente").addEventListener("click", () => abrirCliente(f.cliente_id));
    conectarRespuestas(cont, () => { pintarFormularios(); abrirRespuesta(id); });
  } catch (e) { cont.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`; }
}

/* ── Arranque ───────────────────────────────────────────────────── */
document.querySelectorAll("#pestanas button").forEach(b => b.addEventListener("click", () => cambiarVista(b.dataset.vista)));
(async function () {
  const p = new URLSearchParams(location.search);
  const v = VISTAS.includes(p.get("vista")) ? p.get("vista") : "marcas";
  cargarResumen();
  cambiarVista(v, { sinURL: true });
  if (p.get("cliente")) abrirCliente(+p.get("cliente"));
  cargarBadgeFormularios();
  // el badge de "Por matrícula" se llena sin entrar a la pestaña
  try { const r = await api("/api/cartera/por-matricula"); $("badge-matricula").textContent = r.grupos.length ? String(r.grupos.length) : ""; } catch (_) {}
})();
