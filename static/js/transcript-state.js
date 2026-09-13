"use strict";
// Live transcript state for the text box. The box always shows: text before the caret + the spoken transcript + text
// after the caret. Provider deltas extend one item; a completed transcript replaces that item's region, so revisions
// never duplicate words and nothing is appended to the box blindly.
(() => {
  const ATTACHES_LEFT = /^[.,!?;:)\]}%…»”’]/; // Punctuation that needs no space before it.
  const count = (text) => Array.from(text).length;

  function create({ before = "", after = "", maxLength = Infinity } = {}) {
    const items = new Map(); // provider item_id -> { text, final }
    const order = [];

    const item = (id) => {
      if (!items.has(id)) {
        items.set(id, { text: "", final: false });
        order.push(id);
      }
      return items.get(id);
    };

    function spoken(overrides = {}) {
      return order
        .map((id) => (id in overrides ? overrides[id] : items.get(id).text).replace(/\s+/g, " ").trim())
        .filter(Boolean)
        .join(" ")
        .replace(/ (?=[.,!?;:])/g, "");
    }

    function compose(speech) {
      if (!speech) return { text: before + after, caret: before.length };
      const lead = before && !/\s$/.test(before) && !ATTACHES_LEFT.test(speech) ? " " : "";
      const trail = after && !/^\s/.test(after) && !ATTACHES_LEFT.test(after) ? " " : "";
      const head = before + lead + speech;
      return { text: head + trail + after, caret: head.length };
    }

    const fits = (overrides) => count(compose(spoken(overrides)).text) <= maxLength;

    return {
      // Newly available text for one item. "limit" means it did not fit: nothing changes except dropping a word
      // fragment the rejected delta would have finished, so no word is ever left cut in half.
      applyDelta(id, delta) {
        const current = item(id);
        if (current.final || typeof delta !== "string" || !delta) return "ok";
        const next = current.text + delta;
        if (fits({ [id]: next })) {
          current.text = next;
          return "ok";
        }
        if (!/^[\s.,!?;:]/.test(delta)) current.text = current.text.replace(/\S+$/, "");
        return "limit";
      },
      // The final transcript for one item replaces whatever its deltas built. If it would not fit, the delta text stays.
      complete(id, transcript) {
        const current = item(id);
        current.final = true;
        if (typeof transcript !== "string") return "ok";
        if (fits({ [id]: transcript })) {
          current.text = transcript;
          return "ok";
        }
        return "limit";
      },
      // Orders a committed item after the item the provider says came before it.
      place(id, previousId) {
        item(id);
        if (!previousId || !items.has(previousId)) return;
        order.splice(order.indexOf(id), 1);
        order.splice(order.indexOf(previousId) + 1, 0, id);
      },
      has: (id) => items.has(id),
      spoken: () => spoken(),
      snapshot: () => compose(spoken()),
    };
  }

  window.CorectTranscript = { create };
})();
