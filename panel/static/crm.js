/* CRM de leads — piezas compartidas entre /crm (tablero, lista, agenda),
 * la ficha de titular (/titular/...) y el panel principal (badge de etapa
 * en cada fila). Depende de comun.js (api, apiJson, abrirActa, fmtFechaHora,
 * badgeLead, badgeOposicion, badgeEstadoTramite, tipoLegible).
 *
 * Todo texto que viene de la base pasa por crmEsc() antes de ir al HTML
 * (los nombres de titular pueden traer comillas, apóstrofes, barras...), y
 * las claves de titular viajan en atributos data-* — nunca dentro de un
 * onclick armado a mano. */

const CRM_ETAPAS = [
  { id: "nuevo", nombre: "Nuevo" },
  { id: "contactado", nombre: "Contactado" },
  { id: "respondio", nombre: "Respondió" },
  { id: "reunion", nombre: "Reunión / propuesta" },
  { id: "cliente", nombre: "Cliente" },
  { id: "descartado", nombre: "Descartado" },
];
const CRM_ETAPA_NOMBRE = Object.fromEntries(CRM_ETAPAS.map(e => [e.id, e.nombre]));
const CRM_TIPO_ICONO = { nota: "📝", llamada: "📞", email: "✉️", whatsapp: "🟢", reunion: "🤝", sistema: "⚙️", comentario: "💬" };
const CRM_TIPO_NOMBRE = { nota: "Nota", llamada: "Llamada", email: "Mail", whatsapp: "WhatsApp", reunion: "Reunión", sistema: "Automático", comentario: "Comentario interno" };

