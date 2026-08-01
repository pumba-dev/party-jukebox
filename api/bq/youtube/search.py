"""Busca de karaokê, com cache. Cópia estrutural de `spotify/search.py`, com um TTL diferente.

🔴 O TTL é de **6 horas**, e não os 10 minutos do Spotify. Lá o cache protege o rate limit, que
se recupera sozinho em segundos; aqui ele protege uma cota DIÁRIA de 10.000 unidades que zera à
meia-noite do Pacífico e da qual cada busca nova consome 101. São ~99 buscas por dia para a festa
inteira: o cache não é otimização, é o que torna a feature viável.

E o acervo de karaokê não muda durante uma festa — "Evidências karaokê" devolve os mesmos vídeos
às 21h e às 3h. Um TTL curto aqui só gastaria cota para receber a mesma lista.

Como no do Spotify, o cache guarda **só os dados do vídeo**. `queueable`/`blockedReason` são
recalculados a cada resposta: dependem da fila, que muda a cada minuto.
"""

from __future__ import annotations

from collections import OrderedDict

from ..core import clock
from .client import VideoData, YouTubeClient

LIMIT = 10
TTL_MS = 6 * 60 * 60 * 1000
MAX_ENTRIES = 300

_cache: OrderedDict[str, tuple[int, list[VideoData]]] = OrderedDict()
hits = 0
misses = 0


def normalize(q: str) -> str:
    return " ".join(q.strip().lower().split())


def clear() -> None:
    """🔴 Global de módulo: NÃO reseta entre testes. O conftest chama isto no `base`, senão a
    busca de um teste devolve o resultado semeado por outro."""
    global hits, misses
    _cache.clear()
    hits = 0
    misses = 0


async def search(client: YouTubeClient, q: str) -> list[VideoData]:
    global hits, misses
    key = normalize(q)
    now = clock.mono_ms()

    cached = _cache.get(key)
    if cached is not None and now - cached[0] < TTL_MS:
        _cache.move_to_end(key)
        hits += 1
        return cached[1]

    found = await client.search(key, limit=LIMIT)
    misses += 1
    _cache[key] = (now, found)
    _cache.move_to_end(key)
    while len(_cache) > MAX_ENTRIES:
        _cache.popitem(last=False)
    return found
