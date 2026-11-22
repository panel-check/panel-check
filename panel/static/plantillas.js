/* Plantillas de mails (22/11/2026): formato del texto, datos a completar y el «armador» de mails que se
 * abre desde la ficha de un trabajo, la de vigilancia o Mails → «Plantillas para clientes». Se copia con formato (negritas, listas,
 * tabla) para pegar directo en Gmail. Expone window.Plantillas. Ver panel/plantillas_core.py. */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

  async function pedir(path, opciones = {}) {
    const o = { credentials: "include", ...opciones };
    if (o.body && typeof o.body !== "string") {
      o.body = JSON.stringify(o.body);
      o.headers = { "Content-Type": "application/json", ...(o.headers || {}) };
    }
    const r = await fetch(path, o);
    let j = null;
    try { j = await r.json(); } catch (_) { /* sin cuerpo */ }
    if (!r.ok) throw new Error((j && j.detail) || `Error ${r.status}`);
    return j;
  }

  // ── Datos a completar: [nombre], [marca], [fecha de publicación]… ───────────────────────────────
  const clave = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[º°.]/g, "").replace(/\s+/g, " ").trim();
  const RE_DATO = /\[([^\[\]\n]{1,40})\]/g;

  function datosDe(...textos) {
    const vistos = new Map();
    for (const t of textos) for (const m of String(t || "").matchAll(RE_DATO)) if (!vistos.has(clave(m[1]))) vistos.set(clave(m[1]), m[1]);
    return [...vistos].map(([k, etiqueta]) => ({ k, etiqueta }));
  }
  function completar(texto, valores) {
    return String(texto || "").replace(RE_DATO, (todo, nombre) => {
      const v = valores[clave(nombre)];
      return v ? String(v) : todo;
    });
  }

  // ── Formato: **negrita**, _cursiva_, listas «- », tablas «|», párrafos separados por renglón en blanco ──
  function enLinea(s) {
    return esc(s)
      .replace(/\*\*([^*]+?)\*\*/g, "<b>$1</b>")
      .replace(/(^|[\s(])_([^_\n]+?)_(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>")
      .replace(/(https?:\/\/[^\s<]+[^\s<.,;:)])/g, '<a href="$1">$1</a>')
      .replace(/\[([^\[\]\n]{1,40})\]/g, '<mark class="pl-falta">[$1]</mark>');
  }
  function bloques(texto) {
    return String(texto || "").replace(/\r/g, "").split(/\n\s*\n/).map((b) => b.replace(/^\n+|\n+$/g, "")).filter(Boolean);
  }
  function html(texto) {
    return bloques(texto).map((b) => {
      const lineas = b.split("\n");
      if (lineas.every((l) => /^\s*[-•]\s+/.test(l)))
        return `<ul>${lineas.map((l) => `<li>${enLinea(l.replace(/^\s*[-•]\s+/, ""))}</li>`).join("")}</ul>`;
      if (lineas.every((l) => /^\s*\|/.test(l))) {
        const filas = lineas.filter((l) => !/^\s*\|?\s*-{2,}/.test(l))
          .map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
        const td = "border:1px solid #ccc;padding:4px 10px;text-align:left";
        return `<table style="border-collapse:collapse">${filas.map((f, i) => `<tr>${f.map((c) => i === 0 ? `<th style="${td};background:#f3f4f6">${enLinea(c)}</th>` : `<td style="${td}">${enLinea(c)}</td>`).join("")}</tr>`).join("")}</table>`;
      }
      return `<p>${lineas.map(enLinea).join("<br>")}</p>`;
    }).join("");
  }
  function plano(texto) {
    return bloques(texto).map((b) => b.split("\n").map((l) => {
      if (/^\s*\|/.test(l)) return /^\s*\|?\s*-{2,}/.test(l) ? null : l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()).join("  |  ");
      return l.replace(/^\s*-\s+/, "• ");
    }).filter((l) => l !== null).join("\n")).join("\n\n").replace(/\*\*([^*]+?)\*\*/g, "$1").replace(/(^|[\s(])_([^_\n]+?)_/g, "$1$2");
  }

  async function copiar(textoHtml, textoPlano) {
    try {
      if (textoHtml && window.ClipboardItem) {
        await navigator.clipboard.write([new ClipboardItem({
          "text/html": new Blob([`<div style="font-family:Arial,sans-serif;font-size:14px">${textoHtml}</div>`], { type: "text/html" }),
          "text/plain": new Blob([textoPlano], { type: "text/plain" }),
        })]);
      } else {
        await navigator.clipboard.writeText(textoPlano);
      }
      return true;
    } catch (_) {
      try { await navigator.clipboard.writeText(textoPlano); return true; } catch (__) { return false; }
    }
  }

  // ── Valores conocidos a partir del trabajo / cliente ───────────────────────────────────────────
  const fechaCorta = (iso) => { if (!iso) return ""; const [a, m, d] = String(iso).slice(0, 10).split("-"); return a ? `${d}/${m}/${a}` : ""; };
  const sumarAnios = (iso, n) => { if (!iso) return ""; const [a, m, d] = String(iso).slice(0, 10).split("-"); return a ? `${d}/${m}/${Number(a) + n}` : ""; };
  const capital = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : "");

  /** contexto → {clave normalizada: valor}. Acepta {nombre, email, telefono, marca, clase, clases, acta, tipo,
   *  fecha_publicacion, fecha_concesion, fecha_vencimiento, oponente}. */
  function valoresDe(c = {}) {
    const v = {
      "nombre": c.nombre, "marca": c.marca, "nombre de la marca": c.marca, "clase": c.clase, "clases": c.clases || c.clase,
      "acta": c.acta, "tipo": c.tipo ? capital(c.tipo) : "", "oponente": c.oponente,
      "fecha de publicacion": fechaCorta(c.fecha_publicacion), "fecha de concesion": fechaCorta(c.fecha_concesion),
      "fecha de vencimiento": fechaCorta(c.fecha_vencimiento),
      "fecha 5 ano": sumarAnios(c.fecha_concesion, 5), "fecha 6 ano": sumarAnios(c.fecha_concesion, 6),
    };
    for (const k of Object.keys(v)) if (v[k] === undefined || v[k] === null) v[k] = "";
    return v;
  }

  // ── El armador (modal) ─────────────────────────────────────────────────────────────────────────
  let estado = null;   // {plantillas, servicios, canales, ctx, valores, elegida, editado}

  function caja() {
    let m = document.getElementById("pl-modal");
    if (!m) {
      m = document.createElement("div");
      m.id = "pl-modal"; m.className = "pl-fondo"; m.hidden = true;
      m.innerHTML = '<div class="pl-caja" role="dialog" aria-modal="true" aria-label="Armar mail"></div>';
      document.body.appendChild(m);
      m.addEventListener("click", (ev) => { if (ev.target === m) cerrar(); });
      m.addEventListener("click", alClic);
      m.addEventListener("input", alEscribir);
      document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !m.hidden) { ev.stopImmediatePropagation(); cerrar(); } }, true);
    }
    return m;
  }
  function cerrar() { const m = document.getElementById("pl-modal"); if (m) m.hidden = true; }

  function orden(ctx, lista) {
    const sid = ctx.servicio, et = Number(ctx.etapa ?? -1);
    const puntos = (p) => p.servicio === sid ? (p.para_etapa === et ? 0 : p.para_etapa === et + 1 ? 1 : 2) : (p.servicio === "general" ? 3 : 4);
    return [...lista].sort((a, b) => puntos(a) - puntos(b) || (a.orden - b.orden) || (a.id - b.id));
  }

  async function abrir(ctx = {}) {
    const m = caja();
    m.hidden = false;
    m.firstElementChild.innerHTML = '<p class="pl-cargando">Cargando plantillas…</p>';
    try {
      const d = await pedir("/api/plantillas");
      const lista = orden(ctx, d.plantillas);
      estado = { ...d, plantillas: lista, ctx, valores: valoresDe(ctx), elegida: ctx.plantilla_id || lista[0]?.id, editado: null, todas: false };
      pintar();
    } catch (e) {
      m.firstElementChild.innerHTML = `<p class="pl-cargando">No se pudieron cargar las plantillas: ${esc(e.message)}</p>`;
    }
  }

  function elegida() { return estado.plantillas.find((p) => p.id === estado.elegida); }

  function pintar() {
    const m = caja().firstElementChild;
    const { ctx } = estado;
    const p = elegida();
    const sid = ctx.servicio;
    const propias = estado.plantillas.filter((x) => !sid || x.servicio === sid || x.servicio === "general");
    const lista = estado.todas || !sid ? estado.plantillas : propias;
    const et = Number(ctx.etapa ?? -1);
    const item = (x) => `<button type="button" class="pl-item ${x.id === estado.elegida ? "activa" : ""}" data-pl-elegir="${x.id}">
        <span class="tit">${x.canal === "whatsapp" ? "💬" : "✉️"} ${esc(x.titulo)}</span>
        ${sid && x.servicio === sid && (x.para_etapa === et || x.para_etapa === et + 1) ? '<span class="pl-sug">Sugerida</span>' : ""}
        ${!sid || estado.todas ? `<small>${esc(estado.servicios[x.servicio] || x.servicio)}</small>` : ""}</button>`;
    const datos = p ? datosDe(p.asunto, p.cuerpo) : [];
    m.innerHTML = `
      <div class="pl-cab"><b>Armar ${p && p.canal === "whatsapp" ? "mensaje" : "mail"}</b>${ctx.titulo ? ` <span class="pl-ctx">· ${esc(ctx.titulo)}</span>` : ""}
        <button type="button" class="pl-x" data-pl-cerrar aria-label="Cerrar">×</button></div>
      <div class="pl-cuerpo">
        <aside class="pl-lista">${lista.map(item).join("") || '<p class="pl-vacio">No hay plantillas.</p>'}
          ${sid && propias.length < estado.plantillas.length ? `<button type="button" class="pl-mas" data-pl-todas>${estado.todas ? "Ver solo las de este servicio" : "Ver todas las plantillas"}</button>` : ""}
          <a class="pl-mas" href="/mails?tab=clientes" target="_blank" rel="noopener">Editar plantillas ↗</a></aside>
        <section class="pl-armado">${p ? `
          ${p.cuando ? `<p class="pl-cuando">${esc(p.cuando)}</p>` : ""}
          ${p.canal === "mail" ? `<div class="pl-campo"><span>Para</span><input data-pl-para value="${esc(ctx.email || "")}" placeholder="mail del cliente"><button type="button" class="pl-btn" data-pl-copiar="para">Copiar</button></div>
          <div class="pl-campo"><span>Asunto</span><input data-pl-asunto value="${esc(completar(p.asunto || "", estado.valores))}" placeholder="(sin asunto)"><button type="button" class="pl-btn" data-pl-copiar="asunto">Copiar</button></div>` : ""}
          ${datos.length ? `<details class="pl-datos" open><summary>Datos a completar (${datos.filter((x) => !estado.valores[x.k]).length} sin completar)</summary>
            <div class="pl-grilla">${datos.map((x) => `<label class="${estado.valores[x.k] ? "" : "falta"}"><span>${esc(x.etiqueta)}</span><input data-pl-dato="${esc(x.k)}" value="${esc(estado.valores[x.k] || "")}"></label>`).join("")}</div></details>` : ""}
          <div class="pl-vista" id="pl-vista">${html(cuerpoFinal())}</div>
          <div class="pl-acciones">
            <button type="button" class="pl-btn pl-ppal" data-pl-copiar="cuerpo">📋 Copiar ${p.canal === "whatsapp" ? "mensaje" : "texto del mail"}</button>
            ${p.canal === "mail" ? '<button type="button" class="pl-btn" data-pl-gmail title="Abre un mail nuevo en Gmail con el destinatario y el asunto, y deja el texto copiado para pegar (Cmd/Ctrl + V)">Abrir en Gmail ↗</button>'
              : (ctx.telefono ? '<button type="button" class="pl-btn" data-pl-wa>Abrir WhatsApp ↗</button>' : "")}
            <span class="pl-aviso" id="pl-aviso"></span>
          </div>` : '<p class="pl-vacio">Elegí una plantilla.</p>'}
        </section>
      </div>`;
  }

  function cuerpoFinal() { const p = elegida(); return p ? completar(p.cuerpo, estado.valores) : ""; }
  function aviso(t) { const a = document.getElementById("pl-aviso"); if (a) { a.textContent = t; clearTimeout(aviso._t); aviso._t = setTimeout(() => { a.textContent = ""; }, 2500); } }

  function alEscribir(ev) {
    const d = ev.target.closest("[data-pl-dato]");
    if (!d) return;
    estado.valores[d.dataset.plDato] = d.value.trim();
    d.closest("label").classList.toggle("falta", !d.value.trim());
    const p = elegida();
    const as = document.querySelector("[data-pl-asunto]");
    if (as) as.value = completar(p.asunto || "", estado.valores);
    document.getElementById("pl-vista").innerHTML = html(cuerpoFinal());
  }

  async function alClic(ev) {
    const b = ev.target.closest("[data-pl-elegir], [data-pl-cerrar], [data-pl-todas], [data-pl-copiar], [data-pl-gmail], [data-pl-wa]");
    if (!b) return;
    if (b.dataset.plCerrar !== undefined) return cerrar();
    if (b.dataset.plTodas !== undefined) { estado.todas = !estado.todas; return pintar(); }
    if (b.dataset.plElegir) { estado.elegida = Number(b.dataset.plElegir); return pintar(); }
    const p = elegida();
    const texto = cuerpoFinal();
    const faltan = datosDe(texto).length;
    if (b.dataset.plCopiar === "para") return aviso(await copiar(null, document.querySelector("[data-pl-para]").value) ? "Mail copiado" : "No se pudo copiar");
    if (b.dataset.plCopiar === "asunto") return aviso(await copiar(null, document.querySelector("[data-pl-asunto]").value) ? "Asunto copiado" : "No se pudo copiar");
    if (b.dataset.plCopiar === "cuerpo") {
      const ok = await copiar(p.canal === "mail" ? html(texto).replace(/<mark class="pl-falta">/g, "<mark>") : null, plano(texto));
      return aviso(ok ? (faltan ? `Copiado (quedan ${faltan} dato${faltan > 1 ? "s" : ""} sin completar)` : "Copiado: pegalo en Gmail con Cmd/Ctrl + V") : "No se pudo copiar");
    }
    if (b.dataset.plGmail !== undefined) {
      await copiar(html(texto), plano(texto));
      const para = document.querySelector("[data-pl-para]").value.trim();
      const asunto = document.querySelector("[data-pl-asunto]").value.trim();
      window.open(`https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(para)}&su=${encodeURIComponent(asunto)}`, "_blank", "noopener");
      return aviso("Texto copiado: pegalo en el mail con Cmd/Ctrl + V");
    }
    if (b.dataset.plWa !== undefined) {
      let tel = String(estado.ctx.telefono || "").replace(/\D/g, "").replace(/^0/, "");
      if (tel.length === 10) tel = "549" + tel;
      window.open(`https://wa.me/${tel}?text=${encodeURIComponent(plano(texto))}`, "_blank", "noopener");
    }
  }

  window.Plantillas = { abrir, html, plano, datosDe, completar, valoresDe, copiar, MESES };
})();
