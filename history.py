# history.py

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError
from playwright.sync_api import Page, sync_playwright

from config import MOSTRAR_NAVEGADOR


# ============================================================
# CONFIGURACIÓN DEL NAVEGADOR
# ============================================================

# SHOW_BROWSER se configura en .env.

# Espera inicial para permitir que el sitio cargue el perfil.
ESPERA_INICIAL_MS = 5_000

# Tiempo total máximo destinado a esperar que aparezcan
# todas las miniaturas de historias.
ESPERA_MAXIMA_HISTORIAS_MS = 25_000

# Cada cuánto se vuelve a contar el contenido cargado.
INTERVALO_COMPROBACION_MS = 1_000

# El número de historias debe permanecer igual esta cantidad
# de comprobaciones antes de considerar que terminó de cargar.
COMPROBACIONES_ESTABLES_REQUERIDAS = 2


# Carpeta donde se guardan las imágenes si history.py
# se ejecuta directamente desde la consola.
CARPETA_DESCARGAS = Path("historias_descargadas")


URL_PERFIL = "https://insta-stories-viewer.com/es/{username}/"


# Dimensiones mínimas para no confundir la historia
# con el avatar, iconos o logotipos.
ANCHO_MINIMO = 160
ALTO_MINIMO = 280

# Algunas miniaturas comprimidas pueden pesar menos de 15 KB.
# El formato y las dimensiones se validan después con Pillow.
BYTES_MINIMOS_IMAGEN = 3_000


@dataclass
class ImagenDescargada:
    url: str
    contenido: bytes
    content_type: str
    extension: str
    ancho: int
    alto: int
    orden: int
    prioridad: int
    puntuacion: float
    hash_archivo: str


def limpiar_username(valor: str) -> str:
    username = valor.strip().lstrip("@")

    if not username:
        raise ValueError("El username está vacío.")

    if not re.fullmatch(r"[A-Za-z0-9._]+", username):
        raise ValueError(
            "El username solamente puede contener letras, "
            "números, puntos y guiones bajos."
        )

    return username


def es_url_http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def es_url_video(url: str, resource_type: str = "") -> bool:
    url_decodificada = unquote(url).lower()
    ruta = urlsplit(url_decodificada).path.lower()

    extensiones_video = (
        ".mp4",
        ".webm",
        ".mov",
        ".m4v",
        ".avi",
        ".mkv",
        ".m3u8",
        ".ts",
    )

    indicadores_video = (
        "/video.php",
        "video_url=",
        "videoplayback",
        ".mp4?",
        ".webm?",
        ".m3u8?",
    )

    if resource_type == "media":
        return True

    if ruta.endswith(extensiones_video):
        return True

    return any(
        indicador in url_decodificada
        for indicador in indicadores_video
    )


