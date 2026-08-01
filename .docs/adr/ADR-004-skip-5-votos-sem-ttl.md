# ADR-004 — Skip: 5 votos fixos, sem TTL, escopo = a execução atual

**Status:** aceito · 2026-07-31 · decisão do usuário, mecanismo especificado aqui

## Contexto

A ideia original era "50 % dos usuários ativos votam para pular". Isso exige definir *ativo*, e essa
definição é um poço: conectado? com a tela ligada? que interagiu nos últimos 5 min? O brief anterior
chegou a modelar quatro níveis de presença (`CONNECTED`/`PRESENT`/`ENGAGED`/`COHORT`) com heartbeat
condicionado a `document.visibilityState`.

Pior que o custo é o efeito: **um limiar que se move sozinho é impossível de exibir**. O `/tv` diria
"faltam 2 votos" e, meio minuto depois, "faltam 3" — sem ninguém ter retirado voto. Numa festa isso lê
como o app estar mentindo.

## Decisão

**5 votos, número fixo. O voto vale para a execução atual e vale enquanto ela tocar. Não expira.**

O número é ajustável ao vivo no `/host` ([RF-24](../01-requisitos-funcionais.md)) — Late Mode manual,
por slider.

O mecanismo é a chave: o voto tem chave `(play_id, guest_id)`
([04 §3](../04-modelo-de-dados.md)). Trocar de faixa cria um `play_id` novo, e um `play_id` novo **não
tem votos porque nunca teve**.

## Consequências

### Positivas

- **Zero código de expiração.** Sem timer por voto, sem varredura periódica, sem decidir o que fazer com
  voto que expira no meio de uma contagem. A invalidação é consequência de trocar de música.
- **A regra cabe numa frase no `/tv`**: `PULAR 3/5`. Sem asterisco, sem explicação.
- **Presença deixa de ser um conceito do sistema.** Os quatro níveis, o heartbeat visível e a coorte
  desapareceram junto — não foram simplificados, deixaram de ser necessários.
- **`guestsOnline`** sobrou como número puramente informativo no `/tv`
  ([RF-35](../01-requisitos-funcionais.md)), sem nenhuma regra pendurada nele.

### Negativas

- **5 é certo para ~30 pessoas e errado para 8 ou para 80.** Mitigado pelo slider do
  `/host` — que passa de conveniência a **válvula necessária**
  ([RF-24](../01-requisitos-funcionais.md)), e é por isso que o corte de M1.12 em
  [09](../09-plano-implementacao.md#se-o-tempo-apertar) é marcado como doloroso.
- **O voto sobrevive à pessoa sair da festa.** Quem votou e foi embora deixa o voto contando. Com 5
  fixos e uma faixa de 3,5 min a janela é curta o bastante para isso ser irrelevante.

## As duas regras de mecanismo que não são negociáveis

Estas não são preferência — cada uma corresponde a um bug que já foi escrito e corrigido.

### 1. A retirada é um endpoint separado, sem nenhuma guarda

`DELETE /api/skip-votes` ([05 §3](../05-api-http.md)) não compartilha handler com `POST`.

🔴 No desenho anterior os dois eram o mesmo endpoint com um flag `on`, e as guardas rodavam **antes** de
olhar o flag. Resultado: quem votou e mudou de ideia ficava **preso** no voto assim que a faixa entrasse
em proteção ou cooldown — e o contador do `/tv` seguia contando por ele. O documento prometia
"retração sempre liberada" e o código fazia o contrário.

Dois endpoints tornam a classe de bug inexpressável, em vez de proibida por comentário.

### 2. O cooldown é gravado antes da chamada HTTP

Em `conductor.skip()`, a ordem é: **cooldown → fecha o play → escolhe a próxima → `PUT`**
([05 §4.1](../05-api-http.md)).

🔴 O `PUT` leva 150–400 ms. Na ordem inversa, todo voto que chegar nessa janela ainda encontra
`current` apontando para a faixa já sentenciada: o quinto voto pula, e o sexto e o sétimo — que chegam
80 ms depois, porque a sala está engajada e todos tocaram o botão junto — pulam **a música seguinte**,
que ninguém ouviu. É uma reação em cadeia que só aparece quando a festa está boa.

Coberto por teste em [10 §3.3](../10-testes-e-validacao.md).

## As guardas, e por que cada uma existe

| Guarda | Valor | Sem ela |
|---|---|---|
| mínimo ouvido | `20 s`, ajustável (§Revisão) | pula-se pelos 3 primeiros segundos, antes de a música ser a música |
| falta pouco | < 15 s | gasta-se um skip no que ia acabar sozinho |
| cooldown pós-skip | 45 s | dois skips em cadeia, e ninguém ouve nada |
| proteção | 90 s após force-play | a música do bolo morre em 8 s ([ADR-008](ADR-008-force-play-simples-vs-park-resume.md)) |
| `playId` casando | — | voto de uma tela desatualizada conta contra a faixa errada |

## Revisão — 01/08/2026 · o teto de 25 % sai

**O que muda:** `guards.min_heard_ms()` devolvia `min(S.min_heard_ms, duration_ms // 4)` e passa a
devolver `S.min_heard_ms`, literalmente.

**O argumento original continua válido**, e é este: `min` e não `max` porque uma faixa de 40 s
precisaria esperar 20 s — metade dela — para poder ser pulada. Isso não deixou de ser verdade.

**Por que foi revertido mesmo assim.** O teto era invisível e por isso **mentia**. Com 45 s
ajustados no `/host`, numa faixa de 2:30 o valor efetivo era 37 s; o host mexia no controle, o
número mostrado não era o número em vigor, e não havia tela, log nem erro que dissesse a diferença.
Um limiar que não faz o que diz é pior que um limiar mal escolhido — a mesma razão pela qual este
ADR recusou "50 % dos ativos" no Contexto acima: *um limiar impossível de exibir lê como o app estar
mentindo.* O teto era uma instância pequena do mesmo problema.

O que substitui a proteção automática é **exibição**: o `/host` mostra a janela de voto que resulta
da combinação dos limiares, calculada sobre a faixa que está tocando ([08 §8](../08-frontend.md)).
Trocou-se um piso silencioso por uma consequência visível.

**O que se perdeu, explicitamente.** Nada impede mais `min_heard_ms + min_remaining_ms > duração`, e
nesse estado a faixa é **impossível de pular**: `blocked()` devolve `TOO_EARLY` do começo ao fim.
Como `TOO_EARLY` precede `ALMOST_OVER` na ordem, o segundo fica inalcançável — não é "trava e
destrava", é travado o tempo todo. E a mensagem engana: "deixa ela tocar mais 18 s" numa faixa com
8 s de sobra. Coberto por `test_limiar_maior_que_a_faixa_a_torna_impossivel_de_pular`, que existe
para isso não ser redescoberto na festa.

🔴 **A condição de validade desta reversão é a janela de voto na tela.** Ela é a única coisa que
avisa. Se sair do `/host`, o teto tem de voltar — e aí este parágrafo é a justificativa.

## Nomes de votantes

Só no `/host` ([RF-25](../01-requisitos-funcionais.md)). O `/tv` e os convidados veem contagem.

Isso não é privacidade — é dinâmica de festa. Nome na tela grande transforma "a sala quer trocar de
música" em "o Bruno quer trocar a música da Ana", e o custo social disso é maior que a informação vale.
O snapshot do WebSocket **não contém** a lista ([06 §4](../06-realtime-websocket.md)), então não há como
vazar por descuido de template.
