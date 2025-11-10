"""
Render.com Ready OCR API
Flask web service for PDF OCR processing
"""

from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os
from pathlib import Path
from PIL import Image
import pytesseract
from pdf2image import convert_from_path
import tempfile

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Allowed file extensions
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


class SimpleOCR:
    """Lightweight OCR class for PDF to text conversion"""
    
    def __init__(self, language='eng'):
        """Initialize OCR with language"""
        self.language = language
    
    def pdf_to_text(self, pdf_path, max_pages=10, dpi=200):
        """
        Convert PDF to text using OCR
        
        Args:
            pdf_path (str): Path to PDF file
            max_pages (int): Maximum pages to process
            dpi (int): Image quality for OCR
            
        Returns:
            dict: Processing results
        """
        try:
            # Convert PDF pages to images (poppler auto-detected on Linux)
            images = convert_from_path(
                pdf_path,
                dpi=dpi,
                first_page=1,
                last_page=max_pages
            )
            
            num_pages = len(images)
            was_truncated = num_pages >= max_pages
            
            # Extract text from each page
            all_text = []
            for i, image in enumerate(images, 1):
                text = pytesseract.image_to_string(image, lang=self.language)
                all_text.append(text)
            
            # Combine all text
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
        'version': '1.0',
        'endpoints': {
            '/': 'API documentation (GET)',
            '/ocr/pdf': 'Process PDF file (POST)',
            '/ocr/image': 'Process image file (POST)',
            '/health': 'Health check (GET)'
        },
        'usage': {
            'pdf': 'POST multipart/form-data with "file" field containing PDF',
            'image': 'POST multipart/form-data with "file" field containing image'
        },
        'limits': {
            'max_file_size': '16MB',
            'max_pages': 10,
            'allowed_formats': list(ALLOWED_EXTENSIONS)
        }
    })


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200


@app.route('/ocr/pdf', methods=['POST'])
def ocr_pdf():
    """Process PDF file with OCR"""
    
    # Check if file is present
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    # Check if file is selected
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file extension
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: PDF'}), 400
    
    # Check if it's actually a PDF
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'File must be a PDF'}), 400
    
    try:
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            file.save(tmp_file.name)
            tmp_path = tmp_file.name
        
        # Get optional parameters
        max_pages = int(request.form.get('max_pages', 10))
        dpi = int(request.form.get('dpi', 200))
        language = request.form.get('language', 'eng')
        
        # Limit parameters for safety
        max_pages = min(max_pages, 20)
        dpi = min(dpi, 300)
        
        # Create OCR instance with specified language
        ocr_instance = SimpleOCR(language=language)
        
        # Process PDF
        result = ocr_instance.pdf_to_text(tmp_path, max_pages=max_pages, dpi=dpi)
        
        # Clean up temporary file
        os.unlink(tmp_path)
        
        if result['success']:
            return jsonify({
                'success': True,
                'text': result['text'],
                'num_pages': result['num_pages'],
                'was_truncated': result['was_truncated'],
                'character_count': len(result['text'])
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            }), 500
            
    except Exception as e:
        # Clean up on error
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except:
                pass
        
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/ocr/image', methods=['POST'])
def ocr_image():
    """Process image file with OCR"""
    
    # Check if file is present
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    # Check if file is selected
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file extension
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, JPEG'}), 400
    
    try:
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp_file:
            file.save(tmp_file.name)
            tmp_path = tmp_file.name
        
        # Get optional language parameter
        language = request.form.get('language', 'eng')
        
        # Create OCR instance with specified language
        ocr_instance = SimpleOCR(language=language)
        
        # Process image
        result = ocr_instance.image_to_text(tmp_path)
        
        # Clean up temporary file
        os.unlink(tmp_path)
        
        if result['success']:
            return jsonify({
                'success': True,
                'text': result['text'],
                'character_count': len(result['text'])
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            }), 500
            
    except Exception as e:
        # Clean up on error
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except:
                pass
        
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # For local development
    app.run(debug=True, host='0.0.0.0', port=5000)
    
    # For production (Render uses gunicorn)
    # gunicorn will automatically use this app
