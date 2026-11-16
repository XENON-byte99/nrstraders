"""Serve every third-party asset from the device instead of a CDN.

The templates load Tailwind, Font Awesome, Google Fonts and a few scripts from
CDNs. That is fine for the website, but the mobile app has to work with no
internet at all. Rather than fork the templates, this middleware rewrites the
CDN URLs in each HTML response to local copies under ``/static/vendor/``.

``mobile/build_offline_assets.py`` produces those copies from this same table,
so adding a row here is all it takes to make a new CDN asset work offline.
"""
import re

VENDOR_URL = '/static/vendor/'

# (URL prefix used in templates, npm package, directory inside the package,
#  local directory under static/vendor/, file suffixes worth copying)
NPM_ASSETS = [
    ('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/',
     '@fortawesome/fontawesome-free@6.4.0', '', 'fontawesome-6.4.0/', ('css/all.min.css', '.woff2')),
    ('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/',
     '@fortawesome/fontawesome-free@5.15.4', '', 'fontawesome-5.15.4/', ('css/all.min.css', '.woff2')),
    ('https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/',
     'html2pdf.js@0.10.1', 'dist/', 'html2pdf-0.10.1/', ('html2pdf.bundle.min.js',)),
    ('https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/',
     'xlsx@0.18.5', 'dist/', 'xlsx-0.18.5/', ('xlsx.full.min.js',)),
    ('https://cdn.jsdelivr.net/npm/sortablejs@latest/',
     'sortablejs@1.15.6', '', 'sortablejs/', ('Sortable.min.js',)),
    ('https://cdn.jsdelivr.net/npm/lucide@0.263.0/dist/umd/',
     'lucide@0.263.0', 'dist/umd/', 'lucide-0.263.0/', ('lucide.min.js',)),
]

TAILWIND_VERSION = '3.4.17'
TAILWIND_CSS = VENDOR_URL + 'tailwind.css'
FONTS_CSS = VENDOR_URL + 'fonts/fonts.css'

# Category colour themes are assembled at render time ("bg-{{ colour }}-100"),
# so the Tailwind build cannot discover them by scanning templates.
TAILWIND_SAFELIST_COLOURS = ['gray', 'blue', 'purple', 'green', 'red', 'yellow', 'indigo', 'pink', 'orange', 'teal']

_TAILWIND_SCRIPT = re.compile(r'<script\s+src="https://cdn\.tailwindcss\.com[^"]*"\s*>\s*</script>')
GOOGLE_FONTS_LINK = re.compile(r'https://fonts\.googleapis\.com/css2?\?[^"\'\s>]+')
_PRECONNECT = re.compile(r'<link\s+rel="preconnect"[^>]*>\s*')


def rewrite_html(html):
    html = _TAILWIND_SCRIPT.sub('<link rel="stylesheet" href="%s">' % TAILWIND_CSS, html)
    html = GOOGLE_FONTS_LINK.sub(FONTS_CSS, html)
    html = _PRECONNECT.sub('', html)
    for prefix, _pkg, _src, local, _keep in NPM_ASSETS:
        html = html.replace(prefix, VENDOR_URL + local)
    return html


class OfflineAssetsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (not getattr(response, 'streaming', False)
                and response.get('Content-Type', '').startswith('text/html')
                and b'https://' in response.content):
            charset = response.charset or 'utf-8'
            response.content = rewrite_html(response.content.decode(charset)).encode(charset)
            if response.has_header('Content-Length'):
                response['Content-Length'] = str(len(response.content))
        return response
