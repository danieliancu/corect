"use strict";
// Example sentences in the empty homepage text box. Each is typed with a typical learner mistake, then the mistake is
// struck through in red and the correction typed beside it in green. Everything happens in a decorative layer drawn
// over the textarea, never in its value, so examples can't be submitted, counted or mixed into a voice transcript.
(() => {
  // Text before the mistake, the mistake, its correction, text after.
  const EXAMPLES = [
    ["I'm running a bit late, but I should be ", "their", "there", " in ten minutes."],
    ["Do you fancy ", "grab", "grabbing", " a coffee after work?"],
    ["Could you give me a ", "hands", "hand", " with this?"],
    ["What ", "is", "are", " you up to this weekend?"],
    ["I'll give you a call when I ", "got", "get", " home."],
  ];
  const START_MS = 850;
  const MARK_MS = 650; // Pause with the sentence typed before the mistake is struck through.
  const FIX_MS = 350; // Pause after striking through, before the correction is typed.
  const HOLD_MS = 2200;
  const BETWEEN_MS = 550;
  const RESUME_MS = 2000;
  const STATIC_CHECK_MS = 400;
  const typeDelay = () => 35 + Math.random() * 20;
  const eraseDelay = () => 18 + Math.random() * 12;

  const textarea = document.getElementById("text");
  const layer = document.querySelector("[data-example-prompt]");
  if (!textarea || !layer) return;
  const mic = document.querySelector("[data-voice-record]");
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  let timer = null;
  let index = 0;
  let formBusy = false;

  // Only a genuinely empty, unused text box shows examples: not focused, not recording or transcribing, not submitting.
  const canShow = () =>
    textarea.value === "" &&
    document.activeElement !== textarea &&
    (mic?.dataset.voiceState || "idle") === "idle" &&
    !formBusy;

  // The layer covers exactly the textarea's content box, so the sentence wraps like real input and stops above the
  // bottom padding that holds the microphone and the counter.
  function align() {
    const style = getComputedStyle(textarea);
    const px = (name) => parseFloat(style[name]) || 0;
    Object.assign(layer.style, {
      top: `${px("borderTopWidth")}px`,
      left: `${px("borderLeftWidth")}px`,
      right: `${px("borderRightWidth")}px`,
      bottom: `${px("paddingBottom") + px("borderBottomWidth")}px`,
      padding: `${style.paddingTop} ${style.paddingRight} 0 ${style.paddingLeft}`,
      fontFamily: style.fontFamily,
      fontSize: style.fontSize,
      fontWeight: style.fontWeight,
      lineHeight: style.lineHeight,
      letterSpacing: style.letterSpacing,
    });
  }

  function pause() {
    clearTimeout(timer);
    timer = null;
    layer.hidden = true;
    layer.replaceChildren();
  }

  // Draws one frame: the visible part of each piece, the mistake struck through once marked, the correction in green.
  function render([before, wrong, right, after], shown, marked) {
    const pieces = [
      [before.slice(0, shown.before), ""],
      [wrong.slice(0, shown.wrong), marked ? "example-wrong" : ""],
      [` ${right}`.slice(0, shown.right), "example-right"],
      [after.slice(0, shown.after), ""],
    ];
    layer.replaceChildren(...pieces.filter(([text]) => text).map(([text, className]) => {
      const span = document.createElement("span");
      if (className) span.className = className;
      span.textContent = text;
      return span;
    }));
    layer.hidden = false;
  }

  // Every frame of one example with the delay after it: type the sentence, strike the mistake, type the correction,
  // hold, then erase from the end.
  function frames([before, wrong, right, after]) {
    const list = [];
    const shown = { before: 0, wrong: 0, right: 0, after: 0 };
    let marked = false;
    const push = (delay) => list.push({ shown: { ...shown }, marked, delay });
    const grow = (key, length) => {
      while (shown[key] < length) {
        shown[key] += 1;
        push(typeDelay());
      }
    };
    const shrink = (key) => {
      while (shown[key] > 0) {
        shown[key] -= 1;
        push(eraseDelay());
      }
    };
    grow("before", before.length);
    grow("wrong", wrong.length);
    grow("after", after.length);
    list[list.length - 1].delay = MARK_MS;
    marked = true;
    push(FIX_MS);
    grow("right", right.length + 1);
    list[list.length - 1].delay = HOLD_MS;
    ["after", "right", "wrong", "before"].forEach(shrink);
    list[list.length - 1].delay = BETWEEN_MS;
    return list;
  }

  // Plays frames in order; every frame checks the real text box first.
  function play(list, position) {
    if (!canShow()) return pause();
    const frame = list[position];
    render(EXAMPLES[index], frame.shown, frame.marked);
    timer = setTimeout(() => {
      if (position + 1 < list.length) return play(list, position + 1);
      index = (index + 1) % EXAMPLES.length;
      play(frames(EXAMPLES[index]), 0);
    }, frame.delay);
  }

  // Reduced motion: the first example, already corrected, shown still and checked now and then so real text always wins.
  function showStatic() {
    if (!canShow()) return pause();
    const [before, wrong, right, after] = EXAMPLES[0];
    render(EXAMPLES[0], { before: before.length, wrong: wrong.length, right: right.length + 1, after: after.length }, true);
    timer = setTimeout(showStatic, STATIC_CHECK_MS);
  }

  function resume(delay = RESUME_MS) {
    if (!layer.hidden && canShow()) return; // Already showing: never restart mid-sentence.
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!canShow()) return pause();
      align();
      if (reducedMotion?.matches) showStatic();
      else play(frames(EXAMPLES[index]), 0);
    }, delay);
  }

  textarea.addEventListener("focus", pause);
  textarea.addEventListener("pointerdown", pause);
  textarea.addEventListener("input", () => (textarea.value ? pause() : resume()));
  textarea.addEventListener("blur", () => resume());
  // voice.js announces every microphone state; examples stay hidden unless it is idle.
  document.addEventListener("voice-state", (event) => (event.detail?.state === "idle" ? resume() : pause()));
  document.body.addEventListener("htmx:beforeRequest", (event) => {
    if (event.defaultPrevented || !textarea.form?.contains(event.detail.elt)) return;
    formBusy = true;
    pause();
  });
  document.body.addEventListener("htmx:afterRequest", (event) => {
    if (!textarea.form?.contains(event.detail.elt)) return;
    formBusy = false;
    resume();
  });
  window.addEventListener("resize", align);
  if (window.ResizeObserver) new ResizeObserver(align).observe(textarea);
  window.addEventListener("pageshow", () => resume(START_MS));
  reducedMotion?.addEventListener?.("change", () => {
    pause();
    resume(START_MS);
  });

  textarea.removeAttribute("placeholder"); // The examples take the placeholder's place when JavaScript runs.
  resume(START_MS);
  window.CorectExamples = { pause, resume };
})();
