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
| 7 | Monitor: `.\start.ps1 -Tv` abre a `/tv` em quiosque | tela cheia, sem cursor, sem barra. Se abrir à mão, **use a linha que o script imprime** — ver §1.1 |
| 8 | **Do seu celular**, pela Wi-Fi: abrir a URL, apelido, sugerir uma música | **o som sai na JBL.** Este é o único teste que importa |
| 9 | Deixar 2 ou 3 músicas na fila | a festa não começa com o `/tv` na tela de fila vazia |
| 10 | Colar o QR code impresso onde as pessoas passam | perto da comida e da bebida, não na porta |

**O passo 8 é o teste de aceitação da noite inteira.** Se ele passa, tudo o que está entre o celular e
a caixa funciona. Se falha, a §3 diz onde procurar.

**O passo 9 é psicologia, não técnica.** A primeira pessoa que olhar o monitor precisa ver uma festa em
andamento, com fila e capa de álbum. Uma tela dizendo "ninguém sugeriu nada" às 20h05 sinaliza que o
app não está funcionando, não que é a vez dela.

### 1.1 Karaokê — o que fazer na VÉSPERA, não no dia

Pule tudo isto se não vai ter karaokê. Se vai, **os dois primeiros passos precisam acontecer com
antecedência**: o primeiro exige um login, e o segundo pode exigir esperar até o dia seguinte. O
passo a passo com telas do Google Cloud e o porquê de cada escolha está em
[12 §1](12-integracao-youtube.md) e [12 §10](12-integracao-youtube.md).

| # | Fazer | Por quê |
|---|---|---|
| 1 | `.\start.ps1 -Tv` uma vez em casa. Na primeira execução o Chrome abre **sem** quiosque: **entre na conta com YouTube Premium** e feche | O perfil dedicado (`.chrome-tv\`) guarda a sessão. Sem Premium, o anúncio de pré-roll toca na frente de quem ia cantar, e não há como pular |
| 2 | `YOUTUBE_API_KEY` no `api\.env` | Sem ela a aba de karaokê nem aparece para os convidados. A chave é do Google Cloud e a criação pode levar horas |
| 3 | `/host` → Regras → **Karaokê na fila** = "a cada 3 músicas" | Nasce **desligada**. Muito baixo e a festa vira open mic: quem só quer ouvir música nunca é atendido |
| 4 | Do celular: aba **🎤 Cantar**, escolher, esperar ser chamado, **INICIAR** | O teste de aceitação do karaokê. Se o som sair na caixa, está tudo de pé |

🔴 **O `--user-data-dir` é a parte que ninguém acredita ser necessária.** Se o Chrome já estiver
aberto no perfil normal, `chrome <url>` entrega o endereço ao processo existente e **descarta todos
os flags** — inclusive o do autoplay. Não há erro; o som simplesmente não sai. O `start.ps1 -Tv`
usa perfil próprio justamente por isso, e imprime a linha de comando completa **sempre**, com ou
sem o switch, para o caminho manual ser um copiar-e-colar.

**Durante a festa, no `/host` → Saúde**, o bloco Karaokê responde a pergunta que a tela preta não
responde:

| `a /tv está aberta` | `reportando o vídeo` | Significa |
|---|---|---|
| não | — | o quiosque caiu ou ninguém abriu a `/tv` |
| sim | não (com alguém cantando) | o **autoplay foi bloqueado** → aperte a **barra de espaço** na máquina da TV |
| sim | sim | está tocando; o problema é outro |

A cota do YouTube também aparece ali: são ~99 buscas por dia para a festa inteira. Estourou, a
busca de karaokê morre até a virada do dia no Pacífico — e só ela; a fila normal não é afetada.

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

### 3.10 🎤 O nome está no telão e não sai som

Três causas, e o `/host` → **Saúde** → Karaokê separa as três em dois segundos. Comece por lá.

**A `/tv` está aberta e o vídeo não anda** → o Chrome bloqueou o autoplay.
→ **Aperte a barra de espaço na máquina da TV.** A própria tela diz isso, em letra grande.
→ Se resolver toda vez, o quiosque subiu sem os flags: `Ctrl+C` e `.\start.ps1 -Tv` (§1.1).
→ Se ninguém apertar, a festa **não para**: a vez encerra sozinha e a próxima música entra.

**Nenhuma `/tv` está aberta** → o quiosque caiu, ou alguém fechou.
→ Reabra com `.\start.ps1 -Tv`, ou a linha que o console imprimiu.
→ A vez atual provavelmente já vai ter voltado para a fila; ela é chamada de novo mais tarde.

**A pessoa sumiu** (foi ao banheiro, o celular descarregou, desistiu).
→ `/host` → Fila → **Começar por ela** se ela está de pé na sua frente com o microfone.
→ **Passar a vez** se não. Não conta falta e o karaokê volta para o fim da fila.
→ Sem tocar em nada, o prazo vence sozinho e a fila anda.

**O vídeo aparece e diz que não pode tocar** → o dono desligou a incorporação depois, ou é bloqueio
regional. Acontece em canal de karaokê apesar do filtro na busca. A `/tv` explica na tela, o
servidor encerra a vez e a fila continua. Peça outro vídeo para a pessoa.

🔴 **Duas `/tv` abertas: a segunda é MUDA de propósito** ([RF-51](01-requisitos-funcionais.md)).
Se a tela grande ficou sem som logo depois de alguém abrir a `/tv` no celular, feche a do celular —
em 25 s a posse volta sozinha, ou na hora se a aba for fechada de verdade.

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
/tv ....................... http://127.0.0.1/tv        .\start.ps1 -Tv

TUDO PAROU ................ /host → re-resolver device
SÓ ISSO NÃO RESOLVEU ...... Ctrl+C  →  .\start.ps1     (não perde a fila)
PULANDO DEMAIS ............ /host → slider de votos → 6 ou 7
SILÊNCIO E FILA VAZIA ..... /host → Tocar agora
PROTEGER A DO BOLO ........ /host → Tocar agora  (90 s imune a voto)
NINGUÉM CONECTA ........... VPN desligada? IP mudou? rede certa no celular?

KARAOKÊ MUDO .............. barra de espaço NA MÁQUINA DA TV
NOME NO TELÃO E NINGUÉM ... /host → Fila → "Começar por ela" ou "Passar a vez"
SÓ KARAOKÊ, SEM FILA ...... é de propósito: a TV explica. /host → Regras desliga
2ª /tv NÃO FAZ SOM ........ é de propósito: só uma tela toca (RF-51)

REGRA DE OURO ............. está tocando? não mexa.
```
