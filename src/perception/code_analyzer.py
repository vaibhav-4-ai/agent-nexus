"""
Code Analyzer — AST parsing and code understanding via tree-sitter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.infra.logging import get_logger

logger = get_logger("perception.code")


@dataclass
class FunctionInfo:
    """Extracted function information."""
    name: str
    start_line: int
    end_line: int
    parameters: list[str] = field(default_factory=list)
    return_type: str = ""
    docstring: str = ""
    calls: list[str] = field(default_factory=list)


@dataclass
class CodeAnalysis:
    """Complete code analysis result."""
    language: str
    total_lines: int
    imports: list[str] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: str = ""


class CodeAnalyzer:
    """Code analysis engine using tree-sitter for AST parsing."""

    LANGUAGE_MAP = {
        "python": "python", "py": "python",
        "javascript": "javascript", "js": "javascript",
        "typescript": "typescript", "ts": "typescript",
        "go": "go", "rust": "rust", "java": "java",
    }

    def parse_file(self, code: str, language: str) -> CodeAnalysis:
        """Parse code and extract structural information."""
        lang = self.LANGUAGE_MAP.get(language.lower(), language.lower())
        lines = code.split("\n")
        analysis = CodeAnalysis(language=lang, total_lines=len(lines))

        try:
            if lang == "python":
                analysis = self._parse_python(code, analysis)
            else:
                analysis = self._parse_generic(code, analysis, lang)
        except Exception as e:
            logger.warning("code_parse_failed", language=lang, error=str(e))
            analysis.errors.append(str(e))
            analysis = self._parse_generic(code, analysis, lang)

        analysis.summary = (
            f"{lang} file: {analysis.total_lines} lines, "
            f"{len(analysis.functions)} functions, "
            f"{len(analysis.classes)} classes, "
            f"{len(analysis.imports)} imports"
        )
        return analysis

    def _parse_python(self, code: str, analysis: CodeAnalysis) -> CodeAnalysis:
        """Parse Python code using the ast module (always available, no tree-sitter needed)."""
        import ast

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            analysis.errors.append(f"Syntax error: {e}")
            return analysis

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        analysis.imports.append(alias.name)
                else:
                    module = node.module or ""
                    for alias in node.names:
                        analysis.imports.append(f"{module}.{alias.name}")

            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                params = [arg.arg for arg in node.args.args]
                docstring = ast.get_docstring(node) or ""
                returns = ast.unparse(node.returns) if node.returns else ""

                # Find function calls within this function
                calls = []
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name):
                            calls.append(child.func.id)
                        elif isinstance(child.func, ast.Attribute):
                            calls.append(child.func.attr)

                analysis.functions.append(FunctionInfo(
                    name=node.name,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    parameters=params,
                    return_type=returns,
                    docstring=docstring[:200],
                    calls=calls[:20],
                ))

            elif isinstance(node, ast.ClassDef):
                analysis.classes.append(node.name)

        return analysis

    def _parse_generic(self, code: str, analysis: CodeAnalysis, language: str) -> CodeAnalysis:
        """Fallback: regex-based parsing for non-Python languages."""
        import re

        # Extract imports
        import_patterns = {
            "javascript": r'(?:import\s+.*?from\s+["\'](.+?)["\']|require\(["\'](.+?)["\']\))',
            "typescript": r'import\s+.*?from\s+["\'](.+?)["\']',
            "go": r'import\s+"(.+?)"',
            "java": r'import\s+([\w.]+);',
            "rust": r'use\s+([\w:]+)',
        }
        pattern = import_patterns.get(language, r'import\s+(.+)')
        for match in re.finditer(pattern, code):
            analysis.imports.append(match.group(1) or match.group(2) if match.lastindex and match.lastindex > 1 else match.group(1))

        # Extract function definitions
        func_patterns = {
            "javascript": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\())',
            "go": r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)',
            "java": r'(?:public|private|protected)?\s*(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(',
            "rust": r'(?:pub\s+)?fn\s+(\w+)',
        }
        func_pattern = func_patterns.get(language, r'function\s+(\w+)')
        for i, line in enumerate(code.split("\n"), 1):
            match = re.search(func_pattern, line)
            if match:
                name = match.group(1) or (match.group(2) if match.lastindex and match.lastindex > 1 else "")
                if name:
                    analysis.functions.append(FunctionInfo(name=name, start_line=i, end_line=i))

        return analysis

    def find_functions(self, code: str, language: str = "python") -> list[FunctionInfo]:
        """Find all functions in code."""
        return self.parse_file(code, language).functions

    def get_imports(self, code: str, language: str = "python") -> list[str]:
        """Get all imports from code."""
        return self.parse_file(code, language).imports

    def get_call_graph(self, code: str, language: str = "python") -> dict[str, list[str]]:
        """Get a function call graph (caller -> callees)."""
        analysis = self.parse_file(code, language)
        return {fn.name: fn.calls for fn in analysis.functions}
