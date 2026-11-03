#!/opt/homebrew/bin/python3
"""Build joekendall.co.uk into public/.

Standard library only. Run from anywhere:

    python3 bin/build.py

Reads content/posts/*.md, content/linkedin-posts.json and templates/*.html,
copies static/ as it is, and writes the finished site to public/. Upload the
contents of public/ to the docroot.
"""

import datetime as dt
import hashlib
import html
import json
import math
import re
import shutil
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / 'content'
TEMPLATES = ROOT / 'templates'
STATIC = ROOT / 'static'
OUT = ROOT / 'public'
ORIGIN = 'https://joekendall.co.uk/'

LINKEDIN = 'https://www.linkedin.com/in/joe-kendall-0118ab30/'
GITHUB = 'https://github.com/JoeKendall'

DESCRIPTION = ('Joe Kendall is a developer in Manchester and the founder of VerifiedReply. '
               'Full stack TypeScript, and WordPress and WooCommerce sites at Holler Marketing.')

WORDS_PER_MINUTE = 220
HOME_POSTS = 5


def esc(s):
    return html.escape(str(s), quote=True)


def tpl(name):
    return string.Template((TEMPLATES / name).read_text(encoding='utf-8'))


# ---------------------------------------------------------------- Markdown
# Covers what the posts use: headings, paragraphs, fenced code, tables,
# lists, and inline code, bold, italic and links. Nothing else.

INLINE_CODE = re.compile(r'`([^`]+)`')
LINK = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
BOLD = re.compile(r'\*\*(.+?)\*\*')
ITALIC = re.compile(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])')


def inline(text):
    """Render inline Markdown. Code spans are lifted out first so nothing inside them changes."""
    codes = []

    def keep(m):
        codes.append('<code>' + esc(m.group(1)) + '</code>')
        return '\x00%d\x00' % (len(codes) - 1)

    text = INLINE_CODE.sub(keep, text)
    text = esc(text).replace('&#x27;', '’')
    text = LINK.sub(lambda m: '<a href="%s">%s</a>' % (m.group(2), m.group(1)), text)
    text = BOLD.sub(r'<strong>\1</strong>', text)
    text = ITALIC.sub(r'<em>\1</em>', text)
    text = smart_quotes(text)
    return re.sub('\x00(\\d+)\x00', lambda m: codes[int(m.group(1))], text)


def smart_quotes(text):
    """Curl straight quotes in prose, outside tags."""
    parts = re.split(r'(<[^>]+>)', text)
    for i, part in enumerate(parts):
        if part.startswith('<'):
            continue
        part = re.sub(r'&quot;(?=\w)', '“', part)
        part = part.replace('&quot;', '”')
        parts[i] = part
    return ''.join(parts)


def table(lines):
    rows = [[c.strip() for c in l.strip().strip('|').split('|')] for l in lines]
    head, align, body = rows[0], rows[1], rows[2:]
    right = [a.endswith(':') and not a.startswith(':') for a in align]

    def cell(tag, text, i):
        cls = ' class="r"' if right[i] else ''
        scope = ' scope="col"' if tag == 'th' else ''
        return '<%s%s%s>%s</%s>' % (tag, cls, scope, inline(text), tag)

    out = ['<div class="table"><table>', '<thead><tr>' + ''.join(cell('th', h, i) for i, h in enumerate(head)) + '</tr></thead>', '<tbody>']
    for r in body:
        out.append('<tr>' + ''.join(cell('td', c, i) for i, c in enumerate(r)) + '</tr>')
    out.append('</tbody></table></div>')
    return '\n'.join(out)


