/* Consultas web (/consultas-web, menú Más): las marcas que la gente verifica en el formulario
 * del home de smartiesconsultora.com.ar, con el resultado de INPI y sus datos de contacto.
 * Depende de comun.js (api, apiJson, fmtFechaHora, abrirActa). Todo texto que viene de la
 * base pasa por esc() antes de ir al HTML; los datos viajan en atributos data-*. */

const $ = (id) => document.getElementById(id);
const esc = (s) => (s ?? "").toString()
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const CLASE_VEREDICTO = { no_disponible: "mal", con_similares: "aviso", disponible: "ok", error: "", pendiente: "" };
let LEGIBLE = {};
let consultas = [];

const tagVeredicto = (v) =>
  `<span class="cl-tag ${CLASE_VEREDICTO[v] ?? ""}">${esc(LEGIBLE[v] || v)}</span>`;

const urlWeb = (w) => {
  const t = (w || "").trim();
  if (!t) return "";
  const u = /^https?:\/\//i.test(t) ? t : /^[\w-]+(\.[\w-]+)+(\/.*)?$/.test(t) ? "https://" + t : "";
  return u;
};

const linkWhatsapp = (tel, marca) => {
  const d = (tel || "").replace(/\D/g, "");
  if (d.length < 8) return "";
  const texto = `Hola! Te escribimos de Smarties Consultora por la consulta que hiciste sobre la marca ${marca || ""}.`;
  return `https://wa.me/${d}?text=${encodeURIComponent(texto)}`;
};

function contacto(c) {
  const partes = [`<strong>${esc(c.nombre || "—")}</strong>`];
  if (c.email) partes.push(`<a href="mailto:${esc(c.email)}">${esc(c.email)}</a>`);
  if (c.telefono) {
    const wa = linkWhatsapp(c.telefono, c.marca);
    partes.push(wa ? `<a href="${esc(wa)}" target="_blank" rel="noopener">${esc(c.telefono)}</a> <small class="crm-gris">(WhatsApp)</small>` : esc(c.telefono));
  }
  return partes.join("<br>");
}

function coincidencias(c) {
  if (c.veredicto === "error") return '<small class="crm-gris">—</small>';
  const txt = [];
  if (c.exactos) txt.push(`${c.exactos} idéntica${c.exactos !== 1 ? "s" : ""}`);
  if (c.similares) txt.push(`${c.similares} parecida${c.similares !== 1 ? "s" : ""}`);
  return txt.length ? txt.join("<br>") : '<small class="crm-gris">ninguna</small>';
}

