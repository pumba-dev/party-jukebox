"""Busca de faixas, com cache de catálogo.

🔴 O cache guarda **só os dados da faixa vindos do Spotify**. `queueable`/`blockedReason` NÃO
entram: eles dependem da fila e do histórico, que mudam a cada minuto. Cachear a resposta
inteira faria a segunda pessoa que buscasse "Evidências" ver a faixa como disponível 8 minutos
depois de ela já estar na fila — escolher, tocar no botão e levar `ALREADY_QUEUED`
(.docs/07-integracao-spotify.md §7).

RF-06 não é otimização, é proteção de cota: o rate limit do Spotify é **por app**, então 30
convidados digitando ao mesmo tempo disputam o mesmo orçamento, e o modo de falha é a busca
parar para todos simultaneamente. Numa festa a maioria das buscas repete — a sala inteira
procura os mesmos 40 artistas — e o cache torna essas gratuitas.
"""

from __future__ import annotations

from collections import OrderedDict

from ..core import clock
from .client import SpotifyClient, TrackData

LIMIT = 10
TTL_MS = 10 * 60 * 1000
MAX_ENTRIES = 200

_cache: OrderedDict[str, tuple[int, list[TrackData]]] = OrderedDict()
hits = 0
misses = 0


def normalize(q: str) -> str:
    return " ".join(q.strip().lower().split())


def clear() -> None:
    _cache.clear()


async def search(client: SpotifyClient, q: str) -> list[TrackData]:
    global hits, misses
    key = normalize(q)
    now = clock.mono_ms()

    cached = _cache.get(key)
    if cached is not None and now - cached[0] < TTL_MS:
        _cache.move_to_end(key)
        hits += 1
        return cached[1]

    found = await client.search_tracks(key, limit=LIMIT)
    misses += 1
    _cache[key] = (now, found)
    _cache.move_to_end(key)
    while len(_cache) > MAX_ENTRIES:
        _cache.popitem(last=False)
    return found