def markdown(src):
    lines = src.split('\n')
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith('```'):
            lang = line[3:].strip()
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i])
                i += 1
            i += 1
            cls = ' class="language-%s"' % esc(lang) if lang else ''
            out.append('<pre><code%s>%s</code></pre>' % (cls, esc('\n'.join(code))))
            continue
        m = re.match(r'(#{2,4})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            slug = re.sub(r'[^a-z0-9]+', '-', re.sub(r'`', '', text.lower())).strip('-')
            out.append('<h%d id="%s">%s</h%d>' % (level, slug, inline(text), level))
            i += 1
            continue
        if line.lstrip().startswith('|'):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                block.append(lines[i])
                i += 1
            out.append(table(block))
            continue
        if re.match(r'\s*[-*]\s+', line) or re.match(r'\s*\d+\.\s+', line):
            ordered = bool(re.match(r'\s*\d+\.\s+', line))
            items = []
            while i < len(lines) and lines[i].strip():
                l = lines[i]
                m = re.match(r'\s*(?:[-*]|\d+\.)\s+(.*)', l)
                if m:
                    items.append(m.group(1))
                elif items:
                    items[-1] += ' ' + l.strip()
                i += 1
            tag = 'ol' if ordered else 'ul'
            out.append('<%s>\n%s\n</%s>' % (tag, '\n'.join('<li>%s</li>' % inline(t) for t in items), tag))
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(('```', '#', '|')) \
                and not re.match(r'\s*(?:[-*]|\d+\.)\s+', lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append('<p>%s</p>' % inline(' '.join(para)))
    return '\n'.join(out)


# ---------------------------------------------------------------- Content

def front_matter(text):
    m = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if not m:
        raise ValueError('missing front matter')
    meta = {}
    for line in m.group(1).split('\n'):
        k, _, v = line.partition(':')
        meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def long_date(d):
    return '%d %s %d' % (d.day, d.strftime('%B'), d.year)


def short_date(d):
    return '%d %s %d' % (d.day, d.strftime('%b'), d.year)


def load_posts():
    posts = []
    for path in sorted((CONTENT / 'posts').glob('*.md')):
        meta, body = front_matter(path.read_text(encoding='utf-8'))
        for key in ('title', 'date', 'slug', 'description'):
            if not meta.get(key):
                sys.exit('%s: front matter needs %s' % (path.name, key))
        words = len(re.findall(r'\w+', body))
        posts.append({
            'title': meta['title'],
            'date': dt.date.fromisoformat(meta['date']),
            'slug': meta['slug'],
            'description': meta['description'],
            'html': markdown(body),
            'read': max(1, math.ceil(words / WORDS_PER_MINUTE)),
            'source': path,
        })
    posts.sort(key=lambda p: p['date'], reverse=True)
    return posts


def load_linkedin():
    path = CONTENT / 'linkedin-posts.json'
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding='utf-8'))
    out = []
    for n, item in enumerate(items):
        for key in ('date', 'title', 'excerpt', 'url'):
            if not item.get(key):
                sys.exit('linkedin-posts.json entry %d needs %s' % (n + 1, key))
        if not item['url'].startswith('https://www.linkedin.com/'):
            sys.exit('linkedin-posts.json entry %d: url must be a linkedin.com link' % (n + 1))
        out.append({**item, 'date': dt.date.fromisoformat(item['date'])})
    return out


# ---------------------------------------------------------------- Rendering

def photo(up, sizes, widths=(200, 400, 600), cls=''):
    """A <picture> of the square headshot, AVIF then WebP then JPEG."""
    def srcset(ext):
        return ', '.join('%sassets/img/joe-kendall-%d.%s %dw' % (up, w, ext, w) for w in widths)
    return ('<picture><source type="image/avif" srcset="%s" sizes="%s">'
            '<source type="image/webp" srcset="%s" sizes="%s">'
            '<img src="%sassets/img/joe-kendall-%d.jpg" srcset="%s" sizes="%s" width="600" height="600" '
            'alt="Joe Kendall"%s decoding="async"></picture>') % (
        srcset('avif'), sizes, srcset('webp'), sizes, up, widths[1], srcset('jpg'), sizes, cls)


def writing_rows(up, posts, linkedin, limit=None, indent='      '):
    entries = [('post', p['date'], p) for p in posts] + [('li', l['date'], l) for l in linkedin]
    entries.sort(key=lambda e: e[1], reverse=True)
    if limit:
        entries = entries[:limit]
    rows = []
    for kind, d, e in entries:
        date = '<time datetime="%s">%s</time>' % (d.isoformat(), short_date(d))
        if kind == 'post':
            rows.append('%s<li><span class="d">%s</span><span class="t"><a href="%swriting/%s/">%s</a></span><span class="m">%d min</span></li>'
                        % (indent, date, up, e['slug'], inline(e['title']), e['read']))
        else:
            rows.append('%s<li><span class="d">%s</span><span class="t"><a href="%s">%s</a><small>%s</small></span><span class="m">LinkedIn</span></li>'
                        % (indent, date, esc(e['url']), esc(e['title']), esc(e['excerpt'])))
    return '\n'.join(rows)


def nav(up, current):
    items = [('Now', up + '#now'), ('Writing', up + 'writing/'), ('Elsewhere', up + '#elsewhere')]
    return ''.join('<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if label == current else '', label)
                   for label, href in items)


