"""As fixtures da suíte, num lugar só.

Conftest de RAIZ, e é o que importa: fixture daqui é herdada por toda subpasta. Antes a `client`
morava dentro de `test_api.py` e dois arquivos a importavam com `# noqa: F401` — o que, além de
feio, obrigava a lembrar em qual teste cada helper nasceu.

Divisão de trabalho com `tests/apoio/`: aqui as FIXTURES (injeção pelo pytest), lá as FUNÇÕES
(import por nome). `simulate(cond, clk, ms)` recebe o relógio explicitamente e não teria nada a
ganhar sendo mágica.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
# 🔴 Vazia, e por atribuição como as de cima. Quem tem a chave no `.env` de verdade faz o lifespan
# da fixture `client` construir um `YouTubeClient`, e aí `karaokeEnabled` nasce `True` — o teste
# que afirma "o host ligou mas não há chave" falha na máquina de quem configurou o karaokê e passa
# na de quem não configurou. Quem quer o cliente ligado num teste chama `apoio.youtube.ligar()`,
# que injeta o duplo. Mesma armadilha do `WIFI_PASSWORD` em `tests/core/test_net_wifi_qr.py`.
os.environ["YOUTUBE_API_KEY"] = ""
# o log do teste não vai para api\party.log, senão o histórico da festa nasce sujo
os.environ["LOG_PATH"] = str(Path(tempfile.gettempdir()) / "bq-test.log")

# 🔴 A ordem acima é frágil e a subdivisão da suíte em pacotes criou uma forma nova de quebrá-la:
# um `__init__.py` de subpasta é importado ANTES deste arquivo. Se ele importasse `bq`, o
# `config.py` validaria contra o ambiente REAL, e a falha apareceria como `SystemExit(2)` de
# configuração inválida — ou, pior, como teste do /host quebrando com BAD_PIN, sem relação
# aparente com a causa. Há um teste em arquitetura/ para a regra; esta asserção é o diagnóstico.
assert "bq.core.config" not in sys.modules, (
    "bq foi importado ANTES deste conftest: as variáveis de ambiente de teste não valeram. "
    "Causa provável: algum __init__.py de tests/ importa bq."
)

from bq import runtime  # noqa: E402
from bq.core import db  # noqa: E402
from bq.core.config import settings  # noqa: E402
from bq.domain import guests  # noqa: E402
from bq.domain.party import S, party  # noqa: E402
from bq.spotify import search as spotify_search  # noqa: E402
from bq.youtube import search as youtube_search  # noqa: E402

from .apoio.relogio import FakeClock  # noqa: E402


@pytest.fixture
def clk(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """🔴 Patch no MÓDULO `bq.core.clock`, e é por isso que nenhum módulo pode fazer
    `from .clock import mono_ms`: com o nome importado direto, o patch não alcança o chamador
    e o teste passa medindo o relógio de verdade (10 §2.1 / RNF-07).

    Estas duas strings são caminho-em-string: mover `clock.py` sem atualizá-las falha alto
    (`AttributeError`), mas deixar um shim de re-export para trás falha em SILÊNCIO. Ver
    `tests/arquitetura/test_relogio.py`, que existe para esse caso.
    """
    c = FakeClock()
    monkeypatch.setattr("bq.core.clock.mono_ms", lambda: c.mono)
    monkeypatch.setattr("bq.core.clock.wall_ms", lambda: c.wall)
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
    # A posse da /tv é monotônica e global de módulo: sem zerar, um `tvId` de outro teste continua
    # dono e o `claim` deste responde `owner=false` sem nenhuma /tv por perto.
    party.tv_owner = ""
    party.tv_beat_at_mono = 0
    # Os singletons de bq.runtime são globais de módulo: sem zerar, um maestro de outro teste
    # sobrevive e `votes.cast` vota na faixa errada — que foi exatamente o que aconteceu.
    runtime.conductor = None
    runtime.hub = None
    runtime.spotify = None
    runtime.device = None
    runtime.youtube = None
    # 🔴 O cache de busca é global de MÓDULO e não reseta sozinho: sem isto, a busca de um teste
    # devolve o que outro semeou, e o `misses` que alguns testes contam vem do teste anterior.
    youtube_search.clear()
    spotify_search.clear()
    yield
    # Invariantes no teardown de TODO teste: uma linha presa em `playing` para sempre para a
    # fila em silêncio, e o custo de descobrir isso aqui em vez de na festa é uma query.
    broken = {k: v for k, v in db.check_invariants().items() if v}
    assert not broken, f"invariante violado: {broken}"
    db.close()


@pytest.fixture
def guest(base: None) -> guests.Guest:
    return guests.create("Ana")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """O app de verdade, pela porta HTTP. Não usa a fixture `base`: o lifespan do FastAPI abre e
    fecha o banco por conta própria."""

    async def sem_maestro(self: object) -> None:  # o maestro tem teste próprio
        return None

    monkeypatch.setattr("bq.playback.conductor.Conductor.run_forever", sem_maestro)
    monkeypatch.setattr(settings, "db_path", tmp_path / "api.db")
    monkeypatch.setattr(settings, "tokens_path", tmp_path / ".tokens.json")
    # 🔴 Os caches de busca são globais de MÓDULO e sobrevivem entre testes, e esta fixture não usa
    # `base`. Sem limpar aqui, a busca de um teste devolve o que outro semeou — e o sintoma é
    # cruel: o teste passa sozinho e falha na suíte, ou o contrário. Já custou um diagnóstico.
    spotify_search.clear()
    youtube_search.clear()
    # Mesmo motivo, mesma classe de bug: a posse da /tv é global de módulo e esta fixture não passa
    # por `base`. Um `tvId` fixo repetido entre testes herdaria a posse do anterior.
    party.tv_owner = ""
    party.tv_beat_at_mono = 0
    db.close()
    # 🔴 Import TARDIO, de propósito: `bq.app` monta o app no import, e o `settings.db_path`
    # acima precisa já estar patcheado quando isso acontece. Subir esta linha para o topo do
    # arquivo faz a suíte escrever no `api/party.db` de verdade.
    from bq.app import app

    with TestClient(app) as c:
        yield c
        # dentro do `with`: ao sair, o lifespan fecha a conexão do banco
        broken = {k: v for k, v in db.check_invariants().items() if v}
        assert not broken, f"invariante violado: {broken}"
