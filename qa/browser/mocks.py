"""Shared text fixtures and browser stand-ins for the microphone, WebRTC and audio playback."""
ACTION = "Vreau să sune natural!"
ROMANIAN = "Nu cred că ajung la muncă înainte de nouă."
BRITISH = "I don't think I'll get to work before nine."
UNNATURAL = "I want to ask you if you can help me with a thing."

# Browser stand-ins for the microphone, the permission state and the WebRTC connection to OpenAI: tests play the
# provider's transcript events and read the moments the page muted the microphone and committed the audio.
REALTIME_MOCKS = """(() => {
  const rt = (window.__rt = { gum: 0, tracksStopped: 0, sent: [], channel: null, pc: null, pcClosed: false,
                              permission: "prompt", gumDelay: 0, gumError: null, sessionRequests: [] });
  if (!navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: {} });
  navigator.mediaDevices.getUserMedia = async () => {
    rt.gum += 1;
    if (rt.gumDelay) await new Promise((resolve) => setTimeout(resolve, rt.gumDelay));
    rt.gumAt = performance.now();
    if (rt.gumError) throw new DOMException("mock", rt.gumError);
    const track = { kind: "audio", live: true, stop() { rt.tracksStopped += 1; },
      get enabled() { return this.live; },
      set enabled(value) { if (!value && this.live) rt.mutedAt = performance.now(); this.live = value; } };
    return { getTracks: () => [track], getAudioTracks: () => [track] };
  };
  const permissions = navigator.permissions;
  Object.defineProperty(navigator, "permissions", { configurable: true, value: {
    query: async (descriptor) => descriptor && descriptor.name === "microphone"
      ? { state: rt.permission } : permissions.query(descriptor) } });
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (url.includes("realtime-transcription/session")) rt.sessionRequests.push(performance.now());
    return nativeFetch(input, init);
  };
  class FakeChannel extends EventTarget {
    constructor() { super(); this.readyState = "connecting"; }
    send(data) {
      const event = JSON.parse(data);
      if (event.type === "input_audio_buffer.commit") rt.commitAt = performance.now();
      rt.sent.push(event);
    }
    close() { this.readyState = "closed"; }
  }
  class FakePeerConnection extends EventTarget {
    constructor() { super(); this.connectionState = "new"; rt.pc = this; }
    addTrack() {}
    createDataChannel() { rt.channel = new FakeChannel(); return rt.channel; }
    async createOffer() { return { type: "offer", sdp: "v=0 fake-offer" }; }
    async setLocalDescription() {}
    async setRemoteDescription() { if (!rt.holdOpen) setTimeout(rt.open, 50); }
    close() { this.connectionState = "closed"; rt.pcClosed = true; }
  }
  window.RTCPeerConnection = FakePeerConnection;
  rt.open = () => { rt.channel.readyState = "open"; rt.channel.dispatchEvent(new Event("open")); };
  rt.emit = (event) => rt.channel.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(event) }));
  rt.drop = () => { rt.pc.connectionState = "failed"; rt.pc.dispatchEvent(new Event("connectionstatechange")); };
  class FakeRecorder extends EventTarget {
    static isTypeSupported(type) { return type.startsWith("audio/webm"); }
    constructor(stream, options) { super(); this.mimeType = options.mimeType; this.state = "inactive"; }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      const data = new Blob([new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0, 0, 0, 0])], { type: this.mimeType });
      this.dispatchEvent(Object.assign(new Event("dataavailable"), { data }));
      this.dispatchEvent(new Event("stop"));
    }
  }
  window.MediaRecorder = FakeRecorder;
})();"""
DELTA, COMPLETED = "conversation.item.input_audio_transcription.delta", "conversation.item.input_audio_transcription.completed"
# Homepage examples: text before the literal phrase, the literal phrase, its natural British English, text after
# (as in static/js/example-prompts.js).
EXAMPLES = [("", "I hurt my head","I've got a headache", ", so I'm staying in tonight."),
            ("Sorry, ", "I have delayed with ten minutes", "I'm ten minutes late", "."),
            ("Can you ", "make us a photo", "take a photo of us", "?"),
            ("", "It depends of you what we make", "It's up to you what we do", " this weekend."),
            ("", "I finally took the driving exam", "I finally passed my driving test", "!")]
TYPED = [before + wrong + after for before, wrong, _, after in EXAMPLES]
CORRECTED = [f"{before}{wrong} {right}{after}" for before, wrong, right, after in EXAMPLES]

# Browser stand-ins for the microphone, MediaRecorder and audio playback, so no hardware or paid call is needed.
MEDIA_MOCKS = """(() => {
  const track = { stop() { window.__tracksStopped = (window.__tracksStopped || 0) + 1; } };
  if (!navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: {} });
  navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [track] });
  class FakeRecorder extends EventTarget {
    static isTypeSupported(type) { return type.startsWith("audio/webm"); }
    constructor(stream, options) { super(); this.mimeType = options.mimeType; this.state = "inactive"; }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      const data = new Blob([new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0, 0, 0, 0])], { type: this.mimeType });
      this.dispatchEvent(Object.assign(new Event("dataavailable"), { data }));
      this.dispatchEvent(new Event("stop"));
    }
  }
  window.MediaRecorder = FakeRecorder;
  // Keep the fake MP3 bytes from being decoded, which would fire a real media error.
  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    configurable: true, get() { return this.__src || ""; }, set(value) { this.__src = value; },
  });
  HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  HTMLMediaElement.prototype.pause = function () { this.dispatchEvent(new Event("pause")); };
})();"""
