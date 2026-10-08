/* Página /agendar-llamada: solo el formulario para agendar, pensada como acceso rápido.
 * Usa el mismo formulario del calendario (calendario_comun.js) abierto «embebido». */

const $ag = (id) => document.getElementById(id);

function resumenAgendado(r) {
  const e = r.evento;
  const cuando = `${calFechaLarga(e.fecha)} · ${calHoraTexto(e)}`;
  const aviso = r.aviso || {};
  const lineas = [];
  for (const [quien, x] of [["a la persona", aviso.persona], ["al equipo", aviso.equipo]]) {
    if (!x) continue;
    lineas.push(x.enviado ? `✉️ Aviso enviado ${quien} (${_escapeHtml(x.email)})`
                          : `⚠️ No se pudo avisar ${quien}: ${_escapeHtml(x.error || "error")}`);
  }
  const meet = e.meet_url
    ? `<p>🎥 Link de Meet: <a href="${_escapeHtml(e.meet_url)}" target="_blank" rel="noopener">${_escapeHtml(e.meet_url)}</a></p>
       <button type="button" class="cal-btn chico" id="ag-copiar">Copiar link</button>`
    : "";
  $ag("ag-exito").innerHTML = `
    <div class="agendar-exito">
      <h3>✔ Agendado: ${_escapeHtml(e.titulo)}</h3>
      <p>${_escapeHtml(cuando)}</p>
      ${meet}
      ${lineas.map(l => `<p>${l}</p>`).join("")}
      ${e.texto_whatsapp ? '<button type="button" class="cal-btn chico principal" id="ag-wsp" title="Un texto con el día, la hora y cómo es, para pasárselo a la persona por WhatsApp">💬 Texto para WhatsApp</button>' : ""}
      <a class="cal-btn chico" href="/calendario">Ver en el calendario</a>
    </div>`;
  $ag("ag-exito").hidden = false;
  const wsp = $ag("ag-wsp");
  if (wsp) wsp.addEventListener("click", () => calMostrarWhatsApp(e, { recienAgendado: true }));
  const copiar = $ag("ag-copiar");
  if (copiar) copiar.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(e.meet_url); copiar.textContent = "¡Copiado!"; }
    catch (_) { copiar.textContent = "Copialo a mano desde arriba"; }
  });
}

function abrirFormulario() {
  calAbrirModal({ embebido: true, alGuardar: (r) => { if (r && r.evento) resumenAgendado(r); abrirFormulario(); window.scrollTo({ top: 0, behavior: "smooth" }); } });
}

(async function iniciar() {
  let estado = null;
  try { estado = await api("/api/calendario/estado"); } catch (_) {}
  if (!estado || !estado.configurado) {
    $ag("ag-estado").innerHTML = '<p class="cal-aviso">El calendario todavía no está conectado con Google. La guía está en <a href="/calendario">Calendario</a>.</p>';
    return;
  }
  abrirFormulario();
})();
