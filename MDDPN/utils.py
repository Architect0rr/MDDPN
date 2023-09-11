#!/usr/bin/env python3.8
# -*- coding: utf-8 -*-

# Copyright (c) 2023 Perevoshchikov Egor
#
# This software is released under the MIT License.
# https://opensource.org/licenses/MIT

# Last modified: 11-09-2023 23:34:20

import os
import shlex
import logging
import subprocess as sb


def wexec(cmd: str, logger: logging.Logger) -> str:
    logger.debug(f"Calling '{cmd}'")
    cmds = shlex.split(cmd)
    proc = sb.run(cmds, capture_output=True)
    bout = proc.stdout.decode()
    berr = proc.stderr.decode()
    if proc.returncode != 0:
        logger.error("Process returned non-zero exitcode")
        logger.error("### OUTPUT ###")
        logger.error("bout")
        logger.error("### ERROR ###")
        logger.error(berr)
        logger.error("")
        raise RuntimeError("Process returned non-zero exitcode")
    return bout


def is_exe(fpath: str, logger: logging.Logger, exit: bool = False):
    logger.debug(f"Checking: '{fpath}'")
    if not (os.path.isfile(fpath) and os.access(fpath, os.X_OK)):
        logger.debug("This is not standard file")
        if not exit:
            logger.debug("Resolving via 'which'")
            cmd = f"which {fpath}"
            cmds = shlex.split(cmd)
            proc = sb.run(cmds, capture_output=True)
            bout = proc.stdout.decode()
            # berr = proc.stderr.decode()
            if proc.returncode != 0:
                logger.debug('Process returned nonzero returncode')
                return False
            else:
                return is_exe(bout.strip(), logger.getChild('2nd'), exit=True)
        else:
            return False
    else:
        return True


if __name__ == "__main__":
    pass
