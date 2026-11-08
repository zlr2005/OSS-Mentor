"""Receive bounded compressed public evidence on stdin into a fixed D-drive folder."""
import argparse
import base64
import ctypes
import gzip
import hashlib
import json
from pathlib import Path
import sys
import uuid
import zlib


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    if not args.directory.is_dir():
        parser.error('Expected existing destination directory')
    console=ctypes.windll.kernel32
    handle=console.GetStdHandle(-10)
    mode=ctypes.c_uint()
    got_mode=console.GetConsoleMode(handle,ctypes.byref(mode))
    if got_mode:
        console.SetConsoleMode(handle,(mode.value | 2) & ~4)
    print('READY: compressed public verification batches; no network; local files only',flush=True)
    try:
        chunks=[]
        size=0
        while True:
            line=sys.stdin.readline(3001)
            if not line or line.strip()=='quit':
                break
            if not line.strip():
                continue
            if not line.endswith('\n') or len(line)>3000:
                raise ValueError('Oversized or unfinished transfer')
            value=line.strip()
            if value!='commit':
                if any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in value):
                    raise ValueError('Invalid transport fragment')
                chunks.append(value)
                size+=len(value)
                if size>20_000_000:
                    raise ValueError('Oversized transfer')
                continue
            decoder=zlib.decompressobj(-15)
            raw=decoder.decompress(base64.b64decode(''.join(chunks),validate=True),50_000_001)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(raw)>50_000_000:
                raise ValueError('Incomplete or oversized compressed evidence')
            records=json.loads(raw)
            if not isinstance(records,list) or not 0<len(records)<=2000:
                raise ValueError('Expected bounded record array')
            target=args.directory/('compressed_stdin_'+uuid.uuid4().hex+'.json.gz')
            with target.open('xb') as stream:
                stream.write(gzip.compress(raw))
            print(json.dumps(dict(saved=len(records),file=target.name,sha256=hashlib.sha256(raw).hexdigest())),flush=True)
            chunks=[]
            size=0
    finally:
        if got_mode:
            console.SetConsoleMode(handle,mode.value)


if __name__=='__main__':
    main()
