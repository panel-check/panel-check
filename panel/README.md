# Panel de leads

App chiquita (FastAPI + páginas HTML) para ver, filtrar y marcar como
"contactados" los leads que carga el pipeline en Postgres, y hacerles el
seguimiento comercial en el CRM (`/crm`: tablero por etapas, lista, agenda y
ficha de cada lead con historial de gestiones). Pensada para
correr como otro servicio dentro del mismo proyecto de Railway, al lado de
la base y de NocoDB.

## Variables de entorno

- `DATABASE_URL` — la misma Postgres del pipeline (usar la referencia al
  servicio de Postgres del proyecto, no hace falta copiar el valor a mano).
- `PANEL_RECUPERAR` — opcional y temporal (`usuario:clave`): crea o
  restablece ese usuario como administrador al arrancar. Sirve para el primer
  usuario o si nadie puede entrar. Borrarla después de usarla.
- `PANEL_USER` / `PANEL_PASSWORD` / `PANEL_USERS` — formato viejo (HTTP
  Basic). Solo se usan una vez, para crear los usuarios si la tabla
  `usuarios_panel` está vacía; después conviene borrarlas.

## Login

Usuarios con clave hasheada (scrypt) en la tabla `usuarios_panel` y sesiones
con cookie HttpOnly/Secure/SameSite=Lax (`sesiones_panel`, se guarda solo el
hash del token). Cerrar sesión, Mi cuenta (`/cuenta`: cambiar clave, sesiones
abiertas) y administración de usuarios desde el panel. Bloqueo por intentos
fallidos (`intentos_login`). Ver `auth.py` y `auth_api.py`.

## Correr local

```bash
cd panel
pip install -r requirements.txt
DATABASE_URL=postgresql://... PANEL_RECUPERAR=admin:unaClaveLarga uvicorn app:app --reload
```

Abrir http://localhost:8000 (lleva a la pantalla de ingreso).

## Deploy en Railway

Se deploya como un servicio nuevo dentro del proyecto de Railway ya
existente, apuntando este mismo repo de GitHub con **root directory =
`panel`**. Railway detecta el `Procfile` y el `requirements.txt`
automáticamente (Nixpacks). Variables a configurar en el servicio:
`DATABASE_URL` (referenciando el servicio de Postgres) y, para crear el
primer usuario, `PANEL_RECUPERAR`.
