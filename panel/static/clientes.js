/* Clientes y vigilancia marcaria (/clientes).
 * Depende de comun.js (api, apiJson, abrirActa, fmtFechaHora, tipoLegible) y
 * de crm.js (crmEsc, crmFecha, crmUrgencia). Todo texto que viene de la base
 * pasa por esc() antes de ir al HTML; los datos viajan en atributos data-*. */

const esc = crmEsc;
const VISTAS = ["clientes", "marcas", "formularios", "vigilancia", "vencimientos", "matricula", "ajustes"];
const est = {
  vista: "clientes",
  clientes: [], origenes: {},
  alertas: { estado: "abiertas", nivel: "", tipo: "", cliente_id: "", sin_contratar: false },
  estadosAlerta: {},
  clienteAbierto: null,
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

function tagEstadoMarca(m) {
  if (m.estado_tramite === "Concedida") return '<span class="cl-tag ok">Concedida</span>';
  if (m.estado_tramite === "Denegada") return '<span class="cl-tag mal">Denegada</span>';
  if (!m.consultado_en) return '<span class="cl-tag aviso" title="Todavía no se consultó en INPI: se completa sola en la próxima revisión">Pendiente de consultar</span>';
  return '<span class="cl-tag">En trámite</span>';
}

function pedir(fn) {
  return fn().catch(e => { alert(e.message || e); throw e; });
}

/* ── Navegación ─────────────────────────────────────────────────── */
function cambiarVista(v, { sinURL = false } = {}) {
  est.vista = v;
  document.querySelectorAll("#pestanas button").forEach(b => b.classList.toggle("activa", b.dataset.vista === v));
  VISTAS.forEach(x => { vista(x).hidden = x !== v; });
  if (!sinURL) history.replaceState(null, "", v === "clientes" ? "/clientes" : `/clientes?vista=${v}`);
  cargarVista();
}

function cargarVista() {
  ({ clientes: pintarClientes, marcas: pintarMarcas, formularios: pintarFormularios, vigilancia: pintarVigilancia, vencimientos: pintarVencimientos,
     matricula: pintarMatricula, ajustes: pintarAjustes })[est.vista]();
}

async function cargarResumen() {
  try {
    const r = await api("/api/cartera/resumen");
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
        <div class="cl-barra">
          <label>Buscar<input type="search" id="mk-q" placeholder="marca, acta, titular o cliente…" style="min-width:240px"></label>
          <label>Cliente<select id="mk-cliente"><option value="">Todos</option>${est.clientes.map(c => `<option value="${c.id}">${esc(c.nombre)}</option>`).join("")}</select></label>
          <label>Referido<select id="mk-referido"><option value="">Todos</option></select></label>
          <label>Estado<select id="mk-estado"><option value="">Todos</option><option value="en_tramite">En trámite</option><option value="concedida">Concedida</option><option value="denegada">Denegada</option><option value="pendiente">Pendiente de consultar</option></select></label>
          <label>Clase<input type="number" id="mk-clase" min="1" max="45" style="width:80px"></label>
          <label>Vence en<select id="mk-vence"><option value="">—</option><option value="90">90 días</option><option value="180">6 meses</option><option value="365">1 año</option></select></label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-opo"> Con oposición</label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-alertas"> Con alertas</label>
          <label class="check" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="mk-sinvig"> Sin vigilar</label>
        </div>
        <div id="mk-tabla"></div>
      </div>`;
    let t;
    $("mk-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(pintarMarcas, 250); });
    ["mk-cliente", "mk-referido", "mk-estado", "mk-clase", "mk-vence", "mk-opo", "mk-alertas", "mk-sinvig"].forEach(id => $(id).addEventListener("change", pintarMarcas));
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
  const tabla = $("mk-tabla");
  try {
    const r = await api(`/api/cartera/marcas?${p}`);
    llenarSelectReferidos($("mk-referido"), r.referidos || [], $("mk-referido").value);
    if (!r.marcas.length) { tabla.innerHTML = '<p class="vacio">No hay marcas con estos filtros.</p>'; return; }
    tabla.innerHTML = `<div style="overflow-x:auto"><table class="cl-tabla"><thead><tr>
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
      </tbody></table></div><p class="cl-pie">${r.marcas.length} marca(s). Un click en la fila abre la ficha del cliente.</p>`;
    tabla.querySelectorAll("[data-abrir]").forEach(a => a.addEventListener("click", ev => { ev.stopPropagation(); abrirActa(a.dataset.abrir); }));
    tabla.querySelectorAll("tr[data-cliente]").forEach(tr => tr.addEventListener("click", () => abrirCliente(+tr.dataset.cliente)));
  } catch (e) { tabla.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`; }
}

/* ── Modal ──────────────────────────────────────────────────────── */
function cerrarModal() { $("modal-cliente").classList.remove("abierto"); est.clienteAbierto = null; }
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

  const marcasHtml = d.marcas.length ? `
    <div style="overflow-x:auto"><table class="cl-tabla cl-marcas"><thead><tr><th>Acta</th><th>Marca</th><th>Clase</th><th>Estado</th><th>Agente</th><th>Vigilancia</th><th></th></tr></thead><tbody>
    ${d.marcas.map(m => `<tr data-acta="${esc(m.acta)}">
      <td><a class="link-acta" href="javascript:void(0)" data-abrir="${esc(m.acta)}">${esc(m.acta)} ↗</a></td>
      <td><strong>${esc(m.denominacion || (m.tipo === "F" ? "(figurativa, sin texto)" : "(pendiente)"))}</strong><br><small class="crm-gris">${esc(tipoLegible(m.tipo))}${m.fecha_presentacion ? " · presentada " + crmFecha(m.fecha_presentacion) : ""}${m.fecha_vencimiento_marca ? " · vence " + crmFecha(m.fecha_vencimiento_marca) : ""}</small>
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
      <td style="white-space:nowrap"><button type="button" class="cl-btn sec mini" data-accion="actualizar" title="Volver a consultar el expediente en INPI">↻</button>
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

  cont.innerHTML = `
    <div class="cl-ficha">
      <h3>${esc(c.nombre)} ${c.vigilancia_contratada ? '<span class="cl-tag ok">Vigilancia contratada</span>' : '<span class="cl-tag">Vigilancia interna</span>'}${c.activo ? "" : ' <span class="cl-tag">inactivo</span>'}${c.referido ? " " + tagReferido(c.referido) : ""}</h3>
      <div class="crm-gris">Cargado el ${crmFecha(c.alta_en)}${c.alta_por ? " por " + esc(c.alta_por) : ""}${c.clave_crm ? ` · <a href="/titular/${encodeURIComponent(c.clave_crm)}" target="_blank">ver como lead ↗</a>` : ""}</div>

      <form class="cl-form" id="form-cliente">${camposClienteHtml(c)}
        <label class="check"><input type="checkbox" name="activo" ${c.activo ? "checked" : ""}> Cliente activo (los inactivos no se vigilan)</label></form>
      <div class="cl-acciones">
        <button type="button" class="cl-btn" id="cli-guardar">Guardar datos</button>
        <button type="button" class="cl-btn sec" id="cli-poder-btn">📄 Generar poder para firmar</button>
        <button type="button" class="cl-btn peligro" id="cli-borrar">Borrar cliente</button>
        <span class="cl-estado" id="cli-estado"></span></div>
      <div class="cl-caja" id="cli-poder" hidden style="margin-top:10px"></div>
      <div id="cli-poderes-lista">${d.poderes && d.poderes.length ? "<h4>Poderes generados</h4>" + htmlPoderes(d.poderes) : ""}</div>

      ${d.formularios && d.formularios.length ? `<h4>Formularios completados (${d.formularios.length})</h4>
        ${d.formularios.map(f => htmlRespuesta(f, { enFicha: true })).join("")}` : ""}

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

      <h4>Plazos y fechas</h4>${plazosHtml}

      <h4>Alertas de vigilancia (${d.alertas.filter(a => ["nueva", "monitorear", "oponer"].includes(a.estado)).length} abiertas)</h4>
      ${d.alertas.length ? d.alertas.slice(0, 30).map(a => htmlAlerta(a, { sinCliente: true })).join("") : '<p class="crm-gris">Sin alertas.</p>'}

      <h4>Novedades del expediente</h4>${novedadesHtml}
    </div>`;
  cont.parentElement.scrollTop = scroll;
  conectarRespuestas(cont, () => abrirCliente(id));
  $("cli-poder-btn").addEventListener("click", () => abrirGeneradorPoder(id));
  conectarPoderes(id);
  conectarFichaCliente(cont, d);
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
    ? `<div class="vg-plazo ${a.dias_oposicion !== null && a.dias_oposicion <= 7 ? "urgente" : ""}">⏱ ${a.dias_oposicion >= 0 ? "Se puede presentar oposición hasta el" : "El plazo de oposición cerró el"} <strong>${crmFecha(a.plazo_oposicion)}</strong> (${crmUrgencia({ dias: a.dias_oposicion })})</div>`
    : (!otro && abierta ? '<div class="vg-plazo">⏱ Todavía no se publicó en el boletín: el plazo de 30 días para oponerse empieza cuando se publique.</div>' : "");
  const botones = otro
    ? [["monitorear", "Lo sigo"], ["opuesta", "Ya lo hablé"], ["descartada", "Descartar"]]
    : [["monitorear", "Monitorear"], ["oponer", "Hay que oponer"], ["opuesta", "Oposición presentada"], ["descartada", "Descartar"]];
  return `
    <div class="vg-card ${otro ? "vg-otro" : "vg-" + esc(a.nivel)} ${abierta ? "" : "vg-cerrada"}" data-alerta="${a.id}">
      <div class="vg-cab">
        ${otro ? '<span class="cl-tag azul">🔀 OTRO AGENTE</span>' : `${tagNivel(a.nivel)} <span class="vg-puntaje">${a.puntaje}%</span>`}
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
  } catch (e) { lista.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`; }
}

