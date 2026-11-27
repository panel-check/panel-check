// Generadores de escritos para firmar (poder, poder simple marcas, toma de intervención, desiste).
// Compartidos entre la ficha del cliente (Clientes → Poderes) y la ficha del trabajo en Seguimientos (27/11/2026).
// Usan los mismos endpoints de siempre (/api/clientes/{id}/poder, /toma-intervencion, /desiste).
// Uso: Escritos.poder(caja, clienteId, opciones) — caja es el <div> donde se dibuja el formulario.
// opciones: { actas: [...] }  → en toma y desiste vienen tildadas esas actas (las del trabajo)
//           { alListo: fn }    → se llama después de generar (por ejemplo, para recargar la lista de poderes)
(function () {
  const e = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TIPOS_PERSONA = { fisica: "Persona física", juridica: "Persona jurídica" };

  async function pedirJson(url) {
    const r = await fetch(url, { credentials: "include" });
    if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
    if (!r.ok) { let d = `${r.status}`; try { d = (await r.json()).detail || d; } catch (_) {} throw new Error(d); }
    return r.json();
  }

  async function bajar(url, opciones, nombreDefecto) {
    const r = await fetch(url, { credentials: "include", ...opciones });
    if (r.status === 401 && typeof irAlLogin === "function") irAlLogin();
    if (!r.ok) { let d = `${r.status}`; try { d = (await r.json()).detail || d; } catch (_) {} throw new Error(d); }
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    const nombre = m ? decodeURIComponent(m[1]) : nombreDefecto;
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = nombre;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  const cargando = (caja) => { caja.innerHTML = '<p class="crm-cargando esc-cargando">Preparando…</p>'; };
  const error = (caja, msg) => { caja.innerHTML = `<p class="crm-error">No se pudo preparar (${e(msg)}).</p>`; };
  const estado = (caja) => caja.querySelector("[data-esc-estado]");
  function avisar(caja, texto, mal = false) { const s = estado(caja); s.textContent = texto; s.className = "cl-estado" + (mal ? " mal" : ""); }

  // ── Poder (modelo del estudio) y Poder simple marcas ─────────────────────
  async function poder(caja, clienteId, op = {}) {
    const simple = op.modelo === "simple_marcas";
    cargando(caja);
    let r;
    try { r = await pedirJson(`/api/clientes/${clienteId}/poder${simple ? "?modelo=simple_marcas" : ""}`); }
    catch (err) { return error(caja, err.message); }
    const p = r.propuesta;
    const tipos = op.tiposPersona || TIPOS_PERSONA;
    const faltan = [];
    if (!p.otorgante.match(simple ? /domicilio/ : /domicili/)) faltan.push("domicilio");
    if ((simple || p.tipo_persona === "fisica") && !p.otorgante.match(/DNI/)) faltan.push("DNI");
    if (!simple && p.tipo_persona === "juridica" && /^_+ en representación/.test(p.otorgante)) faltan.push("quién firma por la empresa");
    const varios = p.firmantes > 1
      ? `<p class="crm-nota" style="margin:0 0 8px">La marca tiene <strong>${p.firmantes} titulares</strong> (según el formulario): el poder los nombra a todos${simple ? ", en plural («Los abajo firmantes… otorgan…»)," : ""} y lleva un lugar de firma, aclaración y DNI para cada uno.</p>` : "";
    const avisoFaltan = faltan.length ? `<p class="crm-error" style="padding:0 0 8px">Ojo: falta ${faltan.join(", ")} en la ficha del cliente. Podés completarlo acá abajo o en «Datos para el trámite».</p>` : "";
    const fecha = `<label>Lugar<input name="lugar" value="${e(p.lugar)}"></label><label>Fecha<input type="date" name="fecha" value="${e(p.fecha)}"></label>`;
    const firmantes = `<label title="Cuántos titulares firman (cada uno tiene su lugar de firma)">Titulares que firman<input type="number" name="firmantes" min="1" max="6" value="${e(p.firmantes || 1)}"></label>`;
    caja.innerHTML = simple ? `
      <h4 style="margin-top:0">Poder simple marcas</h4>
      <p class="crm-gris" style="margin:0 0 8px">Poder acotado a marcas: trámites ante el INPI y negociaciones y acuerdos (oposiciones, coexistencia). Es para <strong>persona humana</strong> (con DNI). Firma, aclaración y DNI salen como líneas en blanco para completar a mano. Sale siempre en una sola hoja.</p>
      ${p.cliente_juridica ? '<p class="crm-error" style="padding:0 0 8px">Ojo: este cliente figura como persona jurídica y este modelo es para persona humana. Usá el poder completo, que en persona jurídica arma el modelo de empresa («El abajo firmante … en representación de …»).</p>' : ""}
      ${avisoFaltan}${varios}
      <form class="cl-form" data-esc-form style="margin-top:0">
        <label>Firmante<select name="genero">
          <option value="neutro" ${p.genero === "neutro" ? "selected" : ""}>El/La (como el modelo) · Los/Las si son varios</option>
          <option value="masculino" ${p.genero === "masculino" ? "selected" : ""}>El (representarlo, del mandante) · Los si son varios</option>
          <option value="femenino" ${p.genero === "femenino" ? "selected" : ""}>La (representarla, de la mandante) · Las si son varios</option></select></label>
        ${firmantes}${fecha}
        <label class="ancho">El/La abajo firmante… (nombre, DNI y domicilio de cada titular; si son varios, separados por «;»)<textarea name="otorgante" rows="${p.firmantes > 1 ? 6 : 3}">${e(p.otorgante)}</textarea></label>
      </form>
      <p class="crm-nota">Apoderado (fijo en este modelo): ${e(r.apoderado)}</p>` : `
      <h4 style="margin-top:0">Poder para firmar</h4>
      <p class="crm-gris" style="margin:0 0 8px">Se arma con el modelo del estudio y los datos del cliente. Revisá el texto (por ejemplo «domiciliado» / «domiciliada») y descargalo para mandárselo a firmar. La aclaración (y el cargo, si es empresa) salen en blanco: los completa a mano quien firma.</p>
      ${varios}${avisoFaltan}
      <form class="cl-form" data-esc-form style="margin-top:0">
        <label>Tipo<select name="tipo_persona">${Object.entries(tipos).map(([k, v]) => `<option value="${k}" ${k === p.tipo_persona ? "selected" : ""}>${e(v)}</option>`).join("")}</select></label>
        ${fecha}${firmantes}
        <label>Encabezado<select name="genero">
          <option value="masculino" ${p.genero === "masculino" ? "selected" : ""}>El abajo firmante / Los abajo firmantes</option>
          <option value="femenino" ${p.genero === "femenino" ? "selected" : ""}>La abajo firmante / Las abajo firmantes</option>
          <option value="neutro" ${p.genero === "neutro" ? "selected" : ""}>El/La abajo firmante</option></select></label>
        <label class="ancho">${p.tipo_persona === "juridica" ? "Otorgante: quien firma «en representación de» la empresa, y el domicilio de la empresa" : "Otorgante(s): nombre, DNI y domicilio de cada titular (si son varios, separados por «;»)"}<textarea name="otorgante" rows="${p.firmantes > 1 ? 6 : 3}">${e(p.otorgante)}</textarea></label>
      </form>
      <p class="crm-nota">Apoderado: ${e(r.apoderado)} <a href="/clientes?vista=ajustes" title="Se cambia en Ajustes">(cambiar)</a></p>`;
    caja.insertAdjacentHTML("beforeend", `<div class="cl-acciones"><button type="button" class="cl-btn" data-generar="pdf">Descargar PDF</button>
      <button type="button" class="cl-btn sec" data-generar="docx">Descargar Word</button><span class="cl-estado" data-esc-estado></span></div>`);
    const form = caja.querySelector("[data-esc-form]");
    caja.querySelectorAll("[data-generar]").forEach((b) => b.addEventListener("click", async () => {
      const f = form.elements;
      const datos = simple
        ? { modelo: "simple_marcas", tipo_persona: "fisica", genero: f.genero.value, otorgante: f.otorgante.value, lugar: f.lugar.value, fecha: f.fecha.value, firmantes: String(f.firmantes.value || 1) }
        : { tipo_persona: f.tipo_persona.value, otorgante: f.otorgante.value, lugar: f.lugar.value, fecha: f.fecha.value, firmantes: String(f.firmantes.value || 1), genero: f.genero.value };
      avisar(caja, "Generando…"); b.disabled = true;
      try {
        await bajar(`/api/clientes/${clienteId}/poder?formato=${b.dataset.generar}`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos) },
          `${simple ? "Poder_simple_marcas" : "Poder"}.${b.dataset.generar}`);
        avisar(caja, "Listo: se descargó y quedó en los poderes generados del cliente.");
        if (op.alListo) op.alListo();
      } catch (err) { avisar(caja, err.message, true); }
      b.disabled = false;
    }));
  }

  // ── Toma de intervención y Desiste: un documento por acta ────────────────
  function tildada(m, op, porDefecto) {
    if (op.actas && op.actas.length) return op.actas.map(String).includes(String(m.acta));
    return porDefecto;
  }

  async function toma(caja, clienteId, op = {}) {
    cargando(caja);
    let r;
    try { r = await pedirJson(`/api/clientes/${clienteId}/toma-intervencion`); }
    catch (err) { return error(caja, err.message); }
    if (!r.marcas.length) { caja.innerHTML = '<p class="crm-gris">Este cliente no tiene marcas cargadas en Clientes → Marcas.</p>'; return; }
    const hayOp = r.marcas.some((m) => m.con_oposicion);
    const texto = op.actas && op.actas.length ? "Vienen tildadas las actas de este trabajo."
      : hayOp ? "Vienen tildadas las marcas con oposición abierta." : "Ninguna marca tiene oposición abierta: tildá las que necesites.";
    caja.innerHTML = `
      <h4 style="margin-top:0">Toma de intervención</h4>
      <p class="crm-gris" style="margin:0 0 8px">Se genera <strong>un documento por marca</strong> con el modelo del estudio. ${texto} Podés corregir los datos antes de bajarlos; si elegís varias, se baja un ZIP.</p>
      <div style="overflow-x:auto"><table class="cl-tabla"><thead><tr><th></th><th>Acta</th><th>Marca</th><th>Clase</th><th>Tipo</th><th>Solicitante</th><th>Fecha</th></tr></thead><tbody>
      ${r.marcas.map((m, i) => `<tr data-i="${i}">
        <td><input type="checkbox" data-sel ${tildada(m, op, m.con_oposicion) ? "checked" : ""}></td>
        <td><input data-k="acta" value="${e(m.acta)}" size="9"></td>
        <td><input data-k="marca" value="${e(m.marca)}">${m.con_oposicion ? '<br><span class="cl-tag aviso">⚖ oposición / vista</span>' : ""}</td>
        <td><input data-k="clase" value="${e(m.clase)}" size="3"></td>
        <td><input data-k="tipo" value="${e(m.tipo)}" size="12"></td>
        <td><input data-k="solicitante" value="${e(m.solicitante)}"></td>
        <td><input type="date" data-k="fecha" value="${e(m.fecha)}"></td></tr>`).join("")}</tbody></table></div>
      <div class="cl-acciones"><button type="button" class="cl-btn" data-generar="docx">Descargar Word</button>
        <button type="button" class="cl-btn sec" data-generar="pdf">Descargar PDF</button><span class="cl-estado" data-esc-estado></span></div>`;
    conectarTabla(caja, `/api/clientes/${clienteId}/toma-intervencion`, "Toma_de_intervencion", "documentos", op);
  }

  async function desiste(caja, clienteId, op = {}) {
    cargando(caja);
    let r;
    try { r = await pedirJson(`/api/clientes/${clienteId}/desiste`); }
    catch (err) { return error(caja, err.message); }
    if (!r.marcas.length) { caja.innerHTML = '<p class="crm-gris">Este cliente no tiene marcas cargadas en Clientes → Marcas.</p>'; return; }
    const una = r.marcas.length === 1;
    const opcGenero = (g) => ["domiciliada", "domiciliado", "domiciliado/a"].map((x) => `<option ${x === g ? "selected" : ""}>${x}</option>`).join("");
    caja.innerHTML = `
      <h4 style="margin-top:0">Desiste</h4>
      <p class="crm-gris" style="margin:0 0 8px">Escrito para <strong>desistir de la solicitud de registro</strong> de la marca ante el INPI, firmado por ${e(r.apoderada)} (Matrícula INPI N.º ${e(r.matricula)}) como apoderada del titular, «conforme al poder que obra en el expediente». Se genera <strong>uno por marca</strong>: ${op.actas && op.actas.length ? "vienen tildadas las actas de este trabajo." : una ? "la marca del cliente ya viene tildada." : "tildá la o las marcas a desistir."} Podés corregir los datos antes de bajarlo; si elegís varias, se baja un ZIP.</p>
      <div style="overflow-x:auto"><table class="cl-tabla"><thead><tr><th></th><th>Acta</th><th>Marca</th><th>Clase</th><th>Tipo</th><th>Titular</th><th>CUIT / DNI</th><th>Domicilio</th></tr></thead><tbody>
      ${r.marcas.map((m, i) => `<tr data-i="${i}">
        <td><input type="checkbox" data-sel ${tildada(m, op, una) ? "checked" : ""}></td>
        <td><input data-k="acta" value="${e(m.acta)}" size="9">${m.estado ? `<br><span class="crm-gris">${e(m.estado)}</span>` : ""}</td>
        <td><input data-k="marca" value="${e(m.marca)}"></td>
        <td><input data-k="clase" value="${e(m.clase)}" size="3"></td>
        <td><input data-k="tipo" value="${e(m.tipo)}" size="12"></td>
        <td><input data-k="titular" value="${e(m.titular)}"></td>
        <td><input data-k="identificacion" value="${e(m.identificacion)}" size="18" placeholder="CUIT 30-…"></td>
        <td><select data-k="genero">${opcGenero(m.genero)}</select> en<br><input data-k="domicilio" value="${e(m.domicilio)}" size="34"><input type="hidden" data-k="fecha" value="${e(m.fecha)}"></td></tr>`).join("")}</tbody></table></div>
      <div class="cl-acciones"><button type="button" class="cl-btn" data-generar="pdf">Descargar PDF</button>
        <button type="button" class="cl-btn sec" data-generar="docx">Descargar Word</button><span class="cl-estado" data-esc-estado></span></div>`;
    conectarTabla(caja, `/api/clientes/${clienteId}/desiste`, "Desiste", "escritos", op,
      (items) => items.some((o) => !o.acta.trim() || !o.marca.trim() || !o.titular.trim()) ? "Falta el acta, la marca o el titular." : "");
  }

  function conectarTabla(caja, url, nombre, plural, op, validar) {
    caja.querySelectorAll("[data-generar]").forEach((b) => b.addEventListener("click", async () => {
      const items = [...caja.querySelectorAll("tr[data-i]")].filter((tr) => tr.querySelector("[data-sel]").checked).map((tr) => {
        const o = {}; tr.querySelectorAll("[data-k]").forEach((i) => { o[i.dataset.k] = i.value; }); return o; });
      if (!items.length) return avisar(caja, "Tildá al menos una marca.", true);
      const falta = validar && validar(items);
      if (falta) return avisar(caja, falta, true);
      avisar(caja, "Generando…"); b.disabled = true;
      try {
        await bajar(`${url}?formato=${b.dataset.generar}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) }, nombre);
        avisar(caja, items.length > 1 ? `Listo: se descargó un ZIP con ${items.length} ${plural}.` : "Listo: se descargó.");
        if (op.alListo) op.alListo();
      } catch (err) { avisar(caja, err.message, true); }
      b.disabled = false;
    }));
  }

  window.Escritos = {
    poder: (caja, id, op = {}) => poder(caja, id, op),
    poderSimple: (caja, id, op = {}) => poder(caja, id, { ...op, modelo: "simple_marcas" }),
    toma, desiste, bajar,
  };
})();
