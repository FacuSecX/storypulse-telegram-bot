# Publicar el proyecto en GitHub

## 1. Crear el repositorio

En GitHub, crea un repositorio vacío llamado:

```text
storypulse-telegram-bot
```

No agregues README, licencia ni `.gitignore` desde la web, porque ya están incluidos.

## 2. Revisar que no haya secretos

Antes del primer commit:

```bash
git status
git grep -nE '[0-9]{7,12}:[A-Za-z0-9_-]{20,}' || true
git check-ignore .env accounts.json
```

Los dos últimos archivos deben aparecer como ignorados.

## 3. Crear el historial local

```bash
git init
git add .
git status
git commit -m "Initial public release"
git branch -M main
```

Revisa cuidadosamente la lista mostrada por `git status` antes del commit.

## 4. Conectar y publicar

Reemplaza `TU_USUARIO`:

```bash
git remote add origin https://github.com/TU_USUARIO/storypulse-telegram-bot.git
git push -u origin main
```

## 5. Configuración privada local

Después de clonar o descargar el repositorio:

```bash
cp .env.example .env
cp accounts.example.json accounts.json
```

Nunca uses `git add -f` con `.env`, `accounts.json`, bases de datos, cookies, sesiones o logs.

## Token previamente expuesto

Un token incluido anteriormente en archivos de prueba debe revocarse antes de publicar o ejecutar esta versión. Eliminarlo del código nuevo no invalida una credencial que ya fue compartida.
