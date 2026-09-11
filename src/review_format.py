"""Small, escaped renderer for authored review bullets; no model calls."""
import html
import re
from urllib.parse import urlsplit, quote


def render_points(text: str) -> str:
    out, listing = [], False
    for line in (text or '').splitlines():
        line = line.strip()
        bullet = line.startswith(('- ', '• '))
        if bullet and not listing:
            out.append('<ul class="brief-points">')
            listing = True
        elif not bullet and listing:
            out.append('</ul>')
            listing = False
        if line:
            content = html.escape(line[2:] if bullet else line)
            content = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', content)
            tag = 'li' if bullet else 'p'
            out.append(f'<{tag}>{content}</{tag}>')
    if listing:
        out.append('</ul>')
    return ''.join(out)


def safe_sources(data):
    return [s for s in data.get('sources', [])
            if urlsplit(s.get('url', '')).scheme in ('http', 'https')
            and urlsplit(s['url']).netloc]


def editorial_html(data):
    out = ['<div class="brief-summary">', render_points(data.get('summary', '')), '</div>']
    for section in data.get('sections', []):
        out += ['<section class="brief-section">',
                f'<h3>{html.escape(section["title"])}</h3>',
                render_points(section.get('body', '')), '</section>']
    sources = safe_sources(data)
    if sources:
        out += ['<aside class="brief-evidence"><h3>근거 기사</h3><ol class="brief-sources">']
        for source in sources:
            out.append(f'<li><a href="{html.escape(source["url"], quote=True)}" '
                       f'target="_blank" rel="noopener noreferrer">{html.escape(source["title"])}</a></li>')
        out.append('</ol></aside>')
    return ''.join(out)


def editorial_md(data):
    out = [data.get('summary', '')]
    for section in data.get('sections', []):
        out += ['', '### ' + section['title'], '', section.get('body', '')]
    sources = safe_sources(data)
    if sources:
        out += ['', '### 근거 기사', '']
        for source in sources:
            title = source['title'].replace('[', r'\[').replace(']', r'\]')
            url = quote(source['url'], safe=':/?&=%#@+;,~!$*-._')
            out.append(f'- [{title}]({url})')
    return '\n'.join(out)
