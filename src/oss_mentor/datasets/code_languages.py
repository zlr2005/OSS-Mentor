"""File-extension scope for the multilingual contribution sample."""
from pathlib import PurePosixPath

EXTENSIONS = {
    '.py': 'Python', '.pyi': 'Python', '.java': 'Java', '.kt': 'Kotlin',
    '.ts': 'TypeScript', '.tsx': 'TypeScript', '.js': 'JavaScript', '.jsx': 'JavaScript',
    '.mjs': 'JavaScript', '.cjs': 'JavaScript', '.rs': 'Rust', '.go': 'Go',
    '.c': 'C', '.h': 'C', '.cpp': 'C++', '.cc': 'C++', '.cxx': 'C++', '.hpp': 'C++',
    '.cs': 'C#', '.vim': 'Vim Script', '.groovy': 'Groovy', '.gradle': 'Groovy',
    '.rb': 'Ruby', '.swift': 'Swift', '.scala': 'Scala', '.sh': 'Shell', '.vue': 'Vue',
    '.svelte': 'Svelte', '.lua': 'Lua', '.ex': 'Elixir', '.exs': 'Elixir', '.php': 'PHP',
}
LICENSE_PATHS = ('LICENSE', 'LICENSE.txt', 'LICENSE.rst', 'LICENSE.md', 'LICENSE-MIT',
                 'COPYING', 'COPYING.txt', 'COPYING.md', 'LICENCE', 'LICENCE.md')


def code_language(path):
    return EXTENSIONS.get(PurePosixPath(path).suffix.lower())
