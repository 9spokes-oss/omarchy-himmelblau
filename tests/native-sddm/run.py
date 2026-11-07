#!/usr/bin/env python3
"""Compile/run synthetic native proofs. Never authenticates or starts a session."""
import argparse
from pathlib import Path
import subprocess
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--sddm-build',type=Path,required=True)
parser.add_argument('--build-directory',type=Path)
args=parser.parse_args()
build=args.build_directory or args.sddm_build/'conversation-proofs'
subprocess.run(['cmake','-S',str(Path(__file__).resolve().parent),'-B',str(build),
               '-DSDDM_SOURCE='+str(args.source.resolve()),'-DSDDM_BUILD='+str(args.sddm_build.resolve())],check=True)
subprocess.run(['cmake','--build',str(build),'-j2'],check=True)
subprocess.run(['ctest','--test-dir',str(build),'--output-on-failure'],check=True)
