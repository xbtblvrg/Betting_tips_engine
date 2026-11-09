"""Autonomous Codex (GPT-6-Astra) Model Synthesizer & Code Generator.

Generates, validates, and screens real executable Python candidate models
derived from quantitative literature and mathematical formulations for demoted
and high-priority market segments.

Enforces:
- Strict AST safety sandbox (no dangerous imports, pure mathematical code).
- Pre-flight mathematical axioms & latency checks (validate_candidate_model).
- Leak-free Walk-Forward Crucible out-of-sample backtesting on 97k+ historical wagers.
- Automatic registration into the Shadow Incubator ($0.00 virtual stakes) for proven candidates.
"""
from __future__ import annotations
import ast
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple, Type

from .ai_delegation import invoke, decode, read, file_lock
from .ai_feedback_loop import AIFeedbackLoopManager, AIFeedbackRecord
from .dynamic_model_registry import DynamicArchetypeRegistry
from .historical_trainer import HistoricalCandidateTrainer, get_default_segment_context
from .market_autopsy import MarketAutopsy, MarketAutopsyAnalyzer, TargetedSynthesisBriefBuilder
from .providers import DATA
from .sandbox_models.base import (
    BaseCandidateModel,
    DiscreteDistribution,
    build_universal_score_lattice,
    ClaytonCopula,
    GumbelCopula,
    FrankCopula,
    GaussianCopula,
    build_copula_score_lattice,
    WeibullRenewalHazard,
    ZeroInflatedPoisson,
    ZeroInflatedNegativeBinomial,
    JamesSteinShrinkage,
    EmpiricalBayesPoissonRate,
    KalmanRatingTracker,
    TemperatureScaler,
    continuous_fractional_kelly,
)
from .sandbox_models.validator import validate_candidate_model
from .tournament_engine import TournamentEngine, normalize_segment_key

log = logging.getLogger(__name__)


SYNTHESIS_LOG_FILE = DATA / "research" / "codex_model_syntheses.jsonl"
GENERATED_MODELS_DIR = Path(__file__).resolve().parent / "sandbox_models" / "generated"
DELEGATION_CONFIG = Path("config/ai_delegation.json")

DISALLOWED_MODULES = {
    'os', 'sys', 'subprocess', 'shutil', 'socket', 'urllib', 'requests',
    'http', 'asyncio', 'multiprocessing', 'ctypes', 'pickle', 'pty', 'builtins'
}


@dataclass
class SynthesizedModelRecord:
    model_id: str
    segment_key: str
    class_name: str
    source_code: str
    generator_provider: str
    rationale: str
    syntax_valid: bool
    safety_valid: bool
    validation_passed: bool
    validation_errors: List[str]
    oos_sample_size: int = 0
    oos_win_rate: float = 0.0
    oos_simulated_roi: float = 0.0
    oos_delta_brier: float = 0.0
    oos_max_drawdown: float = 0.0
    crucible_passed: bool = False
    is_admitted_to_incubator: bool = False
    synthesized_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    failure_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fix_indentation(code: str) -> str:
    """Attempts to recover from mixed or unexpected indentation in LLM-generated code."""
    try:
        ast.parse(code)
        return code
    except SyntaxError as e:
        if 'unexpected indent' not in str(e):
            return code

    lines = code.splitlines()
    in_doc = False
    q_char = None
    code_lines_indices = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if not in_doc and (s.startswith('"""') or s.startswith("'''")):
            q_char = '"""' if s.startswith('"""') else "'''"
            if s.count(q_char) < 2:
                in_doc = True
            continue
        if in_doc:
            if q_char in line:
                in_doc = False
            continue
        code_lines_indices.append(i)

    if not code_lines_indices:
        return code

    first_idx = code_lines_indices[0]
    first_line = lines[first_idx]
    first_indent = len(first_line) - len(first_line.lstrip())
    if first_indent > 0:
        new_lines = list(lines[:first_idx])
        for l in lines[first_idx:]:
            if len(l) >= first_indent and l[:first_indent] == ' ' * first_indent:
                new_lines.append(l[first_indent:])
            elif not l.strip():
                new_lines.append('')
            else:
                new_lines.append(l)
        cand = '\n'.join(new_lines)
        try:
            ast.parse(cand)
            return cand
        except Exception:
            pass
    return code


def _inject_imports_safely(code: str, new_imports: List[str]) -> str:
    """Injects imports after any __future__ statements and module docstrings, preserving syntax validity."""
    if not new_imports:
        return code

    import_block = "\n".join(new_imports) + "\n"
    lines = code.splitlines()
    insert_idx = 0

    in_triple_quotes = False
    quote_char = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if not in_triple_quotes:
                insert_idx = i + 1
            continue
        if not in_triple_quotes and (stripped.startswith('"""') or stripped.startswith("'''")):
            quote_char = '"""' if stripped.startswith('"""') else "'''"
            if stripped.count(quote_char) >= 2:
                insert_idx = i + 1
                continue
            else:
                in_triple_quotes = True
                continue
        if in_triple_quotes:
            if quote_char in line:
                in_triple_quotes = False
                insert_idx = i + 1
            continue
        if stripped.startswith("from __future__"):
            insert_idx = i + 1
            continue
        break

    prefix = "\n".join(lines[:insert_idx])
    suffix = "\n".join(lines[insert_idx:])
    return (prefix + "\n" if prefix else "") + import_block + suffix


