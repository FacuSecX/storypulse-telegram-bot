# publicaciones.py
#
# SSSInstagram V6.2 — publicaciones rápidas y filtradas por tarjeta.
#
# Flujo:
# 1. Abre https://sssinstagram.com/es
# 2. Escribe el username en el formulario principal.
# 3. Pulsa Descargar.
# 4. Espera el perfil y la pestaña POSTS.
# 5. Recorre únicamente tarjetas que contienen imagen + botón Descargar.
# 6. Hace scroll rápido hasta alcanzar el total declarado o quedar estable.
# 7. Descarga varias imágenes en paralelo y las normaliza para Telegram.
#
# Este módulo no usa SQLite ni conserva historial de publicaciones.

from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from config import MOSTRAR_NAVEGADOR


URL_BUSCADOR = "https://sssinstagram.com/es"

# SHOW_BROWSER se configura en .env.

# Esperas y límites.
ESPERA_DESPUES_FORMULARIO_MS = 1_200
ESPERA_ENTRE_SCROLL_MS = 850
TIEMPO_MAXIMO_SCROLL_MS = 75_000
SEGUNDOS_ESTABLE_AL_FINAL = 5
RONDAS_ESTABLES_REQUERIDAS = 3
MAXIMO_TARJETAS = 1_000

# Descarga paralela. Cuatro mantiene buena velocidad sin saturar el sitio.
DESCARGAS_SIMULTANEAS = 4
TIMEOUT_CONEXION_SEGUNDOS = 20
TIMEOUT_LECTURA_SEGUNDOS = 60

# Validación de imágenes.
ANCHO_MINIMO = 240
ALTO_MINIMO = 240
BYTES_MINIMOS = 5_000
MAXIMO_BYTES_TELEGRAM = 9_500_000
MAXIMA_SUMA_DIMENSIONES = 9_500


@dataclass
class RecursoPublicacion:
    url: str
    orden: float
    contenido: bytes
    extension: str
    ancho: int
    alto: int
    hash_archivo: str


@dataclass
class ResultadoPublicaciones:
    username: str
    recursos: list[RecursoPublicacion]

    @property
    def cantidad_imagenes(self) -> int:
        return len(self.recursos)

    @property
    def tamano_total_bytes(self) -> int:
        return sum(
            len(recurso.contenido)
            for recurso in self.recursos
        )


ProgressCallback = Callable[[str], None] | None


def informar_progreso(
    callback: ProgressCallback,
    mensaje: str,
) -> None:
    if callback is None:
        return

    try:
        callback(mensaje)
    except Exception:
        # Un fallo editando el mensaje de progreso no debe cancelar
        # la descarga principal.
        pass


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


def es_video(url: str, resource_type: str = "") -> bool:
    valor = unquote(url).lower()
    ruta = urlsplit(valor).path.lower()

    extensiones = (
        ".mp4",
        ".webm",
        ".mov",
        ".m4v",
        ".avi",
        ".mkv",
        ".m3u8",
        ".ts",
    )

    if resource_type == "media":
        return True

    return (
        ruta.endswith(extensiones)
        or any(f"{extension}?" in valor for extension in extensiones)
        or "videoplayback" in valor
    )


def obtener_elemento_visible(
    page: Page,
    selectores: list[str],
):
    for selector in selectores:
        localizador = page.locator(selector)

        try:
            cantidad = localizador.count()
        except Exception:
            continue

        for indice in range(cantidad):
            elemento = localizador.nth(indice)

            try:
                if elemento.is_visible():
                    return elemento
            except Exception:
                continue

    return None


def cerrar_popup(popup) -> None:
    try:
        popup.close()
    except Exception:
        pass


