"""
BTC 15min Martingale Fade Bot - Polymarket
Estrategia: Streak 5 + Martin 2x + Entry 45c

Espera 5 blocos consecutivos do mesmo lado,
aposta contra (fade) com martingale 2x (5->10->20 contratos).
Entry via limit order a 45c (maker, 0% fee).

Bust apos 3 losses seguidos -> reseta.
"""
import os
import sys
import time
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load .env - try multiple locations
for env_path in [
    Path(__file__).parent / '.env',
    Path('/home/pedroccm/.env'),
    Path('/home/pedroccm/.env.poly'),
]:
    if env_path.exists():
        load_dotenv(env_path, override=True)

# Debug: verify key is loaded
if not os.getenv('POLY_PRIVATE_KEY'):
    print(f'ERROR: POLY_PRIVATE_KEY not found in env!')
    print(f'Searched: {Path(__file__).parent / ".env"}, /home/pedroccm/.env')
    sys.exit(1)

ET = ZoneInfo('America/New_York')
UTC = ZoneInfo('UTC')
GAMMA_URL = 'https://gamma-api.polymarket.com'
CLOB_URL = 'https://clob.polymarket.com'

# === ESTRATEGIA ===
WAIT_STREAK = 5          # esperar 5 consecutivos
MARTIN_MULT = 2           # multiplicador 2x
MARTIN_MAX = 3             # max niveis (bust no 3)
BASE_CONTRACTS = 5         # 5 contratos base (minimo da Poly)
ENTRY_PRICE = 0.45         # 45c
INTERVALO = 5              # segundos entre polls
STOP_LOSS_USD = 50         # para tudo se perder $50

# === TELEGRAM (opcional) ===
TG_TOKEN = os.getenv('TG_TOKEN', '')
TG_CHAT_ID = os.getenv('TG_CHAT_ID', '')

# === SETUP ===
LOG_FILE = Path(__file__).parent / 'martin_bot.log'


def log(msg):
    ts = datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    sys.stdout.flush()
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=10,
        )
    except:
        pass


def get_client():
    from py_clob_client.client import ClobClient
    private_key = os.getenv('POLY_PRIVATE_KEY')
    funder = os.getenv('POLY_FUNDER')

    client = ClobClient(
        host=CLOB_URL, chain_id=137, key=private_key,
        signature_type=1, funder=funder,
    )
    creds = client.create_or_derive_api_creds()
    client = ClobClient(
        host=CLOB_URL, chain_id=137, key=private_key,
        signature_type=1, funder=funder, creds=creds,
    )
    return client


def get_current_blocks():
    """Busca blocos BTC 15min ativos e recentemente finalizados."""
    now = datetime.now(UTC)
    epoch = int(now.timestamp())
    block_start = (epoch // 900) * 900

    blocks = []
    # Checar bloco atual + 2 anteriores + 1 proximo
    for offset in [-2, -1, 0, 1]:
        ts = block_start + (offset * 900)
        slug = f'btc-updown-15m-{ts}'
        try:
            resp = requests.get(f'{GAMMA_URL}/events', params={'slug': slug}, timeout=10)
            if resp.status_code != 200:
                continue
            events = resp.json()
            if not events:
                continue
            ev = events[0]
            markets = ev.get('markets', [])
            if not markets:
                continue
            mkt = markets[0]
            tokens_raw = mkt.get('clobTokenIds', '[]')
            if isinstance(tokens_raw, str):
                tokens = json.loads(tokens_raw)
            else:
                tokens = tokens_raw or []

            blocks.append({
                'slug': slug,
                'ts': ts,
                'title': ev.get('title', ''),
                'end_date': ev.get('endDate', ''),
                'yes_token': tokens[0] if len(tokens) > 0 else None,
                'no_token': tokens[1] if len(tokens) > 1 else None,
                'active': mkt.get('active', False),
                'closed': mkt.get('closed', False),
                'last_price': float(mkt.get('lastTradePrice', 0) or 0),
            })
        except:
            continue

    return blocks


def get_price(token_id, side='BUY'):
    """Retorna preco em decimal (0-1)."""
    try:
        resp = requests.get(f'{CLOB_URL}/price', params={
            'token_id': token_id, 'side': side
        }, timeout=5)
        if resp.status_code == 200:
            return float(resp.json().get('price', 0))
    except:
        pass
    return 0


def determine_winner(block):
    """Determina vencedor de bloco finalizado."""
    if not block.get('yes_token'):
        return None
    price = get_price(block['yes_token'], 'BUY')
    if price >= 0.80:
        return 'YES'
    elif price <= 0.20:
        return 'NO'
    # Tambem checar last_price
    if block.get('last_price', 0) >= 0.80:
        return 'YES'
    elif block.get('last_price', 0) <= 0.20:
        return 'NO'
    return None


def place_order(client, token_id, price, contracts):
    """Coloca limit order."""
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY

    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=contracts,
        side=BUY,
    )
    signed = client.create_order(order_args)
    result = client.post_order(signed, OrderType.GTC)
    return result


