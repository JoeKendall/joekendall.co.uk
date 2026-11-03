---
title: Reading the scroll position at load can cause PageSpeed's forced reflow warning
date: 2026-11-03
slug: forced-reflow-scroll-position
description: Why reading scrollY at load triggers PageSpeed's forced reflow warning, why requestAnimationFrame doesn't fix it, and what an unattributed forced reflow really is.
---

PageSpeed Insights flagged a "forced reflow" on the home page of [VerifiedReply](https://verifiedreply.io/), the reviews plugin I founded. It took me three attempts to fix, and the first two were wrong in ways that are easy to repeat. Then the warning came back with no script attached, and that turned out to be Lighthouse miscounting the page's CSS animations. This post covers what the warning means, why my first two fixes didn't work, the fix that did, and what the leftover warning really was.

## What a forced reflow is

A browser batches its work. When a script changes the page, the browser doesn't lay it out again straight away. It waits and does the layout once, just before it next draws the screen.

A forced reflow is what happens when a script asks a layout question in the middle of that wait. How far down the page am I? How tall is this element? Where is it on screen? The browser can't answer from a layout it hasn't done yet, so it stops and does the layout there and then, while your script waits.

Lighthouse reports it under "Forced reflow" with the script, the line and the time it cost. Mine was 56 ms on a simulated mid-range phone. It isn't large, but it lands while the page is still loading, which is when it hurts most.

## The first attempt: fixing the wrong line

The page has a small animated demo whose height is set by JavaScript. It cleared the height, measured an element with `offsetHeight`, then set the height again. That is a textbook forced reflow, so I replaced it with CSS and moved on.

The mistake was that I hadn't seen Lighthouse point at that code. I couldn't get the warning to appear on my own machine, so I searched the scripts for the usual suspects and fixed the first one I found. The change was worth making, but the warning was still there after the deploy, and now Lighthouse named a different line:

```js
if ((window.scrollY || window.pageYOffset || 0) > 80) return;
```

That line runs as soon as the deferred script loads. It decides two small things: whether to send an analytics event when the visitor first scrolls, and when to fade in a video further down. Reading `scrollY` looks harmless, but it is a layout question. A Chrome trace showed it forcing a style recalculation and a layout every time it ran during load.

**Lesson one:** make the warning name a line before you fix anything. It doesn't show on every run, so if it doesn't show for you, run it again (more on that below) rather than guessing.

## The second attempt: waiting a frame

The usual advice is to put layout reads inside `requestAnimationFrame`, so I did:

```js
function start() {
  if ((window.scrollY || window.pageYOffset || 0) > 80) return;
  window.addEventListener('scroll', onScroll, { passive: true });
}
requestAnimationFrame(start);
```

My reasoning was that by the next frame, layout would be done. That is backwards. In each frame the browser runs the `requestAnimationFrame` callbacks first, then works out styles, then layout, then draws. A read inside the callback comes before that frame's style and layout, so if anything has changed since the last frame, the read still forces them.

It looked fixed. Lighthouse flagged nothing on my machine in 21 runs. A trace was more honest: every load still forced a style recalculation, just a smaller one (0.7 to 4 ms), now from the line inside `start()`. On the live site, Lighthouse flagged it again in 2 of 6 runs, at 51 ms in one of them.

**Lesson two:** `requestAnimationFrame` means "before the next frame", not "after layout". It's the right place to write to the page. It isn't a safe place to read layout from.

## The fix: let the browser tell you

The script didn't need the scroll position. It needed to know if the visitor had scrolled more than 80px. `IntersectionObserver` answers that without being asked a layout question, because the browser works out intersections as part of its own layout and calls you afterwards.

Put an invisible 80px strip at the top of the page and watch it. While the strip is on screen, the visitor is within the first 80px. Once it leaves, they've scrolled past.

```js
var marker = document.createElement('div');
var s = marker.style;
s.position = 'absolute';
s.top = '0';
s.left = '0';
s.width = '1px';
s.height = '80px';
s.visibility = 'hidden';
s.pointerEvents = 'none';
marker.setAttribute('aria-hidden', 'true');
document.body.appendChild(marker);

var atLoad = true;
var io = new IntersectionObserver(function (entries) {
  var first = atLoad;
  atLoad = false;
  if (entries[entries.length - 1].isIntersecting) return;
  io.disconnect();
  marker.remove();
  passed(first);   // first is true if the page opened already scrolled
});
io.observe(marker);
```

A few details that matter:

- **The first callback always comes.** An observer reports once as soon as it starts watching, even if nothing has moved. That first report is your answer at load, so you don't need to read `scrollY` at all.
- **The marker has to be positioned against the page.** Check that neither `body` nor `html` is positioned or transformed, or the strip won't start at the top.
- **Disconnect and remove it once it has fired.** It only has one job.
- **Opening the page at an anchor tricks it.** A link to `/#how` makes the browser jump after the page has loaded. The first callback sees the top of the page, then the jump looks like the visitor scrolling. In my tests that fired the "visitor scrolled" event on 8 of 10 anchor loads. If that matters to you, check `location.hash` against an element on the page and treat that load as having started scrolled.

## Proving it

The warning only appears on some runs, which is what made the first two attempts look fine. One clean Lighthouse run proves nothing. Two things made it measurable.

**Swap the script and run Lighthouse many times.** I ran Lighthouse through Puppeteer against my local copy and used request interception to serve either the old or the new script. Everything else was identical, so any difference came from the script.

| Script | Runs flagged |
|---|---:|
| Original, `scrollY` read at load | 4 of 12 |
| `requestAnimationFrame` version | 0 of 12 locally, 2 of 6 live |
| `IntersectionObserver` version | 0 of 12 |

**Take a Chrome trace with the CPU slowed down.** In DevTools, open the Performance panel, set the CPU to 4x slowdown and the network to a slow mobile profile, then record a reload. A forced layout shows as a "Recalculate style" or "Layout" event with a red corner and a warning naming the script. This caught the `requestAnimationFrame` version, which Lighthouse let through on my machine.

| Script | Loads with forced style or layout from the script |
|---|---:|
| `requestAnimationFrame` version | 10 of 10 |
| `IntersectionObserver` version | 0 of 10 |

## The result

Across eight Lighthouse mobile runs on the live site, neither script was flagged. But the warning didn't go away. Some runs still showed a forced reflow, now with the source given as `[unattributed]`, and PageSpeed put it at 67 ms on desktop and 101 ms on mobile.

## The warning that wasn't a forced reflow

"Unattributed" means Lighthouse found layout work it counts as forced but couldn't find the script that asked for it. A trace of the page found no forced layout from any script, so something didn't add up.

The answer was in how Lighthouse decides what counts as forced. It reads the trace as one stream of events from every thread, sorted by time. Any event whose name starts with `v8` counts as JavaScript running, even when it's on a background thread. When Chrome downloads a deferred script, it parses it on a background thread as the bytes arrive, and logs that as a long `v8` event. While that event is open, every style and layout step on the main thread is counted as forced, including the browser's own ordinary work for the next frame.

What filled that window on this page was CSS animation. The page has several animations that loop for ever, and the main one is a slow colour wash across the cards, done by animating custom properties. Animations like that make the browser recalculate styles on every frame. Each recalculation is about half a millisecond, but over a few hundred milliseconds of script download they add up past Lighthouse's 30 ms threshold. None of them has a JavaScript stack, so the total shows as unattributed.

It only appears when a script is still downloading while the animations run, which is why it came and went between runs. To prove it, I slowed only the page's own scripts to 8 KB/s, so they always took a while to arrive, and removed one thing at a time:

| Variant, scripts slowed | Desktop | Mobile |
|---|---:|---:|
| Page as served | 8 of 8 flagged | 8 of 8 flagged |
| Analytics script blocked | 8 of 8 | 8 of 8 |
| Fonts blocked | 8 of 8 | 8 of 8 |
| Videos removed | 3 of 8 | 8 of 8 |
| Inline scripts removed | 8 of 8 | 8 of 8 |
| No JavaScript at all | 0 of 8 | 0 of 8 |
| All CSS animations switched off | 0 of 8 | 0 of 8 |

Removing all the JavaScript cleared it because nothing was downloading. Switching off the animations cleared it because nothing was being recalculated. Nothing else made any difference.

The fix that suits this page is to hold the looping animations until the page has loaded. By then the deferred scripts have arrived, so there's nothing to miscount. One rule pauses them while a class is missing from `<html>`, and a line of script adds the class on `load`:

```css
.vr-js:not(.vr-loaded) .vr-band {
  animation-play-state: paused;
}
```

```js
if (document.readyState === 'complete') document.documentElement.classList.add('vr-loaded');
else addEventListener('load', function () {
  document.documentElement.classList.add('vr-loaded');
});
```

Scoping it to a class that JavaScript adds means a visitor with scripts off never gets stuck with paused animations.

In testing, holding the colour wash and the looping demos cleared the warning on desktop every time. On mobile it brought it down to 19 to 28 ms, under the threshold, but not with much room to spare. What's left comes from smaller animations spread around the page. After it went live, ten Lighthouse runs against the site, six mobile and four desktop, showed no forced reflow at all.

I also tried rebuilding the opening animation (a typing caret and text that inks in) with `transform` and `clip-path`, which Chrome can run off the main thread. It looked the same and made no measurable difference to the warning. The version that redrew the text made the page's largest contentful paint about 0.4 s later, so I didn't keep it.

**Lesson three:** an unattributed forced reflow might not be a forced reflow. If a trace shows no script forcing layout, look at what animates while scripts load.

## Checking your own pages

Search your scripts for layout reads that run at load: `scrollY`, `pageYOffset`, `offsetHeight`, `offsetTop`, `getBoundingClientRect`, `getComputedStyle`, `clientHeight` and `innerHeight`. Each one is worth a look, but don't change any of them until Lighthouse or a trace names the line. When you do, ask what the read is for. Usually the real question is "has this come into view?" or "has the visitor scrolled past this?", and an observer can answer that without forcing anything.

If Lighthouse says `[unattributed]`, look for CSS animations that loop while your scripts are still downloading. Anything that animates colour, custom properties, `left`, `top` or `background-position` costs a style recalculation every frame. Holding those until `load` is cheap, and below the fold nobody sees the difference.

The insight is unscored, so it won't move your PageSpeed score either way. It's worth understanding before you spend a day chasing it.
