"""M2 — modo passivo (RF-19), readoção após restart (RF-40), bump (RF-30), histórico (RF-42).

Duas destas quatro só se testam de mesa. "Alguém mexeu no Spotify pelo celular três vezes" e
"o processo caiu com música tocando" são reproduzíveis à mão uma vez cada, com paciência e uma
caixa de som — e nenhuma das duas se reproduz na ordem certa quando você quer.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bq import db, guests, history, queue, runtime
from bq.conductor import MAX_EXTERNAL_STRIKES, Conductor
from bq.play import PlayState
from bq.party import party
from bq.spotify.device import DeviceResolver

from .conftest import FakeClock
from .fake_spotify import FakeSpotify
from .test_api import client, seed_track  # noqa: F401  (fixtures reusadas)
from .test_conductor import build, enqueue, simulate
from .test_ws_e_host import entrar, virar_host

OUTRA = "spotify:track:9999999999999999999999"


def sequestrar(fake: FakeSpotify, uri: str = OUTRA) -> None:
    """Alguém deu play em outra coisa na mesma conta, por fora do bq."""
    fake.playing = uri
    fake.started_wall = fake.clk.wall
    fake.duration = 600_000


async def sequestro_completo(cond: Conductor, fake: FakeSpotify, clk: FakeClock) -> None:
    """Sequestra e dá tempo de o maestro detectar, retomar e **confirmar** a retomada.

    Os 2,5 s não são folga arbitrária: o poller roda a 1 Hz, então detectar custa até 1 s e
    confirmar a retomada custa outro poll. Com menos, a faixa retomada fica em DISPATCHING e o
    sequestro seguinte cai no caminho de `_chase_confirmation` — que é um cenário diferente, com
    teste próprio.
    """
    sequestrar(fake)
    await simulate(cond, clk, 2_500)


# --- RF-19 · modo passivo ---------------------------------------------------------------------


async def test_uma_mudanca_externa_retoma_o_controle(clk: FakeClock, guest: guests.Guest) -> None:
    """Antes de desistir, o sistema BRIGA — é a primeira metade de RF-19."""
    cond, fake = build(clk)
    for n in (1, 2, 3):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)

    await simulate(cond, clk, 2_000)
    assert cond.current is not None
    primeira = cond.current.play_id

    sequestrar(fake)
    await simulate(cond, clk, 1_500)

    assert party.external_strikes == 1
    assert cond.passive is False, "uma vez não é rendição"
    assert cond.current is not None and cond.current.play_id != primeira
    assert fake.playing not in (OUTRA, None), "retomamos o controle do device"


async def test_tres_seguidas_viram_modo_passivo(clk: FakeClock, guest: guests.Guest) -> None:
    """RF-19. Ao terceiro sequestro o sistema para de brigar — e para de despachar."""
    cond, fake = build(clk)
    for n in range(1, 7):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)

    await simulate(cond, clk, 2_000)
    for _ in range(MAX_EXTERNAL_STRIKES):
        await sequestro_completo(cond, fake, clk)

    assert party.external_strikes == MAX_EXTERNAL_STRIKES
    assert cond.passive is True
    assert cond.current is None

    # e agora NÃO despacha, mesmo com fila cheia e tempo passando
    antes = len(fake.starts)
    await simulate(cond, clk, 10_000)
    assert len(fake.starts) == antes, "modo passivo não despacha"
    assert queue.size() > 0, "a fila continua lá, esperando o host resolver"


async def test_faixa_que_toca_inteira_zera_os_strikes(clk: FakeClock, guest: guests.Guest) -> None:
    """🔴 RF-19 diz 3 mudanças externas **SEGUIDAS**.

    Sem o reset, um sequestro às 21h e outro às 23h somariam, e o sistema entraria em passivo por
    dois incidentes sem relação nenhuma — com o sintoma aparecendo horas depois da causa.
    """
    cond, fake = build(clk)
    for n in range(1, 6):
        enqueue(fake, guest.id, n, 4_000, clk.wall + n)

    await simulate(cond, clk, 1_500)
    sequestrar(fake)
    await simulate(cond, clk, 1_500)
    assert party.external_strikes == 1

    # deixa a próxima tocar até o fim
    await simulate(cond, clk, 6_000)
    assert party.external_strikes == 0, "a série quebrou"

    sequestrar(fake)
    await simulate(cond, clk, 1_500)
    assert party.external_strikes == 1 and cond.passive is False


async def test_reativar_volta_a_despachar(clk: FakeClock, guest: guests.Guest) -> None:
    cond, fake = build(clk)
    for n in range(1, 7):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)
    await simulate(cond, clk, 2_000)
    for _ in range(MAX_EXTERNAL_STRIKES):
        await sequestro_completo(cond, fake, clk)
    assert cond.passive is True

    await cond.reactivate()
    assert cond.passive is False and party.external_strikes == 0

    await simulate(cond, clk, 2_000)
    assert cond.current is not None, "voltou a tocar a fila"


async def test_snapshot_conta_por_que_a_fila_parou(clk: FakeClock, guest: guests.Guest) -> None:
    """Sem `stalled`, o /tv mostraria "a fila está vazia" com a fila cheia (models.py)."""
    from bq import snapshot

    cond, fake = build(clk)
    for n in range(1, 7):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)
    await simulate(cond, clk, 2_000)

    assert snapshot.build(None).stalled is None
    for _ in range(MAX_EXTERNAL_STRIKES):
        await sequestro_completo(cond, fake, clk)

    s = snapshot.build(None)
    assert s.stalled == "passive"
    assert s.player.type == "idle" and len(s.queue) > 0, "é exatamente o par que mentia"


async def test_pausa_do_host_tambem_aparece_no_stalled(clk: FakeClock, guest: guests.Guest) -> None:
    """A pausa de RF-28 tinha o mesmo bug de tela, e ele já existia antes de M2.3."""
    from bq import snapshot
    from bq.party import S

    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 60_000, clk.wall)
    await simulate(cond, clk, 2_000)

    await cond.pause()
    assert snapshot.build(None).stalled == "paused"
    await cond.resume()
    assert snapshot.build(None).stalled is None
    assert S.paused is False


# --- RF-40 · readoção após restart ------------------------------------------------------------


def reiniciar(clk: FakeClock, fake: FakeSpotify) -> Conductor:
    """Um processo novo com o MESMO banco e o mesmo Spotify. `anchor_mono` não sobrevive."""
    resolver = DeviceResolver(cast(Any, fake), fake.device_name)
    novo = Conductor(cast(Any, fake), resolver)
    runtime.conductor = novo
    runtime.device = resolver
    return novo


async def test_readota_a_faixa_que_estava_tocando(clk: FakeClock, guest: guests.Guest) -> None:
    """RF-40. Reiniciar no meio da música não recomeça a música."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 200_000, clk.wall)
    await simulate(cond, clk, 2_000)
    assert cond.current is not None
    play_id = cond.current.play_id

    await simulate(cond, clk, 40_000)  # ~42 s de música ouvida
    posicao_real = clk.wall - fake.started_wall

    novo = reiniciar(clk, fake)
    await novo.adopt()

    assert novo.current is not None, "readotou em vez de largar"
    assert novo.current.play_id == play_id, "é o MESMO play, não um novo"
    assert novo.current.state is PlayState.PLAYING
    assert abs(novo.current.start_pos_ms - posicao_real) < 500, "continuou de onde estava"
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 1


