"""Temporal: prende SOLO el seguimiento de aperturas (no el de clics) en smartiesconsultora.com.ar
(cuenta Prospectos de Resend) y muestra cómo queda."""
import os, requests
key = os.environ["RESEND_API_KEY_PROSPECTOS"]
H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
doms = requests.get("https://api.resend.com/domains", headers=H, timeout=30).json()["data"]
d = next(x for x in doms if x["name"] == "smartiesconsultora.com.ar")
r = requests.patch(f"https://api.resend.com/domains/{d['id']}", headers=H, json={"tracking_subdomain": "links", "open_tracking": True, "click_tracking": False}, timeout=30)
print("PATCH", r.status_code, r.text[:300])
for x in doms:
    det = requests.get(f"https://api.resend.com/domains/{x['id']}", headers=H, timeout=30).json()
    print(f"{x['name']}: open_tracking={det.get('open_tracking')} click_tracking={det.get('click_tracking')}")

det = requests.get(f"https://api.resend.com/domains/{d['id']}", headers=H, timeout=30).json()
for rec in det.get("records") or []:
    print("registro:", rec.get("record"), rec.get("type"), rec.get("name"), "->", rec.get("value"), "status=", rec.get("status"))
print("tracking_subdomain:", det.get("tracking_subdomain"))