async function cargar() {
  const lista = $("cw-lista");
  const p = new URLSearchParams({
    estado: $("cw-estado").value, veredicto: $("cw-veredicto").value, q: $("cw-q").value.trim(),
  });
  try {
    const r = await api(`/api/consultas-web?${p}`);
    LEGIBLE = r.veredictos || {};
    consultas = r.consultas;
    if (!consultas.length) {
      lista.innerHTML = `<p class="vacio">${$("cw-estado").value === "nueva" && !$("cw-q").value && !$("cw-veredicto").value
        ? "No hay consultas nuevas sin revisar." : "No hay consultas con ese filtro."}</p>`;
      return;
    }
    lista.innerHTML = `<table class="cl-tabla"><thead><tr><th>Recibida</th><th>Marca</th><th>Resultado</th><th>Coincidencias</th><th>Contacto</th><th>Estado</th><th></th></tr></thead><tbody>
      ${consultas.map((c) => `<tr data-id="${c.id}">
        <td>${esc(fmtFechaHora(c.creada_en))}</td>
        <td><strong>${esc(c.marca)}</strong><br><small class="crm-gris">${esc(c.actividad || "")}</small></td>
        <td>${tagVeredicto(c.veredicto)}${c.adicionales ? `<br><small class="crm-gris">+${c.adicionales} pregunta${c.adicionales !== 1 ? "s" : ""}</small>` : ""}</td>
        <td>${coincidencias(c)}</td>
        <td>${contacto(c)}</td>
        <td>${c.estado === "nueva" ? '<span class="cl-tag azul">Nueva</span>' : '<span class="cl-tag ok">Revisada</span>'}
          ${c.aviso_error ? '<br><small class="crm-error" title="' + esc(c.aviso_error) + '">mail de aviso no enviado</small>' : ""}</td>
        <td style="white-space:nowrap"><button type="button" class="cl-btn mini" data-ver="${c.id}">Ver</button>
          <button type="button" class="cl-btn sec mini" data-estado="${c.id}" data-a="${c.estado === "nueva" ? "revisada" : "nueva"}">${c.estado === "nueva" ? "Marcar revisada" : "Volver a nueva"}</button></td>
      </tr>`).join("")}
      </tbody></table><p class="cl-pie">${consultas.length} consulta(s).</p>`;
    lista.querySelectorAll("[data-ver]").forEach((b) => b.addEventListener("click", () => abrirConsulta(+b.dataset.ver)));
    lista.querySelectorAll("[data-estado]").forEach((b) => b.addEventListener("click", () => cambiarEstado(+b.dataset.estado, b.dataset.a)));
  } catch (e) {
    lista.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`;
  }
}

async function cambiarEstado(id, estado, alTerminar) {
  try {
    await apiJson(`/api/consultas-web/${id}/estado`, "POST", { estado });
    if (alTerminar) alTerminar();
    await cargar();
  } catch (e) { mostrarAviso(`No se pudo cambiar el estado (${e.message}).`, "aviso"); }
}

function cerrarModal() { $("modal-consulta").classList.remove("abierto"); }

async function abrirConsulta(id) {
  $("modal-consulta").classList.add("abierto");
  const cont = $("modal-consulta-contenido");
  cont.innerHTML = '<p class="crm-cargando">Cargando…</p>';
  try {
    const c = await api(`/api/consultas-web/${id}`);
    const web = urlWeb(c.web);
    const wa = linkWhatsapp(c.telefono, c.marca);
    const filas = (c.muestras || []).map((m) => `<tr>
        <td>${esc(m.denominacion)}</td><td>${esc(m.clase ?? "?")}</td><td>${esc(m.estado || "—")}</td>
        <td>${m.tipo === "identica" ? '<span class="cl-tag mal">idéntica</span>' : '<span class="cl-tag aviso">parecida</span>'}
          <small class="crm-gris">${esc(m.puntaje)}${m.motivos && m.motivos.length ? " · " + esc(m.motivos.join(", ")) : ""}</small></td>
        <td>${m.acta ? `<button type="button" class="cl-btn sec mini" data-acta="${esc(m.acta)}">Ver acta ${esc(m.acta)}</button>` : ""}</td></tr>`).join("");
    cont.innerHTML = `<div class="cl-ficha">
      <h3>${esc(c.marca)} · ${tagVeredicto(c.veredicto)}</h3>
      <p class="crm-gris" style="margin-top:0">Recibida ${esc(fmtFechaHora(c.creada_en))}${c.desde_cache ? " · resultado del caché (menos de 24 hs)" : ""}
        · N° ${esc(c.codigo)}</p>
      <table class="fm-tabla">
        <tr><th>Actividad</th><td>${esc(c.actividad || "—")}</td></tr>
        <tr><th>Nombre</th><td>${esc(c.nombre || "—")}</td></tr>
        <tr><th>E-mail</th><td>${c.email ? `<a href="mailto:${esc(c.email)}">${esc(c.email)}</a>` : "—"}</td></tr>
        <tr><th>Teléfono</th><td>${c.telefono ? (wa ? `<a href="${esc(wa)}" target="_blank" rel="noopener">${esc(c.telefono)}</a>` : esc(c.telefono)) : "—"}</td></tr>
        <tr><th>Web o redes</th><td>${web ? `<a href="${esc(web)}" target="_blank" rel="noopener noreferrer">${esc(c.web)}</a>` : esc(c.web || "—")}</td></tr>
        <tr><th>Viene de</th><td>${esc(c.origen || "—")}</td></tr>
      </table>
      ${c.veredicto === "error" ? `<p class="crm-alerta crm-alerta-info">INPI no respondió a tiempo${c.error ? ` (${esc(c.error)})` : ""}. A la persona se le avisó que la consulta se revisa a mano: conviene buscar la marca en INPI y escribirle.</p>` : ""}
      ${filas ? `<h4>Marcas de INPI que coinciden</h4>
        <table class="cl-tabla"><thead><tr><th>Denominación</th><th>Clase</th><th>Estado</th><th>Parecido</th><th></th></tr></thead><tbody>${filas}</tbody></table>
        ${c.posible_mas ? '<p class="cl-pie">INPI devolvió la cantidad máxima de marcas por búsqueda: puede haber más que no se vieron.</p>' : ""}`
        : (c.veredicto !== "error" ? '<p class="cl-pie">INPI no devolvió ninguna marca parecida.</p>' : "")}
      ${c.adicionales.length ? `<h4>Preguntas adicionales (${c.adicionales.length})</h4>${c.adicionales.map((a) => `
        <div class="cl-caja"><small class="crm-gris">${esc(fmtFechaHora(a.creada_en))} · ${esc(a.nombre || "")} · ${esc(a.email || "")}</small>
          <p style="white-space:pre-wrap;margin:6px 0 0">${esc(a.pregunta)}</p></div>`).join("")}` : ""}
      ${c.aviso_error ? `<p class="crm-error">El mail de aviso al equipo no salió: ${esc(c.aviso_error)}</p>` : ""}
      <div class="cl-acciones">
        <button type="button" class="cl-btn" id="cw-d-estado">${c.estado === "nueva" ? "Marcar revisada" : "Volver a nueva"}</button>
        <button type="button" class="cl-btn peligro" id="cw-d-borrar">Borrar</button>
      </div></div>`;
    cont.querySelectorAll("[data-acta]").forEach((b) => b.addEventListener("click", () => abrirActa(b.dataset.acta)));
    $("cw-d-estado").addEventListener("click", () =>
      cambiarEstado(id, c.estado === "nueva" ? "revisada" : "nueva", () => { cerrarModal(); }));
    $("cw-d-borrar").addEventListener("click", async () => {
      if (!confirm(`¿Borrar la consulta de «${c.marca}»? No se puede deshacer.`)) return;
      try { await apiJson(`/api/consultas-web/${id}`, "DELETE"); cerrarModal(); await cargar(); }
      catch (e) { mostrarAviso(`No se pudo borrar (${e.message}).`, "aviso"); }
    });
  } catch (e) {
    cont.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`;
  }
}

/* ── Arranque ───────────────────────────────────────────────────── */
$("modal-consulta").addEventListener("click", cerrarModal);
document.querySelector("#modal-consulta .modal-cerrar").addEventListener("click", cerrarModal);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarModal(); });
$("cw-estado").addEventListener("change", cargar);
$("cw-veredicto").addEventListener("change", cargar);
let _t = null;
$("cw-q").addEventListener("input", () => { clearTimeout(_t); _t = setTimeout(cargar, 300); });
window.alRecargar = cargar;
cargar();
