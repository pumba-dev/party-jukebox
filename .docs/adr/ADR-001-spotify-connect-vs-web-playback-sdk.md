# ADR-001 — Spotify Connect (dirigir o app desktop), não Web Playback SDK

**Status:** aceito · 2026-07-31
**Supera:** o desenho de playback do [DESIGN-v0 §6–§7](../historico/DESIGN-v0.md)

## Contexto

O requisito é [RF-15](../01-requisitos-funcionais.md): a API põe uma música do Spotify para tocar no
notebook. O backend escolhido é Python ([ADR-002](ADR-002-fastapi-sqlite-stdlib.md)).

O brief anterior assumia o **Web Playback SDK**, que transforma uma aba do browser num device Spotify.
Isso é incompatível com um backend Python por um motivo estrutural, não de conveniência: **o SDK é uma
biblioteca JavaScript de browser**, apoiada em EME/Widevine para DRM. Não existe port, wrapper ou
equivalente em Python — e não pode existir, porque a parte que importa é o módulo de descriptografia do
browser.

Isso deixava dois caminhos de verdade, e a escolha decide metade da arquitetura.

## Decisão

**O app desktop do Spotify é o motor de áudio. A API Python é controle remoto, via Spotify Connect.**

```
[FastAPI] --REST--> [Spotify Cloud] --Connect--> [app desktop] --A2DP--> [JBL]
```

Concretamente: `PUT /v1/me/player/play?device_id=…` com `{"uris": [...]}`, uma faixa por vez, device
resolvido por **nome** ([07 §3](../07-integracao-spotify.md)).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| **Web Playback SDK numa aba `/player`**, com o Python conversando com ela por WebSocket | Funciona, e foi o desenho anterior. Custa: uma aba que precisa ficar viva 4 horas, gesto obrigatório do usuário para iniciar (política de autoplay), dependência de Widevine, e — o pior — o **backend passa a depender de um cliente browser** para a função central do produto. O estado autoritativo do player fica no browser e o Python só o observa por um socket que pode cair. Ganha precisão de evento; perde a propriedade de "fechei tudo e a música continua". |
| **`librespot`** (cliente Connect open-source em Rust) como device headless | Mesmo modelo de controle da opção escolhida, mas com um binário não-oficial a mais para compilar e manter, e nenhuma vantagem: o app desktop oficial já faz o papel de device, já está instalado, e já está logado. |
| **Baixar/streamar áudio e tocar localmente** | Fora dos termos de uso do Spotify, e um sistema inteiro a mais (codec, buffer, saída de áudio) que [00 §3](../00-visao-e-escopo.md) põe explicitamente fora de escopo. |
| **Pré-enfileirar com `POST /me/player/queue`** para gap zero | Rejeitado como técnica dentro da opção escolhida. Elimina os ~300 ms de silêncio, mas a partir do momento em que uma faixa entra na fila interna do Spotify ela **sai do nosso controle**: não dá para remover num force-play, não dá para reordenar, e o comportamento de `PUT play` sobre fila pendente não é documentado. Trocaria determinismo na fila — que **é** o produto — por 300 ms que já cabem no [RNF-02](../02-requisitos-nao-funcionais.md). |

## Consequências

### Positivas

- **Desaparece uma superfície inteira** que existia no desenho anterior: aba `/player`, `PLAYER_TOKEN`,
  EME/Widevine, `activateElement`, e a fronteira de confiança `Origin: null`. Não é código simplificado
  — é código que não precisa ser escrito nem defendido.
- **Escopos OAuth mínimos**: `user-read-playback-state` e `user-modify-playback-state`. Sem `streaming`.
- **Áudio nativo.** O app desktop sai pelo device default do Windows, e todo o problema de roteamento
  fica do lado que [o usuário declarou ser dele](../00-visao-e-escopo.md#3-escopo).
- **Robusto a browser.** Pode fechar todas as abas, inclusive `/tv` e `/host`. A música continua.
- **Development Mode basta.** Nenhum dos endpoints usados exige Extended Quota — verificado contra a
  documentação oficial. *(O brief anterior afirmava que uma allowlist não publicada de endpoints
  desqualificava o Spotify; a verificação mostrou que não existe tal restrição em `/search` nem em
  `/me/player/*`.)*

### Negativas — e são reais

- 🔴 **O playback depende de internet.** O Web API é na nuvem. Sem rede, não há busca nem despacho.
  Aceito em [RNF §8](../02-requisitos-nao-funcionais.md#8-riscos-aceitos-explicitamente).
- 🔴 **Dependemos de um programa de terceiros estar aberto e logado.** Três modos de falha que não são
  bugs nossos: app fechado, app deslogado, `device_id` mudando sozinho
  ([07 §3](../07-integracao-spotify.md)).
- **O estado verdadeiro do playback vive fora do processo.** É isso que torna "reconciliar" o problema
  central do maestro ([03 §4.5](../03-arquitetura.md)) e o que obriga o polling a 1 Hz.
- **Piso de ~300 ms de silêncio** entre faixas. Dentro do [RNF-02](../02-requisitos-nao-funcionais.md),
  mas só com a antecipação de despacho de [03 §4.4](../03-arquitetura.md) — que existe **por causa**
  desta decisão.
- **Sem eventos push.** O SDK dava `player_state_changed`; aqui é polling. Custa 1 req/s do orçamento e
  até 1 s de atraso na detecção — motivo pelo qual a antecipação por relógio local é obrigatória e não
  otimização.

### O que esta decisão nos obriga a fazer

1. Resolver device por **nome**, nunca cachear `device_id` ([07 §3](../07-integracao-spotify.md)).
2. Antecipar o despacho em `DISPATCH_LEAD_MS` ([03 §4.4](../03-arquitetura.md)), e **medir** essa
   constante (tarefa M0.13).
3. Confirmar `DISPATCHING → PLAYING` pelo `pstate`, **nunca** pelo `204`
   ([03 §4.5](../03-arquitetura.md)).
4. Tratar `204` com corpo vazio em `GET /me/player` como estado normal
   ([07 §6](../07-integracao-spotify.md)).
5. Ter um botão de "re-resolver device" no `/host` — a ação de recuperação mais provável da noite.

## Como reverter

Voltar ao Web Playback SDK exigiria uma página `/player` com o SDK, um canal de comando
servidor→browser, e mover a autoridade de estado para o cliente. Não é uma troca de módulo: é o
[03 §4](../03-arquitetura.md) inteiro. A especificação antiga está preservada no
[DESIGN-v0](../historico/DESIGN-v0.md) caso isso seja necessário algum dia.