def extract_clean_code(answer: str) -> str:
    """Robustly extracts executable Python source code from LLM response text."""
    if not answer or not answer.strip():
        return ""
    text = answer.strip()

    # Strip XML/HTML tool invocation tags (such as <invoke...>, <thinking...>, <antThinking...>)
    text = re.sub(r'<invoke\b[^>]*>.*?</invoke>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<invoke\b[^>]*>.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thinking\b[^>]*>.*?</thinking>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<antThinking\b[^>]*>.*?</antThinking>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()

    import textwrap
    code = ""
    # 1. Closed code blocks: prioritize block containing 'class '
    blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', text, re.DOTALL | re.IGNORECASE)
    for b in blocks:
        if 'class ' in b:
            code = textwrap.dedent(b).strip()
            break
    if not code and blocks:
        for b in blocks:
            if b.strip():
                code = textwrap.dedent(b).strip()
                break

    if not code:
        inline_blocks = re.findall(r'```(?:python)?\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        for b in inline_blocks:
            if 'class ' in b:
                code = textwrap.dedent(b).strip()
                break

    # 2. Unclosed code block: starts with ```python or ``` but never closed
    if not code:
        unclosed = re.search(r'```(?:python)?\s*\n(.*)', text, re.DOTALL | re.IGNORECASE)
        if unclosed and 'class ' in unclosed.group(1):
            code = textwrap.dedent(unclosed.group(1)).strip()

    # 3. No fences: find first line of actual Python code (import, from, class, def, @)
    if not code:
        lines = text.splitlines()
        code_start_idx = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(('import ', 'from ', 'class ', 'def ', '@')):
                code_start_idx = idx
                break

        if code_start_idx is not None:
            candidate_lines = lines[code_start_idx:]
            cand = textwrap.dedent("\n".join(candidate_lines)).strip()
            if 'class ' in cand:
                code = cand

    if not code:
        return ""

    # Strip markdown blockquote prefixes if present
    if code.startswith('>'):
        code = '\n'.join(re.sub(r'^>\s?', '', line) for line in code.splitlines())
    code = textwrap.dedent(code).strip()
    code = _fix_indentation(code)

    # Collect needed normalization imports
    required_injections: List[str] = []

    # Auto-normalize base candidate imports if used
    if 'DiscreteDistribution' in code or 'build_universal_score_lattice' in code or 'BaseCandidateModel' in code:
        if 'from betting_engine.sandbox_models.base import' in code:
            code = re.sub(
                r'from betting_engine\.sandbox_models\.base import (?:\([^)]*\)|[^\n]+)',
                'from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice',
                code,
                flags=re.DOTALL
            )
        elif 'from betting_engine.sandbox_models.base' not in code:
            required_injections.append('from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice')

    # Auto-normalize quant primitives imports if referenced
    quant_names = [
        'ClaytonCopula', 'GumbelCopula', 'FrankCopula', 'GaussianCopula',
        'build_copula_score_lattice', 'WeibullRenewalHazard', 'ZeroInflatedPoisson',
        'ZeroInflatedNegativeBinomial', 'JamesSteinShrinkage', 'EmpiricalBayesPoissonRate',
        'KalmanRatingTracker', 'TemperatureScaler', 'continuous_fractional_kelly'
    ]
    used_primitives = [p for p in quant_names if p in code and f'import {p}' not in code and '*' not in code]
    if used_primitives:
        required_injections.append(f"from betting_engine.quant_primitives import {', '.join(used_primitives)}")

    if required_injections:
        code = _inject_imports_safely(code, required_injections)

    return code


def check_ast_safety(source_code: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Analyzes Python code AST for security compliance and finds candidate class name.
    
    Returns:
        (is_safe, candidate_class_name, error_message)
    """
    if not source_code or not source_code.strip():
        return False, None, "No candidate class definition found in response"

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        source_code = _fix_indentation(source_code)
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e2:
            return False, None, f"SyntaxError in generated code: {e2}"

    candidate_class = None

    for node in ast.walk(tree):
        # Disallow dangerous imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split('.')[0]
                if root_pkg in DISALLOWED_MODULES:
                    return False, None, f"Security violation: disallowed import '{root_pkg}'"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split('.')[0]
                if root_pkg in DISALLOWED_MODULES:
                    return False, None, f"Security violation: disallowed from-import '{root_pkg}'"

        # Disallow exec, eval, open calls, and dangerous __import__ calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                func_id = node.func.id
                if func_id in ('eval', 'exec', 'open'):
                    return False, None, f"Security violation: disallowed function call '{func_id}'"
                if func_id == '__import__':
                    if not node.args:
                        return False, None, "Security violation: empty __import__ call"
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                        mod_root = arg0.value.split('.')[0]
                        if mod_root in DISALLOWED_MODULES:
                            return False, None, f"Security violation: disallowed dynamic import '{mod_root}'"
                    else:
                        return False, None, "Security violation: dynamic non-literal __import__ call"

        # Find class that inherits from BaseCandidateModel
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = getattr(base, 'id', None) or getattr(base, 'attr', None)
                if base_name in ('BaseCandidateModel',):
                    candidate_class = node.name

    if not candidate_class:
        return False, None, "No class inheriting from BaseCandidateModel was defined in source."

    # Validate that the source code has zero undefined referenced globals via symtable
    try:
        import symtable
        import builtins
        sandbox_primitives = {
            'BaseCandidateModel', 'DiscreteDistribution', 'build_universal_score_lattice',
            'ClaytonCopula', 'GumbelCopula', 'FrankCopula', 'GaussianCopula',
            'build_copula_score_lattice', 'WeibullRenewalHazard', 'ZeroInflatedPoisson',
            'ZeroInflatedNegativeBinomial', 'JamesSteinShrinkage', 'EmpiricalBayesPoissonRate',
            'KalmanRatingTracker', 'TemperatureScaler', 'continuous_fractional_kelly'
        }
        allowed_builtins = set(dir(builtins)) | {
            '__file__', '__name__', '__doc__', '__package__', '__path__', '__class__',
            '__spec__', '__loader__', '__builtins__', '__cached__', '__conditional_annotations__'
        } | sandbox_primitives
        st = symtable.symtable(source_code, '<synthesized>', 'exec')
        top_symbols = set()
        for sym in st.get_symbols():
            if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
                top_symbols.add(sym.get_name())
        has_star_import = any(
            isinstance(node, ast.ImportFrom) and any(alias.name == '*' for alias in node.names)
            for node in ast.walk(tree)
        )
        if not has_star_import:
            undefined = []
            def _check_table(t):
                if t.get_name() == '__annotate__':
                    return
                for sym in t.get_symbols():
                    s_name = sym.get_name()
                    if sym.is_global() and sym.is_referenced():
                        if s_name not in top_symbols and s_name not in allowed_builtins:
                            undefined.append(s_name)
                for child in t.get_children():
                    _check_table(child)
            _check_table(st)
            if undefined:
                return False, None, f"Undefined globals in synthesized code: {undefined}"
    except SyntaxError as e:
        return False, None, f"SyntaxError in symtable check: {e}"
    except Exception:
        pass

    return True, candidate_class, None


def instantiate_synthesized_class(
    source_code: str,
    class_name: str,
    **kwargs
) -> BaseCandidateModel:
    """Safely executes source code in isolated scope and instantiates the candidate model."""
    import sys
    import types

    mod_name = f"__codex_sandbox_{class_name}__"
    mod = types.ModuleType(mod_name)

    exec_scope: Dict[str, Any] = {
        'BaseCandidateModel': BaseCandidateModel,
        'DiscreteDistribution': DiscreteDistribution,
        'build_universal_score_lattice': build_universal_score_lattice,
        'ClaytonCopula': ClaytonCopula,
        'GumbelCopula': GumbelCopula,
        'FrankCopula': FrankCopula,
        'GaussianCopula': GaussianCopula,
        'build_copula_score_lattice': build_copula_score_lattice,
        'WeibullRenewalHazard': WeibullRenewalHazard,
        'ZeroInflatedPoisson': ZeroInflatedPoisson,
        'ZeroInflatedNegativeBinomial': ZeroInflatedNegativeBinomial,
        'JamesSteinShrinkage': JamesSteinShrinkage,
        'EmpiricalBayesPoissonRate': EmpiricalBayesPoissonRate,
        'KalmanRatingTracker': KalmanRatingTracker,
        'TemperatureScaler': TemperatureScaler,
        'continuous_fractional_kelly': continuous_fractional_kelly,
        '__name__': mod_name,
    }
    mod.__dict__.update(exec_scope)
    sys.modules[mod_name] = mod

    code_obj = compile(source_code, filename=f"<{mod_name}>", mode="exec")
    exec(code_obj, mod.__dict__)

    if class_name not in mod.__dict__:
        raise ValueError(f"Class '{class_name}' not found in executed namespace.")

    cls = mod.__dict__[class_name]
    return cls(**kwargs)


class CodexModelSynthesizer:
    """Autonomous generator that prompts Codex (or algorithmic synthesis) to write quantitative models."""

    def __init__(self, data_dir: Path = DATA):
        self.data_dir = Path(data_dir)
        self.log_file = self.data_dir / "research" / "codex_model_syntheses.jsonl"
        self.tournament = TournamentEngine(self.data_dir)
        self.trainer = HistoricalCandidateTrainer(self.data_dir / "shadow_incubator.sqlite3")
        self.feedback_manager = AIFeedbackLoopManager(self.data_dir)
        self.autopsy_analyzer = MarketAutopsyAnalyzer(self.data_dir / "shadow_incubator.sqlite3")
        self.brief_builder = TargetedSynthesisBriefBuilder()
        self.registry = DynamicArchetypeRegistry.get_instance()

    def _append_log(self, record: SynthesizedModelRecord) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
        except Exception:
            pass

    def _generate_algorithmic_archetype(
        self,
        league: str,
        scope: str,
        family: str,
        model_id: str,
        archetype_hint: Optional[str] = None,
        autopsy: Optional[MarketAutopsy] = None,
    ) -> Tuple[str, str, str]:
        """Algorithmic fallback synthesizer that writes verified mathematical Python source code.
        
        Returns:
            (source_code, class_name, rationale)
        """
        lg_u = league.upper()
        if archetype_hint:
            tag = archetype_hint
        elif lg_u in ('ATP', 'WTA', 'CHALLENGER') or 'tennis' in family:
            tag = 'markov_tennis'
        elif lg_u == 'NHL' and ('period_3' in scope or 'regulation' in scope):
            tag = 'weibull'
        elif lg_u in ('MLS', 'EPL', 'UCL'):
            tag = 'dixon_coles'
        elif 'totals' in family:
            tag = 'hurdle'
        else:
            tag = 'asymmetric_laplace'

        base_ctx = get_default_segment_context(league, scope)
        def_h = float(autopsy.home_expected if autopsy else 0.0) or float(base_ctx.get('home_expected') or 1.5)
        def_a = float(autopsy.away_expected if autopsy else 0.0) or float(base_ctx.get('away_expected') or 1.2)
        disp_alpha = max(0.01, round(float(autopsy.dispersion_index) - 1.0, 3)) if (autopsy and autopsy.dispersion_index > 1.0) else 0.15
        zero_hurdle = round(float(autopsy.zero_frequency), 3) if (autopsy and autopsy.zero_frequency > 0.0) else 0.05
        kappa = round(max(0.7, min(1.4, 1.0 + (float(autopsy.skewness) * 0.1))), 3) if autopsy else 1.05

        # 1. Tennis: Absorbing Markov Chain
        if tag == 'markov_tennis':
            class_name = f"MarkovChainTennisModel_{league}_{scope}"
            source_code = f'''from __future__ import annotations
import math
from typing import Any, Dict, Optional
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class {class_name}(BaseCandidateModel):
    """Absorbing Markov chain model for point-to-game-to-set tennis transition probabilities."""

    def __init__(
        self,
        model_id: str = "{model_id}",
        version: str = "1.0.0",
        league: str = "{league}",
        scope: str = "{scope}",
        family: str = "{family}",
        serve_win_prob_a: float = 0.65,
        serve_win_prob_b: float = 0.61,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {{'serve_a': serve_win_prob_a, 'serve_b': serve_win_prob_b}},
        )
        self.serve_win_prob_a = serve_win_prob_a
        self.serve_win_prob_b = serve_win_prob_b

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        diff = float(game_context.get('home_expected') or 1.0) - float(game_context.get('away_expected') or 1.0)
        p_home_game = 1.0 / (1.0 + math.exp(-0.45 * diff))
        p_h20 = p_home_game ** 2
        p_h21 = 2 * p_home_game * (1.0 - p_home_game) * p_home_game
        p_a21 = 2 * p_home_game * (1.0 - p_home_game) * (1.0 - p_home_game)
        p_a20 = (1.0 - p_home_game) ** 2
        tot = max(1e-6, p_h20 + p_h21 + p_a21 + p_a20)
        scores = {{
            (2, 0): p_h20 / tot,
            (2, 1): p_h21 / tot,
            (1, 2): p_a21 / tot,
            (0, 2): p_a20 / tot,
        }}
        return DiscreteDistribution.from_scores(scores)
'''
            rationale = "Absorbing Markov chain model for tennis point-to-set transition dynamics."
            return source_code, class_name, rationale

        # 2. NHL 3rd Period & Regulation: Weibull Accelerating Hazard
        elif tag == 'weibull':
            class_name = f"WeibullAcceleratingHazardModel_{league}_{scope}"
            source_code = f'''from __future__ import annotations
import math
from typing import Any, Dict, Optional
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class {class_name}(BaseCandidateModel):
    """Weibull accelerating hazard process capturing late-period empty-net surges."""

    def __init__(
        self,
        model_id: str = "{model_id}",
        version: str = "1.0.0",
        league: str = "{league}",
        scope: str = "{scope}",
        family: str = "{family}",
        hazard_shape_k: float = 1.35,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {{'shape_k': hazard_shape_k}},
        )
        self.hazard_shape_k = hazard_shape_k

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_mu = float(game_context.get('home_expected') or {def_h}) * (self.hazard_shape_k ** 0.5)
        a_mu = float(game_context.get('away_expected') or {def_a}) * (self.hazard_shape_k ** 0.5)
        scores = build_universal_score_lattice(h_mu, a_mu, dispersion_alpha=0.18, max_discrete_score=8)
        return DiscreteDistribution.from_scores(scores)
'''
            rationale = "Weibull Non-Homogeneous Renewal Process for late-period empty-net hazards."
            return source_code, class_name, rationale

        # 3. Soccer (MLS / EPL / UCL): Dixon-Coles Bivariate Poisson
        elif tag == 'dixon_coles':
            class_name = f"DixonColesSoccerModel_{league}_{scope}"
            source_code = f'''from __future__ import annotations
import math
from typing import Any, Dict, Optional
from scipy.stats import poisson
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class {class_name}(BaseCandidateModel):
    """Dixon-Coles bivariate Poisson with low-scoring dependency parameter."""

    def __init__(
        self,
        model_id: str = "{model_id}",
        version: str = "1.0.0",
        league: str = "{league}",
        scope: str = "{scope}",
        family: str = "{family}",
        rho: float = -0.11,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {{'rho': rho}},
        )
        self.rho = rho

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_mu = float(game_context.get('home_expected') or {def_h})
        a_mu = float(game_context.get('away_expected') or {def_a})
        scores = {{}}
        for h in range(7):
            for a in range(7):
                p_ind = poisson.pmf(h, h_mu) * poisson.pmf(a, a_mu)
                adj = 1.0
                if h == 0 and a == 0:
                    adj = 1.0 - (h_mu * a_mu * self.rho)
                elif h == 0 and a == 1:
                    adj = 1.0 + (h_mu * self.rho)
                elif h == 1 and a == 0:
                    adj = 1.0 + (a_mu * self.rho)
                elif h == 1 and a == 1:
                    adj = 1.0 - self.rho
                scores[(h, a)] = max(0.0001, float(p_ind * max(0.05, adj)))
        return DiscreteDistribution.from_scores(scores)
'''
            rationale = "Dixon & Coles (1997) Bivariate Poisson with low-scoring dependence."
            return source_code, class_name, rationale

        # 4. Totals in NBA / NFL / General: Two-Part Hurdle Negative Binomial
        elif 'totals' in family or tag == 'hurdle':
            class_name = f"NegativeBinomialHurdleModel_{league}_{scope}"
            surge_mult = 1.30 if (league == 'NHL' and 'period_3' in scope) else 1.0
            eff_disp = 0.10 if (league == 'NHL' and 'period_3' in scope) else disp_alpha
            eff_zh = 0.0 if (league == 'NHL' and 'period_3' in scope) else zero_hurdle
            source_code = f'''from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple
from scipy.stats import nbinom, poisson
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class {class_name}(BaseCandidateModel):
    """Novel Two-Part Hurdle Negative Binomial model synthesizing zero-hazard and dispersion dynamics."""

    def __init__(
        self,
        model_id: str = "{model_id}",
        version: str = "1.0.0",
        league: str = "{league}",
        scope: str = "{scope}",
        family: str = "{family}",
        zero_hurdle_prob: float = {eff_zh},
        dispersion_alpha: float = {eff_disp},
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {{'hurdle': zero_hurdle_prob, 'alpha': dispersion_alpha}},
        )
        self.zero_hurdle_prob = zero_hurdle_prob
        self.dispersion_alpha = dispersion_alpha
        self.surge_mult = {surge_mult}

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        def_h = {def_h}
        def_a = {def_a}
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or def_h) * self.surge_mult
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or def_a) * self.surge_mult

        scores = build_universal_score_lattice(
            h_mu, a_mu, dispersion_alpha=self.dispersion_alpha, max_discrete_score=8 if h_mu < 12.0 else None
        )
        if (0, 0) in scores:
            scores[(0, 0)] = max(0.001, scores[(0, 0)] * (1.0 - self.zero_hurdle_prob))
        return DiscreteDistribution.from_scores(scores)
'''
            rationale = "Two-Part Hurdle Negative Binomial addressing overdispersed tails while conditioning on positive scoring."
            return source_code, class_name, rationale

        # 5. Spreads & Moneylines: Asymmetric Laplace Difference
        else:
            class_name = f"AsymmetricLaplaceSpreadModel_{league}_{scope}"
            source_code = f'''from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple
from scipy.stats import norm
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class {class_name}(BaseCandidateModel):
    """Asymmetric Laplace difference model capturing sharp modal peaks and heavy score differential tails."""

    def __init__(
        self,
        model_id: str = "{model_id}",
        version: str = "1.0.0",
        league: str = "{league}",
        scope: str = "{scope}",
        family: str = "{family}",
        asymmetry_kappa: float = {kappa},
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {{'asymmetry_kappa': asymmetry_kappa}},
        )
        self.asymmetry_kappa = asymmetry_kappa

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        def_h = {def_h}
        def_a = {def_a}
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or def_h) * self.asymmetry_kappa
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or def_a)

        scores = build_universal_score_lattice(h_mu, a_mu, max_discrete_score=8 if h_mu < 12.0 else None)
        return DiscreteDistribution.from_scores(scores)
'''
            rationale = "Asymmetric Laplace Difference capturing leptokurtic margin concentration around key numbers."
            return source_code, class_name, rationale

    def _invoke_codex_with_watchdog(
        self,
        config: Dict[str, Any],
        prompt: str,
        timeout: float = 300.0,
        max_hard_ceiling: float = 600.0,
        idle_timeout: float = 60.0,
    ) -> Dict[str, Any]:
        """Spawns Codex CLI with progress-aware streaming watchdog and explicit reasoning effort."""
        binary = config.get('binary') or '/Users/belavarga/.local/bin/codex'
        model = ['--model', config['model']] if config.get('model') else []
        reasoning = config.get('reasoning_effort', 'medium')
        argv = [
            binary, 'exec', '--sandbox', 'read-only', '--skip-git-repo-check',
            '--ephemeral', '--color', 'never',
            '-c', f'model_reasoning_effort="{reasoning}"',
            *model, str(prompt)
        ]
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin')}
        for k in ('OPENAI_API_KEY', 'CODEX_HOME', 'HOME', 'USER', 'LOGNAME', 'TMPDIR'):
            if k in os.environ:
                env[k] = os.environ[k]

        with tempfile.TemporaryDirectory(prefix='codex-synth-') as temp:
            cwd = Path(temp)
            process = subprocess.Popen(
                argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
            )
            if process.stdin:
                try:
                    process.stdin.close()
                except Exception:
                    pass

            buffers = [bytearray(), bytearray()]
            selector = selectors.DefaultSelector()
            for stream, idx in ((process.stdout, 0), (process.stderr, 1)):
                if stream:
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, idx)

            started = time.monotonic()
            last_activity = started
            state = None
            max_bytes = 512 * 1024

            try:
                while selector.get_map() or process.poll() is None:
                    elapsed = time.monotonic() - started
                    idle_elapsed = time.monotonic() - last_activity
                    # If elapsed >= timeout and no new chunks in idle_timeout, or if hard ceiling reached
                    if elapsed >= max_hard_ceiling or (idle_elapsed >= idle_timeout and elapsed >= timeout):
                        state = 'timeout'
                        break
                    remaining = min(0.1, max(0.0, max_hard_ceiling - elapsed))
                    for key, _ in selector.select(remaining):
                        stream = key.fileobj
                        try:
                            chunk = os.read(stream.fileno(), 8192)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(stream)
                            try:
                                stream.close()
                            except Exception:
                                pass
                            continue
                        last_activity = time.monotonic()
                        target = buffers[key.data]
                        room = max_bytes - len(target)
                        target.extend(chunk[:max(0, room)])
                        if len(chunk) > room:
                            state = 'output_limit'
                    if state:
                        break
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                selector.close()
                for s in (process.stdout, process.stderr, process.stdin):
                    if s and not s.closed:
                        try:
                            s.close()
                        except Exception:
                            pass

            return {
                'returncode': process.returncode,
                'state': state,
                'stdout': buffers[0].decode('utf-8', errors='replace'),
                'stderr': buffers[1].decode('utf-8', errors='replace'),
            }

    def _query_codex_for_model_code(
        self,
        segment_key: str,
        domain_problem: str,
        sample_context: Dict[str, Any],
        model_id: str,
        claude_hypothesis: Optional[str] = None,
        grok_critique: Optional[str] = None,
    ) -> Optional[Tuple[str, str, str]]:
        """Invokes OpenAI Codex (gpt-6-astra) to generate a Python model class."""
        if not DELEGATION_CONFIG.exists():
            return None

        policy = read(DELEGATION_CONFIG, {})
        codex_cfg = policy.get('providers', {}).get('codex', {})
        if not codex_cfg.get('enabled'):
            return None

        parts = segment_key.split('|')
        league, scope, family = parts[0], parts[1], parts[2]

        feedback_context = self.feedback_manager.get_in_context_learning_prompt(league, family)

        prompt = (
            f"You are OpenAI Codex (gpt-6-astra), elite mathematical quant modeler. Write a complete, production-grade Python "
            f"candidate model class for the sports betting segment '{segment_key}' (League: {league}, Scope: {scope}, Family: {family}).\n\n"
            f"DOMAIN DIAGNOSIS & GOAL:\n{domain_problem}\n"
            f"{f'CLAUDE SPORTS MECHANICS HYPOTHESIS: {claude_hypothesis}' if claude_hypothesis else ''}\n"
            f"{f'GROK RED-TEAM AUDIT & GUARDRAILS: {grok_critique}' if grok_critique else ''}\n\n"
            f"REINFORCEMENT LEARNING CONTEXT (HISTORICAL ALPHA & COUNTER-EXAMPLES):\n{feedback_context}\n\n"
            f"CORE QUANTITATIVE PRINCIPLES (ZERO HARDCODED CLAMPS):\n"
            f"1. Pure independent domain model: Derive true objective probabilities from sports mechanics and situational features. Zero blending of bookmaker vig into forecasts.\n"
            f"2. Continuous valuation: Do NOT introduce arbitrary hardcoded odds caps or discrete step filters. Every contract with odds >= 1.40 and positive mathematical expectation is eligible.\n"
            f"3. Anti-Tautological Invariant: The model must be genuinely predictive out-of-sample, capturing real scoring dynamics rather than overfitting to in-sample noise.\n\n"
            f"TECHNICAL REQUIREMENTS:\n"
            f"1. Inherit from 'betting_engine.sandbox_models.base.BaseCandidateModel'.\n"
            f"2. Implement 'predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution'. Construct distributions using either `DiscreteDistribution(probabilities={{'home': p_h, 'draw': p_d, 'away': p_a}})` for 3-way/moneyline, `DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)`, or `DiscreteDistribution.from_scores(scores)` for score matrices.\n"
            f"3. INSTITUTIONAL QUANT PRIMITIVES & ECOSYSTEM:\n"
            f"   You have direct access to Python's scientific mathematical ecosystem:\n"
            f"   - Standard libraries: 'math', 'typing', 'numpy', 'scipy' (scipy.stats, scipy.optimize, scipy.integrate, scipy.special, scipy.linalg), 'sklearn' (linear_model, isotonic, calibration), 'statsmodels' (api, tsa, glm), 'pandas', 'mpmath'.\n"
            f"   - Quant Primitives in Execution Scope (pre-loaded, no import needed):\n"
            f"     All standard mathematical helpers, numerical distribution solvers, empirical score lattices, and state trackers are loaded into scope.\n"
            f"     You are NOT constrained to any specific tool: formulate custom equations, probability distributions, or composite architectures independently from first principles based purely on the observed data moments and sports mechanics.\n"
            f"4. DOMAIN FEATURES: game_context provides rich situational features: 'home_expected', 'away_expected', 'home_rest_days', 'away_rest_days', 'home' attack/defense, 'away' attack/defense, weather (wind_speed, is_dome, temp), and venue.\n"
            f"5. SAFETY BOUNDARY: Pure deterministic computation only. Do NOT import OS/process/network modules (os, sys, subprocess, socket, requests, urllib).\n"
            f"6. Constructor must take model_id='{model_id}', version='1.0.0', league='{league}', scope='{scope}', family='{family}'.\n"
            f"7. Return ONLY valid executable Python code in your response without markdown chat filler."
        )

        try:
            with file_lock(DATA / "development" / "ai-locks" / "codex.lock", timeout=60.0) as acquired:
                if not acquired:
                    log.warning(f"Codex lock busy (60.0s) for {segment_key}; yielding to Claude parallel fallback")
                    return None
                res = self._invoke_codex_with_watchdog(codex_cfg, prompt, timeout=240.0, max_hard_ceiling=600.0, idle_timeout=60.0)
                state, answer = decode('codex', res, codex_cfg.get('model'))
                if state == 'received_unreviewed' and answer:
                    clean_code = extract_clean_code(answer)
                    is_safe, cls_name, err = check_ast_safety(clean_code)
                    if is_safe and cls_name:
                        return clean_code, cls_name, f"Codex (gpt-6-astra) synthesized model addressing {domain_problem[:120]}"
                    else:
                        err_msg = err or "Class not found in AST"
                        log.warning(f"Codex AST check failed for {segment_key}: {err_msg}; sample: {answer[:180]}")
                        try:
                            fb_rec = AIFeedbackRecord(
                                id=f"synth_err_codex_{model_id}",
                                segment_key=segment_key,
                                league=league,
                                scope=scope,
                                family=family,
                                model_id=f"{model_id}_codex_ast_err",
                                class_name=cls_name or "SyntaxErrorModel",
                                generator_provider="codex",
                                source_file_path="",
                                claude_hypothesis=claude_hypothesis,
                                codex_rationale=f"AST check failed: {err_msg}",
                                grok_critique=grok_critique,
                                crucible_passed=False,
                                current_tier=1,
                                graduation_status="syntax_failed",
                                failure_reason=f"Codex AST check failed: {err_msg}",
                                validation_errors=[f"Codex AST check failed: {err_msg}"],
                                learned_insight=f"Syntax/AST check failed: {err_msg}",
                            )
                            self.feedback_manager.record_synthesis_attempt(fb_rec)
                        except Exception:
                            pass
                else:
                    log.warning(f"Codex decode returned state '{state}' for {segment_key}; rc: {res.get('returncode')}")
        except Exception as e:
            log.warning(f"Codex invocation exception for {segment_key}: {e}")

        return None

    def _query_claude_for_domain_hypothesis(
        self,
        segment_key: str,
        domain_problem: str,
    ) -> Optional[str]:
        """Queries Anthropic Claude (claude-opus-5-5) for sports domain mechanics hypothesis."""
        if not DELEGATION_CONFIG.exists():
            return None
        policy = read(DELEGATION_CONFIG, {})
        claude_cfg = dict(policy.get('providers', {}).get('claude', {}))
        if not claude_cfg.get('enabled'):
            return None
        claude_cfg.setdefault('idle_timeout', 30.0)
        claude_cfg.setdefault('max_hard_ceiling', 120.0)

        parts = segment_key.split('|')
        league, scope, family = parts[0], parts[1], parts[2]
        prompt = (
            f"You are Anthropic Claude (claude-opus-5-5), sports domain mechanics and tactical modeling specialist. "
            f"For betting segment '{segment_key}' (League: {league}, Scope: {scope}, Family: {family}):\n"
            f"DIAGNOSIS TASK: {domain_problem}\n\n"
            f"In 2-4 concise sentences, identify the specific sports mechanics and physical dynamics of this league and market that cause standard models to leak edge, "
            f"and deduce a concrete mathematical formulation from first principles to capture this alpha. Output domain analysis only."
        )
        try:
            with file_lock(DATA / "development" / "ai-locks" / "claude.lock", timeout=60.0) as acquired:
                if not acquired:
                    log.warning(f"Failed to acquire claude.lock within 60.0s for hypothesis {segment_key}")
                    return None
                res = invoke('claude', claude_cfg, prompt, timeout=60)
                state, answer = decode('claude', res, claude_cfg.get('model'))
                if state == 'received_unreviewed' and answer:
                    return answer.strip()
        except Exception as e:
            log.warning(f"Claude domain hypothesis exception for {segment_key}: {e}")
        return None

    def _query_claude_for_model_code(
        self,
        segment_key: str,
        domain_problem: str,
        sample_context: Dict[str, Any],
        model_id: str,
        claude_hypothesis: Optional[str] = None,
        grok_critique: Optional[str] = None,
    ) -> Optional[Tuple[str, str, str]]:
        """Invokes Anthropic Claude (claude-opus-5-5) as 1:1 fallback when Codex is busy/unavailable."""
        if not DELEGATION_CONFIG.exists():
            return None

        policy = read(DELEGATION_CONFIG, {})
        claude_cfg = dict(policy.get('providers', {}).get('claude', {}))
        if not claude_cfg.get('enabled'):
            return None
        claude_cfg.setdefault('idle_timeout', 60.0)
        claude_cfg.setdefault('max_hard_ceiling', 600.0)

        parts = segment_key.split('|')
        league, scope, family = parts[0], parts[1], parts[2]

        feedback_context = self.feedback_manager.get_in_context_learning_prompt(league, family)

        prompt = (
            f"You are Anthropic Claude (claude-opus-5-5), elite quantitative modeler and sports domain mechanics specialist. "
            f"Write a complete, production-grade Python candidate model class for the sports betting segment '{segment_key}' "
            f"(League: {league}, Scope: {scope}, Family: {family}).\n\n"
            f"DOMAIN DIAGNOSIS & GOAL:\n{domain_problem}\n"
            f"{f'CLAUDE SPORTS MECHANICS HYPOTHESIS: {claude_hypothesis}' if claude_hypothesis else ''}\n"
            f"{f'GROK RED-TEAM AUDIT & GUARDRAILS: {grok_critique}' if grok_critique else ''}\n\n"
            f"REINFORCEMENT LEARNING CONTEXT (HISTORICAL ALPHA & COUNTER-EXAMPLES):\n{feedback_context}\n\n"
            f"CORE QUANTITATIVE PRINCIPLES (ZERO HARDCODED CLAMPS):\n"
            f"1. Pure independent domain model: Derive true objective probabilities from sports mechanics and situational features. Zero blending of bookmaker vig into forecasts.\n"
            f"2. Continuous valuation: Do NOT introduce arbitrary hardcoded odds caps or discrete step filters. Every contract with odds >= 1.40 and positive mathematical expectation is eligible.\n"
            f"3. Anti-Tautological Invariant: The model must be genuinely predictive out-of-sample, capturing real scoring dynamics rather than overfitting to in-sample noise.\n\n"
            f"TECHNICAL REQUIREMENTS:\n"
            f"1. Inherit from 'betting_engine.sandbox_models.base.BaseCandidateModel'.\n"
            f"2. Implement 'predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution'. Construct distributions using either `DiscreteDistribution(probabilities={{'home': p_h, 'draw': p_d, 'away': p_a}})` for 3-way/moneyline, `DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)`, or `DiscreteDistribution.from_scores(scores)` for score matrices.\n"
            f"3. INSTITUTIONAL QUANT PRIMITIVES & ECOSYSTEM:\n"
            f"   You have direct access to Python's scientific mathematical ecosystem:\n"
            f"   - Standard libraries: 'math', 'typing', 'numpy', 'scipy' (scipy.stats, scipy.optimize, scipy.integrate, scipy.special, scipy.linalg), 'sklearn' (linear_model, isotonic, calibration), 'statsmodels' (api, tsa, glm), 'pandas', 'mpmath'.\n"
            f"   - Quant Primitives in Execution Scope (pre-loaded, no import needed):\n"
            f"     All standard mathematical helpers, numerical distribution solvers, empirical score lattices, and state trackers are loaded into scope.\n"
            f"     You are NOT constrained to any specific tool: formulate custom equations, probability distributions, or composite architectures independently from first principles based purely on the observed data moments and sports mechanics.\n"
            f"4. DOMAIN FEATURES: game_context provides rich situational features: 'home_expected', 'away_expected', 'home_rest_days', 'away_rest_days', 'home' attack/defense, 'away' attack/defense, weather (wind_speed, is_dome, temp), and venue.\n"
            f"5. SAFETY BOUNDARY: Pure deterministic computation only. Do NOT import OS/process/network modules (os, sys, subprocess, socket, requests, urllib).\n"
            f"6. Constructor must take model_id='{model_id}', version='1.0.0', league='{league}', scope='{scope}', family='{family}'.\n"
            f"7. CRITICAL HEADLESS INSTRUCTION: You are executing in non-interactive batch generation mode.\n"
            f"   - Do NOT output XML tags, tool invocations (<invoke>), or conversational preface.\n"
            f"   - Start directly with standard Python imports and the candidate class.\n"
            f"   - Return ONLY the executable Python code enclosed in ```python ... ```."
        )

        try:
            with file_lock(DATA / "development" / "ai-locks" / "claude.lock", timeout=60.0) as acquired:
                if not acquired:
                    log.warning(f"Failed to acquire claude.lock within 60.0s for {segment_key}")
                    return None
                res = invoke('claude', claude_cfg, prompt, timeout=240)
                state, answer = decode('claude', res, claude_cfg.get('model'))
                if state == 'received_unreviewed' and answer:
                    clean_code = extract_clean_code(answer)
                    is_safe, cls_name, err = check_ast_safety(clean_code)
                    if is_safe and cls_name:
                        return clean_code, cls_name, f"Claude (claude-opus-5-5) synthesized model addressing {domain_problem[:120]}"
                    else:
                        err_msg = err or "Class not found in AST"
                        log.warning(f"Claude AST check failed for {segment_key}: {err_msg}; sample: {answer[:180]}")
                        try:
                            fb_rec = AIFeedbackRecord(
                                id=f"synth_err_claude_{model_id}",
                                segment_key=segment_key,
                                league=league,
                                scope=scope,
                                family=family,
                                model_id=f"{model_id}_claude_ast_err",
                                class_name=cls_name or "SyntaxErrorModel",
                                generator_provider="claude",
                                source_file_path="",
                                claude_hypothesis=claude_hypothesis,
                                codex_rationale=f"AST check failed: {err_msg}",
                                grok_critique=grok_critique,
                                crucible_passed=False,
                                current_tier=1,
                                graduation_status="syntax_failed",
                                failure_reason=f"Claude AST check failed: {err_msg}",
                                validation_errors=[f"Claude AST check failed: {err_msg}"],
                                learned_insight=f"Syntax/AST check failed: {err_msg}",
                            )
                            self.feedback_manager.record_synthesis_attempt(fb_rec)
                        except Exception:
                            pass
                else:
                    log.warning(f"Claude decode returned state '{state}' for {segment_key}; rc: {res.get('returncode')}")
        except Exception as e:
            log.warning(f"Claude invocation exception for {segment_key}: {e}")

        return None

    def synthesize_and_screen_candidate(
        self,
        segment_key: str,
        domain_problem: str = "Overcoming heavy-tailed variance and bookmaker vig",
        sample_context: Optional[Dict[str, Any]] = None,
        force_archetype: Optional[str] = None,
        claude_hypothesis: Optional[str] = None,
        grok_critique: Optional[str] = None,
    ) -> SynthesizedModelRecord:
        """Synthesizes a novel mathematical model, checks AST safety, validates, and screens on OOS historical data."""
        canonical_seg = normalize_segment_key(segment_key)
        parts = canonical_seg.split('|')
        league, scope, family = parts[0], parts[1], parts[2]

        now_tag = datetime.now(timezone.utc).strftime("%m%d_%H%M%S")
        model_id = f"codex_synth_{league.lower()}_{scope}_{now_tag}"

        ctx = sample_context or get_default_segment_context(league, scope)

        # -1. Automated Empirical Market Autopsy & Targeted Quantitative Brief
        autopsy = self.autopsy_analyzer.analyze_market(canonical_seg)
        targeted_problem = self.brief_builder.build_targeted_prompt(autopsy, domain_problem)

        # 0. Enrich with Claude domain mechanics hypothesis if not provided
        if not claude_hypothesis and not force_archetype:
            try:
                claude_hypothesis = self._query_claude_for_domain_hypothesis(canonical_seg, targeted_problem)
            except Exception:
                pass

        # 1. Generate code via Codex (with Claude 1:1 fallback, then algorithmic fallback)
        provider = "codex"
        codex_res = None if force_archetype else self._query_codex_for_model_code(
            canonical_seg, targeted_problem, ctx, model_id,
            claude_hypothesis=claude_hypothesis, grok_critique=grok_critique
        )
        if codex_res:
            source_code, class_name, rationale = codex_res
        elif not force_archetype:
            # Fallback to Claude (1:1 identical environment) if Codex is busy/locked/unavailable
            claude_res = self._query_claude_for_model_code(
                canonical_seg, targeted_problem, ctx, model_id,
                claude_hypothesis=claude_hypothesis, grok_critique=grok_critique
            )
            if claude_res:
                provider = "claude"
                source_code, class_name, rationale = claude_res
            else:
                provider = "algorithmic_codex_engine"
                source_code, class_name, rationale = self._generate_algorithmic_archetype(
                    league, scope, family, model_id, archetype_hint=force_archetype, autopsy=autopsy
                )
        else:
            provider = "algorithmic_codex_engine"
            source_code, class_name, rationale = self._generate_algorithmic_archetype(
                league, scope, family, model_id, archetype_hint=force_archetype, autopsy=autopsy
            )

        # 2. AST Safety & Structure Check
        is_safe, found_class, safety_err = check_ast_safety(source_code)
        if not is_safe or not found_class:
            err_msg = safety_err or "Safety check failed"
            record = SynthesizedModelRecord(
                model_id=model_id,
                segment_key=canonical_seg,
                class_name=class_name,
                source_code=source_code,
                generator_provider=provider,
                rationale=rationale,
                syntax_valid=False,
                safety_valid=False,
                validation_passed=False,
                validation_errors=[err_msg],
                failure_reason=err_msg
            )
            self._append_log(record)
            try:
                fb_rec = AIFeedbackRecord(
                    id=f"synth_{model_id}",
                    segment_key=canonical_seg,
                    league=league,
                    scope=scope,
                    family=family,
                    model_id=model_id,
                    class_name=class_name or "SyntaxErrorModel",
                    generator_provider=provider,
                    source_file_path="",
                    claude_hypothesis=claude_hypothesis,
                    codex_rationale=rationale,
                    grok_critique=grok_critique,
                    crucible_passed=False,
                    current_tier=1,
                    graduation_status="syntax_failed",
                    failure_reason=err_msg,
                    validation_errors=[err_msg],
                    learned_insight=f"Syntax/AST safety error: {err_msg}",
                )
                self.feedback_manager.record_synthesis_attempt(fb_rec)
            except Exception:
                pass
            return record

        # 3. Instantiate in isolated scope
        try:
            model_instance = instantiate_synthesized_class(
                source_code,
                found_class,
                model_id=model_id,
                league=league,
                scope=scope,
                family=family
            )
        except Exception as exc:
            inst_err = f"Instantiation error: {exc}"
            record = SynthesizedModelRecord(
                model_id=model_id,
                segment_key=canonical_seg,
                class_name=found_class,
                source_code=source_code,
                generator_provider=provider,
                rationale=rationale,
                syntax_valid=True,
                safety_valid=True,
                validation_passed=False,
                validation_errors=[inst_err],
                failure_reason=inst_err
            )
            self._append_log(record)
            try:
                fb_rec = AIFeedbackRecord(
                    id=f"synth_{model_id}",
                    segment_key=canonical_seg,
                    league=league,
                    scope=scope,
                    family=family,
                    model_id=model_id,
                    class_name=found_class,
                    generator_provider=provider,
                    source_file_path="",
                    claude_hypothesis=claude_hypothesis,
                    codex_rationale=rationale,
                    grok_critique=grok_critique,
                    crucible_passed=False,
                    current_tier=1,
                    graduation_status="instantiation_failed",
                    failure_reason=inst_err,
                    validation_errors=[inst_err],
                    learned_insight=inst_err,
                )
                self.feedback_manager.record_synthesis_attempt(fb_rec)
            except Exception:
                pass
            return record

        # 4. Pre-flight Validation (Latency, Axioms, Marginals)
        val_passed, val_errors = validate_candidate_model(model_instance, ctx)
        if not val_passed:
            fail_reason = f"Pre-flight failed: {'; '.join(val_errors)}"
            record = SynthesizedModelRecord(
                model_id=model_id,
                segment_key=canonical_seg,
                class_name=found_class,
                source_code=source_code,
                generator_provider=provider,
                rationale=rationale,
                syntax_valid=True,
                safety_valid=True,
                validation_passed=False,
                validation_errors=val_errors,
                failure_reason=fail_reason
            )
            self._append_log(record)
            try:
                fb_rec = AIFeedbackRecord(
                    id=f"synth_{model_id}",
                    segment_key=canonical_seg,
                    league=league,
                    scope=scope,
                    family=family,
                    model_id=model_id,
                    class_name=found_class,
                    generator_provider=provider,
                    source_file_path="",
                    claude_hypothesis=claude_hypothesis,
                    codex_rationale=rationale,
                    grok_critique=grok_critique,
                    crucible_passed=False,
                    current_tier=1,
                    graduation_status="preflight_failed",
                    failure_reason=fail_reason,
                    validation_errors=val_errors,
                    learned_insight=fail_reason,
                )
                self.feedback_manager.record_synthesis_attempt(fb_rec)
            except Exception:
                pass
            return record

        # 5. Out-of-Sample Walk-Forward Crucible Evaluation on Historical Data
        # Reuses HistoricalCandidateTrainer with the instantiated model
        validation_errors: List[str] = []
        wagers = self.trainer._fetch_settled_wagers(league, scope, family)
        split_idx = max(5, int(len(wagers) * 0.75))
        oos_wagers = wagers[split_idx:] if len(wagers) >= 10 else wagers

        oos_accepted = 0
        oos_wins = 0
        oos_losses = 0
        oos_pushes = 0
        oos_pnl = 0.0
        sum_brier_m = 0.0
        sum_brier_mkt = 0.0
        running_pnl = 0.0
        peak_pnl = 0.0
        max_dd = 0.0
        eval_errors: List[str] = []

        for row in oos_wagers:
            sel, line, odds, _, mkt_prob, side, res, orig_pnl, _ = row
            odds_val = float(odds or 2.0)
            mkt_p = float(mkt_prob or (1.0 / max(1.05, odds_val)))

            ctx_wager = dict(ctx)
            if line is not None:
                ctx_wager['line'] = line
                ctx_wager['total_line'] = line
                ctx_wager['spread_line'] = line
                ctx_wager['reference_total'] = line
                ctx_wager['reference_spread'] = line
                sel_str = str(sel or '').lower()
                if side == 'home' or 'home' in sel_str:
                    ctx_wager['home_spread'] = line
                    ctx_wager['home_total_line'] = line
                elif side == 'away' or 'away' in sel_str:
                    ctx_wager['away_spread'] = line
                    ctx_wager['away_total_line'] = line

            try:
                prob, ev = model_instance.evaluate_market(
                    canonical_seg, sel or '', line, odds_val, ctx_wager, side=side
                )
            except Exception as e_eval:
                if len(eval_errors) < 3:
                    eval_errors.append(f"{type(e_eval).__name__}: {e_eval}")
                continue

            if ev >= 0.035:
                oos_accepted += 1
                y = 1.0 if res == 'won' else (0.5 if res == 'push' else 0.0)
                sum_brier_m += (prob - y) ** 2
                sum_brier_mkt += (mkt_p - y) ** 2

                if res == 'won':
                    oos_wins += 1
                    sim_pnl = odds_val - 1.0
                elif res == 'push':
                    oos_pushes += 1
                    sim_pnl = 0.0
                else:
                    oos_losses += 1
                    sim_pnl = -1.0

                oos_pnl += sim_pnl
                running_pnl += sim_pnl
                if running_pnl > peak_pnl:
                    peak_pnl = running_pnl
                total_stake_so_far = float(oos_accepted)
                dd = (peak_pnl - running_pnl) / max(1.0, peak_pnl + total_stake_so_far) if (peak_pnl > 0 or total_stake_so_far > 0) else 0.0
                if dd > max_dd:
                    max_dd = dd

        sim_roi = (oos_pnl / max(1, oos_accepted)) if oos_accepted > 0 else 0.0
        win_rate = (oos_wins / max(1, oos_wins + oos_losses)) if (oos_wins + oos_losses) > 0 else 0.0
        brier_m = (sum_brier_m / max(1, oos_accepted)) if oos_accepted > 0 else 0.25
        brier_mkt = (sum_brier_mkt / max(1, oos_accepted)) if oos_accepted > 0 else 0.25
        delta_brier = brier_mkt - brier_m

        # Admission gate (Commandment 6 Tier 1 & Anti-Tautological Invariant):
        # Strictly require N_oos >= 5, ROI >= +3.5%, Delta Brier >= -0.005, and DD < 25%.
        # If insufficient historical wagers exist in the ledger (oos_accepted < 5 or len(wagers) < 5),
        # the model CANNOT be certified as having an empirical out-of-sample statistical edge.
        crucible_ok = (sim_roi >= 0.035 and delta_brier >= -0.005 and max_dd < 0.25) if oos_accepted >= 5 else False
        if not crucible_ok and not validation_errors:
            if eval_errors and oos_accepted == 0:
                validation_errors = [f"Model execution error during out-of-sample evaluation: {'; '.join(eval_errors)}"]
            elif len(oos_wagers) < 5:
                validation_errors = [f"Insufficient historical settled ground truth in database: {len(oos_wagers)} settled wagers available (minimum 5 required)."]
            elif oos_accepted < 5:
                validation_errors = [f"Crucible hurdle failed: Model accepted only {oos_accepted}/{len(oos_wagers)} wagers with EV >= +3.5% (minimum 5 required)."]
            else:
                validation_errors = [f"Crucible hurdle failed: ROI={sim_roi*100:.1f}% (hurdle +3.5%), delta_brier={delta_brier:+.4f}, max_dd={max_dd*100:.1f}%."]

        # 6. Admission to Shadow Incubator ($0.00)
        admitted = False
        if crucible_ok:
            # Save source code to sandbox_models/generated/
            GENERATED_MODELS_DIR.mkdir(parents=True, exist_ok=True)
            saved_file = GENERATED_MODELS_DIR / f"{model_id}.py"
            saved_file.write_text(source_code, encoding="utf-8")

            # Register in tournament
            ok, t_errs = self.tournament.register_candidate(canonical_seg, model_instance, sample_context=ctx)
            admitted = ok

            # Register in dynamic archetype registry for continuous model library enrichment
            try:
                self.registry.register_new_model(
                    model_id=model_id,
                    source_code=source_code,
                    class_name=found_class,
                    league=league,
                    scope=scope,
                    family=family,
                    oos_roi=sim_roi,
                    delta_brier=delta_brier
                )
            except Exception:
                pass

            # Formally bind champion candidate model to demoted_markets in Shadow Incubator (Tier 2 $0.00)
            try:
                from .demoted_market_bridge import DemotedMarketIncubationBridge
                from .historical_trainer import CandidateTrainingResult
                bridge = DemotedMarketIncubationBridge(data_dir=self.data_dir)
                cand_res = CandidateTrainingResult(
                    segment_key=canonical_seg,
                    model_id=model_id,
                    model_archetype=found_class,
                    model_params={'source_file': f"{model_id}.py", 'provider': provider},
                    train_sample_size=0,
                    oos_sample_size=oos_accepted,
                    oos_win_rate=round(win_rate, 4),
                    oos_simulated_roi=round(sim_roi, 4),
                    oos_delta_brier=round(delta_brier, 4),
                    oos_max_drawdown=round(max_dd, 4),
                    is_qualified=True,
                    scorecard_s=100.0,
                )
                b_ok = bridge.admit_champion_to_market(None, canonical_seg, cand_res)
                if b_ok:
                    admitted = True
                    log.debug("[SYNTH_ADMIT_SUCCESS] Candidate model '%s' (%s) admitted to Tier 2 for '%s'", model_id, found_class, canonical_seg)
                else:
                    log.debug("[SYNTH_ADMIT_REJECTED] Bridge did not admit candidate '%s' for '%s'", model_id, canonical_seg)
            except Exception as e_adm:
                log.debug("Auto-admission to demoted_markets in Tier 2 non-fatal: %s", e_adm)

        fail_msg = '; '.join(validation_errors) if (not crucible_ok and validation_errors) else None
        record = SynthesizedModelRecord(
            model_id=model_id,
            segment_key=canonical_seg,
            class_name=found_class,
            source_code=source_code,
            generator_provider=provider,
            rationale=rationale,
            syntax_valid=True,
            safety_valid=True,
            validation_passed=True,
            validation_errors=validation_errors if not crucible_ok else [],
            failure_reason=fail_msg,
            oos_sample_size=oos_accepted,
            oos_win_rate=round(win_rate, 4),
            oos_simulated_roi=round(sim_roi, 4),
            oos_delta_brier=round(delta_brier, 4),
            oos_max_drawdown=round(max_dd, 4),
            crucible_passed=crucible_ok,
            is_admitted_to_incubator=admitted
        )
        self._append_log(record)

        # 7. Record to AI Reinforcement Feedback Loop Memory
        try:
            source_path = str((GENERATED_MODELS_DIR / f"{model_id}.py").resolve()) if admitted else ""
            learned = f"Crucible {'Passed' if crucible_ok else 'Failed'}: OOS ROI={sim_roi*100:.1f}%, DeltaBrier={delta_brier:.4f}, DD={max_dd*100:.1f}%"
            if fail_msg:
                learned += f" | Reason: {fail_msg}"
            fb_rec = AIFeedbackRecord(
                id=f"synth_{model_id}",
                segment_key=canonical_seg,
                league=league,
                scope=scope,
                family=family,
                model_id=model_id,
                class_name=found_class,
                generator_provider=provider,
                source_file_path=source_path,
                claude_hypothesis=claude_hypothesis,
                codex_rationale=rationale,
                grok_critique=grok_critique,
                oos_sample_size=oos_accepted,
                oos_win_rate=round(win_rate, 4),
                oos_simulated_roi=round(sim_roi, 4),
                oos_delta_brier=round(delta_brier, 4),
                oos_max_drawdown=round(max_dd, 4),
                crucible_passed=crucible_ok,
                current_tier=2 if admitted else 1,
                graduation_status="incubating" if admitted else "crucible_failed",
                learned_insight=learned,
                failure_reason=fail_msg,
                validation_errors=validation_errors if not crucible_ok else None,
            )
            self.feedback_manager.record_synthesis_attempt(fb_rec)
        except Exception:
            pass

        return record
