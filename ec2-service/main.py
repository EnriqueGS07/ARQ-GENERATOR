# app/main.py
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import tempfile
import shutil
import os
import json
import re
from git import Repo
from pathlib import Path
import requests
from typing import Optional

app = FastAPI(
    title="EC2 Architecture Generator Service",
    description="Servicio que analiza repositorios Git y genera diagramas Mermaid",
    version="1.0.0"
)

OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
# Modelo optimizado para velocidad: llama3.2:3b-instruct-q4_0 (más rápido que llama3:8b)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b-instruct-q4_0")
API_KEY = os.getenv("EC2_API_KEY", "")
MAX_REPO_SIZE_MB = 100
MAX_TREE_LINES = 500  # Aumentado para diagramas más completos
MAX_FILE_SIZE_KB = 50  # Aumentado para más contexto

# Security
security = HTTPBearer(auto_error=False)

def verify_api_key(
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> bool:
    """
    Verifica la API key. Si no está configurada, permite acceso.
    Si está configurada, requiere que coincida.
    """
    if not API_KEY:
        # Si no hay API key configurada, permitir acceso
        return True
    
    # Verificar header X-API-Key
    if api_key and api_key == API_KEY:
        return True
    
    # Verificar Bearer token
    if credentials and credentials.credentials == API_KEY:
        return True
    
    raise HTTPException(
        status_code=401,
        detail="API key inválida o faltante"
    )

class AnalyzeRequest(BaseModel):
    repo_url: str = Field(..., description="URL del repositorio Git a analizar")
    depth: int = Field(default=1, ge=1, le=3, description="Profundidad del clone (1-3)")

def validate_repo_url(url: str) -> bool:
    """Valida que la URL sea un repositorio Git válido"""
    valid_prefixes = ("http://", "https://", "git@", "git://")
    return url.startswith(valid_prefixes) and ("github.com" in url or "gitlab.com" in url or "bitbucket.org" in url or url.endswith(".git"))

def repo_tree_text(repo_path: str, max_depth: int = 3, ignore_dirs: Optional[list] = None) -> str:
    """Genera un árbol de texto de la estructura del repositorio"""
    ignore_dirs = ignore_dirs or [
        "node_modules", ".venv", "venv", "env", "dist", "build", 
        "__pycache__", ".git", ".idea", ".vscode", ".pytest_cache",
        ".mypy_cache", "target", "bin", "obj", ".gradle"
    ]
    ignore_files = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".dylib"}
    
    lines = []
    lines.append(".")
    
    for root, dirs, files in os.walk(repo_path):
        # Filtrar directorios ignorados
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
        
        # Calcular profundidad
        rel = os.path.relpath(root, repo_path)
        if rel == ".":
            depth = 0
        else:
            depth = len(rel.split(os.sep))
        
        # Limitar profundidad
        if depth > max_depth:
            dirs.clear()
            continue
        
        # Limitar número de líneas
        if len(lines) >= MAX_TREE_LINES:
            lines.append("  " * (depth + 1) + "... (truncado)")
            break
        
        # Agregar directorio
        if rel != ".":
            lines.append("  " * depth + os.path.basename(root) + "/")
        
        # Agregar archivos (limitados)
        file_count = 0
        for f in sorted(files):
            if file_count >= 20:  # Máximo 20 archivos por directorio
                lines.append("  " * (depth + 1) + "... (más archivos)")
                break
            
            # Ignorar archivos binarios y ocultos
            if f.startswith(".") or any(f.endswith(ext) for ext in ignore_files):
                continue
            
            lines.append("  " * (depth + 1) + f)
            file_count += 1
    
    return "\n".join(lines)

def read_key_files(repo_path: str, max_depth: int = 2, patterns: Optional[list] = None) -> dict:
    """Lee archivos clave del repositorio"""
    patterns = patterns or [
        "README", "package.json", "requirements.txt", "Dockerfile", 
        "docker-compose.yml", "pom.xml", "build.gradle", "Cargo.toml",
        "go.mod", "composer.json", "Gemfile", "Pipfile", ".env.example"
    ]
    
    extracted = {}
    max_file_size = MAX_FILE_SIZE_KB * 1024
    
    for root, dirs, files in os.walk(repo_path):
        # Limitar profundidad
        rel = os.path.relpath(root, repo_path)
        if rel != ".":
            depth = len(rel.split(os.sep))
            if depth > max_depth:
                dirs.clear()
                continue
        
        for f in files:
            for pattern in patterns:
                if f.lower().startswith(pattern.lower()) or f.lower() == pattern.lower():
                    file_path = os.path.join(root, f)
                    try:
                        # Verificar tamaño
                        if os.path.getsize(file_path) > max_file_size:
                            continue
                        
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as fh:
                            content = fh.read(5000)  # Primeros 5000 caracteres
                        
                        rel_path = os.path.relpath(file_path, repo_path)
                        extracted[rel_path] = content
                        break  # No procesar el mismo archivo múltiples veces
                    except Exception:
                        pass
    
    return extracted