def completar_busqueda(
    page: Page,
    username: str,
) -> None:
    campo = obtener_elemento_visible(
        page,
        [
            'input[placeholder*="Insertar" i]',
            'input[placeholder*="link" i]',
            'input[placeholder*="enlace" i]',
            'input[name*="url" i]',
            'input[name*="link" i]',
            'input[type="search"]',
            'input[type="text"]',
        ],
    )

    if campo is None:
        raise RuntimeError(
            "No se encontró el formulario principal de SSSInstagram."
        )

    campo.scroll_into_view_if_needed(timeout=15_000)
    campo.click(timeout=10_000)
    campo.fill("")
    campo.fill(username)

    print("Username escrito en SSSInstagram:", username)

    # Prioriza el botón perteneciente al mismo formulario del input.
    boton = None

    try:
        formulario = campo.locator("xpath=ancestor::form[1]")

        if formulario.count() > 0:
            candidatos = formulario.locator(
                'button[type="submit"], input[type="submit"], button'
            )

            for indice in range(candidatos.count()):
                candidato = candidatos.nth(indice)

                try:
                    texto = (
                        candidato.inner_text(timeout=2_000)
                        if candidato.evaluate("e => e.tagName") != "INPUT"
                        else candidato.get_attribute("value") or ""
                    )

                    if (
                        candidato.is_visible()
                        and re.search(r"descargar|download", texto, re.I)
                    ):
                        boton = candidato
                        break
                except Exception:
                    continue

    except Exception:
        boton = None

    if boton is None:
        try:
            candidatos = page.get_by_role(
                "button",
                name=re.compile(r"^\s*(Descargar|Download)\s*$", re.I),
            )

            for indice in range(candidatos.count()):
                candidato = candidatos.nth(indice)

                if candidato.is_visible():
                    boton = candidato
                    break
        except Exception:
            boton = None

    if boton is None:
        boton = obtener_elemento_visible(
            page,
            [
                'button:has-text("Descargar")',
                'button:has-text("Download")',
                'input[type="submit"][value*="Descargar" i]',
                'input[type="submit"][value*="Download" i]',
                'button[type="submit"]',
            ],
        )

    if boton is None:
        campo.press("Enter")
        print("Formulario enviado con Enter.")
    else:
        boton.scroll_into_view_if_needed(timeout=10_000)
        boton.click(timeout=15_000)
        print("Botón Descargar pulsado.")

    # Algunos proveedores abren publicidad en el primer clic. Si el
    # resultado no aparece, se vuelve a enviar una única vez.
    try:
        esperar_resultado(page, username, timeout=35_000)
    except RuntimeError:
        print("Primer envío sin resultado; realizando un segundo intento.")

        if boton is not None:
            boton.click(timeout=15_000)
        else:
            campo.press("Enter")

        esperar_resultado(page, username, timeout=45_000)

    page.wait_for_timeout(ESPERA_DESPUES_FORMULARIO_MS)


def esperar_resultado(
    page: Page,
    username: str,
    timeout: int,
) -> None:
    try:
        page.wait_for_function(
            r"""
            username => {
                const texto = (document.body.innerText || "").toLowerCase();
                const usuario = username.toLowerCase();

                const tieneUsuario =
                    texto.includes("@" + usuario)
                    || texto.includes(usuario);

                const tienePosts = Array.from(
                    document.querySelectorAll("body *")
                ).some(elemento => {
                    const t = (elemento.textContent || "")
                        .trim()
                        .toUpperCase();

                    const r = elemento.getBoundingClientRect();

                    return (
                        (t === "POSTS" || t === "PUBLICACIONES")
                        && r.width >= 20
                        && r.height >= 8
                    );
                });

                const tieneTarjeta = Array.from(
                    document.querySelectorAll("a, button, [role='button']")
                ).some(elemento => {
                    const t = (elemento.textContent || "").trim();
                    const r = elemento.getBoundingClientRect();

                    return (
                        /^(Descargar|Download)$/i.test(t)
                        && r.width >= 60
                        && r.height >= 20
                    );
                });

                return tieneUsuario && tienePosts && tieneTarjeta;
            }
            """,
            arg=username,
            timeout=timeout,
        )

    except PlaywrightTimeoutError as error:
        raise RuntimeError(
            "SSSInstagram no mostró el perfil o las publicaciones "
            "después de enviar el username."
        ) from error


