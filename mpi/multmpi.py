#!/usr/bin/env python3.8
# -*- coding: utf-8 -*-

# First created by Egor Perevoshchikov at 2022-10-29 15:41.
# Last-update: 2023-02-19 19:23:11
#

from math import floor
from pathlib import Path
from urllib import response
from mpi4py import MPI
import mpiworks as MW
from mpiworks import MPIComm, MPI_TAGS, MPISanityError

from typing import Dict, Union, Tuple, List

import numpy as np
# from multiprocessing import Process, Manager
import adios2
import freud
import fastd_mpi as fd
import treat_mpi as treat
import time
import argparse
import json
from datetime import datetime
from enum import Enum


# prdata_file = "ntb.bp"
data_file = 'data.json'


# def stateWriter(mpi_comm: MPIComm, mpi_rank, mpi_size):
#     savefile = "pstates.json"
#     to_write = {}
#     while True:
#         data = stateQue.get()
#         if isinstance(data, fd.SpecialObject):
#             break
#         to_write[data[0]] = data[1]
#         with open(cwd / savefile, 'w') as fp:
#             json.dump(to_write, fp)


class Role(Enum):
    reader = 0
    proceeder = 1
    treater = 2
    kill = 3


def reader(cwd: Path, mpi_comm: MPIComm, mpi_rank: int, mpi_size: int) -> None:
    mpi_comm.Barrier()
    ino, storages_ = mpi_comm.recv(source=0, tag=MPI_TAGS.SERV_DATA)  # type: Tuple[int, Dict[str, int]]
    proceeder_rank = mpi_rank + 1
    worker_counter = 0
    sync_value = 0
    for storage in storages_:
        with adios2.open(str(cwd / storage), 'r') as reader:
            total_steps = reader.steps()
            i = 0
            for step in reader:
                if i < storages_[storage]["begin"]:
                    i += 1
                    continue
                arr = step.read('atoms')
                arr = arr[:, 2:5].astype(dtype=np.float32)
                tpl = (worker_counter + ino, mpi_rank, arr)

                mpi_comm.send(obj=tpl, dest=proceeder_rank, tag=MPI_TAGS.DATA)
                worker_counter += 1
                mpi_comm.send(obj=worker_counter, dest=0, tag=MPI_TAGS.SERVICE)

                if i == storages_[storage]["end"] - 1:
                    break
                i += 1
                while mpi_comm.iprobe(source=proceeder_rank, tag=MPI_TAGS.SERVICE):
                    sync_value = mpi_comm.recv(source=proceeder_rank, tag=MPI_TAGS.SERVICE)  # type: int
                while worker_counter - sync_value > 50:
                    time.sleep(0.5)

                if step.current_step() == total_steps - 1:
                    break

    mpi_comm.send(obj=1, dest=proceeder_rank, tag=MPI_TAGS.SERVICE)
    print(f"MPI rank {mpi_rank}, reader finished")
    return 0


def bearbeit(cwd: Path, storages: Dict[str, int]) -> Tuple[int, np.ndarray]:
    storage = list(storages.keys())[0]
    adin = adios2.open(str(cwd / storage), 'r')

    N = int(adin.read('natoms'))  # type: int
    Lx = adin.read('boxxhi')  # type: float
    Ly = adin.read('boxyhi')  # type: float
    Lz = adin.read('boxzhi')  # type: float

    adin.close()

    bdims = np.array([Lx, Ly, Lz])
    print("Box volume is: ", np.prod(bdims))
    print("N atoms: ", N)
    son = {'N': N, "Volume": np.prod(bdims)}
    with open(cwd / data_file, 'w') as fp:
        json.dump(son, fp)
    return (N, bdims)


def storage_check(cwd: Path, storages: Dict[str, int]):
    for storage, end_step in storages.items():
        with adios2.open(str(cwd / storage), 'r') as reader:
            total_steps = reader.steps()
            if end_step > total_steps:
                raise RuntimeError(
                    f"End step {end_step} in {storage} is bigger that total step count ({total_steps})")


def storage_rsolve(cwd: Path, storages: Dict[str, Union[int, str]]) -> Dict[str, int]:
    for storage, end_step in storages.items():
        if end_step == "full":
            with adios2.open(str(cwd / storage), 'r') as reader_c:
                storages[storage] = reader_c.steps()
    return storages


