import pandas as pd
import glob

# Carregar dados
files = sorted(glob.glob('E:/sites/kalshi/data/pythonanywhere/polymarket/poly_btc15m_*.csv'))
print(f'Arquivos: {len(files)}')

dfs = []
for f in files:
    dfs.append(pd.read_csv(f))
data = pd.concat(dfs, ignore_index=True)
print(f'Total rows: {len(data):,}')
print(f'Periodo: {data["timestamp"].iloc[0]} a {data["timestamp"].iloc[-1]}')

# Extrair resultados de cada bloco
data['timestamp'] = pd.to_datetime(data['timestamp'])
data['close_time'] = pd.to_datetime(data['close_time'], format='mixed')

blocks = data.groupby('event_ticker').agg(
    last_yes=('yes_buy', 'last'),
    close_time=('close_time', 'first'),
).reset_index()

blocks['winner'] = blocks['last_yes'].apply(
    lambda x: 'YES' if x >= 80 else ('NO' if x <= 20 else 'UNKNOWN')
)

print(f'\nTotal blocos: {len(blocks)}')
print(f'Resolvidos: {(blocks["winner"] != "UNKNOWN").sum()}')
print(blocks['winner'].value_counts().to_string())

blocks_resolved = blocks[blocks['winner'] != 'UNKNOWN'].sort_values('close_time').reset_index(drop=True)
print(f'\nBlocos para backtest: {len(blocks_resolved)}')

# Backtest engine
def run_backtest(blocks_df, wait_streak=4, martin_mult=3.0, martin_max=3, base_size=2.0, entry_price=48):
    streak_side = None
    streak_count = 0
    in_martin = False
    martin_level = 0
    bet_side = None
    trades = []
    pnl_total = 0

    for i, row in blocks_df.iterrows():
        winner = row['winner']

        if in_martin and bet_side:
            size = base_size * (martin_mult ** martin_level)
            cost = size * (entry_price / 100)

            if bet_side == winner:
                pnl = size - cost
                result = 'WIN'
            else:
                pnl = -cost
                result = 'LOSS'

            pnl_total += pnl
            trades.append({
                'close_time': row['close_time'],
                'winner': winner,
                'bet_side': bet_side,
                'martin_level': martin_level,
                'size': round(size, 2),
                'cost': round(cost, 2),
                'pnl': round(pnl, 2),
                'pnl_total': round(pnl_total, 2),
                'result': result,
            })

            if result == 'WIN':
                in_martin = False
                martin_level = 0
                bet_side = None
            else:
                martin_level += 1
                if martin_level >= martin_max:
                    in_martin = False
                    martin_level = 0
                    bet_side = None

        if winner == streak_side:
            streak_count += 1
        else:
            streak_side = winner
            streak_count = 1

        if not in_martin and streak_count >= wait_streak:
            in_martin = True
            martin_level = 0
            bet_side = 'NO' if streak_side == 'YES' else 'YES'

    return pd.DataFrame(trades), pnl_total


# Config padrao
WAIT = 4
MULT = 3.0
MAX_LVL = 3
BASE = 2.0
ENTRY = 48

trades_df, total_pnl = run_backtest(blocks_resolved, WAIT, MULT, MAX_LVL, BASE, ENTRY)

print(f'\n{"="*55}')
print(f'  BACKTEST MARTINGALE FADE - BTC 15min Polymarket')
print(f'{"="*55}')
print(f'Blocos: {len(blocks_resolved)} | Streak: {WAIT} | Martin: {MULT}x | Base: ${BASE} | Entry: {ENTRY}c')
print(f'Sizes: ${BASE} -> ${BASE*3} -> ${BASE*9}')
print(f'\n--- RESULTADOS ---')
print(f'Total trades: {len(trades_df)}')