async def test_readocao_nao_duplica_o_play(clk: FakeClock, guest: guests.Guest) -> None:
    """A consequência prática: a próxima faixa entra na hora certa, não 3 min depois."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 10_000, clk.wall)
    enqueue(fake, guest.id, 2, 10_000, clk.wall + 1)
    await simulate(cond, clk, 6_000)

    novo = reiniciar(clk, fake)
    await novo.adopt()
    await simulate(novo, clk, 8_000)

    plays = db.q("SELECT id, end_reason FROM play ORDER BY id")
    assert len(plays) == 2, f"uma readoção + uma nova, não três: {[dict(p) for p in plays]}"
    assert plays[0]["end_reason"] == "finished"


async def test_spotify_seguiu_para_outra_faixa_enquanto_estavamos_fora(
    clk: FakeClock, guest: guests.Guest
) -> None:
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 200_000, clk.wall)
    await simulate(cond, clk, 2_000)

    sequestrar(fake)
    novo = reiniciar(clk, fake)
    await novo.adopt()

    assert novo.current is None
    assert db.scalar("SELECT end_reason FROM play WHERE id=1") == "external"
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 0


async def test_ficamos_fora_mais_tempo_que_a_musica(clk: FakeClock, guest: guests.Guest) -> None:
    """Servidor fora do ar 10 min: a faixa acabou sozinha. `finished`, não `error`."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 30_000, clk.wall)
    await simulate(cond, clk, 2_000)

    clk.advance(600_000)
    fake.playing = None  # o Spotify parou em algum momento
    novo = reiniciar(clk, fake)
    await novo.adopt()

    assert db.scalar("SELECT end_reason FROM play WHERE id=1") == "finished"


