import pandas as pd
import glob

files = sorted(glob.glob('E:/sites/kalshi/data/pythonanywhere/polymarket/poly_btc15m_*.csv'))
dfs = [pd.read_csv(f) for f in files]
data = pd.concat(dfs, ignore_index=True)
data['close_time'] = pd.to_datetime(data['close_time'], format='mixed')

blocks = data.groupby('event_ticker').agg(
    last_yes=('yes_buy', 'last'),
    close_time=('close_time', 'first'),
).reset_index()
blocks['winner'] = blocks['last_yes'].apply(
    lambda x: 'YES' if x >= 80 else ('NO' if x <= 20 else 'UNKNOWN'))
br = blocks[blocks['winner'] != 'UNKNOWN'].sort_values('close_time').reset_index(drop=True)
print(f'Blocos: {len(br)}')


def run_bt(blocks_df, base_ct=5, entry=0.48, mult=3, max_lvl=3, wait=4, fee=0):
    s_side = None
    s_count = 0
    in_m = False
    m_lvl = 0
    bet = None
    trades = []
    pnl = 0
    peak = 0
    max_dd = 0

    for _, row in blocks_df.iterrows():
        w = row['winner']

        if in_m and bet:
            ct = int(base_ct * (mult ** m_lvl))
            cost = ct * entry
            f = ct * fee

            if bet == w:
                profit = ct * (1.0 - entry) - f
                pnl += profit
                result = 'WIN'
            else:
                profit = -(cost + f)
                pnl += profit
                result = 'LOSS'

            if pnl > peak:
                peak = pnl
            dd = peak - pnl
            if dd > max_dd:
                max_dd = dd

            trades.append({
                'close_time': row['close_time'],
                'winner': w, 'bet': bet, 'lvl': m_lvl,
                'ct': ct, 'cost': round(cost, 2), 'fee': round(f, 2),
                'pnl': round(profit, 2), 'pnl_total': round(pnl, 2),
                'result': result,
            })

            if result == 'WIN':
                in_m = False
                m_lvl = 0
                bet = None
            else:
                m_lvl += 1
                if m_lvl >= max_lvl:
                    in_m = False
                    m_lvl = 0
                    bet = None

        if w == s_side:
            s_count += 1
        else:
            s_side = w
            s_count = 1

        if not in_m and s_count >= wait:
            in_m = True
            m_lvl = 0
            bet = 'NO' if s_side == 'YES' else 'YES'

    return pd.DataFrame(trades), pnl, max_dd


# CENARIO 1: MAKER (0% fee)
print('\n' + '='*60)
print('  MAKER (limit order, 0% fee)')
print('  Base: 5 contratos | Entry: 48c | Martin: 3x')
print('='*60)
t1, p1, d1 = run_bt(br, fee=0)
w1 = (t1['result'] == 'WIN').sum()
b1 = len(t1[(t1['lvl'] == 2) & (t1['result'] == 'LOSS')])
print(f'Trades: {len(t1)} | Wins: {w1} ({100*w1/len(t1):.0f}%)')
print(f'PnL: ${p1:.2f}')
print(f'Max Drawdown: -${d1:.2f}')
print(f'Busts: {b1} (custo cada: $31.20)')
for lvl in range(3):
    lt = t1[t1['lvl'] == lvl]
    if len(lt) == 0: continue
    ww = (lt['result'] == 'WIN').sum()
    ll = (lt['result'] == 'LOSS').sum()
    pp = lt['pnl'].sum()
    ct = int(5 * 3**lvl)
    print(f'  L{lvl} ({ct}ct/${ct*0.48:.2f}): {len(lt)} trades | W:{ww} L:{ll} ({100*ww/len(lt):.0f}%) | PnL: ${pp:.2f}')

# CENARIO 2: TAKER (2c/contrato)
print('\n' + '='*60)
print('  TAKER (market order, 2c/contrato fee)')
print('  Base: 5 contratos | Entry: 48c | Martin: 3x')
print('='*60)
t2, p2, d2 = run_bt(br, fee=0.02)
w2 = (t2['result'] == 'WIN').sum()
b2 = len(t2[(t2['lvl'] == 2) & (t2['result'] == 'LOSS')])
print(f'Trades: {len(t2)} | Wins: {w2} ({100*w2/len(t2):.0f}%)')
print(f'PnL: ${p2:.2f}')
print(f'Max Drawdown: -${d2:.2f}')
print(f'Busts: {b2}')
for lvl in range(3):
    lt = t2[t2['lvl'] == lvl]
    if len(lt) == 0: continue
    ww = (lt['result'] == 'WIN').sum()
    ll = (lt['result'] == 'LOSS').sum()
    pp = lt['pnl'].sum()
    ct = int(5 * 3**lvl)
    print(f'  L{lvl} ({ct}ct/${ct*0.48:.2f}+fee): {len(lt)} trades | W:{ww} L:{ll} ({100*ww/len(lt):.0f}%) | PnL: ${pp:.2f}')

# SENSIBILIDADE
print('\n' + '='*60)
print('  SENSIBILIDADE (taker fee 2c/ct)')
print('='*60)
results = []
for streak in [3, 4, 5]:
    for entry in [0.45, 0.48, 0.50]:
        for base in [5, 10]:
            t, p, d = run_bt(br, base_ct=base, entry=entry, wait=streak, fee=0.02)
            if len(t) > 0:
                w = (t['result'] == 'WIN').sum()
                b = len(t[(t['lvl'] == 2) & (t['result'] == 'LOSS')])
                bust_cost = sum(int(base * 3**l) * (entry + 0.02) for l in range(3))
                results.append({
                    'streak': streak,
                    'entry': f'{int(entry*100)}c',
                    'base': f'{base}ct',
                    'trades': len(t),
                    'wr%': round(100*w/len(t), 0),
                    'pnl$': round(p, 2),
                    'max_dd$': round(d, 2),
                    'busts': b,
                    'bust$': round(bust_cost, 2),
                })

sens = pd.DataFrame(results).sort_values('pnl$', ascending=False)
print(sens.to_string(index=False))

# PnL diario
print('\n' + '='*60)
print('  PNL DIARIO (config padrao, taker fee)')
print('='*60)
if len(t2) > 0:
    t2['date'] = t2['close_time'].dt.date
    daily = t2.groupby('date').agg(
        trades=('result', 'count'),
        wins=('result', lambda x: (x == 'WIN').sum()),
        pnl=('pnl', 'sum'),
    )
    daily['pnl_cum'] = daily['pnl'].cumsum()
    for date, row in daily.iterrows():
        bar = '+' * int(abs(row['pnl'])) if row['pnl'] > 0 else '-' * int(abs(row['pnl']))
        print(f'  {date} | {row["trades"]:2.0f} trades | W:{row["wins"]:2.0f} | ${row["pnl"]:+7.2f} | cum: ${row["pnl_cum"]:+8.2f} | {bar}')
