from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _booleano(nombre: str, predeterminado: bool = False) -> bool:
    valor = os.getenv(nombre)

    if valor is None:
        return predeterminado

    return valor.strip().lower() in {
        "1",
        "true",
        "yes",
        "si",
        "sí",
        "on",
    }


def _lista_ids(valor: str) -> set[int]:
    ids: set[int] = set()

    for parte in valor.split(","):
        parte = parte.strip()

        if not parte:
            continue

        try:
            ids.add(int(parte))
        except ValueError as error:
            raise RuntimeError(
                "TELEGRAM_USER_IDS contiene un valor inválido: "
                f"{parte!r}"
            ) from error

    return ids


def _ruta_desde_entorno(nombre: str, predeterminada: str) -> Path:
    valor = os.getenv(nombre, predeterminada).strip()
    ruta = Path(valor).expanduser()

    if not ruta.is_absolute():
        ruta = BASE_DIR / ruta

    return ruta.resolve()


def cargar_cuentas() -> dict[str, str]:
    ruta = _ruta_desde_entorno(
        "ACCOUNTS_FILE",
        "accounts.json",
    )

    if not ruta.exists():
        return {}

    try:
        contenido = json.loads(
            ruta.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"No se pudo leer el archivo de cuentas: {ruta}"
        ) from error

    if not isinstance(contenido, dict):
        raise RuntimeError(
            "accounts.json debe contener un objeto JSON con "
            "el formato nombre visible -> username."
        )

    cuentas: dict[str, str] = {}

    for nombre, username in contenido.items():
        nombre_limpio = str(nombre).strip()
        username_limpio = str(username).strip().lstrip("@")

        if not nombre_limpio or not username_limpio:
            raise RuntimeError(
                "accounts.json contiene una cuenta vacía."
            )

        cuentas[nombre_limpio] = username_limpio

    return cuentas


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USUARIOS_AUTORIZADOS = _lista_ids(
    os.getenv("TELEGRAM_USER_IDS", "")
)

NOMBRE_ZONA_HORARIA = os.getenv(
    "TIMEZONE",
    "UTC",
).strip()
ZONA_HORARIA = ZoneInfo(NOMBRE_ZONA_HORARIA)

CUENTAS = cargar_cuentas()
MOSTRAR_NAVEGADOR = _booleano(
    "SHOW_BROWSER",
    False,
)

RUTA_BASE_DATOS = _ruta_desde_entorno(
    "DATABASE_PATH",
    "data/storypulse.db",
)


def validar_configuracion_bot() -> None:
    errores: list[str] = []

    if not BOT_TOKEN:
        errores.append(
            "BOT_TOKEN no está configurado en .env."
        )

    if not USUARIOS_AUTORIZADOS:
        errores.append(
            "TELEGRAM_USER_IDS no contiene usuarios autorizados."
        )

    if not CUENTAS:
        errores.append(
            "No hay perfiles configurados. Copia "
            "accounts.example.json como accounts.json y edítalo."
        )

    if errores:
        raise RuntimeError("\n".join(errores))
