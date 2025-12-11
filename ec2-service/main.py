# app/main.py
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import tempfile
import shutil
import os
import json
import re
from git import Repo
from pathlib import Path
import requests
from typing import Optional, Dict, List, Any

app = FastAPI(
    title="EC2 Architecture Generator Service",
    description="Servicio que analiza repositorios Git y genera diagramas Mermaid",
    version="2.0.0"
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b-instruct-q4_0")
API_KEY = os.getenv("EC2_API_KEY", "")
MAX_REPO_SIZE_MB = 100
MAX_TREE_LINES = 300  # Reducido para velocidad
MAX_FILE_SIZE_KB = 40  # Reducido para velocidad

# Security
security = HTTPBearer(auto_error=False)

def verify_api_key(
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> bool:
    """Verifica la API key"""
    if not API_KEY:
        return True
    
    if api_key and api_key == API_KEY:
        return True
    
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

# ============================================================================
# COMPONENTE 1: EXTRACTOR DE ESTRUCTURA DEL REPOSITORIO
# ============================================================================

def extract_repository_structure(repo_path: str, max_depth: int = 3) -> Dict[str, Any]:
    """
    Componente 1: Extractor de estructura del repositorio
    Analiza estáticamente el repositorio usando GitPython
    Retorna: estructura de archivos, dependencias, configuraciones y metadatos en formato JSON normalizado
    """
    structure = {
        "tree": [],
        "key_files": {},
        "dependencies": {},
        "modules": [],
        "config_files": [],
        "endpoints": [],
        "technologies": []
    }
    
    ignore_dirs = [
        "node_modules", ".venv", "venv", "env", "dist", "build", 
        "__pycache__", ".git", ".idea", ".vscode", ".pytest_cache",
        ".mypy_cache", "target", "bin", "obj", ".gradle"
    ]
    ignore_files = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".dylib"}
    
    # Patrones de archivos clave
    config_patterns = [
        "package.json", "requirements.txt", "Dockerfile", "docker-compose.yml",
        "pom.xml", "build.gradle", "Cargo.toml", "go.mod", "composer.json",
        "Gemfile", "Pipfile", ".env.example", "Makefile", "CMakeLists.txt"
    ]
    
    max_file_size = MAX_FILE_SIZE_KB * 1024
    lines = []
    modules = set()
    
    for root, dirs, files in os.walk(repo_path):
        # Filtrar directorios ignorados
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
        
        # Calcular profundidad
        rel = os.path.relpath(root, repo_path)
        if rel == ".":
            depth = 0
        else:
            depth = len(rel.split(os.sep))
            # Identificar módulos (directorios principales)
            if depth == 1:
                modules.add(os.path.basename(root))
        
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
        
        # Procesar archivos
        file_count = 0
        for f in sorted(files):
            if file_count >= 20:
                lines.append("  " * (depth + 1) + "... (más archivos)")
                break
            
            if f.startswith(".") or any(f.endswith(ext) for ext in ignore_files):
                continue
            
            lines.append("  " * (depth + 1) + f)
            file_count += 1
            
            # Extraer archivos clave y dependencias
            file_path = os.path.join(root, f)
            for pattern in config_patterns:
                if f.lower() == pattern.lower() or f.lower().startswith(pattern.lower()):
                    try:
                        if os.path.getsize(file_path) <= max_file_size:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as fh:
                                content = fh.read(2000)  # Primeros 2000 caracteres
                                rel_path = os.path.relpath(file_path, repo_path)
                                structure["key_files"][rel_path] = content
                                
                                # Detectar tipo de archivo de configuración
                                if pattern in ["pom.xml", "build.gradle", "package.json", "go.mod"]:
                                    structure["config_files"].append(rel_path)
                                    # Extraer dependencias básicas
                                    if "dependency" in content.lower() or "dependencies" in content.lower():
                                        structure["dependencies"][rel_path] = "detected"
                                    
                                    # Detectar tecnologías
                                    if "pom.xml" in pattern or "build.gradle" in pattern:
                                        structure["technologies"].append("Java")
                                    elif "package.json" in pattern:
                                        structure["technologies"].append("Node.js")
                                    elif "go.mod" in pattern:
                                        structure["technologies"].append("Go")
                                    elif "requirements.txt" in pattern or "Pipfile" in pattern:
                                        structure["technologies"].append("Python")
                    except Exception:
                        pass
                    break
    
    structure["tree"] = "\n".join(lines)
    structure["modules"] = sorted(list(modules))
    structure["technologies"] = list(set(structure["technologies"]))  # Eliminar duplicados
    
    return structure

# ============================================================================
# COMPONENTE 2: PROCESADOR DE LENGUAJE (Razonamiento Estructural)
# ============================================================================

def call_ollama(prompt: str, max_tokens: int = 2000) -> str:
    """Llama a la API de Ollama"""
    try:
        health_check = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        if health_check.status_code != 200:
            raise HTTPException(
                status_code=503,
                detail=f"Ollama no está disponible en {OLLAMA_API_URL}"
            )
        
        # Verificar que el modelo esté disponible
        models_response = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        if models_response.status_code == 200:
            models_data = models_response.json()
            available_models = [model.get("name", "") for model in models_data.get("models", [])]
            if OLLAMA_MODEL not in available_models:
                raise HTTPException(
                    status_code=503,
                    detail=f"Modelo '{OLLAMA_MODEL}' no está disponible. Ejecuta: ollama pull {OLLAMA_MODEL}"
                )
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"No se puede conectar con Ollama en {OLLAMA_API_URL}"
        )
    
    try:
        response = requests.post(
            f"{OLLAMA_API_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": max_tokens,
                    "num_thread": 2  # Optimizado para t3.large
                }
            },
            timeout=1200
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=503,
                detail=f"Modelo '{OLLAMA_MODEL}' no encontrado. Ejecuta: ollama pull {OLLAMA_MODEL}"
            )
        raise HTTPException(status_code=503, detail=f"Error HTTP de Ollama: {str(e)}")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Ollama no respondió en 20 minutos")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Error al conectar con Ollama: {str(e)}")

