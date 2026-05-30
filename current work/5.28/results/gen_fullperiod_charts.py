"""生成全期（2014-2022）超额曲线、回撤、月度超额图表。
信号来自 composite.parquet，策略模拟为 top-50 等权，未经优化器约束。
2021-2022 为样本外验证期，其余为样本内信号近似。
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

plt.rcParams['font.family'] = 'Microsoft YaHei'
plt.rcParams['axes.unicode_minus'] = False

# ── 数据加载 ──────────────────────────────────────────
fwd = pd.read_parquet('data/processed/fwd_ret_panel.parquet')
idx_nav_series = pd.read_parquet('data/processed/index_quote.parquet')['nav']

RUN_MAP = {
    'expanding':   '20260527_141545__baseline_expanding_ridge_te6_lam0050',
    'rolling-36m': '20260527_141843__challenger_rolling36_te6_lam0050',
    'rolling-48m': '20260527_142056__challenger_rolling48_te6_lam0050',
    'rolling-60m': '20260527_142318__challenger_rolling60_te6_lam0050',
    'decay-hl24':  '20260528_141041__challenger_decay_ridge_hl24_te6_lam0050',
    'decay-hl36':  '20260528_141324__challenger_decay_ridge_hl36_te6_lam0050',
    'decay-hl48':  '20260528_141729__challenger_decay_ridge_hl48_te6_lam0050',
}

TOP_N = 50
VALID_START = pd.Timestamp('2021-01-01')

COLORS = {
    'expanding':   '#555555',
    'rolling-36m': '#2196F3',
    'rolling-48m': '#F44336',
    'rolling-60m': '#FF9800',
    'decay-hl24':  '#4CAF50',
    'decay-hl36':  '#9C27B0',
    'decay-hl48':  '#00BCD4',
}
LINEWIDTHS = {
    'rolling-48m': 2.5,
    'decay-hl24':  2.0,
}


def compute_strategy_returns(run_id):
    comp = pd.read_parquet(f'runs/train_valid/{run_id}/signal/composite.parquet')
    dates = sorted(comp.index)
    records = []
    for i, d in enumerate(dates[:-1]):
        d_next = dates[i + 1]
        if d not in fwd.index:
            continue
        scores = comp.loc[d].dropna()
        if len(scores) < TOP_N:
            continue
        top50 = scores.nlargest(TOP_N).index
        port_ret = fwd.loc[d, top50].dropna().mean()
        nav_start = idx_nav_series.asof(d)
        nav_end   = idx_nav_series.asof(d_next)
        bench_ret = (nav_end - nav_start) / nav_start
        records.append({
            'date':      d_next,
            'port_ret':  port_ret,
            'bench_ret': bench_ret,
            'excess':    port_ret - bench_ret,
        })
    return pd.DataFrame(records).set_index('date')


def drawdown_series(cum):
    return (cum - cum.cummax()) / cum.cummax()


def shade_valid(ax):
    ax.axvspan(VALID_START, pd.Timestamp('2023-01-01'), alpha=0.08, color='orange')
    ax.axvline(x=VALID_START, color='orange', linewidth=1.2, linestyle='--', alpha=0.6)


# ── 计算所有策略收益 ──────────────────────────────────
print('Computing returns...')
strat_data = {}
for name, run_id in RUN_MAP.items():
    strat_data[name] = compute_strategy_returns(run_id)
    df = strat_data[name]
    ir = df['excess'].mean() / df['excess'].std() * np.sqrt(12)
    ann = df['excess'].mean() * 12
    print(f'  {name}: IR={ir:.3f}, ann_excess={ann*100:.2f}%')

out_dir = Path('current work/5.28/charts')
out_dir.mkdir(parents=True, exist_ok=True)

# ── Chart 1: 全期超额累积净值曲线 ─────────────────────────────
fig, ax = plt.subplots(figsize=(14, 7))
shade_valid(ax)
ax.axhline(y=1.0, color='black', linewidth=0.8, linestyle='--', alpha=0.4)

for name, df in strat_data.items():
    cum = (1 + df['excess']).cumprod()
    lw = LINEWIDTHS.get(name, 1.5)
    ax.plot(cum.index, cum.values, color=COLORS[name], linewidth=lw, label=name, alpha=0.9)
    last_val = cum.iloc[-1]
    ax.annotate(f'{last_val:.2f}',
                xy=(cum.index[-1], last_val),
                xytext=(5, 0), textcoords='offset points',
                fontsize=7.5, color=COLORS[name], va='center')

ax.set_title('全期超额净值曲线（2014-2022）\n橙色区域 = 样本外验证期 2021-2022  |  此前为训练期信号 Top-50 等权近似',
             fontsize=13, fontweight='bold', pad=12)
ax.set_xlabel('日期', fontsize=11)
ax.set_ylabel('累积超额净值', fontsize=11)
ax.legend(fontsize=9, loc='upper left', ncol=2, framealpha=0.85)
ax.grid(True, alpha=0.3, linestyle='--')
ax.set_xlim(pd.Timestamp('2014-01-01'), pd.Timestamp('2023-03-01'))
fig.tight_layout()
fig.savefig(out_dir / 'fullperiod_cum_excess.png', dpi=150, bbox_inches='tight')
plt.close(fig)
print('Saved: fullperiod_cum_excess.png')

# ── Chart 2: 逐年超额（3 策略 × 9 年）───────────────────────
fig, axes = plt.subplots(2, 5, figsize=(20, 8))
axes = axes.flatten()
years = list(range(2014, 2023))
focus = ['expanding', 'rolling-48m', 'decay-hl24']

for idx_y, year in enumerate(years):
    ax = axes[idx_y]
    year_vals = {}
    for nm in focus:
        df = strat_data[nm]
        yr_mask = df.index.year == year
        year_vals[nm] = df.loc[yr_mask, 'excess'].sum() * 100

    bars = ax.bar(list(year_vals.keys()), list(year_vals.values()),
                  color=[COLORS[n] for n in year_vals], width=0.6, alpha=0.85)
    ax.axhline(0, color='black', linewidth=0.8)
    title = f'{year}' + (' ★OOS' if year >= 2021 else '')
    ax.set_title(title, fontsize=10, fontweight='bold',
                 color='darkorange' if year >= 2021 else 'black')
    ax.set_xticks(range(len(focus)))
    ax.set_xticklabels(['exp', 'r48', 'hl24'], rotation=20, fontsize=8)
    ax.grid(True, alpha=0.3, axis='y', linestyle='--')
    if year >= 2021:
        ax.set_facecolor('#FFF5E6')
    for bar, val in zip(bars, year_vals.values()):
        y_pos = bar.get_height() + 0.4 if val >= 0 else bar.get_height() - 1.5
        va = 'bottom' if val >= 0 else 'top'
        ax.text(bar.get_x() + bar.get_width()/2, y_pos,
                f'{val:.1f}%', ha='center', va=va, fontsize=7.5)

# Hide unused subplots (2×5=10, years=9)
axes[-1].axis('off')
fig.suptitle('逐年超额收益：expanding vs rolling-48m vs decay-hl24\n★OOS = 样本外验证期 | exp=expanding, r48=rolling-48m, hl24=decay-hl24',
             fontsize=12, fontweight='bold')
fig.tight_layout()
fig.savefig(out_dir / 'fullperiod_yearly_bar.png', dpi=150, bbox_inches='tight')
plt.close(fig)
print('Saved: fullperiod_yearly_bar.png')

# ── Chart 3: 全期超额回撤曲线 ──────────────────────────────────
fig, ax = plt.subplots(figsize=(14, 6))
shade_valid(ax)
ax.axhline(y=0, color='black', linewidth=0.8, linestyle='--', alpha=0.4)

for name, df in strat_data.items():
    cum = (1 + df['excess']).cumprod()
    dd = drawdown_series(cum) * 100
    lw = LINEWIDTHS.get(name, 1.5)
    ax.plot(dd.index, dd.values, color=COLORS[name], linewidth=lw, label=name, alpha=0.9)

ax.set_title('全期超额回撤曲线（2014-2022）\n橙色区域 = 样本外验证期',
             fontsize=13, fontweight='bold', pad=12)
ax.set_xlabel('日期', fontsize=11)
ax.set_ylabel('超额回撤 (%)', fontsize=11)
ax.legend(fontsize=9, loc='lower left', ncol=2, framealpha=0.85)
ax.grid(True, alpha=0.3, linestyle='--')
ax.set_xlim(pd.Timestamp('2014-01-01'), pd.Timestamp('2023-03-01'))
fig.tight_layout()
fig.savefig(out_dir / 'fullperiod_drawdown.png', dpi=150, bbox_inches='tight')
plt.close(fig)
print('Saved: fullperiod_drawdown.png')

# ── Chart 4: 3 策略月度超额时序图（各占一行）────────────────────
fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True)

for i, name in enumerate(['expanding', 'rolling-48m', 'decay-hl24']):
    ax = axes[i]
    df = strat_data[name]
    excess_pct = df['excess'] * 100
    bar_colors = ['#E53935' if x < 0 else '#43A047' for x in excess_pct.values]
    ax.bar(excess_pct.index, excess_pct.values, color=bar_colors, width=20, alpha=0.8)
    roll12 = excess_pct.rolling(12).mean()
    ax.plot(roll12.index, roll12.values, color=COLORS[name], linewidth=2.0, label='12月滚动均值')
    ax.axhline(0, color='black', linewidth=0.8)
    shade_valid(ax)
    ir_full = excess_pct.mean() / excess_pct.std() * (12 ** 0.5)
    ann_full = excess_pct.mean() * 12
    # Valid-period stats
    exc_valid = excess_pct.loc[excess_pct.index >= VALID_START]
    ir_valid = exc_valid.mean() / exc_valid.std() * (12 ** 0.5) if len(exc_valid) > 1 else float('nan')
    ann_valid = exc_valid.mean() * 12
    ax.set_title(
        f'{name}  |  全期 IR={ir_full:.2f} 年化超额={ann_full:.1f}%  |  '
        f'验证期 IR={ir_valid:.2f} 年化超额={ann_valid:.1f}%',
        fontsize=10, fontweight='bold', color=COLORS[name]
    )
    ax.set_ylabel('月度超额 (%)', fontsize=9)
    ax.legend(fontsize=8, loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--', axis='y')

axes[-1].set_xlabel('日期', fontsize=10)
fig.suptitle('月度超额收益时序图（2014-2022）\n绿=正超额，红=负超额，折线=12月滚动均值；橙色区域=验证期',
             fontsize=12, fontweight='bold')
fig.tight_layout()
fig.savefig(out_dir / 'fullperiod_monthly_excess.png', dpi=150, bbox_inches='tight')
plt.close(fig)
print('Saved: fullperiod_monthly_excess.png')

print('\nAll 4 charts done.')
