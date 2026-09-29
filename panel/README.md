# Panel de leads

App chiquita (FastAPI + una página HTML) para ver, filtrar y marcar como
"contactados" los leads que carga el pipeline en Postgres. Pensada para
correr como otro servicio dentro del mismo proyecto de Railway, al lado de
la base y de NocoDB.

## Variables de entorno

- `DATABASE_URL` — la misma Postgres del pipeline (usar la referencia al
  servicio de Postgres del proyecto, no hace falta copiar el valor a mano).
- `PANEL_USER` — usuario para el login (HTTP Basic).
- `PANEL_PASSWORD` — clave para el login.

## Correr local

```bash
cd panel
pip install -r requirements.txt
DATABASE_URL=postgresql://... PANEL_USER=admin PANEL_PASSWORD=cambiala uvicorn app:app --reload
```

Abrir http://localhost:8000 (pide usuario/clave).

## Deploy en Railway

Se deploya como un servicio nuevo dentro del proyecto de Railway ya
existente, apuntando este mismo repo de GitHub con **root directory =
`panel`**. Railway detecta el `Procfile` y el `requirements.txt`
automáticamente (Nixpacks). Variables a configurar en el servicio:
`DATABASE_URL` (referenciando el servicio de Postgres), `PANEL_USER`,
`PANEL_PASSWORD`.
