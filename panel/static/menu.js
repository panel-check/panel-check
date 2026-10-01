/* Menú de arriba (todas las pantallas del panel):
 *  - submenús "Automatizaciones" y "Más": se abren al pasar el mouse (CSS) y
 *    también con un clic / Enter, para pantallas táctiles y teclado;
 *  - botón de comentarios (💬): abre un panel a la derecha que tapa 1/4 de la
 *    pantalla, con la sección /comentarios adentro, y un botón para cerrarlo.
 * El HTML del menú está en cada página; acá solo se le da el comportamiento. */

(function () {
  // Dentro del propio panel de comentarios (iframe) no hace falta nada de esto.
  if (window.self !== window.top) return;

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

  // ── Panel lateral de comentarios ──────────────────────────────────────
  let panel = null;

  function contarNuevos() {
    if (typeof actualizarContadorComentarios === "function") return actualizarContadorComentarios();
    // Páginas sin comun.js (Ayuda, Mi cuenta): mismo contador, hecho acá.
    const badge = document.querySelector("#link-comentarios .nav-badge");
    if (!badge) return;
    fetch("/api/comentarios/resumen", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((r) => {
        if (!r) return;
        badge.textContent = r.nuevos ? String(r.nuevos) : "";
        badge.title = r.nuevos ? `${r.nuevos} nuevo(s) desde la última vez que entraste` : "";
      })
      .catch(() => {});
  }

  function crearPanel() {
    panel = document.createElement("aside");
    panel.className = "panel-coment";
    panel.id = "panel-coment";
    panel.setAttribute("aria-label", "Comentarios");
    panel.innerHTML = `
      <div class="panel-coment-cab">
        <strong>Comentarios</strong>
        <button type="button" class="panel-coment-cerrar" title="Cerrar" aria-label="Cerrar comentarios">✕</button>
      </div>
      <iframe title="Comentarios internos"></iframe>`;
    document.body.appendChild(panel);
    panel.querySelector(".panel-coment-cerrar").addEventListener("click", () => abrirComentarios(false));
    // El contador se actualiza cuando la pantalla de comentarios ya marcó todo como visto.
    panel.querySelector("iframe").addEventListener("load", () => setTimeout(contarNuevos, 1500));
  }

  // abrirComentarios(true|false, acta?) -- también se puede llamar desde otras pantallas.
  function abrirComentarios(abrir, acta) {
    const boton = document.getElementById("link-comentarios");
    if (!panel) {
      if (!abrir) return;
      crearPanel();
    }
    const iframe = panel.querySelector("iframe");
    if (abrir) {
      const url = "/comentarios?embebido=1" + (acta ? "&acta=" + encodeURIComponent(acta) : "");
      // Se carga al abrir por primera vez (o si piden un acta puntual); al reabrir queda como estaba.
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
  }
  window.abrirPanelComentarios = (acta) => abrirComentarios(true, acta);

  function iniciarComentarios() {
    const boton = document.getElementById("link-comentarios");
    if (!boton) return;
    // En la propia pantalla /comentarios no hace falta el panel: el botón queda marcado y listo.
    if (location.pathname === "/comentarios") return;
    boton.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirComentarios(!(panel && panel.classList.contains("abierto")));
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && panel && panel.classList.contains("abierto")) abrirComentarios(false);
    });
    if (typeof actualizarContadorComentarios !== "function") contarNuevos();
  }

  function iniciar() { iniciarGrupos(); iniciarComentarios(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
