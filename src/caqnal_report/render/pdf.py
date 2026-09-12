"""PPTX → PDF（LibreOffice headless）。フォントは OS 解決なので Noto Sans JP がインストールされていること。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/opt/homebrew/bin/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(p).exists():
            return p
    return None


def convert_to_pdf(pptx: Path, out_dir: Path | None = None, timeout: int = 600) -> Path:
    exe = find_soffice()
    if exe is None:
        raise RuntimeError("LibreOffice (soffice) が見つかりません。brew install --cask libreoffice")
    out_dir = out_dir or pptx.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    pdf = out_dir / (pptx.stem + ".pdf")
    if r.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"PDF 変換に失敗: {pptx.name}\n{r.stdout}\n{r.stderr}")
    return pdf
