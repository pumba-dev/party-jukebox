"""Infraestrutura: relógio, configuração, banco, log, rede e o envelope de erro.

REGRA DA CAMADA (R2): `core/` não importa nada de `bq` além de `core/`. É a base da ordem de
dependência, e nada aqui sabe o que é uma festa — não há fila, nem convidado, nem faixa.

Os dois `.sql` moram aqui porque `db.py` os lê pelo próprio caminho: separá-los faz `seeds.sql`
falhar em todo boot e `schema.sql` falhar só em banco novo, que é o caminho que os testes com
`tmp_path` exercitam e o boot com banco existente não.
"""
