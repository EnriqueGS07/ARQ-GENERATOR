const DEFAULT_API_URL = "http://52.204.44.230:8000";

let currentMermaidCode = "";
let mermaidInitialized = false;

document.addEventListener("DOMContentLoaded", () => {
  initializeMermaid();
  setupEventListeners();
  checkHealth();
});

function initializeMermaid() {
  mermaid.initialize({
    startOnLoad: false,
    theme: "default",
    securityLevel: "loose",
    flowchart: {
      useMaxWidth: true,
      htmlLabels: true,
      curve: "basis",
    },
  });
  mermaidInitialized = true;
}

function setupEventListeners() {
  document
    .getElementById("analyzeBtn")
    .addEventListener("click", analyzeRepository);
  document.getElementById("copyBtn").addEventListener("click", copyMermaidCode);
  document.getElementById("downloadBtn").addEventListener("click", downloadSVG);
  document.getElementById("uploadS3Btn").addEventListener("click", showS3Modal);
  document.querySelector(".close").addEventListener("click", closeS3Modal);
  document.getElementById("cancelBtn").addEventListener("click", closeS3Modal);
  document.getElementById("uploadBtn").addEventListener("click", uploadToS3);
}

function getApiUrl() {
  return DEFAULT_API_URL;
}

async function checkHealth() {
  try {
    const apiUrl = getApiUrl();
    const response = await fetch(`${apiUrl}/health`);
    const data = await response.json();

    if (data.status === "ok") {
      showStatus(
        `✅ Servicio disponible - Ollama: ${data.ollama} - Modelo: ${data.model}`,
        "success"
      );
    }
  } catch (error) {
    showStatus("⚠️ No se pudo verificar el estado del servicio", "info");
  }
}