def obtener_total_posts(page: Page) -> int | None:
    valor = page.evaluate(
        r"""
        () => {
            const texto = document.body.innerText || "";

            const patrones = [
                /(\d[\d.,]*\s*[KkMm]?)\s+posts\b/i,
                /(\d[\d.,]*\s*[KkMm]?)\s+publicaciones\b/i
            ];

            for (const patron of patrones) {
                const coincidencia = texto.match(patron);

                if (coincidencia) {
                    return coincidencia[1];
                }
            }

            return null;
        }
        """
    )

    if not valor:
        return None

    texto = str(valor).strip().upper().replace(" ", "")
    multiplicador = 1

    if texto.endswith("K"):
        multiplicador = 1_000
        texto = texto[:-1]
    elif texto.endswith("M"):
        multiplicador = 1_000_000
        texto = texto[:-1]

    if multiplicador > 1:
        texto = texto.replace(",", ".")

        try:
            return int(float(texto) * multiplicador)
        except ValueError:
            return None

    texto = texto.replace(".", "").replace(",", "")

    try:
        return int(texto)
    except ValueError:
        return None


def activar_posts_si_es_necesario(page: Page) -> None:
    """POSTS aparece activo por defecto; se pulsa solo si otra pestaña lo está."""

    info = page.evaluate(
        r"""
        () => {
            const candidatos = Array.from(
                document.querySelectorAll("a, button, li, [role='tab'], [role='button']")
            ).filter(elemento => {
                const texto = (elemento.textContent || "").trim().toUpperCase();
                const r = elemento.getBoundingClientRect();

                return (
                    (texto === "POSTS" || texto === "PUBLICACIONES")
                    && r.width >= 20
                    && r.height >= 8
                );
            });

            const elemento = candidatos[0];

            if (!elemento) {
                return null;
            }

            const clase = String(elemento.className || "");
            const estilo = getComputedStyle(elemento);

            return {
                activa:
                    /(active|selected|current|is-active)/i.test(clase)
                    || elemento.getAttribute("aria-selected") === "true"
                    || elemento.getAttribute("data-state") === "active"
                    || Number.parseInt(estilo.fontWeight || "400", 10) >= 600,
                x: elemento.getBoundingClientRect().left
                    + elemento.getBoundingClientRect().width / 2,
                y: elemento.getBoundingClientRect().top
                    + elemento.getBoundingClientRect().height / 2
            };
        }
        """
    )

    if info is None:
        raise RuntimeError("No se encontró la pestaña POSTS en SSSInstagram.")

    if bool(info.get("activa")):
        return

    page.mouse.click(float(info["x"]), float(info["y"]))
    page.wait_for_timeout(900)


