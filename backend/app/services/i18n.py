"""Multilingual alert rendering for the eight NER states.

TRANSLATION REVIEW REQUIRED
---------------------------
The non-English strings below are working translations intended to make the
multilingual path real rather than a stub. They have NOT been reviewed by native
speakers. This is safety-critical text: a mistranslated evacuation instruction
is worse than no instruction. Before any public deployment, have each language
signed off by the relevant State Disaster Management Authority, and record the
sign-off in docs/translation-signoff.md.

`review_status()` reports the current state, and /api/v1/alerts/languages
surfaces it so the gap is visible in the product rather than buried here.

Design notes
------------
* Templates use `str.format` placeholders, so a missing key raises loudly at
  render time instead of shipping a bulletin reading "{location}".
* Every language falls back to English rather than to an empty string, and the
  fallback is reported on the delivery record so it can be audited.
* SMS bodies are kept short: many recipients are on 2G with limited handsets,
  and Unicode SMS fragments at 70 characters per part.
"""

from __future__ import annotations

from app.models.enums import Language

# Languages signed off by a native speaker. English is the source language
# these bulletins are authored in, so it needs no translation review; every
# other entry must be added only when an SDMA has actually signed it off.
REVIEWED: set[str] = {Language.EN}

LANGUAGE_NAMES: dict[str, str] = {
    Language.EN: "English",
    Language.HI: "हिन्दी (Hindi)",
    Language.AS: "অসমীয়া (Assamese)",
    Language.BN: "বাংলা (Bengali)",
    Language.MNI: "মৈতৈলোন্ (Manipuri)",
    Language.KHA: "Khasi",
    Language.LUS: "Mizo (Duhlian)",
    Language.NE: "नेपाली (Nepali)",
}

# Which languages each state's alerts are rendered into by default.
STATE_LANGUAGES: dict[str, list[str]] = {
    "Sikkim": [Language.EN, Language.NE, Language.HI],
    "Arunachal Pradesh": [Language.EN, Language.HI],
    "Assam": [Language.EN, Language.AS, Language.HI, Language.BN],
    "Meghalaya": [Language.EN, Language.KHA, Language.HI],
    "Manipur": [Language.EN, Language.MNI, Language.HI],
    "Mizoram": [Language.EN, Language.LUS, Language.HI],
    "Nagaland": [Language.EN, Language.HI],
    "Tripura": [Language.EN, Language.BN, Language.HI],
}

LEVEL_WORDS: dict[str, dict[str, str]] = {
    Language.EN: {"critical": "CRITICAL", "high": "HIGH", "moderate": "MODERATE", "low": "LOW"},
    Language.HI: {"critical": "अति गंभीर", "high": "उच्च", "moderate": "मध्यम", "low": "कम"},
    Language.AS: {"critical": "অতি গুৰুতৰ", "high": "উচ্চ", "moderate": "মধ্যম", "low": "কম"},
    Language.BN: {"critical": "অতি গুরুতর", "high": "উচ্চ", "moderate": "মাঝারি", "low": "কম"},
    Language.MNI: {"critical": "অতি খুদোংথীবা", "high": "ৱাংবা", "moderate": "মরিল", "low": "নেমবা"},
    Language.KHA: {"critical": "SNGEWBHA", "high": "KHRAW", "moderate": "PATLA", "low": "RIT"},
    Language.LUS: {"critical": "HLAUHAWM TAK", "high": "SANG", "moderate": "LAILUNG", "low": "HNIAM"},
    Language.NE: {"critical": "अति गम्भीर", "high": "उच्च", "moderate": "मध्यम", "low": "कम"},
}