def distribute(storages: Dict[str, int], mm: int) -> Dict[str, Dict[str, Union[int, Dict[str, int]]]]:
    ll = sum(list(storages.values()))
    dp = np.linspace(0, ll - 1, mm + 1, dtype=int)
    bp = dp
    dp = dp[1:] - dp[:-1]
    dp = np.vstack([bp[:-1].astype(dtype=int),
                    np.cumsum(dp).astype(dtype=int)])
    wd = {}
    st = {}
    for storage, value in storages.items():
        st[storage] = value
    ls = 0
    b_r = 0
    for i, (begin_, end_) in enumerate(dp.T):
        begin = int(begin_)
        end = int(end_)
        beg = begin - b_r + ls
        en = end - begin
        wd[str(i)] = {"no": begin, "storages": {}}
        # wd[str(i)]["storages"] = {}
        for storage in list(st):
            value = st[storage]
            if en >= value:
                wd[str(i)]["storages"][storage] = {
                    "begin": beg, "end": value + ls}
                if ls != 0:
                    beg -= ls
                    ls = 0
                b_r += value
                en -= value
                del st[storage]
            elif en < value:
                wd[str(i)]["storages"][storage] = {
                    "begin": beg, "end": en + ls}
                b_r += en
                st[storage] -= en
                ls = en
                break
    return wd


def main(cwd: Path, mpi_comm: MPIComm, mpi_rank: int, mpi_size: int, nv: int):
    print("Started at ", datetime.now().strftime("%d.%m.%Y %H:%M:%S"))

    parser = argparse.ArgumentParser(description='Process some floats.')
    parser.add_argument('--debug', action='store_true',
                        help='Debug, prints only parsed arguments')
    parser.add_argument('-o', '--outfile', type=str, default="rdata.csv",
                        help='File in which save obtained data')
    parser.add_argument('-k', '--kmax', metavar='kmax', type=int, nargs='?', default=10,
                        action='store', help='kmax')
    parser.add_argument('-g', '--critical_size', metavar='g', type=int, nargs='?', default=19,
                        action='store', help='Critical size')
    parser.add_argument('-t', '--timestep', metavar='ts', type=float, nargs='?', default=0.005,
                        action='store', help='Timestep')
    parser.add_argument('-s', '--dis', metavar='dis', type=int, nargs='?', default=1000,
                        action='store', help='Time between dump records')
    parser.add_argument('storages', type=str,
                        help='Folder in which search for .bp files')
    args = parser.parse_args()

    if args.debug:
        print("Envolved args:")
        print(args)
    else:
        storages = json.loads(args.storages)  # type: dict
        storages = storage_rsolve(cwd, storages)
        storage_check(cwd, storages)
        thread_num = floor((mpi_size - nv) / 3)
        print(f"Thread num: {thread_num}")
        thread_len = 3
        wd = distribute(storages, thread_num)
        print("Distribution")
        print(json.dumps(wd, indent=4))

        for i in range(thread_num):
            for j in range(thread_len):
                mpi_comm.send(obj=Role(j), dest=thread_len*i + j + nv, tag=MPI_TAGS.DISTRIBUTION)

        for i in range(thread_len * thread_num + nv, mpi_size):
            mpi_comm.send(obj=Role.kill, dest=i, tag=MPI_TAGS.DISTRIBUTION)
        mpi_comm.send(obj=[nv + 1 + thread_len*i for i in range(thread_num)], dest=2, tag=MPI_TAGS.TO_ACCEPT)
        mpi_comm.send(obj=[nv + 2 + thread_len*i for i in range(thread_num)], dest=1, tag=MPI_TAGS.TO_ACCEPT)

        # for i in range(thread_num):
        #     for j in range(3):
        #         if j == 0:
        #             sd = nv + i + j + 1
        #         elif j == 1:
        #             sd = (nv + i + j - 1, nv + i + j + 1)
        #         elif j == 2:
        #             sd = nv + i + j - 1
        #         mpi_comm.send(obj=sd, dest=i + j + nv,
        #                       tag=MPI_TAGS.NEIGHBORS)

        N, bdims = bearbeit(cwd, storages)

        for i in range(thread_num):
            dasdict = wd[str(i)]
            sn = int(dasdict["no"])
            store = dasdict["storages"]
            mpi_comm.send(obj=(sn, store), dest=nv + thread_len * i, tag=MPI_TAGS.SERV_DATA)
            mpi_comm.send(obj=(N, bdims), dest=nv + thread_len * i + 1, tag=MPI_TAGS.SERV_DATA)
            tpl = (cwd, N, bdims, args.kmax, args.critical_size, args.timestep, args.dis)
            mpi_comm.send(obj=tpl, dest=nv + thread_len * i + 2, tag=MPI_TAGS.SERV_DATA)

        mpi_comm.Barrier()
        print(f"MPI rank {mpi_rank}, barrier off")
        states = {}

        response_array = []
        while True:
            for i in range(1, mpi_size):
                if mpi_comm.iprobe(source=i, tag=MPI_TAGS.ONLINE):
                    resp = mpi_comm.recv(source=i, tag=MPI_TAGS.ONLINE)
                    print(f"Recieved from {i}: {resp}")
                    states[str(i)] = {}
                    states[str(i)]['name'] = resp
                    response_array.append((i, resp))
            if len(response_array) == mpi_size - 1:
                break

        # response_array = mpi_comm.gather(None)  # type: List[Tuple[int, str]]
        # print("Gathered")
        # for i in range(1, len(response_array)):
        #     print(f"MPI rank {response_array[i][0]} --- {response_array[i][1]}")
        #     states[str(i)] = {}
        #     states[str(i)]['name'] = response_array[i][1]

        mpi_comm.Barrier()
        while True:
            for i in range(1, mpi_size):
                if mpi_comm.iprobe(source=i, tag=MPI_TAGS.SERVICE):
                    states[str(i)]['state'] = mpi_comm.recv(source=i, tag=MPI_TAGS.SERVICE)
            with open(cwd / "st.json", "w") as fp:
                json.dump(states, fp)


