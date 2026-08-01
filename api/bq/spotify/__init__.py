"""Camada Spotify: fala HTTP e devolve dataclasses.

🔴 Regra de dependência (.docs/03-arquitetura.md §6): **este pacote não conhece o banco.**
É o que permite substituir o Spotify inteiro por um duplo em teste (M1.15) — a decisão de
teste mais valiosa do projeto.
"""
