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

// Desde la pestaña «Disponibilidad» del panel lateral: tocar un horario libre completa el día y la hora del formulario.
window.addEventListener("message", (ev) => {
  if (ev.origin !== location.origin || !ev.data || ev.data.tipo !== "completar-turno") return;
  const { fecha, hora } = ev.data;
  if (!document.getElementById("mc-fecha")) return;
  document.getElementById("mc-fecha").value = fecha;
  document.getElementById("mc-fecha").dispatchEvent(new Event("change"));
  document.getElementById("mc-todo").checked = false;
  if (typeof _calPintarTodoElDia === "function") _calPintarTodoElDia();
  document.getElementById("mc-hora").value = hora;
  document.getElementById("mc-hora").dispatchEvent(new Event("input"));
  document.getElementById("mc-dur").value = "30";
  document.getElementById("mc-hora").focus();
});

(async function iniciar() {
  let estado = null;
  try { estado = await api("/api/calendario/estado"); } catch (_) {}
  if (!estado || !estado.configurado) {
    $ag("ag-estado").innerHTML = '<p class="cal-aviso">El calendario todavía no está conectado con Google. La guía está en <a href="/calendario">Calendario</a>.</p>';
    return;
  }
  abrirFormulario();
})();
