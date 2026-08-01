# ADR-011 · Karaokê pelo YouTube na `/tv`, com o servidor dono do relógio

**Status:** aceita
**Contexto:** M3. O bq é um jukebox; falta o outro modo de festa — alguém pegar o microfone.

## Problema

O pedido é simples de enunciar e não trivial de atender: o convidado escolhe uma música para
**cantar**; ela entra na fila; quando chega a vez, a `/tv` chama a pessoa pelo nome e espera; ela
começa pelo próprio celular; toca o instrumental com a letra sincronizada; no fim, "Parabéns!", e
a fila normal continua.

Três coisas precisam existir juntas e **em sincronia**: áudio sem voz, letra, e um relógio comum
entre elas.

### O Spotify não pode entregar isto, e a razão é dupla

1. **Não existe remoção de voz na API.** Sem stems, sem parâmetro. O "Sing" com redução vocal é
   feature do cliente japonês (parceria Joysound) e nunca foi exposto na Web API.
2. **Não existe endpoint de letra.** A letra do app vem do Musixmatch e não sai na Web API. Os
   scrapers não-oficiais usam o token interno do app — e depois do aperto de fev/2026 no Dev Mode
   (Premium do dono obrigatório, 1 client ID, 5 usuários, allowlist de endpoints, mirando "uso
   automatizado de risco") isso arrisca a conta que é o **motor de áudio do produto inteiro**.

Separar voz localmente (Demucs e afins) está fora por construção: o bq nunca vê um sample de áudio
([ADR-001](ADR-001-spotify-connect-vs-web-playback-sdk.md)), precisaria baixar a faixa, e custa
minutos de CPU por música com a pessoa esperando de pé.

O Spotify **tem** faixas de playback de gravadoras terceiras no catálogo, e elas tocam
normalmente. Mas aí a letra teria de vir de fora (LRCLIB é grátis e sem chave), indexada pela
**gravação original**, e reproduzida sobre outro arranjo: intro diferente, repetição diferente,
deriva. Letra fora de sincronia numa TV de 40 polegadas, com trinta pessoas olhando, é o pior modo
de falha possível — pior que letra nenhuma.

## Decisão

**Um vídeo de karaokê do YouTube, num iframe da `/tv`.** Ele já traz o instrumental **e** a letra
sincronizada queimada na imagem: o problema de sincronia desaparece porque não há duas fontes de
tempo para conciliar. A cobertura de repertório brasileiro é excelente, e como a `/tv` roda no
mesmo notebook do Spotify desktop, o áudio sai na mesma caixa — nenhum hardware novo.

**Não existe campo de letra no contrato, e não vai existir.** É a consequência que dá sentido à
decisão: buscar letra numa API e renderizar por cima reintroduziria exatamente o problema que a
escolha do vídeo elimina.

### Isto não revisa o ADR-001

O ADR-001 rejeitou *"o backend depender de um cliente browser para a função central, com o estado
autoritativo do player no browser"*. Aqui, as três metades dessa frase continuam valendo:

- **o karaokê não é a função central** — é um modo; o jukebox segue sendo o produto;
- **o servidor continua dono do relógio.** Todo turno tem um teto duro derivado de âncora +
  duração, exatamente como `Play.dispatch_next_at_mono`. Se a `/tv` fechar, travar ou nunca
  reportar, o teto vence, o Spotify volta e a festa continua;
- **a telemetria da `/tv` é refinamento, não autoridade.** Ela move a âncora de posição; ela não
  decide o que toca.

O ADR-009 também fica intacto: a telemetria é um `POST`, não uma `ClientMsg`. O WebSocket continua
estritamente servidor→cliente.

### As decisões estruturais que caem daí

**Um karaokê é uma linha de `track` com `provider='karaoke'`**, id `yt:<videoId>`. O `:` não é
base62, então um id de karaokê nunca colide com um `TrackId` do Spotify por construção. Isso reusa
`suggestion`, `play`, `_end_play`, `ux_sug_active_track`, `repeat_window_ms`, `too_long` e o
`/historico` sem uma linha nova em cada. Uma fila paralela forçaria dois históricos, dois caminhos
de voto e dois `_end_play`.

**A espera fica FORA de `play`.** As três fases são `WAITING` (chamada, sem linha em `play`),
`SINGING` (play normal, aberto) e `CHEERING` ("Parabéns", play fechado). Um play para a chamada
exigiria um `end_reason` novo para "a pessoa não veio", uma linha com `heard_ms = 0` e um item
fantasma no histórico. Sem linha, desistir é um `UPDATE` numa `suggestion`. E `current is None`
durante a chamada é o que mantém `assert self.current is None` em `_open`, o invariante
`ux_play_open` e a saída-cedo de `_reconcile` valendo sem exceção.

**A ordem entre provedores é uma função, não um `ORDER BY`.** "A cada N do conjunto A, um do B"
depende do deslocamento inicial — quantas normais tocaram desde o último karaokê —, que não é
coluna. `queue.ordered()` é a fonte única: `peek_next()` e `listing()` saem dela, e é isso que
impede o `▸ a seguir` de mentir. A dívida é recontada do banco a cada chamada, então sobrevive a
restart de graça, pelo mesmo princípio do round-rank.

**`PlayerState` ganha três variantes em vez de um campo irmão.** Enquanto alguém canta, nada toca
no Spotify: é exclusão mútua, que é a definição de união discriminada. Com um campo
`karaoke: … | null`, cada uma das três telas teria de codificar à mão "se karaoke não é null,
ignore player" — e a primeira que esquecesse mostraria a capa da música anterior enquanto a pessoa
canta. De graça, o voto de skip some estruturalmente: o bloco inteiro do botão exige
`type === 'playing'`, impossível durante um turno. Cinco pessoas calarem quem está cantando na
frente de trinta é um objeto social diferente de pular uma música, e o comportamento certo saiu
sem nenhuma flag.

**A posse do áudio é arbitrada pelo servidor.** Uma segunda `/tv` aberta no celular para espiar
faria a sala ouvir dois players dessincronizados — sem erro, sem exceção, com as duas telas
certas. `POST /api/tv/claim` bate a cada 10 s; a primeira a chegar ganha enquanto continuar
batendo (TTL 25 s), e só a dona monta o iframe. `POST /api/tv/release` no `pagehide` devolve a
posse na hora, para trocar de monitor não custar 25 s de silêncio.

## Consequências

**Boas.** Não há sincronização de letra a manter. O turno inteiro é testável sem browser: a `/tv`
vira uma chamada de função e uma vez completa roda em milissegundos. O desmonte do turno mora
dentro de `_end_play`, então skip, force-play, erro e fim natural viram "Parabéns" de graça.

**Custos aceitos.**

- **Autoplay.** O Chrome barra áudio sem gesto do usuário. Mitigado por perfil dedicado +
  `--autoplay-policy=no-user-gesture-required` (`start.ps1 -Tv`), resgate por barra de espaço, e
  um teto no servidor — a festa não morre por causa de uma política de autoplay.
- **Cota do YouTube.** `search.list` custa 100 de 10.000 unidades/dia: ~99 buscas não cacheadas
  para a festa inteira. O cache de 6 h não é otimização, é o que torna a feature viável.
- **A camada 2 fica empatada** (`bq.spotify` = `bq.youtube`), o que enfraquece a "ordem total" do
  ADR-010. `tests/arquitetura/test_camadas.py` ganhou a regra que o empate não cobre: os dois
  clientes externos não se conhecem.
- **`_stalled()` passa a espelhar duas coisas** — `passive`/`paused` espelham a guarda de `_step`;
  `karaoke_only` é a ordenação recusando tudo. Causa diferente, mesma pergunta do campo.
- **Telemetria sem autenticação.** Aceito sob ADR-007, com três controles compensatórios
  (validação estrita, `playId` prendendo o relatório à vez aberta, escopo mínimo) e um gancho de
  token documentado em `routes/karaoke.py`.
- **Anúncio de pré-roll.** Só a conta Premium no perfil dedicado resolve. Não tentamos detectar
  nem pular programaticamente: durante o anúncio o estado também é `PLAYING`, e seria contra os
  termos.

## Alternativas rejeitadas

**Faixa de playback do Spotify + letra do LRCLIB.** Manteria tudo num provedor só e reusaria o
despacho existente. Rejeitada pela deriva: a letra é indexada pela gravação original e o arranjo
do playback é outro.

**Separação de voz local (Demucs).** Rejeitada por construção — o bq nunca vê áudio — e por custo:
minutos de CPU por música.

**Um `Protocol` de provedor de playback.** Unificaria a *forma* (`get_playback() -> Poll`) mas não
o que difere: quem é autoridade sobre o fim, o que "sem dado" significa, e se existe um portão
humano antes do início. E mudaria o construtor do `Conductor`, e portanto todos os duplos.

**Filas separadas por provedor.** Duas listas obrigariam cada tela a re-derivar "1 karaokê a cada
N" — três implementações do mesmo algoritmo, e o `▸ a seguir` mentindo na primeira que divergisse.

**Votação para pular quem canta.** Ver acima: some estruturalmente, e é o comportamento certo.

**Readotar um karaokê em curso no restart.** Impossível sem canal servidor→cliente: a `/tv`
recarrega (o `bootId` muda) e o vídeo morre com ela. `adopt()` devolve a sugestão a `queued`
mantendo o rank, e a pessoa recupera a vez do começo.
