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
MAX_TREE_LINES = 200  # Balance entre velocidad y contexto
MAX_FILE_SIZE_KB = 40  # Balance entre velocidad y contexto

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
                    "temperature": 0.1,  # Reducido para ser más determinista y evitar alucinaciones
                    "num_predict": 800  # Aumentado para permitir diagramas más completos
                }
            },
            timeout=1200  # 20 minutos para repositorios muy grandes
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
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

def fix_mermaid_syntax(mermaid_code: str) -> str:
    """Corrige errores comunes de sintaxis en diagramas Mermaid"""
    if not mermaid_code:
        return "flowchart TD\n    A[Empty]"
    
    lines = mermaid_code.split('\n')
    fixed_lines = []
    has_declaration = False
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Verificar si tiene declaración de tipo de diagrama
        if line.lower().startswith(('flowchart', 'graph', 'classdiagram', 'sequencediagram')):
            has_declaration = True
            # Asegurar que sea flowchart TD (más común y simple)
            if not line.lower().startswith('flowchart'):
                if 'graph' in line.lower():
                    fixed_lines.append('flowchart TD')
                else:
                    fixed_lines.append('flowchart TD')
            else:
                # Asegurar que tenga dirección (TD, LR, etc.)
                if 'TD' not in line.upper() and 'LR' not in line.upper() and 'BT' not in line.upper() and 'RL' not in line.upper():
                    fixed_lines.append('flowchart TD')
                else:
                    fixed_lines.append(line)
            continue
        
        # Corregir sintaxis de relaciones
        # Reemplazar ->> por --> (sintaxis de sequenceDiagram en flowchart)
        if '->>' in line:
            line = line.replace('->>', '-->')
        
        # Corregir relaciones sin formato correcto
        if '->' in line or '-->' in line or '---' in line:
            # Asegurar formato correcto: A --> B
            parts = re.split(r'(-+>|->|--)', line)
            if len(parts) >= 3:
                # Limpiar espacios
                left = parts[0].strip()
                arrow = parts[1]
                right = parts[2].strip()
                
                # Normalizar flecha a -->
                if arrow != '-->':
                    arrow = '-->'
                
                # Asegurar que los nodos tengan formato correcto
                # Si no tienen [], agregarlos
                if not left.startswith('[') and not left[0].isupper():
                    # Es un ID, buscar si tiene label
                    if '[' in left:
                        left = left
                    else:
                        # Extraer ID si tiene formato ID[Label]
                        if '[' in left:
                            left = left
                        else:
                            left = f"{left}[{left}]"
                
                if not right.startswith('[') and not right[0].isupper():
                    if '[' in right:
                        right = right
                    else:
                        # Extraer ID si tiene formato ID[Label]
                        if '[' in right:
                            right = right
                        else:
                            right = f"{right}[{right}]"
                
                line = f"    {left}{arrow}{right}"
        
        # Corregir nodos sin formato correcto
        elif '[' in line or ']' in line or '(' in line or ')' in line or '{' in line or '}' in line:
            # Es un nodo, asegurar formato correcto
            if not line.startswith('    '):
                line = '    ' + line.lstrip()
            
            # Asegurar que tenga formato: ID[Label] o ID(Label) o ID{Label}
            if '[' in line and ']' not in line:
                line = line + ']'
            elif ']' in line and '[' not in line:
                line = '[' + line
        
        # Agregar línea si no está vacía
        if line:
            fixed_lines.append(line)
    
    # Si no tiene declaración, agregarla
    if not has_declaration:
        fixed_lines.insert(0, 'flowchart TD')
    
    result = '\n'.join(fixed_lines)
    
    # Validación final: asegurar que tenga al menos un nodo
    if not re.search(r'\[.*?\]|\(.*?\)|\{.*?\}', result):
        result = "flowchart TD\n    A[Repository]"
    
    return result