def call_ollama(prompt: str) -> str:
    """Llama a la API de Ollama para generar el diagrama Mermaid"""
    # Verificar que Ollama esté disponible
    try:
        health_check = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        if health_check.status_code != 200:
            raise HTTPException(
                status_code=503,
                detail=f"Ollama no está disponible. Verifica que el servicio esté corriendo en {OLLAMA_API_URL}"
            )
        
        # Verificar que el modelo esté disponible
        models_response = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        if models_response.status_code == 200:
            models_data = models_response.json()
            available_models = [model.get("name", "") for model in models_data.get("models", [])]
            if OLLAMA_MODEL not in available_models:
                raise HTTPException(
                    status_code=503,
                    detail=f"Modelo '{OLLAMA_MODEL}' no está disponible. Modelos disponibles: {', '.join(available_models[:5])}. Ejecuta: ollama pull {OLLAMA_MODEL}"
                )
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"No se puede conectar con Ollama en {OLLAMA_API_URL}. Verifica que el servicio esté corriendo."
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"Error al verificar Ollama: {str(e)}"
        )
    
    # Llamar a la API de generación
    try:
        response = requests.post(
            f"{OLLAMA_API_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # Reducido para ser más determinista y evitar alucinaciones
                    "num_predict": 2000  # Aumentado significativamente para diagramas grandes y completos
                }
            },
            timeout=1200  # 20 minutos para repositorios muy grandes
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=503,
                detail=f"Modelo '{OLLAMA_MODEL}' no encontrado. Ejecuta en la EC2: ollama pull {OLLAMA_MODEL}"
            )
        raise HTTPException(
            status_code=503,
            detail=f"Error HTTP de Ollama: {str(e)}"
        )
    except requests.exceptions.Timeout as e:
        raise HTTPException(
            status_code=504,  # Gateway Timeout
            detail=f"Ollama no respondió en 20 minutos. El repositorio es muy grande o el modelo está lento."
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503, 
            detail=f"Error al conectar con Ollama: {str(e)}"
        )

def extract_mermaid_code(text: str) -> str:
    """Extrae el código Mermaid del texto generado por el LLM"""
    # Buscar bloques de código mermaid
    mermaid_patterns = [
        r"```mermaid\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
        r"mermaid\s*\n(.*?)(?:\n\n|\Z)",
    ]
    
    for pattern in mermaid_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            code = match.group(1).strip()
            # Verificar que contiene sintaxis mermaid
            if any(keyword in code.lower() for keyword in ["graph", "flowchart", "classDiagram", "sequenceDiagram"]):
                return code
    
    # Si no se encuentra un bloque, buscar líneas que parezcan mermaid
    lines = text.split("\n")
    mermaid_lines = []
    in_mermaid = False
    
    for line in lines:
        line_lower = line.lower().strip()
        if any(keyword in line_lower for keyword in ["graph", "flowchart", "classDiagram", "sequenceDiagram"]):
            in_mermaid = True
        
        if in_mermaid:
            mermaid_lines.append(line)
            # Detener si encontramos un bloque completo
            if line.strip().endswith(";") and len(mermaid_lines) > 3:
                break
    
    if mermaid_lines:
        return "\n".join(mermaid_lines)
    
    # Si todo falla, devolver el texto completo
    return text.strip()