def person():
    return {
        '@type': 'Person',
        '@id': ORIGIN + '#joe',
        'name': 'Joe Kendall',
        'url': ORIGIN,
        'image': ORIGIN + 'assets/img/joe-kendall-600.jpg',
        'jobTitle': 'Founder of VerifiedReply',
        'homeLocation': {'@type': 'Place', 'name': 'Manchester, England'},
        'worksFor': [
            {'@id': 'https://verifiedreply.io/#org'},
            {'@type': 'Organization', 'name': 'Holler Marketing', 'url': 'https://hollermarketing.co.uk/'},
        ],
        'knowsAbout': ['TypeScript', 'Svelte', 'Node.js', 'WordPress', 'WooCommerce', 'Web performance'],
        'sameAs': [LINKEDIN, GITHUB, 'https://verifiedreply.io/about-contact/#joe'],
    }


def org_verifiedreply():
    return {
        '@type': 'Organization',
        '@id': 'https://verifiedreply.io/#org',
        'name': 'VerifiedReply',
        'url': 'https://verifiedreply.io/',
        'founder': {'@id': ORIGIN + '#joe'},
    }


def website():
    return {
        '@type': 'WebSite',
        '@id': ORIGIN + '#website',
        'url': ORIGIN,
        'name': 'Joe Kendall',
        'inLanguage': 'en-GB',
        'publisher': {'@id': ORIGIN + '#joe'},
    }


def jsonld(graph):
    return json.dumps({'@context': 'https://schema.org', '@graph': graph}, indent=2, ensure_ascii=False).replace('</', '<\\/')


def page(up, body, *, title, description, url, og_title=None, og_type='website', graph, current=None, canonical=True, version):
    return tpl('base.html').substitute(
        up=up,
        v=version,
        title=esc(title),
        og_title=esc(og_title or title),
        description=esc(description),
        url=esc(url),
        og_type=og_type,
        canonical='<link rel="canonical" href="%s">' % esc(url) if canonical else '<meta name="robots" content="noindex">',
        jsonld=jsonld(graph),
        nav=nav(up, current),
        home_current=' aria-current="page"' if current == 'Home' else '',
        body=body,
    )