async def test_poll_falhando_no_boot_nao_trava_a_fila(clk: FakeClock, guest: guests.Guest) -> None:
    """🔴 O motivo real de a readoção não ser opcional.

    `ux_play_open` só admite um play aberto. Se o boot deixasse a linha órfã, o próximo despacho
    estouraria no INSERT — a fila para com a fila cheia, e o log fala de índice único em vez de
    falar de restart.
    """
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 200_000, clk.wall)
    enqueue(fake, guest.id, 2, 20_000, clk.wall + 1)
    await simulate(cond, clk, 2_000)

    fake.fail_poll = True
    novo = reiniciar(clk, fake)
    await novo.adopt()
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 0, "nada órfão"

    fake.fail_poll = False
    await simulate(novo, clk, 3_000)
    assert novo.current is not None, "a fila voltou a andar"


async def test_sem_nada_aberto_a_readocao_e_no_op(clk: FakeClock, guest: guests.Guest) -> None:
    cond, fake = build(clk)
    novo = reiniciar(clk, fake)
    await novo.adopt()
    assert novo.current is None and db.scalar("SELECT COUNT(*) FROM play") == 0


# --- RF-30 · bump -----------------------------------------------------------------------------


def fila(c: TestClient) -> list[str]:
    return [i["track"]["name"] for i in c.get("/api/state").json()["queue"]]


def test_bump_leva_para_a_frente(client: TestClient) -> None:  # noqa: F811
    entrar(client, "Ana")
    a1 = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    entrar(client, "Bru")
    b1 = client.post("/api/suggestions", json={"trackId": seed_track(2)}).json()["suggestionId"]
    assert fila(client) == ["Faixa 1", "Faixa 2"]

    virar_host(client)
    assert client.post(f"/api/host/suggestions/{b1}/bump").status_code == 200
    assert fila(client) == ["Faixa 2", "Faixa 1"]
    assert a1  # a da Ana continua na fila, só atrás


def test_dois_bumps_seguidos_respeitam_a_ordem_dos_cliques(client: TestClient) -> None:  # noqa: F811
    """🔴 Com `rank = -1` fixo, os dois empatariam e o desempate seria `suggested_at` — o host
    clica em C por último e C não vai para a frente. Ele lê isso como o botão não funcionar."""
    for n, nome in ((1, "Ana"), (2, "Bru"), (3, "Cadu")):
        entrar(client, nome)
        client.post("/api/suggestions", json={"trackId": seed_track(n)})

    ids = [i["suggestionId"] for i in client.get("/api/state").json()["queue"]]
    virar_host(client)
    client.post(f"/api/host/suggestions/{ids[1]}/bump")  # Faixa 2
    client.post(f"/api/host/suggestions/{ids[2]}/bump")  # Faixa 3, depois
    assert fila(client) == ["Faixa 3", "Faixa 2", "Faixa 1"]


def test_bump_de_sugestao_que_nao_esta_na_fila(client: TestClient) -> None:  # noqa: F811
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    client.delete(f"/api/suggestions/{sid}")
    virar_host(client)
    r = client.post(f"/api/host/suggestions/{sid}/bump")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_QUEUED"
    assert client.post("/api/host/suggestions/9999/bump").status_code == 404


def test_bump_exige_host(client: TestClient) -> None:  # noqa: F811
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    r = client.post(f"/api/host/suggestions/{sid}/bump")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_HOST"


