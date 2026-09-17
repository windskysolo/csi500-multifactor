# L1/L2 Inspection Report

- Generated: 2026-06-02T18:47:40.862656+08:00
- Root: `E:\Acoding\Project\500 improve`
- Summary: PASS=9 WARN=1 FAIL=0

## L1

### L1-1 - selected factors have panel files: PASS

All selected factors have corresponding parquet files.

```json
{
  "panel_dir": "E:\\Acoding\\Project\\500 improve\\data\\processed\\factor_panels_test_run_1",
  "n_panel_files": 61,
  "n_selected": 18,
  "missing_factors": [],
  "extra_panel_files": [
    "accrual",
    "analyst_cnt_chg",
    "analyst_rating_chg",
    "asset_growth",
    "asset_turn",
    "boll_pct",
    "bp",
    "chip_winner_rate",
    "cost_deviation",
    "ep_vs_history",
    "eps_dispersion",
    "fcfp",
    "float_pct_30d",
    "garp",
    "high_52w",
    "ind_adj_mom",
    "ind_adj_mom_6_1",
    "insider_net_buy",
    "large_net_inflow",
    "leverage",
    "macd_cross",
    "margin_ratio",
    "max_ret",
    "mf_flow_ratio",
    "mom_12_1",
    "mom_6_1",
    "mom_consistency_6",
    "mom_risk_adj",
    "north_flow_5d",
    "np_yoy",
    "obv_chg_20d",
    "pledge_ratio",
    "ret_1m",
    "roa",
    "roe",
    "roe_smoothed_4q",
    "roe_stability",
    "rsi_12",
    "rsi_6",
    "share_issuance",
    "short_ratio",
    "sp_ttm",
    "vol_60d"
  ]
}
```

### L1-2 - actual test rebalance dates: PASS

Found 36 actual test rebalance dates.

```json
{
  "count": 36,
  "first": "2023-01-31",
  "last": "2025-12-31",
  "first_five": [
    "2023-01-31",
    "2023-02-28",
    "2023-03-31",
    "2023-04-28",
    "2023-05-31"
  ],
  "last_five": [
    "2025-08-29",
    "2025-09-30",
    "2025-10-31",
    "2025-11-28",
    "2025-12-31"
  ]
}
```

### L1-3 - fixed ALT factor panels: WARN

Repaired ALT factor panel rows exist, but some test rows are all-NaN and should be reviewed.

```json
{
  "failures": [],
  "warnings": [
    "hk_hold_chg: 15 all-NaN test rows",
    "hk_hold_ratio: 11 all-NaN test rows"
  ],
  "factors": {
    "analyst_eps_revision": {
      "date_min": "2012-01-31",
      "date_max": "2025-12-31",
      "rows": 168,
      "test_rows": 36,
      "missing_test_dates": [],
      "zero_valid_dates": [],
      "avg_valid_stocks": 380.30555555555554,
      "min_valid_stocks": 329
    },
    "hk_hold_chg": {
      "date_min": "2012-01-31",
      "date_max": "2025-12-31",
      "rows": 168,
      "test_rows": 36,
      "missing_test_dates": [],
      "zero_valid_dates": [
        "2024-10-31",
        "2024-11-29",
        "2024-12-31",
        "2025-01-27",
        "2025-02-28",
        "2025-03-31",
        "2025-04-30",
        "2025-05-30",
        "2025-06-30",
        "2025-07-31",
        "2025-08-29",
        "2025-09-30",
        "2025-10-31",
        "2025-11-28",
        "2025-12-31"
      ],
      "avg_valid_stocks": 288.3611111111111,
      "min_valid_stocks": 0
    },
    "hk_hold_ratio": {
      "date_min": "2012-01-31",
      "date_max": "2025-12-31",
      "rows": 168,
      "test_rows": 36,
      "missing_test_dates": [],
      "zero_valid_dates": [
        "2024-08-30",
        "2024-10-31",
        "2024-11-29",
        "2025-01-27",
        "2025-02-28",
        "2025-04-30",
        "2025-05-30",
        "2025-07-31",
        "2025-08-29",
        "2025-10-31",
        "2025-11-28"
      ],
      "avg_valid_stocks": 343.8888888888889,
      "min_valid_stocks": 0
    }
  }
}
```