def es_cdn_del_sitio(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    ruta = urlsplit(url).path.lower()

    dominios_conocidos = (
        "cdn.insta-stories-viewer.com",
        "cdn.iqsaved.com",
    )

    if hostname in dominios_conocidos:
        return True

    return (
        "insta-stories-viewer.com" in hostname
        and (
            "img.php" in ruta
            or "img2.php" in ruta
        )
    )


def content_type_y_extension(
    formato_pillow: str | None,
    content_type_servidor: str,
) -> tuple[str, str]:
    formato = (formato_pillow or "").upper()

    equivalencias = {
        "JPEG": ("image/jpeg", "jpg"),
        "JPG": ("image/jpeg", "jpg"),
        "PNG": ("image/png", "png"),
        "WEBP": ("image/webp", "webp"),
        "GIF": ("image/gif", "gif"),
    }

    if formato in equivalencias:
        return equivalencias[formato]

    content_type = (
        content_type_servidor
        .lower()
        .split(";")[0]
        .strip()
    )

    por_content_type = {
        "image/jpeg": ("image/jpeg", "jpg"),
        "image/jpg": ("image/jpeg", "jpg"),
        "image/png": ("image/png", "png"),
        "image/webp": ("image/webp", "webp"),
        "image/gif": ("image/gif", "gif"),
    }

    return por_content_type.get(
        content_type,
        ("image/jpeg", "jpg"),
    )


# ============================================================
# ESPERA ADAPTATIVA Y LAZY LOADING
# ============================================================

def localizar_y_mostrar_stories(page: Page) -> None:
    """
    Desplaza la pestaña Stories al centro de la ventana.
    Si no puede localizarla por texto, realiza un desplazamiento
    aproximado para activar el contenido dinámico.
    """

    try:
        stories = page.get_by_text(
            "Stories",
            exact=True,
        ).first

        stories.scroll_into_view_if_needed(
            timeout=10_000
        )

        page.wait_for_timeout(1_000)

    except Exception:
        page.evaluate(
            """
            () => {
                const altura = Math.max(
                    document.body.scrollHeight,
                    document.documentElement.scrollHeight
                );

                window.scrollTo({
                    top: Math.min(altura * 0.35, 1200),
                    behavior: "instant"
                });
            }
            """
        )

        page.wait_for_timeout(1_000)


def estado_carga_historias(page: Page) -> dict:
    """
    Cuenta las posibles miniaturas de historias que ya existen
    en el DOM y devuelve sus URL.

    Se consideran:
    - img/currentSrc/src;
    - picture/source/srcset;
    - poster;
    - data-src y atributos equivalentes;
    - background-image;
    - elementos verticales cercanos a la pestaña Stories.
    """

    return page.evaluate(
        """
        () => {
            function normalizar(valor) {
                if (!valor || typeof valor !== "string") {
                    return null;
                }

                valor = valor.trim();

                if (
                    valor.startsWith("data:") ||
                    valor.startsWith("blob:") ||
                    valor.startsWith("javascript:")
                ) {
                    return null;
                }

                try {
                    return new URL(valor, location.href).href;
                } catch {
                    return null;
                }
            }

            function agregar(urls, valor) {
                const url = normalizar(valor);

                if (url) {
                    urls.add(url);
                }
            }

            function agregarSrcset(urls, srcset) {
                if (!srcset) {
                    return;
                }

                for (const parte of srcset.split(",")) {
                    agregar(
                        urls,
                        parte.trim().split(/\\s+/)[0]
                    );
                }
            }

            function esVisible(elemento, rect) {
                const estilo = getComputedStyle(elemento);

                return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    estilo.display !== "none" &&
                    estilo.visibility !== "hidden" &&
                    Number(estilo.opacity || 1) > 0
                );
            }

            const todos = Array.from(
                document.querySelectorAll("body *")
            );

            const etiquetasStories = todos.filter(elemento => {
                const texto =
                    (elemento.textContent || "").trim();

                const rect =
                    elemento.getBoundingClientRect();

                return (
                    texto === "Stories" &&
                    rect.width >= 20 &&
                    rect.width <= 280 &&
                    rect.height >= 10 &&
                    rect.height <= 100
                );
            });

            etiquetasStories.sort((a, b) => {
                const rectA = a.getBoundingClientRect();
                const rectB = b.getBoundingClientRect();

                return (
                    rectA.width * rectA.height
                    - rectB.width * rectB.height
                );
            });

            const tabStories =
                etiquetasStories.length > 0
                    ? etiquetasStories[0]
                    : null;

            const limiteSuperior = tabStories
                ? tabStories.getBoundingClientRect().bottom - 100
                : 120;

            const limiteInferior = limiteSuperior + 2500;

            const urls = new Set();
            let elementosVerticales = 0;
            let imagenesVisibles = 0;

            const elementos = Array.from(
                document.querySelectorAll(
                    [
                        "img",
                        "picture source",
                        "video",
                        "[data-src]",
                        "[data-original]",
                        "[data-lazy-src]",
                        "[data-image]",
                        "[data-url]",
                        "[data-poster]"
                    ].join(",")
                )
            );

            for (const elemento of elementos) {
                let visual = elemento;

                if (elemento.tagName === "SOURCE") {
                    visual =
                        elemento.closest("picture")
                            ?.querySelector("img")
                        || elemento.parentElement
                        || elemento;
                }

                const rect =
                    visual.getBoundingClientRect();

                const anchoNatural =
                    visual.naturalWidth
                    || visual.videoWidth
                    || 0;

                const altoNatural =
                    visual.naturalHeight
                    || visual.videoHeight
                    || 0;

                const verticalVisual =
                    rect.width >= 100 &&
                    rect.height >= 170 &&
                    rect.height > rect.width * 1.15;

                const verticalNatural =
                    anchoNatural >= 200 &&
                    altoNatural >= 350 &&
                    altoNatural > anchoNatural * 1.15;

                const cercaDeStories =
                    rect.top >= limiteSuperior &&
                    rect.top <= limiteInferior;

                const candidato =
                    cercaDeStories &&
                    (
                        verticalVisual
                        || verticalNatural
                    );

                if (!candidato) {
                    continue;
                }

                elementosVerticales += 1;

                if (esVisible(visual, rect)) {
                    imagenesVisibles += 1;
                }

                agregar(urls, elemento.currentSrc);
                agregar(urls, elemento.src);
                agregar(urls, elemento.poster);

                const atributos = [
                    "src",
                    "poster",
                    "data-src",
                    "data-original",
                    "data-lazy-src",
                    "data-image",
                    "data-url",
                    "data-poster"
                ];

                for (const atributo of atributos) {
                    agregar(
                        urls,
                        elemento.getAttribute(atributo)
                    );
                }

                agregarSrcset(
                    urls,
                    elemento.srcset
                );

                agregarSrcset(
                    urls,
                    elemento.getAttribute("srcset")
                );

                agregarSrcset(
                    urls,
                    elemento.getAttribute("data-srcset")
                );

                const estilo =
                    getComputedStyle(visual);

                const fondo =
                    estilo.backgroundImage || "";

                const expresion =
                    /url\\(["']?(.*?)["']?\\)/g;

                let coincidencia;

                while (
                    (
                        coincidencia =
                        expresion.exec(fondo)
                    ) !== null
                ) {
                    agregar(
                        urls,
                        coincidencia[1]
                    );
                }
            }

            return {
                cantidad_urls: urls.size,
                elementos_verticales: elementosVerticales,
                imagenes_visibles: imagenesVisibles,
                urls: Array.from(urls),
                tiene_tab_stories: Boolean(tabStories)
            };
        }
        """
    )


def activar_lazy_loading(page: Page, iteracion: int) -> None:
    """
    Activa imágenes diferidas mediante desplazamiento vertical
    y también desplaza posibles carruseles horizontales.
    """

    direccion = 1 if iteracion % 2 == 0 else -1

    page.evaluate(
        """
        ({ direccion, iteracion }) => {
            const desplazamientoVertical =
                direccion > 0 ? 320 : -160;

            window.scrollBy({
                top: desplazamientoVertical,
                behavior: "instant"
            });

            const elementos = Array.from(
                document.querySelectorAll("body *")
            );

            for (const elemento of elementos) {
                const estilo = getComputedStyle(elemento);
                const rect = elemento.getBoundingClientRect();

                const desplazableHorizontal =
                    elemento.scrollWidth >
                        elemento.clientWidth + 40
                    && elemento.clientWidth >= 150
                    && rect.top >= 100
                    && rect.top <= window.innerHeight + 1400
                    && (
                        estilo.overflowX === "auto"
                        || estilo.overflowX === "scroll"
                        || elemento.scrollWidth >
                            elemento.clientWidth * 1.25
                    );

                if (!desplazableHorizontal) {
                    continue;
                }

                if (iteracion % 3 === 0) {
                    elemento.scrollLeft =
                        elemento.scrollWidth;
                } else {
                    elemento.scrollLeft = Math.min(
                        elemento.scrollLeft
                            + elemento.clientWidth * 0.80,
                        elemento.scrollWidth
                    );
                }
            }

            // Fuerza al navegador a recalcular layout.
            void document.body.offsetHeight;
        }
        """,
        {
            "direccion": direccion,
            "iteracion": iteracion,
        },
    )


def esperar_carga_completa_historias(
    page: Page,
) -> dict:
    """
    Espera hasta que la cantidad de miniaturas deje de crecer.

    No espera siempre el tiempo máximo:
    termina antes cuando la cantidad permanece estable durante
    varias comprobaciones consecutivas.
    """

    print(
        "Esperando la carga inicial del perfil: "
        f"{ESPERA_INICIAL_MS // 1000} segundos..."
    )

    page.wait_for_timeout(
        ESPERA_INICIAL_MS
    )

    localizar_y_mostrar_stories(page)

    inicio = time.monotonic()
    limite_segundos = (
        ESPERA_MAXIMA_HISTORIAS_MS / 1000
    )

    mejor_estado = estado_carga_historias(
        page
    )

    maximo_detectado = max(
        int(mejor_estado["cantidad_urls"]),
        int(mejor_estado["elementos_verticales"]),
    )

    cantidad_anterior = -1
    estables = 0
    iteracion = 0

    while (
        time.monotonic() - inicio
        < limite_segundos
    ):
        activar_lazy_loading(
            page,
            iteracion,
        )

        page.wait_for_timeout(
            INTERVALO_COMPROBACION_MS
        )

        estado = estado_carga_historias(
            page
        )

        cantidad_actual = max(
            int(estado["cantidad_urls"]),
            int(estado["elementos_verticales"]),
        )

        transcurrido = int(
            time.monotonic() - inicio
        )

        print(
            "Carga de Stories: "
            f"{cantidad_actual} candidato(s), "
            f"{estado['imagenes_visibles']} visible(s), "
            f"{transcurrido}s"
        )

        if cantidad_actual > maximo_detectado:
            maximo_detectado = cantidad_actual
            mejor_estado = estado

        if (
            cantidad_actual > 0
            and cantidad_actual == cantidad_anterior
        ):
            estables += 1
        else:
            estables = 0

        cantidad_anterior = cantidad_actual
        iteracion += 1

        if (
            cantidad_actual > 0
            and estables
            >= COMPROBACIONES_ESTABLES_REQUERIDAS
        ):
            print(
                "La cantidad de historias quedó estable. "
                "Se continúa con la descarga."
            )
            break

    else:
        print(
            "Se alcanzó el tiempo máximo de espera. "
            "Se utilizará todo lo detectado hasta ahora."
        )

    # Regresa nuevamente a la sección y concede un pequeño
    # margen final para respuestas de red pendientes.
    localizar_y_mostrar_stories(page)
    page.wait_for_timeout(2_000)

    estado_final = estado_carga_historias(
        page
    )

    cantidad_final = max(
        int(estado_final["cantidad_urls"]),
        int(estado_final["elementos_verticales"]),
    )

    if cantidad_final >= maximo_detectado:
        mejor_estado = estado_final

    print(
        "Carga finalizada. Candidatos detectados: "
        f"{max(maximo_detectado, cantidad_final)}"
    )

    return mejor_estado


# ============================================================
# EXTRACCIÓN DE URL
# ============================================================

def extraer_urls_del_dom(page: Page) -> list[dict]:
    """
    Busca URL de imágenes en:

    - img src/currentSrc;
    - srcset;
    - picture/source;
    - poster de video;
    - data-src y atributos similares;
    - background-image.

    Asigna mayor prioridad a imágenes verticales ubicadas
    debajo de la sección Stories.
    """

    return page.locator("body").evaluate(
        """
        () => {
            const resultados = [];
            let contador = 0;

            function esVisible(elemento, rect) {
                const estilo = getComputedStyle(elemento);

                return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    estilo.display !== "none" &&
                    estilo.visibility !== "hidden" &&
                    Number(estilo.opacity || 1) > 0
                );
            }

            function normalizarUrl(valor) {
                if (!valor || typeof valor !== "string") {
                    return null;
                }

                valor = valor.trim();

                if (
                    valor.startsWith("data:") ||
                    valor.startsWith("blob:") ||
                    valor.startsWith("javascript:")
                ) {
                    return null;
                }

                try {
                    return new URL(valor, location.href).href;
                } catch {
                    return null;
                }
            }

            const elementosPagina = Array.from(
                document.querySelectorAll("body *")
            );

            const etiquetasStories =
                elementosPagina.filter(elemento => {
                    const texto =
                        (elemento.textContent || "").trim();

                    const rect =
                        elemento.getBoundingClientRect();

                    return (
                        texto === "Stories" &&
                        rect.width >= 20 &&
                        rect.width <= 250 &&
                        rect.height >= 10 &&
                        rect.height <= 100 &&
                        rect.bottom > -500
                    );
                });

            etiquetasStories.sort((a, b) => {
                const rectA = a.getBoundingClientRect();
                const rectB = b.getBoundingClientRect();

                return (
                    rectA.width * rectA.height
                    - rectB.width * rectB.height
                );
            });

            const etiquetaStories =
                etiquetasStories.length > 0
                    ? etiquetasStories[0]
                    : null;

            const limiteStories = etiquetaStories
                ? etiquetaStories.getBoundingClientRect().bottom
                : 0;

            function agregar(
                valor,
                elemento,
                tipo,
                ordenElemento
            ) {
                const url = normalizarUrl(valor);

                if (!url) {
                    return;
                }

                let elementoVisual = elemento;

                if (elemento.tagName === "SOURCE") {
                    elementoVisual =
                        elemento.closest("picture")
                            ?.querySelector("img")
                        || elemento.parentElement
                        || elemento;
                }

                const rect =
                    elementoVisual.getBoundingClientRect();

                const visible =
                    esVisible(elementoVisual, rect);

                const anchoNatural =
                    elementoVisual.naturalWidth
                    || elementoVisual.videoWidth
                    || 0;

                const altoNatural =
                    elementoVisual.naturalHeight
                    || elementoVisual.videoHeight
                    || 0;

                const esVerticalVisual =
                    rect.width >= 100 &&
                    rect.height >= 170 &&
                    rect.height > rect.width * 1.15;

                const esVerticalNatural =
                    anchoNatural >= 200 &&
                    altoNatural >= 350 &&
                    altoNatural >
                        anchoNatural * 1.15;

                const esVertical =
                    esVerticalVisual
                    || esVerticalNatural;

                const debajoDeStories =
                    limiteStories > 0 &&
                    rect.top >= limiteStories - 180 &&
                    rect.top <= limiteStories + 2600;

                let prioridad = 0;

                if (visible) {
                    prioridad += 1;
                }

                if (esVertical) {
                    prioridad += 2;
                }

                if (debajoDeStories) {
                    prioridad += 4;
                }

                if (
                    esVertical
                    && debajoDeStories
                    && visible
                ) {
                    prioridad += 10;
                }

                if (
                    url.includes(
                        "cdn.insta-stories-viewer.com"
                    )
                    || url.includes(
                        "cdn.iqsaved.com"
                    )
                ) {
                    prioridad += 3;
                }

                resultados.push({
                    url,
                    tipo,
                    orden: ordenElemento,
                    prioridad,
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    naturalWidth: anchoNatural,
                    naturalHeight: altoNatural
                });
            }

            /*
             * Se inspecciona todo el DOM porque algunas historias
             * guardan la miniatura en un contenedor padre, en una
             * propiedad CSS calculada o en un atributo no estándar.
             */
            const elementos = Array.from(
                document.querySelectorAll("body *")
            );

            for (const elemento of elementos) {
                const ordenElemento =
                    contador++;

                agregar(
                    elemento.currentSrc,
                    elemento,
                    "currentSrc",
                    ordenElemento
                );

                agregar(
                    elemento.src,
                    elemento,
                    "src",
                    ordenElemento
                );

                agregar(
                    elemento.poster,
                    elemento,
                    "poster",
                    ordenElemento
                );

                const atributos = [
                    "src",
                    "poster",
                    "data-src",
                    "data-original",
                    "data-lazy-src",
                    "data-image",
                    "data-url",
                    "data-poster"
                ];

                for (const atributo of atributos) {
                    agregar(
                        elemento.getAttribute(atributo),
                        elemento,
                        atributo,
                        ordenElemento
                    );
                }

                /*
                 * También se inspeccionan todos los atributos.
                 * Algunos cambios del sitio usan nombres como
                 * data-thumb, data-preview o data-background.
                 */
                for (
                    const atributo
                    of Array.from(elemento.attributes || [])
                ) {
                    const valor = atributo.value || "";

                    agregar(
                        valor,
                        elemento,
                        `atributo:${atributo.name}`,
                        ordenElemento
                    );

                    const coincidenciasHttp = valor.match(
                        /https?:\\/\\/[^\\s"'<>]+/g
                    );

                    if (coincidenciasHttp) {
                        for (const urlEncontrada of coincidenciasHttp) {
                            agregar(
                                urlEncontrada,
                                elemento,
                                `atributo-url:${atributo.name}`,
                                ordenElemento
                            );
                        }
                    }
                }

                const srcsets = [
                    elemento.srcset,
                    elemento.getAttribute("srcset"),
                    elemento.getAttribute("data-srcset")
                ];

                for (const srcset of srcsets) {
                    if (!srcset) {
                        continue;
                    }

                    for (
                        const parte
                        of srcset.split(",")
                    ) {
                        const urlSrcset =
                            parte
                            .trim()
                            .split(/\\s+/)[0];

                        agregar(
                            urlSrcset,
                            elemento,
                            "srcset",
                            ordenElemento
                        );
                    }
                }

                const fondo =
                    getComputedStyle(
                        elemento
                    ).backgroundImage || "";

                const expresion =
                    /url\\(["']?(.*?)["']?\\)/g;

                let coincidencia;

                while (
                    (
                        coincidencia =
                        expresion.exec(fondo)
                    ) !== null
                ) {
                    agregar(
                        coincidencia[1],
                        elemento,
                        "background",
                        ordenElemento
                    );
                }
            }

            return resultados;
        }
        """
    )


def combinar_candidatos(
    urls_dom: list[dict],
    urls_red: dict[str, dict],
) -> dict[str, dict]:
    candidatos_por_url: dict[str, dict] = {}

    for elemento in urls_dom:
        url_imagen = elemento.get(
            "url",
            "",
        )

        if not es_url_http(url_imagen):
            continue

        if es_url_video(url_imagen):
            continue

        existente = candidatos_por_url.get(
            url_imagen
        )

        if (
            existente is None
            or int(
                elemento.get("prioridad", 0)
            ) > int(
                existente.get("prioridad", 0)
            )
        ):
            candidatos_por_url[
                url_imagen
            ] = elemento

    for url_imagen, elemento in urls_red.items():
        if es_url_video(url_imagen):
            continue

        existente = candidatos_por_url.get(
            url_imagen
        )

        if (
            existente is None
            or int(
                elemento.get("prioridad", 0)
            ) > int(
                existente.get("prioridad", 0)
            )
        ):
            candidatos_por_url[
                url_imagen
            ] = elemento

    return candidatos_por_url


# ============================================================
# DESCARGA PRINCIPAL
# ============================================================

def descargar_imagenes(
    username: str,
) -> list[ImagenDescargada]:
    url_pagina = URL_PERFIL.format(
        username=username
    )

    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(
            headless=not MOSTRAR_NAVEGADOR,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=user-gesture-required",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-extensions",
                "--disable-sync",
                "--disable-default-apps",
                "--no-first-run",
                "--mute-audio",
            ],
        )

        contexto = navegador.new_context(
            viewport={
                "width": 1365,
                "height": 900,
            },
            locale="es-ES",
            service_workers="block",
            reduced_motion="reduce",
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/142.0.0.0 "
                "Safari/537.36"
            ),
        )

        pagina = contexto.new_page()

        urls_red: dict[str, dict] = {}
        contador_red = 100_000

        def registrar_respuesta(
            response,
        ) -> None:
            nonlocal contador_red

            try:
                content_type = (
                    response.headers.get(
                        "content-type",
                        "",
                    ).lower()
                )

                if not content_type.startswith(
                    "image/"
                ):
                    return

                url_respuesta = response.url

                if not es_url_http(
                    url_respuesta
                ):
                    return

                if url_respuesta not in urls_red:
                    urls_red[
                        url_respuesta
                    ] = {
                        "url": url_respuesta,
                        "orden": contador_red,
                        "prioridad": (
                            3
                            if es_cdn_del_sitio(
                                url_respuesta
                            )
                            else 0
                        ),
                        "tipo": "respuesta_red",
                    }

                    contador_red += 1

            except Exception:
                pass

        pagina.on(
            "response",
            registrar_respuesta,
        )

        def controlar_peticion(
            route,
            request,
        ) -> None:
            if es_url_video(
                request.url,
                request.resource_type,
            ) or request.resource_type == "font":
                route.abort()
                return

            route.continue_()

        pagina.route(
            "**/*",
            controlar_peticion,
        )

        try:
            print(f"Abriendo: {url_pagina}")

            pagina.goto(
                url_pagina,
                wait_until="domcontentloaded",
                timeout=120_000,
            )

            # Nueva espera adaptativa. Continúa antes si la
            # cantidad de historias ya dejó de aumentar.
            estado_espera = esperar_carga_completa_historias(
                pagina
            )

            historias_esperadas = max(
                int(estado_espera.get("cantidad_urls", 0)),
                int(estado_espera.get("elementos_verticales", 0)),
            )

            print(
                "Historias esperadas según el DOM:",
                historias_esperadas,
            )

            # Primera extracción completa.
            urls_dom = extraer_urls_del_dom(
                pagina
            )

            candidatos_por_url = combinar_candidatos(
                urls_dom,
                urls_red,
            )

            print(
                "URL de imágenes detectadas "
                "después de la espera:",
                len(candidatos_por_url),
            )

            # Margen y segunda extracción:
            # recoge cualquier respuesta que haya llegado justo
            # después de la primera lectura.
            activar_lazy_loading(
                pagina,
                99,
            )

            pagina.wait_for_timeout(
                1_200
            )

            urls_dom_segunda = (
                extraer_urls_del_dom(
                    pagina
                )
            )

            candidatos_segunda = (
                combinar_candidatos(
                    urls_dom_segunda,
                    urls_red,
                )
            )

            candidatos_por_url.update(
                candidatos_segunda
            )

            print(
                "URL totales tras la segunda pasada:",
                len(candidatos_por_url),
            )

            # Una activación final suele ser suficiente después
            # de que el contador se estabilizó. Evita 7,5 segundos
            # fijos en cada consulta.
            activar_lazy_loading(
                pagina,
                200,
            )
            pagina.wait_for_timeout(1_200)

            urls_dom_tercera = extraer_urls_del_dom(
                pagina
            )

            candidatos_tercera = combinar_candidatos(
                urls_dom_tercera,
                urls_red,
            )

            candidatos_por_url.update(
                candidatos_tercera
            )

            print(
                "URL totales tras la tercera pasada:",
                len(candidatos_por_url),
            )

            descargadas: list[
                ImagenDescargada
            ] = []

            for numero, elemento in enumerate(
                candidatos_por_url.values(),
                start=1,
            ):
                url_imagen = elemento["url"]

                prioridad = int(
                    elemento.get(
                        "prioridad",
                        0,
                    )
                )

                orden = int(
                    elemento.get(
                        "orden",
                        100_000,
                    )
                )

                # Solo se analizan recursos del CDN del visor
                # o imágenes identificadas cerca de Stories.
                if (
                    not es_cdn_del_sitio(
                        url_imagen
                    )
                    and prioridad < 6
                ):
                    print(
                        f"[{numero}] Omitida por prioridad baja "
                        f"({prioridad}): {url_imagen[:100]}"
                    )
                    continue

                try:
                    respuesta = (
                        contexto.request.get(
                            url_imagen,
                            headers={
                                "Referer": url_pagina,
                                "Accept": (
                                    "image/avif,"
                                    "image/webp,"
                                    "image/png,"
                                    "image/jpeg,"
                                    "image/*,"
                                    "*/*;q=0.8"
                                ),
                            },
                            timeout=60_000,
                            fail_on_status_code=False,
                        )
                    )

                    try:
                        if not respuesta.ok:
                            print(
                                f"[{numero}] HTTP "
                                f"{respuesta.status}: "
                                f"{url_imagen[:90]}"
                            )
                            continue

                        contenido = respuesta.body()

                        if len(contenido) < BYTES_MINIMOS_IMAGEN:
                            print(
                                f"[{numero}] Rechazada: archivo demasiado "
                                f"pequeño ({len(contenido)} bytes)."
                            )
                            continue

                        content_type_servidor = (
                            respuesta.headers.get(
                                "content-type",
                                "",
                            )
                        )

                    finally:
                        respuesta.dispose()

                    try:
                        with Image.open(
                            BytesIO(contenido)
                        ) as imagen:
                            imagen.load()

                            ancho, alto = (
                                imagen.size
                            )

                            formato = (
                                imagen.format
                            )

                    except (
                        UnidentifiedImageError,
                        OSError,
                        ValueError,
                    ) as error_imagen:
                        print(
                            f"[{numero}] Rechazada: Pillow no pudo "
                            f"abrirla ({error_imagen})."
                        )
                        continue

                    if ancho < ANCHO_MINIMO:
                        print(
                            f"[{numero}] Rechazada: ancho {ancho} "
                            f"menor que {ANCHO_MINIMO}."
                        )
                        continue

                    if alto < ALTO_MINIMO:
                        print(
                            f"[{numero}] Rechazada: alto {alto} "
                            f"menor que {ALTO_MINIMO}."
                        )
                        continue

                    proporcion = alto / ancho

                    # Historias verticales:
                    # aproximadamente 9:16.
                    if proporcion < 1.20:
                        print(
                            f"[{numero}] Rechazada: proporción "
                            f"{proporcion:.2f} demasiado horizontal."
                        )
                        continue

                    if proporcion > 2.60:
                        print(
                            f"[{numero}] Rechazada: proporción "
                            f"{proporcion:.2f} demasiado estrecha."
                        )
                        continue

                    (
                        content_type,
                        extension,
                    ) = content_type_y_extension(
                        formato,
                        content_type_servidor,
                    )

                    area = ancho * alto

                    distancia_9_16 = abs(
                        proporcion - (16 / 9)
                    )

                    puntuacion = (
                        prioridad * 1_000_000
                        + area
                        - distancia_9_16
                        * 100_000
                    )

                    hash_archivo = (
                        hashlib.sha256(
                            contenido
                        ).hexdigest()
                    )

                    descargadas.append(
                        ImagenDescargada(
                            url=url_imagen,
                            contenido=contenido,
                            content_type=content_type,
                            extension=extension,
                            ancho=ancho,
                            alto=alto,
                            orden=orden,
                            prioridad=prioridad,
                            puntuacion=puntuacion,
                            hash_archivo=hash_archivo,
                        )
                    )

                    print(
                        f"[{numero}] Candidata: "
                        f"{ancho}x{alto} - "
                        f"{len(contenido) // 1024} KB - "
                        f"prioridad {prioridad}"
                    )

                except Exception as error:
                    print(
                        f"[{numero}] "
                        "No se pudo analizar: "
                        f"{error}"
                    )

            if not descargadas:
                debug_html = Path(
                    f"debug_{username}.html"
                )

                debug_html.write_text(
                    pagina.content(),
                    encoding="utf-8",
                )

                raise RuntimeError(
                    "No se encontró ninguna imagen "
                    "vertical válida. "
                    f"Se guardó {debug_html} "
                    "para diagnóstico."
                )

            prioridad_maxima = max(
                imagen.prioridad
                for imagen in descargadas
            )

            # No se descartan imágenes solo por tener una prioridad
            # inferior a la máxima. Una historia cargada fuera del
            # área visible puede tener prioridad menor y seguir siendo
            # una miniatura válida.

            # Elimina archivos exactamente duplicados.
            sin_duplicados: list[
                ImagenDescargada
            ] = []

            hashes_vistos: set[str] = set()

            for imagen in sorted(
                descargadas,
                key=lambda elemento: (
                    elemento.orden,
                    -elemento.puntuacion,
                ),
            ):
                if (
                    imagen.hash_archivo
                    in hashes_vistos
                ):
                    continue

                hashes_vistos.add(
                    imagen.hash_archivo
                )

                sin_duplicados.append(
                    imagen
                )

            # La deduplicación por hash ya eliminó copias exactas.
            # No se agrupa por "orden" porque dos historias distintas
            # pueden quedar asociadas al mismo contenedor del carrusel.
            resultado = sorted(
                sin_duplicados,
                key=lambda elemento: (
                    elemento.orden,
                    -elemento.puntuacion,
                ),
            )

            if prioridad_maxima < 10:
                resultado.sort(
                    key=lambda elemento:
                        elemento.puntuacion,
                    reverse=True,
                )

            # Límite preventivo, muy superior a la cantidad
            # normal de historias de una cuenta.
            resultado = resultado[:30]

            print(
                "Imágenes finales seleccionadas:",
                len(resultado),
            )

            if (
                historias_esperadas > 0
                and len(resultado) < historias_esperadas
            ):
                print(
                    "ADVERTENCIA: el DOM mostró "
                    f"{historias_esperadas} historia(s), pero solo "
                    f"{len(resultado)} recurso(s) de imagen pasaron "
                    "la validación. Revisa los mensajes 'Rechazada' "
                    "anteriores para identificar el motivo."
                )

            return resultado

        finally:
            contexto.close()
            navegador.close()


