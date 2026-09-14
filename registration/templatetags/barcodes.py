"""
Adds Django template tags to help render barcodes in templates.

As with any custom template tags, add `{% load barcodes %}` to the top of your
template to use these tags.
"""

from base64 import b64encode
from io import BytesIO
from xml.etree import ElementTree as ET

import qrcode
from django import template
from django.utils.html import mark_safe
from pdf417 import encode, render_image, render_svg
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import CircleModuleDrawer

register = template.Library()

@register.simple_tag
def pdf417_svg(*args, **kwargs) -> str:
    """
    Generates an SVG of a PDF417 barcode generated from a list of strings.

    :param args: A list of strings to concatenate with a given *delimiter*.
        Non-string values will be coerced into strings.
    :param columns: Keyword argument only. How many columns are in the
        generated barcode. Affects the width of the barcode. Defaults to 6.
    :param security_level: Keyword argument only. How many error checks are
        added into the barcode to prevent corruption. Affects the width of the
        barcode. Defaults to 2.
    :param delimiter: Keyword argument only. The string used to join the list
        of strings provided in *args*. Defaults to a newline (``"\\n"``).
    :param class_name: Keyword argument only. The literal contents of the
        `class` attribute to add to the root `<svg>` element

    :returns: A string containing the entire SVG document tree.

    Example 1:

    .. code-block:: html
        {% load barcodes %}
        <!-- Renders a barcode with name/address delimited by newlines -->
        {% pdf417_svg name address1 address2 city state zip %}

    Example 2:

    .. code-block:: html
        {% load barcodes %}
        <!--
            Renders a barcode with badge number, name, and level delimited by
            tab stops
        -->
        {% pdf417_svg badge.number badge.name badge.level delimiter="\\t" %}
    """

    columns = kwargs.get("columns", 6)
    security_level = kwargs.get("security_level", 2)
    delimiter = kwargs.get("delimiter", "\n")
    class_list = kwargs.get("class_list")
    codes = encode(delimiter.join(str(x) for x in args), columns, security_level)
    svg = render_svg(codes)
    if (class_list):
        svg.getroot().set("class", class_list)
    return mark_safe(ET.tostring(svg.getroot(), "unicode"))

@register.simple_tag
def pdf417_data_uri(*args, **kwargs) -> str:
    """
    Generates a data URI for a PDF417 barcode generated from a list of strings.

    :param args: A list of strings to concatenate with a given *delimiter*.
        Non-string values will be coerced into strings.
    :param columns: Keyword argument only. How many columns are in the
        generated barcode. Affects the width of the barcode. Defaults to 6.
    :param security_level: Keyword argument only. How many error checks are
        added into the barcode to prevent corruption. Affects the width of the
        barcode. Defaults to 2.
    :param delimiter: Keyword argument only. The string used to join the list
        of strings provided in *args*. Defaults to a newline (``"\\n"``).

    :returns: A base64 data URI representing a PNG image.

    Example 1:

    .. code-block:: html
        {% load barcodes %}
        <!-- Renders a barcode with name/address delimited by newlines -->
        <img src="{% pdf417_data_uri name address1 address2 city state zip %}">

    Example 2:

    .. code-block:: html
        {% load barcodes %}
        <!--
            Renders a barcode with badge number, name, and level delimited by
            tab stops
        -->
        <img src="{% pdf417_data_uri badge.number badge.name badge.level delimiter="\\t" %}">
    """

    columns = kwargs.get("columns", 6)
    security_level = kwargs.get("security_level", 2)
    delimiter = kwargs.get("delimiter", "\n")
    codes = encode(delimiter.join(str(x) for x in args), columns, security_level)
    image = render_image(codes, 1, 1, 0)
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    b64 = b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"

@register.simple_tag
def qr_data_uri(data: str, circles = False) -> str:
    """
    Generates a data URI for a QR code containing arbitrary text data.

    :param data: The text to encode in the QR code.
    :param circles: Render the QR code blocks as circles?

    :returns: A base64 data URI representing a PNG image.

    Example:

    .. code-block:: html
        {% load barcodes %}
        <img src="{% qr_data_uri "https://example.com/" %}">
    """

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        image_factory=StyledPilImage
    )
    qr.add_data(data)
    mk_img_kwargs = {}
    if circles:
        mk_img_kwargs["module_drawer"] = CircleModuleDrawer()
    buffered = BytesIO()
    image = qr.make_image(**mk_img_kwargs)
    image.save(buffered, format="PNG")
    b64 = b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"
