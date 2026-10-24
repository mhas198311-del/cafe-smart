"""
Suggested English names for Arabic menu items, from a glossary of common cafe words.
Only a starting point: the manager reviews every suggestion before it is saved.
"""
import re

# Whole names first (checked before word by word), then single words. Longer keys win.
PHRASES = {
    "قهوة تركية": "Turkish Coffee",
    "قهوة عربية": "Arabic Coffee",
    "قهوة سادة": "Arabic Coffee",
    "قهوة اليوم": "Coffee of the Day",
    "قهوة مختصة": "Specialty Coffee",
    "قهوة مثلجة": "Iced Coffee",
    "قهوة باردة": "Iced Coffee",
    "كولد برو": "Cold Brew",
    "في 60": "V60",
    "في60": "V60",
    "اسبانش لاتيه": "Spanish Latte",
    "سبانش لاتيه": "Spanish Latte",
    "اسبانيش لاتيه": "Spanish Latte",
    "لاتيه اسباني": "Spanish Latte",
    "فلات وايت": "Flat White",
    "كراميل ماكياتو": "Caramel Macchiato",
    "وايت موكا": "White Mocha",
    "هوت شوكلت": "Hot Chocolate",
    "هوت شوكليت": "Hot Chocolate",
    "شوكولاتة ساخنة": "Hot Chocolate",
    "شاي اخضر": "Green Tea",
    "شاي أخضر": "Green Tea",
    "شاي كرك": "Karak Tea",
    "شاي مثلج": "Iced Tea",
    "ايس تي": "Iced Tea",
    "آيس تي": "Iced Tea",
    "ايس لاتيه": "Iced Latte",
    "آيس لاتيه": "Iced Latte",
    "ايس موكا": "Iced Mocha",
    "آيس موكا": "Iced Mocha",
    "ايس امريكانو": "Iced Americano",
    "آيس امريكانو": "Iced Americano",
    "ميلك شيك": "Milkshake",
    "ميلكشيك": "Milkshake",
    "ريد بول": "Red Bull",
    "مياه معدنية": "Mineral Water",
    "ماء": "Water",
    "مي": "Water",
    "عصير برتقال": "Orange Juice",
    "عصير ليمون": "Lemonade",
    "ليمون ونعنع": "Lemon Mint",
    "ليمون نعنع": "Lemon Mint",
    "تشيز كيك": "Cheesecake",
    "براوني": "Brownie",
    "رأس ارجيلة": "Hookah Head",
    "راس ارجيلة": "Hookah Head",
    "راس أرجيلة": "Hookah Head",
    "تغيير راس": "Head Change",
}

