import re
import unicodedata

from transformers import AutoTokenizer

from . import punctuation

model_id = 'vinai/phobert-base-v2'
tokenizer = None

def get_tokenizer():
    global tokenizer
    if tokenizer is None:
        tokenizer = AutoTokenizer.from_pretrained(model_id)
    return tokenizer



VI_IPA_CONSONANTS = [
    'b', 'c', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm', 'n', 'p', 'r', 's', 't', 'v', 'w', 'x', 'z',
    'ŋ',
    'ɲ',
    'ʈ',
    'ɖ',
    'tʰ',
    'kʰ',
    'ʂ',
    'ɣ',
    'χ',
]

VI_IPA_VOWELS = [
    'a', 'ă', 'â', 'e', 'ê', 'i', 'o', 'ô', 'ơ', 'u', 'ư', 'y',
    'ə',
    'ɛ',
    'ɔ',
    'ɯ',
    'ɤ',
    'ɐ',
    'ʊ',
    'ɪ',
    'ʌ',
    'æ',
]


VI_TONE_MARKERS = ['1', '2', '3', '4', '5', '6', 'ˈ', 'ˌ', 'ː']


VI_IPA_SYMBOLS = [

    'b', 'c', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm', 'n', 'p', 'r', 's', 't', 'v', 'w', 'x', 'z',
    'ŋ', 'ɲ', 'ʈ', 'ɖ', 'ʂ', 'ɣ', 'χ', 'ʔ',

    'a', 'ă', 'e', 'i', 'o', 'u', 'y',
    'ə', 'ɛ', 'ɔ', 'ɯ', 'ɤ', 'ɐ', 'ʊ', 'ɪ', 'ʌ', 'æ', 'ɑ',

    'ˈ', 'ˌ', 'ː',

    '1', '2', '3', '4', '5', '6',
]

def normalize_vietnamese_text(text):
    """Normalize Vietnamese text."""

    text = unicodedata.normalize('NFC', text)


    text = re.sub(r'\s+', ' ', text)
    text = text.strip()


    text = convert_numbers_to_vietnamese(text)

    return text

def convert_numbers_to_vietnamese(text):
    """Convert numbers to Vietnamese words (basic implementation)."""
    num_map = {
        '0': 'không', '1': 'một', '2': 'hai', '3': 'ba', '4': 'bốn',
        '5': 'năm', '6': 'sáu', '7': 'bảy', '8': 'tám', '9': 'chín',
        '10': 'mười', '100': 'trăm', '1000': 'nghìn'
    }


    def replace_num(match):
        num = match.group(0)
        if num in num_map:
            return num_map[num]
        return num


    text = re.sub(r'\b\d\b', replace_num, text)
    return text

def text_normalize(text):
    """Normalize text for Vietnamese TTS."""
    text = normalize_vietnamese_text(text)
    return text

def parse_ipa_phonemes(phonemized_text):
    """
    Parse IPA phonemized text from VieNeu-TTS dataset.
    Example: "ŋˈyə2j ŋˈyə2j bˈan xwˈan vˈe2"
    Returns: phones, tones, word2ph
    """
    phones = []
    tones = []
    word2ph = []


    words = phonemized_text.strip().split()

    for word in words:
        word_phones = []


        i = 0
        current_tone = 0

        while i < len(word):
            char = word[i]


            if char.isdigit():
                current_tone = int(char)
                i += 1
                continue


            if char in ['ˈ', 'ˌ']:

                i += 1
                continue


            if char == 'ː':

                if word_phones:
                    word_phones[-1] = word_phones[-1] + 'ː'
                i += 1
                continue


            if char in punctuation:
                if word_phones:
                    phones.extend(word_phones)
                    tones.extend([current_tone] * len(word_phones))
                    word2ph.append(len(word_phones))
                    word_phones = []
                phones.append(char)
                tones.append(0)
                word2ph.append(1)
                i += 1
                continue


            word_phones.append(char)
            i += 1


        if word_phones:
            phones.extend(word_phones)
            tones.extend([current_tone] * len(word_phones))
            word2ph.append(len(word_phones))

    return phones, tones, word2ph

def g2p_ipa(text):
    """
    Convert text to phonemes using external IPA converter.
    This is a fallback for when phonemized_text is not available.
    For training, we use the pre-phonemized text from the dataset.
    """
    try:
        from viphoneme import vi2ipa
        phonemized = vi2ipa(text)
        phones, tones, word2ph = parse_ipa_phonemes(phonemized)
    except ImportError:

        phones, tones, word2ph = g2p_char_based(text)


    phones = ["_"] + phones + ["_"]
    tones = [0] + tones + [0]
    word2ph = [1] + word2ph + [1]

    return phones, tones, word2ph