if len(trades_df) > 0:
    wins = (trades_df['result'] == 'WIN').sum()
    losses = (trades_df['result'] == 'LOSS').sum()
    print(f'Wins: {wins} ({100*wins/len(trades_df):.1f}%)')
    print(f'Losses: {losses} ({100*losses/len(trades_df):.1f}%)')
    print(f'PnL total: ${total_pnl:.2f}')
    print(f'PnL medio/trade: ${total_pnl/len(trades_df):.2f}')
    print(f'Maior ganho: ${trades_df["pnl"].max():.2f}')
    print(f'Maior perda: ${trades_df["pnl"].min():.2f}')
    print(f'Max drawdown: ${trades_df["pnl_total"].min():.2f}')
    print(f'Max profit: ${trades_df["pnl_total"].max():.2f}')

    busts = trades_df[(trades_df['martin_level'] == MAX_LVL - 1) & (trades_df['result'] == 'LOSS')]
    print(f'\nBusts (3 losses seguidos): {len(busts)}')
    print(f'Custo total busts: ${busts["cost"].sum():.2f}')

    print(f'\nPor nivel:')
    for lvl in range(MAX_LVL):
        lt = trades_df[trades_df['martin_level'] == lvl]
        if len(lt) > 0:
            w = (lt['result'] == 'WIN').sum()
            l = (lt['result'] == 'LOSS').sum()
            p = lt['pnl'].sum()
            print(f'  L{lvl} (${BASE * MULT**lvl:.0f}): {len(lt)} trades | W:{w} L:{l} ({100*w/len(lt):.0f}%) | PnL: ${p:.2f}')

# Streaks
print(f'\n--- STREAKS ---')
streaks = []
s_side = None
s_count = 0
for _, row in blocks_resolved.iterrows():
    if row['winner'] == s_side:
        s_count += 1
    else:
        if s_count > 0:
            streaks.append(s_count)
        s_side = row['winner']
        s_count = 1
if s_count > 0:
    streaks.append(s_count)

streak_counts = pd.Series(streaks).value_counts().sort_index()
for s, c in streak_counts.items():
    marker = ' <<<' if s == WAIT else ''
    print(f'  {s}x: {c} vezes{marker}')
print(f'  Max streak: {max(streaks)}x')

# Sensibilidade
print(f'\n{"="*55}')
print(f'  SENSIBILIDADE')
print(f'{"="*55}')

results = []
for streak in [3, 4, 5, 6]:
    for entry in [45, 48, 50]:
        for base in [1, 2, 5]:
            t, pnl = run_backtest(blocks_resolved, streak, MULT, MAX_LVL, base, entry)
            if len(t) > 0:
                w = (t['result'] == 'WIN').sum()
                b = len(t[(t['martin_level'] == MAX_LVL - 1) & (t['result'] == 'LOSS')])
                results.append({
                    'streak': streak, 'entry': entry, 'base$': base,
                    'trades': len(t), 'wins': w,
                    'wr%': round(100*w/len(t), 1),
                    'pnl$': round(pnl, 2), 'busts': b,
                    'max_dd$': round(t['pnl_total'].min(), 2),
                    'pnl/trade$': round(pnl/len(t), 2),
                })

sens = pd.DataFrame(results).sort_values('pnl$', ascending=False)
print(f'\nTop 10:\n')
print(sens.head(10).to_string(index=False))
print(f'\nBottom 5:')
print(sens.tail(5).to_string(index=False))

# Trades detalhados
print(f'\n{"="*55}')
print(f'  TRADES (config padrao)')
print(f'{"="*55}')
if len(trades_df) > 0:
    for _, t in trades_df.iterrows():
        e = 'W' if t['result'] == 'WIN' else 'L'
        print(f'  {t["close_time"]} | {e} L{t["martin_level"]} | bet={t["bet_side"]:3s} vs {t["winner"]:3s} | ${t["size"]:5.0f} @ {ENTRY}c | pnl=${t["pnl"]:+6.2f} | total=${t["pnl_total"]:+7.2f}')
