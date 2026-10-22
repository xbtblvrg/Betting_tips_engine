"""Automated Codebase Static Scope & AST Integrity Test.

Ensures zero undefined global variables (NameErrors), syntax errors,
or missing symbol definitions across all production modules in src/betting_engine.
"""
import ast
import builtins
from pathlib import Path
import symtable
import pytest

BUILTINS = set(dir(builtins)) | {
    '__file__', '__name__', '__doc__', '__package__', '__path__', '__class__',
    '__spec__', '__loader__', '__builtins__', '__cached__', '__conditional_annotations__'
}

def get_engine_python_files():
    src_dir = Path("src/betting_engine")
    return sorted(list(src_dir.rglob("*.py")))

@pytest.mark.parametrize("filepath", get_engine_python_files(), ids=lambda p: p.name)
def test_module_has_zero_undefined_globals(filepath: Path):
    content = filepath.read_text(encoding="utf-8")
    
    # Verify AST parses cleanly
    tree = ast.parse(content, filename=str(filepath))
    assert tree is not None
    
    # Build symbol table
    st = symtable.symtable(content, str(filepath), "exec")
    
    # Collect top-level defined/imported symbols
    top_symbols = set()
    for sym in st.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
            top_symbols.add(sym.get_name())
            
    # Check if wildcard import is present
    has_star_import = any(
        isinstance(node, ast.ImportFrom) and any(alias.name == '*' for alias in node.names)
        for node in ast.walk(tree)
    )
    if has_star_import:
        return  # Wildcard imports inject external globals dynamically
        
    undefined = []
    def check_table(t):
        if t.get_name() == '__annotate__':
            return
        for sym in t.get_symbols():
            s_name = sym.get_name()
            if sym.is_global() and sym.is_referenced():
                if s_name not in top_symbols and s_name not in BUILTINS:
                    undefined.append((t.get_name(), s_name))
        for child in t.get_children():
            check_table(child)
            
    check_table(st)
    assert not undefined, f"Undefined variables detected in {filepath}: {undefined}"
