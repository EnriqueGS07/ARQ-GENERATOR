# Architecture Generator

Sistema para generar diagramas Mermaid de arquitectura analizando repositorios Git mediante análisis estático y modelos de lenguaje local (LLM).

## 🏗️ Arquitectura del Sistema

### Visión General

El sistema está diseñado como una aplicación containerizada que se ejecuta en una instancia EC2, utilizando Docker Compose para orquestar dos servicios principales:

```
┌─────────────────────────────────────────────────────────┐
│              EC2 Instance (t3.large)                     │
│                                                           │
│  ┌───────────────────────────────────────────────────┐  │
│  │         Docker Compose Network                   │  │
│  │                                                   │  │
│  │  ┌───────────────────────────────────────────┐  │  │
│  │  │  architecture-generator                    │  │  │
│  │  │  - FastAPI Service (Puerto 8000)          │  │  │
│  │  │  - Imagen: Docker Hub                      │  │  │
│  │  │  - Módulos: api.py, extractor.py,          │  │  │
│  │  │             processor.py                   │  │  │
│  │  └───────────────┬───────────────────────────┘  │  │
│  │                  │ HTTP                          │  │
│  │                  ▼                               │  │
│  │  ┌───────────────────────────────────────────┐  │  │
│  │  │  ollama                                    │  │  │
│  │  │  - LLM Service (Puerto 11434)              │  │  │
│  │  │  - Modelo: llama3.2:3b-instruct-q4_0      │  │  │
│  │  │  - Volumen persistente para modelos        │  │  │
│  │  └───────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Componentes Principales

#### 1. **architecture-generator** (Servicio FastAPI)

Servicio principal que expone la API REST para análisis de repositorios.

**Características**:

- Framework: FastAPI (Python 3.11)
- Puerto: 8000
- Imagen: Almacenada en Docker Hub
- Timeout: 1200 segundos (20 minutos) para mantener conexiones activas

**Módulos**:

1. **`api.py`**: Capa de API REST

   - Endpoint `POST /analyze`: Analiza repositorio y genera diagrama
   - Endpoint `GET /health`: Health check del servicio
   - Validación de API key (opcional)
   - Manejo de CORS
   - Clonación temporal de repositorios Git

2. **`extractor.py`**: Análisis estático de repositorios

   - Extracción de estructura de directorios
   - Detección de archivos de configuración (package.json, pom.xml, etc.)
   - Identificación de dependencias
   - Detección de módulos y tecnologías
   - Filtrado de archivos irrelevantes (node_modules, .git, etc.)

3. **`processor.py`**: Generación de diagramas Mermaid
   - Construcción de prompts optimizados para LLM
   - Comunicación con Ollama API
   - Extracción y validación de código Mermaid
   - Manejo de errores y timeouts

**Variables de Entorno**:

- `OLLAMA_API_URL`: URL del servicio Ollama (default: `http://ollama:11434`)
- `OLLAMA_MODEL`: Modelo LLM a utilizar (default: `llama3.2:3b-instruct-q4_0`)
- `EC2_API_KEY`: API key opcional para autenticación

#### 2. **ollama** (Servicio LLM)

Servicio de modelos de lenguaje local para generar diagramas Mermaid.

**Características**:

- Imagen: `ollama/ollama:latest`
- Puerto: 11434
- Modelo: `llama3.2:3b-instruct-q4_0` (quantizado, 3B parámetros)
- Volumen persistente: `ollama-data` para almacenar modelos

**Modelo Utilizado**:

- **llama3.2:3b-instruct-q4_0**: Modelo quantizado optimizado para velocidad y eficiencia
- Tamaño: ~2GB
- Ideal para instancias EC2 con recursos limitados (t3.large)

### Flujo de Procesamiento

