# Open-MXO2-Mini – reverse engineering

How the driver came about, and the tools used. [`NOTES.md`](NOTES.md) is the chronological research log: the Gaia
FPGA registers, SRI/I2C bridge, AD9398 and ADV7181 setup, capture/DMA/interrupt model, audio, colour and timing
findings, and every probe with its result. Later sections supersede earlier guesses.

## Contents

| Path | |
|---|---|
| `NOTES.md` | Research log |
| `tools/*.py` | Python probes that drive the card from user space through sysfs (BAR0 registers, BAR1 card memory) with the driver unloaded, plus test tools for the finished driver (`obs_sim.py`, `obs_live_test.py`, `obs_ws_test.py`, `obs_pw_switch_test.py`, `mode_test.sh`, which run as a normal user in the `video` group) and table generators (`gen_adv7181_header.py`, `chipregs.py`, `adv7181_tables.py`) |
| `run-*.sh` | Wrappers that run one probe or driver test as root and write their log to `notes/` (`reload-driver.sh` loads the build from `../driver`, and pauses PipeWire if it holds the card) |
| `windows/` | Windows 7 helpers that capture the Matrox driver's register writes (RW-Everything, kd) for comparison |

Not included (see the top-level `.gitignore`): `notes/` (captured frames, WAVs, register/I2C dumps, logs that the
scripts write), `bin/` (the Matrox Windows driver binaries the register tables are extracted from), `decomp/` and
`ghidra-proj/` (Ghidra output for those binaries). Some table generators need `bin/MVKAVCIO.sys` and
`bin/MVKAVBUS.sys` from the Matrox driver package. The generated tables are already committed:
`tools/adv7181_modes.py`, `../driver/*_modes.h`.

## Running the probes

The probes write to the card's registers directly. Run them only with the driver unloaded (the `run-*.sh` scripts
do that), as root, and expect to reload the driver (`sudo modprobe mxo2`) afterwards. Most scripts assume the card at
PCI address `0000:02:00.0`.
