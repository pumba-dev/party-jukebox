"""Rotas HTTP. Nada importa este pacote (.docs/03-arquitetura.md §6).

As rotas nunca decidem o que toca — só pedem (`conductor.wake()` ou `await conductor.…`) e o
maestro resolve dentro do lock. É isso que permite não ter transação distribuída nenhuma.
"""
