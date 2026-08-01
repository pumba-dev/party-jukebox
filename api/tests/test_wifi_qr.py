"""O QR que conecta o celular na rede da festa (bq/net.py).

Por que isto merece testes, sendo cinco linhas de string: um QR de Wi-Fi errado **escaneia
perfeitamente**. O celular mostra "conectar-se à rede", a pessoa toca, e falha — ou pior, entra
sem senha em outra rede. Não há mensagem de erro em lugar nenhum, e o único jeito de descobrir é
tentar com um celular de verdade. Um teste de mesa custa nada e cobre o que ninguém revisa
olhando.
"""

from __future__ import annotations

import re

import pytest

from bq.core import net
from bq.core.config import settings


@pytest.fixture
def rede(monkeypatch: pytest.MonkeyPatch):
    """Configura SSID/senha/auth como se viessem do .env."""

    def configurar(ssid: str, senha: str = "segredo", auth: str = "WPA", oculta: bool = False):
        monkeypatch.setattr(settings, "wifi_ssid", ssid)
        monkeypatch.setattr(settings, "wifi_password", senha)
        monkeypatch.setattr(settings, "wifi_auth", auth)
        monkeypatch.setattr(settings, "wifi_hidden", oculta)
        return net.wifi_payload()

    return configurar


# --- forma ------------------------------------------------------------------------------------


def test_formato_canonico(rede) -> None:
    """O esquema do ZXing, na forma que o Android gera em Wi-Fi → Compartilhar."""
    assert rede("Festa", "senha123") == "WIFI:T:WPA;S:Festa;P:senha123;;"


def test_termina_com_dois_ponto_e_virgula(rede) -> None:
    """Um fecha o último campo, o outro fecha o registro. Leitores que exigem o par existem."""
    assert rede("Festa").endswith(";;")


def test_sem_ssid_nao_ha_qr(rede) -> None:
    """Rede não configurada é o padrão, e não um erro: o /tv mostra só o QR da fila."""
    assert rede("") is None


def test_rede_aberta_nao_manda_senha(rede) -> None:
    """`P:` vazio numa rede aberta faz alguns leitores tentarem autenticar e falhar."""
    saida = rede("Festa", "", auth="nopass")
    assert saida == "WIFI:T:nopass;S:Festa;;"
    assert "P:" not in saida


def test_rede_oculta_marca_H(rede) -> None:
    assert rede("Festa", "senha123", oculta=True) == "WIFI:T:WPA;S:Festa;P:senha123;H:true;;"


def test_senha_comum_passa_intacta(rede) -> None:
    """Uma senha alfanumérica típica não deve ganhar escape nenhum.

    🔴 Este teste usa um valor FICTÍCIO de propósito. A senha real da rede vive no `api/.env`,
    que é gitignored — repeti-la aqui a colocaria no histórico do git para sempre, que é
    exatamente o que o .gitignore existe para evitar.
    """
    assert rede("MinhaRede_5G", "NomeSobrenome23") == "WIFI:T:WPA;S:MinhaRede_5G;P:NomeSobrenome23;;"


# --- escape: a parte que erra em silêncio ------------------------------------------------------


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        # 🔴 O caso que importa. Sem escape, `casa;123` termina o campo P no `;` e o roteador
        # recebe a senha `casa`. O QR escaneia, o celular tenta, e falha sem dizer por quê.
        ("casa;123", r"casa\;123"),
        ("a:b", r"a\:b"),
        ("a,b", r"a\,b"),
        ('a"b', r"a\"b"),
        ("a\\b", r"a\\b"),
        (";;", r"\;\;"),
    ],
)
def test_escapa_os_caracteres_de_estrutura(rede, bruto: str, esperado: str) -> None:
    assert rede("Festa", bruto) == f"WIFI:T:WPA;S:Festa;P:{esperado};;"


def test_escapa_no_ssid_tambem(rede) -> None:
    """O SSID é tão capaz de ter `;` quanto a senha, e ninguém pensa nisso."""
    assert rede("Casa;Festa", "x") == r"WIFI:T:WPA;S:Casa\;Festa;P:x;;"


def test_ponto_e_virgula_da_senha_nao_vira_separador(rede) -> None:
    """A propriedade de verdade por trás do escape: contar os separadores REAIS.

    Um QR com escape correto tem exatamente 3 `;` não-escapados — um depois de T, um depois de S
    e o par final conta como dois... então 4. O teste conta pela estrutura em vez de comparar a
    string, que é o que pegaria um escape novo feito errado.
    """
    saida = rede("Festa", "a;b;c")
    nao_escapados = re.findall(r"(?<!\\);", saida)
    assert len(nao_escapados) == 4, saida
    # e o valor volta inteiro ao desescapar
    campo = saida.split("P:", 1)[1].removesuffix(";;")
    assert campo.replace("\\;", ";") == "a;b;c"


def test_valor_todo_hexadecimal_vai_entre_aspas(rede) -> None:
    """Caso de borda raro e real: pelo esquema, um valor todo-hexa pode ser interpretado como
    bytes em hexa em vez de texto — a senha `5150` chegaria como dois bytes. As aspas resolvem."""
    assert rede("Festa", "5150") == 'WIFI:T:WPA;S:Festa;P:"5150";;'
    assert rede("Festa", "abcABC123") == 'WIFI:T:WPA;S:Festa;P:"abcABC123";;'
    # e não estraga o caso normal
    assert rede("Festa", "senhag123") == "WIFI:T:WPA;S:Festa;P:senhag123;;"


def test_ssid_todo_hexadecimal_tambem(rede) -> None:
    assert rede("beef", "x") == 'WIFI:T:WPA;S:"beef";P:x;;'


# --- o snapshot expõe os dois campos -----------------------------------------------------------


def test_snapshot_carrega_qr_e_ssid(base: None, rede) -> None:
    """O /tv lê os dois do snapshot: o QR para escanear e o nome para confirmar a rede certa."""
    from bq import snapshot

    # `;` no SSID para provar que só o QR ganha escape. Senha com `s`, que não é dígito hexa:
    # com `abc` a regra das aspas dispararia e o assert ficaria sobre duas coisas ao mesmo tempo.
    rede("Casa;5G", "senha")
    s = snapshot.build(None).model_dump(by_alias=True)
    assert s["wifiQr"] == r"WIFI:T:WPA;S:Casa\;5G;P:senha;;"
    assert s["wifiSsid"] == "Casa;5G", "o texto na tela é cru, sem o escape do esquema"


def test_snapshot_sem_rede_configurada(base: None, rede) -> None:
    from bq import snapshot

    rede("")
    s = snapshot.build(None).model_dump(by_alias=True)
    assert s["wifiQr"] is None and s["wifiSsid"] is None
