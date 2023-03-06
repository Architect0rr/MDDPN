#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# First created by Egor Perevoshchikov at 2022-10-29 15:41.
# Last-update: 2023-01-07 16:21:52
#


from pathlib import Path
# import sys

import numpy as np
import adios2
import freud
import fastd as fd
import pandas as pd
import argparse
from datetime import datetime
# import re
# from os import listdir


def main(file: Path, nst: int, fs: Path):
    print("Main opening: ", str(file))
    adin = adios2.open(str(file), 'r')  # type: ignore
    N = int(adin.read('natoms'))
    Lx = adin.read('boxxhi')
    Ly = adin.read('boxyhi')
    Lz = adin.read('boxzhi')
    total_count = adin.steps()
    print("Total step count: ", total_count)
    if nst > total_count:
        adin.close()
        raise RuntimeError(
            "Needed step is bigger than total available step count")

    box = freud.box.Box.from_box(np.array([Lx, Ly, Lz]))

    print("Box volume is: ", box.volume)
    print("N atoms: ", N)

    for step in adin:
        if int(step.current_step()) != nst:
            continue
        else:
            arr = step.read('atoms')
            arr = arr[:, 2:5]
            sizes, dist = fd.proc(arr, N, box)
            print("Created clusters: ",len(dist[dist > 1]))
            pd.DataFrame(np.vstack([sizes, dist])).to_csv(fs, header=False, index=False)
            break
    adin.close()


def lstt(cwd: Path, storages: list):
    # onlyfiles = [f for f in listdir(tdir)]
    # tf = [f for f in onlyfiles if re.match(r"^dump[0-9]?.bp$", f)]
    # tf.sort()
    files = {}
    gs = 0
    for fn in storages:
        adin = adios2.open(str(cwd / fn), 'r')  # type: ignore
        total_count = adin.steps()
        adin.close()
        files[fn] = {}
        files[fn]["min"] = gs
        gs += total_count
        files[fn]["max"] = gs + 1
    return files


if __name__ == "__main__":
    print("Started at ", datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
    parser = argparse.ArgumentParser(
        description='Get cluster distribution from ADIOS2 db generated by LAMMPS.')
    parser.add_argument('--steps', metavar='steps', type=int, nargs='+', required=True,
                        help='steps to get')
    parser.add_argument('--storages', metavar='storages', type=str, nargs='+', required=True,
                        help='.bp storages')
    args = parser.parse_args()
    cwd = Path.cwd()
    pfiles = lstt(cwd, args.storages)
    for step in args.steps:
        for key, val in pfiles.items():
            if val['min'] < step and step < val['max']:
                main(cwd / key, step - val['min'], cwd / ("step" + str(step) + ".csv"))
                break
    print("End. Exit...")
else:
    raise ImportError("Cannot be imported")

# possible can stumble world kiss manual label payment fee green omit traffic