function crmEsc(s) {
  return (s ?? "").toString()
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function crmFecha(iso) {
  if (!iso) return "";
  const [a, m, d] = iso.toString().slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

function crmBadgeEtapa(etapa, extra = "") {
  const e = etapa || "nuevo";
  return `<span class="crm-etapa crm-etapa-${crmEsc(e)}" ${extra}>${crmEsc(CRM_ETAPA_NOMBRE[e] || e)}</span>`;
}

function crmIniciales(usuario) {
  if (!usuario) return "";
  return `<span class="crm-asignado" title="Asignado a ${crmEsc(usuario)}">${crmEsc(usuario.slice(0, 2).toUpperCase())}</span>`;
}

// Link de WhatsApp a partir de un teléfono argentino escrito como venga
// ("011 15 4444-5555", "+54 9 351 555-1234", "3515551234"...). Si no tiene
// código de país se asume Argentina (54 9 + área + número, sin el 0 ni el 15).
// Es de mejor esfuerzo: conviene revisar que abra el chat correcto.
function crmLinkWhatsApp(tel) {
  let d = (tel || "").replace(/\D/g, "");
  if (d.length < 8) return null;
  if (d.startsWith("00")) d = d.slice(2);
  if (d.startsWith("54")) {
    if (!d.startsWith("549") && d.length === 12) d = "549" + d.slice(2);
  } else {
    if (d.startsWith("0")) d = d.slice(1);
    if (d.length === 12) d = d.replace(/^(\d{2,4})15(\d{6,8})$/, "$1$2");
    d = "549" + d;
  }
  return `https://wa.me/${d}`;
}

function crmUrgencia(p) {
  const dias = p.dias;
  if (dias === undefined || dias === null) return "";
  if (dias < 0) return `hace ${-dias} día${dias === -1 ? "" : "s"}`;
  if (dias === 0) return "hoy";
  if (dias === 1) return "mañana";
  return `en ${dias} días`;
}

/* ── Badge de etapa en las filas del panel principal ──────────────────
 * filaHtml pone <span class="crm-slot" data-clave-crm="..."></span>; esto
 * pide las etapas de todas las claves visibles de una vez y las pinta.
 * "Nuevo" no se muestra (es el estado de casi todas las filas). */
function crmSlot(clave) {
  if (!clave) return "";
  return `<span class="crm-slot" data-clave-crm="${crmEsc(clave)}"></span>`;
}

async function crmPintarEtapas() {
  const slots = [...document.querySelectorAll("[data-clave-crm]")];
  const claves = [...new Set(slots.map(s => s.dataset.claveCrm).filter(Boolean))];
  if (!claves.length) return;
  let data;
  try {
    data = await apiJson("/api/crm/etapas", "POST", { claves });
  } catch (_) { return; }
  slots.forEach(s => {
    const info = data[s.dataset.claveCrm];
    if (!info || info.etapa === "nuevo") { s.innerHTML = ""; return; }
    const titulo = `CRM: ${info.nombre}${info.asignado ? ` · asignado a ${info.asignado}` : ""}`
      + (info.proximo_seguimiento ? ` · seguimiento ${crmFecha(info.proximo_seguimiento)}` : "");
    s.innerHTML = crmBadgeEtapa(info.etapa, `title="${crmEsc(titulo)}"`);
  });
}

/* ── Ficha del lead ───────────────────────────────────────────────────
 * crmRenderFicha(contenedor, clave, opciones) arma la ficha del lead.
 *
 * Dos modos:
 *  - Popup del CRM (sin `partes`): todo apilado dentro de `contenedor`:
 *    cabecera, marcas, plazos y gestión (campos del CRM, registrar, historial).
 *  - Página del titular (con `partes` = {cabecera, plazos, gestion}, selectores
 *    relativos a `contenedor`): cada pieza va a su lugar —la cabecera queda
 *    siempre a la vista y plazos/gestión viven en pestañas— sin tocar el resto
 *    de lo que hay dentro de `contenedor` (tabla de marcas, mail).
 *
 * opciones.alCambiar() se llama después de cada guardado, para que la página
 * recargue lo suyo; opciones.alRenderizar(datos) después de pintar la ficha. */

async function crmRenderFicha(cont, clave, opciones = {}) {
  const partes = opciones.partes ? {
    cabecera: cont.querySelector(opciones.partes.cabecera),
    plazos: cont.querySelector(opciones.partes.plazos),
    gestion: cont.querySelector(opciones.partes.gestion),
  } : null;
  const mostrar = html => {
    if (partes) { partes.cabecera.innerHTML = html; partes.plazos.innerHTML = ""; partes.gestion.innerHTML = ""; }
    else cont.innerHTML = html;
  };
  mostrar('<p class="crm-cargando">Cargando…</p>');
  let d;
  try {
    d = await api(`/api/crm/lead?clave=${encodeURIComponent(clave)}`);
  } catch (e) {
    mostrar(`<p class="crm-error">No se pudo cargar la ficha del lead (${crmEsc(e.message)}).</p>`);
    return;
  }
  const raices = partes ? [partes.cabecera, partes.plazos, partes.gestion] : [cont];
  cont._crm = { datos: d, opciones, raices };
  if (partes) {
    partes.cabecera.innerHTML = _crmHtmlCabecera(d, opciones);
    partes.plazos.innerHTML = _crmHtmlPlazos(d);
    partes.gestion.innerHTML = _crmHtmlGestion(d, opciones);
  } else {
    cont.innerHTML = `<div class="crm-ficha">${_crmHtmlCabecera(d, opciones)}${_crmHtmlMarcasModal(d)}
      <h4>Plazos y fechas</h4>${_crmHtmlPlazos(d)}${_crmHtmlGestion(d, opciones)}</div>`;
  }
  _crmConectarFicha(cont);
  if (typeof opciones.alRenderizar === "function") opciones.alRenderizar(d);
}

/* Cabecera: lo que tiene que verse siempre. En la página del titular (modo
 * `partes`) el nombre ya está en el título de la página, así que acá va un
 * resumen (etapa, asignado, próximo seguimiento) en vez del título. */
function _crmHtmlCabecera(d, opciones = {}) {
  const { enModal = false, partes = null } = opciones;
  const l = d.lead || {};
  const etapa = l.etapa || "nuevo";
  const enTitular = !!partes;
  const titulo = l.titular || d.clave;
  const clases = (l.clases || []).slice().sort((a, b) => a - b).join(", ");
  const linkTit = !enTitular && d.clave && !d.clave.startsWith("ACTA ")
    ? `<a class="link-titular" href="/titular/${encodeURIComponent(d.clave)}" target="${enModal ? "_blank" : "_self"}">Ver todas sus marcas ↗</a>` : "";

  const alertas = [];
  if (l.oposicion_sin_apoderado) alertas.push('<div class="crm-alerta crm-alerta-opo">⚠ Tiene una marca con <strong>oposición o vista</strong> y todavía nadie se presentó como apoderado: es el momento de ofrecer ayuda.</div>');
  else if (l.oposicion_atendida) alertas.push('<div class="crm-alerta">⚖ Tuvo una oposición, marcada como <strong>atendida</strong>: no hace falta ofrecer ayuda. <button type="button" class="crm-btn-chico" data-accion="opo-reabrir">Deshacer</button></div>');
  else if (l.con_oposicion && !l.con_gestor_manual) alertas.push('<div class="crm-alerta">⚖ Tuvo una oposición, pero ya se sumó un apoderado/gestor.</div>');
  if (l.con_gestor_manual) {
    const quien = [l.con_gestor_manual_por ? `por ${crmEsc(l.con_gestor_manual_por)}` : "", l.con_gestor_manual_en ? `el ${crmFecha(l.con_gestor_manual_en)}` : ""].filter(Boolean).join(" ");
    alertas.push(`<div class="crm-alerta crm-alerta-gestor">🧑‍⚖️ <strong>Tiene gestor/apoderado</strong>${l.gestor_nombre ? ` (${crmEsc(l.gestor_nombre)})` : ""} — marcado a mano ${quien}. Ya no es lead: salió de la lista de leads. <button type="button" class="crm-btn-chico" data-accion="sin-gestor">Deshacer</button></div>`);
  }
  if (l.tiene_marcas_con_agente && !l.con_gestor_manual) alertas.push('<div class="crm-alerta">ℹ Este titular tiene <strong>otras marcas presentadas con agente/apoderado</strong>: puede que ya trabaje con alguien.</div>');
  if (l.pre_boletin) alertas.push('<div class="crm-alerta crm-alerta-info">🆕 Tiene marcas detectadas antes del boletín (todavía no publicadas).</div>');

  const cli = d.cliente
    ? `<div class="crm-alerta crm-alerta-info">✅ Ya es cliente: <a href="/clientes?cliente=${d.cliente.id}" target="_blank"><strong>${crmEsc(d.cliente.nombre)}</strong> ↗</a>${d.cliente.vigilancia_contratada ? " · vigilancia contratada" : ""}</div>`
    : "";

  // Las dos formas en que termina un lead: se convirtió en cliente, o resultó
  // que ya tiene gestor/apoderado (deja de ser lead y pasa a «con agente»).
  const botones = [];
  // Con una oposición/vista todavía sin gestor, lo primero que se hace es mirar el
  // expediente en INPI y mandar el mail de oposición: se dejan a mano acá. El mail
  // solo se ofrece donde la página sabe mandarlo (opciones.mailOposicion: ficha del titular).
  const conOpo = m => m.tuvo_oposicion === true && m.es_lead === true && m.oposicion_atendida !== true && m.oposicion_sirve !== false;
  const marcaOpo = (!d.cliente && l.es_lead && l.oposicion_sin_apoderado)
    ? (d.marcas.find(m => conOpo(m) && m.email) || d.marcas.find(conOpo))
    : null;
  if (marcaOpo) {
    const nombreMarca = marcaOpo.denominacion_inpi || marcaOpo.denominacion || marcaOpo.acta;
    botones.push(`<button type="button" class="crm-btn-gestor" data-accion="ver-acta-opo" data-acta-opo="${crmEsc(marcaOpo.acta)}" title="Abre el expediente en el portal de INPI (ahí está el botón GRILLA DIGITAL con el historial del acta)">🔎 Ver acta ${crmEsc(marcaOpo.acta)} en INPI</button>`);
    botones.push(`<button type="button" class="crm-btn-gestor" data-accion="ver-grilla-opo" data-acta-opo="${crmEsc(marcaOpo.acta)}" title="Abre directo la Grilla Digital del acta (archivos del expediente: formularios, oposiciones, publicación...)">🗂 Ver Grilla Digital</button>`);
    if (typeof opciones.mailOposicion === "function") {
      const ayudaMail = marcaOpo.email
        ? `Manda el mail «Recibió una oposición» de la marca ${nombreMarca} a ${marcaOpo.email} (pide confirmación antes de enviar)`
        : "Esta marca no tiene mail: se completa en la pestaña Mail";
      botones.push(`<button type="button" class="crm-btn-primario" data-accion="mail-oposicion" data-acta-opo="${crmEsc(marcaOpo.acta)}" title="${crmEsc(ayudaMail)}">✉ Enviar mail de oposición</button><span class="crm-gris" id="estado-mail-opo"></span>`);
    }
  }
  if (!d.cliente && d.lead) botones.push(`<button type="button" class="${marcaOpo ? "crm-btn-gestor" : "crm-btn-primario"}" data-accion="convertir" title="Pasa sus marcas a la cartera (con vigilancia) y marca el lead como «Cliente».">Convertir en cliente</button>`);
  if (!d.cliente && l.es_lead) botones.push('<button type="button" class="crm-btn-gestor" data-accion="con-gestor" title="Si el titular ya trabaja con un gestor/apoderado: deja de ser lead y pasa a «con agente». Se puede deshacer.">Tiene gestor/apoderado</button>');
  const acciones = botones.length ? `<div class="crm-acciones-resultado">${botones.join("")}</div>` : "";

  let cab;
  if (enTitular) {
    const seg = d.plazos.find(p => p.tipo === "seguimiento");
    cab = `<div class="crm-cab-resumen">${crmBadgeEtapa(etapa)}
        ${l.asignado ? `${crmIniciales(l.asignado)} <span>${crmEsc(l.asignado)}</span>` : '<span class="crm-gris">Sin asignar</span>'}
        ${seg ? `<span class="crm-chip-seg crm-chip-${crmEsc(seg.urgencia)}">Seguimiento ${crmFecha(seg.fecha)} · ${crmEsc(crmUrgencia(seg))}</span>` : ""}
        ${l.modificado_por ? `<span class="crm-gris">últ. cambio: ${crmEsc(l.modificado_por)} ${fmtFechaHora(l.modificado_en)}</span>` : ""}
      </div>`;
  } else {
    cab = `<div class="crm-ficha-cab">
        <h3>${crmEsc(titulo)} ${crmBadgeEtapa(etapa)}</h3>
        <div class="crm-gris">${l.cuit ? `CUIT ${crmEsc(l.cuit)} · ` : ""}${l.cant_marcas || d.marcas.length} marca(s)${clases ? ` · clase ${crmEsc(clases)}` : ""}
          ${l.modificado_por ? ` · últ. cambio: ${crmEsc(l.modificado_por)} ${fmtFechaHora(l.modificado_en)}` : ""} ${linkTit}</div>
      </div>`;
  }
  return `${cab}${alertas.join("")}${cli}${acciones}`;
}

function _crmHtmlMarcasModal(d) {
  return `
    <h4>Marcas (${d.marcas.length})</h4>
    <table class="crm-tabla-marcas"><tbody>
      ${d.marcas.map(m => `<tr>
        <td><a class="link-acta" href="javascript:void(0)" data-acta="${crmEsc(m.acta)}">${crmEsc(m.acta)} ↗</a></td>
        <td>${crmEsc(m.denominacion_inpi || m.denominacion || sinNombre(m.tipo))}</td>
        <td>Clase ${crmEsc(m.clase)}</td>
        <td>${crmEsc(tipoLegible(m.tipo))}</td>
        <td>${badgeLead(m)} ${badgeEstadoTramite(m, true)}</td>
      </tr>`).join("")}
    </tbody></table>`;
}

function _crmHtmlPlazos(d) {
  return d.plazos.length
    ? `<ul class="crm-plazos">${d.plazos.map(p => `
        <li class="crm-plazo crm-urg-${p.urgencia}">
          <span class="crm-plazo-fecha">${crmFecha(p.fecha)}<small>${crmUrgencia(p)}</small></span>
          <span class="crm-plazo-texto"><strong>${crmEsc(p.titulo)}</strong>
            ${p.acta ? ` · <a class="link-acta" href="javascript:void(0)" data-acta="${crmEsc(p.acta)}">${crmEsc(p.marca)} (acta ${crmEsc(p.acta)}, clase ${crmEsc(p.clase)})</a>` : ""}
            ${p.detalle ? `<br><small class="crm-gris">${crmEsc(p.detalle)}</small>` : ""}</span>
        </li>`).join("")}</ul>
       <p class="crm-nota">Fechas orientativas calculadas por el sistema: confirmar siempre en el expediente.</p>`
    : '<p class="crm-gris">Sin plazos a la vista.</p>';
}

/* Gestión comercial: campos del CRM, registrar una gestión e historial. */
function _crmHtmlGestion(d, { enModal = false } = {}) {
  const l = d.lead || {};
  const etapa = l.etapa || "nuevo";
  const opcionesEtapa = d.etapas.map(e => `<option value="${e.id}" ${e.id === etapa ? "selected" : ""}>${crmEsc(e.nombre)}</option>`).join("");
  const opcionesAsig = `<option value="">Sin asignar</option>` + d.usuarios.map(u =>
    `<option value="${crmEsc(u)}" ${u === l.asignado ? "selected" : ""}>${crmEsc(u)}${u === d.usuario ? " (yo)" : ""}</option>`).join("");
  const wa = crmLinkWhatsApp(l.telefono);
  const email = l.email
    ? `<a href="mailto:${crmEsc(l.email)}">${crmEsc(l.email)}</a>`
    : '<span class="crm-gris">sin email todavía</span>';
  const opcionesTipo = d.tipos_actividad.map(t => `<option value="${t.id}">${crmEsc(t.nombre)}</option>`).join("");

  const items = [
    ...d.actividad.map(a => ({ ...a, _clase: "actividad" })),
    ...d.comentarios.map(c => ({ ...c, tipo: "comentario", _clase: "comentario" })),
  ].sort((a, b) => (a.creado_en < b.creado_en ? 1 : -1));
  const historial = items.length
    ? items.map(i => `
        <div class="crm-hito crm-hito-${crmEsc(i.tipo)}">
          <div class="crm-hito-cab">${CRM_TIPO_ICONO[i.tipo] || "•"} <strong>${crmEsc(CRM_TIPO_NOMBRE[i.tipo] || i.tipo)}</strong>
            · ${crmEsc(i.autor)} <span class="crm-gris">${fmtFechaHora(i.creado_en)}</span>
            ${i._clase === "comentario" && i.acta ? `<span class="crm-gris">· acta ${crmEsc(i.acta)}${i.destinatario ? ` · para ${crmEsc(i.destinatario)}` : ""}</span>` : ""}
            ${i._clase === "actividad" && i.tipo !== "sistema" && i.autor === d.usuario
              ? `<button type="button" class="crm-borrar" data-borrar-actividad="${i.id}" title="Borrar esta gestión">✕</button>` : ""}
          </div>
          <div class="crm-hito-texto">${crmEsc(i.texto).replace(/\n/g, "<br>")}</div>
        </div>`).join("")
    : '<p class="crm-gris">Todavía no hay gestiones registradas.</p>';

  return `
      <div class="crm-grid">
        <label>Etapa<select name="etapa">${opcionesEtapa}</select></label>
        <label>Asignado a<select name="asignado">${opcionesAsig}</select></label>
        <label>Próximo seguimiento<input type="date" name="proximo_seguimiento" value="${crmEsc((l.proximo_seguimiento || "").slice(0, 10))}"></label>
        <label>Teléfono<span class="crm-fila-campo"><input type="text" name="telefono" value="${crmEsc(l.telefono || "")}" placeholder="ej. 11 5555-1234">
          ${wa ? `<a class="crm-btn-wa" href="${wa}" target="_blank" rel="noopener" title="Abrir chat de WhatsApp">WhatsApp</a>` : ""}</span></label>
        <label>Email<span class="crm-email">${email}</span></label>
        <label class="crm-solo-descartado">Motivo del descarte<input type="text" name="motivo_descarte" value="${crmEsc(l.motivo_descarte || "")}" placeholder="ej. ya tiene abogado, no le interesa..."></label>
        <label class="crm-solo-cliente">Servicio contratado<input type="text" name="servicio" value="${crmEsc(l.servicio || "")}" placeholder="ej. respuesta a oposición"></label>
        <label class="crm-solo-cliente">Honorarios<span class="crm-fila-campo"><input type="number" min="0" step="0.01" name="monto" value="${l.monto ?? ""}">
          <select name="moneda"><option value="ARS" ${l.moneda !== "USD" ? "selected" : ""}>ARS</option><option value="USD" ${l.moneda === "USD" ? "selected" : ""}>USD</option></select></span></label>
      </div>
      <div class="crm-guardar"><button type="button" class="crm-btn-primario" data-accion="guardar">Guardar cambios</button> <span class="crm-estado"></span></div>

      <h4>Registrar una gestión</h4>
      <form class="crm-form-actividad">
        <div class="crm-fila-campo">
          <select name="tipo">${opcionesTipo}</select>
          <label class="crm-inline">Próximo seguimiento <input type="date" name="seguimiento_actividad"></label>
        </div>
        <textarea name="texto" rows="2" placeholder="Qué se hizo o se habló (ej. le mandé el mail de presentación, pidió presupuesto...)" required></textarea>
        <div><button type="submit" class="crm-btn-primario">Registrar</button>
          <span class="crm-gris crm-ayuda-form">Registrar una llamada, mail, WhatsApp o reunión pasa al lead de "Nuevo" a "Contactado" solo.</span></div>
      </form>

      <h4>Historial</h4>
      <div class="crm-historial">${historial}</div>`;
}

function _crmActualizarCamposPorEtapa({ uno, todos }) {
  const etapa = uno('select[name="etapa"]').value;
  todos(".crm-solo-descartado").forEach(el => { el.hidden = etapa !== "descartado"; });
  todos(".crm-solo-cliente").forEach(el => { el.hidden = etapa !== "cliente"; });
}

function _crmConectarFicha(cont) {
  const { datos, opciones, raices } = cont._crm;
  const l = datos.lead || {};
  // La ficha puede estar repartida en varios contenedores (página del titular):
  // se busca solo dentro de ellos, nunca en el resto de la página.
  const uno = sel => { for (const r of raices) { const e = r.querySelector(sel); if (e) return e; } return null; };
  const todos = sel => raices.flatMap(r => [...r.querySelectorAll(sel)]);
  _crmActualizarCamposPorEtapa({ uno, todos });
  uno('select[name="etapa"]').addEventListener("change", () => _crmActualizarCamposPorEtapa({ uno, todos }));

  todos("[data-acta]").forEach(a => a.addEventListener("click", () => abrirActa(a.dataset.acta)));

  const recargar = async () => {
    await crmRenderFicha(cont, datos.clave, opciones);
    if (typeof opciones.alCambiar === "function") await opciones.alCambiar();
  };

  // Oposición marcada como «atendida» con el botón de versiones anteriores:
  // el botón ya no se ofrece, pero lo que ya estaba marcado se puede deshacer.
  const btnReabrir = uno('[data-accion="opo-reabrir"]');
  if (btnReabrir) btnReabrir.addEventListener("click", async () => {
    btnReabrir.disabled = true;
    try {
      await apiJson("/api/marcas/oposicion-atendida", "POST", { actas: l.actas || [], atendida: false });
      await recargar();
    } catch (e) { btnReabrir.disabled = false; alert(`No se pudo guardar: ${e.message}`); }
  });

  // Atajos del lead con oposición: ver el expediente en INPI y mandar el mail de oposición.
  const btnVerActa = uno('[data-accion="ver-acta-opo"]');
  if (btnVerActa) btnVerActa.addEventListener("click", () => abrirActa(btnVerActa.dataset.actaOpo));
  const btnVerGrilla = uno('[data-accion="ver-grilla-opo"]');
  if (btnVerGrilla) btnVerGrilla.addEventListener("click", () => abrirGrilla(btnVerGrilla.dataset.actaOpo));
  const btnMailOpo = uno('[data-accion="mail-oposicion"]');
  if (btnMailOpo) btnMailOpo.addEventListener("click", async () => {
    btnMailOpo.disabled = true;
    try { await opciones.mailOposicion(btnMailOpo.dataset.actaOpo); }
    finally { btnMailOpo.disabled = false; }
  });

  // Tiene gestor/apoderado: deja de ser lead y pasa a «con agente».
  const btnGestor = uno('[data-accion="con-gestor"]');
  if (btnGestor) btnGestor.addEventListener("click", async () => {
    const nombre = prompt(
      "Marcar que este titular ya tiene gestor/apoderado.\n\n"
      + "Deja de ser lead y pasa a «con agente»: sale de la lista de leads y no se le vuelve a avisar. Se puede deshacer.\n\n"
      + "¿Quién es el gestor/apoderado? (opcional: podés dejarlo vacío)", "");
    if (nombre === null) return;  // canceló
    btnGestor.disabled = true;
    try {
      await apiJson(`/api/crm/con-gestor?clave=${encodeURIComponent(datos.clave)}`, "POST", { con_gestor: true, nombre: nombre.trim() || null });
      await recargar();
    } catch (e) { btnGestor.disabled = false; alert(`No se pudo marcar: ${e.message}`); }
  });

  const btnSinGestor = uno('[data-accion="sin-gestor"]');
  if (btnSinGestor) btnSinGestor.addEventListener("click", async () => {
    if (!confirm("¿Deshacer «tiene gestor/apoderado»? Vuelve a ser lead y a aparecer en la lista de leads.")) return;
    btnSinGestor.disabled = true;
    try {
      await apiJson(`/api/crm/con-gestor?clave=${encodeURIComponent(datos.clave)}`, "POST", { con_gestor: false });
      await recargar();
    } catch (e) { btnSinGestor.disabled = false; alert(`No se pudo deshacer: ${e.message}`); }
  });

  const btnConvertir = uno('[data-accion="convertir"]');
  if (btnConvertir) btnConvertir.addEventListener("click", async () => {
    if (!confirm("¿Convertir este lead en cliente? Sus marcas pasan a la cartera y se empiezan a vigilar.")) return;
    btnConvertir.disabled = true;
    try {
      const r = await apiJson(`/api/clientes/desde-lead?clave=${encodeURIComponent(datos.clave)}`, "POST");
      await recargar();
      if (r.cliente && confirm(`Listo: ${r.marcas_sumadas} marca(s) en la cartera. ¿Abrir la ficha del cliente?`)) window.open(`/clientes?cliente=${r.cliente.id}`, "_blank");
    } catch (e) { btnConvertir.disabled = false; alert(`No se pudo convertir: ${e.message}`); }
  });

  uno('[data-accion="guardar"]').addEventListener("click", async (ev) => {
    const boton = ev.currentTarget;
    const estado = uno(".crm-estado");
    const campo = n => uno(`[name="${n}"]`);
    const valores = {
      etapa: campo("etapa").value,
      asignado: campo("asignado").value || null,
      proximo_seguimiento: campo("proximo_seguimiento").value || null,
      telefono: campo("telefono").value.trim() || null,
      motivo_descarte: campo("motivo_descarte").value.trim() || null,
      servicio: campo("servicio").value.trim() || null,
      monto: campo("monto").value === "" ? null : Number(campo("monto").value),
      moneda: campo("moneda").value,
    };
    const originales = {
      etapa: l.etapa || "nuevo", asignado: l.asignado || null,
      proximo_seguimiento: (l.proximo_seguimiento || "").slice(0, 10) || null,
      telefono: l.telefono || null, motivo_descarte: l.motivo_descarte || null,
      servicio: l.servicio || null, monto: l.monto === null || l.monto === undefined ? null : Number(l.monto),
      moneda: l.moneda || "ARS",
    };
    const cambios = {};
    Object.keys(valores).forEach(k => { if (valores[k] !== originales[k]) cambios[k] = valores[k]; });
    if (!Object.keys(cambios).length) { estado.textContent = "No hay cambios."; return; }
    boton.disabled = true;
    estado.textContent = "Guardando…";
    try {
      await apiJson(`/api/crm/lead?clave=${encodeURIComponent(datos.clave)}`, "POST", cambios);
      await recargar();
    } catch (e) {
      boton.disabled = false;
      estado.textContent = `No se pudo guardar: ${e.message}`;
    }
  });

  uno(".crm-form-actividad").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const boton = form.querySelector("button[type=submit]");
    boton.disabled = true;
    try {
      await apiJson(`/api/crm/actividad?clave=${encodeURIComponent(datos.clave)}`, "POST", {
        tipo: form.tipo.value,
        texto: form.texto.value,
        proximo_seguimiento: form.seguimiento_actividad.value || null,
      });
      await recargar();
    } catch (e) {
      boton.disabled = false;
      alert(`No se pudo registrar: ${e.message}`);
    }
  });

  todos("[data-borrar-actividad]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("¿Borrar esta gestión del historial?")) return;
    try {
      await apiJson(`/api/crm/actividad/${b.dataset.borrarActividad}`, "DELETE");
      await recargar();
    } catch (e) { alert(`No se pudo borrar: ${e.message}`); }
  }));
}

/* Ficha en un popup (tablero, lista y agenda de /crm). */
function crmAbrirFichaModal(clave, alCambiar) {
  let modal = document.getElementById("modal-crm");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "modal-crm";
    modal.className = "modal-fondo";
    modal.innerHTML = `
      <div class="modal-caja modal-crm" onclick="event.stopPropagation()">
        <button type="button" class="modal-cerrar" title="Cerrar">&times;</button>
        <div id="modal-crm-contenido"></div>
      </div>`;
    modal.addEventListener("click", () => modal.classList.remove("abierto"));
    modal.querySelector(".modal-cerrar").addEventListener("click", () => modal.classList.remove("abierto"));
    document.addEventListener("keydown", ev => { if (ev.key === "Escape") modal.classList.remove("abierto"); });
    document.body.appendChild(modal);
  }
  modal.classList.add("abierto");
  crmRenderFicha(document.getElementById("modal-crm-contenido"), clave, { enModal: true, alCambiar });
}
