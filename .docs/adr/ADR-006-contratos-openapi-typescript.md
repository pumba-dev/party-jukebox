# ADR-006 — Contratos: OpenAPI→TS no HTTP, união discriminada à mão no WebSocket

**Status:** aceito · 2026-07-31

## Contexto

Duas linguagens, dois lados, um contrato. O backend é Python com pydantic
([ADR-002](ADR-002-fastapi-sqlite-stdlib.md)); o frontend é Vue com TypeScript `strict`
([08](../08-frontend.md)). O contrato atravessa dois canais com naturezas diferentes: **HTTP**
(requisição/resposta, ações — [05](../05-api-http.md)) e **WebSocket** (broadcast de estado —
[06](../06-realtime-websocket.md)).

## Decisão

**Canais diferentes, estratégias diferentes.**

| Canal | Estratégia | Arquivo |
|---|---|---|
| HTTP | `openapi-typescript` gera do `/openapi.json` do FastAPI | `types/api.d.ts` — **gerado** |
| WebSocket | união discriminada escrita à mão, espelhada em `models.py` | `types/ws.ts` — **fonte** |
| IDs | branded types | `types/brands.ts` |

```jsonc
"scripts": {
  "types": "openapi-typescript http://127.0.0.1/openapi.json -o src/types/api.d.ts",
  "build": "npm run types && vue-tsc --noEmit && vite build"
}
```

O `vue-tsc --noEmit` **antes** do `vite build` é essencial: o Vite transpila TS sem checar tipos, então
sem esse passo o build passa com erro de tipo e o erro aparece em runtime, na festa. Com `types` rodando
antes, **renomear um campo no pydantic quebra `npm run build`** — que é o efeito desejado.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| **Tipos TS à mão para tudo** | Zero configuração. Mas os dois lados divergem em silêncio na primeira vez que um campo é renomeado, e o bug aparece em runtime. Num app de uma noite, "aparece em runtime" significa "aparece na festa". |
| **Zod validando tudo em runtime** | Pega divergência na hora e com mensagem legível. Custa duplicar o schema em dois lugares e mantê-los. O pydantic já valida a **entrada** no servidor, que é onde dado não confiável existe; validar de novo a saída do nosso próprio servidor é cerimônia. |
| **Gerar o WebSocket também** | Não há de onde: o AsyncAPI não é emitido pelo FastAPI, e mensagens de WS não aparecem no OpenAPI. Gerar exigiria um schema intermediário e um gerador — mais peças que os ~60 linhas de `ws.ts`. |
| **`datamodel-code-generator` no sentido inverso** (TS→Python) | Inverte a direção e deixa o pydantic derivado. O servidor é quem valida entrada não confiável; ele deve ser a fonte. |

## Por que o WebSocket é escrito à mão, e por que isso é uma vantagem

Não é concessão — é onde o TypeScript rende mais neste projeto.

O estado do player **não** é "um objeto com faixa opcional". São quatro estados mutuamente exclusivos:

```ts
export type PlayerState =
  | { type: 'idle' }
  | { type: 'dispatching'; track: Track }
  | { type: 'playing'; playId: PlayId; track: Track; positionMs: number
      anchorEpochMs: number; suggestedBy: string | null
      source: 'guest' | 'host_force'; protectedUntilMs: number | null }
  | { type: 'paused'; playId: PlayId; track: Track; positionMs: number }
```

Um gerador a partir de um schema pydantic produziria, na melhor das hipóteses, campos opcionais — e aí o
código do `/tv` viraria `player.track!.name` em dez lugares. O estado que quebraria é justamente `idle`,
que por [ADR-005](ADR-005-fila-vazia-silencio.md) acontece **de propósito** às 22h30, no monitor grande,
na frente de todos.

Com a união escrita à mão, o compilador recusa acessar `track` sem estreitar por `type`, e a tela de fila
vazia deixa de ser um caso esquecido para ser um ramo obrigatório.

Mesma lógica em `SkipState.blockedReason`: um literal union (`'PROTECTED' | 'TOO_EARLY' | …`) faz o
`switch` do frontend ser exaustivo, e um motivo novo no backend **não compila** até ser tratado na tela.

## Branded types

```ts
declare const brand: unique symbol
type Brand<T, B> = T & { readonly [brand]: B }
export type TrackId  = Brand<string, 'TrackId'>
export type TrackUri = Brand<string, 'TrackUri'>
export type PlayId   = Brand<number, 'PlayId'>
export type GuestId  = Brand<number, 'GuestId'>
```

O par que justifica isso é **`TrackId` × `TrackUri`**: `4iV5W9uYEdYUVa79Axb7Rh` e
`spotify:track:4iV5W9uYEdYUVa79Axb7Rh` são ambos `string`. Mandar um onde se espera o outro compila,
passa pelo `fetch`, e **falha no servidor** — [07 §4](../07-integracao-spotify.md) exige `uris`, e o
Spotify rejeita o id nu.

O único `as` do código-fonte fica na fronteira onde o dado entra
([RNF-22](../02-requisitos-nao-funcionais.md)); depois disso o compilador cuida.

## Consequências

### Positivas

- Uma fonte por canal, nenhuma duplicação manual no HTTP.
- Mudança de contrato quebra o **build**, não a festa.
- A união do WS é o único lugar onde os dois lados podem divergir — e são 60 linhas revisáveis de uma
  vez.

### Negativas

- 🔴 **Gerar os tipos exige o servidor rodando** em `127.0.0.1`. `npm run build` numa máquina sem a API
  no ar falha, e a mensagem de erro não diz isso claramente. Está registrado no `start.ps1`
  ([03 §8](../03-arquitetura.md)), que sobe a API antes de buildar.
- **`api.d.ts` gerado é verboso** e navegar nele à mão é desagradável. Não é para ser lido — é para o
  compilador.
- **`ws.ts` e `models.py` podem divergir** sem ninguém notar até runtime. Mitigação: os dois arquivos
  são citados um no outro por comentário, e o snapshot completo aparece no
  `GET /api/state` ([05 §3](../05-api-http.md)) — que **é** tipado pelo OpenAPI. Ou seja, o mesmo shape
  atravessa um canal gerado e um canal manual, e uma divergência aparece como erro de tipo na store.
  Não é garantia, mas é rede.

O último ponto é o achado: o `GET /api/state` existia por motivo de latência (primeiro paint sem esperar
o handshake do WS) e acabou virando a checagem cruzada entre os dois canais.
