# PolyChange - Aprendizados e Documentação

## Carteira Polymarket

### Arquitetura de Wallets
A Polymarket usa **Magic Link** para criar wallets. Isso gera um sistema de 3 endereços:

| Endereço | Papel | Tem fundos? |
|---|---|---|
| `0x84f90113dF277aCba303378641DA6a306C7446E1` | **Signer** - derivado da private key, assina ordens | Não (só assina) |
| `0x35C643D4e103d16d3338c5a0A400b27Cf55045cB` | **Funder/Proxy** - onde o USDC fica, é a conta do site | Sim (cash + posições) |
| `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | **Conditional Token** - endereço do contrato CTF | Não |

### Private Key
- A private key `0x2a0b8f99...` gera o signer `0x84f9...`
- Foi exportada do Polymarket (Settings → Export Private Key)
- A mesma private key é usada em todos os bots

### Como Conectar via API (py_clob_client)
```python
from py_clob_client.client import ClobClient

# OBRIGATÓRIO: signature_type=1 (Magic wallet) + funder
client = ClobClient(
    host='https://clob.polymarket.com',
    chain_id=137,
    key=os.getenv('POLY_PRIVATE_KEY'),
    signature_type=1,        # <-- Magic wallet
    funder=os.getenv('POLY_FUNDER'),  # <-- 0x35C6...
)

# Derivar API creds (NÃO usar creds manuais)
creds = client.create_or_derive_api_creds()

# Recriar com creds
client = ClobClient(
    host='https://clob.polymarket.com',
    chain_id=137,
    key=os.getenv('POLY_PRIVATE_KEY'),
    signature_type=1,
    funder=os.getenv('POLY_FUNDER'),
    creds=creds,
)
```

**ERROS COMUNS:**
- `not enough balance / allowance` → Faltou o `funder` ou `signature_type=1`
- `A private key is needed` → `.env` não carregou ou Python version incompatível
- `Unauthorized/Invalid api key` → Não usar API keys manuais, usar `create_or_derive_api_creds()`

### Consultar Saldo
O saldo NÃO fica como USDC on-chain. Fica dentro do sistema da Polymarket.

```python
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

params = BalanceAllowanceParams(
    asset_type=AssetType.COLLATERAL,
    signature_type=1,
)
bal = client.get_balance_allowance(params)
cash_usd = int(bal['balance']) / 1e6  # 6 decimais
```

### Colocar Ordens
```python
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

