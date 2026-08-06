from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from config import RUTA_BASE_DATOS

# Impide que dos tareas intenten crear/migrar las tablas a la vez.
_BLOQUEO_ESQUEMA = threading.Lock()


ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS programaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    intervalo_minutos INTEGER NOT NULL,
    activa INTEGER NOT NULL DEFAULT 1,
    creada_en TEXT NOT NULL,
    ultima_ejecucion TEXT,
    proxima_ejecucion TEXT,
    notificacion_sonora INTEGER NOT NULL DEFAULT 0,
    UNIQUE(chat_id, username)
);

CREATE TABLE IF NOT EXISTS historias_enviadas (
    chat_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    hash_imagen TEXT NOT NULL,
    enviada_en TEXT NOT NULL,
    PRIMARY KEY(chat_id, username, hash_imagen)
);

CREATE INDEX IF NOT EXISTS
    idx_programaciones_chat
    ON programaciones(chat_id);

CREATE INDEX IF NOT EXISTS
    idx_programaciones_activas
    ON programaciones(activa);

CREATE INDEX IF NOT EXISTS
    idx_historias_chat_usuario
    ON historias_enviadas(chat_id, username);
"""


TABLAS_REQUERIDAS = {
    "programaciones",
    "historias_enviadas",
}


def ahora_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tablas_existentes(
    conexion: sqlite3.Connection,
) -> set[str]:
    filas = conexion.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()

    return {
        str(fila["name"])
        for fila in filas
    }


def _columnas_existentes(
    conexion: sqlite3.Connection,
    tabla: str,
) -> set[str]:
    filas = conexion.execute(
        f"PRAGMA table_info({tabla})"
    ).fetchall()

    return {
        str(fila["name"])
        for fila in filas
    }


def _esquema_completo(
    conexion: sqlite3.Connection,
) -> bool:
    tablas = _tablas_existentes(
        conexion
    )

    if not TABLAS_REQUERIDAS.issubset(
        tablas
    ):
        return False

    columnas_programaciones = (
        _columnas_existentes(
            conexion,
            "programaciones",
        )
    )

    return (
        "notificacion_sonora"
        in columnas_programaciones
    )


def _asegurar_esquema(
    conexion: sqlite3.Connection,
) -> None:
    """
    Crea tablas y aplica migraciones sin borrar programaciones ni
    historias enviadas.
    """

    if _esquema_completo(
        conexion
    ):
        return

    with _BLOQUEO_ESQUEMA:
        if _esquema_completo(
            conexion
        ):
            return

        conexion.executescript(
            ESQUEMA_SQL
        )

        columnas_programaciones = (
            _columnas_existentes(
                conexion,
                "programaciones",
            )
        )

        if (
            "notificacion_sonora"
            not in columnas_programaciones
        ):
            conexion.execute(
                """
                ALTER TABLE programaciones
                ADD COLUMN notificacion_sonora
                INTEGER NOT NULL DEFAULT 0
                """
            )

        conexion.commit()


def conectar() -> sqlite3.Connection:
    RUTA_BASE_DATOS.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conexion = sqlite3.connect(
        RUTA_BASE_DATOS,
        timeout=30,
    )

    conexion.row_factory = sqlite3.Row
    conexion.execute(
        "PRAGMA journal_mode=WAL"
    )
    conexion.execute(
        "PRAGMA foreign_keys=ON"
    )
    conexion.execute(
        "PRAGMA busy_timeout=30000"
    )

    try:
        _asegurar_esquema(
            conexion
        )
    except Exception:
        conexion.close()
        raise

    return conexion


def inicializar() -> None:
    # conectar() ya verifica y repara el esquema.
    with conectar():
        pass


def diagnostico() -> dict[str, Any]:
    """Información segura para comprobar qué base abre el bot."""

    with conectar() as conexion:
        tablas = sorted(
            _tablas_existentes(
                conexion
            )
        )

    return {
        "ruta": str(
            RUTA_BASE_DATOS.resolve()
        ),
        "tablas": tablas,
    }


def guardar_programacion(
    chat_id: int,
    username: str,
    intervalo_minutos: int,
) -> int:
    """
    Crea la programación o actualiza la ya existente
    para el mismo chat y username.
    """

    with conectar() as conexion:
        conexion.execute(
            """
            INSERT INTO programaciones (
                chat_id,
                username,
                intervalo_minutos,
                activa,
                creada_en,
                proxima_ejecucion
            )
            VALUES (?, ?, ?, 1, ?, NULL)
            ON CONFLICT(chat_id, username)
            DO UPDATE SET
                intervalo_minutos = excluded.intervalo_minutos,
                activa = 1
            """,
            (
                chat_id,
                username,
                intervalo_minutos,
                ahora_utc_iso(),
            ),
        )

        fila = conexion.execute(
            """
            SELECT id
            FROM programaciones
            WHERE chat_id = ? AND username = ?
            """,
            (chat_id, username),
        ).fetchone()

    if fila is None:
        raise RuntimeError(
            "No se pudo guardar la programación."
        )

    return int(fila["id"])


def obtener_programacion(
    programacion_id: int,
) -> dict[str, Any] | None:
    with conectar() as conexion:
        fila = conexion.execute(
            """
            SELECT *
            FROM programaciones
            WHERE id = ?
            """,
            (programacion_id,),
        ).fetchone()

    return dict(fila) if fila else None


def listar_programaciones(
    chat_id: int,
) -> list[dict[str, Any]]:
    with conectar() as conexion:
        filas = conexion.execute(
            """
            SELECT *
            FROM programaciones
            WHERE chat_id = ?
            ORDER BY username COLLATE NOCASE
            """,
            (chat_id,),
        ).fetchall()

    return [dict(fila) for fila in filas]


def listar_programaciones_activas(
) -> list[dict[str, Any]]:
    with conectar() as conexion:
        filas = conexion.execute(
            """
            SELECT *
            FROM programaciones
            WHERE activa = 1
            ORDER BY id
            """
        ).fetchall()

    return [dict(fila) for fila in filas]


def actualizar_estado(
    programacion_id: int,
    activa: bool,
) -> None:
    with conectar() as conexion:
        conexion.execute(
            """
            UPDATE programaciones
            SET activa = ?,
                proxima_ejecucion =
                    CASE WHEN ? = 0 THEN NULL
                         ELSE proxima_ejecucion
                    END
            WHERE id = ?
            """,
            (
                1 if activa else 0,
                1 if activa else 0,
                programacion_id,
            ),
        )


def actualizar_notificacion_sonora(
    programacion_id: int,
    activa: bool,
) -> None:
    """Activa o silencia la alerta de una programación."""

    with conectar() as conexion:
        conexion.execute(
            """
            UPDATE programaciones
            SET notificacion_sonora = ?
            WHERE id = ?
            """,
            (
                1 if activa else 0,
                programacion_id,
            ),
        )


def actualizar_intervalo(
    programacion_id: int,
    intervalo_minutos: int,
) -> None:
    with conectar() as conexion:
        conexion.execute(
            """
            UPDATE programaciones
            SET intervalo_minutos = ?,
                activa = 1
            WHERE id = ?
            """,
            (
                intervalo_minutos,
                programacion_id,
            ),
        )


def actualizar_fechas_ejecucion(
    programacion_id: int,
    ultima_ejecucion: str | None,
    proxima_ejecucion: str | None,
) -> None:
    with conectar() as conexion:
        conexion.execute(
            """
            UPDATE programaciones
            SET ultima_ejecucion = ?,
                proxima_ejecucion = ?
            WHERE id = ?
            """,
            (
                ultima_ejecucion,
                proxima_ejecucion,
                programacion_id,
            ),
        )


def establecer_proxima_ejecucion(
    programacion_id: int,
    proxima_ejecucion: str | None,
) -> None:
    with conectar() as conexion:
        conexion.execute(
            """
            UPDATE programaciones
            SET proxima_ejecucion = ?
            WHERE id = ?
            """,
            (
                proxima_ejecucion,
                programacion_id,
            ),
        )


def eliminar_programacion(
    programacion_id: int,
) -> None:
    with conectar() as conexion:
        conexion.execute(
            """
            DELETE FROM programaciones
            WHERE id = ?
            """,
            (programacion_id,),
        )


def imagen_ya_enviada(
    chat_id: int,
    username: str,
    hash_imagen: str,
) -> bool:
    """
    La deduplicación es permanente: mientras exista la base,
    el mismo archivo no se vuelve a enviar.
    """

    with conectar() as conexion:
        fila = conexion.execute(
            """
            SELECT 1
            FROM historias_enviadas
            WHERE chat_id = ?
              AND username = ?
              AND hash_imagen = ?
            LIMIT 1
            """,
            (
                chat_id,
                username,
                hash_imagen,
            ),
        ).fetchone()

    return fila is not None


def registrar_imagen_enviada(
    chat_id: int,
    username: str,
    hash_imagen: str,
) -> None:
    with conectar() as conexion:
        conexion.execute(
            """
            INSERT OR IGNORE INTO historias_enviadas (
                chat_id,
                username,
                hash_imagen,
                enviada_en
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                chat_id,
                username,
                hash_imagen,
                ahora_utc_iso(),
            ),
        )