def extraer_tarjetas_posts(page: Page) -> list[dict]:
    """
    Devuelve únicamente recursos pertenecientes a tarjetas de POSTS.

    Una tarjeta válida debe estar debajo de la pestaña POSTS y contener:
    - una imagen de tamaño razonable;
    - su propio botón/enlace Descargar.

    Esto excluye logo, avatar, publicidad e imágenes de ayuda.
    """

    return page.evaluate(
        r"""
        () => {
            function visible(elemento) {
                if (!elemento) return false;
                const r = elemento.getBoundingClientRect();
                const e = getComputedStyle(elemento);
                return (
                    r.width > 1
                    && r.height > 1
                    && e.display !== "none"
                    && e.visibility !== "hidden"
                );
            }

            function normalizar(valor) {
                if (!valor || typeof valor !== "string") return null;

                valor = valor
                    .trim()
                    .replaceAll("\\/", "/")
                    .replaceAll("&amp;", "&");

                if (
                    !valor
                    || valor.startsWith("data:")
                    || valor.startsWith("blob:")
                    || valor.startsWith("javascript:")
                    || valor === "#"
                ) {
                    return null;
                }

                try {
                    return new URL(valor, location.href).href;
                } catch {
                    return null;
                }
            }

            function srcsetOrdenado(valor) {
                if (!valor) return [];

                return valor
                    .split(",")
                    .map(entrada => entrada.trim())
                    .filter(Boolean)
                    .map(entrada => {
                        const partes = entrada.split(/\s+/);
                        const url = normalizar(partes[0]);
                        const descriptor = partes[1] || "1x";
                        let peso = 1;

                        if (descriptor.endsWith("w")) {
                            peso = Number.parseFloat(descriptor) || 1;
                        } else if (descriptor.endsWith("x")) {
                            peso = (Number.parseFloat(descriptor) || 1) * 10000;
                        }

                        return {url, peso};
                    })
                    .filter(x => x.url)
                    .sort((a, b) => b.peso - a.peso)
                    .map(x => x.url);
            }

            const pestaña = Array.from(
                document.querySelectorAll("body *")
            ).find(elemento => {
                const texto = (elemento.textContent || "").trim().toUpperCase();
                const r = elemento.getBoundingClientRect();

                return (
                    (texto === "POSTS" || texto === "PUBLICACIONES")
                    && r.width >= 20
                    && r.height >= 8
                    && r.width <= 450
                );
            });

            const limite = pestaña
                ? pestaña.getBoundingClientRect().bottom + window.scrollY - 25
                : 0;

            const botones = Array.from(
                document.querySelectorAll("a, button, [role='button']")
            ).filter(elemento => {
                const texto = (elemento.textContent || "").trim();
                const r = elemento.getBoundingClientRect();

                return (
                    /^(Descargar|Download)$/i.test(texto)
                    && visible(elemento)
                    && r.top + window.scrollY > limite
                    && r.width >= 60
                    && r.height >= 20
                );
            });

            const resultados = [];
            const grupos = new Set();

            for (let indice = 0; indice < botones.length; indice++) {
                const boton = botones[indice];
                let nodo = boton.parentElement;
                let contenedor = null;
                let respaldo = null;

                for (let nivel = 0; nodo && nivel < 9; nivel++, nodo = nodo.parentElement) {
                    if (nodo === document.body || nodo === document.documentElement) break;

                    const r = nodo.getBoundingClientRect();
                    const imagenes = Array.from(nodo.querySelectorAll("img"))
                        .filter(imagen => {
                            const ir = imagen.getBoundingClientRect();
                            return (
                                visible(imagen)
                                && Math.max(ir.width, imagen.naturalWidth || 0) >= 120
                                && Math.max(ir.height, imagen.naturalHeight || 0) >= 120
                            );
                        });

                    if (!imagenes.length) continue;

                    if (!respaldo) respaldo = nodo;

                    const descargas = Array.from(
                        nodo.querySelectorAll("a, button, [role='button']")
                    ).filter(x => /^(Descargar|Download)$/i.test((x.textContent || "").trim()));

                    if (
                        descargas.length <= 2
                        && r.width >= 170
                        && r.width <= 900
                        && r.height >= 180
                        && r.height <= 1300
                    ) {
                        contenedor = nodo;
                        break;
                    }
                }

                contenedor = contenedor || respaldo;
                if (!contenedor) continue;

                const cr = contenedor.getBoundingClientRect();
                const top = cr.top + window.scrollY;
                if (top < limite) continue;

                const imagenes = Array.from(contenedor.querySelectorAll("img"))
                    .filter(imagen => {
                        const r = imagen.getBoundingClientRect();
                        return (
                            visible(imagen)
                            && Math.max(r.width, imagen.naturalWidth || 0) >= 120
                            && Math.max(r.height, imagen.naturalHeight || 0) >= 120
                        );
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        const aa = Math.max(ar.width, a.naturalWidth || 0)
                            * Math.max(ar.height, a.naturalHeight || 0);
                        const ba = Math.max(br.width, b.naturalWidth || 0)
                            * Math.max(br.height, b.naturalHeight || 0);
                        return ba - aa;
                    });

                const imagen = imagenes[0];
                if (!imagen) continue;

                const urls = [];
                const agregar = valor => {
                    const url = normalizar(valor);
                    if (url && !urls.includes(url)) urls.push(url);
                };

                // El href del botón suele apuntar al recurso de mejor calidad.
                agregar(boton.getAttribute("href"));
                agregar(boton.closest("a")?.getAttribute("href"));
                agregar(imagen.closest("a")?.getAttribute("href"));
                agregar(imagen.getAttribute("data-full"));
                agregar(imagen.getAttribute("data-original"));
                agregar(imagen.getAttribute("data-download"));
                agregar(imagen.getAttribute("data-hd"));

                for (const url of srcsetOrdenado(imagen.getAttribute("srcset"))) {
                    agregar(url);
                }

                for (const url of srcsetOrdenado(imagen.getAttribute("data-srcset"))) {
                    agregar(url);
                }

                agregar(imagen.currentSrc);
                agregar(imagen.getAttribute("data-src"));
                agregar(imagen.getAttribute("data-lazy-src"));
                agregar(imagen.getAttribute("src"));

                if (!urls.length) continue;

                const grupo = String(
                    contenedor.getAttribute("data-id")
                    || contenedor.getAttribute("data-media-id")
                    || imagen.getAttribute("src")
                    || boton.getAttribute("href")
                    || `${Math.round(top)}:${Math.round(cr.left + window.scrollX)}`
                );

                if (grupos.has(grupo)) continue;
                grupos.add(grupo);

                resultados.push({
                    grupo,
                    orden: top * 100000 + Math.max(0, cr.left + window.scrollX) * 10 + indice / 100,
                    urls,
                    ancho: Math.max(imagen.naturalWidth || 0, imagen.getBoundingClientRect().width),
                    alto: Math.max(imagen.naturalHeight || 0, imagen.getBoundingClientRect().height)
                });
            }

            resultados.sort((a, b) => a.orden - b.orden);
            return resultados;
        }
        """
    )