def generate_mermaid_directly(structure: Dict[str, Any]) -> str:
    """
    Componente 2 y 3 combinados: Genera diagrama Mermaid directamente
    Optimizado para velocidad - una sola llamada al LLM
    """
    tree_summary = structure["tree"][:2000]  # Reducido para ser más rápido
    modules = structure["modules"]
    technologies = structure["technologies"]
    
    # Construir lista de componentes reales (solo nombres, sin duplicados)
    components_list = []
    seen = set()
    
    # Agregar módulos
    for module in modules:
        if module and module not in seen:
            components_list.append(module)
            seen.add(module.lower())
    
    # Agregar directorios principales del árbol
    for line in tree_summary.split('\n')[:50]:  # Solo primeras 50 líneas
        line = line.strip()
        if line and not line.startswith('.') and '/' in line:
            dir_name = line.split('/')[0].strip()
            if dir_name and dir_name.lower() not in seen and len(dir_name) > 1:
                components_list.append(dir_name)
                seen.add(dir_name.lower())
    
    # Agregar archivos clave importantes
    for file_path in list(structure["key_files"].keys())[:5]:  # Solo 5 archivos
        file_name = os.path.basename(file_path)
        if file_name and file_name.lower() not in seen:
            components_list.append(file_name)
            seen.add(file_name.lower())
    
    prompt = f"""Genera un diagrama Mermaid de arquitectura basado en esta estructura de repositorio.

ESTRUCTURA DEL REPOSITORIO:
{tree_summary}

COMPONENTES REALES ENCONTRADOS:
{', '.join(components_list) if components_list else 'Ninguno detectado'}

MÓDULOS: {', '.join(modules) if modules else 'Ninguno'}
TECNOLOGÍAS: {', '.join(technologies) if technologies else 'Desconocidas'}

INSTRUCCIONES CRÍTICAS:

1. USA SOLO los nombres EXACTOS que aparecen en "COMPONENTES REALES ENCONTRADOS" y "MÓDULOS"
2. NO uses texto genérico como "MÓDULOS DETECTADOS", "Componente 1", "Capa 1", etc.
3. Si hay módulos como "drivers", "payments", "rides", "users" - usa esos nombres EXACTOS
4. Si hay archivos como "pom.xml", "README.md" - inclúyelos con sus nombres reales

SINTAXIS:
- Primera línea: flowchart TD
- Nodos: A[NombreReal] donde NombreReal es el nombre exacto del componente
- Relaciones: A --> B (solo -->)
- Indentación: 4 espacios

EJEMPLO CORRECTO (si hay módulos drivers, payments):
```mermaid
flowchart TD
    A[drivers]
    B[payments]
    C[rides]
    D[users]
    A --> E[pom.xml]
    B --> F[pom.xml]
```

EJEMPLO INCORRECTO (NO hagas esto):
```mermaid
flowchart TD
    A[MÓDULOS DETECTADOS]
    A --> B[Módulo 1]
```
(INCORRECTO: usa nombres genéricos en lugar de los reales)

Genera SOLO el código Mermaid válido usando los nombres REALES de los componentes:"""

    llm_output = call_ollama(prompt, max_tokens=2000)
    mermaid_code = extract_mermaid_code(llm_output)
    
    # Asegurar que empiece con flowchart TD
    if not mermaid_code.strip().startswith(('flowchart', 'graph')):
        mermaid_code = "flowchart TD\n" + mermaid_code
    
    return mermaid_code

