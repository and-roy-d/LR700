# LR700 Workflows

This repository separates the BlueFors/BFTC workflow from the Lake Shore 370 workflow, while keeping shared LR700-related utilities at the top level.

## Layout

```text
.
├── bftc_workflow/
│   ├── Tc_check_main.py
│   ├── bftc.py
│   ├── bftc_test_adaptive.py
│   ├── data_logger.py
│   ├── ramp_and_log.py
│   └── ramp_heater.py
├── lakeshore_workflow/
│   ├── data_logger.py
│   ├── lakeshore_370_temperature_test.py
│   └── main.py
├── dash_app.py
├── lr700.py
├── lr700_new.py
├── prologix_lr700_test.py
└── Data/
```

## Shared Pieces

- `dash_app.py` reads the latest `.npy` log file and serves the live plotter.
- `lr700.py`, `lr700_new.py`, and `prologix_lr700_test.py` stay at the top level as shared LR700 utilities.

## Workflows

### BlueFors / BFTC

Run:

```bash
python bftc_workflow/Tc_check_main.py
```

### Lake Shore 370 + LR700

Run:

```bash
python lakeshore_workflow/main.py
```

This workflow logs:

- `r_ohm`
- `x_ohm`
- `t_K`
- `p_uW` as `NaN`
- `time_s`

The file format stays compatible with the existing live plotter because the original `r_ohm`, `t_K`, `p_uW`, and `time_s` fields are preserved.

To probe the LS370 directly:

```bash
python lakeshore_workflow/lakeshore_370_temperature_test.py --port COM6 --channel 4
```