def obtener_estado_scroll(page: Page) -> dict:
    return page.evaluate(
        r"""
        () => {
            const altura = Math.max(
                document.body.scrollHeight,
                document.documentElement.scrollHeight
            );

            return {
                scrollTop: window.scrollY,
                clientHeight: window.innerHeight,
                scrollHeight: altura,
                alFinal: window.scrollY + window.innerHeight >= altura - 220
            };
        }
        """
    )


def desplazar(page: Page) -> dict:
    estado = obtener_estado_scroll(page)

    if bool(estado["alFinal"]):
        page.keyboard.press("End")
    else:
        paso = max(850, int(float(estado["clientHeight"]) * 0.92))
        page.mouse.wheel(0, paso)

    page.wait_for_timeout(ESPERA_ENTRE_SCROLL_MS)
    return obtener_estado_scroll(page)


def cargar_tarjetas(
    page: Page,
    total_posts: int | None,
    progress_callback: ProgressCallback = None,
) -> list[dict]:
    acumuladas: dict[str, dict] = {}
    inicio = time.monotonic()
    ultima_novedad = inicio
    rondas_estables = 0
    ronda = 0

    while (time.monotonic() - inicio) * 1000 < TIEMPO_MAXIMO_SCROLL_MS:
        tarjetas = extraer_tarjetas_posts(page)
        cantidad_antes = len(acumuladas)

        for tarjeta in tarjetas:
            grupo = str(tarjeta["grupo"])
            existente = acumuladas.get(grupo)

            if existente is None:
                acumuladas[grupo] = tarjeta
            else:
                # Incorpora URL nuevas si el sitio mejora el srcset al hacer scroll.
                for url in tarjeta.get("urls", []):
                    if url not in existente["urls"]:
                        existente["urls"].append(url)

                existente["orden"] = min(
                    float(existente["orden"]),
                    float(tarjeta["orden"]),
                )

        estado = obtener_estado_scroll(page)
        ahora = time.monotonic()
        cantidad = len(acumuladas)

        if cantidad > cantidad_antes:
            ultima_novedad = ahora
            rondas_estables = 0
        else:
            rondas_estables += 1

        quieto = ahora - ultima_novedad
        transcurrido = ahora - inicio

        print(
            "SSS ronda",
            ronda + 1,
            "| tarjetas:",
            cantidad,
            "| total:",
            total_posts,
            "| scroll:",
            f'{int(estado["scrollTop"])}/{int(estado["scrollHeight"])}',
            "| final:",
            estado["alFinal"],
            "| quieto:",
            f"{quieto:.1f}s",
        )

        informar_progreso(
            progress_callback,
            (
                "📥 Cargando publicaciones en SSSInstagram…\n"
                f"Tarjetas detectadas: {cantidad}"
                + (f"/{total_posts}" if total_posts else "")
                + f"\nSin novedades: {int(quieto)} s"
                + f"\nTiempo total: {int(transcurrido)} s"
            ),
        )

        alcanzo_total = (
            total_posts is not None
            and total_posts > 0
            and cantidad >= total_posts
            and quieto >= 1.5
        )

        estable_al_final = (
            bool(estado["alFinal"])
            and quieto >= SEGUNDOS_ESTABLE_AL_FINAL
            and rondas_estables >= RONDAS_ESTABLES_REQUERIDAS
        )

        if alcanzo_total or estable_al_final:
            break

        if cantidad >= MAXIMO_TARJETAS:
            break

        desplazar(page)
        ronda += 1

    # Última lectura en el fondo para capturar el último lote lazy-loaded.
    page.keyboard.press("End")
    page.wait_for_timeout(900)

    for tarjeta in extraer_tarjetas_posts(page):
        grupo = str(tarjeta["grupo"])
        existente = acumuladas.get(grupo)

        if existente is None:
            acumuladas[grupo] = tarjeta
        else:
            for url in tarjeta.get("urls", []):
                if url not in existente["urls"]:
                    existente["urls"].append(url)

    return sorted(
        acumuladas.values(),
        key=lambda tarjeta: float(tarjeta["orden"]),
    )


