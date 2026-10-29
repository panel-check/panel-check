"""Diagnóstico temporal (solo lectura) de entregabilidad en Resend. No imprime mails de personas:
solo dominios, configuración y cantidades por tipo de mail."""
import collections, os, re, requests

def get(key, path, **params):
    r = requests.get("https://api.resend.com" + path, headers={"Authorization": f"Bearer {key}"}, params=params, timeout=30)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])

def tipo(asunto):
    a = (asunto or "").lower()
    if "tu reunión" in a or "reunión" in a: return "INVITACION REUNION"
    if "presupuesto" in a: return "presupuesto"
    if "oposición" in a: return "oposicion"
    if "vista" in a: return "vista"
    if "consulta de marca" in a: return "confirmacion web"
    if "publicada" in a: return "marca publicada"
    if "poder" in a: return "poder"
    return "otro"

for cuenta, env in (("prospectos", "RESEND_API_KEY_PROSPECTOS"), ("interna", "RESEND_API_KEY")):
    key = os.environ.get(env)
    print(f"\n===== cuenta {cuenta} =====")
    if not key: print("sin clave"); continue
    st, dom = get(key, "/domains")
    for d in (dom.get("data") or []) if isinstance(dom, dict) else []:
        st2, det = get(key, f"/domains/{d['id']}")
        det = det if isinstance(det, dict) else {}
        print(f"dominio {d.get('name')}: status={d.get('status')} region={d.get('region')} "
              f"open_tracking={det.get('open_tracking')} click_tracking={det.get('click_tracking')} "
              f"capabilities={det.get('capabilities')}")
        for rec in det.get("records") or []:
            print(f"   registro {rec.get('record')} {rec.get('type')} {rec.get('name')} status={rec.get('status')}")
    if cuenta != "prospectos": continue
    c = collections.defaultdict(collections.Counter)
    despues, total = None, 0
    for _ in range(10):
        p = {"limit": 100}
        if despues: p["after"] = despues
        st, r = get(key, "/emails", **p)
        if st != 200: print("listar:", st, r); break
        data = r.get("data") or []
        for x in data:
            frm = re.sub(r".*<|>.*", "", x.get("from") or "")
            k = (tipo(x.get("subject")), frm.split("@")[-1] if "@" in frm else frm)
            c[k][x.get("last_event") or "?"] += 1; total += 1
        if not r.get("has_more") or not data: break
        despues = data[-1]["id"]
    print(f"\nmails listados: {total}")
    for k, v in sorted(c.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"{k[0]:22} desde @{k[1]:32} total={sum(v.values()):4}  " + ", ".join(f"{e}={n}" for e, n in v.most_common()))
