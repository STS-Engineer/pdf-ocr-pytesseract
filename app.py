"""
Render.com Ready OCR API
Flask web service for PDF/Image OCR processing
- Supports: file upload, file_url, pdf_path
"""

from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os
from pathlib import Path
from PIL import Image
import pytesseract
from pdf2image import convert_from_path
import tempfile
import requests
from urllib.parse import urlparse

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# Allowed file extensions
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

# Base dir for server-side pdf_path (CHANGED: now points to repo root)
SAFE_BASE_DIR = Path(__file__).parent.resolve()  # This is the repo root where app.py lives


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_http_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https")
    except Exception:
        return False


def is_safe_path(base_dir: Path, user_path: Path) -> bool:
    """
    Ensure user_path stays within base_dir to avoid path traversal.
    """
    try:
        # Python 3.9+: Path.is_relative_to
        return user_path.resolve().is_relative_to(base_dir.resolve())
    except AttributeError:
        # For Python < 3.9
        return str(user_path.resolve()).startswith(str(base_dir.resolve()))


class SimpleOCR:
    """Lightweight OCR class for PDF/image to text conversion"""

    def __init__(self, language='eng'):
        self.language = language

    def pdf_to_text(self, pdf_path, max_pages=10, dpi=200):
        """
        Convert PDF to text using OCR.

        Args:
            pdf_path (str): Path to PDF file
            max_pages (int): Maximum pages to process
            dpi (int): Image quality for OCR

        Returns:
            dict: Processing results
        """
        try:
            images = convert_from_path(
                pdf_path,
                dpi=dpi,
                first_page=1,
                last_page=max_pages
            )

            num_pages = len(images)
            was_truncated = num_pages >= max_pages

            all_text = []
            for i, image in enumerate(images, 1):
                text = pytesseract.image_to_string(image, lang=self.language)
                all_text.append(text)

            combined_text = "\n\n--- Page Break ---\n\n".join(all_text)

            return {
                'success': True,
                'text': combined_text,
                'num_pages': num_pages,
                'was_truncated': was_truncated,
                'error': None
            }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'text': None,
                'num_pages': 0,
                'was_truncated': False
            }

    def image_to_text(self, image_path):
        """Extract text from a single image"""
        try:
            image = Image.open(image_path)
            text = pytesseract.image_to_string(image, lang=self.language)

            return {
                'success': True,
                'text': text,
                'error': None
            }
        except Exception as e:
            return {
                'success': False,
                'text': None,
                'error': str(e)
            }


# Initialize OCR
ocr = SimpleOCR(language='eng')


@app.route('/')
def home():
    """API documentation"""
    return jsonify({
        'service': 'OCR API',
        'version': '1.2',
        'endpoints': {
            '/': 'API documentation (GET)',
            '/ocr/pdf': 'Process PDF file (POST)',
            '/ocr/image': 'Process image file (POST)',
            '/health': 'Health check (GET)'
        },
        'usage': {
            'pdf (form-data)': {
                'file': 'PDF upload (required if not using file_url/pdf_path)',
                'language': 'eng (optional)',
                'max_pages': 10,
                'dpi': 200
            },
            'pdf (JSON)': {
                'file_url': 'http(s) URL to a PDF (optional)',
                'pdf_path': 'server-side path in repo root (optional)',
                'language': 'eng (optional)',
                'max_pages': 10,
                'dpi': 200
            },
            'image (form-data)': {
                'file': 'PNG/JPG upload',
                'language': 'eng (optional)'
            }
        },
        'limits': {
            'max_upload_size': '16MB',
            'max_pages_cap': 20,
            'dpi_cap': 300,
            'allowed_formats': list(ALLOWED_EXTENSIONS)
        },
        'note': f'pdf_path looks for files in: {SAFE_BASE_DIR}'
    })


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200