def normalizar_para_telegram(
    contenido: bytes,
) -> tuple[bytes, str, int, int] | None:
    try:
        with Image.open(BytesIO(contenido)) as original:
            original.load()
            imagen = ImageOps.exif_transpose(original)
            ancho, alto = imagen.size

            if ancho < ANCHO_MINIMO or alto < ALTO_MINIMO:
                return None

            proporcion = ancho / alto

            if proporcion < 0.08 or proporcion > 12:
                return None

            if ancho + alto > MAXIMA_SUMA_DIMENSIONES:
                escala = MAXIMA_SUMA_DIMENSIONES / (ancho + alto)
                imagen = imagen.resize(
                    (
                        max(1, int(ancho * escala)),
                        max(1, int(alto * escala)),
                    ),
                    Image.Resampling.LANCZOS,
                )
                ancho, alto = imagen.size

            if imagen.mode not in ("RGB", "L"):
                if "A" in imagen.mode or imagen.mode == "P":
                    rgba = imagen.convert("RGBA")
                    fondo = Image.new("RGB", rgba.size, "white")
                    fondo.paste(rgba, mask=rgba.getchannel("A"))
                    imagen = fondo
                else:
                    imagen = imagen.convert("RGB")
            elif imagen.mode == "L":
                imagen = imagen.convert("RGB")

            for calidad in (95, 92, 88, 84, 78, 70, 62):
                salida = BytesIO()
                imagen.save(
                    salida,
                    format="JPEG",
                    quality=calidad,
                    optimize=True,
                    progressive=True,
                )
                bytes_finales = salida.getvalue()

                if len(bytes_finales) <= MAXIMO_BYTES_TELEGRAM:
                    return bytes_finales, "jpg", ancho, alto

    except (UnidentifiedImageError, OSError, ValueError):
        return None

    return None


