"""PDF Loader implementation using MarkItDown.

This module implements PDF parsing with image extraction support,
converting PDFs to standardized Markdown format with image placeholders.

Features:
- Text extraction and Markdown conversion via MarkItDown
- Image extraction and storage
- Image placeholder insertion with metadata tracking
- Graceful degradation if image extraction fails
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional

try:
    from markitdown import MarkItDown
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    pdfplumber = None
    PDFPLUMBER_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    TESSERACT_AVAILABLE = False

from PIL import Image
import io

from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader

logger = logging.getLogger(__name__)


class PdfLoader(BaseLoader):
    """PDF Loader using MarkItDown for text extraction and Markdown conversion.
    
    This loader:
    1. Extracts text from PDF and converts to Markdown
    2. Extracts images and saves to data/images/{doc_hash}/
    3. Inserts image placeholders in the format [IMAGE: {image_id}]
    4. Records image metadata in Document.metadata.images
    
    Configuration:
        extract_images: Enable/disable image extraction (default: True)
        image_storage_dir: Base directory for image storage (default: data/images)
    
    Graceful Degradation:
        If image extraction fails, logs warning and continues with text-only parsing.
    """
    
    def __init__(
        self,
        extract_images: bool = True,
        image_storage_dir: str | Path = "data/images",
        ocr_enabled: bool = True,
        ocr_language: str = "eng",
        ocr_dpi: int = 200,
    ):
        """Initialize PDF Loader.
        
        Args:
            extract_images: Whether to extract images from PDFs.
            image_storage_dir: Base directory for storing extracted images.
        """
        if not MARKITDOWN_AVAILABLE:
            raise ImportError(
                "MarkItDown is required for PdfLoader. "
                "Install with: pip install markitdown"
            )
        
        self.extract_images = extract_images
        self.image_storage_dir = Path(image_storage_dir)
        self.ocr_enabled = ocr_enabled
        self.ocr_language = ocr_language
        self.ocr_dpi = ocr_dpi
        self._markitdown = MarkItDown()

        # MarkItDown/pdfminer can emit one DEBUG record for every PDF token
        # when the ingestion CLI uses --verbose. Keep parser internals quiet;
        # the pipeline's stage logs are the useful progress signal.
        for noisy_logger in (
            "pdfminer",
            "pdfminer.psparser",
            "pdfminer.pdfparser",
            "pdfplumber",
        ):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    
    def load(self, file_path: str | Path) -> Document:
        """Load and parse a PDF file.
        
        Args:
            file_path: Path to the PDF file.
            
        Returns:
            Document with Markdown text and metadata.
            
        Raises:
            FileNotFoundError: If the PDF file doesn't exist.
            ValueError: If the file is not a valid PDF.
            RuntimeError: If parsing fails critically.
        """
        # Validate file
        path = self._validate_file(file_path)
        if path.suffix.lower() != '.pdf':
            raise ValueError(f"File is not a PDF: {path}")
        
        # Compute document hash for unique ID and image directory
        doc_hash = self._compute_file_hash(path)
        doc_id = f"doc_{doc_hash[:16]}"
        
        # Detect whether the source PDF already contains a text layer. For
        # image-only PDFs, create a temporary searchable PDF with OCR first,
        # then pass that OCR output to MarkItDown as requested.
        extraction_method = "markitdown"
        text_layer = self._extract_text(path)
        conversion_path = path

        try:
            if not text_layer.strip() and self.ocr_enabled:
                logger.info("No PDF text layer found; starting OCR for %s", path)
                with tempfile.TemporaryDirectory(prefix="pdf_ocr_") as temp_dir:
                    ocr_path = Path(temp_dir) / f"{path.stem}.ocr.pdf"
                    self._create_ocr_pdf(path, ocr_path)
                    logger.info("OCR completed; passing OCR PDF to MarkItDown")
                    text_content = self._convert_with_markitdown(ocr_path)
                    extraction_method = "ocr+markitdown"
            else:
                logger.info("PDF text layer found; passing source PDF to MarkItDown")
                text_content = self._convert_with_markitdown(conversion_path)

            # MarkItDown may return no text even when a text layer exists. In
            # that case retry through OCR once, unless OCR was already used.
            if not text_content.strip() and self.ocr_enabled and extraction_method == "markitdown":
                logger.info("MarkItDown returned no text; retrying through OCR for %s", path)
                with tempfile.TemporaryDirectory(prefix="pdf_ocr_") as temp_dir:
                    ocr_path = Path(temp_dir) / f"{path.stem}.ocr.pdf"
                    self._create_ocr_pdf(path, ocr_path)
                    logger.info("OCR completed; passing OCR PDF to MarkItDown")
                    text_content = self._convert_with_markitdown(ocr_path)
                    extraction_method = "ocr+markitdown"
        except Exception as e:
            logger.error(f"Failed to parse PDF {path}: {e}")
            raise RuntimeError(f"PDF parsing failed: {e}") from e

        if not text_content.strip():
            raise RuntimeError(
                f"No text was extracted from PDF: {path}. "
                "The PDF may be image-only and OCR did not produce text."
            )
        
        # Initialize metadata
        metadata: Dict[str, Any] = {
            "source_path": str(path),
            "doc_type": "pdf",
            "doc_hash": doc_hash,
            "text_extraction": extraction_method,
        }
        
        # Extract title from first heading if available
        title = self._extract_title(text_content)
        if title:
            metadata["title"] = title
        
        # Handle image extraction (with graceful degradation)
        if self.extract_images:
            try:
                text_content, images_metadata = self._extract_and_process_images(
                    path, text_content, doc_hash
                )
                if images_metadata:
                    metadata["images"] = images_metadata
            except Exception as e:
                logger.warning(
                    f"Image extraction failed for {path}, continuing with text-only: {e}"
                )
        
        return Document(
            id=doc_id,
            text=text_content,
            metadata=metadata
        )

    def _extract_text(self, path: Path) -> str:
        """Return the existing PDF text layer, if one is available.

        This method is only used to decide whether OCR is needed. The final
        Markdown conversion is still performed by MarkItDown.
        """
        # PyMuPDF is a lightweight text-layer check and avoids walking every
        # token through pdfminer just to decide whether OCR is needed.
        if PYMUPDF_AVAILABLE and fitz is not None:
            pdf = fitz.open(str(path))
            try:
                page_text = [page.get_text("text").strip() for page in pdf]
            finally:
                pdf.close()
            return "\n\n".join(text for text in page_text if text)

        # Keep pdfplumber as a fallback for environments without PyMuPDF.
        if PDFPLUMBER_AVAILABLE and pdfplumber is not None:
            page_text = []
            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    if text.strip():
                        page_text.append(text.strip())
            return "\n\n".join(page_text)

        return ""

    def _convert_with_markitdown(self, path: Path) -> str:
        """Convert a PDF to Markdown text with MarkItDown."""
        result = self._markitdown.convert(str(path))
        return result.text_content if hasattr(result, "text_content") else str(result)

    def _create_ocr_pdf(self, pdf_path: Path, output_path: Path) -> Path:
        """Create a searchable PDF by OCRing each page, then return its path.

        Tesseract produces a PDF containing the original rendered page and a
        searchable text layer. The output is temporary and is consumed by
        MarkItDown; the source PDF is never overwritten.
        """
        if not TESSERACT_AVAILABLE or pytesseract is None:
            raise RuntimeError(
                "OCR requires the Python package 'pytesseract'. "
                "Install it and the Tesseract OCR executable before ingesting "
                "image-only PDFs."
            )
        if not PYMUPDF_AVAILABLE or fitz is None:
            raise RuntimeError("OCR requires PyMuPDF to render PDF pages.")

        tesseract_cmd = os.getenv("TESSERACT_CMD")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        output_path.parent.mkdir(parents=True, exist_ok=True)
        source_doc = None
        ocr_doc = None
        try:
            source_doc = fitz.open(str(pdf_path))
            ocr_doc = fitz.open()

            scale = self.ocr_dpi / 72.0
            matrix = fitz.Matrix(scale, scale)

            for page_number, page in enumerate(source_doc, start=1):
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = Image.frombytes(
                    "RGB",
                    (pixmap.width, pixmap.height),
                    pixmap.samples,
                )

                try:
                    page_pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                        image,
                        lang=self.ocr_language,
                        extension="pdf",
                    )
                except Exception as exc:
                    if isinstance(exc, getattr(pytesseract, "TesseractNotFoundError", ())):
                        raise RuntimeError(
                            "Tesseract executable was not found. Install Tesseract OCR "
                            "and make sure it is available on PATH."
                        ) from exc
                    raise RuntimeError(
                        f"OCR failed on page {page_number} of {pdf_path}: {exc}"
                    ) from exc

                page_doc = fitz.open(stream=page_pdf_bytes, filetype="pdf")
                try:
                    ocr_doc.insert_pdf(page_doc)
                finally:
                    page_doc.close()

            ocr_doc.save(str(output_path), garbage=4, deflate=True)
            logger.info("Created OCR PDF %s from %s", output_path, pdf_path)
            return output_path
        finally:
            if ocr_doc is not None:
                ocr_doc.close()
            if source_doc is not None:
                source_doc.close()
    
    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file content.
        
        Args:
            file_path: Path to file.
            
        Returns:
            Hex string of SHA256 hash.
        """
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def _extract_title(self, text: str) -> Optional[str]:
        """Extract title from first Markdown heading or first non-empty line.
        
        Args:
            text: Markdown text content.
            
        Returns:
            Title string if found, None otherwise.
        """
        lines = text.split('\n')
        
        # First try to find a markdown heading
        for line in lines[:20]:  # Check first 20 lines
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()
        
        # Fallback: use first non-empty line as title
        for line in lines[:10]:
            line = line.strip()
            if line and len(line) > 0:
                return line
        
        return None
    
    def _extract_and_process_images(
        self,
        pdf_path: Path,
        text_content: str,
        doc_hash: str
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Extract images from PDF and insert placeholders.
        
        Uses PyMuPDF to extract images, save them to disk, and insert
        placeholders in the text content.
        
        Args:
            pdf_path: Path to PDF file.
            text_content: Extracted text content.
            doc_hash: Document hash for image directory.
            
        Returns:
            Tuple of (modified_text, images_metadata_list)
        """
        if not self.extract_images:
            logger.debug(f"Image extraction disabled for {pdf_path}")
            return text_content, []
        
        if not PYMUPDF_AVAILABLE:
            logger.warning(f"PyMuPDF not available, skipping image extraction for {pdf_path}")
            return text_content, []
        
        images_metadata = []
        modified_text = text_content
        
        try:
            # Create image storage directory
            image_dir = self.image_storage_dir / doc_hash
            image_dir.mkdir(parents=True, exist_ok=True)
            
            # Open PDF with PyMuPDF
            doc = fitz.open(pdf_path)
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                
                for img_index, img_info in enumerate(image_list):
                    try:
                        # Extract image
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        
                        # Generate image ID and filename
                        image_id = self._generate_image_id(doc_hash, page_num + 1, img_index + 1)
                        image_filename = f"{image_id}.{image_ext}"
                        image_path = image_dir / image_filename
                        
                        # Save image
                        with open(image_path, "wb") as img_file:
                            img_file.write(image_bytes)
                        
                        # Get image dimensions
                        try:
                            img = Image.open(io.BytesIO(image_bytes))
                            width, height = img.size
                        except Exception:
                            width, height = 0, 0
                        
                        # Create placeholder
                        placeholder = f"[IMAGE: {image_id}]"
                        
                        # Insert placeholder at end of current page's content
                        # (simplified - in production, you'd parse page boundaries)
                        insert_position = len(modified_text)
                        modified_text += f"\n{placeholder}\n"
                        
                        # Convert path to be relative to project root or absolute
                        try:
                            relative_path = image_path.relative_to(Path.cwd())
                        except ValueError:
                            # If not in cwd, use absolute path
                            relative_path = image_path.absolute()
                        
                        # Record metadata
                        image_metadata = {
                            "id": image_id,
                            "path": str(relative_path),
                            "page": page_num + 1,
                            "text_offset": insert_position + 1,  # +1 for newline
                            "text_length": len(placeholder),
                            "position": {
                                "width": width,
                                "height": height,
                                "page": page_num + 1,
                                "index": img_index
                            }
                        }
                        images_metadata.append(image_metadata)
                        
                        logger.debug(f"Extracted image {image_id} from page {page_num + 1}")
                        
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
                        continue
            
            doc.close()
            
            if images_metadata:
                logger.info(f"Extracted {len(images_metadata)} images from {pdf_path}")
            else:
                logger.debug(f"No images found in {pdf_path}")
            
            return modified_text, images_metadata
            
        except Exception as e:
            logger.warning(f"Image extraction failed for {pdf_path}: {e}")
            # Graceful degradation: return original text without images
            return text_content, []
    
    @staticmethod
    def _generate_image_id(doc_hash: str, page: int, sequence: int) -> str:
        """Generate unique image ID.
        
        Args:
            doc_hash: Document hash.
            page: Page number (0-based).
            sequence: Image sequence on page (0-based).
            
        Returns:
            Unique image ID string.
        """
        return f"{doc_hash[:8]}_{page}_{sequence}"
