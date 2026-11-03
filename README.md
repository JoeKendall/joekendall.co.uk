# joekendall.co.uk

The source of [joekendall.co.uk](https://joekendall.co.uk/), my personal site.

Plain HTML and CSS, built by a small Python script. No framework, no npm, no bundler. A few lines of inline JavaScript let the rules draw in on hover, and the site works the same without them.

## Layout

```
content/posts/*.md          posts, Markdown with front matter
content/linkedin-posts.json LinkedIn posts to list under Writing
templates/*.html            page templates ($name placeholders)
static/                     copied into the build as it is: CSS, font, images, icons, .htaccess, robots.txt
bin/build.py                renders everything into public/
```

## Build

```
python3 bin/build.py
```

Python 3, standard library only. The finished site is written to `public/`, ready to upload.

## Adding a post

Create `content/posts/<slug>.md`:

```
---
title: The post's title
date: 2026-11-20
slug: the-posts-slug
description: One or two sentences for search results and sharing.
---

The post, in Markdown.
```

Headings, paragraphs, lists, fenced code, tables, inline code, bold, italic and links are supported. Run the build and the post gets its own page at `/writing/<slug>/`, and appears on the home page, the writing page, the sitemap, the RSS feed and `llms.txt`.

## Adding a LinkedIn post

LinkedIn has no public feed for a person's posts, so the list is kept by hand in `content/linkedin-posts.json`:

```json
[
  {
    "date": "2026-11-20",
    "title": "The first line of the post",
    "excerpt": "A sentence or two of what I wrote.",
    "url": "https://www.linkedin.com/posts/..."
  }
]
```

## Type

Archivo by Omnibus-Type, subset and self-hosted, under the SIL Open Font License (`static/assets/fonts/OFL.txt`).
