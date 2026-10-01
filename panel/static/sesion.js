/* Menú de usuario del encabezado (todas las pantallas del panel): muestra con
 * qué usuario estás adentro y da acceso a Mi cuenta, Usuarios (admin) y
 * Cerrar sesión. También manda al login si la sesión venció. */

function irAlLogin() {
  const aca = location.pathname + location.search;
  location.href = "/login?next=" + encodeURIComponent(aca);
}

async function cerrarSesion() {
  try {
    await fetch("/api/logout", { method: "POST", credentials: "include" });
  } catch (_) { /* igual vamos al login */ }
  location.href = "/login";
}

(function () {
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function montar() {
    const nav = document.querySelector(".app-header .nav");
    if (!nav || document.getElementById("menu-usuario")) return;
    let yo;
    try {
      const r = await fetch("/api/yo", { credentials: "include" });
      if (r.status === 401) return irAlLogin();
      if (!r.ok) return;
      yo = await r.json();
    } catch (_) { return; }

    const cont = document.createElement("div");
    cont.className = "menu-usuario";
    cont.id = "menu-usuario";
    const inicial = (yo.usuario || "?").charAt(0).toUpperCase();
    cont.innerHTML = `
      <button type="button" class="mu-boton" aria-haspopup="true" aria-expanded="false" title="Sesión iniciada como ${esc(yo.usuario)}">
        <span class="mu-avatar">${esc(inicial)}</span><span class="mu-nombre">${esc(yo.usuario)}</span>
        <svg class="mu-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      <div class="mu-panel" role="menu" hidden>
        <div class="mu-cab">Sesión iniciada como<strong>${esc(yo.usuario)}</strong>${yo.es_admin ? '<span class="mu-rol">Administrador</span>' : ""}</div>
        <a role="menuitem" href="/cuenta">Mi cuenta y clave</a>
        ${yo.es_admin ? '<a role="menuitem" href="/cuenta#usuarios">Usuarios del panel</a>' : ""}
        <button type="button" role="menuitem" class="mu-salir">Cerrar sesión</button>
      </div>`;
    nav.after(cont);

    const boton = cont.querySelector(".mu-boton");
    const panel = cont.querySelector(".mu-panel");
    const abrir = (si) => { panel.hidden = !si; boton.setAttribute("aria-expanded", si ? "true" : "false"); };
    boton.addEventListener("click", (e) => { e.stopPropagation(); abrir(panel.hidden); });
    document.addEventListener("click", (e) => { if (!cont.contains(e.target)) abrir(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") abrir(false); });
    cont.querySelector(".mu-salir").addEventListener("click", cerrarSesion);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", montar);
  else montar();
})();
