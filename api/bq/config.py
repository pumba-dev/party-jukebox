"""Configuração do `.env`, tipada e validada no boot.

Falha de validação **aborta o boot** com mensagem legível: descobrir que o PIN não estava setado
às 21h, com convidados chegando, é pior que não subir às 18h (.docs/03-arquitetura.md §7).

Limiares de jogo (5 votos, cooldown, duração máxima, janela de repetição) NÃO ficam aqui —
vivem na tabela `setting`, porque RF-24 exige ajuste ao vivo sem restart. Ver bq/party.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=API_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    spotify_client_id: str = Field(min_length=8)
    spotify_client_secret: str = Field(min_length=8)
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    spotify_device_name: str = "PUMBABOOK"

    host_pin: str = Field(pattern=r"^\d{4}$")

    bind_host: str = "0.0.0.0"
    bind_port: int = Field(default=80, ge=1, le=65535)
    # Preenchido pelo start.ps1 a partir dos adaptadores reais. Vazio = heurística de rota,
    # que erra com VPN ligada. Ver bq/net.py.
    lan_ip: str = ""

    db_path: Path = API_DIR / "party.db"
    tokens_path: Path = API_DIR / ".tokens.json"
    log_path: Path = API_DIR / "party.log"
    web_dist: Path = API_DIR.parent / "web" / "dist"

    @field_validator("spotify_redirect_uri")
    @classmethod
    def _no_localhost(cls, v: str) -> str:
        # 🔴 O Spotify PROÍBE `localhost` como redirect URI — só IP literal de loopback.
        # O erro que ele devolve é `INVALID_CLIENT: Invalid redirect URI`, que não menciona
        # localhost e manda conferir o client_id. A maioria dos tutoriais ensina errado.
        # Ver .docs/07-integracao-spotify.md §2.
        if "localhost" in v:
            raise ValueError(
                "o Spotify não aceita 'localhost' como redirect URI. "
                "Use o IP literal: http://127.0.0.1:8888/callback"
            )
        if not v.startswith(("http://127.0.0.1", "http://[::1]", "https://")):
            raise ValueError(
                "redirect URI precisa ser loopback por IP (http://127.0.0.1:…) ou https://"
            )
        return v

    @property
    def redirect_port(self) -> int:
        tail = self.spotify_redirect_uri.split("//", 1)[-1]
        host_port = tail.split("/", 1)[0]
        if ":" in host_port.rsplit("]", 1)[-1]:
            return int(host_port.rsplit(":", 1)[1])
        return 443 if self.spotify_redirect_uri.startswith("https") else 80


def load() -> Settings:
    """Carrega ou morre. Nunca devolve configuração meio válida."""
    try:
        return Settings()  # type: ignore[call-arg]  # vem do .env
    except ValidationError as e:
        env = API_DIR / ".env"
        print("\n  bq não subiu: configuração inválida.\n", file=sys.stderr)
        for err in e.errors():
            key = ".".join(str(p) for p in err["loc"]) or "?"
            print(f"    {key.upper():<24} {err['msg']}", file=sys.stderr)
        if not env.exists():
            print(
                f"\n  O arquivo {env} não existe."
                f"\n  Copie o modelo:  copy api\\.env.example api\\.env\n",
                file=sys.stderr,
            )
        else:
            print(f"\n  Corrija {env} e suba de novo.\n", file=sys.stderr)
        raise SystemExit(2) from None


settings = load()
