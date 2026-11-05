/* Informes (/informes, menú Más): todos los informes —preliminares (PDF al cliente + IA interno) e
 * Informes Oposición IA—, de clientes y de leads que todavía no son clientes, agrupados por marca, y
 * «Nuevo informe» para cargar uno buscando la marca o el titular entre los clientes y los leads.
 * La lista y el diálogo están en informes_comun.js. Depende de comun.js (api). */

(function () {
  const $ = (id) => document.getElementById(id);
  let grupos = [], filtro = "";

  const FILTROS = {
    "": () => true,
    leads: (g) => !!g.lead_clave,
    interno: (g) => g.familia !== "oposicion" && !g.interno.length,
    cliente: (g) => g.familia !== "oposicion" && !g.cliente.length,
    oposiciones: (g) => g.familia === "oposicion",
  };

  function pintar() {
    const cont = $("inf-lista");
    document.querySelectorAll("#inf-falta [data-n]").forEach((s) => {
      const n = grupos.filter(FILTROS[s.dataset.n]).length;
      s.textContent = n ? String(n) : "";
    });
    const visibles = grupos.filter(FILTROS[filtro]);
    if (!grupos.length && !$("inf-q").value.trim()) {
      cont.innerHTML = `<div class="ix-vacio"><strong>Todavía no hay informes</strong>Cargá el primero buscando la marca o el titular: puede ser un cliente o un lead de los boletines.
        <br><button type="button" class="ix-boton" id="inf-nuevo-2">Nuevo informe</button></div>`;
      $("inf-nuevo-2").addEventListener("click", nuevo);
      return;
    }
    if (!visibles.length) {
      cont.innerHTML = '<div class="ix-vacio"><strong>Nada con ese filtro</strong>Probá con otra búsqueda o mostrá todas.</div>';
      return;
    }
    cont.innerHTML = ixListaHtml(visibles) + `<p class="ix-pie">${visibles.length} marca${visibles.length !== 1 ? "s" : ""}</p>`;
    ixConectarLista(cont, visibles, cargar);
  }

  async function cargar() {
    try {
      const r = await api(`/api/informes?${new URLSearchParams({ q: $("inf-q").value.trim() })}`);
      grupos = r.grupos || [];
      pintar();
    } catch (e) {
      $("inf-lista").innerHTML = `<p class="crm-error">No se pudo cargar (${ixEsc(e.message)}).</p>`;
    }
  }

  const nuevo = () => ixNuevoInforme({ modo: filtro === "oposiciones" ? "oposicion" : "", alTerminar: cargar });

  $("inf-nuevo").addEventListener("click", nuevo);
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
