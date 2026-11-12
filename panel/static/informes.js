/* Informes (/informes, menú Más): todos los informes —preliminares (PDF al cliente + IA interno) e
 * Informes Oposición IA e Informes Vista IA—, de clientes y de leads que todavía no son clientes, agrupados por marca, y
 * «Nuevo informe» para cargar uno buscando la marca o el titular entre los clientes y los leads.
 * Selección múltiple (12/11/2026): casillas por marca para archivar varias juntas; las archivadas salen de
 * la lista y se ven con «Archivados» (ahí se pueden desarchivar). Si a una marca archivada se le carga un
 * informe nuevo, vuelve sola a la lista.
 * La lista y el diálogo están en informes_comun.js. Depende de comun.js (api, apiJson). */

(function () {
  const $ = (id) => document.getElementById(id);
  let grupos = [], filtro = "", visibles = [];
  const elegidos = new Set();  // claves ixClave(g) de las marcas seleccionadas

  const activo = (g) => !g.archivado;
  const FILTROS = {
    "": activo,
    leads: (g) => activo(g) && !!g.lead_clave,
    interno: (g) => activo(g) && g.familia === "preliminar" && !g.interno.length,
    cliente: (g) => activo(g) && g.familia === "preliminar" && !g.cliente.length,
    oposiciones: (g) => activo(g) && g.familia === "oposicion",
    vistas: (g) => activo(g) && g.familia === "vista",
    archivados: (g) => !!g.archivado,
  };

  function pintarBarra() {
    const elegidas = visibles.filter((g) => elegidos.has(ixClave(g)));
    $("inf-barra").hidden = !elegidas.length;
    $("inf-barra-info").textContent = `${elegidas.length} marca${elegidas.length !== 1 ? "s" : ""} seleccionada${elegidas.length !== 1 ? "s" : ""}`;
    const enArchivo = filtro === "archivados";
    $("inf-archivar").hidden = enArchivo;
    $("inf-desarchivar").hidden = !enArchivo;
    const todas = $("inf-todas");
    if (todas) {
      todas.checked = visibles.length > 0 && elegidas.length === visibles.length;
      todas.indeterminate = elegidas.length > 0 && elegidas.length < visibles.length;
    }
  }

  function pintar() {
    const cont = $("inf-lista");
    document.querySelectorAll("#inf-falta [data-n]").forEach((s) => {
      const n = grupos.filter(FILTROS[s.dataset.n]).length;
      s.textContent = n ? String(n) : "";
    });
    visibles = grupos.filter(FILTROS[filtro]);
    // La selección queda solo con lo que se ve (al cambiar de filtro o buscar no se archiva algo oculto).
    const claves = new Set(visibles.map(ixClave));
    [...elegidos].forEach((k) => { if (!claves.has(k)) elegidos.delete(k); });
    if (!grupos.length && !$("inf-q").value.trim()) {
      cont.innerHTML = `<div class="ix-vacio"><strong>Todavía no hay informes</strong>Cargá el primero buscando la marca o el titular: puede ser un cliente o un lead de los boletines.
        <br><button type="button" class="ix-boton" id="inf-nuevo-2">Nuevo informe</button></div>`;
      $("inf-nuevo-2").addEventListener("click", nuevo);
      pintarBarra();
      return;
    }
    if (!visibles.length) {
      cont.innerHTML = filtro === "archivados"
        ? '<div class="ix-vacio"><strong>No hay informes archivados</strong>Para archivar, tildá las marcas en la lista y tocá «Archivar».</div>'
        : '<div class="ix-vacio"><strong>Nada con ese filtro</strong>Probá con otra búsqueda o mostrá todas.</div>';
      pintarBarra();
      return;
    }
    cont.innerHTML = `<label class="ix-todas"><input type="checkbox" id="inf-todas" /> Seleccionar todas las que se ven</label>`
      + ixListaHtml(visibles, { seleccion: true, elegidos })
      + `<p class="ix-pie">${visibles.length} marca${visibles.length !== 1 ? "s" : ""}</p>`;
    ixConectarLista(cont, visibles, cargar);
    cont.querySelectorAll("[data-ix-sel]").forEach((cb) => cb.addEventListener("change", () => {
      const g = visibles[+cb.dataset.ixSel];
      cb.checked ? elegidos.add(ixClave(g)) : elegidos.delete(ixClave(g));
      cb.closest(".ix-fila").classList.toggle("elegida", cb.checked);
      pintarBarra();
    }));
    $("inf-todas").addEventListener("change", (ev) => {
      visibles.forEach((g) => ev.target.checked ? elegidos.add(ixClave(g)) : elegidos.delete(ixClave(g)));
      pintar();
    });
    pintarBarra();
  }

  async function cargar() {
    try {
      const r = await api(`/api/informes?${new URLSearchParams({ q: $("inf-q").value.trim(), archivados: "todos" })}`);
      grupos = r.grupos || [];
      pintar();
    } catch (e) {
      $("inf-lista").innerHTML = `<p class="crm-error">No se pudo cargar (${ixEsc(e.message)}).</p>`;
    }
  }

  async function archivar(valor) {
    const elegidas = visibles.filter((g) => elegidos.has(ixClave(g)));
    if (!elegidas.length) return;
    const ids = elegidas.flatMap((g) => g.ids || []);
    const n = elegidas.length, txt = `${n} marca${n !== 1 ? "s" : ""}`;
    const botones = [$("inf-archivar"), $("inf-desarchivar")];
    botones.forEach((b) => { b.disabled = true; });
    try {
      await apiJson("/api/informes/archivar", "POST", { ids, archivar: valor });
      elegidos.clear();
      await cargar();
      if (typeof mostrarAviso === "function") mostrarAviso(valor ? `Se archivaron ${txt}. Se ven en «Archivados».` : `${txt} volvieron a la lista.`, "ok");
    } catch (e) {
      alert(`No se pudo ${valor ? "archivar" : "desarchivar"}: ${e.message}`);
    } finally {
      botones.forEach((b) => { b.disabled = false; });
    }
  }

  const nuevo = () => ixNuevoInforme({ modo: filtro === "oposiciones" ? "oposicion" : filtro === "vistas" ? "vista" : "", alTerminar: cargar });

  $("inf-nuevo").addEventListener("click", nuevo);
  $("inf-archivar").addEventListener("click", () => archivar(true));
  $("inf-desarchivar").addEventListener("click", () => archivar(false));
  $("inf-quitar").addEventListener("click", () => { elegidos.clear(); pintar(); });
  $("inf-falta").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-f]");
    if (!b) return;
    filtro = b.dataset.f;
    document.querySelectorAll("#inf-falta [data-f]").forEach((x) => x.classList.toggle("activo", x === b));
    pintar();
  });
  let t = null;
  $("inf-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(cargar, 250); });
  window.alRecargar = cargar;
  cargar();
  ixAbrirLinkCarga({ alTerminar: cargar });  // link «abrir en el panel» de las skills (/informes#cargar=…)
})();
