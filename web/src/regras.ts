// As regras do jogo como o /host as escolhe (RF-24): rótulo, explicação e as opções de cada limiar.
//
// Módulo separado e não uma const dentro do `HostView.vue` por dois motivos concretos:
//
// 1. São ~170 linhas de DADO, sem uma linha de Vue. No arquivo da tela elas afogavam o estado e o
//    ciclo de vida, que é o que se precisa ler quando se abre o `HostView`.
// 2. `<script setup>` não exporta, e o assert de cobertura no fim deste arquivo precisa ser
//    exportado para não cair no `noUnusedLocals` — o mesmo idioma de `types/contract.ts`.
//
// Três grupos, e o agrupamento é VERDADEIRO, não decorativo: o grupo "Pular" é exatamente o que
// `guards.blocked()` lê, na ordem em que lê (`bq/domain/guards.py`).
//
// As listas de opções são escritas à mão em vez de geradas por um helper de formatação. É mais
// linhas e vale: cada rótulo é uma frase em português ("na hora", "a noite toda"), e a lista É a
// recomendação que o host lê. O `le` do pydantic é maior que o último preset em todos os campos, e
// um valor fora da lista aparece como "Personalizado" (ver `components/CampoSelect.vue`).

import type { components } from './types/api'

/** Os limiares ajustáveis. Vem do `SettingsPatch` de propósito: ele é exatamente o conjunto que o
 *  servidor aceita escrever, então acrescentar um limiar no backend faz este tipo crescer sozinho —
 *  e o assert de cobertura no fim deste arquivo passa a falhar. */
export type Chave = keyof components['schemas']['SettingsPatch']

/** 3:30 — a duração de referência da janela de voto quando não há nada tocando. */
export const REFERENCIA_MS = 210_000