# --- SMS bodies -------------------------------------------------------------
# Placeholders: {level} {location} {district} {window} {action}
SMS_TEMPLATES: dict[str, str] = {
    Language.EN: (
        "PRAHARI ALERT [{level}]: Landslide risk at {location}, {district}. "
        "Next {window} hours. {action} Helpline 1077."
    ),
    Language.HI: (
        "प्रहरी चेतावनी [{level}]: {district} के {location} में भूस्खलन का खतरा। "
        "अगले {window} घंटे। {action} हेल्पलाइन 1077।"
    ),
    Language.AS: (
        "PRAHARI সতৰ্কবাণী [{level}]: {district}ৰ {location}ত ভূমিস্খলনৰ আশংকা। "
        "পৰৱৰ্তী {window} ঘণ্টা। {action} হেল্পলাইন 1077।"
    ),
    Language.BN: (
        "PRAHARI সতর্কতা [{level}]: {district}-এর {location}-এ ভূমিধসের ঝুঁকি। "
        "পরবর্তী {window} ঘণ্টা। {action} হেল্পলাইন 1077।"
    ),
    Language.MNI: (
        "PRAHARI চেকশিন-ৱা [{level}]: {district} গী {location} দা লৌ-ৰোং তাবগী খুদোংথীবা। "
        "মথং {window} পুং। {action} হেল্পলাইন 1077।"
    ),
    Language.KHA: (
        "PRAHARI SNGEWBHA [{level}]: Ka jingtuh ka khyndew ha {location}, {district}. "
        "Ha ki {window} por kiba wan. {action} Helpline 1077."
    ),
    Language.LUS: (
        "PRAHARI VAUNA [{level}]: {district} chhunga {location}-ah lei tlak thei. "
        "Darkar {window} chhung. {action} Helpline 1077."
    ),
    Language.NE: (
        "प्रहरी चेतावनी [{level}]: {district} को {location} मा पहिरोको जोखिम। "
        "अर्को {window} घण्टा। {action} हेल्पलाइन 1077।"
    ),
}

# --- Recommended actions, keyed by risk level -------------------------------
ACTION_TEMPLATES: dict[str, dict[str, str]] = {
    Language.EN: {
        "critical": "Evacuate the slope area now. Avoid the road.",
        "high": "Move away from the slope. Do not travel unless essential.",
        "moderate": "Stay alert. Avoid the slope after heavy rain.",
        "low": "No action needed.",
    },
    Language.HI: {
        "critical": "ढलान क्षेत्र तुरंत खाली करें। सड़क से बचें।",
        "high": "ढलान से दूर हटें। अत्यावश्यक होने पर ही यात्रा करें।",
        "moderate": "सतर्क रहें। भारी वर्षा के बाद ढलान से बचें।",
        "low": "कोई कार्रवाई आवश्यक नहीं।",
    },
    Language.AS: {
        "critical": "ঢাল অঞ্চল এতিয়াই খালী কৰক। ৰাস্তা এৰি চলক।",
        "high": "ঢালৰ পৰা আঁতৰি যাওক। প্ৰয়োজন নহ'লে যাত্ৰা নকৰিব।",
        "moderate": "সতৰ্ক থাকক। বৰষুণৰ পিছত ঢাল এৰি চলক।",
        "low": "কোনো ব্যৱস্থাৰ প্ৰয়োজন নাই।",
    },
    Language.BN: {
        "critical": "ঢাল এলাকা এখনই খালি করুন। রাস্তা এড়িয়ে চলুন।",
        "high": "ঢাল থেকে সরে যান। অত্যাবশ্যক না হলে ভ্রমণ করবেন না।",
        "moderate": "সতর্ক থাকুন। ভারী বৃষ্টির পর ঢাল এড়িয়ে চলুন।",
        "low": "কোনো ব্যবস্থার প্রয়োজন নেই।",
    },
    Language.MNI: {
        "critical": "চিংগী মফম অদু হৌজিক থাদোক্লগা চৎলু। লম্বী অদু শিজিন্নগনু।",
        "high": "চিং অদুদগী লাপথোক্লু। তঙাইফদবা নত্তনা চৎপা থিংলু।",
        "moderate": "চেকশিল্লু। নোং চেন্দ্রবা মতুংদা চিং অদু শিজিন্নগনু।",
        "low": "করিগুম্বা তৌবা চঙদে।",
    },
    Language.KHA: {
        "critical": "Mih noh na ka jaka lum mynta. Wat leit ha ka surok.",
        "high": "Mih jngai na ka lum. Wat iaid lada em ka jingdonkam.",
        "moderate": "Sngew ryngkat. Wat leit ha ka lum haba la slap eh.",
        "low": "Em kam ban leh.",
    },
    Language.LUS: {
        "critical": "Tlang bulah chuan pawt chhuak nghal rawh. Kawng hmang suh.",
        "high": "Tlang bul ata hlat rawh. A pawimawh loh chuan kal suh.",
        "moderate": "Fimkhur rawh. Ruah a sur hnuah tlang bulah kal suh.",
        "low": "Engmah tih a ngai lo.",
    },
    Language.NE: {
        "critical": "भिरालो क्षेत्र तुरुन्तै खाली गर्नुहोस्। सडक नजानुहोस्।",
        "high": "भिरालोबाट टाढा जानुहोस्। अत्यावश्यक बाहेक यात्रा नगर्नुहोस्।",
        "moderate": "सतर्क रहनुहोस्। भारी वर्षापछि भिरालो नजानुहोस्।",
        "low": "कुनै कारबाही आवश्यक छैन।",
    },
}

