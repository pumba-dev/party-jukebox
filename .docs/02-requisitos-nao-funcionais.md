# 02 — Requisitos não funcionais

Escrito para a escala real: **~30 convidados, uma noite, uma máquina.** Isso significa que nenhum
requisito aqui é sobre throughput ou escala — a carga de pico (30 WebSockets, ~2 req/s) é atendida
por um processo Python single-threaded com folga de mais de uma ordem de magnitude. Os requisitos que
importam são de **latência percebida, continuidade e disciplina de relógio**, porque é ali que este
sistema em particular falha de forma visível para 30 pessoas ao mesmo tempo.

---

## 1. Latência e continuidade

| # | Requisito | Medida |
|---|---|---|
| **RNF-01** | Sugerir → aparecer na fila do `/tv` e de todos os celulares | ≤ 300 ms |
| **RNF-02** | Silêncio entre o fim de uma faixa e o início da próxima | ≤ 1 000 ms, alvo 400 ms |
| **RNF-03** | Votar → contador atualizar em todas as telas | ≤ 300 ms |
| **RNF-04** | Digitar na busca → resultados na tela | ≤ 800 ms (inclui debounce de 350 ms) |
| **RNF-05** | Barra de progresso do `/tv` avança suavemente, sem saltos visíveis para trás | erro ≤ 500 ms contra a realidade |
| **RNF-06** | Restart do servidor → sistema operante de novo | ≤ 10 s, sem intervenção |

**RNF-02 é o requisito mais difícil desta lista e o único que exige uma técnica específica.** Um
`PUT /me/player/play` custa 150–400 ms de rede mais 200–600 ms de latência interna do Spotify até o
som sair. Se o despacho só começar *depois* de detectar que a faixa acabou, o piso é ~600 ms e o teto
passa de 1,5 s — e com polling de 1 Hz somam-se até 1 000 ms de atraso de detecção, estourando o
requisito. A solução obrigatória está em [03 §4](03-arquitetura.md): **agendar o despacho por relógio
local para ~150 ms antes do fim previsto**, de modo que a chamada HTTP já esteja em voo quando a
faixa terminar. O polling existe apenas como rede de segurança.

**RNF-05 proíbe uma implementação óbvia e errada.** Se a barra de progresso for redesenhada só quando
chega `pstate` do servidor, ela anda em degraus de 1 s. Se ela usar o `progress_ms` do servidor como
verdade a cada mensagem, ela **anda para trás** sempre que a latência de rede variar — e barra que
volta parece travamento. O `/tv` projeta a posição localmente e usa cada `pstate` só para correção
suave. Detalhe em [08 §6](08-frontend.md).

## 2. Disciplina de relógio  🔴

Esta seção é **normativa**. É a que mais provavelmente vai gerar um bug de produção se ignorada,
porque o bug não é uma exceção — é um número silenciosamente errado por um fator de 1000.

`time.monotonic()` do Python devolve **segundos em `float`**. Todas as durações deste sistema estão em
**milissegundos inteiros** (`SKIP_COOLDOWN_MS = 45_000`, `MIN_REMAINING_MS = 15_000`). Comparar um
contra o outro não levanta exceção nenhuma em Python: `12.4 < 45000` é simplesmente `True`, para
sempre. Uma guarda de 45 segundos passa a ser uma guarda de 45 milissegundos e nunca mais recusa
nada — e você só descobre quando cinco pessoas pularem cinco músicas em quinze segundos, na festa.

*(No brief anterior, em Node, o mesmo erro tinha a forma oposta e mais educada: `process.hrtime.bigint()`
devolve BigInt em nanossegundos, e comparar com literal numérico lança `TypeError` na primeira chamada.
Python não oferece essa cortesia.)*

| # | Requisito |
|---|---|
| **RNF-07** | Existem **exatamente duas** funções de tempo no código, e nenhum `time.monotonic()` ou `time.time()` é chamado fora delas. |
| **RNF-08** | Toda duração, timestamp e limiar em memória é `int` de **milissegundos**. Nenhum `float` de segundos atravessa fronteira de função. |
| **RNF-09** | Progresso, prazos e guardas usam o relógio **monotônico**. Registros persistidos usam o relógio de **parede**. Nunca o contrário. |

```python
# bq/core/clock.py — normativo. Nada de tempo entra no sistema por outro caminho.
import time

def mono_ms() -> int:
    """Monotônico, ms. Para medir decorrido, agendar prazos e comparar guardas.
    Imune a ajuste de NTP e a mudança de horário. NÃO tem significado absoluto."""
    return time.monotonic_ns() // 1_000_000

def wall_ms() -> int:
    """Relógio de parede, ms desde a epoch. Para gravar no banco e exibir.
    Pode saltar para frente ou para trás. NUNCA use para medir decorrido."""
    return time.time_ns() // 1_000_000
```

