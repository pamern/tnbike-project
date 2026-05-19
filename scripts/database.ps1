# ============================================================
# scripts/database.ps1
# TNBIKE Database Manager
#
# Actions:
#   init
#       - docker compose up -d
#       - tạo database nếu chưa có
#       - nếu DB chưa có bảng: chạy SQL init/import
#       - nếu DB đã có bảng: chỉ chạy các SQL an toàn như 03_create_email_log.sql
#
#   reset
#       - docker compose up -d
#       - DROP DATABASE
#       - CREATE DATABASE
#       - chạy lại toàn bộ SQL files
#
# Notes:
#   - Dùng reset khi muốn dựng DB sạch từ đầu.
#   - Dùng init khi mới setup hoặc muốn đảm bảo DB/email_log tồn tại.
# ============================================================

param(
    [ValidateSet("init", "reset")]
    [string]$Action = "init",

    [string]$Container = "tnbike_postgres",
    [string]$Database = "tnbike_db",
    [string]$DbUser = "postgres",
    [string]$SqlDir = "sql",

    [string[]]$SqlFiles = @(
        "01_create_tables.sql",
        "02_import_data.sql",
        "03_create_email_log.sql"
    ),

    # Với init: xóa schema tnbike trước rồi chạy lại toàn bộ SQL.
    [switch]$DropSchemaFirst,

    # Với init: nếu DB đã có bảng, vẫn ép chạy toàn bộ SqlFiles.
    # Cẩn thận: nếu 01_create_tables.sql không có IF NOT EXISTS thì có thể lỗi.
    [switch]$RunAllSqlOnExistingDb,

    # Không tự docker compose up -d.
    [switch]$SkipDockerComposeUp,

    [int]$WaitSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ============================================================
# PATH
# ============================================================

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$SqlDirPath = Resolve-Path (Join-Path $ProjectRoot $SqlDir)

# ============================================================
# HELPERS
# ============================================================

function Invoke-CommandChecked {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$Description = ""
    )

    if ($Description -ne "") {
        Write-Host ""
        Write-Host ">>> $Description"
    }

    Write-Host ">> $Exe $($Arguments -join ' ')"

    & $Exe @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $Exe $($Arguments -join ' ')"
    }
}

function Invoke-CommandOutput {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$Description = ""
    )

    if ($Description -ne "") {
        Write-Host ""
        Write-Host ">>> $Description"
    }

    Write-Host ">> $Exe $($Arguments -join ' ')"

    $output = & $Exe @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $Exe $($Arguments -join ' ')"
    }

    return ($output | Out-String).Trim()
}

function Start-DockerComposeIfNeeded {
    if ($SkipDockerComposeUp) {
        Write-Host "Skip docker compose up -d"
        return
    }

    Push-Location $ProjectRoot

    try {
        Invoke-CommandChecked `
            -Exe "docker" `
            -Arguments @("compose", "up", "-d") `
            -Description "Start Docker Compose services"
    }
    finally {
        Pop-Location
    }
}

function Wait-ContainerRunning {
    param(
        [string]$ContainerName
    )

    $deadline = (Get-Date).AddSeconds($WaitSeconds)

    while ((Get-Date) -lt $deadline) {
        $containers = Invoke-CommandOutput `
            -Exe "docker" `
            -Arguments @("ps", "--format", "{{.Names}}") `
            -Description "Check running containers"

        $containerList = @()

        if ($containers -ne "") {
            $containerList = $containers -split "`r?`n"
        }

        if ($containerList -contains $ContainerName) {
            Write-Host "Container is running: $ContainerName"
            return
        }

        Write-Host "Waiting for container '$ContainerName'..."
        Start-Sleep -Seconds 2
    }

    throw "Container '$ContainerName' is not running after $WaitSeconds seconds."
}

function Wait-PostgresReady {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)

    Write-Host ""
    Write-Host ">>> Wait PostgreSQL ready"

    while ((Get-Date) -lt $deadline) {
        & docker exec $Container pg_isready -U $DbUser -d postgres *> $null

        if ($LASTEXITCODE -eq 0) {
            Write-Host "PostgreSQL is ready."
            return
        }

        Write-Host "Waiting for PostgreSQL..."
        Start-Sleep -Seconds 2
    }

    throw "PostgreSQL is not ready after $WaitSeconds seconds."
}

function Assert-DockerReady {
    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @("--version") `
        -Description "Check Docker CLI"

    Start-DockerComposeIfNeeded

    Wait-ContainerRunning -ContainerName $Container
    Wait-PostgresReady
}

function Test-DatabaseExists {
    $checkSql = "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_database WHERE datname = '$Database') THEN 1 ELSE 0 END;"

    $exists = Invoke-CommandOutput `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", "postgres",
            "-tAc", $checkSql
        ) `
        -Description "Check database exists"

    return ($exists -eq "1")
}

function Ensure-DatabaseExists {
    $exists = Test-DatabaseExists

    if ($exists) {
        Write-Host "Database already exists: $Database"
        return
    }

    $createSql = "CREATE DATABASE `"$Database`" ENCODING 'UTF8';"

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", "postgres",
            "-c", $createSql
        ) `
        -Description "Create database"

    Write-Host "Database created: $Database"
}

function Test-TnbikeSchemaHasTables {
    $checkSql = @"
SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'tnbike'
      AND table_type = 'BASE TABLE'
) THEN 1 ELSE 0 END;
"@

    $hasTables = Invoke-CommandOutput `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", $Database,
            "-tAc", $checkSql
        ) `
        -Description "Check schema tnbike has tables"

    return ($hasTables -eq "1")
}

function Reset-Database {
    $terminateSql = @"
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '$Database'
  AND pid <> pg_backend_pid();
"@

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", "postgres",
            "-c", $terminateSql
        ) `
        -Description "Terminate active database connections"

    $dropSql = "DROP DATABASE IF EXISTS `"$Database`";"

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", "postgres",
            "-c", $dropSql
        ) `
        -Description "Drop database"

    $createSql = "CREATE DATABASE `"$Database`" ENCODING 'UTF8';"

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", "postgres",
            "-c", $createSql
        ) `
        -Description "Create database"

    Write-Host "Database reset completed: $Database"
}