def g2p_char_based(text):
    """
    Character-based G2P with Vietnamese to IPA mapping.
    """
    phones = []
    tones = []
    word2ph = []


    tone_marks = {
        '\u0300': 2,
        '\u0301': 1,
        '\u0303': 3,
        '\u0309': 4,
        '\u0323': 5,
    }



    vi_to_ipa = {

        'ngh': 'ŋ',
        'ng': 'ŋ',
        'nh': 'ɲ',
        'ch': ['t', 'ʃ'],
        'tr': 'ʈ',
        'th': ['t', 'h'],
        'ph': 'f',
        'kh': 'x',
        'gh': 'ɣ',
        'gi': 'z',
        'qu': 'kw',

        'đ': 'ɗ',

        'x': 's',
        'c': 'k',
        'd': 'z',
        'r': 'ɹ',
        's': 's',
        'b': 'b',
        'g': 'ɣ',
        'h': 'h',
        'k': 'k',
        'l': 'l',
        'm': 'm',
        'n': 'n',
        'p': 'p',
        't': 't',
        'v': 'v',
        'f': 'f',
        'j': 'j',
        'w': 'w',
        'y': 'j',

        'a': 'aː',
        'ă': 'a',
        'â': 'ə',
        'e': 'ɛ',
        'ê': 'e',
        'i': 'i',
        'o': 'ɔ',
        'ô': 'o',
        'ơ': 'əː',
        'u': 'u',
        'ư': 'ɯ',
    }

    words = text.split()
    for word in words:

        decomposed = unicodedata.normalize('NFD', word)
        word_phones = []
        current_tone = 0

        i = 0
        chars = list(decomposed)
        while i < len(chars):
            char = chars[i]

            if char in tone_marks:
                current_tone = tone_marks[char]
                i += 1
                continue

            if char in punctuation:
                if word_phones:
                    phones.extend(word_phones)
                    tones.extend([current_tone] * len(word_phones))
                    word2ph.append(len(word_phones))
                    word_phones = []
                phones.append(char)
                tones.append(0)
                word2ph.append(1)
                current_tone = 0
                i += 1
                continue

            if unicodedata.combining(char):
                i += 1
                continue


            lower_char = char.lower()
            matched = False


            if i + 2 < len(chars):
                trigraph = (lower_char + chars[i+1].lower() + chars[i+2].lower())
                if trigraph in vi_to_ipa:
                    result = vi_to_ipa[trigraph]
                    if isinstance(result, list):
                        word_phones.extend(result)
                    else:
                        word_phones.append(result)
                    i += 3
                    matched = True


            if not matched and i + 1 < len(chars):
                digraph = lower_char + chars[i+1].lower()
                if digraph in vi_to_ipa:
                    result = vi_to_ipa[digraph]
                    if isinstance(result, list):
                        word_phones.extend(result)
                    else:
                        word_phones.append(result)
                    i += 2
                    matched = True


            if not matched:
                if lower_char in vi_to_ipa:
                    result = vi_to_ipa[lower_char]
                    if isinstance(result, list):
                        word_phones.extend(result)
                    else:
                        word_phones.append(result)
                else:
                    word_phones.append(lower_char)
                i += 1

        if word_phones:
            phones.extend(word_phones)
            tones.extend([current_tone] * len(word_phones))
            word2ph.append(len(word_phones))


    phones = ["_"] + phones + ["_"]
    tones = [0] + tones + [0]
    word2ph = [1] + word2ph + [1]

    return phones, tones, word2ph

def g2p(text):
    """
    Main G2P function for Vietnamese.
    Uses character-to-IPA mapping with BERT alignment.
    """
    tok = get_tokenizer()
    norm_text = text_normalize(text)


    tokenized = tok.tokenize(norm_text)


    phones, tones, word2ph = g2p_char_based(norm_text)



    if len(word2ph) != len(tokenized) + 2:

        total_phones = sum(word2ph)
        new_word2ph = distribute_phones(total_phones, len(tokenized))
        word2ph = [1] + new_word2ph + [1]

    return phones, tones, word2ph

def g2p_with_phonemes(text, phonemized_text):
    """
    G2P using pre-phonemized text from dataset.
    This is the recommended method for training.
    """
    tok = get_tokenizer()


    phones, tones, word2ph = parse_ipa_phonemes(phonemized_text)


    phones = ["_"] + phones + ["_"]
    tones = [0] + tones + [0]


    tokenized = tok.tokenize(text)


    if word2ph:
        total_phones = sum(word2ph)
        new_word2ph = distribute_phones(total_phones, len(tokenized))
        word2ph = [1] + new_word2ph + [1]
    else:
        word2ph = [1] + [1] * len(tokenized) + [1]

    return phones, tones, word2ph

def distribute_phones(n_phone, n_word):
    """Distribute phones across words as evenly as possible."""
    if n_word == 0:
        return []
    phones_per_word = [n_phone // n_word] * n_word
    remainder = n_phone % n_word
    for i in range(remainder):
        phones_per_word[i] += 1
    return phones_per_word

def get_bert_feature(text, word2ph, device='cuda'):
    """Get BERT features for Vietnamese text."""
    from . import vietnamese_bert
    return vietnamese_bert.get_bert_feature(text, word2ph, device=device, model_id=model_id)


if __name__ == "__main__":

    test_text = "Xin chào, tôi là một trợ lý AI."
    test_phonemes = "sˈin tʂˈaːw, tˈoj lˈaː2 mˈo6t tʂˈɤ4 lˈi4 ˌaːˈi."

    print("Test text:", test_text)
    print("Normalized:", text_normalize(test_text))


    phones, tones, word2ph = g2p_with_phonemes(test_text, test_phonemes)
    print("Phones:", phones)
    print("Tones:", tones)
    print("Word2Ph:", word2ph)


    phones2, tones2, word2ph2 = g2p(test_text)
    print("\nChar-based phones:", phones2)
    print("Char-based tones:", tones2)
