"""Small editorial question bank; answers never come from user input or AI."""
QUESTIONS = [
    ("verb_form", "I didn't ___ him yesterday.", ["saw", "see", "seen"], 1, "După didn't folosim forma de bază: see."),
    ("verb_tense", "I've ___ here for five years.", ["live", "lived", "living"], 1, "Cu have folosim participiul: lived. Acțiunea a început în trecut și continuă."),
    ("article", "She's ___ engineer.", ["a", "an", "the"], 1, "Înaintea unui sunet vocalic folosim an: an engineer."),
    ("preposition", "I'm interested ___ music.", ["on", "at", "in"], 2, "În engleză spunem interested in, nu interested on."),
    ("conditional", "If I ___ more money, I would travel.", ["had", "would have", "have"], 0, "Pentru o situație imaginară folosim if + trecut: if I had."),
    ("collocation", "I ___ a photo yesterday.", ["made", "took", "did"], 1, "În engleză spunem take a photo, chiar dacă în română spunem a face o fotografie."),
    ("romanian_transfer", "I ___ 48 years old.", ["have", "am", "do"], 1, "Vârsta se exprimă cu to be: I am 48 years old."),
    ("subject_verb_agreement", "She ___ to work by bus.", ["go", "going", "goes"], 2, "Cu she, la prezent, adăugăm -s: she goes."),
    ("spelling", "Which spelling is correct?", ["definately", "definitely", "definitly"], 1, "Forma corectă este definitely."),
    ("word_order", "Which question is correct?", ["Where you live?", "Where do you live?", "Where live you?"], 1, "La întrebări folosim do înaintea subiectului: Where do you live?"),
    ("punctuation", "Which sentence is punctuated correctly?", ["Lets eat, Anna.", "Let's eat Anna.", "Let's eat, Anna."], 2, "Let's are apostrof, iar virgula separă numele persoanei căreia îi vorbim."),
    ("vocabulary", "Can I ___ your pen for a moment?", ["borrow", "lend", "learn"], 0, "Borrow înseamnă a lua cu împrumut; lend înseamnă a da cu împrumut."),
    ("british_english", "Which spelling is the usual British preference?", ["color", "colour", "colur"], 1, "În engleza britanică preferăm colour. Color este corect în engleza americană."),
    ("other", "Which sentence is correct?", ["I'm agree.", "I agree.", "I am agreement."], 1, "Agree este deja verb: spunem I agree, fără am."),
]
