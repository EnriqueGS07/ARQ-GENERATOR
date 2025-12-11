# Frontend - Architecture Generator

Frontend web para consumir el servicio de generación de diagramas de arquitectura.

## 🎨 Características

- **Análisis de repositorios**: Interfaz gráfica para analizar repositorios Git
- **Visualización de diagramas**: Renderizado interactivo de diagramas Mermaid
- **Descargar diagramas**: Exportar diagramas como archivos SVG
- **Subir a S3**: Subir diagramas directamente a Amazon S3
- **Copiar código**: Copiar código Mermaid al portapapeles

## 📁 Archivos

- `index.html`: Interfaz principal
- `styles.css`: Estilos CSS
- `app.js`: Lógica JavaScript

## 🚀 Despliegue

### Opción 1: Servir desde S3 (Recomendado)

1. Sube los archivos a un bucket S3:

```bash
aws s3 cp frontend/ s3://tu-bucket/ --recursive
```

2. Habilita hosting estático en S3:
   - Ve a las propiedades del bucket
   - Habilita "Static website hosting"
   - Configura `index.html` como documento índice

3. Configura CORS en el bucket (si es necesario):
```json
[
    {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["GET", "HEAD"],
        "AllowedOrigins": ["*"],
        "ExposeHeaders": []
    }
]
```

4. Accede al frontend mediante la URL del bucket o CloudFront

### Opción 2: CloudFront

1. Crea una distribución CloudFront apuntando al bucket S3
2. Configura el origen como el bucket S3
3. Accede mediante la URL de CloudFront

### Opción 3: Servidor Web Local

```bash
# Con Python
cd frontend
python -m http.server 8080

# Con Node.js (http-server)
npx http-server frontend -p 8080
```

## ⚙️ Configuración

### URL del API

Por defecto, el frontend está configurado para usar:
```
http://52.204.44.230:8000
```

Puedes cambiarlo desde la interfaz o editando `app.js`:

```javascript
const API_URL = 'http://tu-ip:8000';
```

### API Key

Si el backend requiere API key, ingrésala en el campo correspondiente de la interfaz.

### Configuración de S3

Para subir diagramas a S3, necesitas:

1. **Credenciales AWS**:
   - AWS Access Key ID
   - AWS Secret Access Key

2. **Configuración del bucket**:
   - Bucket name
   - Key (ruta en S3)
   - Región

3. **Permisos del bucket**: El bucket debe permitir `PutObject` para las credenciales proporcionadas

**Política IAM mínima requerida**:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:PutObjectAcl"
            ],
            "Resource": "arn:aws:s3:::tu-bucket/*"
        }
    ]
}
```

**CORS del bucket** (si subes desde otro dominio):
```json
[
    {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["PUT", "POST", "GET", "HEAD"],
        "AllowedOrigins": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3000
    }
]
```

## 🔧 Dependencias

El frontend utiliza CDN para las siguientes librerías:

- **Mermaid.js**: Para renderizar diagramas
  - CDN: `https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js`

- **AWS SDK**: Para subida a S3
  - CDN: `https://sdk.amazonaws.com/js/aws-sdk-2.1000.0.min.js`

## 📱 Responsive

El frontend es completamente responsive y funciona en:
- Desktop
- Tablet
- Mobile

## 🔒 Seguridad

⚠️ **Importante**: Las credenciales de AWS se ingresan en el navegador. Para producción, considera:

1. Usar presigned URLs desde el backend
2. Implementar autenticación OAuth
3. Usar roles IAM con permisos temporales
4. Configurar CloudFront con WAF

## 🐛 Troubleshooting

### El diagrama no se renderiza

- Verifica que el código Mermaid sea válido
- Revisa la consola del navegador para errores
- Asegúrate de que Mermaid.js se cargó correctamente

### Error al subir a S3

- Verifica las credenciales AWS
- Confirma que el bucket existe y está en la región correcta
- Revisa los permisos IAM
- Verifica la configuración CORS del bucket

### Error de CORS

- El backend ya tiene CORS configurado para permitir cualquier origen
- Si persiste, verifica que la URL del API sea correcta

## 📝 Notas

- El frontend funciona completamente del lado del cliente
- No requiere backend adicional
- Las credenciales AWS se manejan en el navegador (considera seguridad)
- Compatible con todos los navegadores modernos