def descargar_url(
    url: str,
    referer: str,
    cookies: dict[str, str],
    user_agent: str,
) -> RecursoPublicacion | None:
    if not es_url_http(url) or es_video(url):
        return None

    try:
        respuesta = requests.get(
            url,
            headers={
                "Referer": referer,
                "User-Agent": user_agent,
                "Accept": (
                    "image/avif,image/webp,image/png,image/jpeg,"
                    "image/*,*/*;q=0.8"
                ),
            },
            cookies=cookies,
            timeout=(
                TIMEOUT_CONEXION_SEGUNDOS,
                TIMEOUT_LECTURA_SEGUNDOS,
            ),
            allow_redirects=True,
        )

        if respuesta.status_code >= 400:
            return None

        contenido = respuesta.content

    except requests.RequestException:
        return None

    if len(contenido) < BYTES_MINIMOS:
        return None

    normalizada = normalizar_para_telegram(contenido)

    if normalizada is None:
        return None

    contenido_final, extension, ancho, alto = normalizada

    return RecursoPublicacion(
        url=url,
        orden=0,
        contenido=contenido_final,
        extension=extension,
        ancho=ancho,
        alto=alto,
        hash_archivo=hashlib.sha256(contenido_final).hexdigest(),
    )


def descargar_tarjeta(
    tarjeta: dict,
    referer: str,
    cookies: dict[str, str],
    user_agent: str,
) -> RecursoPublicacion | None:
    # Prueba primero el enlace del botón y después las variantes HD/srcset.
    for url in tarjeta.get("urls", []):
        recurso = descargar_url(
            url=url,
            referer=referer,
            cookies=cookies,
            user_agent=user_agent,
        )

        if recurso is not None:
            recurso.orden = float(tarjeta["orden"])
            return recurso

    return None