def main():
    log('=' * 50)
    log('  MARTIN BOT - Polymarket BTC 15min')
    log(f'  Streak: {WAIT_STREAK} | Martin: {MARTIN_MULT}x | Entry: {int(ENTRY_PRICE*100)}c')
    log(f'  Base: {BASE_CONTRACTS}ct | Sizes: {BASE_CONTRACTS} -> {BASE_CONTRACTS*2} -> {BASE_CONTRACTS*4}')
    log(f'  Stop loss: ${STOP_LOSS_USD}')
    log('=' * 50)

    client = get_client()
    log('Client connected!')

    send_telegram(
        f'<b>MARTIN BOT started</b>\n'
        f'Streak: {WAIT_STREAK} | Martin: {MARTIN_MULT}x\n'
        f'Entry: {int(ENTRY_PRICE*100)}c | Base: {BASE_CONTRACTS}ct\n'
        f'Sizes: {BASE_CONTRACTS}->{BASE_CONTRACTS*2}->{BASE_CONTRACTS*4}'
    )

    # State
    streak_side = None
    streak_count = 0
    resolved_slugs = set()

    in_martin = False
    martin_level = 0
    bet_side = None  # 'YES' or 'NO'

    pending_order = None  # {slug, order_id, token_id, side, contracts, price}
    active_trade = None   # {slug, token_id, side, contracts, price}

    pnl_total = 0
    trade_count = 0
    win_count = 0
    bust_count = 0

    loop_count = 0
    last_log = 0

    while True:
        try:
            now = datetime.now(ET)
            loop_count += 1

            blocks = get_current_blocks()

            for block in blocks:
                slug = block['slug']

                # === BLOCO FINALIZADO: resolver ===
                if (block['closed'] or not block['active']) and slug not in resolved_slugs:
                    winner = determine_winner(block)
                    if winner is None:
                        continue

                    resolved_slugs.add(slug)

                    # Resolver trade ativo
                    if active_trade and active_trade['slug'] == slug:
                        ct = active_trade['contracts']
                        if active_trade['side'] == winner:
                            profit = ct * (1.0 - ENTRY_PRICE)
                            pnl_total += profit
                            result = 'WIN'
                            win_count += 1
                        else:
                            loss = ct * ENTRY_PRICE
                            pnl_total -= loss
                            profit = -loss
                            result = 'LOSS'

                        trade_count += 1
                        log(f'  {result} L{martin_level} | {active_trade["side"]} {ct}ct @ {int(ENTRY_PRICE*100)}c | pnl=${profit:+.2f} | total=${pnl_total:+.2f}')

                        active_trade = None

                        if result == 'WIN':
                            in_martin = False
                            martin_level = 0
                            bet_side = None
                        else:
                            martin_level += 1
                            if martin_level >= MARTIN_MAX:
                                bust_count += 1
                                log(f'  BUST #{bust_count}! Resetting.')
                                send_telegram(f'BUST #{bust_count}! PnL: ${pnl_total:+.2f}')
                                in_martin = False
                                martin_level = 0
                                bet_side = None

                    # Cancelar ordem pendente desse bloco
                    if pending_order and pending_order['slug'] == slug:
                        try:
                            client.cancel(pending_order['order_id'])
                            log(f'  Cancelled unfilled order for {slug}')
                        except:
                            pass
                        pending_order = None

                    # Atualizar streak
                    if winner == streak_side:
                        streak_count += 1
                    else:
                        streak_side = winner
                        streak_count = 1

                    log(f'  BLOCK {slug} -> {winner} | streak={streak_count}x{streak_side}')

                    # Trigger martin?
                    if not in_martin and streak_count >= WAIT_STREAK:
                        in_martin = True
                        martin_level = 0
                        bet_side = 'NO' if streak_side == 'YES' else 'YES'
                        log(f'  >>> MARTIN TRIGGERED: bet {bet_side} (fade {streak_side} x{streak_count})')
                        send_telegram(f'MARTIN triggered! Bet {bet_side} (fade {streak_side} x{streak_count})')

                # === BLOCO ATIVO: colocar ordem ===
                if block['active'] and not block['closed']:
                    if in_martin and bet_side and active_trade is None and pending_order is None:
                        # Stop loss check
                        if pnl_total <= -STOP_LOSS_USD:
                            log(f'STOP LOSS! PnL=${pnl_total:.2f}')
                            send_telegram(f'STOP LOSS! PnL=${pnl_total:.2f}')
                            return

                        contracts = int(BASE_CONTRACTS * (MARTIN_MULT ** martin_level))
                        token_id = block['yes_token'] if bet_side == 'YES' else block['no_token']

                        if not token_id:
                            continue

                        log(f'  PLACING: {bet_side} {contracts}ct @ {int(ENTRY_PRICE*100)}c L{martin_level} | {slug}')

                        try:
                            result = place_order(client, token_id, ENTRY_PRICE, contracts)
                            order_id = result.get('orderID', '')
                            status = result.get('status', '')

                            if result.get('success'):
                                if status in ('matched', 'live', 'delayed'):
                                    # Assumir como preenchido (limit order)
                                    active_trade = {
                                        'slug': slug,
                                        'token_id': token_id,
                                        'side': bet_side,
                                        'contracts': contracts,
                                        'price': ENTRY_PRICE,
                                    }
                                    log(f'  ORDER OK: {order_id} status={status}')
                                else:
                                    pending_order = {
                                        'slug': slug,
                                        'order_id': order_id,
                                        'token_id': token_id,
                                        'side': bet_side,
                                        'contracts': contracts,
                                        'price': ENTRY_PRICE,
                                    }
                                    log(f'  ORDER PENDING: {order_id} status={status}')
                            else:
                                log(f'  ORDER FAILED: {result}')
                        except Exception as e:
                            log(f'  ORDER ERROR: {str(e)[:100]}')

                    # Check pending order fill
                    if pending_order and pending_order['slug'] == slug:
                        try:
                            orders = client.get_orders()
                            order_found = False
                            if orders:
                                for o in orders:
                                    if o.get('id') == pending_order['order_id']:
                                        order_found = True
                                        break
                            if not order_found:
                                # Order not in open orders = filled or cancelled
                                active_trade = {
                                    'slug': slug,
                                    'token_id': pending_order['token_id'],
                                    'side': pending_order['side'],
                                    'contracts': pending_order['contracts'],
                                    'price': ENTRY_PRICE,
                                }
                                log(f'  FILLED: {pending_order["side"]} {pending_order["contracts"]}ct')
                                pending_order = None
                        except:
                            pass

            # Cleanup old slugs
            if len(resolved_slugs) > 500:
                resolved_slugs = set(list(resolved_slugs)[-200:])

            # Heartbeat log every 5 min
            if time.time() - last_log >= 300:
                status = f'MARTIN L{martin_level}' if in_martin else 'WATCHING'
                log(f'  heartbeat | loops={loop_count} | streak={streak_count}x{streak_side or "?"} | trades={trade_count} W:{win_count} | busts={bust_count} | pnl=${pnl_total:+.2f} | {status}')
                last_log = time.time()

            time.sleep(INTERVALO)

        except KeyboardInterrupt:
            log(f'Stopped. trades={trade_count} W:{win_count} busts={bust_count} pnl=${pnl_total:+.2f}')
            send_telegram(f'Bot stopped. PnL: ${pnl_total:+.2f}')
            break
        except Exception as e:
            log(f'ERROR: {str(e)[:100]}')
            time.sleep(10)
            # Reconnect client
            try:
                client = get_client()
            except:
                pass


if __name__ == '__main__':
    main()
