// A costura entre os dois mundos de tipo (.docs/08-frontend.md §2).
//
// `ws.ts` é escrito à mão porque é o protocolo do WebSocket, que não entra no OpenAPI.
// `api.d.ts` é GERADO do pydantic. Os dois descrevem o MESMO snapshot, então nada garante que
// continuem de acordo — a não ser esta checagem.
//
// 🔴 Se um campo for renomeado no `bq/models.py`, a linha abaixo para de compilar e
// `npm run build` falha (ADR-006). É esse o efeito desejado: a alternativa é o frontend ler
// `undefined` em runtime, na festa.
//
// A direção da checagem importa: o nosso tipo tem ids "marcados" (`TrackId` em vez de `string`),
// que são MAIS estreitos. Um branded string é assignável a string, então `nosso extends gerado`
// compila quando os campos batem — e falha quando um campo muda de nome ou desaparece.

import type { components } from './api'
import type { StateSnapshot } from './ws'

type Gerado = components['schemas']['StateSnapshot']

type Assert<_T extends true> = true
type Extends<A, B> = A extends B ? true : false

export type _SnapshotBate = Assert<Extends<StateSnapshot, Gerado>>
export type _PlayerBate = Assert<Extends<StateSnapshot['player'], Gerado['player']>>
export type _FilaBate = Assert<Extends<StateSnapshot['queue'][number], Gerado['queue'][number]>>
export type _SkipBate = Assert<Extends<StateSnapshot['skip'], Gerado['skip']>>
