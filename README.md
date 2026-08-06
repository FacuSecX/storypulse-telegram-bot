# StoryPulse Telegram Bot

Bot de Telegram para consultar historias y publicaciones de perfiles públicos de Instagram mediante servicios web intermediarios, programar revisiones automáticas y recibir alertas configurables por perfil.

> **Importante:** el proyecto no utiliza credenciales de Instagram. Depende de páginas intermediarias públicas, por lo que puede dejar de funcionar si esos sitios cambian su estructura, limitan el acceso o desaparecen.

## ¿Por qué usar servicios intermediarios en lugar de Instaloader?

StoryPulse fue diseñado para consultar **contenido público sin iniciar sesión en Instagram**. En lugar de guardar una sesión de Instagram en el equipo o VPS, automatiza páginas intermediarias que permiten consultar perfiles públicos desde el navegador.

Esto ofrece varios beneficios prácticos para un bot que funciona de manera continua:

| Con servicios intermediarios | Con Instaloader u otras librerías autenticadas |
|---|---|
| No requiere usuario ni contraseña de Instagram. | Normalmente necesita iniciar sesión para consultar historias. |
| No almacena cookies ni archivos de sesión de Instagram. | Debe proteger y renovar una sesión autenticada. |
| No necesita gestionar códigos 2FA desde el bot. | El primer acceso puede solicitar autenticación en dos pasos. |
| Evita que el bot provoque checkpoints sobre una cuenta personal al iniciar sesión desde un VPS. | Los cambios de IP, dispositivo o centro de datos pueden generar verificaciones. |
| Se puede mover entre Windows y VPS sin trasladar una sesión de Instagram. | La sesión puede invalidarse al cambiar de red o ubicación. |
| El repositorio público no necesita instrucciones para manejar credenciales de Instagram. | Una configuración incorrecta puede exponer cookies o archivos de sesión. |
| Las cuentas personales quedan separadas del proceso de consulta. | Las solicitudes se realizan con la identidad de la cuenta autenticada. |

### Ventajas para mantenimiento y despliegue

- **Configuración más sencilla:** solo requiere el token de Telegram, los IDs autorizados y la lista de perfiles públicos.
- **Menor exposición de credenciales:** una filtración del servidor no revela una contraseña o sesión de Instagram porque el proyecto no las guarda.
- **Despliegue más portable:** puede instalarse en otro equipo sin repetir un login de Instagram.
- **Menos interrupciones por seguridad de cuenta:** no depende de aprobar accesos, checkpoints o códigos 2FA para continuar funcionando.
- **Adecuado para tareas anónimas sobre contenido público:** el bot no necesita seguir perfiles ni actuar como una cuenta de Instagram.

### Diferencias y límites importantes

Este enfoque no es necesariamente mejor para todos los proyectos. Instaloader y otras librerías pueden ofrecer metadatos más completos, URLs más directas y mayor control cuando existe una sesión válida. StoryPulse prioriza **no autenticar una cuenta personal** y acepta estas limitaciones a cambio:

- depende de que los sitios intermediarios estén disponibles;
- sus selectores pueden cambiar sin aviso;
- la resolución entregada puede ser inferior a la original;
- puede haber publicidad, límites o protecciones anti-bot;
- solo debe utilizarse con contenido público y accesible legítimamente.

Por eso el proyecto mantiene los scrapers separados en `history.py` y `publicaciones.py`: si una página cambia, puede reemplazarse ese módulo sin reescribir el bot de Telegram, las programaciones o la base de datos.

## Funciones

- Consulta manual de historias públicas.
- Consulta manual de publicaciones y carruseles.
- Programaciones automáticas por perfil.
- Intervalos configurables desde Telegram.
- Deduplicación en SQLite para no reenviar la misma historia.
- Pausar, reanudar, modificar y eliminar programaciones.
- Notificación sonora independiente por perfil programado.
- Prueba de alerta con un botón dedicado.
- Envío silencioso cuando no corresponde generar sonido.
- Mensaje de progreso editable durante las consultas manuales.
- Navegador Chromium en modo oculto por defecto.
- Lista privada de perfiles fuera del repositorio.
- Acceso restringido por ID de Telegram.

## Comportamiento de las alertas

Cada programación guarda su propia preferencia:

