"""Read vote.py, evaluate the bytes literal, and probe its structure
for any plaintext leakage. Pyarmor 9.x trial sometimes leaves frame
metadata (function names, file path) in cleartext between the header
and the encrypted body."""
import re, ast, struct, collections

VOTE = r'd:\Arbeit\MessageForVote\StarRailVote\_internal\vote.py'
GUI  = r'd:\Arbeit\MessageForVote\extracted\gui.pyc'

def get_blob_from_vote_py(path):
    text = open(path, encoding='utf-8').read()
    # find the b'...' literal inside __pyarmor__(...)
    start = text.find("b'PY000000")
    assert start >= 0, "blob start not found"
    # the literal ends at the last "')" before EOF
    end = text.rfind("')")
    assert end > start, "blob end not found"
    literal = text[start:end + 1]
    return ast.literal_eval(literal)


def get_blob_from_gui_pyc(path):
    import marshal
    code = marshal.loads(open(path, 'rb').read())
    for c in code.co_consts:
        if isinstance(c, bytes) and c.startswith(b'PY000000'):
            return c
    raise SystemExit('blob not found in gui.pyc')


def analyze(label, blob):
    print(f'\n=== {label} ===')
    print(f'total length: {len(blob)}')
    print(f'header (first 64): {blob[:64].hex()}')
    # Decode the documented header layout (best-effort):
    # magic[8] flags[4] pyc_magic[4] flags2[4] f1[4] f2[4] f3[4] body_size[4] vers[4]
    h = blob[:40]
    parts = [
        ('magic', h[:8]),
        ('header_flags', h[8:12]),
        ('pyc_magic', h[12:16]),
        ('field0x10', h[16:20]),
        ('field0x14', h[20:24]),
        ('field0x18', h[24:28]),
        ('field0x1c', h[28:32]),
        ('body_size', struct.unpack('<I', h[32:36])[0]),
        ('vers_field', h[36:40].hex()),
    ]
    for k, v in parts:
        print(f'  {k}: {v.hex() if isinstance(v, bytes) else v}')

    # body starts at offset 40 (typical) and extends body_size bytes
    body_size = parts[7][1]
    body_start = 40
    body = blob[body_start:body_start + body_size]
    trailer = blob[body_start + body_size:]
    print(f'body len={len(body)}, trailer len={len(trailer)}')
    if trailer:
        print(f'trailer first 64: {trailer[:64].hex()}')
        # search trailer for printable strings
        ascii_runs = re.findall(rb'[\x20-\x7e]{4,}', trailer)
        if ascii_runs:
            print(f'trailer printable runs ({len(ascii_runs)}):')
            for s in ascii_runs[:30]:
                print(f'  {s!r}')

    # Search the WHOLE blob for printable runs (length >= 4)
    all_runs = re.findall(rb'[\x20-\x7e]{4,}', blob)
    print(f'\nall printable runs (>=4 chars): {len(all_runs)}')
    # filter out the obvious magic
    interesting = [s for s in all_runs if s not in (b'PY000000',)]
    # show first batch
    for s in interesting[:80]:
        print(f'  {s!r}')

    # Byte frequency to gauge entropy (encrypted data should be near-uniform)
    cnt = collections.Counter(body)
    top = cnt.most_common(8)
    print(f'\nbody top-8 byte freqs: {[(hex(b), n) for b, n in top]}')
    print(f'  unique bytes in body: {len(cnt)}/256')
    # if entropy high (252+ unique, even-ish distribution), body is encrypted/random
    avg = len(body) / 256
    skew = max(n for _, n in top) / avg if avg > 0 else 0
    print(f'  freq skew (max/avg): {skew:.2f}  (1.0 = uniform, >>2 = structure)')


for label, fn, path in [
    ('vote.py blob', get_blob_from_vote_py, VOTE),
    ('gui.pyc blob', get_blob_from_gui_pyc, GUI),
]:
    blob = fn(path)
    analyze(label, blob)
