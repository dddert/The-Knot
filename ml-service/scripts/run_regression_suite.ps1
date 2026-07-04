param(
    [string]$BaseUrl = $(if ($env:BASE_URL) { $env:BASE_URL } else { "http://localhost:8000" }),
    [string]$Role = $(if ($env:ROLE) { $env:ROLE } else { "analyst" }),
    [string]$UserId = $(if ($env:USER_ID) { $env:USER_ID } else { "tester" }),
    [string]$Token = $(if ($env:TOKEN) { $env:TOKEN } else { "analyst-token" }),
    [string]$OutDir = $(if ($env:OUT_DIR) { $env:OUT_DIR } else { "regression-results" })
)

$ErrorActionPreference = "Stop"

# Console UTF-8 for readable output.
try {
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {}

# HttpClient avoids Windows PowerShell 5.1 request-body encoding bugs.
Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromMinutes(5)
$client.DefaultRequestHeaders.Add("X-Demo-Role-Token", $Token)

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$tests = @(
    @{ Name = "01_so2_after_2018"; Query = "Какие способы удаления SO2 из металлургических газов описаны в российских источниках после 2018 года?" },
    @{ Name = "02_heap_cold_climate"; Query = "Какие технологии кучного выщелачивания применяются в условиях холодного климата?" },
    @{ Name = "03_technogenic_gypsum"; Query = "Какие способы переработки техногенного гипса описаны в корпусе?" },
    @{ Name = "04_numeric_ni_negative"; Query = "Найди процессы с извлечением никеля не менее 90% при температуре ниже 100 °C" },
    @{ Name = "05_numeric_cu_positive"; Query = "Найди процессы с извлечением меди не менее 90% при температуре выше 200 °C" },
    @{ Name = "06_so2_comparison"; Query = "Сравни мокрые и сухие способы удаления SO2 из металлургических газов" },
    @{ Name = "07_expert_autoclave"; Query = "Какие эксперты и лаборатории занимаются автоклавным выщелачиванием?" },
    @{ Name = "08_contradictions_cu_temperature"; Query = "Найди противоречивые данные о влиянии температуры на извлечение меди из металлургических шлаков" },
    @{ Name = "09_knowledge_gaps_cold_heap"; Query = "Какие пробелы в исследованиях кучного выщелачивания в холодном климате видны по найденным источникам?" },
    @{ Name = "10_patents_autoclave"; Query = "Какие патенты и технологические решения по автоклавному выщелачиванию упоминаются в корпусе?" }
)

function Invoke-Utf8JsonPost {
    param(
        [string]$Uri,
        [string]$Json
    )

    $content = [System.Net.Http.StringContent]::new(
        $Json,
        [System.Text.UTF8Encoding]::new($false),
        "application/json"
    )

    $response = $client.PostAsync($Uri, $content).GetAwaiter().GetResult()
    $responseText = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()

    if (-not $response.IsSuccessStatusCode) {
        throw "HTTP $([int]$response.StatusCode) $($response.ReasonPhrase): $responseText"
    }

    return $responseText
}

Write-Host "BASE_URL: $BaseUrl" -ForegroundColor DarkGray

# TCP preflight.
$baseUri = [Uri]$BaseUrl
$port = if ($baseUri.IsDefaultPort) {
    if ($baseUri.Scheme -eq "https") { 443 } else { 80 }
} else {
    $baseUri.Port
}

$tcp = Test-NetConnection `
    -ComputerName $baseUri.Host `
    -Port $port `
    -WarningAction SilentlyContinue

if (-not $tcp.TcpTestSucceeded) {
    Write-Host "PRECHECK FAILED: backend unreachable at $BaseUrl" -ForegroundColor Red
    exit 2
}

Write-Host "Backend TCP check: OK" -ForegroundColor Green

$failed = 0
$mockDetected = $false

foreach ($test in $tests) {
    $name = $test.Name
    $query = $test.Query

    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    Write-Host $query

    $body = @{
        query = $query
        filters = @{}
    } | ConvertTo-Json -Depth 20 -Compress

    $uri = "$BaseUrl/api/search?role=$([uri]::EscapeDataString($Role))&user_id=$([uri]::EscapeDataString($UserId))"

    try {
        $responseText = Invoke-Utf8JsonPost -Uri $uri -Json $body
        $response = $responseText | ConvertFrom-Json
    }
    catch {
        $failed++
        Write-Host "FAILED: $name" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red

        $_ | Out-String |
            Set-Content -Path (Join-Path $OutDir "$name.error.txt") -Encoding UTF8
        continue
    }

    # Fail loudly if backend is still in mock mode.
    $answerSummary = [string]$response.answer.summary
    if ($answerSummary -match "mock-режим|mock.?mode") {
        $mockDetected = $true
        Write-Host "MOCK MODE DETECTED — regression result is invalid." -ForegroundColor Red
    }

    $fullPath = Join-Path $OutDir "$name.full.json"
    $summaryPath = Join-Path $OutDir "$name.summary.json"

    # Write UTF-8 without BOM consistently.
    $fullJson = $response | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText(
        $fullPath,
        $fullJson,
        [System.Text.UTF8Encoding]::new($false)
    )

    $factsCount = @($response.facts).Count
    $evidence = @($response.retrieved_evidence)
    $evidenceCount = $evidence.Count

    $topHits = @(
        $evidence |
            Select-Object -First 10 |
            ForEach-Object {
                $text = [string]$_.text
                $excerpt = if ($text.Length -gt 360) {
                    $text.Substring(0, 360)
                } else {
                    $text
                }

                [ordered]@{
                    chunk_id = $_.chunk_id
                    filename = $_.filename
                    page_start = $_.page_start
                    score = $_.score
                    dense_score = $_.dense_score
                    lexical_score = $_.lexical_score
                    reranker_score = $_.reranker_score
                    excerpt = $excerpt
                }
            }
    )

    $summary = [ordered]@{
        query = $query
        query_plan = $response.query_plan
        facts_count = $factsCount
        evidence_count = $evidenceCount
        answer = [ordered]@{
            summary = $response.answer.summary
            confidence = $response.answer.confidence
            source_count = $response.answer.source_count
            related_experts = $response.answer.related_experts
        }
        top_hits = $topHits
    }

    $summaryJson = $summary | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText(
        $summaryPath,
        $summaryJson,
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host "facts=$factsCount evidence=$evidenceCount confidence=$($response.answer.confidence)" -ForegroundColor Green
}

$client.Dispose()
$handler.Dispose()

Write-Host ""
Write-Host "Saved results to: $OutDir" -ForegroundColor Green

if ($mockDetected) {
    Write-Host "INVALID RUN: backend mock mode was detected." -ForegroundColor Red
    exit 3
}

if ($failed -gt 0) {
    Write-Host "Completed with $failed failed request(s)." -ForegroundColor Yellow
    exit 4
}
