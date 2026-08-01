# 10 — Testes e validação

## 1. O que vale testar num app de uma noite

Não é "cobertura". É: **onde um erro é silencioso, ou onde reproduzir à mão é caro.**

| Testar | Por quê |
|---|---|
| Aritmética de tempo | erra por fator de 1000 sem levantar exceção ([RNF, §2](02-requisitos-nao-funcionais.md)) |
| Ordenação round-rank | erra devolvendo *uma* linha plausível, e ninguém percebe até a festa |
| Ordem das guardas de voto | o bug aparece só sob concorrência, no pico do engajamento |
| Máquina de estados do maestro | reproduzir à mão custa 3,5 min por transição e uma caixa ligada |
| Invariantes do banco | uma linha presa em `playing` para sempre para a fila em silêncio |

| **Não** testar | Por quê |
|---|---|
| Rotas CRUD triviais | pydantic valida, e o erro é imediato e óbvio |
| Componentes Vue | 3 telas, verificadas a olho a cada build |
| O Spotify de verdade | é dependência externa; testamos **nosso** comportamento contra ela |
| Carga / performance | 30 clientes numa LAN. Não há o que medir |

Ferramenta: `pytest` + `pytest-asyncio`. Sem `httpx` real, sem banco em arquivo, sem fixture de rede.

## 2. As duas peças que tornam tudo testável

### 2.1 Relógio injetável

`clock.py` expõe duas funções de módulo ([RNF-07](02-requisitos-nao-funcionais.md)). Os testes as
substituem com `monkeypatch`:

```python
# tests/apoio/relogio.py
class FakeClock:
    def __init__(self, t0: int = 1_700_000_000_000):
        self.mono = 5_000_000      # arbitrário; monotônico não tem significado absoluto
        self.wall = t0
    def advance(self, ms: int) -> None:   # mono e wall JUNTOS: o duplo do Spotify deriva
        self.mono += ms                   # progress_ms da parede, o maestro projeta do mono
        self.wall += ms

# tests/conftest.py
@pytest.fixture
def clk(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr("bq.core.clock.mono_ms", lambda: c.mono)
    monkeypatch.setattr("bq.core.clock.wall_ms", lambda: c.wall)
    return c
```

🔴 **Estas duas strings são caminho-em-string, e têm dois modos de falha muito diferentes.** Mover
`clock.py` sem atualizá-las falha ALTO (`AttributeError` na fixture). Deixar um **shim de
re-export** no caminho antigo falha em SILÊNCIO: o patch acerta o shim, os consumidores continuam
vendo a função real, e a suíte inteira passa medindo o relógio de verdade. É por isso que nenhum
`__init__.py` de `bq/` re-exporta nada, e é por isso que existe `tests/arquitetura/test_relogio.py`
— o único teste do projeto cujo trabalho é garantir que os outros testes não estão mentindo.

**Por que `monkeypatch` e não injeção de dependência.** Passar um objeto `clock` por parâmetro
contaminaria a assinatura de praticamente toda função do sistema — inclusive as que só existem para
fazer uma query. Para 4 módulos com aritmética de tempo ([RNF-24](02-requisitos-nao-funcionais.md)),
patch no teste é o custo menor. A condição para isso funcionar é a regra do
[RNF-07](02-requisitos-nao-funcionais.md): **ninguém chama `time.monotonic()` fora de `clock.py`** — se
um módulo chamar direto, o patch não o alcança e o teste passa medindo o relógio real.

### 2.2 Spotify falso · M1.15

```python
# tests/apoio/spotify.py
class FakeSpotify:
    """Substitui spotify/client.py inteiro. Modela device, playback e latência."""
    def __init__(self, clk: FakeClock, latency_ms: int = 200):
        self.clk, self.latency = clk, latency_ms
        self.playing: str | None = None      # uri
        self.started_wall: int = 0
        self.duration: int = 0
        self.calls: list[tuple[str, str]] = []      # log para asserção de ordem
        self.fail_next: int | None = None           # injeta 404/429/503

    async def play(self, uri: str, duration_ms: int, device_id: str) -> None:
        self.calls.append(("play", uri))
        if self.fail_next: raise SpotifyError(self.fail_next)
        self.clk.advance(self.latency)               # a chamada custa tempo — de propósito
        self.playing, self.started_wall, self.duration = uri, self.clk.wall, duration_ms

    async def get_playback(self) -> Snapshot | None:
        if self.playing is None: return None         # 204 — corpo vazio (07 §6)
        pos = self.clk.wall - self.started_wall
        if pos >= self.duration:
            self.playing = None
            return None
        return Snapshot(uri=self.playing, progress_ms=pos, is_playing=True, type="track")
```

**`self.clk.advance(self.latency)` dentro do `play` é o detalhe que faz este duplo valer.** Sem ele, a
chamada ao Spotify é instantânea no teste e os dois bugs de ordenação do
[05 §4.1](05-api-http.md) **não reproduzem** — eles existem exatamente porque o `PUT` leva 150–400 ms.
Um duplo sem latência dá teste verde e bug em produção.