```
1. Cliente → POST /analyze
   {
     "repo_url": "https://github.com/user/repo.git",
     "depth": 1
   }

2. api.py
   ├── Valida API key (si está configurada)
   ├── Valida URL del repositorio
   └── Clona repositorio en directorio temporal

3. extractor.py
   ├── Analiza estructura de directorios
   ├── Identifica archivos clave (pom.xml, package.json, etc.)
   ├── Detecta dependencias y tecnologías
   └── Extrae módulos y componentes

4. processor.py
   ├── Construye prompt con estructura del repositorio
   ├── Llama a Ollama API (HTTP POST)
   ├── Espera respuesta del modelo LLM
   └── Extrae código Mermaid de la respuesta

5. api.py → Retorna respuesta
   {
     "mermaid": "flowchart TD\n    A[...] --> B[...]"
   }

6. Limpieza: Elimina directorio temporal del repositorio
```

### Arquitectura de Red

**Comunicación Interna**:

- Los contenedores se comunican a través de la red interna de Docker Compose
- `architecture-generator` accede a `ollama` mediante `http://ollama:11434`
- No se requiere exposición externa del puerto 11434 (solo para debugging)

**Comunicación Externa**:

- Puerto 8000 expuesto para acceso al servicio FastAPI
- Security Group de EC2 debe permitir tráfico en puerto 8000
- CORS configurado para permitir cualquier origen

### Persistencia de Datos

**Volúmenes Docker**:

- `ollama-data`: Almacena modelos de Ollama de forma persistente
- `./tmp:/app/tmp`: Directorio temporal para clonación de repositorios (montado desde host)

**Datos No Persistentes**:

- Repositorios clonados se eliminan después de cada análisis
- No se almacena información de repositorios analizados

## 📁 Estructura del Proyecto

```
ARQ-GENERATOR/
├── api.py                    # Módulo 3: API FastAPI (endpoints REST)
├── extractor.py              # Módulo 1: Análisis estático de repositorios
├── processor.py              # Módulo 2: Generación de diagramas Mermaid
├── Dockerfile                # Definición de imagen Docker del servicio
├── docker-compose.yml        # Orquestación de servicios Docker
├── requirements.txt          # Dependencias Python
├── README.md                 # Esta documentación
└── tmp/                      # Directorio temporal (volumen Docker)
```

### Separación de Responsabilidades

El código está organizado en tres módulos independientes:

1. **`extractor.py`**: Lógica de análisis estático

   - No depende de FastAPI ni Ollama
   - Funciones puras de análisis de archivos
   - Fácil de testear de forma aislada

2. **`processor.py`**: Lógica de generación con LLM

   - Comunicación con Ollama
   - Construcción de prompts
   - Extracción de código Mermaid
   - Independiente de la API

3. **`api.py`**: Capa de presentación
   - Endpoints REST
   - Validación de entrada
   - Orquestación de extractor y processor
   - Manejo de errores HTTP

## 🐳 Containerización

### Dockerfile

La imagen del servicio está basada en `python:3.11-slim` e incluye:

- Git para clonación de repositorios
- Dependencias Python desde `requirements.txt`
- Los tres módulos del servicio (`api.py`, `extractor.py`, `processor.py`)
- Uvicorn como servidor ASGI

### Docker Compose

Orquesta dos servicios:

```yaml
services:
  architecture-generator:
    image: usuario-dockerhub/architecture-generator:latest
    ports:
      - "8000:8000"
    environment:
      - OLLAMA_API_URL=http://ollama:11434
      - OLLAMA_MODEL=llama3.2:3b-instruct-q4_0
    depends_on:
      - ollama

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
```

**Ventajas de esta arquitectura**:

- Aislamiento de servicios
- Fácil escalabilidad horizontal
- Gestión simplificada de dependencias
- Versionado mediante imágenes Docker Hub

## 🔧 Configuración

### Requisitos de Infraestructura

**EC2 Instance**:

- Tipo: t3.large o superior (8GB+ RAM recomendado)
- Almacenamiento: 50GB mínimo (para modelos de Ollama)
- Sistema Operativo: Ubuntu 22.04 LTS o Amazon Linux 2023
- Docker y Docker Compose instalados

**Security Group**:

- Puerto 8000 abierto para acceso al servicio
- Puerto 11434 opcional (solo para debugging de Ollama)

### Variables de Entorno

**docker-compose.yml**:

```yaml
environment:
  - OLLAMA_API_URL=http://ollama:11434
  - OLLAMA_MODEL=llama3.2:3b-instruct-q4_0
  - EC2_API_KEY=${EC2_API_KEY:-} # Opcional
```

