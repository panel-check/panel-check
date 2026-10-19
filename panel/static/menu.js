/* Menú de arriba (todas las pantallas del panel):
 *  - submenús "Automatizaciones" y "Más": se abren al pasar el mouse (CSS) y
 *    también con un clic / Enter, para pantallas táctiles y teclado;
 *  - botón de chat (💬): abre un panel a la derecha que tapa 1/4 de la
 *    pantalla, con la sección /comentarios adentro, y un botón para cerrarlo;
 *  - avisos del chat: cada 30 s mira si hay mensajes nuevos. Si hay, el ícono
 *    cambia (burbuja llena), aparece un círculo rojo con la cantidad, el título
 *    de la pestaña muestra "(n)" y, si el mensaje llegó mientras mirabas el
 *    panel, sale un cartelito con quién escribió y qué dijo;
 *  - lupa (🔍), teléfono (📞) y rayito (⚡), que se agregan solos al principio de
 *    la barra: búsqueda rápida de una marca, «Agendar llamada» (se abre en un panel
 *    a la derecha, igual que el chat) y accesos rápidos a búsquedas guardadas;
 *  - logo de Meet, que se agrega solo al lado del chat: abre la sala fija de
 *    reuniones internas del equipo;
 *  - submenú de segundo nivel dentro de «Más» (Automatizaciones → Boletines, Crons).
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
      cerrarSubgrupos(g);
    });
  }

  // Sub-submenús (los que están adentro de «Más»).
  function cerrarSubgrupos(dentroDe) {
    (dentroDe || document).querySelectorAll(".app-header .nav-sub-grupo.abierto").forEach((sg) => {
      sg.classList.remove("abierto");
      const b = sg.querySelector(":scope > .nav-sub-boton");
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
    document.querySelectorAll(".app-header .nav-sub-grupo").forEach((sg) => {
      const boton = sg.querySelector(":scope > .nav-sub-boton");
      if (!boton) return;
      boton.addEventListener("click", (e) => {
        e.stopPropagation();   // «Más» sigue abierto
        const abrir = !sg.classList.contains("abierto");
        cerrarSubgrupos(sg.parentElement);
        sg.classList.toggle("abierto", abrir);
        boton.setAttribute("aria-expanded", abrir ? "true" : "false");
      });
    });
    // Al sacar el mouse de «Más», el sub-submenú vuelve a quedar cerrado.
    document.querySelectorAll(".app-header .nav-grupo").forEach((g) => {
      g.addEventListener("mouseleave", () => cerrarSubgrupos(g));
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
    // Mismo aviso en la versión celular: el puntito del cuadrado «Chat» en la
    // hoja de secciones, y el de la pestaña «Más» para que se note sin
    // necesidad de abrirla.
    const puntoChat = document.querySelector('.seccion-tile[data-accion="chat"] .seccion-tile-badge');
    if (puntoChat) puntoChat.hidden = !(n > 0);
    const puntoMas = document.querySelector(".tab-bar-mobil .tab-mas .tab-badge");
    if (puntoMas) puntoMas.hidden = !(n > 0);
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
      if (typeof agendaAbierta === "function" && agendaAbierta()) abrirAgenda(false);   // comparten el mismo lugar
      if (typeof sheetAbierto === "function" && sheetAbierto()) abrirSheet(false);      // idem con la hoja de secciones (celular)
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
    const tileChat = document.querySelector('.seccion-tile[data-accion="chat"]');
    if (tileChat) tileChat.classList.toggle("activa", !!abrir);
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


  // ── Accesos rápidos (⚡) ───────────────────────────────────────────────
  // Búsquedas guardadas de la lista de leads. Se pasa el mouse por el rayito
  // y aparecen: las mías (con cuántas marcas da cada una hoy), las que
  // compartió el equipo y "Guardar la búsqueda actual". Guardar una búsqueda
  // = guardar el mismo querystring de filtros que ya muestra la barra de
  // direcciones de Leads; abrirla es ir a /?<filtros>. Se guardan en el
  // servidor (/api/accesos-rapidos), personales, con opción de compartir.
  const RAYO_SVG = '<svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>';
  const acc = { grupo: null, menu: null, datos: null, ultima: 0, cargando: false };

  async function apiAcc(ruta, metodo, cuerpo) {
    const r = await fetch(ruta, {
      method: metodo || "GET",
      credentials: "include",
      headers: cuerpo ? { "Content-Type": "application/json" } : undefined,
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
    });
    if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
    if (!r.ok) {
      let detalle = `Error ${r.status}`;
      try { detalle = (await r.json()).detail || detalle; } catch (_) {}
      throw new Error(detalle);
    }
    return r.json();
  }

  const puedeGuardar = () => typeof window.consultaParaGuardar === "function";
  const nFmt = (n) => (n === null || n === undefined) ? "–" : Number(n).toLocaleString("es-AR");

  function itemAcceso(a) {
    const href = "/?" + a.consulta;
    const dueno = a.mio ? "" : `<span class="acceso-dueno">de ${esc(a.dueno)}</span>`;
    const compartido = a.mio && a.compartido ? '<span class="acceso-compartido" title="Compartido con el equipo">👥</span>' : "";
    const botones = a.mio
      ? `<span class="acceso-acc">
           <button type="button" class="acceso-btn" data-acc="editar" title="Editar, compartir o actualizar con los filtros actuales" aria-label="Editar ${esc(a.nombre)}">✎</button>
           <button type="button" class="acceso-btn" data-acc="borrar" title="Borrar" aria-label="Borrar ${esc(a.nombre)}">🗑</button>
         </span>`
      : "";
    return `<div class="acceso" data-id="${a.id}">
      <a class="acceso-link" href="${esc(href)}" data-consulta="${esc(a.consulta)}" role="menuitem" title="${esc(a.nombre)}">
        <span class="acceso-nombre">${esc(a.nombre)}${compartido}${dueno}</span>
        <span class="acceso-cant" data-cant="${a.id}">…</span>
      </a>${botones}
    </div>`;
  }

  // Pantallas que siempre están en el ⚡ (no son búsquedas guardadas: son links fijos para todo el equipo).
  const PANTALLAS_RAPIDAS = [{ nombre: "🗓 Disponibilidad", href: "/disponibilidad", titulo: "Los horarios libres para agendar reuniones" }];
  function pantallasRapidas() {
    return `<div class="acceso-titulo">Pantallas</div>` + PANTALLAS_RAPIDAS.map((p) =>
      `<div class="acceso"><a class="acceso-link" href="${esc(p.href)}" role="menuitem" title="${esc(p.titulo)}"><span class="acceso-nombre">${esc(p.nombre)}</span></a></div>`).join("");
  }

  function renderAccesos() {
    const d = acc.datos;
    if (!d) { acc.menu.innerHTML = pantallasRapidas() + '<div class="acceso-vacio">Cargando…</div>'; return; }
    let html = pantallasRapidas();
    if (d.propios.length) {
      html += `<div class="acceso-titulo">Mis accesos</div>` + d.propios.map(itemAcceso).join("");
    } else {
      html += `<div class="acceso-vacio">Todavía no guardaste ninguna búsqueda. Armá en <strong>Leads</strong> los filtros que usás siempre y guardalos acá para volver con un clic.</div>`;
    }
    if (d.compartidos.length) {
      html += `<div class="acceso-titulo">Del equipo</div>` + d.compartidos.map(itemAcceso).join("");
    }
    html += `<div class="acceso-pie">` + (puedeGuardar()
      ? `<button type="button" class="acceso-guardar" data-acc="guardar">＋ Guardar la búsqueda actual…</button>`
      : `<div class="acceso-nota">Para guardar una búsqueda, andá a Leads y elegí los filtros.</div>
         <a class="acceso-ir" href="/">Ir a Leads →</a>`) + `</div>`;
    acc.menu.innerHTML = html;
  }

  async function cargarAccesos(forzar) {
    if (acc.cargando) return;
    if (!forzar && acc.datos && Date.now() - acc.ultima < 20000) return; // el mouse pasa seguido: no pedir de más
    acc.cargando = true;
    try {
      acc.datos = await apiAcc("/api/accesos-rapidos");
      acc.ultima = Date.now();
      renderAccesos();
      // Las cantidades tardan un poco más (una consulta por búsqueda): llegan después.
      apiAcc("/api/accesos-rapidos/cantidades").then((c) => {
        acc.menu.querySelectorAll("[data-cant]").forEach((el) => {
          const n = c[el.dataset.cant];
          el.textContent = nFmt(n);
          el.classList.toggle("hay", typeof n === "number" && n > 0);
        });
      }).catch(() => {
        acc.menu.querySelectorAll("[data-cant]").forEach((el) => { el.textContent = "–"; });
      });
    } catch (e) {
      acc.menu.innerHTML = `<div class="acceso-vacio">No se pudieron cargar los accesos rápidos (${esc(e.message)}).</div>`;
    } finally {
      acc.cargando = false;
    }
  }

  function cerrarMenuAccesos() {
    acc.grupo.classList.remove("abierto");
    acc.grupo.classList.add("sin-hover"); // el mouse sigue encima: sin esto el CSS lo dejaría abierto
    acc.grupo.querySelector(".nav-boton").setAttribute("aria-expanded", "false");
  }

  // Ventana para guardar / editar un acceso. `a` = el acceso a editar (o null para uno nuevo).
  function abrirModalAcceso(a) {
    let fondo = document.getElementById("modal-acceso");
    if (!fondo) {
      fondo = document.createElement("div");
      fondo.id = "modal-acceso";
      fondo.className = "modal-fondo";
      fondo.addEventListener("click", (e) => { if (e.target === fondo) fondo.classList.remove("abierto"); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") fondo.classList.remove("abierto"); });
      document.body.appendChild(fondo);
    }
    const hayFiltros = puedeGuardar();
    const resumen = hayFiltros && typeof window.describirFiltrosActuales === "function" ? window.describirFiltrosActuales() : [];
    const textoResumen = resumen.length ? esc(resumen.join(" · ")) : "Sin filtros: todas las marcas";
    fondo.innerHTML = `
      <div class="modal-caja modal-acceso" role="dialog" aria-modal="true" aria-label="${a ? "Editar acceso rápido" : "Guardar acceso rápido"}">
        <button type="button" class="modal-cerrar" aria-label="Cerrar">&times;</button>
        <h3 class="titulo-coment">⚡ ${a ? "Editar acceso rápido" : "Guardar búsqueda actual"}</h3>
        <label class="campo">Nombre
          <input type="text" id="acceso-nombre" maxlength="60" placeholder="Ej.: Oposiciones sin contactar" value="${a ? esc(a.nombre) : ""}" autocomplete="off" />
        </label>
        <label class="check">
          <input type="checkbox" id="acceso-compartir" ${a && a.compartido ? "checked" : ""} />
          <span>Compartir con el equipo<small>Lo van a ver todos en su ⚡, pero solo vos lo podés cambiar o borrar.</small></span>
        </label>
        ${a && hayFiltros ? `<label class="check">
          <input type="checkbox" id="acceso-actualizar" />
          <span>Actualizar con los filtros que tengo ahora<small>Si no lo tildás, se mantiene la búsqueda que ya tenía guardada.</small></span>
        </label>` : ""}
        ${(!a && hayFiltros) || (a && hayFiltros) ? `<div class="acceso-resumen"><strong>Filtros de ahora:</strong> ${textoResumen}</div>` : ""}
        <div class="acceso-error" id="acceso-error" hidden></div>
        <div class="acciones-modal">
          <button type="button" id="acceso-cancelar">Cancelar</button>
          <button type="button" class="principal" id="acceso-guardar">${a ? "Guardar cambios" : "Guardar"}</button>
        </div>
      </div>`;
    fondo.classList.add("abierto");
    const nombre = fondo.querySelector("#acceso-nombre");
    const error = fondo.querySelector("#acceso-error");
    const cerrar = () => fondo.classList.remove("abierto");
    fondo.querySelector(".modal-cerrar").addEventListener("click", cerrar);
    fondo.querySelector("#acceso-cancelar").addEventListener("click", cerrar);
    setTimeout(() => { nombre.focus(); nombre.select(); }, 30);

    const guardar = async () => {
      const btn = fondo.querySelector("#acceso-guardar");
      error.hidden = true;
      btn.disabled = true;
      try {
        const compartido = fondo.querySelector("#acceso-compartir").checked;
        if (a) {
          const cambios = { nombre: nombre.value, compartido };
          const act = fondo.querySelector("#acceso-actualizar");
          if (act && act.checked) cambios.consulta = window.consultaParaGuardar();
          await apiAcc(`/api/accesos-rapidos/${a.id}`, "PUT", cambios);
        } else {
          await apiAcc("/api/accesos-rapidos", "POST", { nombre: nombre.value, consulta: window.consultaParaGuardar(), compartido });
        }
        cerrar();
        acc.datos = null;
        if (typeof window.mostrarAviso === "function") window.mostrarAviso(a ? "Acceso rápido actualizado" : "Acceso rápido guardado ⚡");
      } catch (e) {
        error.textContent = e.message;
        error.hidden = false;
      } finally {
        btn.disabled = false;
      }
    };
    fondo.querySelector("#acceso-guardar").addEventListener("click", guardar);
    nombre.addEventListener("keydown", (e) => { if (e.key === "Enter") guardar(); });
  }

  async function borrarAcceso(a) {
    if (!confirm(`¿Borrar el acceso rápido «${a.nombre}»?${a.compartido ? "\nTambién deja de verse para el equipo." : ""}`)) return;
    try {
      await apiAcc(`/api/accesos-rapidos/${a.id}`, "DELETE");
      await cargarAccesos(true);
    } catch (e) {
      alert(`No se pudo borrar: ${e.message}`);
    }
  }

  function iniciarAccesos() {
    const nav = document.querySelector(".app-header .nav");
    if (!nav || document.querySelector(".nav-rayo")) return;
    const grupo = document.createElement("div");
    grupo.className = "nav-grupo nav-rayo";
    grupo.innerHTML = `
      <button type="button" class="nav-boton" aria-haspopup="true" aria-expanded="false" title="Accesos rápidos" aria-label="Accesos rápidos">${RAYO_SVG}</button>
      <div class="nav-sub accesos-menu" role="menu"></div>`;
    nav.insertBefore(grupo, nav.firstChild);
    acc.grupo = grupo;
    acc.menu = grupo.querySelector(".accesos-menu");
    renderAccesos();

    grupo.addEventListener("mouseenter", () => cargarAccesos(false));
    grupo.addEventListener("mouseleave", () => grupo.classList.remove("sin-hover"));
    grupo.querySelector(".nav-boton").addEventListener("click", () => { grupo.classList.remove("sin-hover"); cargarAccesos(false); });

    acc.menu.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-acc]");
      const fila = e.target.closest(".acceso");
      const a = fila && acc.datos
        ? [...acc.datos.propios, ...acc.datos.compartidos].find((x) => String(x.id) === fila.dataset.id)
        : null;
      if (btn) {
        e.preventDefault();
        e.stopPropagation();
        if (btn.dataset.acc === "guardar") { cerrarMenuAccesos(); abrirModalAcceso(null); }
        else if (btn.dataset.acc === "editar" && a) { cerrarMenuAccesos(); abrirModalAcceso(a); }
        else if (btn.dataset.acc === "borrar" && a) borrarAcceso(a);
        return;
      }
      const link = e.target.closest("a.acceso-link");
      // Estando en Leads, se aplican los filtros sin recargar la página.
      if (link && link.dataset.consulta !== undefined && typeof window.aplicarAccesoRapido === "function" && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
        e.preventDefault();
        window.aplicarAccesoRapido(link.dataset.consulta);
        cerrarMenuAccesos();
      }
    });
  }

  // ── Búsqueda rápida (🔍) ───────────────────────────────────────────────
  // Lupa al principio de la barra, en todas las pantallas. Al hacer clic se
  // desliza para el costado y deja escribir un número de acta, CUIT, mail,
  // nombre de la marca o del titular. Con Enter busca en Leads sobre TODAS las
  // marcas: Lead y Contactado en "Todos" y el resto de los filtros limpios.
  // Estando en Leads se aplica sin recargar (aplicarAccesoRapido); desde otra
  // pantalla lleva a Leads ya con la búsqueda hecha.
  const LUPA_SVG = '<svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';

  function iniciarBusqueda() {
    const nav = document.querySelector(".app-header .nav");
    if (!nav || document.querySelector(".nav-buscar")) return;
    const form = document.createElement("form");
    form.className = "nav-buscar";
    form.setAttribute("role", "search");
    form.innerHTML = `
      <button type="button" class="nav-boton nav-buscar-btn" title="Buscar una marca" aria-label="Buscar una marca" aria-expanded="false">${LUPA_SVG}</button>
      <input type="search" class="nav-buscar-input" placeholder="Acta, CUIT, mail, marca…" aria-label="Buscar por número de acta, CUIT, mail o nombre de la marca" autocomplete="off" maxlength="120" tabindex="-1" />`;
    nav.insertBefore(form, nav.firstChild);

    const boton = form.querySelector(".nav-buscar-btn");
    const campo = form.querySelector(".nav-buscar-input");

    function abrir(abrirla) {
      form.classList.toggle("abierto", abrirla);
      boton.setAttribute("aria-expanded", abrirla ? "true" : "false");
      campo.tabIndex = abrirla ? 0 : -1;
      if (abrirla) setTimeout(() => campo.focus(), 40);
      else campo.blur();
    }

    function buscar() {
      const texto = campo.value.trim();
      if (!texto) { abrir(false); return; }
      const consulta = `q=${encodeURIComponent(texto)}&es_lead=todos&contactado=todos`;
      if (typeof window.aplicarAccesoRapido === "function") {
        window.aplicarAccesoRapido(consulta);
        campo.value = "";
        abrir(false);
      } else {
        location.href = "/?" + consulta;
      }
    }

    boton.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!form.classList.contains("abierto")) abrir(true);
      else if (campo.value.trim()) buscar();
      else abrir(false);
    });
    form.addEventListener("submit", (e) => { e.preventDefault(); buscar(); });
    campo.addEventListener("click", (e) => e.stopPropagation());
    campo.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); campo.value = ""; abrir(false); boton.focus(); }
    });
    // Clic afuera: se vuelve a esconder, pero solo si no hay nada escrito.
    document.addEventListener("click", () => { if (form.classList.contains("abierto") && !campo.value.trim()) abrir(false); });
  }

  // ── Atajos: teléfono (📞) y Meet ───────────────────────────────────────
  // Teléfono: va entre la lupa y el rayito y abre «Agendar llamada» en un panel a la derecha
  // (tapa 1/4 de la pantalla, igual que el chat), sin sacarte de donde estás. Con Ctrl/Cmd+clic
  // o clic del medio se abre la pantalla completa en una pestaña nueva.
  // Meet: va al lado del chat y abre en una pestaña nueva la sala fija que el equipo deja abierta
  // para reuniones internas.
  const URL_AGENDAR = "https://panel.registrodemimarca.com.ar/agendar-llamada";
  const URL_MEET = "https://meet.google.com/tuo-iwyz-jpd?pli=1&authuser=1";
  const TEL_SVG = '<svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
  const MEET_SVG = '<svg class="nav-ico nav-ico-color" viewBox="0 0 87.5 72" aria-hidden="true"><path fill="#00832d" d="M49.5 36l8.53 9.75 11.47 7.33 2-17.02-2-16.64-11.69 6.44z"/><path fill="#0066da" d="M0 51.5V66c0 3.315 2.685 6 6 6h14.5l3-10.96-3-9.54-9.95-3z"/><path fill="#e94235" d="M20.5 0L0 20.5l10.55 3 9.95-3 2.95-9.41z"/><path fill="#2684fc" d="M20.5 20.5H0v31h20.5z"/><path fill="#00ac47" d="M82.6 8.68L69.5 19.42v33.66l13.16 10.79c1.97 1.54 4.85.135 4.85-2.37V11c0-2.535-2.945-3.925-4.91-2.32zM49.5 36v15.5h-29V72h43c3.315 0 6-2.685 6-6V53.08z"/><path fill="#ffba00" d="M63.5 0h-43v20.5h29V36l20-16.57V6c0-3.315-2.685-6-6-6z"/></svg>';

  // Panel lateral de «Agendar llamada»: mismo lugar y mismo aspecto que el del chat; se abre uno u otro.
  let panelAgenda = null;
  function agendaAbierta() { return !!(panelAgenda && panelAgenda.classList.contains("abierto")); }

  // Pestañas del panel: «Agendar» (el formulario) y «Disponibilidad» (los turnos libres de cada día). Cada una es
  // su propio iframe, así lo que se escribió en el formulario no se pierde al mirar la disponibilidad.
  // Cada pantalla del panel es una página nueva: para que el panel lateral no se cierre al cambiar de pantalla,
  // se recuerda (en esta pestaña del navegador) si estaba abierto y en qué pestaña, y se vuelve a abrir solo.
  // Lo que se había escrito en el formulario se recupera aparte, con el borrador de calendario_comun.js.
  const URL_PESTANA = { agendar: "/agendar-llamada?embebido=1", disponibilidad: "/disponibilidad?embebido=1" };
  let pestanaActual = URL_PESTANA[guardado("kom-agenda-pestana")] ? guardado("kom-agenda-pestana") : "agendar";
  function pestanaAgenda(nombre, alAbrir) {
    if (!panelAgenda || !URL_PESTANA[nombre]) return;
    const cambia = nombre !== pestanaActual;
    pestanaActual = nombre;
    guardado("kom-agenda-pestana", nombre);
    panelAgenda.querySelectorAll("button[data-pestana]").forEach((b) => b.setAttribute("aria-selected", b.dataset.pestana === nombre ? "true" : "false"));
    panelAgenda.querySelectorAll("iframe[data-pestana]").forEach((f) => {
      const es = f.dataset.pestana === nombre;
      f.hidden = !es;
      if (!es) return;
      // La disponibilidad se vuelve a pedir cada vez que se mira (puede haberse agendado algo en la otra pestaña).
      if (!f.getAttribute("src")) f.setAttribute("src", URL_PESTANA[nombre]);
      else if (nombre === "disponibilidad" && (cambia || alAbrir)) { try { f.contentWindow.location.reload(); } catch (_) { f.setAttribute("src", URL_PESTANA[nombre]); } }
      // «Agendar» no se recarga (se perdería lo que se estaba escribiendo), pero como el iframe
      // queda vivo entre aperturas, puede haber quedado scrolleado de la vez anterior: se vuelve
      // a arriba del todo (donde está «Completar con IA») cada vez que se abre o se vuelve a esta pestaña.
      else if (cambia || alAbrir) { try { f.contentWindow.scrollTo(0, 0); } catch (_) {} }
    });
  }

  function abrirAgenda(abrir) {
    const boton = document.querySelector(".nav-tel");
    if (!panelAgenda) {
      if (!abrir) return;
      panelAgenda = document.createElement("aside");
      panelAgenda.className = "panel-coment panel-agenda";
      panelAgenda.id = "panel-agenda";
      panelAgenda.setAttribute("aria-label", "Agendar llamada");
      panelAgenda.innerHTML = `
        <div class="panel-coment-cab">
          <strong>📅 Agendar llamada o reunión</strong>
          <button type="button" class="panel-coment-cerrar" title="Cerrar" aria-label="Cerrar Agendar llamada">✕</button>
        </div>
        <div class="panel-agenda-tabs" role="tablist">
          <button type="button" role="tab" data-pestana="agendar" aria-selected="true">📅 Agendar</button>
          <button type="button" role="tab" data-pestana="disponibilidad" aria-selected="false">🗓 Disponibilidad</button>
        </div>
        <iframe title="Agendar llamada" data-pestana="agendar"></iframe>
        <iframe title="Disponibilidad" data-pestana="disponibilidad" hidden></iframe>`;
      document.body.appendChild(panelAgenda);
      panelAgenda.querySelector(".panel-coment-cerrar").addEventListener("click", () => abrirAgenda(false));
      panelAgenda.querySelectorAll("[data-pestana]").forEach((b) => {
        if (b.tagName === "BUTTON") b.addEventListener("click", () => pestanaAgenda(b.dataset.pestana));
      });
      // Tocar un horario libre en «Disponibilidad» vuelve a «Agendar» con el día y la hora ya puestos.
      window.addEventListener("message", (ev) => {
        if (ev.origin !== location.origin || !ev.data || ev.data.tipo !== "agendar-turno") return;
        pestanaAgenda("agendar");
        const f = panelAgenda.querySelector('iframe[data-pestana="agendar"]');
        if (f && f.contentWindow) f.contentWindow.postMessage({ tipo: "completar-turno", fecha: ev.data.fecha, hora: ev.data.hora }, location.origin);
      });
    }
    if (abrir) {
      if (panelAbierto()) abrirChat(false);   // comparten el mismo lugar
      if (typeof sheetAbierto === "function" && sheetAbierto()) abrirSheet(false);   // idem con la hoja de secciones (celular)
      pestanaAgenda(pestanaActual, true);
    }
    void panelAgenda.offsetWidth;   // reflow para que la animación arranque aunque se haya recién creado
    panelAgenda.classList.toggle("abierto", !!abrir);
    guardado("kom-agenda-panel", abrir ? "1" : "0");
    if (boton) {
      boton.classList.toggle("activa", !!abrir);
      boton.setAttribute("aria-expanded", abrir ? "true" : "false");
    }
    // Mismo estado «activo» en la versión celular: la pestaña «Llamada» de
    // la barra de abajo y su cuadrado en la hoja de secciones.
    document.querySelectorAll('.tab-bar-mobil .tab[data-accion="llamada"], .seccion-tile[data-accion="llamada"]')
      .forEach((b) => b.classList.toggle("activa", !!abrir));
  }

  function enlaceNav(clase, href, titulo, svg) {
    const a = document.createElement("a");
    a.className = clase;
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = titulo;
    a.setAttribute("aria-label", titulo);
    a.innerHTML = svg;
    return a;
  }

  function iniciarAtajos() {
    const nav = document.querySelector(".app-header .nav");
    if (!nav) return;
    if (!nav.querySelector(".nav-tel")) {
      const tel = enlaceNav("nav-tel", URL_AGENDAR, "Agendar una llamada", TEL_SVG);
      tel.setAttribute("aria-expanded", "false");
      tel.addEventListener("click", (e) => {
        // Con Ctrl/Cmd/Mayús o clic del medio se deja que el navegador abra la pantalla completa.
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        abrirAgenda(!agendaAbierta());
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && agendaAbierta()) abrirAgenda(false); });
      const lupa = nav.querySelector(".nav-buscar");
      nav.insertBefore(tel, lupa ? lupa.nextSibling : nav.firstChild);
    }
    if (!nav.querySelector(".nav-meet")) {
      const meet = enlaceNav("nav-meet", URL_MEET, "Reunión interna (Google Meet)", MEET_SVG);
      nav.insertBefore(meet, document.getElementById("link-comentarios"));   // si no hay chat, queda al final
    }
    // Si el panel de «Agendar llamada» estaba abierto en la pantalla anterior, sigue abierto en esta.
    if (window.top === window && guardado("kom-agenda-panel") === "1") abrirAgenda(true);
  }

  // ── Secciones (celular) ──────────────────────────────────────────────
  // A partir de 640px de ancho (mismo corte que las tarjetas de Leads y de
  // la ficha de titular) el menú de arriba se esconde — ver el CSS — y se
  // reemplaza por esto: una barra fija abajo con 4 accesos (Leads,
  // Calendario, Llamada, Clientes) + «Más», que abre una hoja con esas 4
  // más el resto de las secciones. En «/» la hoja se abre sola al entrar:
  // es el «home» de la versión celular. Llamada y Chat no son páginas,
  // abren el panel que ya existe (Agendar llamada / Chat). Boceto aprobado
  // 18/10/2026 (variante B: hoja que sube desde la barra de abajo).
  const FIJAS = [
    { id: "leads", nombre: "Leads", href: "/", color: "#015197",
      svg: '<path d="M3 5h18M3 12h18M3 19h12"/>' },
    { id: "calendario", nombre: "Calendario", href: "/calendario", color: "#015197",
      svg: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>' },
    { id: "llamada", nombre: "Llamada", color: "#1a8754", accion: "llamada",
      svg: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>' },
    { id: "clientes", nombre: "Clientes", href: "/clientes", color: "#015197",
      svg: '<path d="M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20"/><circle cx="10" cy="8" r="3.5"/><path d="M20 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M15 4.6a3.5 3.5 0 0 1 0 6.8"/>' },
  ];
  const SECCIONES = [
    { id: "crm", nombre: "CRM", href: "/crm", color: "#015197",
      svg: '<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="11" rx="1.5"/><rect x="17" y="4" width="4" height="7" rx="1.5"/>' },
    { id: "boletines", nombre: "Boletines", href: "/boletines", color: "#475569",
      svg: '<path d="M4 4h13a2 2 0 0 1 2 2v14H6a2 2 0 0 1-2-2z"/><path d="M19 8h1a1 1 0 0 1 1 1v9a2 2 0 0 1-2 2"/><path d="M8 8h7M8 12h7M8 16h4"/>' },
    { id: "crons", nombre: "Crons", href: "/crons", color: "#475569",
      svg: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>' },
    { id: "consultas-web", nombre: "Consultas web", href: "/consultas-web", color: "#475569",
      svg: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>' },
    { id: "mails", nombre: "Mails", href: "/mails", color: "#475569",
      svg: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>' },
    { id: "chat", nombre: "Chat", color: "#1a8754", accion: "chat",
      svg: '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' },
    { id: "ayuda", nombre: "Ayuda", href: "/ayuda", color: "#e8f1fa", outline: true,
      svg: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6"/><path d="M12 17h.01"/>' },
  ];
  const ID_FIJAS = FIJAS.map((s) => s.id);

  // Qué sección es la pantalla actual, para marcar «activo» en la barra y
  // en la hoja. La ficha de un lead (/titular/...) cuenta como Leads: se
  // llega siempre desde ahí.
  function seccionActual() {
    const p = location.pathname;
    if (p === "/" || p.startsWith("/titular/")) return "leads";
    const mapa = {
      "/calendario": "calendario", "/clientes": "clientes", "/crm": "crm",
      "/boletines": "boletines", "/crons": "crons", "/consultas-web": "consultas-web",
      "/mails": "mails", "/ayuda": "ayuda",
      "/agendar-llamada": "llamada", "/disponibilidad": "llamada",
    };
    return mapa[p] || null;
  }
  function masActivo() {
    const s = seccionActual();
    return s !== null && !ID_FIJAS.includes(s);
  }

  function cuadradoHtml(s) {
    const activa = s.id === seccionActual();
    const borde = s.outline ? "1.5px solid #015197" : "1px solid var(--borde)";
    const fondoCaja = activa ? "var(--azul-claro)" : "#fff";
    const fondoIcono = s.outline ? "#e8f1fa" : s.color;
    const colorIcono = s.outline ? "#015197" : "#fff";
    return `
      <button type="button" class="seccion-tile${activa ? " activa" : ""}" data-seccion="${s.id}" data-accion="${s.accion || ""}" data-href="${s.href || ""}" style="border:${borde};background:${fondoCaja};">
        <span class="seccion-tile-ico" style="width:44px;height:44px;background:${fondoIcono};">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="${colorIcono}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${s.svg}</svg>
        </span>
        <span class="seccion-tile-badge" hidden></span>
        <span class="seccion-tile-txt">${s.nombre}</span>
      </button>`;
  }

  let sheetEl = null;

  function sheetAbierto() { return !!(sheetEl && sheetEl.classList.contains("abierto")); }

  function crearSheet() {
    sheetEl = document.createElement("div");
    sheetEl.className = "sheet-secciones";
    sheetEl.innerHTML = `
      <div class="sheet-secciones-scrim"></div>
      <div class="sheet-secciones-hoja" role="dialog" aria-modal="true" aria-label="Elegir sección">
        <button type="button" class="sheet-secciones-agarre" aria-label="Cerrar"></button>
        <div class="sheet-secciones-cab">
          <div class="sheet-secciones-titulo">Secciones</div>
          <button type="button" class="sheet-secciones-cerrar" aria-label="Cerrar">✕</button>
        </div>
        <div class="sheet-secciones-grid">
          ${FIJAS.map(cuadradoHtml).join("")}
          ${SECCIONES.map(cuadradoHtml).join("")}
        </div>
      </div>`;
    document.body.appendChild(sheetEl);
    sheetEl.querySelector(".sheet-secciones-scrim").addEventListener("click", () => abrirSheet(false));
    sheetEl.querySelector(".sheet-secciones-agarre").addEventListener("click", () => abrirSheet(false));
    sheetEl.querySelector(".sheet-secciones-cerrar").addEventListener("click", () => abrirSheet(false));
    sheetEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".seccion-tile");
      if (!btn) return;
      const accion = btn.dataset.accion;
      if (accion === "chat") { abrirSheet(false); abrirChat(true); return; }
      if (accion === "llamada") { abrirSheet(false); abrirAgenda(true); return; }
      const href = btn.dataset.href;
      abrirSheet(false);
      if (href && href !== location.pathname) location.href = href;
    });
  }

  function abrirSheet(abrir) {
    if (!sheetEl) {
      if (!abrir) return;
      crearSheet();
    }
    if (abrir) {
      if (panelAbierto()) abrirChat(false);         // comparten el mismo lugar
      if (agendaAbierta()) abrirAgenda(false);
    }
    void sheetEl.offsetWidth;   // reflow para que la animación arranque aunque se haya recién creado
    sheetEl.classList.toggle("abierto", !!abrir);
    const tabMas = document.querySelector(".tab-bar-mobil .tab-mas");
    if (tabMas) tabMas.setAttribute("aria-expanded", abrir ? "true" : "false");
  }

  function crearBarraMobil() {
    const barra = document.createElement("nav");
    barra.className = "tab-bar-mobil";
    barra.setAttribute("aria-label", "Secciones (celular)");
    barra.innerHTML = FIJAS.map((s) => {
      const activa = s.id === seccionActual();
      return `<button type="button" class="tab${activa ? " activa" : ""}" data-seccion="${s.id}" data-accion="${s.accion || ""}" data-href="${s.href || ""}">
        <svg class="tab-ico" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${s.svg}</svg>
        <span class="tab-badge" hidden></span>
        <span class="tab-txt">${s.nombre}</span>
      </button>`;
    }).join("") + `
      <button type="button" class="tab tab-mas${masActivo() ? " activa" : ""}" aria-haspopup="true" aria-expanded="false">
        <svg class="tab-ico" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>
        <span class="tab-badge" hidden></span>
        <span class="tab-txt">Más</span>
      </button>`;
    document.body.appendChild(barra);
    barra.addEventListener("click", (e) => {
      const btn = e.target.closest(".tab");
      if (!btn) return;
      if (btn.classList.contains("tab-mas")) { abrirSheet(!sheetAbierto()); return; }
      const accion = btn.dataset.accion;
      if (accion === "llamada") { abrirAgenda(!agendaAbierta()); return; }
      const href = btn.dataset.href;
      if (href && href !== location.pathname) location.href = href;
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheetAbierto()) abrirSheet(false); });
  }

  function iniciarSecciones() {
    if (!document.querySelector(".app-header")) return;
    if (document.querySelector(".tab-bar-mobil")) return;
    crearBarraMobil();
    // Home de la versión celular: al entrar en «/» la hoja se abre sola — pero una sola vez
    // por sesión del navegador, no cada vez que se vuelve a Leads desde otra sección (era molesto).
    if (location.pathname === "/" && window.matchMedia("(max-width: 640px)").matches && !guardado("kom-sheet-mostrada")) {
      abrirSheet(true);
      guardado("kom-sheet-mostrada", "1");
    }
  }

  function iniciar() { iniciarSecciones(); iniciarAccesos(); iniciarBusqueda(); iniciarAtajos(); iniciarGrupos(); iniciarChat(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
