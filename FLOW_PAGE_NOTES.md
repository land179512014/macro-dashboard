# Flow Page Notes

## Interaction Rules
- Sector Flow table rows should expand directly underneath the clicked sector or industry.
- Clicking the same sector again should minimize the expanded drilldown.
- Inside the sector drilldown, ticker charts should expand directly underneath the clicked ticker row.
- Clicking the same ticker again should minimize the chart row.

## Sector Drilldown Data
- Use Finviz as the stock source for selected industries.
- Use the performance view for stock performance fields:
  - `https://finviz.com/screener?v=141&f=ind_tobacco`
- Use the valuation view for valuation fields:
  - `https://finviz.com/screener?v=121&f=ind_tobacco`
- Company name is not needed in the drilldown stock table.

## Pending Work
- Rotation Inside This Sector remains a placeholder until the rotation formula is supplied.
- The stock table should preserve sortable columns so the future rotation formula can build on the same parsed fields.
