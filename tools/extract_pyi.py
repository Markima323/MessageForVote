"""Minimal PyInstaller archive extractor for analysis purposes.
Targets the CArchive at the end of a PyInstaller-frozen exe.
Format reference: https://pyinstaller.org/en/stable/ (operating-mode/format)"""
import os, sys, struct, zlib

from _paths import BUNDLE_EXE, EXTRACTED_DIR

EXE = BUNDLE_EXE
OUT = EXTRACTED_DIR

MAGIC = b'MEI\014\013\012\013\016'
COOKIE_FMT = '!8sIIii64s'    # magic, lengthofPackage, toc, tocLen, pyver, pylibname
COOKIE_SIZE = struct.calcsize(COOKIE_FMT)

def find_cookie(data):
    # search backwards for MAGIC
    idx = data.rfind(MAGIC)
    if idx < 0:
        raise SystemExit('cookie magic not found - not a PyInstaller exe?')
    return idx

with open(EXE, 'rb') as f:
    data = f.read()

cookie_pos = find_cookie(data)
cookie = data[cookie_pos:cookie_pos + COOKIE_SIZE]
magic, lenpkg, toc, toclen, pyver, pylib = struct.unpack(COOKIE_FMT, cookie)
print(f'cookie at 0x{cookie_pos:x}  pkg_len={lenpkg}  toc_off={toc}  toc_len={toclen}  pyver={pyver}  pylib={pylib.rstrip(chr(0).encode()).decode(errors="replace")}')

# archive starts at: cookie_pos + COOKIE_SIZE - lenpkg
arch_start = cookie_pos + COOKIE_SIZE - lenpkg
print(f'archive starts at 0x{arch_start:x}')

# TOC absolute position
toc_start = arch_start + toc
toc_end = toc_start + toclen
toc_bytes = data[toc_start:toc_end]
print(f'toc bytes: {len(toc_bytes)}')

# Parse TOC entries: each entry: !iIIIBc<name>
# struct: structSize(I), entryPos(I), cmprsdSize(I), uncmprsdSize(I), cmprsFlag(B), typeCmprsd(c), name(rest)
os.makedirs(OUT, exist_ok=True)

ENTRY_HEAD_FMT = '!IIIIBc'
ENTRY_HEAD_SZ = struct.calcsize(ENTRY_HEAD_FMT)

i = 0
entries = []
while i < len(toc_bytes):
    if i + ENTRY_HEAD_SZ > len(toc_bytes):
        break
    entry_size, entry_pos, cmpr_sz, uncmpr_sz, flag, typ = struct.unpack(ENTRY_HEAD_FMT, toc_bytes[i:i + ENTRY_HEAD_SZ])
    name_bytes = toc_bytes[i + ENTRY_HEAD_SZ : i + entry_size]
    # name is null-terminated, may have padding
    name = name_bytes.split(b'\x00', 1)[0].decode(errors='replace')
    entries.append((name, entry_pos, cmpr_sz, uncmpr_sz, flag, typ))
    i += entry_size

print(f'TOC entries: {len(entries)}')
for name, ep, cs, us, fl, t in entries[:30]:
    print(f'  type={t} flag={fl} cmpr={cs:>9} uncmpr={us:>9}  {name}')
print('  ...' if len(entries) > 30 else '')

# Pull out everything we'll want for analysis: type b'm', b's', b'M' are modules/scripts; b'b' is .pyc; b'z' is PYZ archive
TYPES_OF_INTEREST = {b's', b'm', b'M', b'b', b'z'}
saved = 0
for name, ep, cs, us, fl, t in entries:
    if t not in TYPES_OF_INTEREST:
        continue
    raw = data[arch_start + ep : arch_start + ep + cs]
    if fl:
        try:
            raw = zlib.decompress(raw)
        except Exception as e:
            print(f'decompress fail for {name}: {e}')
            continue
    safe_name = name.replace('/', os.sep).replace('\\', os.sep)
    if t in (b's',):  # script
        out_path = os.path.join(OUT, safe_name + '.pyc')
    elif t in (b'm', b'M'):  # module
        out_path = os.path.join(OUT, safe_name + '.pyc')
    else:
        out_path = os.path.join(OUT, safe_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True) if os.path.dirname(out_path) else None
    with open(out_path, 'wb') as g:
        g.write(raw)
    saved += 1

print(f'saved {saved} entries to {OUT}')
