"""
PolyChange Backend - Proxy para Polymarket APIs + Trading.
Roda local: python server.py
Acessa: http://localhost:5555
"""
import os
import json
import requests
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

GAMMA_URL = 'https://gamma-api.polymarket.com'
CLOB_URL = 'https://clob.polymarket.com'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# Lazy-load trading client (only when needed)
_trade_client = None

def get_trade_client():
    global _trade_client
    if _trade_client is None:
        try:
            from py_clob_client.client import ClobClient
            private_key = os.getenv('POLY_PRIVATE_KEY')
            funder = os.getenv('POLY_FUNDER')
            chain_id = int(os.getenv('POLY_CHAIN_ID', 137))

            # Step 1: Create client with Magic wallet signature
            client = ClobClient(
                host=CLOB_URL,
                chain_id=chain_id,
                key=private_key,
                signature_type=1,  # Magic wallet
                funder=funder,
            )
            # Step 2: Derive API creds automatically
            creds = client.create_or_derive_api_creds()
            # Step 3: Recreate with creds
            _trade_client = ClobClient(
                host=CLOB_URL,
                chain_id=chain_id,
                key=private_key,
                signature_type=1,
                funder=funder,
                creds=creds,
            )
            print(f"Trade client OK! Funder: {funder}")
        except Exception as e:
            print(f"Trade client init failed: {e}")
    return _trade_client


# ========== Static ==========

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


# ========== Market Discovery ==========

@app.route('/api/search')
def search_events():
    """Busca eventos por texto. Ex: /api/search?q=soccer&limit=20"""
    q = request.args.get('q', '')
    limit = request.args.get('limit', 20)
    tag = request.args.get('tag', '')

    params = {'limit': limit, 'active': 'true'}
    if tag:
        params['tag'] = tag

    resp = requests.get(f'{GAMMA_URL}/events', params=params, headers=HEADERS, timeout=10)
    events = resp.json() if resp.status_code == 200 else []

    # Filter by search text if provided
    if q:
        q_lower = q.lower()
        events = [e for e in events if q_lower in e.get('title', '').lower()
                  or q_lower in e.get('slug', '').lower()]

    results = []
    for ev in events:
        markets = ev.get('markets', [])
        outcomes = []
        for mkt in markets:
            tokens_raw = mkt.get('clobTokenIds', '[]')
            if isinstance(tokens_raw, str):
                tokens = json.loads(tokens_raw) if tokens_raw else []
            else:
                tokens = tokens_raw or []

            outcomes.append({
                'question': mkt.get('question', ''),
                'yes_token': tokens[0] if len(tokens) > 0 else None,
                'no_token': tokens[1] if len(tokens) > 1 else None,
                'volume': float(mkt.get('volume', 0) or 0),
                'last_price': float(mkt.get('lastTradePrice', 0) or 0),
                'condition_id': mkt.get('conditionId', ''),
                'outcome': mkt.get('outcome', mkt.get('groupItemTitle', '')),
            })

        results.append({
            'title': ev.get('title', ''),
            'slug': ev.get('slug', ''),
            'end_date': ev.get('endDate', ''),
            'volume': sum(o['volume'] for o in outcomes),
            'outcomes': outcomes,
            'num_outcomes': len(outcomes),
        })

    return jsonify(results)


@app.route('/api/event/<slug>')
def get_event(slug):
    """Busca evento por slug. Ex: /api/event/premier-league-arsenal-chelsea"""
    resp = requests.get(f'{GAMMA_URL}/events', params={
        'slug': slug, 'limit': 1
    }, headers=HEADERS, timeout=10)

    if resp.status_code != 200:
        return jsonify({'error': 'Event not found'}), 404

    events = resp.json()
    if not events:
        return jsonify({'error': 'Event not found'}), 404

    ev = events[0]
    markets = ev.get('markets', [])
    outcomes = []

    for mkt in markets:
        tokens_raw = mkt.get('clobTokenIds', '[]')
        if isinstance(tokens_raw, str):
            tokens = json.loads(tokens_raw) if tokens_raw else []
        else:
            tokens = tokens_raw or []

        outcomes.append({
            'question': mkt.get('question', ''),
            'yes_token': tokens[0] if len(tokens) > 0 else None,
            'no_token': tokens[1] if len(tokens) > 1 else None,
            'volume': float(mkt.get('volume', 0) or 0),
            'last_price': float(mkt.get('lastTradePrice', 0) or 0),
            'condition_id': mkt.get('conditionId', ''),
            'outcome': mkt.get('outcome', mkt.get('groupItemTitle', '')),
        })

    return jsonify({
        'title': ev.get('title', ''),
        'slug': ev.get('slug', ''),
        'end_date': ev.get('endDate', ''),
        'outcomes': outcomes,
    })


# ========== Orderbook ==========

@app.route('/api/book')
def get_book():
    """Busca orderbook. Ex: /api/book?token_id=xxx"""
    token_id = request.args.get('token_id')
    if not token_id:
        return jsonify({'error': 'token_id required'}), 400

    resp = requests.get(f'{CLOB_URL}/book', params={'token_id': token_id}, timeout=5)
    if resp.status_code != 200:
        return jsonify({'error': 'Failed to fetch book'}), 500

    book = resp.json()
    bids = book.get('bids', [])
    asks = book.get('asks', [])

    # Aggregate by price level (in cents)
    bid_levels = {}
    for b in bids:
        price_cents = round(float(b['price']) * 100)
        bid_levels[price_cents] = bid_levels.get(price_cents, 0) + float(b['size'])

    ask_levels = {}
    for a in asks:
        price_cents = round(float(a['price']) * 100)
        ask_levels[price_cents] = ask_levels.get(price_cents, 0) + float(a['size'])

    return jsonify({
        'bids': bid_levels,  # {price_cents: total_size}
        'asks': ask_levels,
        'best_bid': max(bid_levels.keys()) if bid_levels else 0,
        'best_ask': min(ask_levels.keys()) if ask_levels else 0,
        'best_bid_size': bid_levels.get(max(bid_levels.keys()), 0) if bid_levels else 0,
        'best_ask_size': ask_levels.get(min(ask_levels.keys()), 0) if ask_levels else 0,
    })