@app.route('/ocr/pdf', methods=['POST'])
def ocr_pdf():
    """
    Process PDF with OCR. Accepts:
      - multipart/form-data: file=<PDF file>
      - JSON or form-data:   file_url=<http/https url to a PDF>
      - JSON or form-data:   pdf_path=<server-side path in repo root>

    Example JSON body:
    {
      "pdf_path": "MNG2 - 53220997-16 (98173Y13) 03.09.2013.pdf",
      "max_pages": 20
    }
    """
    tmp_path = None
    should_delete_tmp = False

    try:
        # Accept both form-data and JSON
        payload = request.form if request.form else (request.get_json(silent=True) or {})

        # Parameters
        max_pages = int(payload.get('max_pages', 10))
        dpi = int(payload.get('dpi', 200))
        language = payload.get('language', 'eng')

        # Safety caps
        max_pages = min(max_pages, 20)
        dpi = min(dpi, 300)

        # Determine input source (priority: multipart file > file_url > pdf_path)
        source = None

        # 1) multipart file
        if 'file' in request.files and request.files['file'].filename:
            file = request.files['file']

            if not file.filename.lower().endswith('.pdf'):
                return jsonify({'success': False, 'error': 'File must be a PDF'}), 400

            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                file.save(tmp_file.name)
                tmp_path = tmp_file.name
                should_delete_tmp = True
                source = 'upload'

        # 2) file_url (http/https)
        elif 'file_url' in payload and payload['file_url']:
            url = str(payload['file_url']).strip()
            if not is_http_url(url):
                return jsonify({'success': False, 'error': 'file_url must be http/https'}), 400

            # Download to temp file
            r = requests.get(url, timeout=60)
            if r.status_code != 200:
                return jsonify({'success': False, 'error': f'Failed to fetch file_url (HTTP {r.status_code})'}), 400

            # Basic content-type/extension guard
            content_type = r.headers.get('Content-Type', '').lower()
            if ('pdf' not in content_type) and (not url.lower().endswith('.pdf')):
                return jsonify({'success': False, 'error': 'URL does not appear to be a PDF'}), 400

            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                tmp_file.write(r.content)
                tmp_path = tmp_file.name
                should_delete_tmp = True
                source = 'url'

        # 3) pdf_path (server-side in repo root)
        elif 'pdf_path' in payload and payload['pdf_path']:
            user_path_raw = str(payload['pdf_path']).strip()
            user_path = Path(user_path_raw)

            # If relative, treat as relative to SAFE_BASE_DIR (repo root)
            if not user_path.is_absolute():
                user_path = (SAFE_BASE_DIR / user_path).resolve()

            # Enforce .pdf and safe containment
            if user_path.suffix.lower() != '.pdf':
                return jsonify({'success': False, 'error': 'pdf_path must point to a .pdf file'}), 400
            if not is_safe_path(SAFE_BASE_DIR, user_path):
                return jsonify({'success': False, 'error': 'pdf_path is outside the allowed directory'}), 400
            if not user_path.exists():
                return jsonify({'success': False, 'error': f'pdf_path not found: {user_path.name}'}), 400

            tmp_path = str(user_path)
            should_delete_tmp = False
            source = 'path'

        else:
            return jsonify({
                'success': False,
                'error': 'No input provided. Use multipart "file", or "file_url", or "pdf_path".'
            }), 400

        # Process PDF
        ocr_instance = SimpleOCR(language=language)
        result = ocr_instance.pdf_to_text(tmp_path, max_pages=max_pages, dpi=dpi)

        # Cleanup if we created a temp file
        if should_delete_tmp and tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        if result['success']:
            return jsonify({
                'success': True,
                'source': source,
                'text': result['text'],
                'num_pages': result['num_pages'],
                'was_truncated': result['was_truncated'],
                'character_count': len(result['text'])
            }), 200
        else:
            return jsonify({'success': False, 'error': result['error']}), 500

    except Exception as e:
        if should_delete_tmp and tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/ocr/image', methods=['POST'])
def ocr_image():
    """Process image file with OCR (PNG/JPG/JPEG)"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, JPEG'}), 400

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp_file:
            file.save(tmp_file.name)
            tmp_path = tmp_file.name

        language = request.form.get('language', 'eng')
        ocr_instance = SimpleOCR(language=language)
        result = ocr_instance.image_to_text(tmp_path)

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if result['success']:
            return jsonify({
                'success': True,
                'text': result['text'],
                'character_count': len(result['text'])
            }), 200
        else:
            return jsonify({'success': False, 'error': result['error']}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    # For local development
    app.run(debug=True, host='0.0.0.0', port=5000)
    # In production, Render uses gunicorn to load `app`
