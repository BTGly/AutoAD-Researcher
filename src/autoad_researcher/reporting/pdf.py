"""Optional XeLaTeX report rendering with explicit capability diagnostics."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from autoad_researcher.reporting.binary_persistence import write_immutable_report_bytes
from autoad_researcher.reporting.persistence import write_immutable_report_json
from autoad_researcher.reporting.store import ReportStore

REPORT_PDF_JOB_TYPE = "report_render_pdf"
PDF_RENDERER_VERSION = "v1"


class PdfRenderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    renderer: str = "xelatex"
    status: str
    reason: str | None = None
    return_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""


def run_pdf_job(run_dir: Path, job: dict[str, object]) -> list[str]:
    report_id = job.get("report_id")
    if not isinstance(report_id, str):
        raise ValueError("report PDF Job lacks report identity")
    store = ReportStore()
    state = store.load_state(run_dir, report_id)
    if state.generation_status != "content_ready":
        raise ValueError("report PDF rendering requires content_ready")
    if state.format_status.pdf == "ready":
        return _outputs(run_dir, report_id)
    compiler = shutil.which("xelatex")
    if compiler is None:
        return _record_failed(run_dir, report_id, PdfRenderResult(status="failed", reason="xelatex is not available"))
    try:
        with tempfile.TemporaryDirectory(prefix="autoad-report-pdf-") as raw_dir:
            directory = Path(raw_dir)
            (directory / "report.md").write_bytes((run_dir / "reports" / report_id / "report.md").read_bytes())
            (directory / "report.tex").write_text(_latex_document(), encoding="utf-8")
            process = subprocess.run(
                [compiler, "-interaction=nonstopmode", "-halt-on-error", "report.tex"],
                cwd=directory, capture_output=True, text=True, timeout=90, check=False,
            )
            pdf = directory / "report.pdf"
            result = PdfRenderResult(status="ready" if process.returncode == 0 and pdf.is_file() and pdf.stat().st_size else "failed", reason=None if process.returncode == 0 and pdf.is_file() and pdf.stat().st_size else "xelatex did not produce a non-empty report.pdf", return_code=process.returncode, stdout_tail=process.stdout[-4000:], stderr_tail=process.stderr[-4000:])
            if result.status != "ready":
                return _record_failed(run_dir, report_id, result)
            write_immutable_report_bytes(run_dir, report_id=report_id, filename="report.pdf", artifact_type="report_pdf", content=pdf.read_bytes())
            write_immutable_report_json(run_dir, report_id=report_id, filename="report_pdf_result.json", artifact_type="report_pdf_result", value=result.model_dump(mode="json"))
            store.set_format_status(run_dir, report_id=report_id, format_name="pdf", status="ready")
            return _outputs(run_dir, report_id)
    except subprocess.TimeoutExpired as exc:
        return _record_failed(run_dir, report_id, PdfRenderResult(status="failed", reason="xelatex timed out", stdout_tail=(exc.stdout or "")[-4000:], stderr_tail=(exc.stderr or "")[-4000:]))


def _record_failed(run_dir: Path, report_id: str, result: PdfRenderResult) -> list[str]:
    write_immutable_report_json(run_dir, report_id=report_id, filename="report_pdf_result.json", artifact_type="report_pdf_result", value=result.model_dump(mode="json"))
    ReportStore().set_format_status(run_dir, report_id=report_id, format_name="pdf", status="failed")
    return _outputs(run_dir, report_id)


def _outputs(run_dir: Path, report_id: str) -> list[str]:
    directory = run_dir / "reports" / report_id
    return [str((directory / name).relative_to(run_dir)) for name in ("report_pdf_result.json", "report.pdf") if (directory / name).is_file()]


def _latex_document() -> str:
    # VerbatimInput reads Markdown as data, avoiding TeX interpolation from report text.
    return """\\documentclass{ctexart}
\\usepackage{fancyvrb}
\\begin{document}
\\VerbatimInput[fontsize=\\small]{report.md}
\\end{document}
"""
