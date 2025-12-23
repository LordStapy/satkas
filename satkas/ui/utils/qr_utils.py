"""QR code generation utilities."""

import logging
from io import BytesIO

import qrcode
from kivy.core.image import Image as CoreImage

# Suppress PIL debug/info logging
logging.getLogger('PIL').setLevel(logging.WARNING)


def make_qr(payload):
    """
    Generate a QR code from the given payload and return a Kivy texture.
    
    Args:
        payload (str): The data to encode in the QR code
        
    Returns:
        Texture: A Kivy texture containing the QR code image
        
    Example:
        >>> texture = make_qr("kaspa:address123456789")
        >>> image_widget.texture = texture
    """
    # Create QR code instance
    qr = qrcode.QRCode(
        version=1,  # Auto-size
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,  # Size of each box in pixels
        border=2,  # Border size in boxes
    )
    
    # Add data and generate
    qr.add_data(payload)
    qr.make(fit=True)
    
    # Generate PIL image
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to BytesIO (in-memory file)
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    # Create Kivy texture from BytesIO
    core_image = CoreImage(buf, ext='png')
    
    return core_image.texture