async function analyzeRepository() {
  const repoUrl = document.getElementById("repoUrl").value.trim();
  const depth = parseInt(document.getElementById("depth").value) || 1;
  const apiUrl = getApiUrl();

  if (!repoUrl) {
    showStatus("!Por favor ingresa una URL de repositorio", "error");
    return;
  }

  if (!isValidRepoUrl(repoUrl)) {
    showStatus(
      "!URL de repositorio inválida. Debe ser de GitHub, GitLab o Bitbucket",
      "error"
    );
    return;
  }

  const analyzeBtn = document.getElementById("analyzeBtn");
  const btnText = analyzeBtn.querySelector(".btn-text");
  const btnLoader = analyzeBtn.querySelector(".btn-loader");

  analyzeBtn.disabled = true;
  btnText.textContent = "Analizando...";
  btnLoader.style.display = "inline-block";

  hideStatus();
  hideDiagram();

  try {
    const headers = {
      "Content-Type": "application/json",
    };

    const response = await fetch(`${apiUrl}/analyze`, {
      method: "POST",
      headers: headers,
      body: JSON.stringify({
        repo_url: repoUrl,
        depth: depth,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.detail || `Error ${response.status}: ${response.statusText}`
      );
    }

    if (data.mermaid) {
      currentMermaidCode = data.mermaid;
      displayDiagram(data.mermaid);
      showStatus("✅ Diagrama generado exitosamente", "success");
    } else {
      throw new Error("No se recibió código Mermaid en la respuesta");
    }
  } catch (error) {
    console.error("Error:", error);
    showStatus(`❌ Error: ${error.message}`, "error");
  } finally {
    analyzeBtn.disabled = false;
    btnText.textContent = "Analizar Repositorio";
    btnLoader.style.display = "none";
  }
}

function isValidRepoUrl(url) {
  const validPrefixes = ["http://", "https://", "git@", "git://"];
  const hasValidPrefix = validPrefixes.some((prefix) => url.startsWith(prefix));
  const hasValidHost =
    url.includes("github.com") ||
    url.includes("gitlab.com") ||
    url.includes("bitbucket.org") ||
    url.endsWith(".git");
  return hasValidPrefix && hasValidHost;
}

async function displayDiagram(mermaidCode) {
  const container = document.getElementById("mermaidContainer");
  const codeTextarea = document.getElementById("mermaidCode");

  codeTextarea.value = mermaidCode;
  container.innerHTML = "";

  try {
    const { svg } = await mermaid.render("mermaid-diagram", mermaidCode);
    container.innerHTML = svg;
    showDiagram();
  } catch (error) {
    console.error("Error rendering Mermaid:", error);
    container.innerHTML = `
            <div style="color: #721c24; padding: 20px; text-align: center;">
                <p>❌ Error al renderizar el diagrama</p>
                <p style="font-size: 0.9em; margin-top: 10px;">${error.message}</p>
                <p style="font-size: 0.8em; margin-top: 10px; color: #666;">Revisa el código Mermaid en el área de texto</p>
            </div>
        `;
    showDiagram();
  }
}

function showDiagram() {
  document.getElementById("diagramSection").style.display = "block";
  document
    .getElementById("diagramSection")
    .scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function hideDiagram() {
  document.getElementById("diagramSection").style.display = "none";
}

function copyMermaidCode() {
  const codeTextarea = document.getElementById("mermaidCode");
  codeTextarea.select();
  document.execCommand("copy");

  const copyBtn = document.getElementById("copyBtn");
  const originalText = copyBtn.textContent;
  copyBtn.textContent = "✅ Copiado!";
  setTimeout(() => {
    copyBtn.textContent = originalText;
  }, 2000);
}

function downloadSVG() {
  const container = document.getElementById("mermaidContainer");
  const svg = container.querySelector("svg");

  if (!svg) {
    showStatus("❌ No hay diagrama para descargar", "error");
    return;
  }

  const svgData = new XMLSerializer().serializeToString(svg);
  const svgBlob = new Blob([svgData], { type: "image/svg+xml;charset=utf-8" });
  const svgUrl = URL.createObjectURL(svgBlob);

  const downloadLink = document.createElement("a");
  downloadLink.href = svgUrl;
  downloadLink.download = "diagrama-arquitectura.svg";
  document.body.appendChild(downloadLink);
  downloadLink.click();
  document.body.removeChild(downloadLink);
  URL.revokeObjectURL(svgUrl);

  showStatus("✅ Diagrama descargado exitosamente", "success");
}

function showS3Modal() {
  document.getElementById("s3Modal").style.display = "block";
  document.getElementById("s3Status").style.display = "none";
}

function closeS3Modal() {
  document.getElementById("s3Modal").style.display = "none";
}

async function uploadToS3() {
  const bucket = document.getElementById("s3Bucket").value.trim();
  const key = document.getElementById("s3Key").value.trim();
  const region = document.getElementById("s3Region").value.trim();
  const accessKeyId = document.getElementById("awsAccessKey").value.trim();
  const secretAccessKey = document.getElementById("awsSecretKey").value.trim();

  if (!bucket || !key || !region || !accessKeyId || !secretAccessKey) {
    showS3Status("❌ Por favor completa todos los campos", "error");
    return;
  }

  if (!currentMermaidCode) {
    showS3Status("❌ No hay diagrama para subir", "error");
    return;
  }

  const uploadBtn = document.getElementById("uploadBtn");
  uploadBtn.disabled = true;
  uploadBtn.textContent = "Subiendo...";

  try {
    const container = document.getElementById("mermaidContainer");
    const svg = container.querySelector("svg");

    if (!svg) {
      throw new Error("No se encontró el SVG del diagrama");
    }

    const svgData = new XMLSerializer().serializeToString(svg);
    const svgBlob = new Blob([svgData], { type: "image/svg+xml" });

    AWS.config.update({
      accessKeyId: accessKeyId,
      secretAccessKey: secretAccessKey,
      region: region,
    });

    const s3 = new AWS.S3();

    const params = {
      Bucket: bucket,
      Key: key,
      Body: svgBlob,
      ContentType: "image/svg+xml",
      ACL: "public-read",
    };

    await s3.putObject(params).promise();

    const s3Url = `https://${bucket}.s3.${region}.amazonaws.com/${key}`;
    showS3Status(
      `✅ Diagrama subido exitosamente a S3\nURL: ${s3Url}`,
      "success"
    );

    setTimeout(() => {
      closeS3Modal();
      showStatus(`✅ Diagrama subido a S3: ${s3Url}`, "success");
    }, 2000);
  } catch (error) {
    console.error("Error uploading to S3:", error);
    showS3Status(`❌ Error al subir a S3: ${error.message}`, "error");
  } finally {
    uploadBtn.disabled = false;
    uploadBtn.textContent = "Subir a S3";
  }
}

function showS3Status(message, type) {
  const statusDiv = document.getElementById("s3Status");
  statusDiv.textContent = message;
  statusDiv.className = `status-message ${type}`;
  statusDiv.style.display = "block";
}

function showStatus(message, type) {
  const statusDiv = document.getElementById("status");
  statusDiv.textContent = message;
  statusDiv.className = `status-message ${type}`;
  statusDiv.style.display = "block";
}

function hideStatus() {
  document.getElementById("status").style.display = "none";
}

window.addEventListener("click", (event) => {
  const modal = document.getElementById("s3Modal");
  if (event.target === modal) {
    closeS3Modal();
  }
});