function Drop-SchemaIfNeeded {
    if (-not $DropSchemaFirst) {
        return
    }

    if ($Action -ne "init") {
        Write-Host "Skip DropSchemaFirst because action is reset. Reset already drops whole database."
        return
    }

    $dropSql = "DROP SCHEMA IF EXISTS tnbike CASCADE;"

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", $Database,
            "-v", "ON_ERROR_STOP=1",
            "-c", $dropSql
        ) `
        -Description "Drop existing schema tnbike"

    Write-Host "Schema dropped: tnbike"
}

function Invoke-SqlFile {
    param(
        [string]$FileName
    )

    $HostSqlFile = Join-Path $SqlDirPath $FileName
    $ContainerSqlDir = "/tmp/tnbike_sql"
    $ContainerSqlFile = "$ContainerSqlDir/$FileName"

    if (-not (Test-Path $HostSqlFile)) {
        throw "SQL file not found: $HostSqlFile"
    }

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "mkdir", "-p", $ContainerSqlDir
        ) `
        -Description "Create temp SQL folder in container"

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "cp",
            $HostSqlFile,
            "${Container}:$ContainerSqlFile"
        ) `
        -Description "Copy SQL file to container: $FileName"

    Invoke-CommandChecked `
        -Exe "docker" `
        -Arguments @(
            "exec", $Container,
            "psql", "-U", $DbUser, "-d", $Database,
            "-v", "ON_ERROR_STOP=1",
            "-f", $ContainerSqlFile
        ) `
        -Description "Run SQL file: $FileName"
}

function Cleanup-TempSqlFolder {
    try {
        Invoke-CommandChecked `
            -Exe "docker" `
            -Arguments @(
                "exec", $Container,
                "rm", "-rf", "/tmp/tnbike_sql"
            ) `
            -Description "Clean temp SQL folder in container"
    }
    catch {
        Write-Warning "Could not clean temp SQL folder: $($_.Exception.Message)"
    }
}

function Get-SqlFilesToRunForInit {
    $schemaHasTables = Test-TnbikeSchemaHasTables

    if (-not $schemaHasTables) {
        Write-Host "Schema tnbike has no tables. Running all init SQL files."
        return $SqlFiles
    }

    if ($RunAllSqlOnExistingDb) {
        Write-Warning "Schema tnbike already has tables, but RunAllSqlOnExistingDb is enabled."
        return $SqlFiles
    }

    Write-Warning "Schema tnbike already has tables. Skip non-idempotent SQL files like 01_create_tables.sql and 02_import_data.sql."

    $safeFiles = @()

    foreach ($file in $SqlFiles) {
        if ($file -eq "03_create_email_log.sql") {
            $safeFiles += $file
        }
    }

    if ($safeFiles.Count -eq 0) {
        Write-Host "No safe SQL files to run for existing DB."
    }
    else {
        Write-Host "Safe SQL files to run: $($safeFiles -join ', ')"
    }

    return $safeFiles
}

# ============================================================
# MAIN
# ============================================================

Write-Host "============================================================"
Write-Host "TNBIKE DATABASE MANAGER"
Write-Host "============================================================"
Write-Host "Action                    : $Action"
Write-Host "Project root              : $ProjectRoot"
Write-Host "SQL dir                   : $SqlDirPath"
Write-Host "Container                 : $Container"
Write-Host "Database                  : $Database"
Write-Host "DB User                   : $DbUser"
Write-Host "SQL files                 : $($SqlFiles -join ', ')"
Write-Host "Drop schema               : $DropSchemaFirst"
Write-Host "Run all SQL existing DB   : $RunAllSqlOnExistingDb"
Write-Host "Skip docker compose up    : $SkipDockerComposeUp"
Write-Host "Wait seconds              : $WaitSeconds"
Write-Host "============================================================"

if ($Action -eq "reset") {
    Write-Host "WARNING: This will DROP and RECREATE database: $Database"
    Write-Host "============================================================"
}

Assert-DockerReady

$SqlFilesToRun = @()

if ($Action -eq "init") {
    Ensure-DatabaseExists

    if ($DropSchemaFirst) {
        Drop-SchemaIfNeeded
        $SqlFilesToRun = $SqlFiles
    }
    else {
        $SqlFilesToRun = Get-SqlFilesToRunForInit
    }
}
elseif ($Action -eq "reset") {
    Reset-Database
    $SqlFilesToRun = $SqlFiles
}

try {
    foreach ($file in $SqlFilesToRun) {
        Invoke-SqlFile -FileName $file
    }
}
finally {
    Cleanup-TempSqlFolder
}

Write-Host ""
Write-Host "============================================================"
Write-Host "DATABASE $($Action.ToUpper()) SUCCESS"
Write-Host "SQL files executed: $($SqlFilesToRun -join ', ')"
Write-Host "============================================================"