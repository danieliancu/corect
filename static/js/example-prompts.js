"use strict";
// Example sentences in the empty homepage text box. Each is typed as a Romanian sentence translated word for word, then
// the literal phrase is struck through in red and its natural British English typed beside it in green. Everything
// happens in a decorative layer drawn over the textarea, never in its value, so examples can't be submitted, counted or
// mixed into a voice transcript.
(() => {
  // Text before the literal phrase, the literal phrase, its natural British English, text after.
  const EXAMPLES = [
    ["", "I hurt my head","I've got a headache", ", so I'm staying in tonight."], // Mă doare capul
    ["Sorry, ", "I have delayed with ten minutes", "I'm ten minutes late", "."], // Am întârziat cu zece minute
    ["Can you ", "make us a photo", "take a photo of us", "?"], // Ne faci o poză?
    ["", "It depends of you what we make", "It's up to you what we do", " this weekend."], // Depinde de tine ce facem
    ["", "I finally took the driving exam", "I finally passed my driving test", "!"], // Am luat examenul de conducere
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
  // bottom padding that holds the microphone and the counter. On a short box that reservation would swallow most of
  // the room, cutting a sentence off mid-line, so it is given up as far as needed to keep three lines visible.
  // Enlarging the layer never moves the text, which stays at the top: it only lets more of it show.
  const LINES_ALWAYS_VISIBLE = 4; // What the longest example needs on a narrow screen.

  function align() {
    const style = getComputedStyle(textarea);
    const px = (name) => parseFloat(style[name]) || 0;
    const lineHeight = px("lineHeight") || px("fontSize") * 1.5;
    const roomForLines = px("paddingTop") + LINES_ALWAYS_VISIBLE * lineHeight;
    const reserved = Math.min(px("paddingBottom") + px("borderBottomWidth"),
                              Math.max(0, textarea.clientHeight - roomForLines));
    Object.assign(layer.style, {
      top: `${px("borderTopWidth")}px`,
      left: `${px("borderLeftWidth")}px`,
      right: `${px("borderRightWidth")}px`,
      bottom: `${reserved}px`,
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
