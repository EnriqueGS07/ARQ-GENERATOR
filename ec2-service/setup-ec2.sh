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

# Instalar Docker (opcional, para contenerización)
echo "🐳 Instalando Docker..."
if [ "$OS" = "amzn" ] || [ "$OS" = "rhel" ] || [ "$OS" = "centos" ]; then
    sudo yum install -y docker
    sudo systemctl start docker
    sudo systemctl enable docker
elif [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    sudo apt install -y docker.io
    sudo systemctl start docker
    sudo systemctl enable docker
fi

# Instalar Ollama
echo "🤖 Instalando Ollama..."
curl -fsSL https://ollama.com/install.sh | sh

# Configurar Ollama como servicio
echo "⚙️  Configurando Ollama como servicio..."
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
Environment="OLLAMA_HOST=0.0.0.0:11434"

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

if [ "$INSTANCE_TYPE" = "t2.large" ] || [ "$INSTANCE_TYPE" = "t2.xlarge" ]; then
    echo "⚠️  Instancia t2 detectada - usando modelo quantizado"
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
    echo "✅ Configuración optimizada para t2.large"
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

# Crear directorio para la aplicación
APP_DIR="/opt/ec2-service"
echo "📁 Creando directorio de aplicación: $APP_DIR"
sudo mkdir -p $APP_DIR

# Copiar archivos de la aplicación (asumiendo que están en el directorio actual)
if [ -f "main.py" ] && [ -f "requirements.txt" ]; then
    echo "📋 Copiando archivos de la aplicación..."
    sudo cp main.py $APP_DIR/
    sudo cp requirements.txt $APP_DIR/
else
    echo "⚠️  Archivos main.py o requirements.txt no encontrados en el directorio actual"
    echo "   Por favor, copia los archivos manualmente a $APP_DIR"
fi

# Instalar dependencias de Python
echo "🐍 Instalando dependencias de Python..."
cd $APP_DIR
sudo pip3 install -r requirements.txt

# Crear usuario para el servicio
if ! id "ec2-service" &>/dev/null; then
    sudo useradd -r -s /bin/false -d $APP_DIR ec2-service
    sudo chown -R ec2-service:ec2-service $APP_DIR
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
User=ec2-service
WorkingDirectory=$APP_DIR
Environment="OLLAMA_API_URL=http://localhost:11434"
Environment="OLLAMA_MODEL=llama3"
ExecStart=/usr/bin/python3 $APP_DIR/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Nota: El servicio necesita uvicorn, ajustar si es necesario
echo "⚠️  NOTA: El servicio necesita ejecutarse con uvicorn"
echo "   Actualiza el ExecStart en /etc/systemd/system/ec2-service.service"
echo "   Ejemplo: ExecStart=/usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8000"

# Recargar systemd
sudo systemctl daemon-reload

# Configurar swap (obligatorio para t2.large)
echo "💾 Configurando swap..."
INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo "unknown")

if [ "$INSTANCE_TYPE" = "t2.large" ] || [ "$INSTANCE_TYPE" = "t2.xlarge" ]; then
    SWAP_SIZE="16G"
    echo "⚠️  Instancia t2 detectada - configurando swap de 16GB (CRÍTICO)"
else
    SWAP_SIZE="8G"
    echo "Configurando swap de ${SWAP_SIZE}"
fi

if [ ! -f /swapfile ]; then
    sudo fallocate -l $SWAP_SIZE /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    
    # Optimizar swappiness para t2.large
    if [ "$INSTANCE_TYPE" = "t2.large" ] || [ "$INSTANCE_TYPE" = "t2.xlarge" ]; then
        echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
        sudo sysctl -p
    fi
    
    echo "✅ Swap de ${SWAP_SIZE} configurado"
else
    echo "ℹ️  Swap ya existe"
fi

# Configurar límites del sistema
echo "⚙️  Configurando límites del sistema..."
sudo tee -a /etc/security/limits.conf > /dev/null <<EOF
ec2-service soft nofile 65536
ec2-service hard nofile 65536
ollama soft nofile 65536
ollama hard nofile 65536
EOF

# Mostrar resumen
echo ""
echo "✅ Configuración completada!"
echo ""
echo "📊 Resumen:"
echo "   - Ollama instalado y corriendo"
echo "   - Modelo llama3 descargado"
echo "   - Aplicación en: $APP_DIR"
echo "   - Servicios configurados"
echo ""
echo "🔧 Próximos pasos:"
echo "   1. Ajustar ExecStart en /etc/systemd/system/ec2-service.service"
echo "   2. Iniciar servicio: sudo systemctl start ec2-service"
echo "   3. Habilitar inicio automático: sudo systemctl enable ec2-service"
echo "   4. Verificar estado: sudo systemctl status ec2-service"
echo "   5. Ver logs: sudo journalctl -u ec2-service -f"
echo ""
echo "🌐 Endpoints:"
echo "   - Health: http://$(curl -s ifconfig.me):8000/health"
echo "   - API: http://$(curl -s ifconfig.me):8000/analyze"
echo ""

