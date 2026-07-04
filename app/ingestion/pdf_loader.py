from pathlib import Path

class PDFLoader:
    def __init__(self, processed_dir: str | Path = "data/processed"):
        self.processed_dir = Path(processed_dir)
        self.md = self._load_converter()

    def processing(self, pdf_file_path: str) -> str:
        pdf_path = Path(pdf_file_path)
        self._validate_pdf_path(pdf_path)

        # convert PDF to markdown
        content = self.md.convert(str(pdf_path))
        
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.processed_dir / f"{pdf_path.stem}.md"
        output_path.write_text(content.text_content, encoding="utf-8")
        return content.text_content

    @staticmethod
    def _load_converter():
        try:
            from markitdown import MarkItDown
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install markitdown from requirements.txt.") from exc
        return MarkItDown()

    @staticmethod
    def _validate_pdf_path(pdf_path: Path) -> None:
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a PDF file, got: {pdf_path}")
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        if not pdf_path.is_file():
            raise ValueError(f"Expected a file path, got: {pdf_path}")
        if pdf_path.stat().st_size == 0:
            raise ValueError(f"PDF file is empty: {pdf_path}")

    