**Por que `monotonic_ns()` e não `monotonic()`:** devolve `int` diretamente, então a divisão inteira
fecha a porta para um `float` de segundos vazar. A escolha existe para tornar o erro
*impossível de escrever*, não apenas improvável.

**Por que os dois relógios, e não um só.** Usar parede para medir decorrido faz um ajuste de NTP no
meio da festa alterar a posição da faixa que está tocando — e a correção pode ser negativa, o que
faz `remaining()` ficar maior que a duração e a transição do RNF-02 nunca ser agendada. Usar
monotônico para gravar no banco produz um histórico cujos timestamps não significam nada depois de
um reboot: `started_at = 4128371` não é uma hora do dia, e o [RF-41](01-requisitos-funcionais.md)
pede um histórico legível.

**Consequência direta em [RF-40](01-requisitos-funcionais.md):** depois de um restart, o relógio
monotônico reiniciou. A posição da faixa em curso **não pode** ser reconstruída de nada que estava em
memória — tem de vir de um `GET /me/player` fresco. Isso não é um detalhe de implementação, é a razão
pela qual RF-40 é M2 e não M0.

## 3. Robustez

| # | Requisito |
|---|---|
| **RNF-10** | Nenhuma falha de chamada ao Spotify derruba o processo, encerra o maestro ou fecha WebSockets. Toda chamada é embrulhada, com o erro registrado e refletido no `/host`. |
| **RNF-11** | O maestro é uma task que **não pode morrer**. Se a corrotina levantar, ela é reiniciada com backoff, e o evento aparece no `/host`. |
| **RNF-12** | O `/tv` roda 6 horas em fullscreen sem recarregar, sem crescimento de memória e sem acumular timers. |
| **RNF-13** | Perda de WebSocket reconecta sozinha com backoff de 0,5 s a 5 s, e o estado é reconstruído por snapshot completo. Nada de replay de eventos. |
| **RNF-14** | Um convidado com a tela apagada e o browser em background volta a ver estado correto ao reabrir, sem recarregar. |

**RNF-11 é a diferença entre "a música parou" e "a festa acabou".** O maestro é o único componente
sem redundância: se ele morrer, tudo continua respondendo — a API aceita sugestões, o `/tv` mostra a
fila, os votos são contados — e **nada toca**. Esse é o pior modo de falha do sistema, porque todos
os indicadores ficam verdes enquanto a sala está em silêncio, e ninguém olha o console. Por isso o
supervisor e o aviso no `/host`.

**RNF-13 dispensa replay de eventos por decisão de escala.** O snapshot completo do estado
(faixa atual + fila + votos + contagem de pessoas) tem ~2 KB. Com 30 clientes, reenviar tudo a cada
mudança é ~60 KB por evento em rede local — irrelevante. Em troca, elimina o buffer circular de
eventos, os números de sequência, a lógica de gap detection e a classe inteira de bugs de estado
divergente. Ver [06 §2](06-realtime-websocket.md).

**RNF-14 é um requisito de iOS, não um requisito genérico.** O Safari em background suspende
WebSocket sem disparar `onclose` de forma confiável. O celular no bolso durante 20 minutos volta com
um socket que parece aberto e não recebe nada — e o convidado vê a fila de 20 minutos atrás,
sugere uma música que já tocou e recebe um erro que não faz sentido para ele. A mitigação está em
[08 §7](08-frontend.md): revalidar em `visibilitychange`, não confiar no estado do socket.

## 4. Orçamento do Spotify

| # | Requisito |
|---|---|
| **RNF-15** | O polling de estado é **um único** poller no processo, a 1 Hz, independente do número de clientes conectados. |
| **RNF-16** | Busca tem cache no servidor e um limitador global. Um `429` na busca nunca afeta o caminho de playback. |
| **RNF-17** | Todo `429` respeita o `Retry-After`. O caminho de playback tem prioridade sobre a busca em caso de contenção. |

O rate limit do Spotify é **por app, em janela deslizante de 30 s, com o valor não divulgado**
(verificado na documentação oficial). Isso tem duas consequências não óbvias:

1. **Não existe isolamento entre convidados.** Uma única pessoa segurando uma tecla na busca gasta o
   orçamento de todos, e o sintoma é a busca morrer para a festa inteira. Daí RNF-16.
2. **A busca e o playback competem pelo mesmo orçamento.** Se a contenção acontecer, o que precisa
   sobreviver é o playback: busca falhando é uma pessoa esperando; playback falhando é silêncio na
   sala. Daí a prioridade explícita do RNF-17.

