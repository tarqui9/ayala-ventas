# Sistema web de gestión comercial con análisis predictivo — Distribuidora AYALA

Proyecto integrador de **Tecnologías en Internet (TEC-644)** · Universidad Adventista de Bolivia · Gestión 2026-2
Autor: Brian Rehi Tarqui Vasquez

Aplicación web fullstack para gestionar pedidos, distribución, pagos, inventario y reportes,
con un servicio de análisis predictivo que estima qué productos y cuándo volverá a comprar cada cliente.

## Repositorios

| Parte | Tecnología | Repositorio | Despliegue |
|---|---|---|---|
| Frontend (SPA) | Angular 17, TypeScript, Angular Material, Bootstrap 5 | [angular-ventas-frontend](https://github.com/tarqui9/angular-ventas-frontend) | Vercel |
| Backend (API REST) | Laravel 10 (PHP 8.3), Passport (JWT/OAuth2) | [laravel-nventas-backend](https://github.com/tarqui9/laravel-nventas-backend) | Render (Docker) |
| Análisis predictivo | Python 3.12, FastAPI, scikit-learn | este repositorio, carpeta [`ml/`](ml) | Render (Python) |
| Base de datos | MySQL 8 (local) / TiDB Cloud, compatible con MySQL (nube) | — | TiDB Cloud Starter |
| Orquestación local | Docker + Docker Compose | este repositorio, [`docker-compose.yml`](docker-compose.yml) | — |

## Arquitectura

```
Navegador ──HTTPS──▶ Frontend Angular (Vercel)
                         │  REST + JSON, Authorization: Bearer <JWT>
                         ▼
                     Backend Laravel (Render) ──SQL/TLS──▶ MySQL / TiDB Cloud
                         │  HTTP + JSON
                         ▼
                     Servicio de análisis FastAPI (Render)
```

## Ejecución local con Docker Compose

Requisitos: Docker y Docker Compose, Git.

```bash
# 1. Clonar este repositorio y los dos proyectos dentro de él
git clone https://github.com/tarqui9/ayala-ventas.git
cd ayala-ventas
git clone https://github.com/tarqui9/laravel-nventas-backend.git laravel_nventas-main
git clone https://github.com/tarqui9/angular-ventas-frontend.git "app ang ventas"

# 2. Variables de entorno
cp .env.example .env                                         # contraseña de MySQL para Docker
cp laravel_nventas-main/.env.example laravel_nventas-main/.env
docker compose run --rm backend php artisan key:generate
docker compose run --rm backend php artisan passport:keys

# 3. (Opcional) copia de la base: colocar un volcado .sql en docker/mysql-init/

# 4. Levantar todo
docker compose up -d --build
docker compose ps
```

| Servicio | URL local |
|---|---|
| Aplicación (Angular) | http://localhost:4200 |
| API REST (Laravel) | http://localhost:8000/api |
| Análisis (FastAPI, documentación interactiva) | http://localhost:8001/docs |
| phpMyAdmin | http://localhost:8080 |
| MySQL | localhost:3307 |

Detener: `docker compose down` (los datos de MySQL se conservan en un volumen).

## Despliegue en la nube

1. **Base de datos — TiDB Cloud Starter** (gratis, compatible con MySQL): crear el clúster e importar la base.
2. **Backend y análisis — Render**: `New + → Blueprint` con este repositorio. [`render.yaml`](render.yaml)
   crea `ayala-backend` (Docker) y `ayala-ml` (Python) y pide las variables secretas.
3. **Frontend — Vercel**: importar `angular-ventas-frontend` y definir la variable
   `API_URL` con la URL pública de `ayala-backend`. [`vercel.json`](https://github.com/tarqui9/angular-ventas-frontend/blob/master/vercel.json)
   define el build de producción.

### Variables de entorno del backend

| Variable | Local (Docker) | Producción (Render) |
|---|---|---|
| `APP_KEY` | `php artisan key:generate` | clave nueva, solo en Render |
| `DB_HOST` / `DB_PORT` | `mysql` / `3306` | host y puerto de TiDB Cloud (`4000`) |
| `DB_DATABASE` / `DB_USERNAME` / `DB_PASSWORD` | `nventas` / `root` / `.env` | datos de TiDB Cloud |
| `MYSQL_ATTR_SSL_CA` | — | `/etc/ssl/certs/ca-certificates.crt` (conexión cifrada) |
| `PASSPORT_PRIVATE_KEY` / `PASSPORT_PUBLIC_KEY` | `storage/oauth-*.key` | contenido de las llaves RSA |
| `PREDICTIVO_URL` | `http://ml:8001` | URL pública de `ayala-ml` |

Ningún secreto (`.env`, llaves, volcados de base de datos) se guarda en Git.
