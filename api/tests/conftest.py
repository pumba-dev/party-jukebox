"""Fixtures. As duas peças que tornam tudo testável (.docs/10-testes-e-validacao.md §2)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Antes de qualquer import de bq: config.py valida no import e aborta se faltar chave.
#
# Atribuição, não `setdefault`: com `setdefault`, um `HOST_PIN` exportado no shell (ou vindo do
# `.env` de verdade) vencia, e os testes do /host falhavam com BAD_PIN por um motivo que não tem
# nada a ver com o que eles testam. Suíte tem de ser hermética.
os.environ["SPOTIFY_CLIENT_ID"] = "test-client-id"
os.environ["SPOTIFY_CLIENT_SECRET"] = "test-client-secret"
os.environ["HOST_PIN"] = "1234"
os.environ["BIND_PORT"] = "8080"
os.environ["LAN_IP"] = "192.168.0.10"  # não depende de adaptador de rede no teste
# o log do teste não vai para api\party.log, senão o histórico da festa nasce sujo
os.environ["LOG_PATH"] = str(Path(tempfile.gettempdir()) / "bq-test.log")

from bq import db, guests, runtime, tracks  # noqa: E402
from bq.party import S, party  # noqa: E402
from bq.spotify.client import TrackData  # noqa: E402


class FakeClock:
    """Relógio de mesa. `mono` é arbitrário — monotônico não tem significado absoluto."""

    def __init__(self, t0: int = 1_700_000_000_000) -> None:
        self.mono = 5_000_000
        self.wall = t0

    def advance(self, ms: int) -> None:
        self.mono += ms
        self.wall += ms


@pytest.fixture
def clk(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """🔴 Patch no MÓDULO `bq.clock`, e é por isso que nenhum módulo pode fazer
    `from .clock import mono_ms`: com o nome importado direto, o patch não alcança o chamador
    e o teste passa medindo o relógio de verdade (10 §2.1 / RNF-07)."""
    c = FakeClock()
    monkeypatch.setattr("bq.clock.mono_ms", lambda: c.mono)
    monkeypatch.setattr("bq.clock.wall_ms", lambda: c.wall)
    return c


@pytest.fixture
def base(tmp_path: Path) -> Iterator[None]:
    db.close()
    db.connect(tmp_path / "test.db")
    S.reload()
    party.skip_cooldown_until = 0
    party.external_strikes = 0
    party.host_tokens.clear()
    party.recent_errors.clear()
    # Os singletons de bq.runtime são globais de módulo: sem zerar, um maestro de outro teste
    # sobrevive e `votes.cast` vota na faixa errada — que foi exatamente o que aconteceu.
    runtime.conductor = None
    runtime.hub = None
    runtime.spotify = None
    runtime.device = None
    yield
    # Invariantes no teardown de TODO teste: uma linha presa em `playing` para sempre para a
    # fila em silêncio, e o custo de descobrir isso aqui em vez de na festa é uma query.
    broken = {k: v for k, v in db.check_invariants().items() if v}
    assert not broken, f"invariante violado: {broken}"
    db.close()


@pytest.fixture
def guest(base: None) -> guests.Guest:
    return guests.create("Ana")


def make_track(n: int, duration_ms: int = 5_000) -> TrackData:
    tid = f"{n:022d}"
    t = TrackData(
        track_id=tid,
        uri=f"spotify:track:{tid}",
        name=f"Faixa {n}",
        artists="Artista",
        album="Álbum",
        art_url=None,
        duration_ms=duration_ms,
        explicit=False,
    )
    tracks.upsert(t)
    return t
