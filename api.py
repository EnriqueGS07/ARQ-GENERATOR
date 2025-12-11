from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import tempfile
import shutil
import os
from git import Repo
import requests
from typing import Optional
from extractor import extract_repository_structure
from processor import generate_mermaid_diagram

app = FastAPI(
    title="Architecture Generator Service",
    description="Servicio que analiza repositorios Git y genera diagramas Mermaid",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b-instruct-q4_0")
API_KEY = os.getenv("API_KEY", "")
MAX_REPO_SIZE_MB = 100

security = HTTPBearer(auto_error=False)

def verify_api_key(
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> bool:
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
    valid_prefixes = ("http://", "https://", "git@", "git://")
    return url.startswith(valid_prefixes) and ("github.com" in url or "gitlab.com" in url or "bitbucket.org" in url or url.endswith(".git"))

@app.post("/analyze", dependencies=[Depends(verify_api_key)])
def analyze(req: AnalyzeRequest):
    if not validate_repo_url(req.repo_url):
        raise HTTPException(
            status_code=400, 
            detail="URL de repositorio inválida. Debe ser de GitHub, GitLab o Bitbucket."
        )
    
    tmp = tempfile.mkdtemp(prefix="repo_analyze_")
    
    try:
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
        
        total_size = sum(
            os.path.getsize(os.path.join(dirpath, filename))
            for dirpath, dirnames, filenames in os.walk(tmp)
            for filename in filenames
        ) / (1024 * 1024)
        
        if total_size > MAX_REPO_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail=f"Repositorio demasiado grande ({total_size:.1f}MB). Máximo: {MAX_REPO_SIZE_MB}MB"
            )
        
        structure = extract_repository_structure(tmp, max_depth=req.depth)
        mermaid_code = generate_mermaid_diagram(structure)
        
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

