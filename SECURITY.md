# Política de seguridad

## Datos que nunca deben publicarse

- Tokens de Telegram.
- IDs personales de Telegram cuando no sea necesario hacerlos públicos.
- `accounts.json` con perfiles privados de configuración.
- Bases de datos SQLite.
- Cookies, archivos de sesión o credenciales.
- Logs que contengan URLs sensibles o datos personales.

## Token expuesto

Si un token llegó a GitHub, debe considerarse comprometido incluso después de borrar el archivo. Revócalo desde `@BotFather`, genera uno nuevo y, cuando corresponda, elimina el secreto del historial de Git.

## Reporte de vulnerabilidades

No abras un issue público que contenga credenciales, tokens o datos privados. Contacta al mantenedor por un canal privado indicado en el perfil del repositorio.
