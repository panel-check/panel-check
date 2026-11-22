/* Procesos y plantillas (/procesos): lista por servicio, editor con vista previa y armado de mails.
 * Formato y armado en plantillas.js; API en plantillas_api.py. */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pr = { datos: null, abierta: null, q: "" };

  async function pedir(path, opciones = {}) {
    const o = { credentials: "include", ...opciones };
    if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers = { "Content-Type": "application/json" }; }
    const r = await fetch(path, o);
    if (r.status === 401 && typeof irAlLogin === "function") return irAlLogin();
    let j = null; try { j = await r.json(); } catch (_) { /* */ }
    if (!r.ok) throw new Error((j && j.detail) || `Error ${r.status}`);
    return j;
  }

  async function cargar() {
    pr.datos = await pedir("/api/plantillas");
    pintar();
  }

  function etapaTexto(p) {
    const lista = pr.datos.etapas[p.servicio];
    return lista && p.para_etapa != null ? lista[p.para_etapa] : "";
  }

  function pintar() {
    const q = pr.q.toLowerCase();
    const visibles = pr.datos.plantillas.filter((p) => !q || [p.titulo, p.asunto, p.cuando, p.cuerpo].join(" ").toLowerCase().includes(q));
    const grupos = Object.keys(pr.datos.servicios).map((sid) => [sid, visibles.filter((p) => p.servicio === sid)]).filter(([, l]) => l.length);
    $("pr-lista").innerHTML = grupos.map(([sid, lista]) => `<section class="pr-col"><h2>${esc(pr.datos.servicios[sid])}<span>${lista.length}</span></h2>
      ${lista.map((p) => `<button type="button" class="pr-card" data-id="${p.id}"><b>${p.canal === "whatsapp" ? "💬" : "✉️"} ${esc(p.titulo)}</b>
        ${p.cuando ? `<small>${esc(p.cuando)}</small>` : ""}
        ${etapaTexto(p) ? `<span class="pr-tag">${esc(etapaTexto(p))}</span>` : ""}${p.canal === "whatsapp" ? '<span class="pr-tag wa">WhatsApp</span>' : ""}</button>`).join("")}</section>`).join("")
      || `<p class="pl-vacio">${q ? "No hay plantillas con esa búsqueda." : "Todavía no hay plantillas. Creá la primera con «＋ Nueva plantilla»."}</p>`;
  }

  function opciones(sel, mapa, valor) {
    sel.innerHTML = Object.entries(mapa).map(([k, v]) => `<option value="${esc(k)}" ${String(k) === String(valor) ? "selected" : ""}>${esc(v)}</option>`).join("");
  }
  function opcionesEtapa(sid, valor) {
    const lista = pr.datos.etapas[sid] || [];
    $("pr-etapa").innerHTML = '<option value="">—</option>' + lista.map((e, i) => `<option value="${i}" ${String(i) === String(valor ?? "") ? "selected" : ""}>${esc(e)}</option>`).join("");
    $("pr-etapa").disabled = !lista.length;
  }

  function abrirEditor(p) {
    pr.abierta = p ? p.id : null;
    $("pr-modal-tit").textContent = p ? "Editar plantilla" : "Nueva plantilla";
    $("pr-titulo").value = p?.titulo || "";
    opciones($("pr-servicio"), pr.datos.servicios, p?.servicio || "registro");
    opcionesEtapa(p?.servicio || "registro", p?.para_etapa);
    opciones($("pr-canal"), pr.datos.canales, p?.canal || "mail");
    $("pr-asunto").value = p?.asunto || "";
    $("pr-cuando").value = p?.cuando || "";
    $("pr-cuerpo").value = p?.cuerpo || "";
    $("pr-borrar").hidden = !p;
    $("pr-usar").hidden = !p;
    vista();
    $("pr-modal").hidden = false;
    $("pr-titulo").focus();
  }
  function cerrarEditor() { $("pr-modal").hidden = true; }
  function vista() {
    const as = $("pr-asunto").value.trim();
    $("pr-vista").innerHTML = (as ? `<p><b>Asunto:</b> ${esc(as)}</p><hr style="border:0;border-top:1px solid #eee;margin:0 0 12px">` : "") + Plantillas.html($("pr-cuerpo").value);
  }
  function aviso(t) { $("pr-aviso").textContent = t; setTimeout(() => { $("pr-aviso").textContent = ""; }, 2500); }

  $("pr-lista").addEventListener("click", (ev) => {
    const c = ev.target.closest("[data-id]");
    if (c) abrirEditor(pr.datos.plantillas.find((p) => p.id === Number(c.dataset.id)));
  });
  $("pr-nueva").addEventListener("click", () => abrirEditor(null));
  $("pr-armar").addEventListener("click", () => Plantillas.abrir({ titulo: "sin trabajo: completá los datos a mano" }));
  $("pr-cerrar").addEventListener("click", cerrarEditor);
  $("pr-modal").addEventListener("click", (ev) => { if (ev.target === $("pr-modal")) cerrarEditor(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("pr-modal").hidden) cerrarEditor(); });
  $("pr-servicio").addEventListener("change", () => opcionesEtapa($("pr-servicio").value, ""));
  $("pr-cuerpo").addEventListener("input", vista);
  $("pr-asunto").addEventListener("input", vista);
  $("pr-buscar").addEventListener("input", (ev) => { pr.q = ev.target.value.trim(); pintar(); });

  $("pr-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const et = $("pr-etapa").value;
    const body = {
      titulo: $("pr-titulo").value, servicio: $("pr-servicio").value, canal: $("pr-canal").value,
      para_etapa: et === "" ? null : Number(et), asunto: $("pr-asunto").value, cuando: $("pr-cuando").value, cuerpo: $("pr-cuerpo").value,
    };
    try {
      if (pr.abierta) await pedir(`/api/plantillas/${pr.abierta}`, { method: "PUT", body });
      else {
        const lista = pr.datos.plantillas.filter((p) => p.servicio === body.servicio);
        body.orden = lista.length ? Math.max(...lista.map((p) => p.orden)) + 1 : 1;
        pr.abierta = (await pedir("/api/plantillas", { method: "POST", body })).id;
        $("pr-borrar").hidden = false; $("pr-usar").hidden = false; $("pr-modal-tit").textContent = "Editar plantilla";
      }
      await cargar();
      aviso("✓ Guardada");
    } catch (e) { aviso(`No se guardó: ${e.message}`); }
  });
  $("pr-borrar").addEventListener("click", async () => {
    if (!pr.abierta || !confirm("¿Borrar esta plantilla? No se puede deshacer.")) return;
    try { await pedir(`/api/plantillas/${pr.abierta}`, { method: "DELETE" }); cerrarEditor(); await cargar(); }
    catch (e) { aviso(`No se borró: ${e.message}`); }
  });
  $("pr-usar").addEventListener("click", () => {
    const p = pr.datos.plantillas.find((x) => x.id === pr.abierta);
    cerrarEditor();
    Plantillas.abrir({ titulo: "sin trabajo: completá los datos a mano", plantilla_id: p?.id, servicio: p?.servicio });
  });

  cargar().catch((e) => { $("pr-lista").innerHTML = `<p class="pl-vacio">No se pudieron cargar las plantillas: ${esc(e.message)}</p>`; });
})();