def mpi_goto(cwd: Path, mpi_comm: MPIComm, mpi_rank, mpi_size):
    mrole = mpi_comm.recv(source=0, tag=MPI_TAGS.DISTRIBUTION)  # type: Role
    if mrole == Role.reader:
        # mpi_comm.gather((mpi_rank, "reader"))
        mpi_comm.send(obj="reader", dest=0, tag=MPI_TAGS.ONLINE)
        return reader(cwd, mpi_comm, mpi_rank, mpi_size)
    elif mrole == Role.proceeder:
        # mpi_comm.gather((mpi_rank, "proceeder"))
        mpi_comm.send(obj="proceeder", dest=0, tag=MPI_TAGS.ONLINE)
        return fd.proceed(mpi_comm, mpi_rank, mpi_size)
    elif mrole == Role.treater:
        # mpi_comm.gather((mpi_rank, "treater"))
        mpi_comm.send(obj="treater", dest=0, tag=MPI_TAGS.ONLINE)
        return treat.treat_mpi(mpi_comm, mpi_rank, mpi_size)
    elif mrole == Role.kill:
        # mpi_comm.gather((mpi_rank, "killed"))
        mpi_comm.send(obj="killed", dest=0, tag=MPI_TAGS.ONLINE)
        mpi_comm.Barrier()
        return 0


def mpi_root(cwd: Path, mpi_comm: MPIComm, mpi_rank, mpi_size):
    ret = MW.root_sanity(mpi_comm, no_print=True)
    if ret != 0:
        raise MPISanityError("MPI root sanity doesn't passed")
        return ret
    else:
        # print(f"MPI rank {mpi_rank} - root MPI thread, sanity pass, running main")
        print("Sanity: \U0001F7E2")
        return main(cwd, mpi_comm, mpi_rank, mpi_size, 3)


def mpi_nonroot(cwd: Path, mpi_comm: MPIComm, mpi_rank, mpi_size):
    ret = MW.nonroot_sanity(mpi_comm)
    mpi_comm.Barrier()
    if ret != 0:
        raise MPISanityError(f"MPI nonroot sanity doesn't passed, rank {mpi_rank}")
        return ret
    elif mpi_rank == 1:
        # mpi_comm.gather((mpi_rank, "csvWriter"))
        mpi_comm.send(obj="csvWriter", dest=0, tag=MPI_TAGS.ONLINE)
        return MW.csvWriter(cwd / "rdata.csv", mpi_comm, mpi_rank, mpi_size)
    elif mpi_rank == 2:
        # mpi_comm.gather((mpi_rank, "ad_mpi_writer"))
        mpi_comm.send(obj="ad_mpi_writer", dest=0, tag=MPI_TAGS.ONLINE)
        return MW.ad_mpi_writer(cwd / "ntb.bp", mpi_comm, mpi_rank, mpi_size)
    else:
        return mpi_goto(cwd, mpi_comm, mpi_rank, mpi_size)


def mpi_wrap():
    mpi_comm = MPI.COMM_WORLD
    mpi_rank = mpi_comm.Get_rank()
    mpi_size = mpi_comm.Get_size()

    ret = MW.base_sanity(mpi_size, mpi_rank, 6)
    if ret != 0:
        raise MPISanityError(f"MPI base sanity doesn't passed, mpi rank {mpi_rank}")
        return ret

    cwd = Path.cwd()

    if mpi_rank == 0:
        return mpi_root(cwd, mpi_comm, mpi_rank, mpi_size)
    else:
        return mpi_nonroot(cwd, mpi_comm, mpi_rank, mpi_size)


if __name__ == "__main__":
    import sys
    sys.exit(mpi_wrap())

else:
    raise ImportError("Cannot be imported")
