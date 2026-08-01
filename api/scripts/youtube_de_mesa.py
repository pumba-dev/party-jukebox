"""O YouTube de mesa do SERVIDOR de teste (o outro duplo é `tests/apoio/youtube.py`).

Por que dois duplos, pelo mesmo motivo do par do Spotify: o de `tests/apoio/` existe para o
pytest, tem gancho de erro (`erro`) e conta unidades para os testes de cota. Nenhuma das duas
coisas serve a um servidor vivo, e falta nele um catálogo com nomes que um teste de browser possa
digitar e reconhecer.

🔴 Este arquivo é a TERCEIRA cópia manual da superfície do `YouTubeClient`, e o `cast` implícito
que a torna invisível é a substituição de classe em `servidor_de_mesa.py`. Método novo no cliente
real chamado por uma rota estoura `AttributeError` aqui — e ao contrário do Spotify, que morre
dentro do `run_forever`, este morre numa resposta HTTP e vira 500 na cara do teste. Menos cruel,
igualmente invisível para o mypy. **Atualize na mesma edição em que mexer no cliente.**

Não é importado por `bq/` em momento nenhum: quem o injeta é o script irmão, e o `mypy` do
projeto olha só `files = ["bq"]`.

🔴 O `videoId` tem 11 caracteres, como um de verdade. O formato não é validado por nada no bq, mas
um id curto demais tornaria o teste cego a um `LIKE` mal escrito ou a um `split(":")` que corte no
lugar errado — e o id interno é `yt:<videoId>`, então o `:` é exatamente o que se está exercitando.
"""

from __future__ import annotations

import asyncio

import httpx

from bq.youtube.client import VideoData

# Latência de uma ida ao Google. Menor que a do Spotify de mesa de propósito: aqui não há máquina
# de estados esperando confirmação, só um convidado olhando um spinner.
LATENCIA_S = 0.08

_ACERVO = [
    ("Evidências", "Karaokê Brasil", 289_000),
    ("Garota de Ipanema", "Playback Bossa", 245_000),
    ("Ai Se Eu Te Pego", "Karaokê Brasil", 168_000),
    ("Wonderwall", "Sing Along Hits", 260_000),
    ("Bohemian Rhapsody", "Sing Along Hits", 355_000),
    ("Anunciação", "Karaokê Brasil", 262_000),
    ("Sozinho", "Playback Bossa", 231_000),
    # 🔴 Um vídeo LONGO no acervo, de propósito: é o que exercita o `TOO_LONG` esmaecido na tela
    # do convidado sem depender de o host mexer em `maxDurationMs`.
    ("Especial Sertanejo (1 hora)", "Karaokê Brasil", 3_600_000),
]


def _video(i: int, titulo: str, canal: str, dur: int) -> VideoData:
    vid = f"mesa{i:07d}"  # 11 caracteres, como um videoId de verdade
    return VideoData(
        video_id=vid,
        title=f"{titulo} — Karaokê com letra",
        channel=canal,
        thumb_url=None,  # sem rede no teste: uma URL de imagem real seria um request pendurado
        duration_ms=dur,
        embeddable=True,
    )


CATALOGO = [_video(i, t, c, d) for i, (t, c, d) in enumerate(_ACERVO)]


class YouTubeDeMesa:
    """A superfície que `routes/karaoke.py` consome. Nada mais."""

    MAX_ATTEMPTS = 3

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        # A assinatura é a do cliente real: quem constrói é o lifespan de `bq/app.py`, e ele passa
        # os dois posicionalmente.
        self._http = http
        self._key = api_key
        self._used = 0

    @property
    def units_used(self) -> int:
        return self._used

    @property
    def disabled(self) -> bool:
        return False

    def search_backoff_ms(self) -> int:
        return 0

    async def search(self, q: str, limit: int = 10) -> list[VideoData]:
        await asyncio.sleep(LATENCIA_S)
        self._used += 101  # 100 do /search + 1 do /videos, como o cliente real contabiliza
        alvo = q.strip().lower()
        achados = [v for v in CATALOGO if alvo in v.title.lower() or alvo in v.channel.lower()]
        return (achados or CATALOGO)[:limit]
