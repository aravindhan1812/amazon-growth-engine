"""Inject results.json into the dashboard template to produce a standalone HTML page.

Output goes to docs/index.html, which is both the local preview and what GitHub Pages
serves. It is committed to the repo on purpose — Pages can only publish files that are
actually in the repository.
"""

import json
import os


def main() -> None:
    with open("results.json") as handle:
        data = handle.read()
    json.loads(data)  # fail loudly if the results file is malformed

    with open("dashboard_template.html") as handle:
        template = handle.read()

    if "__DATA_JSON__" not in template:
        raise SystemExit("template is missing the __DATA_JSON__ placeholder")

    output = template.replace("__DATA_JSON__", data)
    os.makedirs("docs", exist_ok=True)

    # Tell GitHub Pages to serve the directory as-is rather than running Jekyll over it
    with open(os.path.join("docs", ".nojekyll"), "w") as handle:
        handle.write("")

    out_path = os.path.join("docs", "index.html")
    with open(out_path, "w") as handle:
        handle.write(output)
    print(f"wrote {out_path} ({len(output):,} bytes)")
    print("commit it and enable Pages (Settings -> Pages -> main branch, /docs folder)")


if __name__ == "__main__":
    main()
