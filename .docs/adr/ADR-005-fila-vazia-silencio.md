# ADR-005 — Fila vazia → silêncio e chamada no `/tv`

**Status:** aceito · 2026-07-31 · decisão do usuário, contra a recomendação inicial

## Contexto

Fila vazia não é caso excepcional: é o estado **mais provável** por volta das 22h30, quando todo mundo
está comendo, conversando, e ninguém está olhando o celular. Precisa ter uma resposta definida.

## Decisão

**O som para. O `/tv` ocupa a tela inteira com uma chamada para sugerir, com QR code**
([RF-17](../01-requisitos-funcionais.md), [RF-36](../01-requisitos-funcionais.md)). Nada toca até alguém
sugerir ou o host forçar uma faixa.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| **Playlist de fallback** (era a recomendação) | A sala nunca fica em silêncio e o host nunca precisa correr para o notebook. Rejeitada pelo usuário. |
| **Re-sortear do histórico da noite** | Zero configuração e o gosto é sempre da sala. Mas na primeira meia hora o histórico está vazio e não há de onde sortear — a hora em que fila vazia é mais provável. |

## Consequências

### Positivas

- **A pressão social é o mecanismo pretendido.** Uma tela grande dizendo "ninguém sugeriu nada" com um QR
  de 30 cm é o convite mais eficiente que o app tem, e ele só aparece quando é necessário.
- **Silêncio é honesto.** Uma playlist de fallback preenche o vazio e, ao fazer isso, esconde que o
  mecanismo de participação parou — a festa vira rádio e ninguém nota.
- **`idle` é um estado de primeira classe**, não um `if` esquecido. É o que motiva `PlayerState` ser união
  discriminada ([RNF-23](../02-requisitos-nao-funcionais.md)): o compilador **obriga** a tela de fila
  vazia a existir.
- **Menos código:** nenhuma configuração de playlist, nenhuma resolução de contexto, nenhuma marcação de
  "da casa" no `/tv`.

### Negativas — o custo é real e vale nomear

- 🔴 **Se ninguém olhar o monitor, o silêncio persiste.** É o modo de falha da decisão, e ele é público:
  30 pessoas numa sala quieta.
- **Depende de a mecânica de participação estar viva** naquele momento. Se os celulares estiverem sem
  bateria, ou se a novidade tiver passado, a pressão social não funciona — e aí não há nada por baixo.

## A rede que faz esta decisão ficar de pé

O que separa "silêncio como espera" de "silêncio como beco" é **uma coisa**: o
[RF-26](../01-requisitos-funcionais.md), *Tocar agora*, alcançável em **um toque** no `/host`.

Isso tem três consequências que atravessam o resto da especificação e não são óbvias:

1. **O botão "Tocar agora" do `/host` é o mais importante daquela tela**
   ([08 §8](../08-frontend.md)) e precisa ser alcançável em um toque a partir da busca. Se exigir três
   toques, esta decisão fica pior do que foi projetada.
2. **Cortar M1.13 (force-play) passa a ser mais grave do que parece**
   ([09](../09-plano-implementacao.md#se-o-tempo-apertar)): sem ele, silêncio não tem saída manual, e a
   decisão perde a rede. Se M1.13 cair, a playlist de fallback deveria voltar à mesa.
3. **`GET /me/player` devolvendo `204` deixa de ser caso raro** e passa a ser o caminho normal, 1×/s,
   enquanto a fila estiver vazia. Tratar isso como erro faz o maestro morrer a cada segundo — e o
   `_step()` morto para de despachar, tornando a fila vazia **permanente**. É a razão pela qual o
   `204` de corpo vazio ganhou marcação 🔴 em [07 §6](../07-integracao-spotify.md) e teste próprio em
   [10 §3.3](../10-testes-e-validacao.md).

O item 3 é o achado interessante desta decisão: **ela transformou um caso de borda do Spotify no caminho
mais quente do sistema.**

## Como saber se estava errada

Registrado no [runbook §3.9](../11-runbook-da-festa.md): **se a fila esvaziar três vezes na noite e a
pressão social não resolver em ~30 s**, esta decisão estava errada para esta festa. Uma playlist de
fallback é ~1 h de trabalho e o `/host` já tem o botão que a substitui manualmente.