Com essas duas peças, **uma festa de 20 faixas roda em menos de 5 segundos**, sem rede e sem caixa de
som ligada.

## 3. Testes de mesa

### 3.1 Round-rank · [RF-08](01-requisitos-funcionais.md)

Os 5 cenários de [04 §4.3](04-modelo-de-dados.md) entram como teste tabelar. O de maior valor é o
cenário 5 — contenção sustentada com 4 pessoas repondo a fila a cada execução — que verifica as duas
propriedades juntas:

```python
def test_round_rank_sem_inanicao(db):
    # 4 pessoas, 40 execuções, cada uma repondo a fila a cada faixa tocada
    assert distribuicao == {"Ana": 10, "Bru": 10, "Caio": 10, "Dani": 10}
    assert maior_intervalo <= 4      # = número de pessoas
```

Já verificado fora do projeto, com o DDL de [04 §3](04-modelo-de-dados.md): passa com spread 0 e
intervalo 4. O teste existe para **continuar** passando quando alguém mexer no `ORDER BY`.

E o cenário que corrige a intuição errada, que é o que protege contra "arrumar" o algoritmo:

```python
def test_recem_chegado_vem_antes_da_segunda_do_veterano(db):
    # Ana: A1 A2 A3   |   Dani chega e pede D1
    assert ordem == ["Ana", "Dani", "Ana", "Ana"]
    # NÃO ["Dani", "Ana", ...]: A1 também é rank 0 e foi pedida antes.
```

### 3.2 Guardas de voto · [RF-23](01-requisitos-funcionais.md)

Uma linha de tabela por guarda, mais os casos de fronteira:

| Cenário | Espera |
|---|---|
| `playId` antigo | `STALE_PLAY` |
| durante `dispatching` | `STARTING` |
| 12 s de uma faixa de 4:00 (mínimo = 20 s) | `TOO_EARLY`, `waitMs=8000` |
| 12 s de uma faixa de 0:40 | `TOO_EARLY` — o mínimo é 20 s **em qualquer duração** |
| mínimo de 60 s numa faixa de 0:50 | `TOO_EARLY` do começo ao fim, e `ALMOST_OVER` inalcançável |
| faltando 10 s | `ALMOST_OVER` |
| 30 s após um skip | `SKIP_COOLDOWN` |
| durante proteção | `PROTECTED` |
| votar duas vezes | `200`, contagem **não** dobra |

As linhas 3 e 4 são o par que documenta a saída do teto de 25 %
([ADR-004 §Revisão](adr/ADR-004-skip-5-votos-sem-ttl.md)). A quarta é a que importa e é
contraintuitiva: como `TOO_EARLY` vem **antes** de `ALMOST_OVER` na ordem das guardas e nunca se
resolve, o segundo motivo nunca sai — a faixa não trava-e-destrava, fica travada, e a mensagem
promete uma espera maior do que o que resta da música.

### 3.3 Os quatro testes de regressão que importam

Cada um corresponde a um bug concreto — três deles encontrados por escrito antes de existirem.

```python
async def test_sete_votos_simultaneos_pulam_uma_faixa_so(conductor, clk, fake):
    """05 §4.1 — o cooldown é gravado ANTES do PUT, que custa 200 ms."""
    # 5 votos disparam o skip; os 2 seguintes chegam durante a chamada HTTP
    assert fake.calls.count(("play", ANY)) == 2      # a que pulou + a próxima. NÃO 3.
    assert votos_na_faixa_nova == 0

async def test_retirar_voto_funciona_durante_protecao_e_cooldown(client):
    """RF-22 sem exceção — handlers separados (05 §3)."""
    for estado in ("protegida", "cooldown", "too_early", "almost_over"):
        assert (await delete_skip_vote(estado)).status_code == 200

async def test_204_do_get_playback_nao_mata_o_maestro(conductor, fake):
    """07 §6 — corpo vazio; .json() estouraria. Com fila vazia isso é 1×/s."""
    fake.playing = None
    for _ in range(10): await conductor._step()
    assert conductor.alive and conductor.restarts == 0

async def test_ancoragem_e_na_confirmacao_nao_no_204(conductor, clk, fake):
    """03 §4.5 — ancorar no 204 corta o final de TODAS as músicas."""
    await conductor._dispatch(faixa_de_200s)
    assert conductor.current.state is DISPATCHING     # 204 não confirma
    await conductor._step()                            # poller confirma
    assert conductor.current.state is PLAYING
    fim_previsto = conductor.current.dispatch_next_at_mono
    assert abs(fim_previsto - (clk.mono + 200_000 - 150)) < 50
```

### 3.4 Invariantes

As 7 queries de [04 §5](04-modelo-de-dados.md) rodam **ao fim de cada teste de integração**, como
fixture de teardown. Todas devem devolver 0.

```python
@pytest.fixture(autouse=True)
def _invariantes(db):
    yield
    for nome, q in INVARIANTES.items():
        assert db.execute(q).fetchone()[0] == 0, nome
```