# ============================================================
# EJECUCIÓN DIRECTA DESDE CONSOLA
# ============================================================

def guardar_imagenes(
    username: str,
    imagenes: list[ImagenDescargada],
) -> list[Path]:
    fecha = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    carpeta_usuario = (
        CARPETA_DESCARGAS
        / username
        / fecha
    )

    carpeta_usuario.mkdir(
        parents=True,
        exist_ok=True,
    )

    archivos: list[Path] = []

    for indice, imagen in enumerate(
        imagenes,
        start=1,
    ):
        nombre = (
            f"historia_{indice:02d}_"
            f"{imagen.ancho}x{imagen.alto}."
            f"{imagen.extension}"
        )

        ruta = carpeta_usuario / nombre

        ruta.write_bytes(
            imagen.contenido
        )

        archivos.append(ruta)

        print(
            f"Guardada: {ruta.resolve()}"
        )

    return archivos


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Descarga imágenes de vista previa "
            "de historias públicas."
        )
    )

    parser.add_argument(
        "username",
        help=(
            "Username de Instagram, "
            "con o sin @."
        ),
    )

    argumentos = parser.parse_args()

    try:
        username = limpiar_username(
            argumentos.username
        )

        print("=" * 60)
        print(f"Usuario: @{username}")
        print("=" * 60)

        imagenes = descargar_imagenes(
            username
        )

        archivos = guardar_imagenes(
            username,
            imagenes,
        )

        print("=" * 60)
        print(
            f"Finalizado. Se guardaron "
            f"{len(archivos)} imágenes."
        )
        print("=" * 60)

    except KeyboardInterrupt:
        print("\nProceso cancelado.")
        raise SystemExit(130)

    except Exception as error:
        print(f"\nERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
