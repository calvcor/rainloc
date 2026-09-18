# 🌧️ RainLoc - Plataforma GIS Meteorológica e Hidrológica en Tiempo Real

**RainLoc** es una plataforma web GIS de alto rendimiento orientada a la monitorización meteorológica e hidrológica en tiempo real, con especial cobertura en la **Demarcación Hidrográfica del Júcar (CHJ)** y territorio peninsular español.

Integra en una interfaz unificada e interactiva datos oficiales de radares meteorológicos, descargas eléctricas en vivo, avisos oficiales de AEMET, estaciones de aforo de ríos, niveles de embalses y pluviómetros.

---

## 🚀 Características Principales

### 💧 Hidrología en Tiempo Real (SAIH Júcar)
- **Caudales en Ríos**: 79 estaciones de aforo con caudal instantáneo ($m^3/s$), clasificación según umbrales de alerta (amarillo, naranja y rojo) y series temporales históricas interactivas con gráficas SVG dinámicas (24h, 72h).
- **Embalses y Presas**: 25-32 embalses monitorizados con volumen actual ($hm^3$), capacidad NMN, porcentaje de llenado, cota de nivel y vertido, semáforo de riesgo de desbordamiento y evolución temporal.
- **Pluviómetros**: 182 estaciones de pluviometría con acumulación en 1h, 4h, 12h y 24h, mapa de calor de intensidad y puntos no invasivos optimizados para inspección.
- **Selector de Proximidad**: Detección automática y navegación entre estaciones de aforo y presas cercanas (<3 km).

### 📡 Meteorología en Vivo
- **Radar Meteorológico**: Compuesto nacional y radares individuales calibrados en reflectividad (dBZ) procedentes del programa **ORD / OPERA (EUMETNET)**, con consulta de intensidad por píxel en tiempo real (0ms de latencia).
- **Rayos y Descargas Eléctricas (Blitzortung)**: Conexión push en directo con filtrado configurable por ventana temporal (1, 5 o 15 minutos), cálculo dinámico de tasa de impactos por minuto y diferenciación cromática por antigüedad.
- **Avisos Oficiales (AEMET Meteoalerta)**: Ingesta del feed oficial CAP/ATOM de avisos meteorológicos con polígonos vectoriales y niveles de severidad (amarillo, naranja, rojo).

### 🗺️ Interfaz GIS y Usabilidad
- **Inspector Multi-Capa en Hover**: Lectura unificada bajo el cursor de todas las capas activas de forma instantánea.
- **Jerarquía Visual de Paneles**: Control estricto de capas Leaflet (`z-index`) asegurando que los embalses, caudales y lluvias se visualicen ordenadamente sobre el mapa y las cuencas.
- **Transiciones Seamless**: Cambio fluido entre pestañas y modales sin parpadeos visuales ni animaciones disruptivas.
- **Persistencia de Preferencias**: Estado de capas activas, opacidades y última configuración guardados automáticamente en `LocalStorage`.

---

## 🛠️ Arquitectura Técnica

### Frontend
- **JavaScript Moderno (ES6+ Modules)**: Arquitectura desacoplada y orientada a eventos sin dependencias de frameworks pesados.
- **Leaflet.js**: Motor de mapas interactivos con *panes* personalizados para superposición precisa de capas vectoriales y ráster.
- **Diseño UI / CSS3**: Interfaz *Glassmorphism* oscura, animaciones optimizadas y diseño adaptativo.

### Backend
- **Python 3.10+ / FastAPI**: API asíncrona de alto rendimiento con documentación OpenAPI/Swagger automática.
- **PyProj**: Reproyección espacial de coordenadas geodésicas oficiales de EPSG:25830 (ETRS89 / UTM 30N) a EPSG:4326 (WGS84).
- **Caché Inteligente con TTL**: Sistema de caché centralizado de 5 minutos ($TTL = 300s$) que reduce a 3 peticiones cada 5 minutos la carga sobre los servidores de la CHJ.
- **Ingesta en Segundo Plano**: Workers para sincronización y procesamiento de datos ráster y vectoriales.

---

## 📂 Estructura del Repositorio

