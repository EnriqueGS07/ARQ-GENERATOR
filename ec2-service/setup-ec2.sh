#!/bin/bash
# Script de configuración para EC2
# Ejecutar como: sudo bash setup-ec2.sh

set -e

echo "🚀 Configurando EC2 para servicio de análisis de arquitectura..."

# Detectar sistema operativo
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    echo "❌ No se pudo detectar el sistema operativo"
    exit 1
fi

echo "📦 Sistema operativo detectado: $OS"

# Actualizar sistema
echo "🔄 Actualizando sistema..."
if [ "$OS" = "amzn" ] || [ "$OS" = "rhel" ] || [ "$OS" = "centos" ]; then
    sudo yum update -y
    sudo yum install -y git python3 python3-pip curl wget
elif [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    sudo apt update -y
    sudo apt upgrade -y
    sudo apt install -y git python3 python3-pip curl wget
else
    echo "❌ Sistema operativo no soportado: $OS"
    exit 1
fi

# Verificar espacio en disco antes de continuar
echo "💾 Verificando espacio en disco..."
AVAILABLE_SPACE=$(df -h / | awk 'NR==2 {print $4}' | sed 's/G//')
if [ -z "$AVAILABLE_SPACE" ]; then
    AVAILABLE_SPACE=$(df -h / | awk 'NR==2 {print $4}' | sed 's/M//')
    UNIT="M"
else
    UNIT="G"
fi

echo "   Espacio disponible: ${AVAILABLE_SPACE}${UNIT}"

if [ "$UNIT" = "G" ] && [ "${AVAILABLE_SPACE%.*}" -lt 5 ]; then
    echo "⚠️  ADVERTENCIA: Menos de 5GB disponibles"
    echo "   Considera aumentar el volumen EBS antes de continuar"
    read -p "¿Continuar de todas formas? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Instalar Ollama
echo "🤖 Instalando Ollama..."
curl -fsSL https://ollama.com/install.sh | sh

# Configurar Ollama como servicio
echo "⚙️  Configurando Ollama como servicio..."
INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo "unknown")

# Configurar variables de entorno según tipo de instancia
if [[ "$INSTANCE_TYPE" =~ ^t[23]\.(large|xlarge)$ ]]; then
    OLLAMA_ENV="Environment=\"OLLAMA_NUM_THREAD=2\"
Environment=\"OLLAMA_MAX_LOADED_MODELS=1\"
Environment=\"OLLAMA_HOST=0.0.0.0:11434\""
else
    OLLAMA_ENV="Environment=\"OLLAMA_HOST=0.0.0.0:11434\""
fi

sudo tee /etc/systemd/system/ollama.service > /dev/null <<EOF
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
$OLLAMA_ENV

[Install]
WantedBy=default.target
EOF

# Crear usuario ollama si no existe
if ! id "ollama" &>/dev/null; then
    sudo useradd -r -s /bin/false ollama
fi

sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl start ollama

# Esperar a que Ollama esté listo
echo "⏳ Esperando a que Ollama esté listo..."
sleep 5

# Descargar modelo (detectar tipo de instancia)
INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo "unknown")

# Instancias con 8GB RAM necesitan modelo quantizado
if [[ "$INSTANCE_TYPE" =~ ^t[23]\.(large|xlarge)$ ]]; then
    echo "⚠️  Instancia con 8GB RAM detectada ($INSTANCE_TYPE) - usando modelo quantizado"
    echo "📥 Descargando modelo quantizado llama3:8b-instruct-q4_0 (esto puede tardar)..."
    ollama pull llama3:8b-instruct-q4_0
    
    # Verificar que el modelo se descargó
    if ollama list | grep -q "llama3:8b-instruct-q4_0"; then
        echo "✅ Modelo quantizado descargado correctamente"
        export OLLAMA_MODEL="llama3:8b-instruct-q4_0"
    else
        echo "❌ Error al descargar modelo quantizado"
        exit 1
    fi
    
    # Configurar Ollama para bajo consumo
    export OLLAMA_NUM_THREAD=2
    export OLLAMA_MAX_LOADED_MODELS=1
    echo "✅ Configuración optimizada para $INSTANCE_TYPE"
else
    echo "📥 Descargando modelo estándar llama3 (esto puede tardar varios minutos)..."
    ollama pull llama3
    
    # Verificar que el modelo se descargó
    if ollama list | grep -q llama3; then
        echo "✅ Modelo llama3 descargado correctamente"
        export OLLAMA_MODEL="llama3"
    else
        echo "❌ Error al descargar modelo llama3"
        exit 1
    fi
fi

# Detectar directorio de trabajo (donde está el script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"

echo "📁 Directorio de aplicación: $APP_DIR"

# Verificar que los archivos existen
if [ ! -f "$APP_DIR/main.py" ] || [ ! -f "$APP_DIR/requirements.txt" ]; then
    echo "❌ Error: main.py o requirements.txt no encontrados en $APP_DIR"
    echo "   Asegúrate de ejecutar el script desde el directorio ec2-service"
    exit 1
fi

