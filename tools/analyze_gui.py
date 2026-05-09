"""Run with Python 3.13. Loads gui.pyc as a marshal'd code object,
disassembles it, and emits a structured analysis (functions, classes,
constants, names) suitable for reconstructing the source."""
import marshal, dis, json, sys, types, io, os

from _paths import EXTRACTED_DIR

GUI_PYC = os.path.join(EXTRACTED_DIR, 'gui.pyc')
OUT_DIR = EXTRACTED_DIR

def safe_repr(x, maxlen=2000):
    try:
        s = repr(x)
    except Exception as e:
        s = '<repr-failed: ' + repr(e) + '>'
    return s[:maxlen]

def walk_code(code, path=''):
    """Yield (qualified_name, code_object) for code and all nested codes."""
    qname = (path + '.' if path else '') + getattr(code, 'co_qualname', code.co_name)
    yield qname, code
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            yield from walk_code(c, qname)

def code_summary(code, qname):
    return {
        'qualname': qname,
        'name': code.co_name,
        'firstlineno': code.co_firstlineno,
        'argcount': code.co_argcount,
        'posonlyargcount': code.co_posonlyargcount,
        'kwonlyargcount': code.co_kwonlyargcount,
        'flags': code.co_flags,
        'stacksize': code.co_stacksize,
        'varnames': list(code.co_varnames),
        'cellvars': list(code.co_cellvars),
        'freevars': list(code.co_freevars),
        'names': list(code.co_names),
        'consts': [safe_repr(c, 1500) if not isinstance(c, types.CodeType) else f'<code {c.co_name}>' for c in code.co_consts],
        'co_code_len': len(code.co_code),
    }

def disassemble_to_string(code):
    buf = io.StringIO()
    try:
        dis.dis(code, file=buf, depth=0)  # only this code, not nested
    except Exception as e:
        buf.write('<dis failed: ' + repr(e) + '>')
    return buf.getvalue()

with open(GUI_PYC, 'rb') as f:
    raw = f.read()
print(f'gui.pyc: {len(raw)} bytes')
top = marshal.loads(raw)
print(f'top code: name={top.co_name!r} qualname={top.co_qualname!r} flags=0x{top.co_flags:x}')
print(f'top consts ({len(top.co_consts)}):')
for i, c in enumerate(top.co_consts[:20]):
    print(f'  [{i}] {type(c).__name__}: {safe_repr(c, 200)}')

# Walk every nested code object
all_codes = list(walk_code(top))
print(f'\ntotal code objects (incl. nested): {len(all_codes)}')

# Write a JSON summary
summary = {
    'top': code_summary(top, top.co_qualname),
    'codes': [code_summary(co, qn) for qn, co in all_codes],
}
with open(OUT_DIR + r'\gui_summary.json', 'w', encoding='utf-8') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
print(f'wrote gui_summary.json ({len(summary["codes"])} codes)')

# Write a full disassembly
with open(OUT_DIR + r'\gui_disasm.txt', 'w', encoding='utf-8') as f:
    for qn, co in all_codes:
        f.write('=' * 80 + '\n')
        f.write(f'CODE OBJECT: {qn}\n')
        f.write(f'  filename: {co.co_filename}\n')
        f.write(f'  firstline: {co.co_firstlineno}\n')
        f.write(f'  argcount: {co.co_argcount}  pos_only: {co.co_posonlyargcount}  kw_only: {co.co_kwonlyargcount}\n')
        f.write(f'  flags: 0x{co.co_flags:x}\n')
        f.write(f'  varnames: {list(co.co_varnames)}\n')
        f.write(f'  freevars: {list(co.co_freevars)}\n')
        f.write(f'  cellvars: {list(co.co_cellvars)}\n')
        f.write(f'  names:    {list(co.co_names)}\n')
        f.write(f'  consts ({len(co.co_consts)}):\n')
        for i, c in enumerate(co.co_consts):
            tag = f'<code {c.co_name}>' if isinstance(c, types.CodeType) else safe_repr(c, 800)
            f.write(f'    [{i}] {tag}\n')
        f.write('  --- bytecode ---\n')
        f.write(disassemble_to_string(co))
        f.write('\n')
print('wrote gui_disasm.txt')

# Also marshal-load and try with vote.py if reachable - but vote.py is the source-form pyarmor stub, skip
