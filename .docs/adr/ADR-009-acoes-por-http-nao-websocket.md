# ADR-009 — Ações por HTTP; o WebSocket é só broadcast de estado

**Status:** aceito · 2026-07-31

## Contexto

O app tem duas necessidades de comunicação com naturezas opostas:

1. **ações** — sugerir, votar, retirar voto, forçar faixa. Um cliente, uma intenção, precisa de resposta
   com sucesso ou motivo de recusa;
2. **estado** — a fila mudou, a faixa mudou, o contador subiu. Um evento, 30 telas, sem resposta.

Como já existe um WebSocket para (2), a tentação é mandar (1) por ele também — uma conexão, um protocolo.

## Decisão

**Ações vão por HTTP. O WebSocket é estritamente servidor→cliente. O cliente nunca envia mensagem
alguma.**

Não existe tipo `ClientMsg` em [06 §3](../06-realtime-websocket.md).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| **Tudo pelo WebSocket** | Uma conexão, um protocolo. Mas exigiria reimplementar: correlação pedido↔resposta (`requestId`), códigos de erro, timeout de pedido sem resposta, e "o socket caiu no meio da sugestão — foi aceita?". Tudo isso já existe em HTTP, testado por décadas: `429` com `Retry-After`, corpo de erro tipado, semântica de retry conhecida pelo browser. |
| **HTTP para tudo, sem WS, com polling** | Foi o que M0 faz de propósito (polling de 2 s), e é *suficiente* para provar o caminho do áudio. Mas mata o produto em M1: o contador de skip precisa saltar em todas as telas ao mesmo tempo ([RNF-03](../02-requisitos-nao-funcionais.md): 300 ms), e polling de 300 ms × 30 clientes é 100 req/s para transportar quase sempre nada. |
| **SSE para estado, HTTP para ação** | Tecnicamente adequado — o fluxo *é* unidirecional, e é o caso de uso do SSE. WebSocket ganhou por um detalhe prático: o `EventSource` não envia cookie em todos os cenários de forma consistente entre browsers, e a personalização por conexão de [06 §4](../06-realtime-websocket.md) depende do cookie `bq_guest` chegar no handshake. |

## Consequências

### Positivas

- **O servidor não tem parser de mensagem de entrada no socket.** Sem validação de payload, sem
  autorização por mensagem, sem uma máquina de estados de protocolo. `ws.py` é um gerenciador de
  conexões e um `send_json`.
- **Erros ficam de graça.** Os 19 códigos de [05 §2](../05-api-http.md) chegam como status HTTP com corpo
  tipado, e o frontend tem **um** tradutor de erro.
- **`/tv` usa o mesmo socket que todos.** Como ele não pode escrever nada por
  [RF-38](../01-requisitos-funcionais.md), e ninguém pode, não há caso especial: a conexão dele
  simplesmente não tem cookie `bq_guest`, e `me` vem `null`.
- **A retirada de voto pode ser um endpoint separado** ([ADR-004](ADR-004-skip-5-votos-sem-ttl.md)). Por
  WS ela seria uma mensagem com um flag, e o flag num handler compartilhado é exatamente o que produziu
  o bug de "voto preso" no desenho anterior. HTTP dá dois verbos e dois handlers de graça.
- **Reconexão não perde ação nenhuma**, porque nenhuma ação depende do socket. Um `POST` durante a queda
  funciona normalmente.

### Negativas

- **Duas camadas de rede no cliente**: `api.ts` (~40 linhas) e `ws.ts` (~50). Aceitável — as duas são
  `fetch` e `WebSocket` nativos, sem dependência.
- **Uma ação faz dois roundtrips de efeito**: o `POST` responde ao autor, e o broadcast informa os
  outros. O autor recebe a confirmação duas vezes, por caminhos diferentes.

O segundo ponto tem uma consequência de UI que precisa ser decidida e não descoberta: **a resposta do
`POST` é a fonte da verdade para o autor da ação, e o broadcast é para todos os outros.** O botão de
voto reflete o `200` imediatamente, sem esperar o `state` chegar — senão haveria ~50 ms de "toquei e nada
aconteceu", que numa tela de celular lê como toque não registrado. Quando o broadcast chega, ele
confirma o que já está na tela.

### 🔴 A consequência que não é óbvia

O broadcast chega ao autor **depois** da resposta do `POST`, mas nada garante isso — são caminhos
independentes. Se o broadcast chegar primeiro, a store já tem `youVoted: true` e a resposta do `POST`
confirma o mesmo. Se chegar depois, a resposta pinta e o broadcast confirma.

Os dois caminhos convergem porque o snapshot é **completo e idempotente**
([06 §2](../06-realtime-websocket.md)) e `apply` **substitui em vez de fazer merge**
([08 §3.1](../08-frontend.md)). É essa combinação — e não ordenação — que torna a corrida inofensiva.
Com deltas, ou com `Object.assign`, a ordem passaria a importar e a tela poderia ficar num estado que o
servidor nunca teve.
