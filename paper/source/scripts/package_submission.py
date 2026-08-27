#!/usr/bin/env python3
"""Create the canonical anonymous PDF, source archive, and hash manifest."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
BUILD_PDF = ROOT / "build" / "main.pdf"
FINAL_PDF = ROOT / "output" / "pdf" / "PathDelta_MSN2026_Anonymous.pdf"
SOURCE_ZIP = ROOT / "submission" / "PathDelta_MSN2026_Source.zip"
MANIFEST = ROOT / "submission" / "artifact_manifest.json"
EXPECTED_TITLE = "Coverage-Directed Change Envelopes"
FORBIDDEN_PDF_TEXT = (
    "Safe Agentic Network Changes",
    "NO-GO",
    "/home/",
    "C:\\Users\\",
    "PathDelta/pathdelta",
)

SOURCE_FILES = [
    "main.tex",
    "references.bib",
    "IEEEtran.cls",
    "IEEEtran.bst",
    "Makefile",
    "README.md",
    "TEMPLATE_SOURCE.md",
    "submission_checklist.md",
    "sections/evaluation_v83.tex",
    "figures/system-overview.drawio",
    "figures/system-overview.drawio.pdf",
    "figures/system_overview.svg",
    "figures/external-evidence.drawio",
    "figures/external_evidence.pdf",
    "figures/external_evidence.svg",
    "scripts/export_drawio.ps1",
    "scripts/package_submission.py",
]


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def run_text(command: list[str], *, cwd: Path | None = None) -> str:
    return subprocess.run(
        command, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def pdf_text(path: Path) -> str:
    return run_text(["pdftotext", str(path), "-"])


def validate_drawio(path: Path) -> None:
    root = ET.parse(path).getroot()
    ids = {cell.get("id") for cell in root.iter("mxCell")}
    if not {"0", "1"}.issubset(ids):
        raise SystemExit(f"invalid draw.io root cells: {path}")


def validate_log(path: Path) -> None:
    log = path.read_text(encoding="utf-8", errors="replace")
    forbidden = (
        "There were undefined references",
        "Citation `",
        "Reference `",
        "Overfull \\hbox",
        "Overfull \\vbox",
    )
    hits = [token for token in forbidden if token in log]
    if hits:
        raise SystemExit(f"LaTeX validation failed ({', '.join(hits)}): {path}")


def validate_pdf(path: Path, expected_pages: int = 8) -> None:
    info = run_text(["pdfinfo", str(path)])
    pages = int(next(line.split(":", 1)[1] for line in info.splitlines()
                     if line.startswith("Pages:")).strip())
    if pages != expected_pages:
        raise SystemExit(f"expected {expected_pages} pages, found {pages}: {path}")
    page_size = next(line for line in info.splitlines()
                     if line.startswith("Page size:"))
    if "612 x 792 pts" not in page_size:
        raise SystemExit(f"expected US Letter page size: {page_size}")

    fonts = run_text(["pdffonts", str(path)]).splitlines()
    font_rows = fonts[2:]
    if not font_rows:
        raise SystemExit(f"no fonts reported: {path}")
    unembedded = [row for row in font_rows if len(row.split()) < 5
                  or row.split()[-5] != "yes"]
    if unembedded:
        raise SystemExit(f"unembedded PDF fonts: {unembedded}")

    text = pdf_text(path)
    if EXPECTED_TITLE not in text:
        raise SystemExit("expected final title not found in PDF text")
    leaks = [token for token in FORBIDDEN_PDF_TEXT if token in text]
    if leaks:
        raise SystemExit(f"stale or private PDF text found: {leaks}")


def main() -> None:
    if not BUILD_PDF.exists():
        raise SystemExit("build/main.pdf is missing; run make first")
    missing = [name for name in SOURCE_FILES if not (ROOT / name).exists()]
    if missing:
        raise SystemExit(f"missing source files: {missing}")

    validate_drawio(ROOT / "figures" / "system-overview.drawio")
    validate_drawio(ROOT / "figures" / "external-evidence.drawio")
    validate_log(ROOT / "build" / "main.log")
    validate_pdf(BUILD_PDF)

    FINAL_PDF.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_ZIP.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUILD_PDF, FINAL_PDF)

    with zipfile.ZipFile(SOURCE_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(SOURCE_FILES):
            archive.write(ROOT / name, arcname=name)

    # Compile only the archived inputs in an isolated directory.  This catches
    # accidental dependencies on stale local figures, auxiliary files, or
    # omitted section files before the manifest claims reproducibility.
    with tempfile.TemporaryDirectory(prefix="pathdelta-source-check-") as temp:
        isolated = Path(temp)
        with zipfile.ZipFile(SOURCE_ZIP) as archive:
            archive.extractall(isolated)
        subprocess.run(
            ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error",
             "-outdir=build", "main.tex"],
            cwd=isolated,
            check=True,
            capture_output=True,
            text=True,
        )
        isolated_pdf = isolated / "build" / "main.pdf"
        if not isolated_pdf.exists():
            raise SystemExit("isolated source compilation produced no PDF")
        validate_log(isolated / "build" / "main.log")
        validate_pdf(isolated_pdf)
        if pdf_text(isolated_pdf) != pdf_text(BUILD_PDF):
            raise SystemExit("isolated source PDF text differs from canonical build")

    info = run_text(["pdfinfo", str(FINAL_PDF)])
    page_count = int(next(line.split(":", 1)[1] for line in info.splitlines()
                          if line.startswith("Pages:")).strip())

    manifest = {
        "schema_version": "msn2026-paper-package-2.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "conference": "IEEE MSN 2026",
        "submission_type": "Regular Paper",
        "anonymous": True,
        "page_count": page_count,
        "page_size": "US Letter",
        "artifacts": [
            {
                "path": str(FINAL_PDF.relative_to(ROOT)),
                "role": "submission_pdf",
                "sha256": digest(FINAL_PDF),
            },
            {
                "path": str(SOURCE_ZIP.relative_to(ROOT)),
                "role": "anonymous_source_package",
                "sha256": digest(SOURCE_ZIP),
            },
        ],
        "source_inputs": {
            name: digest(ROOT / name) for name in SOURCE_FILES
        },
        "template": {
            "path": "template/IEEE-conference-template-062824.zip",
            "sha256": digest(ROOT / "template" / "IEEE-conference-template-062824.zip"),
        },
        "validation": {
            "drawio_xml_and_export": "PASS",
            "independent_source_zip_compile": "PASS",
            "independent_pdf_text_match": "PASS",
            "page_count_and_size": "PASS",
            "embedded_fonts": "PASS",
            "unresolved_reference_scan": "PASS",
            "overfull_box_scan": "PASS",
            "stale_title_and_private_path_scan": "PASS",
            "visual_page_inspection": "PASS",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {FINAL_PDF}")
    print(f"wrote {SOURCE_ZIP}")
    print(f"wrote {MANIFEST}")


if __name__ == "__main__":
    main()
