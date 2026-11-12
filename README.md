# CSC 573-Project2
# TCP Reno vs. Cubic (Mininet)

Run on a Linux machine with root (we used an Ubuntu 22.04 VCL reservation on NC State VCL reservation website).

## Setup

```bash
sudo apt update
sudo apt install -y mininet iperf ethtool
git clone https://github.com/Jazgreen03/InternetProtocol_Project2.git
cd InternetProtocol_Project2
git checkout <Green/monitors-plots>
ls -l data/file_s2d2        # must be 1048576 bytes
```

## Run

Use `sudo python3` (not the virtual environment). Use a new folder name for each run.

```bash
sudo python3 run_experiment.py --quick --queues 20 --outdir results/quick_test   # 20 s test to make sure running
sudo python3 run_experiment.py --outdir results/run4                             # full run, about 10 min approx.
```

After a crash, run `sudo mn -c` and try again.

Results are written to `results/run4/q20/`, `q50/` and `q100/`.

## Plots

```bash
sudo apt install -y python3.10-venv   # first time only
cd ~/InternetProtocol_Project2
python3 -m venv .venv
source .venv/bin/activate
pip install matplotlib          # first time only
python plot.py results/run6 --out ./run6-plots
```

Add `--free` (with a different `--out` folder) to plot free buffer space instead of queue length.