- **Alerta activada + historias nuevas:** se envía una única notificación sonora y después las imágenes llegan silenciosamente.
- **Alerta desactivada + historias nuevas:** la alerta y las imágenes llegan sin sonido.
- **Sin historias nuevas:** no se envía ningún mensaje y no se genera ninguna notificación.
- **Consulta manual:** muestra progreso, pero no utiliza la alerta sonora de la programación.

El sonido también depende de la configuración del teléfono: el chat del bot debe tener notificaciones habilitadas, el dispositivo no debe estar en modo silencioso y el modo No molestar puede impedir el sonido.

## Estructura

```text
storypulse-telegram-bot/
├── bot.py                    # Menús, comandos, Telegram y JobQueue
├── config.py                 # Variables de entorno y perfiles privados
├── database.py               # SQLite, migraciones y deduplicación
├── history.py                # Scraper de historias públicas
├── publicaciones.py          # Scraper de publicaciones públicas
├── accounts.example.json     # Ejemplo sin usernames reales
├── .env.example              # Plantilla de configuración
├── requirements.txt
├── LICENSE
├── SECURITY.md
└── .github/workflows/ci.yml
```

## Requisitos

- Python 3.10 o superior.
- Chromium administrado por Playwright.
- Un bot creado con `@BotFather`.
- Al menos un ID numérico de Telegram autorizado.

## Instalación

### Windows CMD

```bat
 git clone https://github.com/TU_USUARIO/storypulse-telegram-bot.git
 cd storypulse-telegram-bot

 py -m venv venv
 venv\Scripts\activate

 python -m pip install --upgrade pip
 python -m pip install -r requirements.txt
 python -m playwright install chromium
```

### Linux o VPS

```bash
 git clone https://github.com/TU_USUARIO/storypulse-telegram-bot.git
 cd storypulse-telegram-bot

 python3 -m venv venv
 source venv/bin/activate

 python -m pip install --upgrade pip
 python -m pip install -r requirements.txt
 python -m playwright install --with-deps chromium
```

## Configuración

Copia las plantillas:

### Windows

```bat
copy .env.example .env
copy accounts.example.json accounts.json
```

### Linux

```bash
cp .env.example .env
cp accounts.example.json accounts.json
chmod 600 .env accounts.json
```

Edita `.env`:

```env
BOT_TOKEN=TOKEN_REAL_ENTREGADO_POR_BOTFATHER
TELEGRAM_USER_IDS=123456789
TIMEZONE=America/Argentina/Buenos_Aires
ACCOUNTS_FILE=accounts.json
DATABASE_PATH=data/storypulse.db
SHOW_BROWSER=false
```

Edita `accounts.json`:

```json
{
  "Nombre visible": "username_publico",
  "Otro perfil": "otro.username"
}
```

`accounts.json` y `.env` están ignorados por Git y no deben subirse.

## Ejecución

```bash
python bot.py
```

Comandos disponibles:

- `/start` — abre el menú.
- `/menu` — muestra los perfiles configurados.
- `/programaciones` — muestra y administra las tareas automáticas.

## Ejecución en segundo plano

Ejemplo con `systemd`:

```ini
[Unit]
Description=StoryPulse Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=debian
WorkingDirectory=/home/debian/storypulse-telegram-bot
EnvironmentFile=/home/debian/storypulse-telegram-bot/.env
ExecStart=/home/debian/storypulse-telegram-bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Privacidad y seguridad

- Nunca escribas el token directamente en `bot.py` o `history.py`.
- No publiques `.env`, `accounts.json`, bases SQLite, cookies, sesiones ni logs.
- Si un token se publicó alguna vez, revócalo en `@BotFather` antes de utilizar el repositorio.
- Restringe el bot mediante `TELEGRAM_USER_IDS`.
- Revisa los cambios con `git diff --cached` antes de cada `git push`.

## Limitaciones

- Solo está pensado para contenido público accesible legítimamente.
- La disponibilidad y calidad dependen de los servicios intermediarios.
- Los selectores web pueden romperse cuando un sitio cambia su HTML.
- El envío como fotografía puede aplicar compresión adicional de Telegram.
- El proyecto no está afiliado con Instagram, Meta, Telegram ni los sitios intermediarios utilizados.

## Uso responsable

Utiliza el proyecto respetando la privacidad, los derechos de autor, las condiciones de los servicios y las leyes aplicables. No lo uses para eludir controles de acceso ni consultar perfiles privados sin autorización.

## Licencia

Distribuido bajo la licencia MIT. Consulta [LICENSE](LICENSE).
