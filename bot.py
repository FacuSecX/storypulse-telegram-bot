# bot.py

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import database as db

from config import (
    BOT_TOKEN,
    CUENTAS,
    USUARIOS_AUTORIZADOS,
    ZONA_HORARIA,
    validar_configuracion_bot,
)
from history import descargar_imagenes
from publicaciones import descargar_publicaciones


# ============================================================
# CONFIGURACIÓN
# ============================================================

# El token, los usuarios autorizados, la zona horaria y los perfiles
# se cargan desde .env y accounts.json mediante config.py.


# Intervalos disponibles para las programaciones.
INTERVALOS = [
    ("1 hora", 60),
    ("2 horas", 120),
    ("3 horas", 180),
    ("6 horas", 360),
    ("12 horas", 720),
    ("24 horas", 1440),
]


# Solo se ejecutará una búsqueda al mismo tiempo.
BLOQUEO_SCRAPER = asyncio.Lock()


logging.basicConfig(
    format=(
        "%(asctime)s - "
        "%(name)s - "
        "%(levelname)s - "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# No registrar las URL completas del Bot API, porque contienen
# el token del bot.
logging.getLogger("httpx").setLevel(logging.WARNING)


class ProgresoTelegram:
    """Actualiza un único mensaje de Telegram cada pocos segundos."""

    def __init__(
        self,
        query,
        encabezado: str,
        intervalo_segundos: float = 4.0,
    ) -> None:
        self.query = query
        self.encabezado = encabezado
        self.intervalo_segundos = intervalo_segundos
        self.inicio = time.monotonic()

        self._detalle = "⏳ Preparando la tarea…"
        self._ultimo_texto = ""
        self._cerrado = False
        self._lock = threading.Lock()
        self._tarea: asyncio.Task | None = None

    def informar(self, detalle: str) -> None:
        with self._lock:
            self._detalle = str(detalle)

    def iniciar(self) -> None:
        if self._tarea is None:
            self._tarea = asyncio.create_task(
                self._bucle()
            )

    async def _bucle(self) -> None:
        while not self._cerrado:
            await asyncio.sleep(
                self.intervalo_segundos
            )

            with self._lock:
                detalle = self._detalle

            segundos = int(
                time.monotonic() - self.inicio
            )

            texto = (
                f"{self.encabezado}\n\n"
                f"{detalle}\n\n"
                f"⏱ Tiempo transcurrido: {segundos} s"
            )

            if texto == self._ultimo_texto:
                continue

            try:
                await self.query.edit_message_text(
                    text=texto,
                    parse_mode="HTML",
                )
                self._ultimo_texto = texto

            except BadRequest as error:
                if (
                    "message is not modified"
                    not in str(error).lower()
                ):
                    logger.warning(
                        "No se pudo actualizar el progreso: %s",
                        error,
                    )

            except TelegramError as error:
                logger.warning(
                    "Error de Telegram actualizando progreso: %s",
                    error,
                )

            except Exception:
                logger.exception(
                    "Error inesperado actualizando progreso."
                )

    async def cerrar(self) -> None:
        self._cerrado = True

        if self._tarea is not None:
            self._tarea.cancel()

            with suppress(
                asyncio.CancelledError
            ):
                await self._tarea

            self._tarea = None


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def usuario_autorizado(update: Update) -> bool:
    usuario = update.effective_user

    return (
        usuario is not None
        and usuario.id in USUARIOS_AUTORIZADOS
    )


def nombre_visible_de(username: str) -> str:
    for nombre, cuenta in CUENTAS.items():
        if cuenta == username:
            return nombre

    return username


def texto_intervalo(minutos: int) -> str:
    if minutos == 60:
        return "1 hora"

    if minutos % 60 == 0:
        return f"{minutos // 60} horas"

    return f"{minutos} minutos"


def hash_imagen(imagen: Any) -> str:
    """
    Utiliza el hash de history.py si existe.
    Si no existe, lo calcula usando los bytes reales.
    """

    hash_existente = getattr(
        imagen,
        "hash_archivo",
        None,
    )

    if hash_existente:
        return str(hash_existente)

    return hashlib.sha256(
        imagen.contenido
    ).hexdigest()


def extension_segura(imagen: Any) -> str:
    extension = str(
        getattr(imagen, "extension", "jpg")
    ).lower()

    if extension not in {
        "jpg",
        "jpeg",
        "png",
        "webp",
    }:
        return "jpg"

    return extension


def formatear_fecha(fecha_iso: str | None) -> str:
    if not fecha_iso:
        return "Sin datos"

    try:
        fecha = datetime.fromisoformat(fecha_iso)

        if fecha.tzinfo is None:
            fecha = fecha.replace(
                tzinfo=timezone.utc
            )

        local = fecha.astimezone(
            ZONA_HORARIA
        )

        return local.strftime(
            "%d/%m/%Y %H:%M"
        )

    except ValueError:
        return fecha_iso


def nombre_job(programacion_id: int) -> str:
    return (
        f"historias_programadas:"
        f"{programacion_id}"
    )


# ============================================================
# MENÚS
# ============================================================

def crear_menu() -> InlineKeyboardMarkup:
    """
    Crea los botones de revisión manual,
    programación y administración.
    """

    botones = []

    for nombre_visible, username in CUENTAS.items():
        botones.append(
            InlineKeyboardButton(
                text=f"📸 {nombre_visible}",
                callback_data=f"historia:{username}",
            )
        )

    filas = []

    for posicion in range(0, len(botones), 2):
        filas.append(
            botones[posicion:posicion + 2]
        )

    filas.append(
        [
            InlineKeyboardButton(
                text="🖼 Descargar publicaciones",
                callback_data="publicaciones:menu",
            )
        ]
    )

    filas.append(
        [
            InlineKeyboardButton(
                text="⚙️ Crear programación",
                callback_data="programar:inicio",
            )
        ]
    )

    filas.append(
        [
            InlineKeyboardButton(
                text="📋 Ver programaciones",
                callback_data="programar:listar",
            )
        ]
    )

    filas.append(
        [
            InlineKeyboardButton(
                text="🔄 Actualizar menú",
                callback_data="actualizar_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(filas)


def menu_cuentas_programacion() -> InlineKeyboardMarkup:
    botones = []

    for nombre_visible, username in CUENTAS.items():
        botones.append(
            InlineKeyboardButton(
                text=nombre_visible,
                callback_data=(
                    f"programar:cuenta:{username}"
                ),
            )
        )

    filas = []

    for posicion in range(0, len(botones), 2):
        filas.append(
            botones[posicion:posicion + 2]
        )

    filas.append(
        [
            InlineKeyboardButton(
                text="‹ Volver",
                callback_data="actualizar_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(filas)


def menu_intervalos_nuevo(
    username: str,
) -> InlineKeyboardMarkup:
    botones = []

    for etiqueta, minutos in INTERVALOS:
        botones.append(
            InlineKeyboardButton(
                text=etiqueta,
                callback_data=(
                    f"programar:crear:"
                    f"{username}:{minutos}"
                ),
            )
        )

    filas = []

    for posicion in range(0, len(botones), 2):
        filas.append(
            botones[posicion:posicion + 2]
        )

    filas.append(
        [
            InlineKeyboardButton(
                text="‹ Volver",
                callback_data="programar:inicio",
            )
        ]
    )

    return InlineKeyboardMarkup(filas)


def menu_intervalos_existente(
    programacion_id: int,
) -> InlineKeyboardMarkup:
    botones = []

    for etiqueta, minutos in INTERVALOS:
        botones.append(
            InlineKeyboardButton(
                text=etiqueta,
                callback_data=(
                    f"programar:intervalo:"
                    f"{programacion_id}:{minutos}"
                ),
            )
        )

    filas = []

    for posicion in range(0, len(botones), 2):
        filas.append(
            botones[posicion:posicion + 2]
        )

    filas.append(
        [
            InlineKeyboardButton(
                text="‹ Volver",
                callback_data=(
                    f"programar:detalle:"
                    f"{programacion_id}"
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(filas)


def notificacion_sonora_activa(
    programacion: dict[str, Any],
) -> bool:
    return bool(
        int(
            programacion.get(
                "notificacion_sonora",
                0,
            )
            or 0
        )
    )


def texto_detalle_programacion(
    programacion: dict[str, Any],
    *,
    aviso: str | None = None,
) -> str:
    estado = (
        "Activa ✅"
        if programacion["activa"]
        else "Pausada ⏸"
    )

    estado_notificacion = (
        "Activada 🔔"
        if notificacion_sonora_activa(
            programacion
        )
        else "Desactivada 🔕"
    )

    lineas = [
        "⚙️ <b>Programación</b>",
        "",
        f"Cuenta: @{programacion['username']}",
        f"Estado: {estado}",
        (
            "Notificación al detectar novedades: "
            f"{estado_notificacion}"
        ),
        (
            "Frecuencia: cada "
            f"{texto_intervalo(int(programacion['intervalo_minutos']))}"
        ),
        (
            "Última revisión: "
            f"{formatear_fecha(programacion['ultima_ejecucion'])}"
        ),
        (
            "Próxima revisión: "
            f"{formatear_fecha(programacion['proxima_ejecucion'])}"
        ),
    ]

    if aviso:
        lineas.extend(
            [
                "",
                aviso,
            ]
        )

    return "\n".join(
        lineas
    )


def menu_detalle(
    programacion: dict[str, Any],
) -> InlineKeyboardMarkup:
    programacion_id = int(
        programacion["id"]
    )

    activa = bool(
        programacion["activa"]
    )

    texto_estado = (
        "⏸ Pausar"
        if activa
        else "▶️ Reanudar"
    )

    notificacion_activa = (
        notificacion_sonora_activa(
            programacion
        )
    )

    texto_notificacion = (
        "🔕 Desactivar notif."
        if notificacion_activa
        else "🔔 Activar notif."
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="🔎 Revisar ahora",
                    callback_data=(
                        f"programar:ahora:"
                        f"{programacion_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=texto_estado,
                    callback_data=(
                        f"programar:estado:"
                        f"{programacion_id}"
                    ),
                ),
                InlineKeyboardButton(
                    text="⏱ Cambiar frecuencia",
                    callback_data=(
                        f"programar:cambiar:"
                        f"{programacion_id}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texto_notificacion,
                    callback_data=(
                        f"programar:notif:"
                        f"{programacion_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧪 Probar alerta",
                    callback_data=(
                        f"programar:probar_alerta:"
                        f"{programacion_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Eliminar",
                    callback_data=(
                        f"programar:eliminar:"
                        f"{programacion_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="‹ Ver programaciones",
                    callback_data="programar:listar",
                )
            ],
        ]
    )



# ============================================================
# DESCARGA MANUAL DE PUBLICACIONES
# ============================================================

def menu_cuentas_publicaciones() -> InlineKeyboardMarkup:
    botones = []

    for nombre_visible, username in CUENTAS.items():
        botones.append(
            InlineKeyboardButton(
                text=nombre_visible,
                callback_data=(
                    f"publicaciones:cuenta:{username}"
                ),
            )
        )

    filas = []

    for posicion in range(0, len(botones), 2):
        filas.append(
            botones[posicion:posicion + 2]
        )

    filas.append(
        [
            InlineKeyboardButton(
                text="‹ Volver",
                callback_data="actualizar_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(filas)


async def mostrar_menu_publicaciones(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        await query.edit_message_text(
            "⛔ No estás autorizado."
        )
        return

    await query.edit_message_text(
        text=(
            "🖼 <b>Descargar publicaciones</b>\n\n"
            "Selecciona el perfil. El bot abrirá "
            "la pestaña Publicaciones, cargará la grilla "
            "completa y mostrará las imágenes en álbumes."
        ),
        parse_mode="HTML",
        reply_markup=menu_cuentas_publicaciones(),
    )


async def descargar_publicaciones_perfil(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        await query.edit_message_text(
            "⛔ No estás autorizado."
        )
        return

    partes = (query.data or "").split(
        ":",
        2,
    )

    if len(partes) != 3:
        return

    username = partes[2]

    if username not in CUENTAS.values():
        await query.edit_message_text(
            "❌ Cuenta no reconocida.",
            reply_markup=crear_menu(),
        )
        return

    await query.edit_message_text(
        text=(
            f"🖼 <b>Publicaciones de @{username}</b>\n\n"
            "⏳ Preparando la búsqueda…"
        ),
        parse_mode="HTML",
    )

    progreso = ProgresoTelegram(
        query=query,
        encabezado=(
            f"🖼 <b>Publicaciones de @{username}</b>"
        ),
    )
    progreso.iniciar()

    try:
        if BLOQUEO_SCRAPER.locked():
            progreso.informar(
                "⌛ Esperando que termine otra búsqueda…"
            )

        async with BLOQUEO_SCRAPER:
            progreso.informar(
                "🚀 Iniciando navegador…"
            )

            resultado = await asyncio.to_thread(
                descargar_publicaciones,
                username,
                progreso.informar,
            )

        recursos = resultado.recursos
        total = len(recursos)

        if total == 0:
            await progreso.cerrar()

            await query.edit_message_text(
                text=(
                    f"ℹ️ No se encontraron publicaciones "
                    f"para @{username}."
                ),
                reply_markup=crear_menu(),
            )
            return

        lotes = [
            recursos[posicion:posicion + 10]
            for posicion in range(0, total, 10)
        ]

        progreso.informar(
            (
                "📤 Comenzando el envío…\n"
                f"Fotografías: {total}\n"
                f"Álbumes: {len(lotes)}"
            )
        )

        for numero_lote, lote in enumerate(
            lotes,
            start=1,
        ):
            inicio = (numero_lote - 1) * 10 + 1
            fin = inicio + len(lote) - 1

            progreso.informar(
                (
                    "📤 Enviando imágenes…\n"
                    f"Álbum: {numero_lote}/{len(lotes)}\n"
                    f"Imágenes: {inicio}-{fin} de {total}"
                )
            )

            if len(lote) == 1:
                recurso = lote[0]
                memoria = BytesIO(
                    recurso.contenido
                )

                archivo = InputFile(
                    memoria,
                    filename=(
                        f"publicacion_{username}_{inicio}."
                        f"{recurso.extension}"
                    ),
                )

                await context.bot.send_photo(
                    chat_id=query.message.chat.id,
                    photo=archivo,
                    caption=(
                        f"🖼 Publicaciones de @{username}\n"
                        f"Imagen {inicio}/{total}"
                    ),
                    read_timeout=180,
                    write_timeout=180,
                    connect_timeout=60,
                )

            else:
                medios = []
                memorias = []

                for desplazamiento, recurso in enumerate(
                    lote,
                    start=0,
                ):
                    indice_global = inicio + desplazamiento

                    memoria = BytesIO(
                        recurso.contenido
                    )
                    memorias.append(memoria)

                    caption = None

                    if desplazamiento == 0:
                        caption = (
                            f"🖼 Publicaciones de @{username}\n"
                            f"Álbum {numero_lote}/{len(lotes)} · "
                            f"Imágenes {inicio}-{fin} de {total}"
                        )

                    medios.append(
                        InputMediaPhoto(
                            media=memoria,
                            filename=(
                                f"publicacion_{username}_"
                                f"{indice_global}."
                                f"{recurso.extension}"
                            ),
                            caption=caption,
                        )
                    )

                await context.bot.send_media_group(
                    chat_id=query.message.chat.id,
                    media=medios,
                    read_timeout=180,
                    write_timeout=180,
                    connect_timeout=60,
                )

            if numero_lote < len(lotes):
                await asyncio.sleep(0.35)

        await progreso.cerrar()

        await query.edit_message_text(
            text=(
                f"✅ Finalizado: {total} imagen(es) "
                f"mostradas en {len(lotes)} álbum(es)."
            ),
            reply_markup=crear_menu(),
        )

    except Exception as error:
        await progreso.cerrar()

        logger.exception(
            "Error descargando publicaciones de @%s",
            username,
        )

        mensaje_error = str(error)

        if len(mensaje_error) > 900:
            mensaje_error = (
                mensaje_error[:900] + "…"
            )

        await query.edit_message_text(
            text=(
                f"❌ Error descargando publicaciones "
                f"de @{username}:\n\n"
                f"{mensaje_error}"
            ),
            reply_markup=crear_menu(),
        )


# ============================================================
# DESCARGA, DEDUPLICACIÓN Y ENVÍO
# ============================================================

async def descargar_y_enviar_nuevas(
    *,
    bot,
    chat_id: int,
    username: str,
    progress_callback=None,
    alerta_automatica: bool = False,
    notificacion_sonora: bool = False,
) -> int:
    """
    Descarga las historias y envía solamente las imágenes
    que todavía no figuran en SQLite.

    Devuelve la cantidad de imágenes nuevas enviadas.
    """

    if progress_callback is not None:
        progress_callback(
            "🌐 Abriendo el perfil y cargando historias…"
        )

    async with BLOQUEO_SCRAPER:
        imagenes = await asyncio.to_thread(
            descargar_imagenes,
            username,
        )

    if progress_callback is not None:
        progress_callback(
            (
                "🧪 Analizando las historias detectadas…\n"
                f"Recursos encontrados: {len(imagenes)}"
            )
        )

    nuevas: list[tuple[Any, str]] = []

    for imagen in imagenes:
        identificador = hash_imagen(imagen)

        if not db.imagen_ya_enviada(
            chat_id=chat_id,
            username=username,
            hash_imagen=identificador,
        ):
            nuevas.append(
                (imagen, identificador)
            )

    total = len(nuevas)

    if progress_callback is not None:
        progress_callback(
            (
                "📋 Comparación terminada.\n"
                f"Historias nuevas: {total}"
            )
        )

    # Solo las ejecuciones automáticas pueden producir una alerta.
    # Si no hay novedades, no se envía ningún mensaje ni sonido.
    if alerta_automatica and total > 0:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔔 Nueva historia detectada en @{username}\n"
                f"Cantidad: {total}"
            ),
            disable_notification=(
                not notificacion_sonora
            ),
        )

    for indice, (
        imagen,
        identificador,
    ) in enumerate(
        nuevas,
        start=1,
    ):
        if progress_callback is not None:
            progress_callback(
                (
                    "📤 Enviando historias…\n"
                    f"Historia: {indice}/{total}"
                )
            )

        archivo_memoria = BytesIO(
            imagen.contenido
        )

        nombre_archivo = (
            f"historia_{username}_{indice}."
            f"{extension_segura(imagen)}"
        )

        archivo_telegram = InputFile(
            archivo_memoria,
            filename=nombre_archivo,
        )

        caption = (
            f"📸 {nombre_visible_de(username)}\n"
            f"@{username}\n"
            f"Nueva historia {indice}/{total}"
        )

        # Se envía como foto visible en el chat.
        await bot.send_photo(
            chat_id=chat_id,
            photo=archivo_telegram,
            caption=caption,
            disable_notification=True,
            read_timeout=90,
            write_timeout=90,
            connect_timeout=30,
        )

        # Se registra únicamente después de que Telegram
        # confirmó correctamente el envío.
        db.registrar_imagen_enviada(
            chat_id=chat_id,
            username=username,
            hash_imagen=identificador,
        )

    return total


# ============================================================
# JOBQUEUE
# ============================================================

def eliminar_job_de_memoria(
    application: Application,
    programacion_id: int,
) -> None:
    if application.job_queue is None:
        return

    trabajos = (
        application.job_queue
        .get_jobs_by_name(
            nombre_job(programacion_id)
        )
    )

    for trabajo in trabajos:
        trabajo.schedule_removal()


def registrar_job(
    application: Application,
    programacion: dict[str, Any],
    *,
    primera_ejecucion: int | datetime = 5,
) -> None:
    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue no está disponible. "
            "Instala python-telegram-bot[job-queue]."
        )

    programacion_id = int(
        programacion["id"]
    )

    intervalo_minutos = int(
        programacion["intervalo_minutos"]
    )

    eliminar_job_de_memoria(
        application,
        programacion_id,
    )

    application.job_queue.run_repeating(
        callback=ejecucion_automatica,
        interval=timedelta(
            minutes=intervalo_minutos
        ),
        first=primera_ejecucion,
        data={
            "programacion_id": programacion_id,
        },
        name=nombre_job(programacion_id),
        chat_id=int(
            programacion["chat_id"]
        ),
        job_kwargs={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 3600,
        },
    )

    if isinstance(
        primera_ejecucion,
        datetime,
    ):
        proxima = primera_ejecucion
    else:
        proxima = (
            datetime.now(timezone.utc)
            + timedelta(
                seconds=max(
                    int(primera_ejecucion),
                    0,
                )
            )
        )

    db.establecer_proxima_ejecucion(
        programacion_id,
        proxima.isoformat(),
    )


async def ejecucion_automatica(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if context.job is None:
        return

    programacion_id = int(
        context.job.data[
            "programacion_id"
        ]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or not bool(
            programacion["activa"]
        )
    ):
        context.job.schedule_removal()
        return

    chat_id = int(
        programacion["chat_id"]
    )

    username = str(
        programacion["username"]
    )

    intervalo_minutos = int(
        programacion["intervalo_minutos"]
    )

    momento_inicio = datetime.now(
        timezone.utc
    )

    proxima = (
        momento_inicio
        + timedelta(
            minutes=intervalo_minutos
        )
    )

    try:
        cantidad = await descargar_y_enviar_nuevas(
            bot=context.bot,
            chat_id=chat_id,
            username=username,
            alerta_automatica=True,
            notificacion_sonora=(
                notificacion_sonora_activa(
                    programacion
                )
            ),
        )

        if cantidad == 0:
            logger.info(
                "@%s: revisión automática sin novedades",
                username,
            )
        else:
            logger.info(
                "@%s: %s historia(s) nueva(s) enviadas",
                username,
                cantidad,
            )

    except Exception as error:
        logger.exception(
            "Error en la programación %s",
            programacion_id,
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"❌ Error en la revisión automática "
                f"de @{username}:\n\n"
                f"{str(error)[:800]}"
            ),
            disable_notification=True,
        )

    finally:
        db.actualizar_fechas_ejecucion(
            programacion_id=programacion_id,
            ultima_ejecucion=momento_inicio.isoformat(),
            proxima_ejecucion=proxima.isoformat(),
        )


# ============================================================
# MENÚ PRINCIPAL Y REVISIÓN MANUAL
# ============================================================

async def mostrar_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not usuario_autorizado(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ No estás autorizado para usar este bot."
            )

        return

    texto = (
        "📱 <b>Visor de historias</b>\n\n"
        "Selecciona una cuenta para revisar sus historias "
        "o configura una revisión automática:"
    )

    await update.effective_message.reply_text(
        text=texto,
        parse_mode="HTML",
        reply_markup=crear_menu(),
    )


async def actualizar_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        await query.edit_message_text(
            "⛔ No estás autorizado."
        )
        return

    await query.edit_message_text(
        text=(
            "📱 <b>Visor de historias</b>\n\n"
            "Selecciona una cuenta para revisar "
            "o configura una programación:"
        ),
        parse_mode="HTML",
        reply_markup=crear_menu(),
    )


async def enviar_historias(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        await query.edit_message_text(
            "⛔ No estás autorizado para usar este bot."
        )
        return

    datos = query.data or ""

    if not datos.startswith(
        "historia:"
    ):
        return

    username = datos.split(
        ":",
        1,
    )[1]

    if username not in CUENTAS.values():
        await query.edit_message_text(
            "❌ Cuenta no reconocida."
        )
        return

    await query.edit_message_text(
        text=(
            f"📸 <b>Historias de @{username}</b>\n\n"
            "⏳ Preparando la revisión…"
        ),
        parse_mode="HTML",
    )

    progreso = ProgresoTelegram(
        query=query,
        encabezado=(
            f"📸 <b>Historias de @{username}</b>"
        ),
    )
    progreso.iniciar()

    try:
        if BLOQUEO_SCRAPER.locked():
            progreso.informar(
                "⌛ Esperando que termine otra búsqueda…"
            )

        cantidad = await descargar_y_enviar_nuevas(
            bot=context.bot,
            chat_id=query.message.chat.id,
            username=username,
            progress_callback=progreso.informar,
        )

        await progreso.cerrar()

        if cantidad == 0:
            texto = (
                f"ℹ️ @{username}: "
                "No se encontraron nuevas historias."
            )
        else:
            texto = (
                f"✅ Finalizado: {cantidad} "
                f"historia(s) nueva(s) enviada(s)."
            )

        await query.edit_message_text(
            text=texto,
            reply_markup=crear_menu(),
        )

    except Exception as error:
        await progreso.cerrar()

        logger.exception(
            "Error revisando @%s",
            username,
        )

        mensaje_error = str(error)

        if len(mensaje_error) > 800:
            mensaje_error = (
                mensaje_error[:800]
                + "…"
            )

        await query.edit_message_text(
            text=(
                f"❌ Error revisando "
                f"@{username}:\n\n"
                f"{mensaje_error}"
            ),
            reply_markup=crear_menu(),
        )


# ============================================================
# CREAR PROGRAMACIÓN
# ============================================================

async def iniciar_programacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        await query.edit_message_text(
            "⛔ No estás autorizado."
        )
        return

    await query.edit_message_text(
        text=(
            "⚙️ <b>Nueva programación</b>\n\n"
            "Selecciona la cuenta:"
        ),
        parse_mode="HTML",
        reply_markup=menu_cuentas_programacion(),
    )


async def elegir_cuenta_programacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    username = (
        query.data or ""
    ).rsplit(
        ":",
        1,
    )[1]

    if username not in CUENTAS.values():
        await query.edit_message_text(
            "❌ Cuenta no reconocida.",
            reply_markup=crear_menu(),
        )
        return

    await query.edit_message_text(
        text=(
            f"⏱ ¿Cada cuánto quieres revisar "
            f"<b>@{username}</b>?"
        ),
        parse_mode="HTML",
        reply_markup=menu_intervalos_nuevo(
            username
        ),
    )


async def crear_programacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    partes = (
        query.data or ""
    ).split(":")

    if len(partes) != 4:
        return

    username = partes[2]
    intervalo_minutos = int(
        partes[3]
    )

    if username not in CUENTAS.values():
        await query.edit_message_text(
            "❌ Cuenta no reconocida.",
            reply_markup=crear_menu(),
        )
        return

    programacion_id = db.guardar_programacion(
        chat_id=query.message.chat.id,
        username=username,
        intervalo_minutos=intervalo_minutos,
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if programacion is None:
        raise RuntimeError(
            "No se pudo recuperar "
            "la programación."
        )

    # Ejecuta la primera revisión en 5 segundos.
    registrar_job(
        context.application,
        programacion,
        primera_ejecucion=5,
    )

    await query.edit_message_text(
        text=(
            "✅ <b>Programación configurada</b>\n\n"
            f"Cuenta: @{username}\n"
            f"Frecuencia: cada "
            f"{texto_intervalo(intervalo_minutos)}\n"
            "Primera revisión: en unos segundos\n"
            "Notificación sonora: desactivada 🔕\n\n"
            "Puedes activarla desde el detalle de esta "
            "programación. Solo sonará cuando una revisión "
            "automática encuentre historias nuevas."
        ),
        parse_mode="HTML",
        reply_markup=menu_detalle(
            programacion
        ),
    )


# ============================================================
# LISTAR Y ADMINISTRAR PROGRAMACIONES
# ============================================================

async def listar_programaciones(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    programaciones = db.listar_programaciones(
        query.message.chat.id
    )

    if not programaciones:
        await query.edit_message_text(
            text=(
                "📋 No tienes programaciones "
                "configuradas."
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="⚙️ Crear programación",
                            callback_data="programar:inicio",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="‹ Menú principal",
                            callback_data="actualizar_menu",
                        )
                    ],
                ]
            ),
        )
        return

    filas = []

    for programacion in programaciones:
        estado = (
            "✅"
            if programacion["activa"]
            else "⏸"
        )

        intervalo = texto_intervalo(
            int(
                programacion[
                    "intervalo_minutos"
                ]
            )
        )

        filas.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{estado} "
                        f"{'🔔' if notificacion_sonora_activa(programacion) else '🔕'} "
                        f"@{programacion['username']} "
                        f"· {intervalo}"
                    ),
                    callback_data=(
                        f"programar:detalle:"
                        f"{programacion['id']}"
                    ),
                )
            ]
        )

    filas.append(
        [
            InlineKeyboardButton(
                text="⚙️ Crear otra",
                callback_data="programar:inicio",
            )
        ]
    )

    filas.append(
        [
            InlineKeyboardButton(
                text="‹ Menú principal",
                callback_data="actualizar_menu",
            )
        ]
    )

    await query.edit_message_text(
        text=(
            "📋 <b>Programaciones</b>\n\n"
            "Selecciona una programación:"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            filas
        ),
    )


async def mostrar_detalle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    programacion_id = int(
        (
            query.data or ""
        ).rsplit(
            ":",
            1,
        )[1]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(
            programacion["chat_id"]
        ) != query.message.chat.id
    ):
        await query.edit_message_text(
            "❌ La programación no existe.",
            reply_markup=crear_menu(),
        )
        return

    await query.edit_message_text(
        text=texto_detalle_programacion(
            programacion
        ),
        parse_mode="HTML",
        reply_markup=menu_detalle(
            programacion
        ),
    )


async def ejecutar_ahora(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    programacion_id = int(
        (
            query.data or ""
        ).rsplit(
            ":",
            1,
        )[1]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(
            programacion["chat_id"]
        ) != query.message.chat.id
    ):
        await query.edit_message_text(
            "❌ La programación no existe.",
            reply_markup=crear_menu(),
        )
        return

    username = str(
        programacion["username"]
    )

    await query.edit_message_text(
        f"🔎 Revisando @{username} ahora…"
    )

    try:
        cantidad = await descargar_y_enviar_nuevas(
            bot=context.bot,
            chat_id=query.message.chat.id,
            username=username,
        )

        ahora = datetime.now(
            timezone.utc
        )

        db.actualizar_fechas_ejecucion(
            programacion_id=programacion_id,
            ultima_ejecucion=ahora.isoformat(),
            proxima_ejecucion=(
                programacion[
                    "proxima_ejecucion"
                ]
            ),
        )

        if cantidad == 0:
            texto = (
                f"ℹ️ @{username}: "
                "No se encontraron nuevas historias."
            )
        else:
            texto = (
                f"✅ @{username}: "
                f"{cantidad} historia(s) "
                "nueva(s) enviada(s)."
            )

        actualizada = db.obtener_programacion(
            programacion_id
        )

        await query.edit_message_text(
            text=texto,
            reply_markup=menu_detalle(
                actualizada
                or programacion
            ),
        )

    except Exception as error:
        logger.exception(
            "Error ejecutando manualmente "
            "la programación %s",
            programacion_id,
        )

        await query.edit_message_text(
            text=(
                f"❌ Error revisando "
                f"@{username}:\n\n"
                f"{str(error)[:800]}"
            ),
            reply_markup=menu_detalle(
                programacion
            ),
        )


async def cambiar_estado(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    programacion_id = int(
        (
            query.data or ""
        ).rsplit(
            ":",
            1,
        )[1]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(
            programacion["chat_id"]
        ) != query.message.chat.id
    ):
        await query.edit_message_text(
            "❌ La programación no existe.",
            reply_markup=crear_menu(),
        )
        return

    nuevo_estado = not bool(
        programacion["activa"]
    )

    db.actualizar_estado(
        programacion_id,
        nuevo_estado,
    )

    if nuevo_estado:
        actualizada = db.obtener_programacion(
            programacion_id
        )

        if actualizada is not None:
            registrar_job(
                context.application,
                actualizada,
                primera_ejecucion=5,
            )
    else:
        eliminar_job_de_memoria(
            context.application,
            programacion_id,
        )

    actualizada = db.obtener_programacion(
        programacion_id
    )

    await query.edit_message_text(
        text=(
            "✅ Programación reanudada. "
            "Se revisará en unos segundos."
            if nuevo_estado
            else "⏸ Programación pausada."
        ),
        reply_markup=menu_detalle(
            actualizada
            or programacion
        ),
    )


async def cambiar_notificacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    programacion_id = int(
        (query.data or "").rsplit(
            ":",
            1,
        )[1]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(programacion["chat_id"])
            != query.message.chat.id
    ):
        await query.edit_message_text(
            "❌ La programación no existe.",
            reply_markup=crear_menu(),
        )
        return

    nuevo_estado = not (
        notificacion_sonora_activa(
            programacion
        )
    )

    db.actualizar_notificacion_sonora(
        programacion_id,
        nuevo_estado,
    )

    actualizada = db.obtener_programacion(
        programacion_id
    ) or programacion

    aviso = (
        "🔔 La alerta sonora quedó activada. "
        "Solo sonará cuando una ejecución automática "
        "encuentre historias nuevas."
        if nuevo_estado
        else
        "🔕 La alerta sonora quedó desactivada. "
        "Las historias nuevas seguirán llegando, pero "
        "sin sonido."
    )

    await query.edit_message_text(
        text=texto_detalle_programacion(
            actualizada,
            aviso=aviso,
        ),
        parse_mode="HTML",
        reply_markup=menu_detalle(
            actualizada
        ),
    )


async def probar_alerta(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Envía una notificación sonora de prueba sin modificar el estado
    guardado de la programación.

    Hay una espera breve para que el usuario pueda minimizar Telegram
    y comprobar la notificación como aparecería en segundo plano.
    """

    query = update.callback_query

    if query is None:
        return

    if not usuario_autorizado(update):
        await query.answer()
        return

    programacion_id = int(
        (query.data or "").rsplit(
            ":",
            1,
        )[1]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(programacion["chat_id"])
            != query.message.chat.id
    ):
        await query.answer(
            "La programación ya no existe.",
            show_alert=True,
        )
        return

    await query.answer(
        "La alerta llegará en 5 segundos. "
        "Minimiza Telegram para probarla.",
        show_alert=True,
    )

    await asyncio.sleep(5)

    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=(
            "🧪 PRUEBA DE ALERTA SONORA\n\n"
            f"Perfil programado: @{programacion['username']}\n"
            "Esta prueba no cambia la configuración guardada."
        ),
        disable_notification=False,
    )


async def mostrar_cambio_intervalo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    programacion_id = int(
        (
            query.data or ""
        ).rsplit(
            ":",
            1,
        )[1]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(
            programacion["chat_id"]
        ) != query.message.chat.id
    ):
        await query.edit_message_text(
            "❌ La programación no existe.",
            reply_markup=crear_menu(),
        )
        return

    await query.edit_message_text(
        text=(
            f"⏱ Selecciona una nueva frecuencia "
            f"para <b>@{programacion['username']}</b>:"
        ),
        parse_mode="HTML",
        reply_markup=menu_intervalos_existente(
            programacion_id
        ),
    )


async def cambiar_intervalo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    partes = (
        query.data or ""
    ).split(":")

    if len(partes) != 4:
        return

    programacion_id = int(
        partes[2]
    )

    intervalo_minutos = int(
        partes[3]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(
            programacion["chat_id"]
        ) != query.message.chat.id
    ):
        await query.edit_message_text(
            "❌ La programación no existe.",
            reply_markup=crear_menu(),
        )
        return

    db.actualizar_intervalo(
        programacion_id,
        intervalo_minutos,
    )

    actualizada = db.obtener_programacion(
        programacion_id
    )

    if actualizada is None:
        raise RuntimeError(
            "No se pudo actualizar "
            "la programación."
        )

    registrar_job(
        context.application,
        actualizada,
        primera_ejecucion=5,
    )

    await query.edit_message_text(
        text=(
            f"✅ Frecuencia actualizada: "
            f"cada {texto_intervalo(intervalo_minutos)}.\n"
            "La próxima revisión comenzará "
            "en unos segundos."
        ),
        reply_markup=menu_detalle(
            actualizada
        ),
    )


async def eliminar_programacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not usuario_autorizado(update):
        return

    programacion_id = int(
        (
            query.data or ""
        ).rsplit(
            ":",
            1,
        )[1]
    )

    programacion = db.obtener_programacion(
        programacion_id
    )

    if (
        programacion is None
        or int(
            programacion["chat_id"]
        ) != query.message.chat.id
    ):
        await query.edit_message_text(
            "❌ La programación no existe.",
            reply_markup=crear_menu(),
        )
        return

    eliminar_job_de_memoria(
        context.application,
        programacion_id,
    )

    db.eliminar_programacion(
        programacion_id
    )

    await query.edit_message_text(
        text=(
            f"🗑 Programación de "
            f"@{programacion['username']} "
            "eliminada."
        ),
        reply_markup=crear_menu(),
    )


# ============================================================
# INICIO, RESTAURACIÓN Y ERRORES
# ============================================================

async def configurar_bot(
    application: Application,
) -> None:
    """
    Configura los comandos y restaura las programaciones
    guardadas después de reiniciar el proceso o el VPS.
    """

    db.inicializar()

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Abrir el menú",
            ),
            BotCommand(
                "menu",
                "Mostrar las cuentas",
            ),
            BotCommand(
                "programaciones",
                "Ver programaciones",
            ),
        ]
    )

    ahora = datetime.now(
        timezone.utc
    )

    programaciones = (
        db.listar_programaciones_activas()
    )

    for programacion in programaciones:
        primera_ejecucion: (
            int | datetime
        ) = 5

        proxima_iso = programacion.get(
            "proxima_ejecucion"
        )

        if proxima_iso:
            try:
                proxima = datetime.fromisoformat(
                    str(proxima_iso)
                )

                if proxima.tzinfo is None:
                    proxima = proxima.replace(
                        tzinfo=timezone.utc
                    )

                if proxima > ahora:
                    primera_ejecucion = proxima

            except ValueError:
                pass

        registrar_job(
            application,
            programacion,
            primera_ejecucion=primera_ejecucion,
        )

    logger.info(
        "Programaciones restauradas: %s",
        len(programaciones),
    )


async def comando_programaciones(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not usuario_autorizado(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ No estás autorizado."
            )
        return

    programaciones = db.listar_programaciones(
        update.effective_chat.id
    )

    if not programaciones:
        await update.effective_message.reply_text(
            text=(
                "📋 No tienes programaciones "
                "configuradas."
            ),
            reply_markup=crear_menu(),
        )
        return

    filas = []

    for programacion in programaciones:
        estado = (
            "✅"
            if programacion["activa"]
            else "⏸"
        )

        filas.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{estado} "
                        f"{'🔔' if notificacion_sonora_activa(programacion) else '🔕'} "
                        f"@{programacion['username']} "
                        f"· {texto_intervalo(int(programacion['intervalo_minutos']))}"
                    ),
                    callback_data=(
                        f"programar:detalle:"
                        f"{programacion['id']}"
                    ),
                )
            ]
        )

    filas.append(
        [
            InlineKeyboardButton(
                text="‹ Menú principal",
                callback_data="actualizar_menu",
            )
        ]
    )

    await update.effective_message.reply_text(
        text="📋 Selecciona una programación:",
        reply_markup=InlineKeyboardMarkup(
            filas
        ),
    )


async def manejar_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception(
        "Error no controlado",
        exc_info=context.error,
    )


def main() -> None:
    validar_configuracion_bot()
    db.inicializar()

    info_db = db.diagnostico()
    logger.info(
        "SQLite: %s | tablas: %s",
        info_db["ruta"],
        ", ".join(info_db["tablas"]),
    )

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(configurar_bot)
        .build()
    )

    if application.job_queue is None:
        raise RuntimeError(
            "Falta JobQueue.\n\n"
            "Instálalo ejecutando:\n"
            "python -m pip install "
            "\"python-telegram-bot[job-queue]==22.8\""
        )

    application.add_handler(
        CommandHandler(
            ["start", "menu"],
            mostrar_menu,
        )
    )

    application.add_handler(
        CommandHandler(
            "programaciones",
            comando_programaciones,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            actualizar_menu,
            pattern=r"^actualizar_menu$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            mostrar_menu_publicaciones,
            pattern=r"^publicaciones:menu$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            descargar_publicaciones_perfil,
            pattern=r"^publicaciones:cuenta:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            enviar_historias,
            pattern=r"^historia:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            iniciar_programacion,
            pattern=r"^programar:inicio$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            elegir_cuenta_programacion,
            pattern=r"^programar:cuenta:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            crear_programacion,
            pattern=r"^programar:crear:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            listar_programaciones,
            pattern=r"^programar:listar$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            mostrar_detalle,
            pattern=r"^programar:detalle:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ejecutar_ahora,
            pattern=r"^programar:ahora:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cambiar_estado,
            pattern=r"^programar:estado:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cambiar_notificacion,
            pattern=r"^programar:notif:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            probar_alerta,
            pattern=r"^programar:probar_alerta:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            mostrar_cambio_intervalo,
            pattern=r"^programar:cambiar:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cambiar_intervalo,
            pattern=r"^programar:intervalo:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            eliminar_programacion,
            pattern=r"^programar:eliminar:",
        )
    )

    application.add_error_handler(
        manejar_error
    )

    print("=" * 60)
    print("STORYPULSE BOT INICIADO")
    print("PROGRAMACIONES AUTOMÁTICAS ACTIVAS")
    print("DEDUPLICACIÓN DE IMÁGENES ACTIVADA")
    print("=" * 60)

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
