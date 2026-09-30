"""
Registro de corridas: cada proceso automático anota, al terminar, qué hizo
(cuántas revisó, cuántos leads agregó, cuántos emails consiguió, etc.) en la
tabla `registro_corridas`. De ahí sale el reporte por día del "+" de cada
tarjeta en Automatizaciones (/crons).

Nunca rompe el proceso que lo llama: si la base falla, lo avisa y sigue.
Las claves de `metricas` tienen que coincidir con REPORTES_AUTOMATIZACIONES
en panel/app.py (que les pone la etiqueta legible).
"""

import json
import os
import sys

import psycopg2

CREAR_TABLA = """
CREATE TABLE IF NOT EXISTS registro_corridas (
    id          BIGSERIAL PRIMARY KEY,
    proceso     TEXT NOT NULL,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metricas    JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_id      TEXT,
    run_url     TEXT
);
CREATE INDEX IF NOT EXISTS idx_registro_corridas_proceso ON registro_corridas(proceso, creado_en DESC);
"""


def registrar(proceso: str, metricas: dict, conn=None) -> None:
    """proceso = nombre del workflow (p.ej. 'escanear_actas.yml')."""
    run_id = os.environ.get("GITHUB_RUN_ID")
    run_url = None
    if run_id and os.environ.get("GITHUB_REPOSITORY"):
        servidor = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_url = f"{servidor}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}"
    limpio = {k: int(v) if isinstance(v, bool) else v for k, v in metricas.items() if v is not None}
    propia = conn is None
    try:
        if propia:
            dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
            if not dsn:
                return
            conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute(CREAR_TABLA)
            cur.execute(
                "INSERT INTO registro_corridas (proceso, metricas, run_id, run_url) VALUES (%s, %s, %s, %s)",
                (proceso, json.dumps(limpio), run_id, run_url),
            )
        conn.commit()
    except Exception as e:
        print(f"::warning::No se pudo guardar el registro de la corrida ({type(e).__name__}: {e})", file=sys.stderr)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if propia and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