export const GRUPOS = [
  {
    titulo: 'Pular',
    janela: true, // este grupo ganha a linha da janela de voto no pé
    campos: [
      {
        key: 'skipVotesNeeded',
        rotulo: 'Votos para pular',
        ajuda:
          'Quantas pessoas precisam votar para a música ir embora. Com 30 convidados, 5 votos é ' +
          'cerca de um sexto da sala; com 8 pessoas, é goleada.',
        opcoes: [
          { valor: 1, rotulo: '1 voto — o primeiro que tocar' },
          { valor: 2, rotulo: '2 votos' },
          { valor: 3, rotulo: '3 votos' },
          { valor: 4, rotulo: '4 votos' },
          { valor: 5, rotulo: '5 votos' },
          { valor: 6, rotulo: '6 votos' },
          { valor: 8, rotulo: '8 votos' },
          { valor: 10, rotulo: '10 votos' },
          { valor: 15, rotulo: '15 votos' },
        ],
      },
      {
        key: 'minHeardMs',
        rotulo: 'Esperar antes de liberar o voto',
        ajuda:
          'Quanto a música toca antes de o botão de pular funcionar. Em "na hora", dá para pular ' +
          'nos primeiros segundos, antes de a música ser a música.',
        opcoes: [
          { valor: 0, rotulo: 'na hora' },
          { valor: 10_000, rotulo: '10 s' },
          { valor: 20_000, rotulo: '20 s' },
          { valor: 30_000, rotulo: '30 s' },
          { valor: 45_000, rotulo: '45 s' },
          { valor: 60_000, rotulo: '1 min' },
          { valor: 90_000, rotulo: '1 min 30' },
          { valor: 120_000, rotulo: '2 min' },
        ],
      },
      {
        key: 'minRemainingMs',
        rotulo: 'Não pular no fim',
        ajuda:
          'Perto do fim o voto é recusado: não vale gastar um pulo no que ia acabar sozinho. Em ' +
          '"até o último segundo", dá para pular a qualquer momento.',
        opcoes: [
          { valor: 0, rotulo: 'até o último segundo' },
          { valor: 5_000, rotulo: 'últimos 5 s' },
          { valor: 10_000, rotulo: 'últimos 10 s' },
          { valor: 15_000, rotulo: 'últimos 15 s' },
          { valor: 20_000, rotulo: 'últimos 20 s' },
          { valor: 30_000, rotulo: 'últimos 30 s' },
          { valor: 45_000, rotulo: 'últimos 45 s' },
          { valor: 60_000, rotulo: 'último minuto' },
        ],
      },
      {
        key: 'skipCooldownMs',
        rotulo: 'Espera depois de um pulo',
        ajuda:
          'Depois que uma música é pulada, ninguém pode votar por este tempo. Em "na hora", dois ' +
          'pulos em cadeia e a sala não ouve nada.',
        opcoes: [
          { valor: 0, rotulo: 'na hora' },
          { valor: 15_000, rotulo: '15 s' },
          { valor: 30_000, rotulo: '30 s' },
          { valor: 45_000, rotulo: '45 s' },
          { valor: 60_000, rotulo: '1 min' },
          { valor: 120_000, rotulo: '2 min' },
          { valor: 300_000, rotulo: '5 min' },
        ],
      },
    ],
  },
  {
    titulo: 'Sugerir',
    janela: false,
    campos: [
      {
        key: 'suggestCooldownMs',
        rotulo: 'Espera entre sugestões',
        ajuda:
          'Quanto cada pessoa espera para sugerir de novo. É a cota que faz a fila não ser de uma ' +
          'pessoa só. Tentativa recusada não gasta a vez.',
        opcoes: [
          { valor: 0, rotulo: 'sem espera' },
          { valor: 30_000, rotulo: '30 s' },
          { valor: 60_000, rotulo: '1 min' },
          { valor: 120_000, rotulo: '2 min' },
          { valor: 180_000, rotulo: '3 min' },
          { valor: 300_000, rotulo: '5 min' },
          { valor: 600_000, rotulo: '10 min' },
          { valor: 1_800_000, rotulo: '30 min' },
        ],
      },
      {
        key: 'maxDurationMs',
        rotulo: 'Duração máxima',
        ajuda:
          'Músicas mais longas que isso não entram na fila. Segura o set de 12 minutos que trava a ' +
          'pista sem ninguém querer.',
        opcoes: [
          { valor: 60_000, rotulo: '1 min' },
          { valor: 180_000, rotulo: '3 min' },
          { valor: 300_000, rotulo: '5 min' },
          { valor: 420_000, rotulo: '7 min' },
          { valor: 600_000, rotulo: '10 min' },
          { valor: 900_000, rotulo: '15 min' },
          { valor: 1_800_000, rotulo: '30 min' },
        ],
      },
      {
        key: 'repeatWindowMs',
        rotulo: 'Não repetir por',
        ajuda:
          'Uma música que já tocou não pode ser sugerida de novo dentro deste tempo. Em "pode ' +
          'repetir", a mesma música pode voltar na hora seguinte.',
        opcoes: [
          { valor: 0, rotulo: 'pode repetir' },
          { valor: 1_800_000, rotulo: '30 min' },
          { valor: 3_600_000, rotulo: '1 h' },
          { valor: 5_400_000, rotulo: '1 h 30' },
          { valor: 7_200_000, rotulo: '2 h' },
          { valor: 10_800_000, rotulo: '3 h' },
          { valor: 21_600_000, rotulo: '6 h' },
          { valor: 86_400_000, rotulo: 'a noite toda' },
        ],
      },
    ],
  },
  {
    titulo: 'Tocar agora',
    janela: false,
    campos: [
      {
        key: 'protectMs',
        rotulo: 'Proteção da música que você forçou',
        ajuda:
          'Quando você força uma música, ela não pode ser pulada por votos durante este tempo. É o ' +
          'que impede a música do bolo de morrer em 8 segundos.',
        opcoes: [
          { valor: 0, rotulo: 'sem proteção' },
          { valor: 30_000, rotulo: '30 s' },
          { valor: 45_000, rotulo: '45 s' },
          { valor: 60_000, rotulo: '1 min' },
          { valor: 90_000, rotulo: '1 min 30' },
          { valor: 120_000, rotulo: '2 min' },
          { valor: 300_000, rotulo: '5 min' },
          { valor: 600_000, rotulo: '10 min' },
        ],
      },
    ],
  },
] as const

// 🔴 Cobertura: garante que TODO limiar que o servidor aceita escrever tem controle na tela. Sem
// isto, acrescentar um limiar no backend (são 5 lugares lá, ver o CLAUDE.md) simplesmente não
// apareceria aqui — em silêncio, e o host nunca saberia que ele existe. Foi o caso de `minHeardMs` e
// `minRemainingMs`, que o `PATCH` aceitava desde M1 e nenhuma tela mostrava.
//
// Mesmo idioma de `types/contract.ts`, e pelo mesmo motivo: o erro tem de ser no build, não na festa.
type Assert<_T extends true> = true
type NaTela = (typeof GRUPOS)[number]['campos'][number]['key']

export type _TodoLimiarTemControle = Assert<
  [Exclude<Chave, NaTela>] extends [never] ? true : false
>
