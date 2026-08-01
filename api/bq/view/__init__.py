"""O que as TELAS recebem: o snapshot, o histórico e o socket que os empurra.

REGRA DA CAMADA (R5): `view/` importa `core/`, `spotify/`, `domain/`, `models.py` e `runtime.py`
— nunca `playback/` nem `routes/`.

Separado de `playback/` de propósito, e o critério é a assimetria de consumidores: o maestro é um
MOTOR singleton, importado só por `app.py` (todo o resto o alcança por `runtime.require_conductor`),
enquanto `snapshot` e `ws` são SERVIÇO COMPARTILHADO, com cinco importadores. Juntá-los daria uma
pasta de 1 270 linhas com dois propósitos, que é o defeito que esta reorganização existe para
corrigir. Assim cada pasta cabe numa frase: `view/` é o que a tela recebe, `playback/` é o que a
caixa de som recebe.

`history.py` mora aqui e não em `domain/` porque `history.build(*, with_voters)` é apresentação
por audiência — a decisão de RF-25 ("nomes de votantes só para o host") é tomada ali dentro. É
também o que compra a regra R4: era o único módulo de regra que importava `models.py`.
"""
