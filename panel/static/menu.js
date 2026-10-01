/* Menú de arriba (todas las pantallas del panel):
 *  - submenús "Automatizaciones" y "Más": se abren al pasar el mouse (CSS) y
 *    también con un clic / Enter, para pantallas táctiles y teclado;
 *  - botón de chat (💬): abre un panel a la derecha que tapa 1/4 de la
 *    pantalla, con la sección /comentarios adentro, y un botón para cerrarlo;
 *  - avisos del chat: cada 30 s mira si hay mensajes nuevos. Si hay, el ícono
 *    cambia (burbuja llena), aparece un círculo rojo con la cantidad, el título
 *    de la pestaña muestra "(n)" y, si el mensaje llegó mientras mirabas el
 *    panel, sale un cartelito con quién escribió y qué dijo.
 * El HTML del menú está en cada página; acá solo se le da el comportamiento. */

(function () {
  // Dentro del propio panel de chat (iframe) no hace falta nada de esto.
  if (window.self !== window.top) return;

  const CADA_MS = 30000;      // cada cuánto se consulta si hay mensajes nuevos
  const CARTEL_MS = 9000;     // cuánto dura el cartelito
  const TITULO_ORIGINAL = document.title;

  // ── Submenús ──────────────────────────────────────────────────────────
  function cerrarGrupos(salvo) {
    document.querySelectorAll(".app-header .nav-grupo.abierto").forEach((g) => {
      if (g === salvo) return;
      g.classList.remove("abierto");
      const b = g.querySelector(":scope > .nav-boton");
      if (b) b.setAttribute("aria-expanded", "false");
    });
  }

  function iniciarGrupos() {
    document.querySelectorAll(".app-header .nav-grupo").forEach((g) => {
      const boton = g.querySelector(":scope > .nav-boton");
      if (!boton) return;
      boton.addEventListener("click", (e) => {
        e.stopPropagation();
        const abrir = !g.classList.contains("abierto");
        cerrarGrupos(g);
        g.classList.toggle("abierto", abrir);
        boton.setAttribute("aria-expanded", abrir ? "true" : "false");
      });
    });
    document.addEventListener("click", () => cerrarGrupos(null));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarGrupos(null); });
  }

  // ── Estado del chat: ícono, número, título de la pestaña ─────────────
  let panel = null;
  const panelAbierto = () => !!(panel && panel.classList.contains("abierto"));

  function guardado(clave, valor) {
    try {
      if (valor === undefined) return sessionStorage.getItem(clave);
      sessionStorage.setItem(clave, String(valor));
    } catch (_) { /* sin storage: no pasa nada */ }
    return null;
  }

  function pintarNuevos(n) {
    const boton = document.getElementById("link-comentarios");
    const badge = boton && boton.querySelector(".nav-badge");
    if (boton) {
      boton.classList.toggle("con-nuevos", n > 0);
      boton.title = n > 0 ? `Chat — ${n} mensaje${n !== 1 ? "s" : ""} nuevo${n !== 1 ? "s" : ""}` : "Chat";
      boton.setAttribute("aria-label", boton.title);
    }
    if (badge) badge.textContent = n > 0 ? (n > 9 ? "9+" : String(n)) : "";
    document.title = n > 0 ? `(${n}) ${TITULO_ORIGINAL}` : TITULO_ORIGINAL;
  }

  // ── Cartelito ─────────────────────────────────────────────────────────
  let cartel = null, timerCartel = null;

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function cerrarCartel() {
    clearTimeout(timerCartel);
    if (cartel) cartel.classList.remove("visible");
  }

  function mostrarCartel(u) {
    if (!cartel) {
      cartel = document.createElement("div");
      cartel.className = "cartel-chat";
      cartel.setAttribute("role", "status");
      cartel.setAttribute("aria-live", "polite");
      document.body.appendChild(cartel);
      cartel.addEventListener("click", (e) => {
        if (e.target.closest(".cartel-chat-cerrar")) { cerrarCartel(); return; }
        cerrarCartel();
        abrirChat(true);
      });
    }
    const quien = u.para_mi ? `${esc(u.autor)} te escribió` : `${esc(u.autor)} escribió en el chat`;
    const texto = u.texto.length > 120 ? u.texto.slice(0, 120) + "…" : u.texto;
    cartel.innerHTML = `
      <div class="cartel-chat-ico" aria-hidden="true">💬</div>
      <div class="cartel-chat-cuerpo">
        <strong>${quien}</strong>
        <span>${esc(texto)}</span>
        ${u.acta ? `<small>Acta ${esc(u.acta)}</small>` : ""}
      </div>
      <button type="button" class="cartel-chat-cerrar" aria-label="Cerrar aviso">✕</button>`;
    // reflow para que la animación arranque aunque ya estuviera creado
    void cartel.offsetWidth;
    cartel.classList.add("visible");
    clearTimeout(timerCartel);
    timerCartel = setTimeout(cerrarCartel, CARTEL_MS);
  }

  // ── Consulta de mensajes nuevos ───────────────────────────────────────
  let consultando = false;
  let primeraVez = true;

  async function marcarVisto() {
    try { await fetch("/api/comentarios/visto", { method: "POST", credentials: "include" }); } catch (_) {}
  }

  async function refrescarChat() {
    if (consultando) return;
    consultando = true;
    try {
      const r = await fetch("/api/comentarios/resumen", { credentials: "include" });
      if (r.status === 401 && typeof irAlLogin === "function") return irAlLogin();
      if (!r.ok) return;
      const d = await r.json();
      let nuevos = d.nuevos || 0;
      const u = d.ultimo;

      if (panelAbierto() && !document.hidden) {
        // Lo está viendo en este momento: se da por leído y se actualiza la lista del panel.
        if (nuevos > 0) {
          await marcarVisto();
          const w = panel.querySelector("iframe").contentWindow;
          if (w && typeof w.alCambiarComentarios === "function") w.alCambiarComentarios();
        }
        nuevos = 0;
      } else if (u) {
        // Cartelito solo para mensajes que llegan con la pantalla abierta (no para los que ya estaban al entrar).
        const ultimoAvisado = Number(guardado("chat_ultimo_avisado") || 0);
        if (!primeraVez && u.id > ultimoAvisado && !document.hidden) mostrarCartel(u);
        if (u.id > ultimoAvisado) guardado("chat_ultimo_avisado", u.id);
      }
      primeraVez = false;
      pintarNuevos(nuevos);
    } catch (_) { /* sin red: se reintenta en la próxima vuelta */ }
    finally { consultando = false; }
  }
  window.refrescarChat = refrescarChat;

  // ── Panel lateral ─────────────────────────────────────────────────────
  function crearPanel() {
    panel = document.createElement("aside");
    panel.className = "panel-coment";
    panel.id = "panel-coment";
    panel.setAttribute("aria-label", "Chat");
    panel.innerHTML = `
      <div class="panel-coment-cab">
        <strong>Chat</strong>
        <button type="button" class="panel-coment-cerrar" title="Cerrar" aria-label="Cerrar el chat">✕</button>
      </div>
      <iframe title="Chat interno"></iframe>`;
    document.body.appendChild(panel);
    panel.querySelector(".panel-coment-cerrar").addEventListener("click", () => abrirChat(false));
  }

  // abrirChat(true|false, acta?) -- también se puede llamar desde otras pantallas.
  async function abrirChat(abrir, acta) {
    const boton = document.getElementById("link-comentarios");
    if (!panel) {
      if (!abrir) return;
      crearPanel();
    }
    const iframe = panel.querySelector("iframe");
    if (abrir) {
      cerrarCartel();
      const url = "/comentarios?embebido=1" + (acta ? "&acta=" + encodeURIComponent(acta) : "");
      // Se carga al abrir por primera vez (o si piden un acta puntual); al reabrir se refresca la lista.
      if (!iframe.getAttribute("src") || acta) iframe.setAttribute("src", url);
      else if (iframe.contentWindow && iframe.contentWindow.alCambiarComentarios) iframe.contentWindow.alCambiarComentarios();
    }
    // reflow para que la animación arranque aunque se haya recién creado
    void panel.offsetWidth;
    panel.classList.toggle("abierto", !!abrir);
    if (boton) {
      boton.classList.toggle("activa", !!abrir);
      boton.setAttribute("aria-expanded", abrir ? "true" : "false");
    }
    if (abrir) {
      await marcarVisto();   // al abrir el chat, lo que había queda leído
      pintarNuevos(0);
    }
  }
  window.abrirPanelComentarios = (acta) => abrirChat(true, acta);

  function iniciarChat() {
    const boton = document.getElementById("link-comentarios");
    if (!boton) return;
    // En la propia pantalla /comentarios no hace falta el panel: el botón queda marcado y listo.
    if (location.pathname === "/comentarios") return;
    boton.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirChat(!panelAbierto());
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && panelAbierto()) abrirChat(false);
    });
    refrescarChat();
    setInterval(() => { if (!document.hidden) refrescarChat(); }, CADA_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refrescarChat(); });
  }

  function iniciar() { iniciarGrupos(); iniciarChat(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
