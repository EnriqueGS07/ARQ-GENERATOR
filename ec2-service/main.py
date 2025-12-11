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
MAX_TREE_LINES = 500
MAX_FILE_SIZE_KB = 50

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
                    "num_predict": max_tokens
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

def process_structure_with_llm(structure: Dict[str, Any]) -> Dict[str, Any]:
    """
    Componente 2: Procesador de lenguaje (Razonamiento Estructural)
    Usa LLM para interpretar la estructura, identificar patrones de arquitectura,
    clasificar componentes y detectar microservicios
    """
    tree_summary = structure["tree"][:4000]  # Limitar para el prompt
    key_files_summary = json.dumps(
        {k: v[:600] for k, v in list(structure["key_files"].items())[:10]},
        ensure_ascii=False, indent=1
    )
    
    prompt = f"""Analiza la siguiente estructura de repositorio e identifica patrones de arquitectura.

ESTRUCTURA DEL REPOSITORIO:
{tree_summary}

ARCHIVOS CLAVE:
{key_files_summary}

MÓDULOS DETECTADOS: {', '.join(structure['modules']) if structure['modules'] else 'Ninguno'}
TECNOLOGÍAS DETECTADAS: {', '.join(structure['technologies']) if structure['technologies'] else 'Desconocidas'}

TAREA: Analiza y clasifica la arquitectura del repositorio. Responde SOLO con un JSON válido con esta estructura exacta:

{{
    "components": ["lista de componentes principales encontrados"],
    "architecture_type": "tipo de arquitectura detectada (monolito, microservicios, modular, etc.)",
    "is_microservices": true/false,
    "modules": ["lista de módulos identificados"],
    "layers": ["lista de capas detectadas si las hay"],
    "relationships": [
        {{"from": "componente1", "to": "componente2", "type": "tipo de relación"}},
        {{"from": "componente2", "to": "componente3", "type": "dependencia"}}
    ],
    "technologies": ["tecnologías detectadas"],
    "patterns": ["patrones de diseño detectados si los hay"]
}}

REGLAS:
- SOLO incluye componentes que realmente existen en la estructura
- NO inventes componentes como "API Gateway", "Backend", "Database" si no están en la estructura
- Las relaciones deben basarse en la estructura real (jerarquía de directorios, imports, dependencias)
- Si hay múltiples módulos en el mismo nivel, probablemente son microservicios
- Responde SOLO con el JSON, sin explicaciones adicionales"""

    llm_output = call_ollama(prompt, max_tokens=1500)
    
    # Extraer JSON del output
    json_match = re.search(r'\{.*\}', llm_output, re.DOTALL)
    if json_match:
        try:
            analysis = json.loads(json_match.group())
            return analysis
        except json.JSONDecodeError:
            pass
    
    # Fallback: análisis básico
    return {
        "components": structure["modules"],
        "architecture_type": "unknown",
        "is_microservices": len(structure["modules"]) > 3,
        "modules": structure["modules"],
        "layers": [],
        "relationships": [],
        "technologies": structure["technologies"],
        "patterns": []
    }

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

def generate_mermaid_diagram(structure: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    """
    Componente 3: Generador de diagramas
    Genera código Mermaid a partir de la estructura extraída y el análisis del LLM
    Usa los resultados del razonamiento estructural para generar el diagrama
    """
    tree_summary = structure["tree"][:3000]
    components = analysis.get("components", structure["modules"])
    relationships = analysis.get("relationships", [])
    is_microservices = analysis.get("is_microservices", False)
    architecture_type = analysis.get("architecture_type", "unknown")
    layers = analysis.get("layers", [])
    
    prompt = f"""Genera un diagrama Mermaid de arquitectura basado en el análisis estructural realizado.

ANÁLISIS ESTRUCTURAL:
- Tipo de arquitectura: {architecture_type}
- Es microservicios: {is_microservices}
- Componentes identificados: {', '.join(components[:20])}
- Módulos: {', '.join(analysis.get('modules', [])[:15])}
- Capas detectadas: {', '.join(layers) if layers else 'Ninguna'}
- Relaciones identificadas: {len(relationships)} relaciones

RELACIONES DETECTADAS:
{json.dumps(relationships[:20], ensure_ascii=False, indent=2)}

ESTRUCTURA DEL REPOSITORIO (referencia):
{tree_summary}

INSTRUCCIONES PARA GENERAR EL DIAGRAMA:

1. SINTAXIS CORRECTA:
   - Primera línea: flowchart TD
   - Nodos: A[Nombre del componente]
   - Relaciones: A --> B (usa SOLO -->)
   - Indentación: 4 espacios

2. CONTENIDO DEL DIAGRAMA:
   - Incluye TODOS los componentes principales identificados
   - Incluye TODOS los módulos detectados
   - Crea relaciones basadas en el análisis estructural
   - Si hay capas, organízalas jerárquicamente
   - Si es microservicios, muestra cada servicio como nodo separado
   - El diagrama debe ser COMPLETO y útil

3. REGLAS ESTRICTAS:
   - SOLO usa componentes que están en la lista de componentes identificados
   - NO inventes: "API Gateway", "Backend", "Database", "Frontend" si no están en los componentes
   - Las relaciones deben reflejar las relaciones detectadas en el análisis
   - Genera un diagrama COMPLETO con al menos 10-20 nodos si hay suficiente estructura

4. EJEMPLO DE FORMATO:
```mermaid
flowchart TD
    A[Componente1]
    B[Componente2]
    C[Componente3]
    A --> B
    B --> C
```

Genera SOLO el código Mermaid válido, sin explicaciones adicionales:"""

    llm_output = call_ollama(prompt, max_tokens=2000)
    mermaid_code = extract_mermaid_code(llm_output)
    
    # Asegurar que empiece con flowchart TD
    if not mermaid_code.strip().startswith(('flowchart', 'graph')):
        mermaid_code = "flowchart TD\n" + mermaid_code
    
    return mermaid_code

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
        
        # PASO 2: Procesar con LLM para razonamiento estructural
        analysis = process_structure_with_llm(structure)
        
        # PASO 3: Generar diagrama Mermaid
        mermaid_code = generate_mermaid_diagram(structure, analysis)
        
        return {
            "mermaid": mermaid_code,
            "repo_size_mb": round(total_size, 2),
            "files_analyzed": len(structure["key_files"]),
            "modules_detected": len(structure["modules"]),
            "architecture_analysis": analysis
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
