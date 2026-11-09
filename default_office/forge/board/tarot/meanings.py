"""Each card's meaning, from Waite's text (The Pictorial Key to the Tarot, 1910, as reprinted in L. W. De Laurence's
The Illustrated Key to the Tarot, 1918: Project Gutenberg #43548): its picture as Waite describes it, its divinatory
meanings upright and reversed. argv[1]: the Gutenberg text; argv[2]: the JSON to write."""
import json, re, sys

text = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n")
def tidy(s):
    s = re.sub(r"\[Illustration[^\]]*\]", "", s)
    s = s.replace("_", "").replace("--", "—")
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(" ,;")
NUM = {w: i for i, w in enumerate("ZERO ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN FOURTEEN FIFTEEN SIXTEEN SEVENTEEN EIGHTEEN NINETEEN TWENTY TWENTY-ONE".split())}
NAMES = ["The Fool", "The Magician", "The High Priestess", "The Empress", "The Emperor", "The Hierophant", "The Lovers", "The Chariot", "Strength",
         "The Hermit", "Wheel of Fortune", "Justice", "The Hanged Man", "Death", "Temperance", "The Devil", "The Tower", "The Star", "The Moon", "The Sun",
         "Judgement", "The World"]
cards = {}
# the majors' pictures: Part II, section 2, "ONE. THE MAGICIAN" ... (each heading on a line of its own): the first paragraph
start, end = text.index("THE TRUMPS MAJOR AND THEIR INNER SYMBOLISM\n"), text.index("CONCLUSION AS TO THE GREATER KEYS")
part = text[start:end]
heads = list(re.finditer(r"^(ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|THIRTEEN|FOURTEEN|FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|NINETEEN|TWENTY-ONE|TWENTY)\. [A-Z ,]+$", part, re.M))
for i, h in enumerate(heads):
    body = part[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(part)]
    paras = [tidy(p) for p in re.split(r"\n\s*\n", body) if tidy(p)]
    pic = paras[0] if paras else ""
    if len(pic) < 160 and len(paras) > 1:
        pic += " " + paras[1]
    cards["m%02d" % NUM[h.group(1)]] = {"name": NAMES[NUM[h.group(1)]], "picture": pic}
# the majors' meanings: Part III, section 3: "1. _The Magician._--... _Reversed_: ..."
start = text.index("THE GREATER ARCANA AND THEIR DIVINATORY MEANINGS\n")
block = text[start: text.index("It will be seen that", start)]
for m in re.finditer(r"(\d+|_Zero\._)\.?\s+_([^_]+)\._—?-*(.*?)_Reversed_:(.*?)(?=\n\s*\n|\Z)", block.replace("--", "—"), re.S):
    n = 0 if "Zero" in m.group(1) else int(m.group(1))
    cards.setdefault("m%02d" % n, {"name": NAMES[n], "picture": ""}).update(upright=tidy(m.group(3)), reversed=tidy(m.group(4)))
# the minors: Part III, section 2: "THE SUIT OF WANDS. KING." / "CUPS. QUEEN." ... each to the next card
RANK = {r: i + 1 for i, r in enumerate("ACE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN PAGE KNIGHT QUEEN KING".split())}
SUIT = {"WANDS": "w", "CUPS": "c", "SWORDS": "s", "PENTACLES": "p"}
start = text.index("THE SUIT OF WANDS. KING.")
stop = text.index("THE GREATER ARCANA AND THEIR DIVINATORY MEANINGS\n")
minor = text[start:stop]
heads = list(re.finditer(r"^(?:THE SUIT OF )?(WANDS|CUPS|SWORDS|PENTACLES)\. (ACE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|PAGE|KNIGHT|QUEEN|KING)\.\s*$", minor, re.M))
for i, h in enumerate(heads):
    body = tidy(minor[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(minor)])
    body = re.split(r"\b(?:THE SUIT OF )?(?:WANDS|CUPS|SWORDS|PENTACLES)\b\s*$", body)[0]
    pic, _, rest = body.partition("Divinatory Meanings:")
    up, _, rev = rest.partition("Reversed:")
    key = "%s%02d" % (SUIT[h.group(1)], RANK[h.group(2)])
    rank, suit = h.group(2).title(), h.group(1).title()
    cards[key] = {"name": "%s of %s" % (rank, suit), "picture": tidy(pic), "upright": tidy(up), "reversed": tidy(rev)}
order = ["m%02d" % i for i in range(22)] + ["%s%02d" % (s, r) for s in "wcsp" for r in range(1, 15)]
missing = [k for k in order if not all(cards.get(k, {}).get(f) for f in ("picture", "upright", "reversed"))]
out = {"source": "A. E. Waite, The Pictorial Key to the Tarot (1910), as reprinted in L. W. De Laurence, The Illustrated Key to the Tarot "
                  "(1918): public domain; Project Gutenberg eBook #43548, https://www.gutenberg.org/ebooks/43548",
       "cards": {k: cards[k] for k in order if k in cards}}
json.dump(out, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print(len(out["cards"]), "cards;", "incomplete:", missing or "none")