def test_bump_nao_devolve_cota_nem_mexe_no_cooldown(client: TestClient) -> None:  # noqa: F811
    """É reordenação, não sugestão nova."""
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    antes = client.get("/api/state").json()["me"]["cooldownUntilMs"]
    # 🔴 Guarda o COOKIE, não o apelido: `entrar()` limpa os cookies, e reentrar com o mesmo
    # apelido cria um convidado NOVO — a identidade é o cookie (RF-04). Sem isto o teste comparava
    # o cooldown da Ana com o de uma Ana recém-nascida e passava por acidente.
    cookie_ana = client.cookies.get("bq_guest")
    assert cookie_ana

    virar_host(client)
    client.post(f"/api/host/suggestions/{sid}/bump")

    client.cookies.clear()
    client.cookies.set("bq_guest", cookie_ana)
    assert client.get("/api/state").json()["me"]["cooldownUntilMs"] == antes


# --- RF-42 · histórico ------------------------------------------------------------------------


def semear_historico() -> None:
    """Duas execuções fechadas e um voto. Fabricado direto no banco: isto é caminho de LEITURA,
    e o caminho de escrita tem os testes do maestro."""
    seed_track(1)
    seed_track(2)
    g = guests.create("Ana")
    b = guests.create("Bru")
    db.run(
        "INSERT INTO play (id,track_id,guest_id,source,started_at,ended_at,end_reason,"
        "duration_ms,heard_ms) VALUES (1,?,?,'guest',1000,200000,'finished',200000,199000)",
        (f"{1:022d}", g.id),
    )
    db.run(
        "INSERT INTO play (id,track_id,guest_id,source,started_at,ended_at,end_reason,"
        "duration_ms,heard_ms) VALUES (2,?,?,'guest',300000,340000,'skip_vote',200000,40000)",
        (f"{2:022d}", b.id),
    )
    db.run("INSERT INTO skip_vote (play_id,guest_id,voted_at) VALUES (2,?,400)", (g.id,))


def test_historico_em_ordem_reversa_com_resumo(base: None) -> None:
    semear_historico()
    h = history.build(with_voters=True)

    assert [i.play_id for i in h.items] == [2, 1], "mais recente primeiro"
    assert h.items[0].end_reason == "skip_vote" and h.items[0].skip_votes == 1
    assert h.items[0].voters == ["Ana"]
    assert h.items[1].heard_ms == 199_000 and h.items[1].suggested_by == "Ana"
    assert h.summary.plays == 2
    assert h.summary.heard_ms == 239_000
    assert h.summary.guests == 2
    assert h.summary.skipped == 1


def test_historico_ignora_play_aberto(base: None) -> None:
    """A faixa que está tocando AGORA não é história ainda."""
    semear_historico()
    db.run(
        "INSERT INTO play (id,track_id,source,started_at,duration_ms)"
        " VALUES (3,?,'host_force',500000,200000)",
        (f"{1:022d}",),
    )
    assert [i.play_id for i in history.build(with_voters=False).items] == [2, 1]


def test_votantes_so_para_o_host(client: TestClient) -> None:  # noqa: F811
    """🔴 RF-25 pela porta lateral. Se o histórico fosse público com nomes, a regra de "nome de
    votante só no /host" estaria burlada por uma rota que ninguém pensa em revisar."""
    semear_historico()

    anonimo = client.get("/api/history").json()
    assert anonimo["summary"]["plays"] == 2, "o resto do histórico é público"
    assert anonimo["items"][0]["skipVotes"] == 1, "o número, sim"
    assert anonimo["items"][0]["voters"] == [], "os nomes, não"

    virar_host(client)
    dono = client.get("/api/history").json()
    assert dono["items"][0]["voters"] == ["Ana"]


def test_historico_vazio_no_comeco_da_festa(client: TestClient) -> None:  # noqa: F811
    h = client.get("/api/history").json()
    assert h["items"] == []
    assert h["summary"] == {"plays": 0, "heardMs": 0, "guests": 0, "skipped": 0}


@pytest.mark.parametrize("rota", ["/historico", "/host", "/tv"])
def test_spa_serve_as_rotas_do_frontend(client: TestClient, rota: str) -> None:  # noqa: F811
    """O catch-all de history mode: recarregar em /historico não pode dar 404 (05 §6)."""
    r = client.get(rota)
    assert r.status_code in (200, 404), r.status_code
    if r.status_code == 404:
        pytest.skip("web/dist não buildado neste ambiente")
    assert "text/html" in r.headers["content-type"]
