<#
Shared configuration for the isolated Stage 8 measurement contour.

This file only describes the contour. It does not start services or modify data.
#>

function Get-Stage8TestProfile {
    $root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    $envFile = Join-Path $root '.env'
    $line = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } |
        Select-Object -First 1
    if (-not $line) { throw 'DATABASE_URL не найден в .env.' }

    $baseUrl = ($line -split '=', 2)[1].Trim()
    if ($baseUrl -notmatch '://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?#]+)') {
        throw 'Формат DATABASE_URL не поддерживается.'
    }

    $profile = [ordered]@{
        DatabaseName     = 'okf_stage8_test'
        CollectionName   = 'okf_knowledge_stage8_test'
        DataDir          = './tests/tmp/stage8-data'
        KnowledgeProfile = 'Измерительная база Stage 8'
        GlossaryEnabled  = 'true'
        DatabaseUser     = $Matches[1]
        DatabasePassword = $Matches[2]
        DatabaseHost     = $Matches[3]
        DatabasePort     = $Matches[4]
        DatabaseUrl      = ($baseUrl -replace '/[^/?#]+$', '/okf_stage8_test')
    }
    $profile.Environment = [ordered]@{
        DATABASE_URL                     = $profile.DatabaseUrl
        DATABASE_URL_DEV                 = ''
        DATA_DIR                         = $profile.DataDir
        QDRANT_COLLECTION                = $profile.CollectionName
        KNOWLEDGE_PROFILE                = $profile.KnowledgeProfile
        GLOSSARY_QUERY_EXPANSION_ENABLED = $profile.GlossaryEnabled
    }
    return [pscustomobject]$profile
}
