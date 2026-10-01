"""Same static import evidence with less expression traversal, no cached inputs."""
import ast
from pathlib import Path
import pytest
from betting_engine import release_coordinator as release
from test_release_coordinator import project,SOURCE,TEST

SOURCES=[
 'import betting_engine.one as renamed, test_two\nfrom .three import four\nfrom tests import test_five\n',
 'if condition:\n import betting_engine.one\nelse:\n from tests import test_two\n',
 'for item in items:\n import test_one\nelse:\n import test_two\nwhile condition:\n from . import three\nelse:\n from ..four import five\n',
 'try:\n import test_one\nexcept ValueError:\n import test_two\nelse:\n import test_three\nfinally:\n import test_four\n',
 'try:\n import test_one\nexcept* ValueError:\n import test_two\nfinally:\n import test_three\n',
 '@decorate("import fake")\nclass Outer(Base):\n from . import inner\n def method(self, arg: Type = default()):\n  import test_method\n  def nested():\n   from tests import test_nested\n',
 'async def run():\n async with context():\n  import test_context\n async for item in source():\n  from .items import value\n else:\n  import test_else\n',
 'with context() as value:\n import test_with\nmatch value:\n case {"key": result} if guard(result):\n  from tests import test_match\n case _:\n  import test_default\n',
 'if TYPE_CHECKING:\n from betting_engine.module import value\n',
 'x = ["import fake", {"from .fake import nothing": 1}, lambda: __import__("dynamic")]\ny = [__import__(name) for name in names]\nf = f"{__import__(name)}"\nimport test_real\n',
 'def recursive():\n try:\n  class Nested:\n   def method(self):\n    match value:\n     case 1:\n      import test_deep\n except Exception:\n  import test_exception\n',
 'from . import *\nfrom ....package import thing\nfrom external import ignored\n',
]


def signature(nodes):
 return [ast.dump(n,include_attributes=True) for n in nodes if isinstance(n,(ast.Import,ast.ImportFrom))]

@pytest.mark.parametrize('source',SOURCES)
def test_every_statement_container_retains_exact_import_nodes_and_order(source):
 tree=ast.parse(source)
 assert signature(release._static_import_nodes(tree))==signature(ast.walk(tree))


def test_unknown_nonexpression_wrapper_remains_traversable():
 class FutureWrapper(ast.AST):
  _fields=('body',)
 wrapper=FutureWrapper();wrapper.body=[ast.parse('import test_future').body[0]]
 tree=ast.Module(body=[ast.parse('import test_first').body[0],wrapper],type_ignores=[])
 assert signature(release._static_import_nodes(tree))==signature(ast.walk(tree))


def test_full_parser_still_rejects_invalid_expression_syntax_before_inspection(tmp_path):
 root,_,rid=project(tmp_path,{SOURCE:'from .one import VALUE\n',TEST:'def test_new(): assert True\n'})
 receipt=release._read_acceptance(root/'data/development',rid);cache={}
 path=root/'data/development/runs'/rid/'accepted-files'/SOURCE
 for invalid in ['x = [1,\nimport betting_engine.hidden\n','x = "unterminated\n','import betting_engine.\n']:
  path.write_text(invalid)
  assert release.utility_prerequisites(root,receipt,peer_cache=cache)==[]
  with pytest.raises((ValueError,TypeError,KeyError)):
   release._read_acceptance(root/'data/development',rid)


def test_one_request_peer_cache_never_reuses_old_source_imports(tmp_path):
 root,_,rid=project(tmp_path,{SOURCE:'from .first import VALUE\n',TEST:'def test_new(): assert True\n'})
 receipt=release._read_acceptance(root/'data/development',rid);cache={}
 path=root/'data/development/runs'/rid/'accepted-files'/SOURCE
 one=release.utility_prerequisites(root,receipt,peer_cache=cache)
 path.write_text('from .later import VALUE\n')
 two=release.utility_prerequisites(root,receipt,peer_cache=cache)
 assert [r['module'] for r in one]==['betting_engine.first']
 assert [r['module'] for r in two]==['betting_engine.later']
 path.write_text('broken = [\n')
 assert release.utility_prerequisites(root,receipt,peer_cache=cache)==[]
 path.write_text('from .restored import VALUE\n')
 assert [r['module'] for r in release.utility_prerequisites(root,receipt,peer_cache=cache)]==['betting_engine.restored']


def test_same_request_current_availability_is_recomputed(tmp_path):
 root,_,rid=project(tmp_path,{SOURCE:'from .peer import VALUE\n',TEST:'def test_new(): assert True\n'})
 receipt=release._read_acceptance(root/'data/development',rid);cache={}
 assert release.utility_prerequisites(root,receipt,current={},peer_cache=cache)
 assert release.utility_prerequisites(root,receipt,current={'src/betting_engine/peer.py':'a'*64},peer_cache=cache)==[]
 assert release.utility_prerequisites(root,receipt,current={'src/betting_engine/peer.py':'SYMLINK'},peer_cache=cache)


def test_relative_imports_keep_source_scope_and_missing_records(tmp_path):
 extra='src/betting_engine/package/tool.py'
 body='from . import neighbor\nfrom ..top import value\nfrom tests import test_shared\n'
 root,_,rid=project(tmp_path,{SOURCE:'VALUE = 2\n',extra:body,TEST:'from test_helper import value\n'})
 receipt=release._read_acceptance(root/'data/development',rid)
 rows=release.utility_prerequisites(root,receipt,current={})
 assert [(r['source'],r['line'],r['module'],r['path'],r['producers']) for r in rows]==[
  (extra,1,'betting_engine.package','src/betting_engine/package.py',[]),
  (extra,2,'betting_engine.top','src/betting_engine/top.py',[]),
  (extra,3,'tests.test_shared','tests/test_shared.py',[]),
  (TEST,1,'test_helper','tests/test_helper.py',[])]


def test_large_expression_subtree_is_not_walked_but_all_imports_are_preserved(monkeypatch):
 tree=ast.parse('data = '+repr(list(range(4000)))+'\nif condition:\n import test_kept\n')
 original=ast.iter_child_nodes;calls=[]
 def track(node):calls.append(type(node).__name__);return original(node)
 monkeypatch.setattr(ast,'iter_child_nodes',track)
 expected=signature(ast.walk(tree));full_count=len(calls);calls.clear()
 actual=signature(release._static_import_nodes(tree))
 assert actual==expected and len(actual)==1
 assert len(calls)<full_count/100
 assert 'List' not in calls and 'Constant' not in calls


def test_coordinator_source_imports_match_full_traversal():
 tree=ast.parse(Path(release.__file__).read_bytes())
 assert signature(release._static_import_nodes(tree))==signature(ast.walk(tree))
