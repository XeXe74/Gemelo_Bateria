<#
Arranque, parada, copia de seguridad y restauración del gemelo digital de la batería (GDB-DIS, 3.1 y 5.4).

Uso (desde la carpeta Gemelo_Bateria, en PowerShell):
    .\herramientas\gemelo.ps1 arrancar                 # producción
    .\herramientas\gemelo.ps1 arrancar -Entorno desarrollo
    .\herramientas\gemelo.ps1 estado
    .\herramientas\gemelo.ps1 parar
    .\herramientas\gemelo.ps1 copia                    # copia en copias\<entorno>-<fecha>
    .\herramientas\gemelo.ps1 restaurar -Copia copias\produccion-20261127-1900

La copia guarda los volúmenes con datos (histórico, buses, estado del simulador y del conector, panel).
El código, la configuración y los documentos están en el repositorio git, y las contraseñas en el
fichero .env, que conviene guardar aparte porque no se versiona.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("arrancar", "parar", "estado", "copia", "restaurar")]
    [string]$Accion,
    [ValidateSet("produccion", "desarrollo")]
    [string]$Entorno = "produccion",
    [string]$Copia = ""
)

# Docker escribe su progreso por la salida de errores: los fallos se detectan por el código de salida.
$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$proyecto = "gdb-$Entorno"
$ficheros = @("-f", "docker-compose.yml")
if ($Entorno -eq "desarrollo") { $ficheros += @("-f", "docker-compose.desarrollo.yml") }
$volumenes = @("influxdb-datos", "influxdb-config", "bus-gemelo-datos", "bus-activo-datos",
               "simulador-estado", "conector-datos", "nodered-datos")
$imagenAux = "python:3.13.7-slim"  # imagen ya descargada, con tar

function Invoke-Compose {
    & docker compose -p $proyecto @ficheros @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose ha fallado ($LASTEXITCODE)" }
}

if (-not (Test-Path ".env")) { throw "Falta el fichero .env (cópialo de .env.ejemplo y pon las contraseñas)" }

switch ($Accion) {
    "arrancar" {
        # Compose arranca en el orden de las dependencias y espera a que cada servicio esté preparado.
        Invoke-Compose up -d --build --wait
        Write-Host "Gemelo arrancado ($Entorno). Panel: http://127.0.0.1:1880/dashboard"
    }
    "parar" {
        # Parada ordenada: Compose detiene primero los servicios que dependen de otros. Cada servicio
        # recibe SIGTERM, anota su parada en el registro y cierra su sesión; el simulador guarda su estado.
        Invoke-Compose stop
        Write-Host "Gemelo detenido ($Entorno)."
    }
    "estado" {
        Invoke-Compose ps
    }
    "copia" {
        $destino = Join-Path $raiz ("copias\{0}-{1}" -f $Entorno, (Get-Date -Format "yyyyMMdd-HHmm"))
        New-Item -ItemType Directory -Force $destino | Out-Null
        # Se para el sistema para que la copia sea coherente y se vuelve a arrancar al terminar.
        Invoke-Compose stop
        foreach ($v in $volumenes) {
            & docker run --rm -v "${proyecto}_${v}:/volumen:ro" -v "${destino}:/copia" $imagenAux `
                tar czf "/copia/$v.tar.gz" -C /volumen .
            if ($LASTEXITCODE -ne 0) { throw "No se pudo copiar el volumen $v" }
        }
        git rev-parse HEAD 2>$null | Out-File -Encoding utf8 (Join-Path $destino "version_git.txt")
        Invoke-Compose up -d --wait
        Write-Host "Copia guardada en $destino"
    }
    "restaurar" {
        if (-not $Copia -or -not (Test-Path $Copia)) { throw "Indica la carpeta de la copia con -Copia" }
        $origen = (Resolve-Path $Copia).Path
        Invoke-Compose stop
        # Crea los volúmenes si no existen (por ejemplo, al restaurar en otro ordenador).
        Invoke-Compose create
        foreach ($v in $volumenes) {
            $fichero = Join-Path $origen "$v.tar.gz"
            if (-not (Test-Path $fichero)) { Write-Warning "La copia no tiene $v; se deja como está"; continue }
            & docker run --rm -v "${proyecto}_${v}:/volumen" -v "${origen}:/copia:ro" $imagenAux `
                sh -c "find /volumen -mindepth 1 -delete && tar xzf /copia/$v.tar.gz -C /volumen"
            if ($LASTEXITCODE -ne 0) { throw "No se pudo restaurar el volumen $v" }
        }
        Invoke-Compose up -d --wait
        Write-Host "Restaurada la copia $origen en el entorno $Entorno"
    }
}
