"""
scripts/generate_worker_form.py

Reads config/criteria.yaml and regenerates the criteria slider HTML + keys
inside cloudflare/worker/index.js (between GENERATED_CRITERIA_START/END markers).

Run before every wrangler deploy — the wrangler.toml [build] hook does this
automatically. Can also be run manually:

    python scripts/generate_worker_form.py

Never edit the GENERATED_CRITERIA_START ... GENERATED_CRITERIA_END block
in index.js directly — edit config/criteria.yaml instead and re-run this script.
"""

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("ERROR: PyYAML is not installed. Run: pip install pyyaml")

REPO_ROOT = Path(__file__).parent.parent
CRITERIA_PATH = REPO_ROOT / "config" / "criteria.yaml"
INDEX_PATH = REPO_ROOT / "cloudflare" / "worker" / "index.js"

START_MARKER = "// GENERATED_CRITERIA_START"
END_MARKER = "// GENERATED_CRITERIA_END"


def load_criteria():
    with open(CRITERIA_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["criteria"]


def build_slider_html(criteria):
    """Return JS template-literal fragment with one div per criterion."""
    lines = []
    for c in criteria:
        key = c["key"]
        label = c["label"]
        hint_low = c["hint_low"]
        hint_high = c["hint_high"]
        lines.append(
            f'      <div class="criterion-row">\n'
            f'        <div class="criterion-header">\n'
            f'          <span class="criterion-label">{label}</span>\n'
            f'          <span class="criterion-val" id="val-{key}">3</span>\n'
            f'        </div>\n'
            f'        <input type="range" name="criteria_{key}" min="1" max="5" value="3"\n'
            f'               aria-label="{label}" aria-valuemin="1" aria-valuemax="5" aria-valuenow="3"\n'
            f'               oninput="updateCriteria(\'{key}\', this.value)">\n'
            f'        <div class="criterion-hints">'
            f'<span>1 — {hint_low}</span><span>5 — {hint_high}</span>'
            f'</div>\n'
            f'      </div>'
        )
    return "\n".join(lines)


def build_keys_js(criteria):
    keys = ", ".join(f'"{c["key"]}"' for c in criteria)
    return f"const criteriaKeys = [{keys}];"


def inject(index_js: str, criteria) -> str:
    slider_html = build_slider_html(criteria)
    keys_js = build_keys_js(criteria)

    replacement = (
        f"{START_MARKER} — edit config/criteria.yaml + run scripts/generate_worker_form.py\n"
        f"  const criteriaSliderHtml = `\n{slider_html}\n  `;\n"
        f"  {keys_js}\n"
        f"  {END_MARKER}"
    )

    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    if not pattern.search(index_js):
        sys.exit(
            f"ERROR: markers '{START_MARKER}' / '{END_MARKER}' not found in {INDEX_PATH}.\n"
            "Add them to the surveyPage() function in index.js."
        )
    return pattern.sub(replacement, index_js)


def main():
    if not CRITERIA_PATH.exists():
        sys.exit(f"ERROR: {CRITERIA_PATH} not found.")
    if not INDEX_PATH.exists():
        sys.exit(f"ERROR: {INDEX_PATH} not found.")

    criteria = load_criteria()
    index_js = INDEX_PATH.read_text(encoding="utf-8")
    updated = inject(index_js, criteria)

    if updated == index_js:
        print("generate_worker_form.py: index.js already up to date.")
        return

    INDEX_PATH.write_text(updated, encoding="utf-8")
    print(f"generate_worker_form.py: updated {len(criteria)} criteria in {INDEX_PATH.name}.")


if __name__ == "__main__":
    main()
