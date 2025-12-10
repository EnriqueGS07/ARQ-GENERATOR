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
# Para t2.large, usar modelo quantizado: llama3:8b-instruct-q4_0
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
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
    try:
        response = requests.post(
            f"{OLLAMA_API_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 2000
                }
            },
            timeout=400
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
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
        
        # Construir prompt mejorado
        prompt = f"""Analiza la siguiente estructura de repositorio y genera un diagrama de arquitectura en formato Mermaid.

Estructura del repositorio:
{tree_txt}

Archivos clave (extractos):
{json.dumps(key_files, ensure_ascii=False, indent=2)}

Genera UN diagrama MERMAID (usa flowchart TD o graph TD) que muestre SOLO lo que realmente existe en el repositorio:

REGLAS ESTRICTAS:
- SOLO incluye componentes, archivos, módulos o servicios que realmente existen en la estructura mostrada arriba
- NO inventes ni asumas componentes que no están presentes
- Si el repositorio solo tiene un README, muestra solo el README
- Si no hay servicios, controladores o bases de datos, NO los incluyas
- Si hay archivos de configuración (package.json, requirements.txt, Dockerfile, etc.), inclúyelos
- Si hay directorios con código, muéstralos como módulos
- Las relaciones deben basarse en imports, dependencias o estructura real del código

Genera el diagrama mostrando:
1. Archivos y directorios principales que existen
2. Relaciones entre archivos basadas en imports o dependencias reales (si las hay)
3. Configuraciones o dependencias externas mencionadas en archivos de configuración
4. NADA MÁS - solo lo que realmente existe

IMPORTANTE: 
- Devuelve SOLO el código Mermaid, sin explicaciones adicionales
- Si el repositorio es muy simple, el diagrama será simple (está bien)
- NO inventes componentes para hacer el diagrama más complejo
- Usa nombres descriptivos y claros para los nodos
- Incluye estilos básicos si es necesario
- El diagrama debe ser claro y legible

Formato esperado:
```mermaid
flowchart TD
    ...
```"""

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