order_args = OrderArgs(
    token_id=token_id,   # YES ou NO token do mercado
    price=0.48,          # 48 centavos (0 a 1)
    size=5,              # contratos (mínimo 5)
    side=BUY,
)
signed = client.create_order(order_args)
result = client.post_order(signed, OrderType.GTC)
```

**Mínimo: 5 contratos por ordem.**

### Fees
- **Maker (limit order):** 0% — coloca oferta no book
- **Taker (market order):** ~2c por contrato

### Posições
- YES e NO são **tokens separados** — não se anulam
- Comprar YES + comprar NO do mesmo mercado = 2 posições abertas, dinheiro travado nos dois
- Para fechar posição: SELL o token que comprou
- O CLOB API não tem endpoint de positions — calcular pelos trades
- Posições ganhas precisam ser **reivindicadas manualmente** no site (sem API pra redeem)

---

## APIs da Polymarket

### 3 APIs diferentes

| API | URL | Auth | Usado para |
|-----|-----|------|------------|
| **CLOB** | `clob.polymarket.com` | Sim (pra trading) | Orderbook, preços, ordens, trades |
| **Gamma** | `gamma-api.polymarket.com` | Não | Buscar eventos, mercados, slugs |
| **Data** | `data-api.polymarket.com` | Não | Posições, perfil (limitado) |

### CLOB - Endpoints importantes
- `GET /book?token_id=xxx` — orderbook (bids/asks)
- `GET /price?token_id=xxx&side=BUY` — preço real (não é best bid/ask!)
- `GET /midpoint?token_id=xxx` — midpoint
- `GET /time` — health check
- Trading via py_clob_client (POST orders)

**IMPORTANTE:** O `/book` retorna bids/asks com valores extremos (1c/$99c com volume alto). O preço REAL vem do `/price` endpoint, que calcula considerando a profundidade do book.

### Gamma - Buscar mercados
```
GET /events?slug=sea-ver-gen-2026-03-15
GET /events?limit=50&active=true
```
Retorna markets com `clobTokenIds` (YES e NO token IDs).

### BTC 15min - Slugs determinísticos
```python
epoch = int(now.timestamp())
block_start = (epoch // 900) * 900
slug = f'btc-updown-15m-{block_start}'
```

### URLs do site
O frontend aceita vários formatos:
- `polymarket.com/event/slug`
- `polymarket.com/pt/sports/sea/slug`
- `polymarket.com/sports/nba/slug`

A Gamma API só encontra pelo slug direto, não pelo path completo.

---

## Estratégia Martingale Fade

### Conceito
Após N blocos consecutivos do mesmo lado (YES ou NO), aposta CONTRA (fade).
Se perder, dobra (martingale). Se perder 3x seguidas = bust, reseta.

### Por que funciona (não é falácia do jogador)
BTC 15min NÃO é moeda — é um mercado com reversão à média:
- Após 4 consecutivos: **56% de reverter**
- Após 5 consecutivos: **68% de reverter**
- Após 6 consecutivos: **71% de reverter**
- Após 7 consecutivos: **75% de reverter**

### Configuração ativa (PythonAnywhere)
```
Streak: 5 (esperar 5 consecutivos)
Martin: 2x (dobra a cada loss)
Entry: 45c (limit order, maker)
Base: 5 contratos (mínimo da Poly)
Stop loss: $50
```

| Nível | Contratos | Custo | Win | Loss |
|-------|-----------|-------|-----|------|
| L0 | 5 | $2.25 | +$2.75 | -$2.25 |
| L1 | 10 | $4.50 | +$5.50 | -$4.50 |
| L2 | 20 | $9.00 | +$11.00 | -$9.00 |
| Bust | | $15.75 | | |

### Backtest (37 dias, 6-Fev a 14-Mar 2026)
- **127 trades** | **69% win rate**
- **PnL: +$212**
- **Max drawdown: -$20**
- **Busts: 2**
- **$5.73/dia médio**

### Para dar bust
- 5 trigger + 3 losses = **8 blocos consecutivos** do mesmo lado = 2 horas de BTC na mesma direção
- Maior streak observada: 9x
- Pior cenário (37 dias): 3 busts em 25 horas = -$47

### Bot no PythonAnywhere
- **Script:** `/home/pedroccm/martin_bot_poly.py`
- **Runner:** `/home/pedroccm/run_martin_poly.sh` (instala deps + roda)
- **Always-on ID:** 231960
- **Log:** `/home/pedroccm/martin_bot.log`
- **Python:** 3.10 (3.13 dá problema com py_clob_client)

---

## Infraestrutura PythonAnywhere

### Always-On Tasks (4 ativos)
1. `btc_15m_simulator.py` — simulador BTC 15min Kalshi
2. `polymarket_btc_15m_v3.py` — monitor preços Polymarket
3. `btc15m_combo.py` — bot combo Kalshi (sem saldo)
4. `martin_bot_poly.py` — **MARTIN BOT POLYMARKET** (ativo)

### Dados
- `data/polymarket/` — snapshots BTC 15min (desde 6-Fev, ~8MB/dia)
- `data/btc15m_sim/` — simulações
- `data/football/` — jogos de futebol
- `data/nba/` — jogos NBA

### .env no PythonAnywhere
`/home/pedroccm/.env` contém:
- Kalshi: `PROD_KEYID`, `PROD_KEYFILE`
- Polymarket: `POLY_PRIVATE_KEY`, `POLY_FUNDER`, `POLY_CHAIN_ID`

---

## PolyChange (Exchange View)

### O que é
Interface web estilo WagerTool/Betfair para operar na Polymarket.
Escada de preços com one-click trading.

### Stack
- **Frontend:** HTML puro (single file)
- **Backend:** Flask (`server.py`) na porta 5555
- **Deploy estático:** https://polychange.netlify.app (só view, sem trading)
- **Trading:** Precisa rodar `python server.py` local

### Features
- Busca eventos por URL ou texto
- 3 escadas lado a lado (Home/Draw/Away para futebol)
- One-click trading (stake pré-definido no topo)
- Health check de todas as APIs (CLOB, Gamma, Data, Trade)
- Saldo real da Polymarket (cash + posições)
- Posições abertas calculadas dos trades do CLOB

### Ladder
- **BACK (azul)** = asks = o que está disponível para COMPRAR
- **LAY (rosa)** = bids = o que está disponível para VENDER
- Clicou → envia ordem instantaneamente (sem confirmação)

---

## Credenciais

### Polymarket
- **Private Key:** `0x2a0b8f99db75f52938b17db5bc710e6dac148c54e7785850314ed1081bca40f2`
- **Funder:** `0x35C643D4e103d16d3338c5a0A400b27Cf55045cB`
- **Chain ID:** 137 (Polygon)
- **NÃO usar API keys manuais** — usar `create_or_derive_api_creds()`

### PythonAnywhere
- **Username:** pedroccm
- **Token:** `be316f7fe18abe3069d004e4952da72756906c62`
- **API:** `https://www.pythonanywhere.com/api/v0/user/pedroccm/`

---

## Lições Aprendidas

1. **signature_type=1** é OBRIGATÓRIO para Magic wallets — sem isso dá "not enough balance"
2. **funder** é OBRIGATÓRIO — é o endereço que tem o dinheiro
3. **create_or_derive_api_creds()** — nunca usar API keys criadas manualmente no site
4. **Saldo não fica on-chain** — precisa usar `get_balance_allowance` do CLOB, não checar USDC no Polygon
5. **Mínimo 5 contratos** por ordem na Polymarket
6. **O `/book` mostra preços extremos** — usar `/price` para preço real
7. **py_clob_client não funciona com Python 3.13** no PythonAnywhere — usar 3.10
8. **Posições ganhas não são creditadas automaticamente** — precisa reivindicar manualmente no site
9. **BTC 15min tem reversão à média** — não é aleatório como moeda, fade de streak tem edge real
10. **Drawdown do backtest deve ser peak-to-valley** — não mínimo absoluto do PnL