```
RainLoc/
├── backend/                        # Servidor API FastAPI
│   ├── app/
│   │   ├── api/v1/                 # Routers REST
│   │   │   ├── cuencas.py          # Geometrías y subsistemas CHJ
│   │   │   ├── saih.py             # Caudales, embalses y lluvias SAIH
│   │   │   ├── radar.py            # Radar meteorológico y reflectividad
│   │   │   ├── lightning.py        # Rayos y descargas eléctricas
│   │   │   ├── warnings.py         # Avisos AEMET Meteoalerta
│   │   │   └── models.py           # Modelos de predicción numérica
│   │   ├── services/               # Servicios de ingesta, cálculo y scraping
│   │   │   ├── saih_service.py     # Gestor de catálogos y consultas SAIH Júcar
│   │   │   ├── radar_worker.py     # Procesador de reflectividad e imágenes radar
│   │   │   ├── aemet_atom.py       # Lector del feed de alertas CAP/ATOM
│   │   │   ├── lightning_service.py# Receptor de descargas en vivo
│   │   │   └── weather_state.py    # Estado consolidado del tiempo
│   │   ├── config.py               # Configuración central, CORS y rutas
│   │   └── main.py                 # Instancia de FastAPI y middlewares
│   ├── data/                       # Archivos GeoJSON generados y cachés
│   ├── requirements.txt            # Dependencias de Python
│   └── run.py                      # Script de arranque con Uvicorn
├── js/                             # Lógica cliente (Frontend)
│   ├── app.js                      # Punto de entrada de la aplicación
│   ├── config.js                   # Catálogo de capas y constantes
│   ├── cuencas.js                  # Manejo de la capa vectorial de cuencas
│   ├── layerManager.js             # Ciclo de vida, renderizado y refresco de capas
│   ├── map.js                      # Inicialización del mapa Leaflet y panes
│   ├── multiLayerInspector.js      # Inspector unificado en hover
│   ├── storage.js                  # Gestor de persistencia en LocalStorage
│   └── ui.js                       # Controladores de interfaz y paneles
├── data/                           # Ficheros GeoJSON de respaldo
├── index.html                      # Vista principal de la aplicación
├── styles.css                      # Estilos visuales de la plataforma
└── .gitignore                      # Exclusiones de Git
```

---

## 🚀 Instalación y Puesta en Marcha

### 🐳 Despliegue Rápido con Docker Compose (Recomendado)

Todo el sistema (Frontend Nginx + Backend FastAPI) se despliega con un único comando exponiendo **únicamente el puerto 80**:

```bash
# Construir e iniciar los contenedores en segundo plano
docker compose up -d --build
```

Una vez levantado:
- 🌐 **Aplicación Web**: [http://localhost](http://localhost) (Puerto 80)
- 📖 **Documentación Swagger / API**: [http://localhost/docs](http://localhost/docs)
- 🩺 **Health check**: [http://localhost/health](http://localhost/health)

Para detener los contenedores:
```bash
docker compose down
```

---

### 💻 Despliegue Manual en Desarrollo

#### 1. Iniciar el Backend

```bash
# 1. Acceder al directorio backend
cd backend

# 2. Crear y activar el entorno virtual
python3 -m venv venv
source venv/bin/activate   # En Linux/macOS
# venv\Scripts\activate    # En Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Iniciar el servidor
python run.py
```

El backend estará escuchando en `http://localhost:8000`.

- **Documentación Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Documentación ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### 2. Iniciar el Frontend

En otra terminal, sirve la raíz del proyecto con cualquier servidor HTTP local:

```bash
# Opción 1: Con Python
python3 -m http.server 3000

# Opción 2: Con Node.js / npx
npx serve -l 3000 .
```

Abre tu navegador en `http://localhost:3000`.

---

## 📡 Endpoints de la API (v1)

| Método | Endpoint | Descripción |
| :--- | :--- | :--- |
| `GET` | `/health` | Comprobación de estado del servicio. |
| `GET` | `/api/v1/cuencas` | GeoJSON completo de subsistemas de la CHJ. |
| `GET` | `/api/v1/cuencas/sistemas` | Agrupación por sistemas de explotación. |
| `GET` | `/api/v1/saih/caudales` | GeoJSON de las 79 estaciones de caudal en ríos. |
| `GET` | `/api/v1/saih/caudales/{id}/historico` | Serie temporal de caudal histórico (24h/72h). |
| `GET` | `/api/v1/saih/embalses` | GeoJSON de embalses y presas de la cuenca. |
| `GET` | `/api/v1/saih/embalses/{id}/historico` | Serie temporal de volumen y cota de embalse. |
| `GET` | `/api/v1/saih/lluvias` | GeoJSON de las 182 estaciones pluviométricas y acumulados. |
| `GET` | `/api/v1/warnings/aemet` | Avisos meteorológicos activos de Meteoalerta. |
| `GET` | `/api/v1/radar/value-at` | Consulta puntual de reflectividad dBZ en coordenadas $(lat, lon)$. |
| `GET` | `/api/v1/lightning/live` | Flujo de rayos e impactos registrados en tiempo real. |

---

## 📜 Fuentes de Datos y Agradecimientos

- **CHJ (Confederación Hidrográfica del Júcar)**: Datos en tiempo real de la red [SAIH Júcar](https://saih.chj.es).
- **AEMET (Agencia Estatal de Meteorología)**: Avisos de fenómenos adversos [Meteoalerta](https://www.aemet.es).
- **EUMETNET / OPERA (Open Radar Data - ORD)**: Proveedor oficial de datos abiertos y compuestos de reflectividad de radar meteorológico.
- **Blitzortung.org**: Red comunitaria de localización de descargas eléctricas.
- **RadarSpain.es**: Proyecto de referencia e inspiración en visualización meteorológica ([RadarSpain.es](https://radarspain.es)).