WORDS = {
    "قهوة": "Coffee", "اسبريسو": "Espresso", "إسبريسو": "Espresso", "اسبرسو": "Espresso",
    "امريكانو": "Americano", "أمريكانو": "Americano", "لاتيه": "Latte", "لاته": "Latte",
    "كابتشينو": "Cappuccino", "كابوتشينو": "Cappuccino", "كابتشينو": "Cappuccino",
    "موكا": "Mocha", "ماكياتو": "Macchiato", "كورتادو": "Cortado", "افوجاتو": "Affogato",
    "نسكافيه": "Nescafe", "نسكافية": "Nescafe", "فرابيه": "Frappe", "فرابتشينو": "Frappuccino",
    "شاي": "Tea", "اعشاب": "Herbal Tea", "أعشاب": "Herbal Tea", "بابونج": "Chamomile",
    "ميرمية": "Sage", "زهورات": "Herbal Infusion", "يانسون": "Anise", "زنجبيل": "Ginger",
    "سحلب": "Sahlab", "ماتشا": "Matcha", "شوكولاتة": "Chocolate", "شوكلت": "Chocolate",
    "عصير": "Juice", "سموذي": "Smoothie", "موهيتو": "Mojito", "ليموناضة": "Lemonade",
    "برتقال": "Orange", "ليمون": "Lemon", "نعنع": "Mint", "فراولة": "Strawberry",
    "مانجا": "Mango", "مانجو": "Mango", "اناناس": "Pineapple", "أناناس": "Pineapple",
    "بطيخ": "Watermelon", "توت": "Berry", "خوخ": "Peach", "تفاح": "Apple", "موز": "Banana",
    "افوكادو": "Avocado", "أفوكادو": "Avocado", "رمان": "Pomegranate", "جوافة": "Guava",
    "كيوي": "Kiwi", "باشن": "Passion Fruit", "جزر": "Carrot", "كوكتيل": "Cocktail",
    "فانيلا": "Vanilla", "كراميل": "Caramel", "بندق": "Hazelnut", "لوتس": "Lotus",
    "نوتيلا": "Nutella", "اوريو": "Oreo", "أوريو": "Oreo", "بستاشيو": "Pistachio",
    "فستق": "Pistachio", "عسل": "Honey", "قرفة": "Cinnamon", "هيل": "Cardamom",
    "حليب": "Milk", "كريمة": "Cream", "ايس": "Iced", "آيس": "Iced", "مثلج": "Iced",
    "بارد": "Iced", "ساخن": "Hot", "حار": "Hot", "دبل": "Double", "سنجل": "Single",
    "كبير": "Large", "صغير": "Small", "وسط": "Medium", "بدون": "without", "سكر": "Sugar",
    "مع": "with", "و": "&",
    "كيك": "Cake", "كيكة": "Cake", "كوكيز": "Cookies", "كرواسون": "Croissant",
    "كرواسان": "Croissant", "مافن": "Muffin", "وافل": "Waffle", "بان كيك": "Pancake",
    "كريب": "Crepe", "دونات": "Donut", "تيراميسو": "Tiramisu", "بسبوسة": "Basbousa",
    "كنافة": "Knafeh", "ايس كريم": "Ice Cream", "آيس كريم": "Ice Cream", "بوظة": "Ice Cream",
    "ساندويش": "Sandwich", "سندويش": "Sandwich", "توست": "Toast", "جبنة": "Cheese",
    "حلوم": "Halloumi", "دجاج": "Chicken", "تونة": "Tuna", "زعتر": "Zaatar",
    "مناقيش": "Manakish", "معجنات": "Pastries", "بيتزا": "Pizza", "سلطة": "Salad",
    "فطور": "Breakfast", "بطاطا": "Fries", "ناتشوز": "Nachos",
    "ارجيلة": "Hookah", "أرجيلة": "Hookah", "اركيلة": "Hookah", "أركيلة": "Hookah",
    "شيشة": "Shisha", "معسل": "Molasses", "تفاحتين": "Double Apple", "فاخر": "Premium",
    "مشكل": "Mixed", "مشكلة": "Mixed", "طبيعي": "Fresh", "فريش": "Fresh",
}

_DIACRITICS = re.compile(r"[ً-ْـ]")


def _normalize(text):
    return _DIACRITICS.sub("", text or "").strip()


def suggest_english(arabic):
    """Best-guess English name for an Arabic menu name, or "" when no word is known."""
    text = _normalize(arabic)
    if not text:
        return ""
    if text in PHRASES:
        return PHRASES[text]
    tokens = []
    rest = text
    glossary = sorted({**WORDS, **PHRASES}.items(), key=lambda kv: -len(kv[0]))
    known = 0
    while rest:
        rest = rest.strip()
        if not rest:
            break
        for ar, en in glossary:
            if rest.startswith(ar) and (len(rest) == len(ar) or rest[len(ar)] == " "):
                tokens.append(en)
                rest = rest[len(ar):]
                known += 1
                break
        else:
            word, _, rest = rest.partition(" ")
            joined = word[3:] if word.startswith("بال") else word[1:] if word.startswith("ب") else ""
            if not re.search(r"\w", word):
                # Emoji and symbols carry no words
                continue
            if joined in WORDS:
                # "بالنعنع" -> "with Mint"
                tokens.append(f"with {WORDS[joined]}")
            elif re.fullmatch(r"[A-Za-z0-9.%+-]+", word):
                # Latin words and numbers (sizes, brand names) are kept as they are
                tokens.append(word)
            else:
                # One unknown word and the guess would be misleading: leave it for the manager
                return ""
            known += 1
    if not known:
        return ""
    english = " ".join(t for t in tokens if t)
    english = re.sub(r"\bIced Iced\b", "Iced", english)
    # Arabic puts the adjective after the noun: "قهوة باردة" -> "Iced Coffee"
    for adjective in ("Iced", "Hot", "Double", "Single", "Fresh"):
        if english.endswith(" " + adjective):
            english = adjective + " " + english[: -len(adjective) - 1]
    # "Juice Orange" -> "Orange Juice", "Tea Mint" -> "Mint Tea", and similar
    for noun in ("Juice", "Tea", "Smoothie", "Mojito", "Milkshake", "Cake", "Hookah", "Latte", "Frappe", "Sandwich", "Crepe", "Waffle", "Croissant", "Manakish"):
        match = re.fullmatch(rf"{noun} (.+)", english)
        if match and " with " not in english and " without " not in english:
            english = f"{match.group(1)} {noun}"
    return english[:1].upper() + english[1:]