### L1-4 - all selected factor test coverage: PASS

All selected factors cover every actual test rebalance date.

```json
{
  "issues": {},
  "n_selected": 18
}
```

### L1-5 - _predict_cross_section compatibility: PASS

No test date should trigger the missing-row or too-few-stocks return None path.

```json
{
  "min_valid_factors": 5,
  "n_dates_checked": 36,
  "min_qualifying_stocks": 489,
  "mean_qualifying_stocks": 496.22222222222223,
  "failures": {}
}
```

## L2

### L2-1 - public fwd_ret cache: PASS

Public fwd_ret cache has no test-period contamination and can be extended by test pipeline.

```json
{
  "date_min": "2012-01-31",
  "date_max": "2022-11-30",
  "rows": 131,
  "n_test_rows": 0,
  "avg_non_null_stocks": 1122.1221374045801,
  "min_non_null_stocks": 892,
  "overall_nan_rate": 0.15566430594087272,
  "failures": [],
  "warnings": []
}
```

### L2-2 - index_member coverage and quality: PASS

index_member covers all test dates with plausible member counts, weights, states, and ts_code format.

```json
{
  "date_min": "2011-08-23",
  "date_max": "2025-12-31",
  "n_dates": 180,
  "missing_test_dates": [],
  "member_count_stats": {
    "min": 500,
    "max": 500,
    "mean": 500.0
  },
  "weight_sum_stats": {
    "column": "index_weight",
    "raw_median_sum": 100.0,
    "normalized_min": 0.99988,
    "normalized_max": 1.0001900000000001
  },
  "missing_status_cols": [],
  "status_null_rates": {
    "tradable": 0.0,
    "is_suspended": 0.0,
    "is_limit_locked": 0.0,
    "is_limit_up_locked": 0.0,
    "is_limit_down_locked": 0.0,
    "is_st": 0.0,
    "is_new_stock": 0.0
  },
  "code_format": {
    "n_codes": 1539,
    "n_bad": 0,
    "bad_examples": []
  },
  "failures": [],
  "warnings": []
}
```

### L2-3 - daily_quote coverage and adjusted prices: PASS

daily_quote has test-period coverage, adjusted open/close prices, and suffixed ts_code values.

```json
{
  "date_min": "2011-01-04",
  "date_max": "2025-12-31",
  "n_unique_trading_days": 3644,
  "n_test_trading_days": 727,
  "columns": [
    "open_adj",
    "close_adj",
    "ret"
  ],
  "missing_required_cols": [],
  "test_nan_rates": {
    "open_adj": 0.0,
    "close_adj": 0.0
  },
  "code_format": {
    "n_codes": 1539,
    "n_bad": 0,
    "bad_examples": []
  },
  "failures": [],
  "warnings": []
}
```

### L2-4 - index_quote total-return benchmark: PASS

index_quote is confirmed as H00905.CSI total-return benchmark with valid nav coverage.

```json
{
  "date_min": "2011-01-04",
  "date_max": "2025-12-31",
  "columns": [
    "close",
    "pct_chg",
    "index_ret",
    "nav"
  ],
  "raw_code_info": {
    "raw_path": "E:\\Acoding\\Project\\500 improve\\data\\raw\\index_daily.csv",
    "exists": true,
    "code_col": "ts_code",
    "codes": [
      "H00905.CSI"
    ]
  },
  "nav_jump_max": 0.10414492996099578,
  "nav_consistency_max_abs": 0.0,
  "failures": [],
  "warnings": []
}
```

### L2-5 - industry coverage and SW2021 breadth: PASS

industry data covers test dates with plausible SW2021 first-level classification breadth.

```json
{
  "date_min": "2011-01-04",
  "date_max": "2025-12-31",
  "n_unique_dates": 3644,
  "missing_test_dates": [],
  "industry_column": "industry_code",
  "n_test_industries": 32,
  "columns": [
    "industry_code",
    "industry_name"
  ],
  "code_format": {
    "n_codes": 1539,
    "n_bad": 0,
    "bad_examples": []
  },
  "failures": [],
  "warnings": []
}
```