# Instalar dependencias de Python
echo "🐍 Instalando dependencias de Python..."
cd $APP_DIR
pip3 install -r requirements.txt

# Detectar usuario actual
CURRENT_USER=$(whoami)
echo "👤 Usando usuario: $CURRENT_USER"

# Determinar modelo según tipo de instancia
if [[ "$INSTANCE_TYPE" =~ ^t[23]\.(large|xlarge)$ ]]; then
    OLLAMA_MODEL="llama3:8b-instruct-q4_0"
else
    OLLAMA_MODEL="llama3"
fi

# Crear servicio systemd
echo "🔧 Configurando servicio systemd..."
sudo tee /etc/systemd/system/ec2-service.service > /dev/null <<EOF
[Unit]
Description=EC2 Architecture Generator Service
After=network-online.target ollama.service
Requires=ollama.service

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
Environment="OLLAMA_API_URL=http://localhost:11434"
Environment="OLLAMA_MODEL=$OLLAMA_MODEL"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Recargar systemd
sudo systemctl daemon-reload

# Configurar swap (obligatorio para instancias con 8GB RAM) - HACERLO PRIMERO
echo "💾 Configurando swap..."
INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo "unknown")

# Instancias con 8GB RAM necesitan swap de 16GB
if [[ "$INSTANCE_TYPE" =~ ^t[23]\.(large|xlarge)$ ]]; then
    SWAP_SIZE="16G"
    echo "⚠️  Instancia con 8GB RAM detectada ($INSTANCE_TYPE) - configurando swap de 16GB (CRÍTICO)"
else
    SWAP_SIZE="8G"
    echo "Configurando swap de ${SWAP_SIZE}"
fi

if [ ! -f /swapfile ]; then
    echo "📦 Creando swapfile de ${SWAP_SIZE}..."
    sudo fallocate -l $SWAP_SIZE /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    
    # Optimizar swappiness para instancias con 8GB RAM
    if [[ "$INSTANCE_TYPE" =~ ^t[23]\.(large|xlarge)$ ]]; then
        echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
        sudo sysctl -p
    fi
    
    echo "✅ Swap de ${SWAP_SIZE} configurado"
    free -h
else
    echo "ℹ️  Swap ya existe"
    free -h
fi

# Configurar límites del sistema
echo "⚙️  Configurando límites del sistema..."
CURRENT_USER=$(whoami)
sudo tee -a /etc/security/limits.conf > /dev/null <<EOF
$CURRENT_USER soft nofile 65536
$CURRENT_USER hard nofile 65536
ollama soft nofile 65536
ollama hard nofile 65536
EOF

# Habilitar e iniciar servicio
echo "🚀 Iniciando servicio..."
sudo systemctl daemon-reload
sudo systemctl enable ec2-service
sudo systemctl start ec2-service

# Esperar un momento
sleep 3

# Verificar estado
echo "📊 Verificando estado del servicio..."
if sudo systemctl is-active --quiet ec2-service; then
    echo "✅ Servicio ec2-service está corriendo"
else
    echo "⚠️  Servicio no está corriendo, revisa logs:"
    echo "   sudo journalctl -u ec2-service -n 50"
fi

# Mostrar resumen
echo ""
echo "✅ Configuración completada!"
echo ""
echo "📊 Resumen:"
echo "   - Sistema operativo: $OS"
echo "   - Tipo de instancia: $INSTANCE_TYPE"
echo "   - Swap configurado: ${SWAP_SIZE}"
echo "   - Ollama instalado y corriendo"
if [[ "$INSTANCE_TYPE" =~ ^t[23]\.(large|xlarge)$ ]]; then
    echo "   - Modelo: llama3:8b-instruct-q4_0 (quantizado)"
else
    echo "   - Modelo: llama3"
fi
echo "   - Aplicación en: $APP_DIR"
echo "   - Usuario del servicio: $CURRENT_USER"
echo "   - Servicios configurados y corriendo"
echo ""
echo "🔍 Verificar:"
echo "   sudo systemctl status ec2-service"
echo "   curl http://localhost:8000/health"
echo ""
echo "📝 Logs:"
echo "   sudo journalctl -u ec2-service -f"
echo ""
if [[ "$INSTANCE_TYPE" =~ ^t[23]\.(large|xlarge)$ ]]; then
    echo "⚠️  RECORDATORIO para $INSTANCE_TYPE (8GB RAM):"
    echo "   - Solo 1 request a la vez"
    echo "   - Tiempo de respuesta: 30-120 segundos"
    echo "   - Monitorea RAM constantemente"
    if [[ "$INSTANCE_TYPE" =~ ^t3\. ]]; then
        echo "   - ✅ Mejor rendimiento CPU que t2 (sin créditos limitados)"
    fi
    echo ""
fi
echo "🌐 Endpoints:"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "TU-EC2-IP")
echo "   - Health: http://$PUBLIC_IP:8000/health"
echo "   - API: http://$PUBLIC_IP:8000/analyze"
echo ""
echo "🔒 No olvides:"
echo "   1. Configurar Security Group para permitir puerto 8000"
echo "   2. Configurar EC2_API_KEY en el servicio si usas autenticación"
echo ""

