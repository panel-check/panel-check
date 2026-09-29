"""
Chequea que Railway haya desplegado realmente el último commit de `main`.

Por qué existe: el 2026-09-29 hubo un incidente de la plataforma de Railway
("API degradation causing slow or stuck deployments") que dejó dos deploys
pegados en QUEUED/INITIALIZING durante horas sin que nada lo avisara — nos
enteramos recién porque el usuario notó a mano, en una captura, que
faltaban columnas nuevas en el panel. La única forma de darse cuenta antes
es comparar el commit que el panel tiene REALMENTE corriendo (expuesto en
GET /api/version, sin login) contra el último commit de main.

Si están desincronizados hace poco (menos de UMBRAL_MINUTOS) es normal:
Railway tarda unos minutos en buildear y desplegar. Si siguen
desincronizados después de ese umbral, algo se atascó — el script imprime
::error:: (mismo mecanismo que los otros scripts de este repo) para que la
tarjeta "Verificación del despliegue" en /crons se ponga en rojo sola.

Variables de entorno:
    PANEL_URL       - base URL pública del panel (sin barra final)
    GITHUB_TOKEN    - para leer el último commit de main (alcanza con el
                       GITHUB_TOKEN automático de Actions, con permiso
                       "contents: read")
    GITHUB_REPOSITORY - lo pone GitHub Actions solo ("owner/repo")

Uso:
    PANEL_URL=... GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo \
        python3 verificar_despliegue.py
"""

import os
import sys
from datetime import datetime, timezone

import requests

UMBRAL_MINUTOS = 20


def main():
    panel_url = os.environ.get("PANEL_URL")
    if not panel_url:
        sys.exit("Falta PANEL_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        sys.exit("Falta GITHUB_REPOSITORY o GITHUB_TOKEN")

    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

    r_commit = requests.get(
        f"https://api.github.com/repos/{repo}/commits/main", headers=headers, timeout=20
    )
    r_commit.raise_for_status()
    commit_data = r_commit.json()
    sha_main = commit_data["sha"][:7]
    fecha_commit = datetime.fromisoformat(
        commit_data["commit"]["committer"]["date"].replace("Z", "+00:00")
    )

    r_version = requests.get(f"{panel_url}/api/version", timeout=20)
    r_version.raise_for_status()
    sha_panel = r_version.json().get("commit")

    if sha_panel == sha_main:
        print(f"::notice::El panel ya tiene desplegado el último commit de main ({sha_main}).")
        return

    edad_min = (datetime.now(timezone.utc) - fecha_commit).total_seconds() / 60

    if edad_min < UMBRAL_MINUTOS:
        print(
            f"::notice::main avanzó a {sha_main} hace {edad_min:.0f} min "
            f"(el panel todavía tiene {sha_panel or 'desconocido'}) — normal, "
            f"Railway está buildeando/desplegando, todavía no pasó el umbral de "
            f"{UMBRAL_MINUTOS} min."
        )
        return

    mensaje = (
        f"El panel sigue corriendo el commit {sha_panel or 'desconocido'}, pero main "
        f"ya tiene {sha_main} desde hace {edad_min:.0f} min. El deploy en Railway "
        f"parece atascado (revisar https://status.railway.com y el proyecto en Railway)."
    )
    print(f"::error::{mensaje}")
    sys.exit(1)


if __name__ == "__main__":
    main()