ROAD_STATUS_WORDS: dict[str, dict[str, str]] = {
    Language.EN: {"open": "Open", "restricted": "Restricted", "blocked": "Blocked"},
    Language.HI: {"open": "खुला", "restricted": "सीमित", "blocked": "अवरुद्ध"},
    Language.AS: {"open": "মুকলি", "restricted": "সীমিত", "blocked": "অৱৰুদ্ধ"},
    Language.BN: {"open": "খোলা", "restricted": "সীমিত", "blocked": "অবরুদ্ধ"},
    Language.MNI: {"open": "হাংবা", "restricted": "থিংবা", "blocked": "থিংজিনবা"},
    Language.KHA: {"open": "Plie", "restricted": "Kyntiew", "blocked": "Khang"},
    Language.LUS: {"open": "Hawng", "restricted": "Tihchin", "blocked": "Khar"},
    Language.NE: {"open": "खुला", "restricted": "सीमित", "blocked": "अवरुद्ध"},
}


def supported_languages() -> list[dict]:
    """Languages available for alerting, with their review status."""
    return [
        {
            "code": str(code),
            "name": name,
            "reviewed": code in REVIEWED,
            "channels": ["sms", "push", "dashboard"],
        }
        for code, name in LANGUAGE_NAMES.items()
    ]


def review_status() -> dict:
    total = len(LANGUAGE_NAMES)
    return {
        "total_languages": total,
        "reviewed": sorted(REVIEWED),
        "pending_review": sorted(set(LANGUAGE_NAMES) - REVIEWED),
        "warning": (
            "Translations are unreviewed working drafts. Obtain native-speaker "
            "sign-off from the relevant SDMA before public deployment."
        )
        if len(REVIEWED) < total
        else None,
    }


def languages_for_state(state: str | None) -> list[str]:
    return STATE_LANGUAGES.get(state or "", [Language.EN, Language.HI])


def render_sms(
    language: str,
    level: str,
    location: str,
    district: str,
    window_hours: int,
) -> tuple[str, bool]:
    """Render an SMS body. Returns (text, used_fallback)."""
    used_fallback = language not in SMS_TEMPLATES
    lang = language if not used_fallback else Language.EN

    template = SMS_TEMPLATES[lang]
    level_word = LEVEL_WORDS.get(lang, LEVEL_WORDS[Language.EN]).get(level, level.upper())
    action = ACTION_TEMPLATES.get(lang, ACTION_TEMPLATES[Language.EN]).get(
        level, ACTION_TEMPLATES[Language.EN]["moderate"]
    )

    text = template.format(
        level=level_word,
        location=location,
        district=district,
        window=window_hours,
        action=action,
    )
    return text, used_fallback


def render_actions(language: str, level: str) -> list[str]:
    """Level-appropriate advisory actions in the requested language."""
    lang = language if language in ACTION_TEMPLATES else Language.EN
    primary = ACTION_TEMPLATES[lang].get(level, ACTION_TEMPLATES[lang]["moderate"])

    extras = {
        Language.EN: [
            "Report new cracks or slope movement through the PRAHARI app.",
            "Keep emergency contacts and documents ready.",
        ],
        Language.HI: [
            "नई दरारें या ढलान की हलचल प्रहरी ऐप से सूचित करें।",
            "आपातकालीन संपर्क और दस्तावेज़ तैयार रखें।",
        ],
        Language.NE: [
            "नयाँ चर्किएको वा भिरालो चलेको प्रहरी एपमा जानकारी दिनुहोस्।",
            "आपतकालीन सम्पर्क र कागजात तयार राख्नुहोस्।",
        ],
    }
    return [primary, *extras.get(lang, extras[Language.EN])]


def estimate_sms_parts(text: str) -> int:
    """Number of SMS parts this body will occupy.

    Any non-Latin character forces the whole message into UCS-2, which cuts the
    payload from 160 to 70 characters per part. Worth surfacing: a four-part
    Assamese bulletin costs four times as much and is four times as likely to
    arrive garbled on a low-end handset.
    """
    unicode_needed = any(ord(ch) > 127 for ch in text)
    if unicode_needed:
        limit, multipart_limit = 70, 67
    else:
        limit, multipart_limit = 160, 153
    if len(text) <= limit:
        return 1
    return -(-len(text) // multipart_limit)