async function probarNombre() {
  const res = $("pr-res"); const nombre = $("pr-nombre").value.trim();
  if (nombre.length < 2) return;
  res.innerHTML = '<p class="crm-cargando">Buscando…</p>';
  try {
    const r = await apiJson("/api/cartera/probar", "POST", { nombre, clase: $("pr-clase").value ? +$("pr-clase").value : null });
    res.innerHTML = r.resultados.length ? `<table class="cl-tabla" style="margin-top:8px"><thead><tr><th>Parecido</th><th>Marca</th><th>Clase</th><th>Acta</th><th>Titular</th><th>Dónde está</th></tr></thead><tbody>
      ${r.resultados.map(x => `<tr><td>${tagNivel(x.nivel)} ${x.puntaje}%<br><small class="crm-gris">${esc(x.motivos || "")}</small></td><td><strong>${esc(x.nombre)}</strong></td><td>${esc(x.clase ?? "")}</td>
        <td><a class="link-acta" href="javascript:void(0)" data-abrir="${esc(x.acta)}">${esc(x.acta)} ↗</a></td><td>${esc(x.titular || "")}</td>
        <td>${x.boletin ? "boletín " + esc(x.boletin) : "pre-boletín"}${x.fecha_publicacion ? " · publicada " + crmFecha(x.fecha_publicacion) : ""}</td></tr>`).join("")}</tbody></table>
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
        <table class="cl-tabla"><tbody>${g.marcas.map(m => `<tr><td style="width:24px"><input type="checkbox" checked data-acta="${esc(m.acta)}"></td>
          <td>${esc(m.acta)}</td><td><strong>${esc(m.denominacion_inpi || "(sin nombre)")}</strong></td><td>clase ${esc(m.clase ?? "?")}</td>
          <td>mat. ${esc(m.matricula_agente)}</td><td>${m.fecha_presentacion ? crmFecha(m.fecha_presentacion) : ""}</td><td>${m.boletin ? "boletín " + esc(m.boletin) : "pre-boletín"}</td></tr>`).join("")}</tbody></table>
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
      ${esc((p.datos.aclaracion || p.datos.otorgante || "").slice(0, 60))} · lugar y fecha: ${esc(p.datos.lugar)}, ${crmFecha(p.datos.fecha)}
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
  if (p.tipo_persona === "juridica" && !p.aclaracion) faltan.push("quién firma");
  caja.innerHTML = `
    <p class="crm-gris" style="margin:0 0 8px">Se arma con el modelo del estudio y los datos del cliente. Revisá el texto (por ejemplo «domiciliado» / «domiciliada») y descargalo para mandárselo a firmar.</p>
    ${faltan.length ? `<p class="crm-error" style="padding:0 0 8px">Ojo: falta ${faltan.join(", ")} en la ficha. Podés completarlo acá abajo o en «Datos para el trámite».</p>` : ""}
    <form class="cl-form" id="poder-form" style="margin-top:0">
      <label>Tipo<select name="tipo_persona">${Object.entries(est.tiposPersona).map(([k, v]) => `<option value="${k}" ${k === p.tipo_persona ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      <label>Lugar<input name="lugar" value="${esc(p.lugar)}"></label>
      <label>Fecha<input type="date" name="fecha" value="${esc(p.fecha)}"></label>
      <label class="ancho">El abajo firmante…<textarea name="otorgante" rows="3">${esc(p.otorgante)}</textarea></label>
      <label>Aclaración (quien firma)<input name="aclaracion" value="${esc(p.aclaracion)}"></label>
      <label data-solo-juridica>Cargo<input name="cargo" value="${esc(p.cargo)}" placeholder="ej. Socio gerente"></label>
    </form>
    <p class="crm-nota">Apoderado: ${esc(r.apoderado)} <a href="/clientes?vista=ajustes" title="Se cambia en Ajustes">(cambiar)</a></p>
    <div class="cl-acciones"><button type="button" class="cl-btn" data-generar="pdf">Descargar PDF</button>
      <button type="button" class="cl-btn sec" data-generar="docx">Descargar Word</button>
      <span class="cl-estado" id="poder-estado"></span></div>`;
  const form = $("poder-form");
  const cargoVisible = () => { form.querySelector("[data-solo-juridica]").hidden = form.elements.tipo_persona.value !== "juridica"; };
  form.elements.tipo_persona.addEventListener("change", cargoVisible); cargoVisible();
  caja.querySelectorAll("[data-generar]").forEach(b => b.addEventListener("click", async () => {
    const f = form.elements;
    const datos = { tipo_persona: f.tipo_persona.value, otorgante: f.otorgante.value, lugar: f.lugar.value,
                    fecha: f.fecha.value, aclaracion: f.aclaracion.value, cargo: f.tipo_persona.value === "juridica" ? f.cargo.value : "" };
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
      ${f.estado === "nueva" ? '<span class="cl-tag azul">Nueva</span>' : `<span class="cl-tag ok" title="${esc(f.revisado_por || "")}">Revisada</span>`}
      ${f.cliente_nuevo ? '<span class="cl-tag">creó el cliente</span>' : '<span class="cl-tag">actualizó datos del cliente</span>'}
      <small class="crm-gris">· ${aviso}</small></div>
    <details ${enFicha ? "" : "open"}><summary>Ver respuestas${f.archivos.length ? ` y ${f.archivos.length} archivo(s)` : ""}</summary>
      <table class="fm-tabla">${filas}</table>${archivos}</details>
    <div class="cl-acciones">
      ${f.estado === "nueva" ? '<button type="button" class="cl-btn sec mini" data-fm="revisada">✓ Marcar revisada</button>' : '<button type="button" class="cl-btn sec mini" data-fm="nueva">Volver a «nueva»</button>'}
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
        <option value="">Todas</option><option value="nueva" selected>Nuevas (sin revisar)</option><option value="revisada">Revisadas</option></select></label></div>
      <div id="fm-lista"></div></div>`;
    $("fm-estado").addEventListener("change", pintarFormularios);
  }
  const lista = $("fm-lista");
  try {
    const r = await api(`/api/formularios?estado=${$("fm-estado").value}`);
    $("badge-formularios").textContent = r.nuevas ? String(r.nuevas) : "";
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
      lista.innerHTML = `<p class="vacio">${$("fm-estado").value === "nueva" ? "No hay formularios nuevos sin revisar." : "Todavía no llegó ningún formulario."}</p>`;
      return;
    }
    lista.innerHTML = `<table class="cl-tabla"><thead><tr><th>Recibido</th><th>Tipo</th><th>Titular</th><th>CUIT</th><th>Marca</th><th>Archivos</th><th>Estado</th><th></th></tr></thead><tbody>
      ${r.respuestas.map(f => `<tr data-resp="${f.id}">
        <td>${fmtFechaHora(f.recibido_en)}</td>
        <td>${esc(est.tiposPersona[f.tipo_persona] || f.tipo_persona)}</td>
        <td><strong>${esc(f.nombre || "")}</strong><br><small class="crm-gris">${esc(f.email || "")}</small></td>
        <td>${esc(f.cuit || "")}</td>
        <td>${esc(f.marca || "—")}</td>
        <td>${f.archivos ? "📎 " + f.archivos : "—"}</td>
        <td>${f.estado === "nueva" ? '<span class="cl-tag azul">Nueva</span>' : '<span class="cl-tag ok">Revisada</span>'}
          ${f.cliente_nuevo ? "" : '<br><small class="crm-gris">ya era cliente</small>'}</td>
        <td style="white-space:nowrap">${f.cliente_id ? `<button type="button" class="cl-btn mini" data-ver-cliente="${f.cliente_id}">Ver cliente</button>` : '<small class="crm-gris">cliente borrado</small>'}
          <button type="button" class="cl-btn sec mini" data-ver-resp="${f.id}">Respuestas</button></td></tr>`).join("")}
      </tbody></table><p class="cl-pie">${r.respuestas.length} formulario(s).</p>`;
    lista.querySelectorAll("[data-ver-cliente]").forEach(b => b.addEventListener("click", () => abrirCliente(+b.dataset.verCliente)));
    lista.querySelectorAll("[data-ver-resp]").forEach(b => b.addEventListener("click", () => abrirRespuesta(+b.dataset.verResp)));
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
  const v = VISTAS.includes(p.get("vista")) ? p.get("vista") : "clientes";
  cargarResumen();
  cambiarVista(v, { sinURL: true });
  if (p.get("cliente")) abrirCliente(+p.get("cliente"));
  cargarBadgeFormularios();
  // el badge de "Por matrícula" se llena sin entrar a la pestaña
  try { const r = await api("/api/cartera/por-matricula"); $("badge-matricula").textContent = r.grupos.length ? String(r.grupos.length) : ""; } catch (_) {}
})();