@app.route('/api/books')
def get_books():
    """Busca orderbooks de multiplos tokens. Ex: /api/books?tokens=id1,id2,id3"""
    tokens_str = request.args.get('tokens', '')
    if not tokens_str:
        return jsonify({'error': 'tokens required'}), 400

    token_ids = [t.strip() for t in tokens_str.split(',') if t.strip()]
    results = {}

    for token_id in token_ids:
        try:
            resp = requests.get(f'{CLOB_URL}/book', params={'token_id': token_id}, timeout=5)
            if resp.status_code == 200:
                book = resp.json()
                bids = book.get('bids', [])
                asks = book.get('asks', [])

                bid_levels = {}
                for b in bids:
                    pc = round(float(b['price']) * 100)
                    bid_levels[pc] = bid_levels.get(pc, 0) + float(b['size'])

                ask_levels = {}
                for a in asks:
                    pc = round(float(a['price']) * 100)
                    ask_levels[pc] = ask_levels.get(pc, 0) + float(a['size'])

                results[token_id] = {
                    'bids': bid_levels,
                    'asks': ask_levels,
                    'best_bid': max(bid_levels.keys()) if bid_levels else 0,
                    'best_ask': min(ask_levels.keys()) if ask_levels else 0,
                }
            else:
                results[token_id] = {'bids': {}, 'asks': {}, 'best_bid': 0, 'best_ask': 0}
        except Exception as e:
            results[token_id] = {'bids': {}, 'asks': {}, 'best_bid': 0, 'best_ask': 0, 'error': str(e)}

    return jsonify(results)


# ========== Trading ==========

@app.route('/api/order', methods=['POST'])
def place_order():
    """Coloca ordem. Body: {token_id, price, size, side}"""
    client = get_trade_client()
    if not client:
        return jsonify({'error': 'Trade client not available'}), 500

    data = request.json
    token_id = data.get('token_id')
    price = float(data.get('price', 0))  # em centavos
    size = float(data.get('size', 0))
    side = data.get('side', 'BUY').upper()

    if not all([token_id, price, size]):
        return jsonify({'error': 'token_id, price, size required'}), 400

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY as BUY_SIDE, SELL as SELL_SIDE

        order_args = OrderArgs(
            token_id=token_id,
            price=price / 100,
            size=size,
            side=BUY_SIDE if side == 'BUY' else SELL_SIDE,
        )
        signed = client.create_order(order_args)
        result = client.post_order(signed, OrderType.GTC)
        return jsonify({'success': True, 'result': str(result)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cancel', methods=['POST'])
def cancel_order():
    """Cancela ordem. Body: {order_id}"""
    client = get_trade_client()
    if not client:
        return jsonify({'error': 'Trade client not available'}), 500

    order_id = request.json.get('order_id')
    if not order_id:
        return jsonify({'error': 'order_id required'}), 400

    try:
        result = client.cancel(order_id)
        return jsonify({'success': True, 'result': str(result)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cancel_all', methods=['POST'])
def cancel_all():
    """Cancela todas as ordens."""
    client = get_trade_client()
    if not client:
        return jsonify({'error': 'Trade client not available'}), 500
    try:
        result = client.cancel_all()
        return jsonify({'success': True, 'result': str(result)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/orders')
def get_orders():
    """Retorna ordens abertas."""
    client = get_trade_client()
    if not client:
        return jsonify({'error': 'Trade client not available'}), 500
    try:
        orders = client.get_orders()
        return jsonify(orders if orders else [])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/trades')
def get_trades():
    """Retorna trades recentes."""
    client = get_trade_client()
    if not client:
        return jsonify({'error': 'Trade client not available'}), 500
    try:
        trades = client.get_trades()
        return jsonify(trades if trades else [])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========== Balance ==========

@app.route('/api/balance')
def get_balance():
    """Retorna saldo USDC da wallet na Polygon."""
    try:
        wallet = os.getenv('POLY_FUNDER', '')
        if not wallet:
            from eth_account import Account
            acct = Account.from_key(os.getenv('POLY_PRIVATE_KEY'))
            wallet = acct.address

        # USDC on Polygon (PoS bridged)
        USDC_POS = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
        # USDC native
        USDC_NATIVE = '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'

        def get_erc20_balance(contract):
            data = '0x70a08231' + wallet[2:].lower().zfill(64)
            resp = requests.post('https://polygon-rpc.com', json={
                'jsonrpc': '2.0', 'method': 'eth_call',
                'params': [{'to': contract, 'data': data}, 'latest'],
                'id': 1
            }, timeout=10)
            result = resp.json().get('result', '0x0')
            return int(result, 16) / 1e6

        usdc = get_erc20_balance(USDC_POS)
        usdc_e = get_erc20_balance(USDC_NATIVE)

        return jsonify({
            'wallet': wallet,
            'usdc': round(usdc, 2),
            'usdc_native': round(usdc_e, 2),
            'total': round(usdc + usdc_e, 2),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("=" * 50)
    print("  POLYCHANGE SERVER")
    print("  http://localhost:5555")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5555, debug=True)