Orçamento estimado em regime: 2 req/s de polling e despacho + picos de busca. Confortável, mas o
limitador existe porque o valor real do limite é desconhecido e o modo de falha é público.

## 5. Compatibilidade

| # | Requisito |
|---|---|
| **RNF-18** | Funciona em iOS Safari 16+ e Android Chrome recente, sem instalar nada e sem prompt de instalação. |
| **RNF-19** | Alvo de layout do convidado: 360×640 CSS px. Toda ação principal alcançável com o polegar. |
| **RNF-20** | Campos de texto com `font-size` ≥ 16 px, para o iOS não dar zoom ao focar. |
| **RNF-21** | O `/tv` é desenhado para 1920×1080 em modo paisagem, em fullscreen. Não precisa ser responsivo abaixo disso. |

**RNF-20 parece trivial e é o bug de festa mais provável do frontend.** Com fonte menor que 16 px, o
iOS dá zoom automático ao focar o input de busca, o layout sai de lugar, e o convidado precisa dar
pinch para voltar — no meio da primeira interação dele com o app, de pé, com uma bebida na mão.

## 6. Qualidade de código

| # | Requisito |
|---|---|
| **RNF-22** | TypeScript em `strict` com `noUncheckedIndexedAccess`. Zero `any`. Zero `as` fora dos limites de *branded types*. |
| **RNF-23** | Estado de player e mensagens de WebSocket são **uniões discriminadas**, não objetos com campos opcionais. |
| **RNF-24** | Python com type hints em toda assinatura pública e `mypy` limpo nos módulos `clock`, `queue`, `votes` e `conductor`. |
| **RNF-25** | Um comando inicia tudo: `.\start.ps1`. |

**RNF-23 é onde o TypeScript paga por si neste projeto.** O estado do player não é "um objeto com
faixa opcional" — são estados que se excluem: `idle` (fila vazia, nada tocando), `dispatching`,
`playing`, `paused`. Modelar como campos opcionais produz `state.track!.name` espalhado pelo
código e a tela que quebra exatamente no estado raro que ninguém testou — fila vazia às 22h30,
que por [ADR-005](adr/ADR-005-fila-vazia-silencio.md) é um estado **esperado**, não excepcional.
Com união discriminada, o compilador recusa acessar `track` sem antes estreitar por `type`.

**RNF-24 restringe o `mypy` a quatro módulos de propósito.** São os que contêm aritmética de tempo e
ordenação — onde um erro é silencioso e cheio de consequência. Nas rotas HTTP, o pydantic já valida
em runtime e o retorno de tipagem estática é baixo. É uma escolha de onde gastar rigor, não
relaxamento geral.

## 7. Operação

| # | Requisito |
|---|---|
| **RNF-26** | Log em texto no console e em `party.log`, com timestamp de parede. Todo despacho, todo skip, todo erro de Spotify. |
| **RNF-27** | O `/host` mostra saúde num olhar: device resolvido, último `pstate`, maestro vivo, erros recentes. |
| **RNF-28** | Nenhum passo de setup precisa de elevação de privilégio. |

## 8. Riscos aceitos explicitamente

Estes são modos de falha **conhecidos e sem mitigação neste escopo**. Estão aqui para não serem
descobertos na festa como surpresa.

| Risco | Por que é aceito | O que fazer na hora |
|---|---|---|
| 🔴 **Internet cair** | O Web API do Spotify é na nuvem: sem internet, não há busca nem despacho. Um acervo local seria um segundo sistema inteiro. | O app desktop continua tocando o que já estava tocando. Ver [11](11-runbook-da-festa.md). |
| **App desktop do Spotify fechar ou deslogar** | Dependência externa fora do nosso controle ([00 §3](00-visao-e-escopo.md)). | Reabrir; o `/host` tem botão de re-resolver device. |
| **Conta Premium com problema** | Playback via API exige Premium. Sem plano B. | Nenhum. |
| **Salto de loudness entre faixas** | O Spotify **não** normaliza volume em devices de terceiros, e o Connect não expõe ganho por faixa. Um master moderno depois de um dos anos 70 dá 6–10 dB de salto. | Volume na JBL, na mão. |
| **~400 ms de silêncio entre faixas** | Piso da arquitetura Connect. Zerar exigiria pré-enfileirar na fila nativa do Spotify e perder o controle de ordem ([ADR-001](adr/ADR-001-spotify-connect-vs-web-playback-sdk.md)). | Nada. Está dentro do RNF-02. |
| **Convidado limpar cookies e zerar o cooldown** | Defesa real exigiria identidade real. Boa fé assumida ([ADR-007](adr/ADR-007-escopo-de-seguranca-reduzido.md)). | Nada. |
