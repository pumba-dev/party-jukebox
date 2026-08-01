# Espelha `bq/routes/`, e é também onde vivem os testes ponta a ponta pelo `TestClient`.
#
# Esses quatro não pertencem a módulo nenhum — eles entram pela porta HTTP e atravessam todas as
# camadas de uma vez. Forçá-los num espelho exato seria uma mentira; aqui é coerente, porque a
# porta por onde entram É `routes/`.