@app.post("/analyze", dependencies=[Depends(verify_api_key)])
def analyze(req: AnalyzeRequest):
    """Analiza un repositorio Git y genera un diagrama Mermaid de arquitectura"""
    # Validar URL
    if not validate_repo_url(req.repo_url):
        raise HTTPException(
            status_code=400, 
            detail="URL de repositorio inválida. Debe ser de GitHub, GitLab o Bitbucket."
        )
    
    tmp = tempfile.mkdtemp(prefix="repo_analyze_")
    
    try:
        # Clonar repositorio
        try:
            Repo.clone_from(
                req.repo_url, 
                tmp, 
                depth=req.depth,
                single_branch=True
            )
        except Exception as e:
            raise HTTPException(
                status_code=400, 
                detail=f"Error al clonar repositorio: {str(e)}"
            )
        
        # Verificar tamaño del repositorio
        total_size = sum(
            os.path.getsize(os.path.join(dirpath, filename))
            for dirpath, dirnames, filenames in os.walk(tmp)
            for filename in filenames
        ) / (1024 * 1024)  # MB
        
        if total_size > MAX_REPO_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail=f"Repositorio demasiado grande ({total_size:.1f}MB). Máximo permitido: {MAX_REPO_SIZE_MB}MB"
            )
        
        # Generar estructura y leer archivos clave
        tree_txt = repo_tree_text(tmp, max_depth=req.depth)
        key_files = read_key_files(tmp, max_depth=req.depth)
        
        # Construir prompt optimizado - incluir más contexto para diagramas completos
        tree_txt_limited = tree_txt[:5000]  # Aumentado significativamente para diagramas más completos
        key_files_limited = {k: v[:800] for k, v in list(key_files.items())[:15]}  # Más archivos y más contenido
        
        prompt = f"""Genera un diagrama Mermaid SOLO con lo que EXISTE en este repositorio.

ESTRUCTURA REAL DEL REPOSITORIO:
{tree_txt_limited}

ARCHIVOS ENCONTRADOS:
{json.dumps(key_files_limited, ensure_ascii=False, indent=1)}

REGLAS ABSOLUTAS:

1. PROHIBIDO inventar componentes que NO están en la estructura:
   - NO "Amazon API Gateway", "API Gateway", "Gateway", "AWS Lambda"
   - NO "Backend", "Frontend", "Server"
   - NO "Database", "DB", "PostgreSQL", "MySQL"
   - NO "API", "REST API", "GraphQL", "Microservice"
   - SOLO usa nombres EXACTOS de directorios/archivos que aparecen arriba

2. VALIDACIÓN DE SINTAXIS (VERIFICA ANTES DE RESPONDER):
   - Primera línea DEBE ser: flowchart TD
   - Nodos: formato A[Nombre] donde A es un ID y Nombre es el directorio/archivo
   - Relaciones: A --> B (usa SOLO -->, NO uses ->>, ---, -.-)
   - Indentación: 4 espacios antes de cada línea (excepto la primera)
   - Cada nodo en su propia línea
   - Cada relación en su propia línea
   - NO mezcles tipos de nodos: usa solo [ ] para todos

3. GENERA UN DIAGRAMA COMPLETO:
   - Incluye TODOS los directorios principales que aparecen en la estructura
   - Incluye TODOS los archivos importantes (pom.xml, package.json, README, etc.)
   - Crea relaciones jerárquicas: si B está dentro de A, entonces A --> B
   - Crea relaciones entre módulos relacionados (drivers, payments, rides, users si existen)
   - El diagrama debe ser COMPLETO, no minimalista
   - Incluye al menos 10-20 nodos si hay suficiente estructura

4. ANTES DE RESPONDER, VERIFICA:
   ✓ ¿Empieza con "flowchart TD"?
   ✓ ¿Todos los nodos tienen formato ID[Nombre]?
   ✓ ¿Todas las relaciones usan --> (no ->>, ---, etc.)?
   ✓ ¿Todos los nombres existen en la estructura?
   ✓ ¿No hay componentes inventados?

EJEMPLO CORRECTO (diagrama completo):
```mermaid
graph TB
    Amazon_API_Gateway[Amazon API Gateway]
    backend_data[Backend Data]
    drivers[Drivers Service]
    payments[Payments Service]
    rides[Rides Service]
    users[Users Service]

    %% Flujo principal
    Amazon_API_Gateway --> backend_data

    backend_data --> drivers
    backend_data --> payments
    backend_data --> rides
    backend_data --> users

    %% Dependencias Maven
    drivers --> drivers_pom_dep[drivers/dependency-reduced-pom.xml]
    payments --> payments_pom_dep[payments/dependency-reduced-pom.xml]
    rides --> rides_pom_dep[rides/dependency-reduced-pom.xml]
    users --> users_pom_dep[users/dependency-reduced-pom.xml]

    drivers --> drivers_pom[drivers/pom.xml]
    payments --> payments_pom[payments/pom.xml]
    rides --> rides_pom[rides/pom.xml]
    users --> users_pom[users/pom.xml]

    %% API REST expuesta por API Gateway
    Amazon_API_Gateway --> drivers
    Amazon_API_Gateway --> payments
    Amazon_API_Gateway --> rides
    Amazon_API_Gateway --> users
```

EJEMPLO INCORRECTO (NO hagas esto):
```mermaid
flowchart TD
    A[Amazon API Gateway] ->> B[Backend]
    B --- C[Database]
```
(INCORRECTO: componentes inventados y sintaxis incorrecta)

IMPORTANTE: Genera un diagrama COMPLETO que incluya todos los componentes principales. 
No seas minimalista - incluye todos los directorios, módulos y archivos importantes.
El diagrama debe ser útil y completo, mostrando la estructura real del repositorio.

Genera SOLO el código Mermaid válido y verificado, sin explicaciones:"""

        # Llamar a Ollama
        llm_output = call_ollama(prompt)
        
        # Extraer código Mermaid
        mermaid_code = extract_mermaid_code(llm_output)
        
        if not mermaid_code:
            raise HTTPException(
                status_code=500,
                detail="No se pudo generar un diagrama Mermaid válido"
            )
        
        return {
            "mermaid": mermaid_code,
            "repo_size_mb": round(total_size, 2),
            "files_analyzed": len(key_files)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error interno: {str(e)}"
        )
    finally:
        # Limpiar directorio temporal
        try:
            shutil.rmtree(tmp)
        except Exception:
            pass

@app.get("/health")
def health():
    """Endpoint de salud para verificar que el servicio está funcionando"""
    try:
        response = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        ollama_status = "connected" if response.status_code == 200 else "disconnected"
    except Exception:
        ollama_status = "disconnected"
    
    return {
        "status": "ok",
        "ollama": ollama_status,
        "model": OLLAMA_MODEL
    }
