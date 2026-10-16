# Terminal report utility

`report_cli.py` is a standalone terminal utility, separate from the Tkinter
application. From the project root, run:

```bash
python -m scripts.report_cli --price 200000
```

The current price is required in BRL. The utility reads `data/state.json` and,
when present, `data/cash_flow.json` by default. Use `--initial-price` to include
a buy-and-hold comparison, or `--state` and `--cash-flow` to select other files.