**Configuración mediante archivo .env**:

```bash
EC2_API_KEY=tu-api-key-secreta
```

### Límites y Restricciones

- **Tamaño máximo de repositorio**: 100MB (configurable en `api.py`)
- **Profundidad de clonación**: 1-3 niveles (configurable en request)
- **Timeout de Ollama**: 1200 segundos (20 minutos)
- **Tamaño de árbol**: Máximo 300 líneas (configurable en `extractor.py`)
- **Tamaño de archivo**: Máximo 40KB por archivo analizado

## 📡 API

### Endpoints

#### `POST /analyze`

Analiza un repositorio Git y genera un diagrama Mermaid.

**Request**:

```json
{
  "repo_url": "https://github.com/user/repo.git",
  "depth": 1
}
```

**Response**:

```json
{
  "mermaid": "flowchart TD\n    A[Frontend] --> B[API]..."
}
```

**Headers opcionales**:

- `X-API-Key`: API key si está configurada

#### `GET /health`

Health check del servicio.

**Response**:

```json
{
  "status": "ok",
  "ollama": "connected",
  "model": "llama3.2:3b-instruct-q4_0"
}
```

### Ejemplo de Uso

```bash
curl -X POST http://TU-EC2-IP:8000/analyze \
  -H "Content-Type: application/json" \
  -H "X-API-Key: tu-api-key" \
  -d '{
    "repo_url": "https://github.com/octocat/Hello-World.git",
    "depth": 1
  }'
```

## 🚀 Despliegue

### 1. Construir y Publicar Imagen

```bash
# Login en Docker Hub
docker login

# Construir imagen
docker build -t usuario-dockerhub/architecture-generator:latest .

# Publicar imagen
docker push usuario-dockerhub/architecture-generator:latest
```

### 2. Desplegar en EC2

```bash
# En EC2, crear directorio
mkdir -p ~/architecture-generator
cd ~/architecture-generator

# Crear docker-compose.yml (ver sección de configuración)

# Descargar imagen
docker-compose pull

# Iniciar servicios
docker-compose up -d

# Descargar modelo de Ollama
docker exec ollama ollama pull llama3.2:3b-instruct-q4_0

# Verificar
curl http://localhost:8000/health
```

### 3. Actualizar Servicio

```bash
# Reconstruir y publicar nueva versión
docker build -t usuario-dockerhub/architecture-generator:latest .
docker push usuario-dockerhub/architecture-generator:latest

# En EC2, actualizar
docker-compose pull
docker-compose up -d
```

## 🔍 Monitoreo y Troubleshooting

### Comandos Útiles

```bash
# Ver estado de contenedores
docker-compose ps

# Ver logs en tiempo real
docker-compose logs -f architecture-generator
docker-compose logs -f ollama

# Verificar espacio en disco
df -h
docker system df

# Verificar modelos de Ollama
docker exec ollama ollama list

# Health check
curl http://localhost:8000/health
```

### Problemas Comunes

**Puerto en uso**:

```bash
sudo lsof -i :8000
sudo lsof -i :11434
docker-compose down
```

**Espacio insuficiente**:

```bash
docker system prune -a --volumes -f
docker exec ollama ollama rm modelo-no-usado
```

**Ollama no responde**:

```bash
curl http://localhost:11434/api/tags
docker-compose restart ollama
```

## 💡 Características Técnicas

### Optimizaciones

- **Modelo quantizado**: Reduce uso de memoria y acelera inferencia
- **Análisis selectivo**: Solo analiza archivos relevantes
- **Clonación superficial**: Usa `depth=1` por defecto para repositorios grandes
- **Timeouts configurables**: Permite procesar repositorios complejos

### Seguridad

- API key opcional para autenticación
- Validación de URLs de repositorios (solo GitHub, GitLab, Bitbucket)
- Eliminación automática de repositorios temporales
- CORS configurado para desarrollo (ajustar para producción)

### Escalabilidad

- Arquitectura modular permite escalar componentes independientemente
- Docker Compose facilita agregar más instancias
- Modelos de Ollama compartidos mediante volúmenes persistentes
