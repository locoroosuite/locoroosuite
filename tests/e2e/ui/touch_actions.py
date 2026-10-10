"""Touch gesture helpers for e2e UI tests (HLD UX3g/UX3h).

Playwright's touchscreen API only supports tap; swipe and long-press are
synthesized by dispatching TouchEvent sequences on the element. The app
attaches delegated listeners on the list container, so the synthetic
events must bubble.
"""

_SWIPE_JS = """
([selector, x0, x1, y, steps]) => {
  const el = document.querySelector(selector);
  if (!el) throw new Error('element not found: ' + selector);
  const mk = (x) => new Touch({ identifier: 1, target: el, clientX: x, clientY: y });
  const fire = (type, touch) => el.dispatchEvent(new TouchEvent(type, {
    bubbles: true,
    cancelable: true,
    touches: [touch],
    targetTouches: [touch],
    changedTouches: [touch]
  }));
  return new Promise((resolve, reject) => {
    try {
      fire('touchstart', mk(x0));
      let i = 0;
      const step = () => {
        i += 1;
        const x = x0 + (x1 - x0) * (i / steps);
        fire('touchmove', mk(x));
        if (i < steps) {
          setTimeout(step, 16);
        } else {
          setTimeout(() => { fire('touchend', mk(x1)); resolve(true); }, 16);
        }
      };
      setTimeout(step, 30);
    } catch (err) {
      reject(String(err));
    }
  });
}
"""

_LONG_PRESS_JS = """
([selector, ms]) => {
  const el = document.querySelector(selector);
  if (!el) throw new Error('element not found: ' + selector);
  const mk = () => new Touch({ identifier: 1, target: el, clientX: 0, clientY: 0 });
  const fire = (type) => el.dispatchEvent(new TouchEvent(type, {
    bubbles: true,
    cancelable: true,
    touches: [mk()],
    targetTouches: [mk()],
    changedTouches: [mk()]
  }));
  return new Promise((resolve, reject) => {
    try {
      fire('touchstart');
      setTimeout(() => { fire('touchend'); resolve(true); }, ms);
    } catch (err) {
      reject(String(err));
    }
  });
}
"""


def swipe_element(page, selector, dx, steps=6):
    """Swipe horizontally on the element's vertical center by dx pixels
    (negative = leftward, revealing the action panel per UX3g)."""
    el = page.query_selector(selector)
    if el is None:
        raise AssertionError(f"element not found for swipe: {selector}")
    box = el.bounding_box()
    if box is None:
        raise AssertionError(f"element has no box: {selector}")
    y = box["y"] + box["height"] / 2
    x0 = box["x"] + box["width"] / 2
    page.evaluate(_SWIPE_JS, [selector, x0, x0 + dx, y, steps])


def tap_element(page, selector):
    """Tap an element's center via the touchscreen API.

    Use this (not page.click) for revealed swipe-panel buttons: Chrome
    retargets the click a tap produces to the row, bypassing the panel
    button handlers; the app's touchend fast-path (js/mail/swipe.js)
    activates the button for real touch sequences only."""
    el = page.query_selector(selector)
    if el is None:
        raise AssertionError(f"element not found for tap: {selector}")
    box = el.bounding_box()
    if box is None:
        raise AssertionError(f"element has no box: {selector}")
    page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def wait_list_settled(page, quiet_ms=1200, timeout=15000):
    """Wait until the mail message list stops mutating.

    The folder view's load-time SSE sync replaces the whole list once the
    background sync completes; a row revealed by swipe (or measured for
    geometry assertions) right before that loses its state / detaches its
    handles when the DOM node is replaced. Interactions and measurements
    must wait for the list to go quiet first."""
    page.wait_for_function(
        """(quiet) => {
          const list = document.getElementById('message-list') || document.getElementById('search-results');
          if (!list) return true;
          if (!window.__lrLastListMut) {
            window.__lrLastListMut = Date.now();
            const obs = new MutationObserver(() => { window.__lrLastListMut = Date.now(); });
            obs.observe(list, { childList: true, subtree: true, attributes: true });
          }
          return Date.now() - window.__lrLastListMut >= quiet;
        }""",
        arg=quiet_ms,
        timeout=timeout,
    )


def long_press_element(page, selector, ms=650):
    """Press-and-hold on the element for ms milliseconds (UX3h selection)."""
    el = page.query_selector(selector)
    if el is None:
        raise AssertionError(f"element not found for long-press: {selector}")
    page.evaluate(_LONG_PRESS_JS, [selector, ms])


_EDGE_SWIPE_JS = """
([x0, x1, y, selector]) => {
  const el = document.querySelector(selector) || document.body;
  const mk = (x) => new Touch({ identifier: 1, target: el, clientX: x, clientY: y });
  const fire = (type, touch) => el.dispatchEvent(new TouchEvent(type, {
    bubbles: true,
    cancelable: true,
    touches: [touch],
    targetTouches: [touch],
    changedTouches: [touch]
  }));
  return new Promise((resolve, reject) => {
    try {
      fire('touchstart', mk(x0));
      let i = 0;
      const steps = 8;
      const step = () => {
        i += 1;
        const x = x0 + (x1 - x0) * (i / steps);
        fire('touchmove', mk(x));
        if (i < steps) {
          setTimeout(step, 16);
        } else {
          setTimeout(() => { fire('touchend', mk(x1)); resolve(true); }, 16);
        }
      };
      setTimeout(step, 30);
    } catch (err) {
      reject(String(err));
    }
  });
}
"""


def edge_swipe(page, selector, x0, x1, y):
    """Drag horizontally from x0 to x1 at viewport y, dispatching a touch
    sequence on selector (U24.2a edge-swipe drawer gestures). The events
    bubble to document where the shared drawer engine listens."""
    el = page.query_selector(selector)
    if el is None:
        raise AssertionError(f"element not found for edge swipe: {selector}")
    page.evaluate(_EDGE_SWIPE_JS, [x0, x1, y, selector])
