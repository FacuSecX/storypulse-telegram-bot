<p align="center">
<img src="http://imgfz.com/i/BaRozI6.png" title="StoryPulse">
</p>

<p align="center">
<a href="https://github.com/FacuSecX"><img title="Autor" src="https://img.shields.io/badge/Author-Facu%20-blue?style=for-the-badge&logo=github"></a>
<a href=""><img title="Version" src="https://img.shields.io/badge/Version-1.0-red?style=for-the-badge&logo="></a>
</p>

<p align="center">
<a href=""><img title="System" src="https://img.shields.io/badge/Supported%20OS-Linux-orange?style=for-the-badge&logo=linux"></a>
<a href="https://paypal.me/FacuSecX"><img title="Paypal" src="https://img.shields.io/badge/Donate-PayPal-green.svg?style=for-the-badge&logo=paypal"></a>
</p>

<p align="center">
<a href="mailto:facusex@gmail.com"><img title="Correo" src="https://img.shields.io/badge/Correo-facusecX@gmail.com-blueviolet?style=for-the-badge&logo=gmai"></a>
<a href="https://t.me/FacuSecX"><img title="Chat" src="https://img.shields.io/badge/CHAT-TELEGRAM-blue?style=for-thjlje-badge&logo=telegram"></a>
</p>





# StoryPulse Telegram Bot
Bot de Telegram para consultar historias y publicaciones de perfiles públicos de Instagram mediante servicios web intermediarios, programar revisiones automáticas y recibir alertas configurables por perfil.

> **Importante:** el proyecto no utiliza credenciales de Instagram. Depende de páginas intermediarias públicas, por lo que puede dejar de funcionar si esos sitios cambian su estructura, limitan el acceso o desaparecen.




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



## ¿Por qué usar servicios intermediarios en lugar de Instaloader?

StoryPulse consulta contenido público mediante páginas intermediarias, en lugar de conectarse directamente a Instagram con una cuenta autenticada.

Este enfoque fue elegido para reducir la complejidad del proyecto y evitar los problemas habituales que aparecen al mantener una sesión de Instagram activa dentro de un bot funcionando las 24 horas.

### Ventajas de este enfoque

- No requiere iniciar sesión en Instagram.
- No solicita usuario, contraseña ni código de autenticación en dos pasos.
- No almacena cookies ni archivos de sesión de Instagram.
- No expone una cuenta personal al funcionamiento continuo del bot.
- Reduce la posibilidad de recibir checkpoints o solicitudes de confirmación.
- Evita tener que renovar sesiones vencidas periódicamente.
- Facilita la instalación en VPS, servidores y computadoras nuevas.
- Permite mover el proyecto entre equipos sin transferir credenciales de Instagram.
- Separa completamente la cuenta personal del sistema de automatización.
- Puede consultar perfiles públicos sin necesidad de que una cuenta los siga.
- Una restricción aplicada al servidor no compromete directamente una cuenta personal.
- La configuración es más sencilla para usuarios sin experiencia con autenticación, cookies o sesiones.

### Comparación con Instaloader y librerías similares

| Característica | StoryPulse con intermediario | Instaloader con autenticación |
|---|---:|---:|
| Requiere cuenta de Instagram | No | Generalmente sí |
| Requiere contraseña | No | Sí, durante el inicio de sesión |
| Puede requerir código 2FA | No | Sí |
| Guarda cookies o sesión | No | Sí |
| Puede recibir checkpoints | Menor exposición directa | Sí |
| Riesgo para una cuenta personal | Bajo | Mayor |
| Instalación en un VPS | Más sencilla | Puede requerir validaciones |
| Cambio de servidor o IP | No afecta una sesión personal | Puede invalidar la sesión |
| Acceso a perfiles privados | No | Solo si la cuenta tiene acceso |
| Metadatos estructurados | Limitados por el intermediario | Más completos |
| Calidad de las imágenes | Depende del intermediario | Puede ser mejor |
| Dependencia de cambios externos | Sí, del sitio intermediario | Sí, de Instagram |

### Problemas habituales al usar autenticación directa

Cuando una librería se conecta directamente a Instagram desde un VPS, pueden aparecer situaciones como:

- inicio de sesión detectado desde una ubicación nueva;
- solicitud de confirmación desde la aplicación oficial;
- checkpoint de seguridad;
- código de autenticación en dos pasos;
- sesión invalidada por cambio de IP;
- restricciones temporales por cantidad de solicitudes;
- bloqueo de una IP perteneciente a un centro de datos;
- necesidad de volver a generar el archivo de sesión;
- riesgo de afectar la cuenta utilizada por el bot.

StoryPulse evita almacenar este tipo de credenciales y delega la consulta pública al servicio intermediario configurado.

### Limitaciones del uso de intermediarios

Este método también tiene algunas desventajas importantes:

- El proyecto depende de que la página intermediaria continúe funcionando.
- Los selectores HTML pueden cambiar y requerir una actualización del scraper.
- La calidad de las fotografías depende de la resolución entregada por la página.
- Algunos servicios pueden mostrar publicidad o ventanas emergentes.
- Puede existir una demora adicional respecto de una consulta directa.
- Los metadatos disponibles pueden ser menos completos.
- No permite acceder a perfiles privados.
- No garantiza disponibilidad permanente del contenido.
- Un cambio en el sitio intermediario puede detener temporalmente las consultas.

### Objetivo del proyecto

StoryPulse no busca reemplazar la API oficial de Instagram ni acceder a contenido privado.

Su objetivo es ofrecer una herramienta sencilla para:

- consultar contenido público;
- automatizar revisiones periódicas;
- detectar nuevas historias;
- evitar envíos duplicados;
- recibir resultados mediante Telegram;
- configurar alertas sonoras por perfil;
- funcionar sin almacenar credenciales de Instagram.

> StoryPulse debe utilizarse únicamente para consultar contenido público y respetando las condiciones de los servicios utilizados.




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