def validate_mermaid_nodes(mermaid_code: str, tree_txt: str) -> str:
    """Valida que los nodos del diagrama Mermaid existan en la estructura"""
    import re
    
    # Extraer nombres de nodos del diagrama Mermaid
    node_pattern = r'\[([^\]]+)\]|\(([^\)]+)\)|\{([^\}]+)\}|([A-Z]\w+)\['
    nodes_in_diagram = []
    for match in re.finditer(node_pattern, mermaid_code):
        node = match.group(1) or match.group(2) or match.group(3) or match.group(4)
        if node:
            nodes_in_diagram.append(node.strip())
    
    # Extraer nombres de directorios/archivos de la estructura
    structure_items = set()
    for line in tree_txt.split('\n'):
        line = line.strip()
        if line and not line.startswith('.'):
            # Extraer nombres de directorios y archivos
            parts = line.split('/')
            for part in parts:
                part = part.strip().rstrip('/').rstrip('.md').rstrip('.xml').rstrip('.json')
                if part and part != '.':
                    structure_items.add(part.lower())
    
    # Palabras prohibidas que indican alucinaciones
    forbidden_words = [
        'api gateway', 'gateway', 'aws lambda', 'lambda', 'serverless',
        'backend', 'back-end', 'back end', 'frontend', 'front-end', 'front end',
        'database', 'db', 'postgresql', 'mysql', 'mongodb', 'redis',
        'server', 'api', 'rest api', 'graphql', 'microservice'
    ]
    
    # Filtrar nodos que no existen en la estructura o contienen palabras prohibidas
    valid_lines = []
    lines = mermaid_code.split('\n')
    
    for line in lines:
        line_lower = line.lower()
        
        # Si es la declaración del diagrama, mantenerla
        if line_lower.strip().startswith(('flowchart', 'graph')):
            valid_lines.append(line)
            continue
        
        # Verificar si contiene palabras prohibidas
        has_forbidden = any(fw in line_lower for fw in forbidden_words)
        
        # Verificar si el nodo existe en la estructura
        node_match = re.search(node_pattern, line)
        if node_match:
            node = (node_match.group(1) or node_match.group(2) or 
                   node_match.group(3) or node_match.group(4) or '').strip().lower()
            
            # Si tiene palabra prohibida, omitir la línea
            if has_forbidden:
                continue
            
            # Verificar si el nodo existe en la estructura (búsqueda flexible)
            node_exists = any(
                node in item or item in node or 
                node.replace(' ', '').replace('-', '').replace('_', '') in item.replace(' ', '').replace('-', '').replace('_', '')
                for item in structure_items
            )
            
            if node_exists or not node:  # Mantener si existe o es un ID sin label
                valid_lines.append(line)
        else:
            # Si no tiene nodo pero es una relación válida, mantenerla
            if '-->' in line or '---' in line:
                valid_lines.append(line)
    
    result = '\n'.join(valid_lines)
    
    # Si se eliminaron demasiadas líneas, generar un diagrama simple
    if len(valid_lines) < 3:
        # Extraer solo los primeros 10 items reales de la estructura
        real_items = list(structure_items)[:10]
        if real_items:
            simple_diagram = "flowchart TD\n"
            for i, item in enumerate(real_items):
                simple_diagram += f"    A{i}[{item}]\n"
            return simple_diagram
    
    return result

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
        
        # Construir prompt optimizado para evitar alucinaciones
        # Incluir más contexto pero limitado
        tree_txt_limited = tree_txt[:2500]  # Aumentado para más contexto
        key_files_limited = {k: v[:500] for k, v in list(key_files.items())[:7]}  # Más archivos para mejor contexto
        
        prompt = f"""Genera un diagrama Mermaid SOLO con lo que EXISTE en este repositorio.

ESTRUCTURA REAL DEL REPOSITORIO:
{tree_txt_limited}

ARCHIVOS ENCONTRADOS:
{json.dumps(key_files_limited, ensure_ascii=False, indent=1)}

REGLAS ABSOLUTAS (NO LAS VIOLES):

1. PROHIBIDO inventar estos componentes (NUNCA los uses):
   - "Amazon API Gateway", "API Gateway", "Gateway", "AWS Lambda", "Lambda"
   - "Backend", "Back-end", "Frontend", "Front-end", "Server"
   - "Database", "DB", "PostgreSQL", "MySQL", "MongoDB"
   - "API", "REST API", "GraphQL", "Microservice"
   - Cualquier componente que NO aparezca en la estructura de arriba

2. SOLO puedes usar:
   - Nombres EXACTOS de directorios que aparecen en la estructura
   - Nombres EXACTOS de archivos que aparecen en la estructura
   - Nada más

3. Sintaxis CORRECTA para flowchart:
   - Primera línea: flowchart TD
   - Nodos: A[Nombre exacto] o A[directorio]
   - Relaciones: A --> B (SOLO si B está dentro de A en la estructura)
   - NO uses: ->>, ---, -.-, ni otros símbolos
   - NO mezcles tipos de nodos: usa solo [ ] para todos

4. Si la estructura muestra:
   - drivers/ → puedes usar: A[drivers]
   - payments/ → puedes usar: B[payments]
   - pom.xml → puedes usar: C[pom.xml]
   - README.md → puedes usar: D[README.md]

EJEMPLO CORRECTO (si estructura tiene: drivers/, payments/, pom.xml):
```mermaid
flowchart TD
    A[drivers]
    B[payments]
    C[pom.xml]
```

EJEMPLO INCORRECTO (NUNCA hagas esto):
```mermaid
flowchart TD
    A[Amazon API Gateway] --> B[Backend]
    B --> C[Database]
```
(INCORRECTO porque "Amazon API Gateway", "Backend" y "Database" NO están en la estructura)

Genera SOLO el código Mermaid válido, sin explicaciones:"""

        # Llamar a Ollama
        llm_output = call_ollama(prompt)
        
        # Extraer código Mermaid
        mermaid_code = extract_mermaid_code(llm_output)
        
        if not mermaid_code:
            raise HTTPException(
                status_code=500,
                detail="No se pudo generar un diagrama Mermaid válido"
            )
        
        # Corregir sintaxis
        mermaid_code = fix_mermaid_syntax(mermaid_code)
        
        # Validar nodos y filtrar alucinaciones
        mermaid_code = validate_mermaid_nodes(mermaid_code, tree_txt)
        
        # Corregir sintaxis nuevamente después de la validación
        mermaid_code = fix_mermaid_syntax(mermaid_code)
        
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
