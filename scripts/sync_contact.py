"""
sync_contact.py — propagate the project contact email from pyproject.toml into
README.md and frontend/index.html.

Single source of truth: `[project].authors[0].email` in `pyproject.toml`.

Run after editing pyproject.toml:

    python scripts/sync_contact.py

Idempotent: safe to run any number of times. Designed to be a no-op when nothing
needs updating, so it can also be wired into a pre-commit hook later if desired.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
FRONTEND = ROOT / "frontend" / "index.html"


def read_contact_email() -> str:
    """Pull email from [project].authors[0]. Returns '' if not set."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    authors = data.get("project", {}).get("authors", []) or []
    if not authors:
        return ""
    return (authors[0] or {}).get("email", "") or ""


def update_readme(email: str) -> bool:
    """Replace the contact block in README. Returns True if file changed."""
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- CONTACT_EMAIL_START -->.*?<!-- CONTACT_EMAIL_END -->",
        re.DOTALL,
    )
    if email:
        replacement = (
            f"<!-- CONTACT_EMAIL_START -->\n"
            f"**Contact:** [{email}](mailto:{email}) · "
            f"GitHub: [@vaibhav-4-ai](https://github.com/vaibhav-4-ai)\n"
            f"<!-- CONTACT_EMAIL_END -->"
        )
    else:
        replacement = (
            f"<!-- CONTACT_EMAIL_START -->\n"
            f"**Contact:** _email pending — see [pyproject.toml](pyproject.toml)_ · "
            f"GitHub: [@vaibhav-4-ai](https://github.com/vaibhav-4-ai)\n"
            f"<!-- CONTACT_EMAIL_END -->"
        )
    new_text = pattern.sub(replacement, text)
    if new_text == text:
        return False
    README.write_text(new_text, encoding="utf-8")
    return True


def update_frontend(email: str) -> bool:
    """Update the mailto: link and toggle its display. Returns True if changed."""
    text = FRONTEND.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- CONTACT_EMAIL_LINK_START -->.*?<!-- CONTACT_EMAIL_LINK_END -->",
        re.DOTALL,
    )
    display = "inline-flex" if email else "none"
    replacement = (
        f'<!-- CONTACT_EMAIL_LINK_START -->\n'
        f'                <a href="mailto:{email}" id="contact-email" class="glass-btn" '
        f'style="display:{display}"><i class="fa-solid fa-envelope"></i> Contact</a>\n'
        f'                <!-- CONTACT_EMAIL_LINK_END -->'
    )
    new_text = pattern.sub(replacement, text)
    if new_text == text:
        return False
    FRONTEND.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    if not PYPROJECT.exists():
        print(f"error: {PYPROJECT} not found", file=sys.stderr)
        return 2

    email = read_contact_email()
    if email:
        print(f"Read contact email from {PYPROJECT.name}: {email}")
    else:
        print(f"No contact email set in {PYPROJECT.name} (using placeholder).")

    if not README.exists():
        print(f"warning: {README} missing, skipping", file=sys.stderr)
    else:
        changed = update_readme(email)
        print(f"  {'✓' if changed else '·'} README.md "
              f"{'(updated)' if changed else '(no change)'}")

    if not FRONTEND.exists():
        print(f"warning: {FRONTEND} missing, skipping", file=sys.stderr)
    else:
        changed = update_frontend(email)
        print(f"  {'✓' if changed else '·'} frontend/index.html "
              f"{'(updated)' if changed else '(no change)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
