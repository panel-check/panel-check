/* Informes (/informes, menú Más): todos los informes preliminares cargados en las fichas de los
 * clientes (solapa Informes), agrupados por cliente y marca, para encontrarlos rápido.
 * Por cada marca: el informe al cliente (PDF) y el informe IA interno, con sus versiones anteriores.
 * Cada uno se abre en /informes/{id}. Depende de comun.js (api, fmtFechaHora).
 * Todo texto que viene de la base pasa por esc() antes de ir al HTML. */

const $ = (id) => document.getElementById(id);
const esc = (s) => (s ?? "").toString()
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const refValida = (r) => /^INFPR-\d+$/i.test(r || "");

function celdaCliente(lista, g) {
  if (!lista.length) {
    return `<small class="crm-gris">No cargado</small>${g.cliente_id ? `<br><a class="cl-btn sec mini" href="/clientes?cliente=${g.cliente_id}&ctab=informes">Cargar en la ficha</a>` : ""}`;
  }
  const u = lista[0];
  return `<a class="cl-btn mini" href="/informes/${u.id}" target="_blank" rel="noopener" title="Abre el PDF con su propio link">Ver PDF ↗</a>
    <a class="cl-btn sec mini" href="/api/informes/${u.id}/pdf">Descargar</a>
    <br><small class="crm-gris">${esc(u.fecha_informe || fmtFechaHora(u.generado_en))}${u.generado_por ? " · " + esc(u.generado_por) : ""}</small>
    ${anteriores(lista)}`;
}

function celdaInterno(lista, g) {
  if (!lista.length) {
    return `<small class="crm-gris">No cargado</small>${g.cliente_id ? `<br><a class="cl-btn sec mini" href="/clientes?cliente=${g.cliente_id}&ctab=informes">Cargar en la ficha</a>` : ""}`;
  }
  const u = lista[0];
  const tags = [
    u.identicas ? `<span class="cl-tag mal">${u.identicas} coincidencia${u.identicas !== 1 ? "s" : ""} exacta${u.identicas !== 1 ? "s" : ""}</span>` : '<span class="cl-tag ok">Sin coincidencias exactas</span>',
    u.relevantes ? `<span class="cl-tag aviso">${u.relevantes} antecedente${u.relevantes !== 1 ? "s" : ""} relevante${u.relevantes !== 1 ? "s" : ""}</span>` : "",
  ].join(" ");
  return `<a class="cl-btn mini" href="/informes/${u.id}" target="_blank" rel="noopener" title="Abre el informe interno completo">Ver interno ↗</a>
    <br>${tags}
    <br><small class="crm-gris">${esc(fmtFechaHora(u.generado_en))}${u.generado_por ? " · " + esc(u.generado_por) : ""}</small>
    ${anteriores(lista)}`;
}

// Versiones anteriores de la misma marca (se cargó otra vez el JSON, por una corrección).
function anteriores(lista) {
  if (lista.length < 2) return "";
  const resto = lista.slice(1);
  return `<details class="inf-versiones"><summary><small>${resto.length} versión${resto.length !== 1 ? "es" : ""} anterior${resto.length !== 1 ? "es" : ""}</small></summary>
    ${resto.map((v) => `<div><a href="/informes/${v.id}" target="_blank" rel="noopener"><small>${esc(fmtFechaHora(v.generado_en))}${v.generado_por ? " · " + esc(v.generado_por) : ""} ↗</small></a></div>`).join("")}
  </details>`;
}

async function cargar() {
  const lista = $("inf-lista");
  const p = new URLSearchParams({ q: $("inf-q").value.trim(), falta: $("inf-falta").value });
  try {
    const r = await api(`/api/informes?${p}`);
    const grupos = r.grupos || [];
    if (!grupos.length) {
      lista.innerHTML = `<p class="vacio">${$("inf-q").value || $("inf-falta").value ? "No hay informes con ese filtro." : "Todavía no hay informes cargados. Se cargan desde la ficha del cliente, solapa Informes."}</p>`;
      return;
    }
    lista.innerHTML = `<table class="cl-tabla"><thead><tr><th>Marca</th><th>Cliente</th><th>Informe al cliente</th><th>Informe IA interno</th><th>Último</th></tr></thead><tbody>
      ${grupos.map((g) => `<tr>
        <td><strong>${esc(g.marca || "—")}</strong>${refValida(g.referencia) ? `<br><span class="cl-tag">${esc(g.referencia.toUpperCase())}</span>` : ""}</td>
        <td>${g.cliente_id ? `<a href="/clientes?cliente=${g.cliente_id}&ctab=informes" title="Abrir la ficha en la solapa Informes">${esc(g.cliente_nombre || "#" + g.cliente_id)}</a>` : '<small class="crm-gris">—</small>'}</td>
        <td>${celdaCliente(g.cliente, g)}</td>
        <td>${celdaInterno(g.interno, g)}</td>
        <td>${esc(fmtFechaHora(g.ultimo))}</td>
      </tr>`).join("")}
      </tbody></table><p class="cl-pie">${grupos.length} marca(s).</p>`;
  } catch (e) {
    lista.innerHTML = `<p class="crm-error">No se pudo cargar (${esc(e.message)}).</p>`;
  }
}

$("inf-falta").addEventListener("change", cargar);
let _t = null;
$("inf-q").addEventListener("input", () => { clearTimeout(_t); _t = setTimeout(cargar, 300); });
window.alRecargar = cargar;
cargar();
