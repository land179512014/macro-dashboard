# Flow Page Notes

## Interaction Rules
- Sector Flow table rows should expand directly underneath the clicked sector or industry.
- Clicking the same sector again should minimize the expanded drilldown.
- Inside the sector drilldown, ticker charts should expand directly underneath the clicked ticker row.
- Clicking the same ticker again should minimize the chart row.

## Sector Drilldown Data
- Use Finviz as the stock source for selected industries.
- Use filtered Finviz tabs to eliminate smaller/illiquid names:
  - Performance: `https://finviz.com/screener?v=141&f=cap_smallover,ind_tobacco,sh_avgvol_o100&o=-perf52w`
  - Valuation: `https://finviz.com/screener?v=121&f=cap_smallover,ind_tobacco,sh_avgvol_o100&o=-perf52w`
- Company name is not needed in the drilldown stock table.
- Performance view should include `Volatility W` and `Volatility M` columns.
- The stock table should preserve sortable columns and default-sort by the rotation score.

## Rotation Score
- Use the volatility-adjusted formula supplied by George:
  - `D = Perf Month`
  - `E = Perf Quarter`
  - `L = Volatility Week`
  - `M = Volatility Month`
- Formula:
  - `((D*0.7)+((D-(E/3))*0.3))*0.4 + (((D*0.4)+(E*0.6))*(M/L))*0.6`
- Finviz percentage strings are converted to decimals before scoring, so `12.3%` is treated as `0.123`.
- Leave score blank if any required input is missing or if `Volatility W` / `Volatility M` is zero.

## UI Controls
- Flow page should include a table font-size toggle: Normal, Large, XL.

## Pending Work
- Compare the decimal-scaled score against the Google Sheet output once sample rows are available.
