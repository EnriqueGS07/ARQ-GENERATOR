import os
import json
import re
import requests
from typing import Dict, Any
from fastapi import HTTPException

OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b-instruct-q4_0")

def call_ollama(prompt: str, max_tokens: int = 2000) -> str:
    try:
        health_check = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        if health_check.status_code != 200:
            raise HTTPException(
                status_code=503,
                detail=f"Ollama no está disponible en {OLLAMA_API_URL}"
            )
        
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
                    "num_thread": 2
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

def extract_mermaid_code(text: str) -> str:
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

def generate_mermaid_diagram(structure: Dict[str, Any]) -> str:
    tree_summary = structure["tree"][:2000]
    modules = structure["modules"]
    technologies = structure["technologies"]
    
    components_list = []
    seen = set()
    
    for module in modules:
        if module and module not in seen:
            components_list.append(module)
            seen.add(module.lower())
    
    for line in tree_summary.split('\n')[:50]:
        line = line.strip()
        if line and not line.startswith('.') and '/' in line:
            dir_name = line.split('/')[0].strip()
            if dir_name and dir_name.lower() not in seen and len(dir_name) > 1:
                components_list.append(dir_name)
                seen.add(dir_name.lower())
    
    for file_path in list(structure["key_files"].keys())[:5]:
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
    
    if not mermaid_code.strip().startswith(('flowchart', 'graph')):
        mermaid_code = "flowchart TD\n" + mermaid_code
    
    return mermaid_code