def descargar_publicaciones(
    username: str,
    progress_callback: ProgressCallback = None,
) -> ResultadoPublicaciones:
    username = limpiar_username(username)

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

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        )

        contexto = navegador.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="es-ES",
            service_workers="block",
            reduced_motion="reduce",
            user_agent=user_agent,
        )

        pagina = contexto.new_page()
        pagina.on("popup", cerrar_popup)
        pagina.set_default_timeout(20_000)
        pagina.set_default_navigation_timeout(90_000)

        def controlar_peticion(route, request) -> None:
            # Fuentes, audio y video no aportan datos al scraper.
            if request.resource_type in {"font", "media"} or es_video(
                request.url,
                request.resource_type,
            ):
                route.abort()
                return

            route.continue_()

        pagina.route("**/*", controlar_peticion)

        try:
            print("Abriendo SSSInstagram:", URL_BUSCADOR)
            informar_progreso(progress_callback, "🌐 Abriendo SSSInstagram…")

            pagina.goto(
                URL_BUSCADOR,
                wait_until="domcontentloaded",
                timeout=90_000,
            )

            pagina.wait_for_selector(
                'input[type="text"], input[type="search"]',
                state="visible",
                timeout=30_000,
            )

            informar_progreso(
                progress_callback,
                f"🔎 Buscando el perfil @{username}…",
            )

            completar_busqueda(pagina, username)
            activar_posts_si_es_necesario(pagina)

            total_posts = obtener_total_posts(pagina)
            print(
                "Posts declarados por SSSInstagram:",
                total_posts if total_posts is not None else "no detectados",
            )

            informar_progreso(
                progress_callback,
                (
                    f"👤 Perfil @{username} cargado.\n"
                    "Leyendo únicamente las tarjetas de POSTS…"
                ),
            )

            tarjetas = cargar_tarjetas(
                page=pagina,
                total_posts=total_posts,
                progress_callback=progress_callback,
            )

            if not tarjetas:
                raise RuntimeError(
                    "SSSInstagram mostró el perfil, pero no se encontraron "
                    "tarjetas válidas dentro de POSTS."
                )

            informar_progreso(
                progress_callback,
                (
                    "⬇️ Descargando fotografías en paralelo…\n"
                    f"Tarjetas para procesar: {len(tarjetas)}"
                ),
            )

            cookies = {
                str(cookie["name"]): str(cookie["value"])
                for cookie in contexto.cookies()
            }
            referer = pagina.url
            recursos_por_indice: dict[int, RecursoPublicacion] = {}
            completadas = 0

            with ThreadPoolExecutor(
                max_workers=DESCARGAS_SIMULTANEAS
            ) as ejecutor:
                futuros = {
                    ejecutor.submit(
                        descargar_tarjeta,
                        tarjeta,
                        referer,
                        cookies,
                        user_agent,
                    ): indice
                    for indice, tarjeta in enumerate(tarjetas)
                }

                for futuro in as_completed(futuros):
                    indice = futuros[futuro]
                    completadas += 1

                    try:
                        recurso = futuro.result()
                    except Exception as error:
                        print("Error en descarga paralela:", error)
                        recurso = None

                    if recurso is not None:
                        recursos_por_indice[indice] = recurso

                    if (
                        completadas == 1
                        or completadas % 4 == 0
                        or completadas == len(tarjetas)
                    ):
                        informar_progreso(
                            progress_callback,
                            (
                                "⬇️ Descargando fotografías…\n"
                                f"Procesadas: {completadas}/{len(tarjetas)}\n"
                                f"Válidas: {len(recursos_por_indice)}"
                            ),
                        )

            recursos: list[RecursoPublicacion] = []
            hashes: set[str] = set()

            for indice in sorted(recursos_por_indice):
                recurso = recursos_por_indice[indice]

                if recurso.hash_archivo in hashes:
                    continue

                hashes.add(recurso.hash_archivo)
                recursos.append(recurso)

            recursos.sort(key=lambda recurso: recurso.orden)

            if not recursos:
                raise RuntimeError(
                    "Se detectaron tarjetas, pero ninguna URL devolvió "
                    "una fotografía válida."
                )

            print("Imágenes finales para Telegram:", len(recursos))

            informar_progreso(
                progress_callback,
                (
                    "✅ Fotografías preparadas.\n"
                    f"Total válido: {len(recursos)}"
                ),
            )

            return ResultadoPublicaciones(
                username=username,
                recursos=recursos,
            )

        finally:
            contexto.close()
            navegador.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Busca un perfil en SSSInstagram, carga POSTS "
            "y guarda las fotografías."
        )
    )

    parser.add_argument(
        "username",
        help="Username de Instagram, con o sin @.",
    )

    argumentos = parser.parse_args()
    resultado = descargar_publicaciones(argumentos.username)

    carpeta = Path(f"publicaciones_{resultado.username}")
    carpeta.mkdir(parents=True, exist_ok=True)

    for indice, recurso in enumerate(resultado.recursos, start=1):
        ruta = carpeta / (
            f"{indice:03d}_{recurso.ancho}x{recurso.alto}."
            f"{recurso.extension}"
        )
        ruta.write_bytes(recurso.contenido)

    print("Carpeta creada:", carpeta.resolve())
    print("Cantidad de imágenes:", resultado.cantidad_imagenes)


if __name__ == "__main__":
    main()