É o teste mais barato do arquivo e o que pega a categoria de bug mais difícil de encontrar de outra
forma: um caminho de saída novo que esquece um dos quatro efeitos de `_end_play()`
([03 §4.6](03-arquitetura.md)) e deixa uma sugestão presa em `playing`. O sintoma em produção é a fila
parando de andar com todos os indicadores verdes.

### 3.5 Simulação de festa

Um teste único, longo, sem asserções finas — só invariantes e sanidade:

```python
async def test_festa_de_quatro_horas(conductor, clk, fake):
    # 30 convidados, sugestões aleatórias, votos aleatórios, 3 force-plays,
    # 2 esvaziamentos de fila, 1 restart do maestro, 4 falhas injetadas do Spotify.
    # 4 horas simuladas em ~5 s.
    assert nunca_dois_plays_abertos
    assert nunca_silencio_com_fila_cheia_por_mais_de_2s
    assert todos_invariantes_zerados
    assert conductor.alive
```

Vale pela terceira asserção: **silêncio com fila cheia** é o modo de falha que nenhum teste unitário
pega, porque emerge da interação entre skip, force-play, fila vazia e falha de rede. É o teste que
justifica o duplo do §2.2 ter latência.

## 4. Validação manual — o ensaio geral

Testes de mesa não cobrem: Bluetooth, iPhone de verdade, legibilidade a 3 metros, e o comportamento de
gente. Uma passada de ~30 min, com a JBL ligada e o monitor no lugar.

| # | Verificar | Como |
|---|---|---|
| V1 | Gap entre faixas ≤ 1 s | cronômetro, 5 transições. **É a tarefa M0.13** |
| V2 | QR funciona no celular de outra pessoa | pedir para alguém apontar a câmera |
| V3 | `/tv` legível a 3 m | ir até onde as pessoas vão ficar e ler |
| V4 | iOS não dá zoom ao focar a busca | iPhone, tocar no campo ([RNF-20](02-requisitos-nao-funcionais.md)) |
| V5 | Celular no bolso 5 min e voltar | a fila está atual, não a de 5 min atrás ([RNF-14](02-requisitos-nao-funcionais.md)) |
| V6 | 5 votos pulam | 5 celulares, ou 5 abas anônimas |
| V7 | Fechar e reabrir o Spotify desktop | `/host` → re-resolver device → volta a tocar |
| V8 | Matar o servidor no meio de uma faixa e subir | fila intacta ([RF-39](01-requisitos-funcionais.md)) |
| V9 | Force-play com música de convidado tocando | ela volta e é a **próxima**; proteção conta no `/tv` |
| V10 | Esvaziar a fila | som para, `/tv` em tela cheia, "Tocar agora" resolve em 1 toque |
| V11 | Wi-Fi do celular oscilando | reconecta sem recarregar |
| V12 | Volume da JBL entre uma faixa antiga e uma moderna | ver o salto e decidir o volume base |
| V13 | Botão "Pular" habilita **sozinho** quando a carência vence | faixa começando, olhar o contador terminar e o botão acender. **Votar no primeiro toque, sem 409** |
| V14 | Botão "Pular" desabilita **sozinho** nos últimos 15 s | não tocar em nada, ver virar "já acabando"; só então tentar votar |
| V15 | Celular NOVO: QR → apelido → sugerir | não volta para a tela do apelido, "Minhas" mostra a música, e o `/tv` passa a contar a pessoa em "N na festa" |

**V13 a V15 cobrem os dois defeitos que a festa revelou** — a guarda de voto que não se anunciava e
o socket que abre antes de existir sessão ([06 §6](06-realtime-websocket.md) e §7). V15 tem de ser
num aparelho **sem cookie prévio**: em aba anônima, ou num celular que nunca abriu a página. Num
aparelho que já entrou hoje o socket já nasce identificado e o defeito não reproduz.

**V12 não tem correção em software.** O Spotify não normaliza loudness em device de terceiros, e o
Connect não expõe ganho por faixa ([RNF, riscos](02-requisitos-nao-funcionais.md#8-riscos-aceitos-explicitamente)).
Está no checklist para você **saber** disso antes da festa e escolher um volume base que aguente o
salto, em vez de descobrir com a casa cheia.

## 5. Critério de release

O `bq` está pronto para a festa quando:

- [ ] M0 e M1 completos, ou M1 com os cortes conscientes de [09](09-plano-implementacao.md#se-o-tempo-apertar)
- [ ] `pytest` verde, incluindo os 4 de §3.3 e a simulação de §3.5
- [ ] `npm run build` limpo, com `vue-tsc` sem erro
- [ ] V1 a V12 verificados **com a JBL ligada e o monitor no lugar**
- [ ] `start.ps1` funciona numa máquina recém-reiniciada, sem passo manual esquecido
- [ ] [runbook](11-runbook-da-festa.md) lido uma vez, com você em frente ao notebook
