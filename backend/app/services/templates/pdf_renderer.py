from __future__ import annotations

import os

def render_pdf_from_html(html: str) -> bytes:
    """Render PDF bytes from HTML using WeasyPrint."""
    try:
        from weasyprint import HTML, CSS
        from weasyprint.text.fonts import FontConfiguration
    except Exception as e:
        raise RuntimeError("WeasyPrint is not installed or unavailable") from e

    # Ensure UTF-8 meta and Korean-friendly font fallback.
    wrapped_html = html or ""
    if "<html" not in wrapped_html.lower():
        wrapped_html = f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"></head>
<body>{wrapped_html}</body>
</html>"""

    font_face_rules: list[str] = []
    font_candidates = [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "truetype"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf", "opentype"),
        ("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf", "truetype"),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "truetype"),
    ]
    for font_path, font_fmt in font_candidates:
        if os.path.exists(font_path):
            font_face_rules.append(
                "@font-face {"
                "font-family: 'KoreanFallbackLocal';"
                f"src: url('file://{font_path}') format('{font_fmt}');"
                "}"
            )

    font_config = FontConfiguration()
    css = CSS(
        string=(
            f"{''.join(font_face_rules)}\n"
            "html, body, p, h1, h2, h3, h4, h5, h6, li, td, th, span, div, pre {"
            "font-family: 'KoreanFallbackLocal', 'Noto Sans CJK KR', 'Noto Sans KR', 'NanumGothic', 'Malgun Gothic', sans-serif !important;"
            "word-break: keep-all;"
            "}"
        ),
        font_config=font_config,
    )
    return HTML(string=wrapped_html).write_pdf(stylesheets=[css], font_config=font_config)
