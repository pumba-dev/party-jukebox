"""O duplo do YouTube. Devolve vídeos de mesa e conta chamadas.

🔴 Como o do Spotify, é injetado com `cast(Any, fake)` — sem Protocol, sem ABC. Método novo em
`YouTubeClient` que alguma rota passe a chamar não existe aqui, e nem o mypy nem a suíte reclamam:
o erro aparece só em produção, como `AttributeError`, no meio da festa. **Atualize este arquivo na
mesma edição em que mexer no cliente.**

`tests/arquitetura/test_duplos.py` compara as duas superfícies e é o que torna esse aviso
executável em vez de decorativo.
"""

from __future__ import annotations

from typing import Any, cast

from bq import runtime
from bq.youtube.client import VideoData


class FakeYouTube:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.disabled = False
        self.units_used = 0
        self.backoff_ms = 0
        # por consulta normalizada; ausente = devolve dois vídeos genéricos
        self.resultados: dict[str, list[VideoData]] = {}
        self.erro: Exception | None = None

    def search_backoff_ms(self) -> int:
        return self.backoff_ms

    async def search(self, q: str, limit: int = 10) -> list[VideoData]:
        self.calls.append(q)
        self.units_used += 101
        if self.erro is not None:
            raise self.erro
        if q in self.resultados:
            return self.resultados[q][:limit]
        return [video(1), video(2)][:limit]


def video(n: int, *, duration_ms: int = 240_000, embeddable: bool = True) -> VideoData:
    return VideoData(
        video_id=f"vid{n:08d}",
        title=f"Karaokê {n}",
        channel="Canal do Karaokê",
        thumb_url=f"https://i.ytimg.com/vi/vid{n:08d}/mqdefault.jpg",
        duration_ms=duration_ms,
        embeddable=embeddable,
    )


def ligar(fake: FakeYouTube | None = None) -> FakeYouTube:
    """Põe o duplo no runtime e devolve ele. Sem isto, `runtime.youtube is None` e o karaokê
    aparece desligado — que é o default correto e o que a maioria dos testes quer."""
    f = fake or FakeYouTube()
    runtime.youtube = cast(Any, f)
    return f