# ============================================================================
# COMPONENTE 3: GENERADOR DE DIAGRAMAS
# ============================================================================

def extract_mermaid_code(text: str) -> str:
    """Extrae el código Mermaid del texto generado por el LLM"""
    mermaid_patterns = [
        r"```mermaid\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
        r"flowchart\s+TD\s*\n(.*?)(?=\n\n|\Z|```)",
        r"graph\s+TD\s*\n(.*?)(?=\n\n|\Z|```)",
    ]
    
    for pattern in mermaid_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            code = match.group(1).strip()
            if any(keyword in code.lower() for keyword in ["graph", "flowchart"]):
                return code
    
    # Buscar líneas que parezcan mermaid
    lines = text.split("\n")
    mermaid_lines = []
    in_mermaid = False
    
    for line in lines:
        line_stripped = line.strip()
        line_lower = line_stripped.lower()
        
        if any(keyword in line_lower for keyword in ["graph", "flowchart"]):
            in_mermaid = True
            mermaid_lines.append(line_stripped)
        elif in_mermaid:
            if line_stripped:
                mermaid_lines.append(line_stripped)
            elif len(mermaid_lines) > 2:
                break
    
    if mermaid_lines:
        return "\n".join(mermaid_lines)
    
    return text.strip()


# ============================================================================
# ENDPOINT PRINCIPAL
# ============================================================================

@app.post("/analyze", dependencies=[Depends(verify_api_key)])
def analyze(req: AnalyzeRequest):
    """Analiza un repositorio Git y genera un diagrama Mermaid de arquitectura"""
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
        
        # Verificar tamaño
        total_size = sum(
            os.path.getsize(os.path.join(dirpath, filename))
            for dirpath, dirnames, filenames in os.walk(tmp)
            for filename in filenames
        ) / (1024 * 1024)  # MB
        
        if total_size > MAX_REPO_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail=f"Repositorio demasiado grande ({total_size:.1f}MB). Máximo: {MAX_REPO_SIZE_MB}MB"
            )
        
        # PASO 1: Extraer estructura del repositorio
        structure = extract_repository_structure(tmp, max_depth=req.depth)
        
        # PASO 2: Generar diagrama Mermaid directamente (optimizado - una sola llamada LLM)
        mermaid_code = generate_mermaid_directly(structure)
        
        return {
            "mermaid": mermaid_code
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error interno: {str(e)}"
        )
    finally:
        try:
            shutil.rmtree(tmp)
        except Exception:
            pass

@app.get("/health")
def health():
    """Endpoint de salud"""
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
