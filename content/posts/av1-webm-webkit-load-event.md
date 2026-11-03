---
title: An AV1 WebM source can hold WebKit's load event for three seconds
date: 2026-10-29
slug: av1-webm-webkit-load-event
description: An AV1 WebM source in a video element can hold WebKit's window load event for three seconds on every iPhone browser. How I found it, and the fix.
---

This affects Safari, Chrome for iOS and every other browser on an iPhone, because on iOS they all run WebKit.

The home page of [VerifiedReply](https://verifiedreply.io/), the WooCommerce reviews plugin I founded, rendered instantly on an iPhone, then sat there with the progress bar stuck at about a quarter for another three seconds. On desktop Chrome it was done in half a second. The server was quick, every asset was small and cached, and nothing on the page was render blocking.

The cause was one line of HTML: a `<source>` pointing at an AV1 WebM file, inside a `<video>` that was set to `preload="none"` and never played until the visitor scrolled to it.

This post covers what WebKit does with that source, how I found it, and the small piece of JavaScript that fixes it.

## The markup

A short looping product video (on VerifiedReply it's the [one-minute introduction](https://verifiedreply.io/watch/what-verifiedreply-is/)), with a modern encode first and a fallback second. This is the pattern most guides recommend:

```html
<video muted playsinline loop preload="none" poster="reel.webp" width="390" height="844">
  <source src="reel.webm" type='video/webm; codecs="av01.0.05M.08"'>
  <source src="reel.mp4" type='video/mp4; codecs="avc1.640028"'>
</video>
```

The WebM is AV1 and about a third of the size of the H.264 MP4 (285 KB against 853 KB in this case). Browsers take the first source they can play, so anything with AV1 support gets the smaller file. `preload="none"` is supposed to mean nothing happens until the video is asked to play.

## What actually happens in WebKit

The page's `DOMContentLoaded` fires on time. The window `load` event fires about three seconds later. Between the two, the network is idle and no JavaScript runs.

That three second wait shows up in more than the progress bar. Anything hung off `load` waits too: analytics that load late on purpose, autoplay that waits for the page to settle, lazy work scheduled for "after load".

It affects every browser on iOS, because they all have to use WebKit. Safari, Chrome for iOS and Brave on iOS all showed it. In WebKit on macOS, using Playwright's build, I measured the same delay, so it isn't only a phone problem. Chrome on desktop, which uses Blink, has no gap at all, which is why it hid for so long.

## Why the load event waits

The HTML spec gives every media element a *delaying-the-load-event flag*. It is set while the element runs its resource selection algorithm, the step where it walks its `<source>` children to choose one, and the document's `load` event can't fire while any element has it set. `preload="none"` doesn't skip resource selection. It only stops the browser fetching media data once a source is chosen.

So the `<video>` is allowed to hold `load` while it decides which source to use. Normally that takes a few milliseconds. With an AV1 WebM source, WebKit took about three seconds every time, on every reload, before it settled.

I haven't traced this through WebKit's source, so I can't say exactly what it spends the time on. It looks like the cost of getting the AV1 decoding path ready. The measurements below are what I can stand behind.

## Finding it

Safari's Web Inspector (connect the phone by USB, then Safari's Develop menu, then the device) showed the shape of it:

- **Network**: everything finished by about 450 ms, then nothing until the first request hung off `load`, at about 3.4 s.
- **Timelines**: the DOMContentLoaded marker at about 2.0 s from the start of the recording, the load marker at about 5.4 s, and no network or script activity in between. Only animation frames.

The Network tab also showed a cluster of unnamed SVG and PNG requests initiated from the line with the `<video>`. Those are Safari's built-in media controls drawing themselves, and they were the clue that pointed at the video.

To prove it, I loaded the page in Playwright's WebKit with an iPhone profile and rewrote the HTML on its way in, removing one thing at a time. These are the timings of `DOMContentLoaded` and `load` in milliseconds:

| Variant | DOMContentLoaded | load |
|---|---:|---:|
| Page as served | 366 | 3359 |
| Both `<video>` elements removed | 303 | 340 |
| `<source>` elements removed | 352 | 394 |
| `controls` removed | 316 | 3313 |
| All scripts removed | 180 | 3345 |
| All inline SVG removed | 358 | 3356 |
| MP4 source only | 346 | 381 |
| WebM source only | 367 | 3348 |
| `type` attributes removed | 373 | 3355 |
| MP4 listed first | 338 | 373 |

The WebM source alone costs three seconds. The MP4 alone costs nothing. The `type` attribute with its codecs string makes no difference, and neither do the controls, the scripts or the inline SVG. One video with a WebM source is enough to cause it.

The rewrite is a few lines of Playwright, and is a quick way to bisect any page:

```python
import re
from playwright.sync_api import sync_playwright

URL = 'https://example.com/'

variants = {
    'as served': lambda h: h,
    'no sources': lambda h: re.sub(r'<source[^>]*>', '', h),
    'mp4 only': lambda h: re.sub(r'<source[^>]*webm[^>]*>', '', h),
}

with sync_playwright() as p:
    browser = p.webkit.launch()
    for name, rewrite in variants.items():
        context = browser.new_context(**p.devices['iPhone 15'])
        page = context.new_page()

        def handle(route, rewrite=rewrite):
            response = route.fetch()
            route.fulfill(response=response, body=rewrite(response.text()))

        page.route(URL, lambda route: handle(route))
        page.goto(URL, wait_until='load')
        dcl, load = page.evaluate("""() => {
            const n = performance.getEntriesByType('navigation')[0];
            return [Math.round(n.domContentLoadedEventEnd), Math.round(n.loadEventStart)];
        }""")
        print(f'{name:12} DOMContentLoaded={dcl} load={load}')
        context.close()
    browser.close()
```

`pip install playwright` and `playwright install webkit` gets you the engine. It runs on macOS, Linux and Windows.

## Three ways to fix it

**Put the MP4 first.** WebKit takes the first source it can play, never looks at the WebM, and the delay goes. It's a two-line change. The cost is that Blink and Gecko also take the first playable source, so every browser now downloads the bigger H.264 file. Here that was about three times the bytes on every play.

**Drop the WebM.** The same result, tidier markup, and the same cost.

**Leave the sources out of the HTML and add them when the video is about to play.** With no `<source>` children there is nothing to select, the media element never holds `load`, and every browser still gets the smallest file it can play. This is the one I went with.

## The fix

Move `src` and `type` into data attributes, so the browser sees sources it can't use yet:

```html
<video id="reel" muted playsinline loop preload="none" poster="reel.webp" width="390" height="844">
  <source data-src="reel.webm" data-type='video/webm; codecs="av01.0.05M.08"'>
  <source data-src="reel.mp4" data-type='video/mp4; codecs="avc1.640028"'>
</video>
```

A `<source>` with no `src` fails resource selection straight away, and the element goes back to waiting quietly without delaying anything.

Then fill them in when the video comes into view, call `load()` so the element runs resource selection again, and play:

```js
(function () {
  var video = document.getElementById('reel');
  if (!video || !('IntersectionObserver' in window)) return;

  var still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var ready = false;

  function addSources() {
    if (ready) return;
    ready = true;
    video.querySelectorAll('source[data-src]').forEach(function (s) {
      s.type = s.getAttribute('data-type');
      s.src = s.getAttribute('data-src');
    });
    video.load();
  }

  function start() {
    new IntersectionObserver(function (entries) {
      if (!entries[0].isIntersecting) {
        video.pause();
        return;
      }
      if (still) return;
      addSources();
      video.play().catch(function () {});
    }, { threshold: 0.5 }).observe(video);
  }

  if (document.readyState === 'complete') start();
  else window.addEventListener('load', start);
})();
```

A few details that matter:

- **Set `type` before `src`.** The browser re-reads the sources when `load()` runs, and setting them in this order means each one is complete when it's checked.
- **`load()` resets `currentTime` to 0.** Add the sources before you seek. If you have buttons that jump to a point in the video, call `addSources()` first and set `currentTime` after.
- **Waiting for `load` is deliberate.** Adding the sources earlier, at script run time, puts the delay straight back, because the page hasn't finished loading yet.
- **Reduced motion and data saver get no video at all.** Nothing is fetched until the visitor presses play, if you give them a button. Add `navigator.connection && navigator.connection.saveData` to the `still` check if you want to respect data saver too.
- **No JavaScript means no video.** A visitor with scripts off sees the poster. If that matters, add a `<noscript>` link to the MP4 next to the element.

## The result

Measured in WebKit with an iPhone profile against the live page, before and after:

| | DOMContentLoaded | load |
|---|---:|---:|
| Sources in the HTML | about 290 ms | about 3350 ms |
| Sources added on play | about 290 ms | about 350 ms |

The video still plays the AV1 file in every browser that supports it, the progress bar finishes as soon as the page's own files are in, and anything waiting on `load` now runs three seconds earlier.

## Checking your own pages

If you serve AV1 WebM in a `<source>`, run this in Safari's Web Inspector console after a reload:

```js
const n = performance.getEntriesByType('navigation')[0];
console.log('DOMContentLoaded', Math.round(n.domContentLoadedEventEnd), 'load', Math.round(n.loadEventStart));
```

A gap of several seconds between the two numbers, with nothing in the Network tab to explain it, is the sign. Remove the WebM source and reload. If the gap goes, this is your problem.
