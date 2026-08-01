# 11 — Runbook da festa

Escrito para ser usado **de pé, com a casa cheia, sem ler nada além da linha que interessa.**

Imprima a §3 ou deixe aberta numa aba. Ela é a única parte que você vai querer no meio da noite.

---

## 1. T-60 min — antes de qualquer convidado

Em ordem. Cada passo tem uma verificação, porque um passo que "parece ter dado certo" é o que quebra
às 22h.

| # | Fazer | Verificar |
|---|---|---|
| 1 | Notebook na tomada, tela ligada | não vai dormir ([configuração de energia já ajustada](historico/DESIGN-v0.md)) |
| 2 | **Wi-Fi conectado**, VPN **desligada** | `ipconfig` mostra `192.168.0.x`. VPN ligada muda o IP e o QR aponta para o vazio |
| 3 | Conectar a **JBL PartyBox 100** por Bluetooth | tocar qualquer coisa e **ouvir na caixa**. Se não sair na caixa, resolva aqui — [não é problema do `bq`](00-visao-e-escopo.md#3-escopo) |
| 4 | Abrir o **app desktop do Spotify** e logar na conta Premium | tocar 10 s de qualquer música manualmente e pausar. Isso ativa o device e evita a escalada do primeiro despacho ([07 §3](07-integracao-spotify.md)) |
| 5 | `.\start.ps1` | a URL e o IP aparecem grandes no console |
| 6 | Abrir `/host` no notebook e digitar o PIN | painel de saúde: **device resolvido**, maestro vivo, sem erros ([05 §5](05-api-http.md)) |
| 7 | Monitor: Chromium em `--kiosk http://127.0.0.1/tv` | tela cheia, sem cursor, sem barra |
| 8 | **Do seu celular**, pela Wi-Fi: abrir a URL, apelido, sugerir uma música | **o som sai na JBL.** Este é o único teste que importa |
| 9 | Deixar 2 ou 3 músicas na fila | a festa não começa com o `/tv` na tela de fila vazia |
| 10 | Colar o QR code impresso onde as pessoas passam | perto da comida e da bebida, não na porta |

**O passo 8 é o teste de aceitação da noite inteira.** Se ele passa, tudo o que está entre o celular e
a caixa funciona. Se falha, a §3 diz onde procurar.

**O passo 9 é psicologia, não técnica.** A primeira pessoa que olhar o monitor precisa ver uma festa em
andamento, com fila e capa de álbum. Uma tela dizendo "ninguém sugeriu nada" às 20h05 sinaliza que o
app não está funcionando, não que é a vez dela.

### QR code

O `start.ps1` imprime a URL. Gere o QR em qualquer gerador e imprima **grande** — pelo menos 10 cm — com
a URL escrita embaixo em texto, para quem tiver câmera ruim. Duas ou três cópias.

---

## 2. Durante a festa — o que olhar

Praticamente nada. O ponto do projeto é
[S5](00-visao-e-escopo.md#5-critérios-de-sucesso): você participa da festa.

Se der uma olhada no `/host` de vez em quando, são três coisas:

| Onde | Verde | Vermelho significa |
|---|---|---|
| `device` | nome resolvido | app do Spotify fechou → §3.1 |
| `maestro` | `alive: true, passive: false` | §3.2 ou §3.5 |
| `guestsOnline` | número parecido com o de gente | os celulares não estão conectando → §3.4 |

**A regra de ouro: se está tocando música, não mexa em nada.**

---

## 3. Quando quebrar — sintoma → ação

### 3.1 🔇 Parou de tocar e a fila tem músicas

A falha mais provável da noite. Em ordem de probabilidade:

1. **O app do Spotify fechou, deslogou, ou o `device_id` mudou.**
   → `/host` → **re-resolver device**. Resolve na maioria dos casos ([07 §3](07-integracao-spotify.md)).
   → Se o painel diz que não achou: abra o app do Spotify, confirme que está logado, re-resolva.

2. **A internet caiu.**
   → Confirme abrindo qualquer site. Sem internet **não há busca nem despacho** — é
   [risco aceito](02-requisitos-nao-funcionais.md#8-riscos-aceitos-explicitamente), sem contorno no
   `bq`.
   → Enquanto não voltar: toque uma playlist direto no app do Spotify, na mão. Quando voltar, `/host` →
   pular, e o `bq` retoma a fila.

3. **O maestro morreu** (`alive: false` ou `restarts` subindo).
   → Ele reinicia sozinho ([RNF-11](02-requisitos-nao-funcionais.md)). Se não voltar em 30 s: reinicie
   o servidor (§3.6). A fila sobrevive.

4. **Está pausado.** → `/host` → Retomar. Sim, acontece.

### 3.2 🎵 A música mudou sozinha para algo que ninguém sugeriu

Alguém mexeu no Spotify direto — pelo celular, ou no app. O `bq` tenta retomar 3 vezes e depois **entra
em modo passivo** para não brigar ([RF-19](01-requisitos-funcionais.md)).

→ Peça para a pessoa parar de mexer no Spotify. É isso mesmo.
→ `/host` → pular. Isso tira do modo passivo e devolve o controle à fila.

### 3.3 📵 Ninguém consegue abrir a página

1. **VPN ligada** → desligue. Ela troca o IP e o QR passa a apontar para o nada.
2. **IP mudou** (DHCP renovou) → `ipconfig`, e escreva o IP novo à mão em cima do QR impresso.
3. **Celular na rede errada** → confirme que está em `EDILAN_5G`, não numa rede de vizinho.
4. **Firewall do Windows** → a rede é categoria **Public** nesta máquina; se aparecer prompt do
   firewall na primeira conexão, **permita**. Se você negou antes, apague a regra em
   `wf.msc` e reinicie o servidor.

### 3.4 🔌 A página abre mas a fila não atualiza

WebSocket não conectou; o app fica mostrando o estado do carregamento.
→ Pedir para a pessoa **recarregar**. Resolve quase sempre.
→ Se acontece com todos ao mesmo tempo, é o servidor: §3.6.

### 3.5 ⏭️ As músicas estão sendo puladas rápido demais

1. **O limiar está baixo demais para o tamanho da festa.** → `/host` → slider de votos → suba para 6 ou
   7. É exatamente para isso que [RF-24](01-requisitos-funcionais.md) existe.
2. **Uma música ruim de verdade** → a votação está funcionando. Deixe.
3. **Uma música que você quer proteger** (bolo, dedicatória) → `/host` → **Tocar agora**. Ela ganha 90 s
   de imunidade a voto ([RF-26](01-requisitos-funcionais.md)).

### 3.6 🔄 Reiniciar o servidor

Seguro. `Ctrl+C` no console, `.\start.ps1` de novo. Fila, histórico, apelidos e limiares sobrevivem
([RF-39](01-requisitos-funcionais.md)); a música em curso pode reiniciar do zero.

Os convidados **não precisam fazer nada** — os celulares reconectam sozinhos em até 5 s
([RNF-13](02-requisitos-nao-funcionais.md)).

### 3.7 🔊 O volume salta entre uma música e outra

Não tem correção em software: o Spotify **não** normaliza loudness em device de terceiros
([RNF, riscos](02-requisitos-nao-funcionais.md#8-riscos-aceitos-explicitamente)).
→ Ajuste o volume na JBL, na mão. Escolha um volume base que aguente o salto.

### 3.8 😬 Alguém está enfileirando coisa ruim de propósito

Não é problema técnico — é festa. Ferramentas, em ordem de sutileza:

1. `/host` → remover a sugestão da fila. Ninguém percebe.
2. `/host` → pular.
3. `/host` → **Tocar agora** com algo bom. Furar a fila resolve o clima na hora.
4. Falar com a pessoa.

### 3.9 🕳️ A fila esvaziou e está tudo em silêncio

Comportamento **projetado**, não defeito ([ADR-005](adr/ADR-005-fila-vazia-silencio.md)): o `/tv` está
em tela cheia chamando as pessoas para sugerir.

→ Se a pressão social funcionar em 30 s, deixe. É o efeito pretendido.
→ Se não: `/host` → **Tocar agora**. Um toque.

**Se isso acontecer três vezes na noite**, a decisão de silêncio estava errada para esta festa. Anote
para a próxima — uma playlist de fallback é ~1 h de trabalho.

---

## 4. Depois da festa

| # | Fazer |
|---|---|
| 1 | **Copie `api/party.db`** antes de qualquer coisa. É o histórico completo da noite: toda faixa, quem sugeriu, quanto tocou, quem votou ([RF-41](01-requisitos-funcionais.md)) |
| 2 | `Ctrl+C` no servidor |
| 3 | Guarde `party.log` junto com o `.db` |

Com os dois arquivos dá para responder: qual música gerou mais voto de skip, quem sugeriu mais, o que
tocou na hora do bolo, qual foi a última música da noite.

**Copie antes de mexer em qualquer coisa.** O banco não tem migração e o
[fluxo de desenvolvimento manda apagar o arquivo quando o schema muda](00-visao-e-escopo.md#3-escopo) —
é bem fácil apagar por reflexo, dois dias depois, o único registro que existe da festa.

---

## 5. Cartão de bolso

```
URL da festa .............. http://192.168.0.10        (confirmar com ipconfig)
/host ..................... http://127.0.0.1/host      PIN: ____
/tv ....................... http://127.0.0.1/tv        Chromium --kiosk

TUDO PAROU ................ /host → re-resolver device
SÓ ISSO NÃO RESOLVEU ...... Ctrl+C  →  .\start.ps1     (não perde a fila)
PULANDO DEMAIS ............ /host → slider de votos → 6 ou 7
SILÊNCIO E FILA VAZIA ..... /host → Tocar agora
PROTEGER A DO BOLO ........ /host → Tocar agora  (90 s imune a voto)
NINGUÉM CONECTA ........... VPN desligada? IP mudou? rede certa no celular?

REGRA DE OURO ............. está tocando? não mexa.
```