def write(rel, text):
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def asset_version():
    h = hashlib.sha256()
    for p in sorted((STATIC / 'assets').rglob('*')):
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def build():
    posts = load_posts()
    linkedin = load_linkedin()
    version = asset_version()
    newest = max([p['date'] for p in posts] + [dt.date(2026, 11, 3)])

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(STATIC, OUT)

    # Home
    body = tpl('home.html').substitute(
        photo=photo('', '(max-width: 60rem) 25vw, 210px').replace('decoding="async"', 'fetchpriority="high"'),
        writing_rows=writing_rows('', posts, linkedin, HOME_POSTS),
    )
    graph = [
        {
            '@type': 'ProfilePage',
            '@id': ORIGIN + '#webpage',
            'url': ORIGIN,
            'name': 'Joe Kendall',
            'description': DESCRIPTION,
            'inLanguage': 'en-GB',
            'isPartOf': {'@id': ORIGIN + '#website'},
            'mainEntity': {'@id': ORIGIN + '#joe'},
            'dateModified': newest.isoformat(),
        },
        person(), org_verifiedreply(), website(),
    ]
    write('index.html', page('', body, title='Joe Kendall: developer in Manchester, founder of VerifiedReply',
                             og_title='Joe Kendall', description=DESCRIPTION, url=ORIGIN, graph=graph,
                             current='Home', version=version))

    # Writing index
    url = ORIGIN + 'writing/'
    body = tpl('writing.html').substitute(writing_rows=writing_rows('../', posts, linkedin, indent='    '))
    graph = [
        {'@type': 'CollectionPage', '@id': url + '#webpage', 'url': url, 'name': 'Writing',
         'inLanguage': 'en-GB', 'isPartOf': {'@id': ORIGIN + '#website'}, 'author': {'@id': ORIGIN + '#joe'}},
        {'@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Joe Kendall', 'item': ORIGIN},
            {'@type': 'ListItem', 'position': 2, 'name': 'Writing', 'item': url}]},
        person(), website(),
    ]
    write('writing/index.html', page('../', body, title='Writing by Joe Kendall',
                                     description='Notes from building and fixing websites, with the measurements. By Joe Kendall.',
                                     url=url, graph=graph, current='Writing', version=version))

    # Posts
    for post in posts:
        url = ORIGIN + 'writing/%s/' % post['slug']
        others = [p for p in posts if p is not post][:3]
        more = writing_rows('../../', others, [], indent='    ') if others else ''
        body = tpl('post.html').substitute(
            up='../../',
            title=inline(post['title']),
            date_iso=post['date'].isoformat(),
            date_long=long_date(post['date']),
            read_time=post['read'],
            content=post['html'],
            byline_photo=photo('../../', '64px', widths=(200, 400, 600)).replace('alt="Joe Kendall"', 'alt="Joe Kendall" loading="lazy"'),
            more_rows=more,
        )
        graph = [
            {'@type': 'BlogPosting', '@id': url + '#post', 'url': url, 'mainEntityOfPage': url,
             'headline': post['title'], 'description': post['description'],
             'datePublished': post['date'].isoformat(), 'dateModified': post['date'].isoformat(),
             'inLanguage': 'en-GB', 'author': {'@id': ORIGIN + '#joe'}, 'publisher': {'@id': ORIGIN + '#joe'},
             'image': ORIGIN + 'og-image.jpg', 'isPartOf': {'@id': ORIGIN + '#website'}},
            {'@type': 'BreadcrumbList', 'itemListElement': [
                {'@type': 'ListItem', 'position': 1, 'name': 'Joe Kendall', 'item': ORIGIN},
                {'@type': 'ListItem', 'position': 2, 'name': 'Writing', 'item': ORIGIN + 'writing/'},
                {'@type': 'ListItem', 'position': 3, 'name': post['title'], 'item': url}]},
            person(), org_verifiedreply(), website(),
        ]
        write('writing/%s/index.html' % post['slug'],
              page('../../', body, title=post['title'], description=post['description'], url=url,
                   og_type='article', graph=graph, current='Writing', version=version))

    # 404: served at any depth, so its links are root-relative
    body = tpl('404.html').substitute(up='/')
    write('404.html', page('/', body, title='Not found: Joe Kendall', description='Nothing at this address.',
                           url=ORIGIN + '404.html', graph=[website()], canonical=False, version=version))

    # Sitemap
    urls = [(ORIGIN, newest), (ORIGIN + 'writing/', newest)] + \
           [(ORIGIN + 'writing/%s/' % p['slug'], p['date']) for p in posts]
    write('sitemap.xml', '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
          ''.join('  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n  </url>\n' % (u, d.isoformat()) for u, d in urls) +
          '</urlset>\n')

    # RSS
    items = ''.join(
        '  <item>\n    <title>%s</title>\n    <link>%swriting/%s/</link>\n    <guid>%swriting/%s/</guid>\n'
        '    <pubDate>%s</pubDate>\n    <description>%s</description>\n  </item>\n' % (
            esc(p['title']), ORIGIN, p['slug'], ORIGIN, p['slug'],
            dt.datetime.combine(p['date'], dt.time(9)).strftime('%a, %d %b %Y %H:%M:%S +0000'), esc(p['description']))
        for p in posts)
    write('writing/feed.xml', '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n<channel>\n'
          '  <title>Joe Kendall: writing</title>\n  <link>%swriting/</link>\n'
          '  <description>Notes from building and fixing websites, with the measurements.</description>\n'
          '  <language>en-gb</language>\n%s</channel>\n</rss>\n' % (ORIGIN, items))

    # llms.txt, in the same shape as verifiedreply.io/llms.txt
    lines = [
        '# Joe Kendall', '',
        '> Joe Kendall is a developer in Manchester and the founder of VerifiedReply. He is a full stack '
        'TypeScript developer, working with Svelte and Node on cloud software, and builds WordPress and '
        'WooCommerce sites at Holler Marketing.', '',
        '## Projects', '',
        '- [VerifiedReply](https://verifiedreply.io/): a WordPress plugin that turns a customer\'s email reply '
        'into a review on the shop\'s site, and keeps the original email so anyone can check the words are the '
        'customer\'s own. Joe founded it. The plugin is on WordPress.org: https://wordpress.org/plugins/verifiedreply/',
        '- [Holler Marketing](https://hollermarketing.co.uk/): where Joe builds WordPress and WooCommerce sites for clients.',
        '- [Solid Wood Tops](https://solidwoodtops.co.uk/): Joe\'s own shop, selling made-to-order solid wood desk tops. '
        'Case study: https://verifiedreply.io/case-studies/solid-wood-tops/', '',
        '## Writing', '',
    ]
    lines += ['- [%s](%swriting/%s/): %s Published %s.' % (p['title'], ORIGIN, p['slug'], p['description'], p['date'].isoformat())
              for p in posts]
    lines += ['', '## Elsewhere', '', '- GitHub: ' + GITHUB, '- LinkedIn: ' + LINKEDIN,
              '- Sitemap: ' + ORIGIN + 'sitemap.xml', '']
    write('llms.txt', '\n'.join(lines))

    print('Built %d posts and %d LinkedIn entries into %s' % (len(posts), len(linkedin), OUT.relative_to(ROOT)))


if __name__ == '__main__':
    build()
