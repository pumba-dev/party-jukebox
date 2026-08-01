<#
    PostToolUse (Edit|Write) — avisa quando `api/bq/models.py` muda.

    Cobre uma falha SILENCIOSA e documentada. O gatilho de rebuild do start.ps1 (linhas 44-51)
    compara o mtime de `web/dist/index.html` só contra `web/src`, `web/index.html` e
    `web/package.json` — `api/` não entra na conta. Então: você mexe no pydantic, roda
    `.\start.ps1`, ele imprime "frontend já buildado", NÃO regenera o `openapi.json`, NÃO roda o
    `vue-tsc`, a garantia do ADR-006 ("renomear campo quebra o build") não dispara, e o campo
    chega `undefined` na festa — com tudo verde no terminal.

    O hook recebe o evento como JSON no STDIN (não existe $CLAUDE_FILE_PATHS). Sai 0 sempre: o
    PostToolUse é não-bloqueante por natureza — a ferramenta já rodou — e um aviso que derruba a
    edição seria pior que o esquecimento que ele previne.
#>

[Console]::OutputEncoding = [Text.Encoding]::UTF8

$entrada = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($entrada)) { exit 0 }

try { $evento = $entrada | ConvertFrom-Json } catch { exit 0 }

$arquivo = $null
if ($evento.tool_input) { $arquivo = $evento.tool_input.file_path }
if (-not $arquivo) { exit 0 }

# Casa a barra dos dois mundos: o `file_path` vem absoluto e em Windows, mas o Git Bash e um
# clone em WSL entregariam `/`.
if ($arquivo -notmatch 'bq[\\/]models\.py$') { exit 0 }

$aviso = @'
api/bq/models.py mudou — o contrato do OpenAPI ficou para trás.

Rode os dois e commite `api/openapi.json` JUNTO da mudança:

    cd api; .\.venv\Scripts\python.exe scripts\dump_openapi.py
    cd ..\web; npm run build

O start.ps1 não faz isso por você: o gatilho de rebuild dele compara o mtime de web/dist só
contra web/src, web/index.html e web/package.json — api/ não entra na conta. Ele imprime
"frontend já buildado", a garantia do ADR-006 não dispara, e o campo chega undefined na festa.
'@

@{
    systemMessage      = $aviso
    hookSpecificOutput = @{
        hookEventName    = 'PostToolUse'
        additionalContext = $aviso
    }
} | ConvertTo-Json -Depth 5 -Compress

exit 0
