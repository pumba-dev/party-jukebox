# ADR-007 — Onde a segurança para, e por quê

**Status:** aceito · 2026-07-31 · decisão do usuário
**Supera:** o §9 inteiro do [DESIGN-v0](../historico/DESIGN-v0.md)

## Contexto

Declaração do usuário: *"não precisa se preocupar tanto com segurança pois só vai ser usado 1 vez e por
pessoas de boa fé, ninguém vai tentar burlar o sistema."*

O modelo de ameaça que isso define é preciso, e vale escrever explicitamente porque é o que autoriza
tudo abaixo:

| | |
|---|---|
| **Atacante** | não existe |
| **Rede** | LAN doméstica, sem internet exposta, sem porta encaminhada |
| **Duração** | uma noite |
| **Pior caso plausível** | um convidado curioso mexendo, ou um acidente honesto |
| **Custo de uma falha** | social, reversível em segundos por uma pessoa com o `/host` |

## Decisão

**Sai tudo que existe para conter má fé. Fica tudo que existe para fazer o jogo funcionar.**

A distinção é essa, e ela não é sobre esforço — é sobre **qual problema o mecanismo resolve**.

### Removido

| Mecanismo do §9 | Contra o que existia |
|---|---|
| Cookie de convidado assinado com HMAC | forjar identidade |
| PIN de LAN + token bucket 3/15 min | acesso não autorizado à rede |
| Shadow-mute de votante abusivo | voto malicioso repetido |
| Blocklist de faixas | sabotagem de catálogo |
| Cap de 3 trocas de voto | oscilação deliberada do contador |
| Idempotência em 3 camadas | replay de requisição |
| Voto por **dispositivo** separado de voto por convidado | multi-conta |
| `protected_until = 0` fora do loopback | comprometimento do `/host` |
| Rate limit genérico por IP | flood |
| Filtro de conteúdo explícito | — (decisão separada do usuário: sem filtro) |

### Mantido

| Mecanismo | Por que **não** é segurança |
|---|---|
| Cooldown de 2 min ([RF-09](../01-requisitos-funcionais.md)) | É a regra que faz a fila ser de todos. Sem ela, quem digita mais rápido ganha. |
| Round-rank ([RF-08](../01-requisitos-funcionais.md)) | É o produto. |
| Dedupe na fila ([RF-11](../01-requisitos-funcionais.md)) | Contra **acidente honesto**: três pessoas amam a mesma música e ela toca três vezes. |
| Janela de repetição de 90 min ([RF-12](../01-requisitos-funcionais.md)) | Idem. |
| Cap de duração de 7 min ([RF-13](../01-requisitos-funcionais.md)) | Contra travar a pista sem querer — e é o que limita o desequilíbrio do [ADR-003](ADR-003-round-rank-vs-wfq.md). |
| 5 votos + guardas ([ADR-004](ADR-004-skip-5-votos-sem-ttl.md)) | É o jogo. |
| **PIN de 4 dígitos no `/host`** ([RF-31](../01-requisitos-funcionais.md)) | Ver abaixo — é o único item que parece segurança e não é. |
| Escape de HTML nos títulos do Spotify | Vue escapa por padrão; custa zero e um `v-html` acidental num título de faixa quebraria o `/tv`. |

## O PIN do `/host` é design de jogo, não segurança

É o item que mais parece contradizer esta decisão, e é o que ela mais precisa justificar.

Com o `/host` aberto, **forçar uma música é sempre mais rápido que convencer 4 pessoas a votar**. No
momento em que duas pessoas descobrem isso — e numa festa isso leva minutos, por contágio, sem nenhuma
má intenção — a votação de skip e a fila justa viram enfeite. Não é um ataque; é o caminho de menor
esforço se revelando para gente de boa fé.

O PIN custa **10 segundos, uma vez**, e preserva os dois mecanismos centrais do produto. Se ele fosse
sobre segurança, 4 dígitos seriam ridículos — e são exatamente adequados para "impedir que o atalho seja
descoberto por acidente".

## O que isso *não* autoriza

Três coisas continuam obrigatórias, e é fácil confundi-las com segurança:

1. 🔴 **Cookies sem a flag `Secure`** ([05 §1](../05-api-http.md)). A festa roda em `http://` na LAN; com
   `Secure`, o browser **não envia o cookie** e não avisa. O sintoma é o app pedir o apelido a cada
   request e o cooldown nunca funcionar. Isso é o oposto de endurecer — é a flag errada quebrando o app.
2. 🔴 **Renomear apelido tem de ser `UPDATE`, não `INSERT`**
   ([RF-03](../01-requisitos-funcionais.md)). Não é anti-fraude: se criar convidado novo, a primeira
   pessoa que trocar o apelido **descobre por acidente** que o cooldown zerou, e conta para as outras.
   É a única defesa de cota que sobrou, e ela é a escolha entre dois verbos SQL.
3. **Limitador na busca** ([RNF-16](../02-requisitos-nao-funcionais.md)). Não protege contra abuso —
   protege a cota do Spotify, que é **por app** e portanto compartilhada por todos
   ([07 §5](../07-integracao-spotify.md)). Uma pessoa segurando uma tecla mata a busca da festa inteira,
   sem querer.

Os três são casos em que "menos segurança" e "mais correção" apontam para o mesmo lado.

## Consequências

### Positivas

- **Menos código, menos superfície, menos a depurar às 22h.** O §9 do documento anterior era, sozinho,
  comparável em volume a metade do resto.
- **Menos fricção para o convidado**: sem PIN, sem login, sem captcha, sem nada. Do QR ao "sugerida" em
  30 s ([S2](../00-visao-e-escopo.md#5-critérios-de-sucesso)) fica alcançável.
- **A especificação fica honesta.** Meia-segurança documentada como segurança é pior que ausência
  declarada.

### Negativas — aceitas explicitamente

- **Limpar cookies zera o cooldown.** Defesa real exigiria identidade real.
- **Um convidado pode votar por dois dispositivos.** O voto é por convidado, não por aparelho.
- **Qualquer um na LAN pode sugerir.** É o objetivo.
- **Se alguém descobrir o PIN, tem controle total.** Resolve-se socialmente
  ([runbook §3.8](../11-runbook-da-festa.md)).

Nenhuma dessas linhas é surpresa na noite: todas estão em
[RNF §8](../02-requisitos-nao-funcionais.md#8-riscos-aceitos-explicitamente).

## Como reverter

🔴 **Se este app algum dia for exposto além da LAN — túnel, ngrok, port forward — este ADR fica inválido
por inteiro**, e não parcialmente. Sem HMAC no cookie, sem rate limit por IP e sem TLS, `/host` com PIN
de 4 dígitos na internet aberta é 10 000 tentativas. A especificação removida está no
[DESIGN-v0 §9](../historico/DESIGN-v0.md).

Fica registrado aqui porque "deixa eu abrir só para o pessoal que não veio" é exatamente o tipo de ideia
que aparece às 23h.
