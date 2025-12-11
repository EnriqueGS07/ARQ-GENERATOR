import os
from typing import Dict, Any

MAX_TREE_LINES = 300
MAX_FILE_SIZE_KB = 40

def extract_repository_structure(repo_path: str, max_depth: int = 3) -> Dict[str, Any]:
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
    
    config_patterns = [
        "package.json", "requirements.txt", "Dockerfile", "docker-compose.yml",
        "pom.xml", "build.gradle", "Cargo.toml", "go.mod", "composer.json",
        "Gemfile", "Pipfile", ".env.example", "Makefile", "CMakeLists.txt"
    ]
    
    max_file_size = MAX_FILE_SIZE_KB * 1024
    lines = []
    modules = set()
    
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
        
        rel = os.path.relpath(root, repo_path)
        if rel == ".":
            depth = 0
        else:
            depth = len(rel.split(os.sep))
            if depth == 1:
                modules.add(os.path.basename(root))
        
        if depth > max_depth:
            dirs.clear()
            continue
        
        if len(lines) >= MAX_TREE_LINES:
            lines.append("  " * (depth + 1) + "... (truncado)")
            break
        
        if rel != ".":
            lines.append("  " * depth + os.path.basename(root) + "/")
        
        file_count = 0
        for f in sorted(files):
            if file_count >= 20:
                lines.append("  " * (depth + 1) + "... (más archivos)")
                break
            
            if f.startswith(".") or any(f.endswith(ext) for ext in ignore_files):
                continue
            
            lines.append("  " * (depth + 1) + f)
            file_count += 1
            
            file_path = os.path.join(root, f)
            for pattern in config_patterns:
                if f.lower() == pattern.lower() or f.lower().startswith(pattern.lower()):
                    try:
                        if os.path.getsize(file_path) <= max_file_size:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as fh:
                                content = fh.read(2000)
                                rel_path = os.path.relpath(file_path, repo_path)
                                structure["key_files"][rel_path] = content
                                
                                if pattern in ["pom.xml", "build.gradle", "package.json", "go.mod"]:
                                    structure["config_files"].append(rel_path)
                                    if "dependency" in content.lower() or "dependencies" in content.lower():
                                        structure["dependencies"][rel_path] = "detected"
                                    
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
    structure["technologies"] = list(set(structure["technologies"]))
    
    return structure

